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
from .textutil import clean_text

log = get_logger("script_llm")

# spoken cards the LLM may rewrite, and how many chars each gets. Kept short so
# the on-screen caption can show the WHOLE line (read-along) without spilling
# past the caption plate — a card == one bite-sized, fully-readable thought.
_LLM_LIMIT = {"hook": 48, "summary": 52, "what": 92, "reaction": 70,
              "factcheck": 58, "sides": 122, "outro": 72}

_SYSTEM = (
    "당신은 정치에 관심 없는 일반인에게 뉴스를 풀어 설명하는 한국어 내레이션 작가입니다. "
    "주어진 '원문 기사'와 분류된 사실만 사용해 각 카드의 내레이션을 다시 씁니다. "
    "규칙:\n"
    "1) 정치를 모르는 사람에게 말하듯: 누가 무엇을 했는지 → 그게 무슨 뜻인지 → 왜 중요한지 순서로.\n"
    "2) 카드가 서로 자연스럽게 이어지게 쓸 것 (예: '그런데', '이게 왜 중요하냐면', '정리하면'). "
    "앞 카드에서 한 말을 반복하지 말 것. 단, 전환 표현은 반드시 내용이 있는 문장 안에 "
    "붙일 것 — '그런데 이렇게 갈립니다.'처럼 전환어만 있는 문장을 따로 쓰지 말 것 "
    "('그런데 민주당은 …라고 봅니다.'처럼).\n"
    "3) 쉬운 말, 짧은 문장. 전문용어는 한 번 풀어서 설명.\n"
    "4) 철저히 중립: 한쪽 편을 들지 말고 양쪽을 같은 무게로. 비꼬거나 평가하지 말 것.\n"
    "5) 반응·입장을 옮길 때 '한쪽은 / 다른 쪽은'처럼 뭉뚱그리지 말 것. 누가 그렇게 "
    "말했는지 분명히: 정당이면 '국민의힘은 …, 민주당은 …', 특정인 발언이면 이름을 "
    "쓰고, 정당·인물이 불분명하면 '온라인에서는 …는 반응이 많습니다'처럼 여론으로 돌릴 것.\n"
    "6) 원문에 없는 사실·숫자·발언을 만들지 말 것. 확실하지 않으면 '~라고 밝혔습니다' 식으로 출처를 남길 것.\n"
    "7) 각 카드는 주어진 글자 수(limit) 이내. 한 문장은 40자 안팎에서 끊어 1~2문장으로 "
    "쓰고, 모든 문장을 '~습니다 / ~합니다 / ~됩니다'처럼 완결형 종결어미로 끝낼 것. "
    "절대 조사·연결어미('…에 따르면 / …라며 / …했지만 / …곳이 / …가운데')로 끝내지 말 것.\n"
    "8) title도 함께: 영상 내내 화면에 박히는 2줄 제목. 1줄은 이 영상의 핵심 "
    "대상(인물·기관·숫자·쟁점)을 구체적으로, 2줄은 클릭하고 싶게 만드는 궁금증 "
    "한 마디('왜?', '무슨 일?', '진짜일까?', '이유는', '판정은'). 각 줄 13자 "
    "이내, 원문에 있는 사실만, 비하·단정·과장('충격/발칵') 금지. 내용과 반드시 일치.\n"
    "9) 이 영상은 유튜브 쇼츠입니다. 카드 순서대로 몰입 곡선을 만들 것:\n"
    "  - hook(앞): 기사에서 가장 세거나 의외인 사실을 첫 문장에 넣을 것 — "
    "핵심 숫자가 있으면 반드시 그 숫자로 시작(예: '반대 45%, 찬성 36%'). "
    "낚시·과장·비하 없이 '사실 자체의 무게'로 첫 3초를 잡을 것.\n"
    "  - summary(앞): 그 숫자를 일상어로 다시 풀 것 (예: '국민 10명 중 4명 이상').\n"
    "  - what(중반): '그런데', '여기서 진짜 핵심은', '문제는 이겁니다' 같은 말로 "
    "긴장을 이어가 끝까지 보게 할 것.\n"
    "  - factcheck(뒷부분): narration은 '확인된 사실은 이겁니다'로 시작해 교차 "
    "검증되는 사실 한 가지만. 그리고 화면 표에 들어갈 facts_table도 만들 것 — "
    "'사실'(원문에서 확인된 사실, 완결 문장), '주장'(누가 무엇을 주장하는지, "
    "'○○측: …' 형태), '전망'(아직 확정 안 된 관측). 각 줄 30자 이내, 조사·이름 "
    "으로 끊지 말고 완결형으로.\n"
    "  - sides(뒷부분): 갈리는 입장을 딱 두 문장으로 — 각 문장이 한 진영. "
    "'민주당(진보층)은 …라는 이유로 …라고 봅니다.' / '국민의힘(보수층)은 …라는 "
    "이유로 …라고 봅니다.' 처럼 주체를 밝히고 '왜'를 한 줄 붙일 것. 전환어만 있는 "
    "문장('그런데 이렇게 갈립니다')은 쓰지 말 것. 어느 쪽도 편들지 말 것.\n"
    "  - outro(마지막): 한 줄로 정리한 뒤, 구독·좋아요·알림 설정을 자연스럽게 "
    "요청하는 문장을 넣을 것 (예: '이런 정치 이슈 30초로 정리해 드립니다. 구독과 "
    "좋아요 눌러주시면 큰 힘이 됩니다').\n"
    "10) 출력은 JSON 객체 하나만. 형식: "
    '{"title": ["1줄","2줄"], "hook": "새 내레이션", "what": "...", "sides": "...", '
    '"outro": "...", "facts_table": {"사실":"...","주장":"...","전망":"..."}}. '
    "문자열 안에서 인용이 필요하면 반드시 홑따옴표(')만 쓸 것(겹따옴표 금지)."
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
        f"[보도 매체 성향] {', '.join(meta.get('leans', [])) or '(불명)'}\n"
        f"[이 기사의 핵심 인물/주제] {meta.get('topic', '') or '(없음)'}\n\n"
        f"[다시 쓸 카드]\n{json.dumps(ask, ensure_ascii=False)}\n\n"
        "각 카드의 draft를 규칙대로 다시 쓰고, 눈길을 끄는 2줄 title도 지어 "
        "JSON으로만 답하세요. "
        '예: {"title":["김성수 후보 처남 전세","특혜 맞나?"],'
        '"hook":"새 내레이션...","what":"...","factcheck":"확인된 사실은...",'
        '"sides":"그런데 이걸 보는 눈은 이렇게 갈립니다. 민주당은... 국민의힘은...",'
        '"outro":"..."}'
    )


