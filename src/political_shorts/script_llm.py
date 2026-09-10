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

# chars each spoken card may run to. GENEROUS on purpose — a viewer who doesn't
# follow politics needs background + a plain-language explanation, not a
# one-line headline. Longer video is fine.
_LLM_LIMIT = {"hook": 60, "summary": 120, "what": 150, "reaction": 110,
              "factcheck": 130, "sides": 190, "outro": 80}

_SYSTEM = (
    "당신은 정치를 전혀 모르는 사람에게 오늘의 뉴스를 처음부터 풀어 설명하는 한국어 "
    "내레이션 작가입니다. 시청자는 이 사건도, 등장 인물도, 관련 제도도 모른다고 "
    "가정하세요. 주어진 '원문 기사'와 분류된 사실만 사용합니다.\n"
    "가장 중요한 원칙: 절대 사실만 툭툭 던지지 말 것. 모든 카드에서 (1) 배경 — "
    "왜 이런 일이 생겼는지, 이 사람·기관이 뭐 하는 곳인지 — 을 먼저 한 줄로 깔고, "
    "(2) 그래서 무슨 일이 있었는지, (3) 그게 왜 문제이고 뭘 의미하는지까지 설명하세요. "
    "'김승원이 사퇴했습니다' (X) → '대통령의 정책을 총괄하는 정책실장 김승원이, "
    "취임 두 달 만에 물러났습니다. 정부 출범 초기라 이례적입니다' (O).\n"
    "규칙:\n"
    "1) 인물·기관·전문용어는 처음 나올 때 반드시 짧게 풀 것: '정책실장(정부 정책을 "
    "총괄하는 자리)', '인사청문회(장관 후보 자격을 국회가 검증하는 절차)'.\n"
    "2) 카드가 이야기처럼 이어지게: 앞 카드 내용을 딛고 다음으로. '그런데', '문제는 "
    "이겁니다', '쉽게 말하면' 같은 연결을 쓰되 내용 있는 문장 안에 붙일 것.\n"
    "3) 쉬운 말, 한 문장 40자 안팎. 모든 문장을 '~습니다/~합니다/~됩니다'로 끝낼 것. "
    "조사·연결어미('…에 따르면 / …라며 / …인데 / …곳이')로 끝내지 말 것.\n"
    "4) 철저히 중립: 한쪽 편을 들거나 비꼬지 말 것. 단, 인물 이름은 그대로 쓸 것 — "
    "이재명 대통령, 한동훈, 윤석열 전 대통령처럼 유명 인물은 또렷하게 직접 언급.\n"
    "5) 입장을 옮길 땐 주체를 분명히: '국민의힘은 …, 민주당은 …', 특정인이면 이름. "
    "불분명하면 '온라인에서는 …는 반응이 나옵니다'.\n"
    "6) 원문에 없는 사실·숫자·발언을 지어내지 말 것.\n\n"
    "카드별 역할 (유튜브 쇼츠 몰입 곡선):\n"
    "  - hook: 가장 세거나 의외인 사실 한 방. 핵심 숫자가 있으면 그 숫자로 시작. "
    "낚시·과장 없이.\n"
    "  - summary: 이 사건의 '배경'. 이 인물·기관이 뭐 하는 곳인지, 왜 지금 이게 "
    "이슈인지 2~3문장으로 깔아줄 것.\n"
    "  - what: 실제로 무슨 일이 있었는지 + 그게 왜 특이하거나 중요한지. '그런데', "
    "'여기서 진짜 핵심은'으로 이어가며 2~3문장.\n"
    "  - factcheck: narration은 '확인된 사실은 이겁니다.'로 시작 → 교차 검증된 사실 "
    "한 가지 → '이게 무슨 뜻이냐면 …'으로 그 사실의 의미를 한 문장 더 설명. "
    "그리고 화면 표용 facts_table도: '사실'(확인된 사실, 완결 문장), '주장'('○○측: …'), "
    "'전망'(아직 확정 안 된 관측). 각 줄 45자 이내, 완결형.\n"
    "  - sides: 입장이 갈리는 지점을 각 진영별로. '민주당(진보 쪽)은 …라는 이유로 "
    "…라고 봅니다.' / '국민의힘(보수 쪽)은 …라는 이유로 …라고 봅니다.' 각 진영에 "
    "'왜 그렇게 보는지' 근거를 붙여 2~3문장. 전환어만 있는 문장 금지. 편들지 말 것.\n"
    "  - outro: 한 줄 정리 + '구독과 좋아요 눌러주시면 큰 힘이 됩니다' 류의 요청.\n\n"
    "title(화면 상단 2줄 제목): 실제 올라간 인기 영상 제목 형식을 따를 것 — "
    "1줄은 유명 인물/사건을 구체적으로(예: '한동훈 녹취록 공개', '이재명 대통령 임기 "
    "발언'), 2줄은 구어체 궁금증 종결(예: '유출 경위 조사할까?', '무슨 일일까요?', "
    "'진짜일까?', '왜 논란인가'). 각 줄 16자 이내. '지금 이 이슈', '핵심만', '쟁점 "
    "정리' 같은 맹탕 문구 절대 금지. 내용과 반드시 일치, 과장·비하 없이.\n\n"
    "출력은 JSON 하나만: "
    '{"title": ["1줄","2줄"], "hook": "...", "summary": "...", "what": "...", '
    '"factcheck": "...", "sides": "...", "outro": "...", '
    '"facts_table": {"사실":"...","주장":"...","전망":"..."}}. '
    "문자열 안 인용은 홑따옴표(')만."
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
        "각 카드의 draft를 규칙대로(배경→무슨 일→왜 중요) 풀어 쓰고, 인기 영상 "
        "형식의 눈길 끄는 2줄 title도 지어 JSON으로만 답하세요.\n"
        '예: {"title":["한동훈 녹취록 공개","유출 경위 조사할까?"],'
        '"hook":"...","summary":"한동훈은 국민의힘 대표를 지낸 인물인데, ...",'
        '"what":"그런데 이번에 공개된 녹취록에는 ...","factcheck":"확인된 사실은 이겁니다. ... '
        '이게 무슨 뜻이냐면 ...","sides":"민주당은 ...라는 이유로 ...","outro":"..."}'
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
