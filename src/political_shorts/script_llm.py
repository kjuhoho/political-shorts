"""Optional narration rewrite — turns the templated card text into a natural,
lay-friendly explanation that flows card to card.

Hard rules, enforced in code (not just the prompt):
  * the LLM only ever REWRITES the `narration` of spoken cards; card structure,
    images, fact-check rows and sources are untouched;
  * it is given ONLY the cluster's own source text + the fact/claim/interp split
    from analyze.py — no outside knowledge is invited;
  * the rewritten script must still pass `safety.review_script` with no new
    blocks, or the original template narration is kept;
  * ANY failure (no key, HTTP error, bad JSON, empty/oversized output, safety
    block) falls back silently to the template output.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import Settings
from .logging_setup import get_logger

log = get_logger("script_llm")

# spoken cards the LLM may rewrite, and how many chars it gets for each
# (looser than the template caps — the point is room to actually explain)
_LLM_LIMIT = {"hook": 55, "summary": 60, "what": 95, "reaction": 90,
              "factcheck": 80, "outro": 48}

_SYSTEM = (
    "당신은 정치에 관심 없는 일반인에게 뉴스를 풀어 설명하는 한국어 내레이션 작가입니다. "
    "주어진 '원문 기사'와 분류된 사실만 사용해 각 카드의 내레이션을 다시 씁니다. "
    "규칙:\n"
    "1) 정치를 모르는 사람에게 말하듯: 누가 무엇을 했는지 → 그게 무슨 뜻인지 → 왜 중요한지 순서로.\n"
    "2) 카드가 서로 자연스럽게 이어지게 쓸 것 (예: '그런데', '이게 왜 중요하냐면', '정리하면'). "
    "앞 카드에서 한 말을 반복하지 말 것.\n"
    "3) 쉬운 말, 짧은 문장. 전문용어는 한 번 풀어서 설명.\n"
    "4) 철저히 중립: 한쪽 편을 들지 말고 양쪽 입장을 같은 무게로. 비꼬거나 평가하지 말 것.\n"
    "5) 원문에 없는 사실·숫자·발언을 만들지 말 것. 확실하지 않으면 '~라고 밝혔습니다' 식으로 출처를 남길 것.\n"
    "6) 각 카드의 narration은 주어진 글자 수(limit) 이내, 반드시 완성된 문장으로 끝낼 것.\n"
    "7) 각 카드에 caption도 함께: 화면 자막용으로 25자 이내의 완결된 짧은 구절 "
    "(조사·연결어미로 끝내지 말 것).\n"
    "8) 출력은 JSON 객체 하나만. 형식: "
    '{"role": {"narration": "...", "caption": "..."}, ...}'
)


def _payload(meta: dict[str, Any], cards: list[dict[str, Any]]) -> str:
    def _bullets(items: list[str], cap: int = 8) -> str:
        return "\n".join(f"- {re.sub(r'\\s+', ' ', t).strip()[:160]}" for t in items[:cap]) or "- (없음)"

    ent = meta.get("entities", {})
    who = ", ".join(
        [f"{n}(대통령)" if n == ent.get("president") else n
         for n in (ent.get("politicians") or [])][:8]
        + (ent.get("parties") or [])[:4]
    ) or "(특정 인물 없음)"
    ask = {"cards": [{"role": c["role"],
                      "limit": _LLM_LIMIT.get(c["role"], 80),
                      "draft": c.get("narration", "")} for c in cards]}
    return (
        f"[원문 기사]\n{meta.get('source_text', '').strip()[:2600]}\n\n"
        f"[사실로 분류된 문장]\n{_bullets(meta.get('facts', []))}\n\n"
        f"[주장 — 누가 말한 것]\n{_bullets(meta.get('claims', []))}\n\n"
        f"[해석·전망 — 사실 아님, 참고만]\n{_bullets(meta.get('interps', []), 5)}\n\n"
        f"[등장 인물·정당] {who}\n"
        f"[이 기사의 핵심 인물/주제] {meta.get('topic', '') or '(없음)'}\n\n"
        f"[다시 쓸 카드]\n{json.dumps(ask, ensure_ascii=False)}\n\n"
        "각 카드의 draft를 위 규칙대로 다시 써서 JSON으로만 답하세요. "
        '예: {"hook":{"narration":"...","caption":"..."},"what":{"narration":"...","caption":"..."}}'
    )


def _norm(v: str) -> str:
    return re.sub(r"\s+", " ", v).strip()


def _parse(raw: str) -> dict[str, dict[str, str]]:
    """-> {role: {"narration": str, "caption": str}}  (caption optional)."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", txt).strip()
    try:
        obj = json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return {}
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return {}
    out: dict[str, dict[str, str]] = {}
    for k, v in (obj.items() if isinstance(obj, dict) else []):
        if isinstance(v, str) and v.strip():
            out[str(k)] = {"narration": _norm(v)}
        elif isinstance(v, dict) and str(v.get("narration", "")).strip():
            row = {"narration": _norm(str(v["narration"]))}
            if str(v.get("caption", "")).strip():
                row["caption"] = _norm(str(v["caption"]))
            out[str(k)] = row
    return out


def rewrite_segments(
    segments: list[dict[str, Any]], meta: dict[str, Any], cfg: Settings,
    base_script: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return `segments` with spoken narration rewritten by the LLM, or the
    unchanged input on any problem."""
    provider = (getattr(cfg, "llm_provider", "") or "").strip()
    if not provider:
        return segments
    spoken = [s for s in segments if s.get("role") in _LLM_LIMIT and s.get("narration")]
    if not spoken:
        return segments

    try:
        from .llm import complete
        raw = complete(_payload(meta, spoken), cfg, max_tokens=900, system=_SYSTEM)
    except Exception as exc:  # pragma: no cover - network dependent
        log.warning("llm narration rewrite skipped: %s", exc)
        return segments

    new = _parse(raw)
    if not new:
        log.warning("llm rewrite: unparseable response, keeping template")
        return segments

    # apply — only where the model returned a sane narration for a card we have
    cand = [dict(s) for s in segments]
    changed = 0
    for s in cand:
        role = s.get("role")
        row = new.get(role)
        if not row:
            continue
        narr = row["narration"]
        lim = _LLM_LIMIT.get(role, 80)
        if not (6 <= len(narr) <= int(lim * 1.8)):
            continue
        s["narration"] = narr
        cap = row.get("caption", "")
        if 4 <= len(cap) <= 40:
            s["caption"] = cap
            s["llm_caption"] = True          # script_gen keeps this as-is
        changed += 1
    if not changed:
        return segments

    # safety gate — DIFFERENTIAL: reject only if the rewrite adds a block that
    # the templated version didn't already have (structural blocks like a
    # single-source 'what' card aren't the rewrite's fault).
    try:
        from .safety import review_script

        def _blocks(segs: list[dict[str, Any]]) -> set[str]:
            probe = dict(base_script or {})
            probe["segments"] = segs
            return set(review_script(probe, cfg).blocks)

        added = _blocks(cand) - _blocks(segments)
        if added:
            log.warning("llm rewrite introduces safety block(s) %s — keeping template",
                        "; ".join(sorted(added))[:140])
            return segments
    except Exception as exc:  # pragma: no cover
        log.warning("llm rewrite: safety check errored (%s) — keeping template", exc)
        return segments

    log.info("llm narration rewrite: %d/%d cards via %s", changed, len(spoken), provider)
    return cand