def _norm(v: str) -> str:
    return re.sub(r"\s+", " ", v).strip()


_ROLE_KEYS = {"hook", "summary", "what", "reaction", "factcheck", "sides", "outro"}


def _title_lines(v: Any) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"\s*[/|·\n]\s*", v)
    if not isinstance(v, list):
        return []
    out = [_norm(str(x)).strip('"\'“”·.') for x in v if str(x).strip()]
    out = [x for x in out if 1 <= len(x) <= 18][:2]
    return out if len(out) == 2 else []


_FC_TAGS = ("사실", "주장", "전망")


def _parse(raw: str) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """-> ({role: narration}, [title1, title2], {사실/주장/전망: text}). Tolerates
    the shapes a small model actually emits: flat {"hook": "..."}, nested
    {"hook": {"narration": "..."}}, an echoed {"cards": [...]}, or a bare list.
    """
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", txt).strip()
    inner = (re.search(r"\{.*\}", txt, re.S) or [None])[0]

    def _repair(s: str) -> str:
        s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        s = re.sub(r",\s*([}\]])", r"\1", s)          # trailing commas
        s = re.sub(r"[\x00-\x1f]+", " ", s)           # raw control chars / newlines
        return s

    obj = None
    for cand in (txt, inner, _repair(txt), _repair(inner or "")):
        if not cand:
            continue
        try:
            obj = json.loads(cand)
            break
        except Exception:
            continue
    if obj is None:
        return {}, [], {}
    title = _title_lines(obj.get("title")) if isinstance(obj, dict) else []
    ftab: dict[str, str] = {}
    _raw_ft = obj.get("facts_table") if isinstance(obj, dict) else None
    if isinstance(_raw_ft, dict):
        for k, v in _raw_ft.items():
            kk = str(k).strip()
            if kk in _FC_TAGS and str(v).strip():
                ftab[kk] = _norm(str(v))[:60]

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
    return out, title, ftab


