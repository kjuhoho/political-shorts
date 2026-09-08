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
    "6) 각 카드는 주어진 글자 수(limit) 이내. 한 문장이 길어지지 않게 40자 안팎에서 "
    "끊어 1~2문장으로 쓰고, 반드시 '~습니다/~です'처럼 완결된 문장으로 끝낼 것 "
    "(조사·연결어미 '…에 따르면 / …라며 / …했지만 / …곳이'로 끝내지 말 것).\n"
    "7) 출력은 JSON 객체 하나만. 키는 카드 role, 값은 새 내레이션 문자열."
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
        '예: {"hook":"새 내레이션...","what":"새 내레이션...","reaction":"...","outro":"..."}'
    )


def _norm(v: str) -> str:
    return re.sub(r"\s+", " ", v).strip()


_ROLE_KEYS = {"hook", "summary", "what", "reaction", "factcheck", "outro"}


def _parse(raw: str) -> dict[str, str]:
    """-> {role: narration}. Tolerates the shapes a small model actually emits:
    flat {"hook": "..."}, nested {"hook": {"narration": "..."}}, an echoed
    {"cards": [{"role": "hook", "narration": "..."}]}, or a bare list of those.
    """
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", txt).strip()
    obj = None
    for cand in (txt, (re.search(r"[\{\[].*[\}\]]", txt, re.S) or [None])[0]):
        if not cand:
            continue
        try:
            obj = json.loads(cand)
            break
        except Exception:
            continue
    if obj is None:
        return {}

    def _text(v: Any) -> str:
        if isinstance(v, str):
            return _norm(v)
        if isinstance(v, dict):
            return _norm(str(v.get("narration") or v.get("text") or v.get("draft") or ""))
        return ""

    rows: list = []
    if isinstance(obj, dict) and isinstance(obj.get("cards"), list):
        rows = obj["cards"]
    elif isinstance(obj, list):
        rows = obj
    out: dict[str, str] = {}
    if rows:
        for r in rows:
            if isinstance(r, dict) and r.get("role") in _ROLE_KEYS:
                t = _text(r)
                if t:
                    out[r["role"]] = t
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in _ROLE_KEYS:
                t = _text(v)
                if t:
                    out[str(k)] = t
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
        narr = new.get(s.get("role", ""))
        if not narr:
            continue
        lim = _LLM_LIMIT.get(s["role"], 80)
        if not (8 <= len(narr) <= int(lim * 1.8)):
            continue
        s["narration"] = narr
        s.pop("caption", None)               # re-derived cleanly in script_gen
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
