"""Run the research stage for one topic and print what it found — no video,
no publishing. Used by the manual `research probe` workflow to check each
source (news / web+official / YouTube) actually returns something in CI.

    python scripts/research_probe.py "이재명 대통령 이란 파병" [주요 인물]
"""
from __future__ import annotations

import json
import logging
import sys

from political_shorts import research
from political_shorts.config import load_settings


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    headline = sys.argv[1]
    topic = sys.argv[2] if len(sys.argv) > 2 else ""
    seed = [x for x in (sys.argv[3].split(",") if len(sys.argv) > 3 else []) if x]
    cfg = load_settings()
    print(f"provider={cfg.llm_provider!r} youtube_key={'set' if cfg.youtube_api_key else 'missing'} "
          f"research_enabled={cfg.research_enabled}")
    q = research.build_query(headline, topic)
    print(f"query: {q}\n")

    plan = research.plan_research(headline, topic, "", cfg)
    print("== plan")
    print(f"  event   : {plan.get('event')}")
    print(f"  question: {plan.get('question')}")
    for pq in plan.get("queries", []):
        print(f"  query   : {pq}")
    print()

    found = research._gather(plan.get("queries") or [q])
    print(f"== articles found for the planned queries: {len(found)}")
    for it in found[:12]:
        print(f"  - {it['title'][:60]} ({it['source']})")
    print()

    web = research.web_notes(headline, topic, cfg, plan=plan, seed_leans=seed or None)
    print("seed leans:", seed, "| leans of sources used:",
          sorted({research.lean_of(s.get("url", "")) or "-" for s in web.get("sources", [])}))
    for s in web.get("sources", []):
        print("   src:", research.lean_of(s.get("url", "")) or "-", s.get("url", "")[:80])
    print(f"\n== web/official: {'ok' if web else 'EMPTY'}  keys={sorted(web)}")
    print(f"  WHY (causes): {len(web.get('why') or [])}")
    for item in (web.get("why") or [])[:5]:
        print("    ", json.dumps(item, ensure_ascii=False)[:360])
    for k in ("background", "status"):
        if web.get(k):
            print(f"  {k}: {str(web[k])[:300]}")
    for k in ("statements", "positions", "pros", "cons", "reactions"):
        v = web.get(k) or []
        print(f"  {k}: {len(v)}")
        for item in v[:2]:
            print("    ", json.dumps(item, ensure_ascii=False)[:260])

    yt = research.youtube(research.build_query(plan.get("event") or headline, topic), cfg)
    print(f"\n== youtube: videos={len(yt.get('videos', []))} comments={len(yt.get('comments', []))}")
    for v in (yt.get("videos") or [])[:3]:
        print(f"  - {v['title']} ({v['channel']})")

    pack = {"query": q, "plan": plan, "news": [], "web": web, "youtube": yt}
    block = research.pack_block(pack)
    print(f"\n== prompt block: {len(block)} chars")
    print(block[:1800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