def rewrite_segments(
    segments: list[dict[str, Any]], meta: dict[str, Any], cfg: Settings,
    base_script: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """(`segments` with narration rewritten by the LLM, a punchy 2-line title)
    — or (unchanged input, []) on any problem."""
    provider = (getattr(cfg, "llm_provider", "") or "").strip()
    if not provider:
        return segments, []
    spoken = [s for s in segments if s.get("role") in _LLM_LIMIT and s.get("narration")]
    if not spoken:
        return segments, []

    from .llm import complete
    payload = _payload(meta, spoken)
    new: dict[str, str] = {}
    title: list[str] = []
    ftab: dict[str, str] = {}
    last_exc = ""
    for attempt in range(3):                 # retry timeouts / unparseable re-gens
        try:
            raw = complete(payload, cfg, max_tokens=1400, system=_SYSTEM)
        except Exception as exc:  # pragma: no cover - network dependent
            last_exc = str(exc)
            log.info("llm rewrite: call %d failed (%s), retrying", attempt + 1, last_exc[:80])
            continue
        new, title, ftab = _parse(raw)
        if new:
            break
        log.info("llm rewrite: response %d unparseable, retrying", attempt + 1)
    if not new:
        log.warning("llm rewrite: gave up after retries (%s) — keeping template",
                    last_exc or "unparseable")
        return segments, []

    def _clean_row(v: str) -> str:
        v = re.sub(r"[·…—]", " ", re.sub(r"\s+", " ", v)).strip(" ·,")
        if len(v) <= 52:
            return v
        # clip to the last sentence end / comma within the budget, never mid-word
        head = v[:52]
        cut = max(head.rfind("니다"), head.rfind(". "), head.rfind(", "))
        return (head[:cut + 2] if cut >= 24 else head[:head.rfind(" ") or 52]).rstrip(" ,·")

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
        return segments, []

    # LLM-written fact-check TABLE rows (clean sentences, not 44-char article
    # clips). Keep the template's "확인" row (source count) at the end.
    if ftab:
        fc = next((s for s in cand if s.get("role") == "factcheck"), None)
        if fc:
            tone = {"사실": "ok", "주장": "claim", "전망": "warn"}
            new_rows = [{"tag": k, "tone": tone[k], "text": _clean_row(v)}
                        for k, v in ftab.items() if k in tone and len(_clean_row(v)) >= 6]
            keep = next((r for r in (fc.get("rows") or []) if r.get("tone") == "info"), None)
            new_rows.append(keep or {"tag": "확인", "tone": "info",
                                     "text": "여러 매체 종합, 원문은 더보기란"})
            if len(new_rows) >= 3:
                fc["rows"] = new_rows

    # title must MATCH the content: a 2+ char token of line 1 has to appear in
    # the headline or the rewritten narration, else drop it (template fallback).
    if title:
        body = clean_text(base_script.get("headline", "") if base_script else "") + " " + \
               " ".join(s.get("narration", "") for s in cand)
        toks = [w for w in re.findall(r"[가-힣]{2,}|[0-9]{2,}", title[0]) if len(w) >= 2]
        if toks and not any(w in body for w in toks):
            log.info("llm title %r doesn't match content — using template", " / ".join(title))
            title = []

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
            return segments, []
    except Exception as exc:  # pragma: no cover
        log.warning("llm rewrite: safety check errored (%s) — keeping template", exc)
        return segments, []

    log.info("llm narration rewrite: %d/%d cards via %s", changed, len(spoken), provider)
    return cand, title
