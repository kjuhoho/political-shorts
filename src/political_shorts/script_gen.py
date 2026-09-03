"""Step 5 — turn a story cluster into a punchy, explainer-style short script.

Structure (v2):
    hook       curiosity-gap opener, names the real actors           (~4s)
    summary    "쉽게 말하면, ..." one plain sentence                  (~4s)
    what       1-2 middle-school beats of what actually happened      (~9s ea)
    reaction   what each side said, attributed                       (~9s)
    factcheck  ✅ 확인된 사실 / 💬 주장 / ⚠️ 해석 / 🔎 원문 확인       (~9s)
    outro      soft CTA + sources                                    (~4s)

Also carries `images` (keyless CC photos) that video.py lays under the cards.
The hook may be sensational in *tone*; every factual line still traces to the
sources, and the fact-check card keeps interpretation clearly labelled.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from .analyze import analyze
from .config import Settings, settings
from .db import cluster_articles, connect
from .hook import (
    detect_entities, detect_frame, make_factcheck, make_hook, make_title, simplify,
    strip_wire_marks,
)
from .logging_setup import get_logger
from .textutil import clean_text, clip_sentence, strip_byline, truncate

log = get_logger("script_gen")

CAPTION_LIMIT = 46
BODY_LIMIT = 72
MAX_WHAT = 1

# Top-performing news shorts run tight — 20-38s. Aim ~30-36s: hook opens a
# loop, the summary card is dropped, 4-5 fast cards.
MAX_VIDEO_SECONDS = 38.0
_KR_CHARS_PER_SEC = 7.0          # edge-tts at ~+13% rate (TTS_RATE 198)
_CARD_PAD_SECONDS = 0.24         # brief breath between cards
# hard per-segment narration caps (chars). 0 = caption-only card, no voice.
_NARR_CAP = {"hook": 46, "summary": 40, "what": 58, "reaction": 62,
             "factcheck": 74, "outro": 0}
_SILENT_CARD_SECONDS = 1.5

_SENT_END = ("다", "요", "죠", "까", "네", "군", ".", "!", "?", "…")


def _spoken(text: str) -> str:
    """Clean a narration string so TTS reads it naturally: no '..'/'…' fragments,
    always ends on a full sentence + proper punctuation."""
    t = clean_text(text).rstrip(" ,·…")
    t = t.replace("...", ".").replace("..", ".").replace(" .", ".").replace("…", "")
    t = t.rstrip(" ,·")
    if not t:
        return t
    if not t.endswith(_SENT_END):
        # drop a trailing partial clause, else just close the sentence
        cut = max(t.rfind("다 "), t.rfind("요 "), t.rfind(". "))
        t = (t[: cut + 1] if cut > len(t) * 0.4 else t).rstrip(" ,·") + "."
    return t

DISCLAIMER = (
    "이 영상은 공개된 언론 보도를 쉽게 풀어 정리한 개인 제작물입니다. "
    "인용·수치는 원문 확인이 필요하고, 해석·전망은 제작자 견해가 아니라 보도 내용을 옮긴 것입니다."
)


def _seg_seconds(seg: dict[str, Any]) -> float:
    n = seg.get("narration", "")
    if not n:
        return _SILENT_CARD_SECONDS
    return len(n) / _KR_CHARS_PER_SEC + _CARD_PAD_SECONDS


def _fit_duration(segments: list[dict[str, Any]], budget: float = MAX_VIDEO_SECONDS) -> list[dict[str, Any]]:
    def total() -> float:
        return sum(_seg_seconds(s) for s in segments)

    # 1) hard per-role caps — trim to a clean boundary, never mid-word/mid-sentence
    for s in segments:
        cap = _NARR_CAP.get(s["role"])
        if cap == 0:
            s["narration"] = ""
        elif cap and len(s.get("narration", "")) > cap:
            s["narration"] = clip_sentence(s["narration"], cap)

    # 2) trim the deck for a short video: drop the standalone summary first,
    #    then any extra what/reaction beats. hook / one what / factcheck / outro
    #    are always kept.
    if total() > budget:
        segments[:] = [s for s in segments if s["role"] != "summary"]
    for role in ("reaction", "what"):
        while total() > budget and sum(1 for s in segments if s["role"] == role) > 1:
            for i in range(len(segments) - 1, -1, -1):
                if segments[i]["role"] == role:
                    segments.pop(i)
                    break

    # 3) still over? drop a whole trailing sentence from the longest card
    guard = 0
    while total() > budget and guard < 10:
        guard += 1
        longest = max((s for s in segments if s.get("narration")),
                      key=lambda s: len(s["narration"]), default=None)
        if not longest:
            break
        sents = re.split(r"(?<=[다요.!?])\s+", longest["narration"])
        if len(sents) > 1:
            longest["narration"] = " ".join(sents[:-1])
        else:
            longest["narration"] = clip_sentence(longest["narration"],
                                                 int(len(longest["narration"]) * 0.85))

    # 4) final polish: every spoken line is a clean, complete sentence
    for s in segments:
        if s.get("narration"):
            s["narration"] = _spoken(s["narration"])
    return segments


def _sources_from_rows(rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for r in rows:
        if r["source_name"] in seen:
            continue
        seen.add(r["source_name"])
        out.append({"name": r["source_name"], "url": r["url"], "lean": r["source_lean"]})
    return out


def _headline(lead_title: str) -> str:
    t = strip_wire_marks(strip_byline(lead_title))
    for sep in (" - ", " | ", " :: "):
        if sep in t:
            t = t.split(sep)[0].strip()
    t = re.sub(r"(청와대|대통령|국무총리|국회)(정책실장|비서실장|안보실장|수석|대변인)", r"\1 \2", t)
    return truncate(t.rstrip(" .·"), 46)


_CONTEXT_HINTS = ("후임", "후임자", "공석", "배경", "이유", "때문", "처음", "첫",
                  "만에", "논란", "앞서", "이어", "당분간", "대행", "겸직", "이날")


def _tokens(t: str) -> set[str]:
    return {w for w in re.split(r"[^0-9A-Za-z가-힣]+", clean_text(t)) if len(w) > 1}


def _too_similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.72


def _context_score(t: str) -> int:
    s = sum(1 for h in _CONTEXT_HINTS if h in t)
    if re.search(r"\d", t):
        s += 1
    return s


_PARTY_WORDS = ("민주당", "국민의힘", "국힘", "조국혁신당", "개혁신당", "진보당", "정의당",
                "여당", "야당", "여권", "야권", "대통령실", "청와대", "정부")
_SPEAKER_RE = re.compile(r"([가-힣]{2,4})\s*(?:수석|대변인|의원|원내대표|대표|장관|위원장|실장)")


def _speaker(text: str) -> str:
    m = _SPEAKER_RE.search(text)
    return m.group(1) if m else ""


def _side_key(text: str) -> str:
    for p in _PARTY_WORDS:
        if p in text:
            return p
    return _speaker(text)


_QUOTE_RE = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{6,70})[\"'“”‘’]")


def _one_quote(text: str, limit: int = 46) -> str:
    """The core of what someone said, ending cleanly (prefer the quoted span)."""
    m = _QUOTE_RE.search(text)
    core = m.group(1) if m else clean_text(text)
    return clip_sentence(core, limit).rstrip(" .…")


def _reaction_line(claims: list) -> str:
    """A 'who said what' line — framed as two sides only when the two quotes
    genuinely come from different actors AND both clip to a usable sentence."""
    if not claims:
        return ""
    a = claims[0]
    ka = _side_key(a.text)
    c0 = _one_quote(a.text)
    if len(c0) < 8:
        return ""
    b = next((c for c in claims[1:]
              if _side_key(c.text) and _side_key(c.text) != ka), None)
    c1 = _one_quote(b.text) if b else ""
    if len(c1) >= 10:
        return f"한쪽은 이렇게 말합니다. {c0}. 다른 쪽은 이렇게 맞섭니다. {c1}."
    return f"이런 말이 나왔습니다. {c0}. 반대편 반응은 아직 나오지 않았습니다."


def build_script(cluster_id: int, cfg: Settings | None = None) -> dict[str, Any]:
    cfg = cfg or settings
    with connect(cfg.db_path) as conn:
        rows = cluster_articles(conn, cluster_id)
    if not rows:
        raise ValueError(f"cluster {cluster_id} has no articles")

    lead = rows[0]
    titles = " \n ".join(r["title"] for r in rows)
    summaries = " \n ".join(r["summary"] for r in rows)
    analysis = analyze(titles, summaries)
    entities = detect_entities(titles, summaries)
    frame = detect_frame(titles, summaries)

    n_sources = len({r["source_name"] for r in rows})
    leans = sorted({r["source_lean"] for r in rows})
    headline = _headline(lead["title"])
    multi = n_sources >= cfg.min_sources_for_fact

    segments: list[dict[str, Any]] = []

    # 1) hook ---------------------------------------------------------------
    hcap, hnar = make_hook(headline, entities, frame, cfg.headline_style)
    segments.append({"role": "hook", "kicker": "오늘의 이슈", "caption": hcap, "narration": hnar})

    # 2) one-line summary -------------------------------------------------
    summary_fact = analysis.facts[0] if analysis.facts else None
    if summary_fact:
        s = simplify(summary_fact.text, add_lead=True)
    else:
        s = f"쉽게 말하면, {n_sources}개 언론이 이 사안을 나란히 보도했습니다: {headline}."
    segments.append({"role": "summary", "kicker": "한 줄 요약",
                     "caption": clip_sentence(s, CAPTION_LIMIT), "narration": s})

    # 3) what happened — a fact that is NOT just the headline restated
    sfx = summary_fact.text if summary_fact else ""
    hl_tokens = _tokens(headline) | _tokens(sfx)

    def _adds_new(text: str) -> bool:
        tt = _tokens(text)
        if not tt:
            return False
        overlap = len(tt & hl_tokens) / len(tt)
        return overlap < 0.6 and len(tt - hl_tokens) >= 3

    body = [f for f in analysis.facts[1:] if _adds_new(f.text)]
    body.sort(key=lambda f: (_context_score(f.text), f.score), reverse=True)
    if body:
        f = body[0]
        # if the outlet joined two clauses with an ellipsis, keep the longer one,
        # then trim to a clean sentence boundary
        raw = max(re.split(r"…|\.\.\.", clean_text(f.text)), key=len)
        clause = simplify(raw, limit=64)
        segments.append({"role": "what", "kicker": "무슨 일이냐면",
                         "caption": clip_sentence(clause, CAPTION_LIMIT),
                         "narration": clause,
                         "source": lead["source_name"], "multi_source": multi,
                         "cues": f.cues})
    # (no genuinely new fact -> skip the 'what' card)

    # 4) reaction (attributed) --------------------------------------
    reaction = _reaction_line(analysis.claims)
    if reaction:
        segments.append({"role": "reaction", "kicker": "양쪽 반응",
                         "caption": clip_sentence(reaction, CAPTION_LIMIT + 8),
                         "narration": reaction, "attributed": True,
                         "cues": analysis.claims[0].cues if analysis.claims else []})

    # 5) fact-check ------------------------------------------------
    if cfg.factcheck_segment:
        fc_rows = make_factcheck(analysis, n_sources)
        fact_row = next((r for r in fc_rows if r["tone"] == "ok"), None)
        fact_t = clip_sentence(fact_row["text"], 40).rstrip(" .…") if fact_row else ""
        has_claim = any(r["tone"] == "claim" for r in fc_rows)
        has_interp = any(r["tone"] == "warn" for r in fc_rows)
        narr = "핵심만 짚습니다. "
        if fact_t:
            narr += f"확인된 건 이겁니다. {fact_t}. "
        if has_claim or has_interp:
            narr += "나머지는 아직 한쪽 주장이거나 전망입니다. 자세한 건 더보기란을 보세요."
        else:
            narr += "자세한 출처는 더보기란에 있습니다."
        segments.append({"role": "factcheck", "kicker": "팩트체크",
                         "caption": "팩트체크", "rows": fc_rows, "narration": narr})

    # 6) outro --------------------------------------------------
    lean_note = ("여러 성향의 매체 보도를 종합했습니다."
                 if len(set(leans) - {"wire"}) >= 2 or len(leans) >= 3
                 else "보도량이 많지 않은 사안이라 추가 확인이 필요합니다.")
    segments.append({"role": "outro", "kicker": "",
                     "caption": "여러분 생각은 어떤가요? 댓글로 알려주세요",
                     "narration": f"{lean_note} 자세한 내용과 원문 링크는 더보기란에 있습니다."})

    segments = _fit_duration(segments)

    # --- align the on-screen caption with what's actually being said, and
    #     number the content cards so the viewer can follow ("1." "2." ...) ---
    n = 0
    for s in segments:
        if s["role"] in ("hook", "outro", "factcheck"):
            s["caption"] = clip_sentence(s.get("caption", ""), 46, ell="..")
        elif s.get("narration"):
            # caption mirrors the voice line; if it can't end on a full sentence
            # inside the width, leave a clean phrase fragment (no dangling "..")
            cap = clip_sentence(s["narration"], 48, ell="")
            s["caption"] = cap.rstrip(" ,·.")
        if s["role"] not in ("outro",):
            n += 1
            s["num"] = n

    est_seconds = round(sum(_seg_seconds(s) for s in segments), 1)
    title = make_title(headline, entities, frame)
    from .hook import pick_actor as _pa
    topic = _pa(headline, entities, frame)

    # 7) images (keyless CC) --------------------------------------
    images: list[dict[str, Any]] = []
    try:
        from .images import collect_images

        images = [a.__dict__ for a in collect_images(entities, frame, headline, cfg)]
    except Exception as exc:  # pragma: no cover - network dependent
        log.warning("image collection failed: %s", exc)

    script: dict[str, Any] = {
        "cluster_id": cluster_id,
        "headline": headline,
        "title": title,
        "topic": topic,
        "n_sources": n_sources,
        "leans": leans,
        "frame": frame.kind,
        "entities": {"president": entities.president, "politicians": entities.politicians,
                     "parties": entities.parties, "institutions": entities.institutions},
        "segments": segments,
        "images": images,
        "est_seconds": est_seconds,
        "sources": _sources_from_rows(rows),
        "counts": {"facts": len(analysis.facts), "claims": len(analysis.claims),
                   "interpretations": len(analysis.interpretations)},
        "disclaimer": DISCLAIMER,
        "style": cfg.headline_style,
    }

    if cfg.llm_available:
        try:
            script = _llm_polish(script, cfg)
        except Exception as exc:  # pragma: no cover
            log.warning("llm polish skipped: %s", exc)

    log.info(
        "script cluster=%d segs=%d ~%.0fs frame=%s imgs=%d facts=%d claims=%d interp=%d src=%d",
        cluster_id, len(segments), est_seconds, frame.kind, len(images),
        len(analysis.facts), len(analysis.claims), len(analysis.interpretations), n_sources,
    )
    return script


def _llm_polish(script: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    """Optional: smooth hook/outro wording only. Facts and roles stay frozen."""
    from .llm import complete

    for seg in script["segments"]:
        if seg["role"] in {"hook", "outro"}:
            prompt = (
                "다음 나레이션을 뜻은 그대로, 더 자연스럽고 흡입력 있는 한국어 한두 문장으로 "
                "다듬어 주세요. 새로운 사실·주장·인물 추가 금지, 과장·허위 금지.\n"
                f"문장: {seg['narration']}\n결과:"
            )
            out = clean_text(complete(prompt, cfg, max_tokens=120))
            if out:
                seg["narration"] = truncate(out, 200)
    script["llm_polished"] = True
    return script


def script_word_estimate(script: dict[str, Any]) -> int:
    return sum(len(s.get("narration", "")) for s in script["segments"])
