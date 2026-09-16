"""Optional narration rewrite — turns the templated card text into a natural,
lay-friendly explanation that flows card to card.

TWO STAGES, not one: a raw article sentence chopped to fit a card is still a
raw article sentence — a viewer who doesn't follow politics needed it
*explained*, not trimmed. So before any card gets written:
  1. `analyze_story()` reads the source + the fact/claim/interp split and
     writes a short structured "what did I just understand" — who each
     person/institution actually is, what happened in plain terms, why it's
     news *today*, which terms need glossing, where the sides disagree and
     why. Best-effort: on any failure this simply returns None.
  2. `rewrite_segments()` writes the actual cards FROM that understanding
     (not from the raw article) via the existing `_SYSTEM` prompt. If stage 1
     failed, it writes from the raw facts/claims/interps exactly as before —
     stage 1 is a quality layer in front of stage 2, never a requirement.

Hard rules, enforced in code (not just the prompt):
  * the LLM only ever REWRITES the `narration` of spoken cards; card structure,
    images, fact-check rows and sources are untouched;
  * both stages are given ONLY the cluster's own source text + the
    fact/claim/interp split from analyze.py — no outside knowledge is invited;
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
from .subtitle import _complete as _sentence_complete
from .textutil import clean_text, clip_sentence

log = get_logger("script_llm")

# chars each spoken card may run to. GENEROUS on purpose — a viewer who doesn't
# follow politics needs background + a plain-language explanation, not a
# one-line headline. Longer video is fine. Widened from the original
# 120/150/190 — those were tight enough that "배경 한 줄 + 무슨 일 + 왜 중요"
# (the SYSTEM prompt's own 3-beat rule) barely fit in one Korean sentence each.
# NOTE: "hook" is deliberately absent — the first ~2s is owned by the dedicated
# Hook Engine (hook_engine.py); the LLM never rewrites it.
_LLM_LIMIT = {"summary": 160, "what": 190, "reaction": 130,
              "factcheck": 150, "sides": 220, "outro": 80}

_SYSTEM = (
    "당신은 정치를 전혀 모르는 사람에게 오늘의 뉴스를 처음부터 풀어 설명하는 한국어 "
    "내레이션 작가입니다. 시청자는 이 사건도, 등장 인물도, 관련 제도도 모른다고 "
    "가정하세요. 주어진 '원문 기사'와 분류된 사실만 사용합니다.\n"
    "[분석 1단계] 블록이 함께 주어지면, 그건 당신이 이미 이 기사를 다 읽고 이해해서 "
    "정리해 둔 내용입니다. 원문을 다시 해석할 필요 없이 그 이해를 바탕으로 곧장 "
    "새 문장을 쓰세요.\n"
    "가장 중요한 원칙 — 절대 원문 문장을 그대로 자르거나 이어붙이지 말 것. 기사에 "
    "있는 표현을 요약·발췌하는 게 아니라, 이해한 내용을 처음부터 새로 풀어 씁니다. "
    "'~고 할 수 있다'며 인용부호를 어중간하게 자르는 것 (X). 모든 카드에서 (1) "
    "배경 — 왜 이런 일이 생겼는지, 이 사람·기관이 뭐 하는 곳인지 — 을 먼저 한 줄로 "
    "깔고, (2) 그래서 무슨 일이 있었는지, (3) 그게 왜 문제이고 뭘 의미하는지까지 "
    "설명하세요. '김승원이 사퇴했습니다' (X) → '대통령의 정책을 총괄하는 정책실장 "
    "김승원이, 취임 두 달 만에 물러났습니다. 정부 출범 초기라 이례적입니다' (O).\n"
    "규칙:\n"
    "1) 인물·기관·전문용어는 처음 나올 때 반드시 짧게 풀 것: '정책실장(정부 정책을 "
    "총괄하는 자리)', '인사청문회(장관 후보 자격을 국회가 검증하는 절차)'. [분석 1단계]의 "
    "terms/who에 있는 풀이를 그대로 활용할 것.\n"
    "2) 카드가 이야기처럼 이어지게: 앞 카드 내용을 딛고 다음으로. '그런데', '문제는 "
    "이겁니다', '쉽게 말하면' 같은 연결을 쓰되 내용 있는 문장 안에 붙일 것.\n"
    "3) 쉬운 말, 한 문장 40자 안팎 — 단, 이건 참고용 목표일 뿐입니다. 40자를 지키려고 "
    "문장을 완결하지 않은 채 끝내는 것은 절대 금지: 명사만 남기고 서술어를 "
    "생략하지 말 것. '통일부는 남북관계를 총괄하는 대한민국.' (X, 명사로 뚝 끊김) "
    "→ '통일부는 남북관계를 총괄하는 정부 부처입니다.' (O). 조금 길어지더라도 "
    "'~습니다/~합니다/~됩니다'로 끝나는 완결된 문장이 글자 수보다 항상 우선입니다. "
    "조사·연결어미('…에 따르면 / …라며 / …인데 / …곳이')로 끝내지 말 것. 인용은 "
    "필요할 때만 짧게, 반드시 여는 따옴표와 닫는 따옴표를 함께 쓸 것 — 절대 "
    "따옴표를 연 채로 문장을 끝내지 말 것.\n"
    "4) 철저히 중립: 한쪽 편을 들거나 비꼬지 말 것. 단, 인물 이름은 그대로 쓸 것 — "
    "이재명 대통령, 한동훈, 윤석열 전 대통령처럼 유명 인물은 또렷하게 직접 언급.\n"
    "5) 입장을 옮길 땐 주체를 분명히: '국민의힘은 …, 민주당은 …', 특정인이면 이름. "
    "불분명하면 '온라인에서는 …는 반응이 나옵니다'. [분석 1단계]의 sides가 있으면 "
    "그 이유(why)까지 살려 쓸 것 — 그냥 입장만 나열하지 말 것.\n"
    "6) 원문에 없는 사실·숫자·발언을 지어내지 말 것.\n"
    "7) 화면 너머 시청자에게 직접 설명하듯 쓸 것 — 사실을 나열만 하고 끝내는 "
    "'혼자 말하고 혼자 이해하는' 느낌을 절대 남기지 말 것. 각 카드가 사실 전달에서 "
    "끝나지 않고, 그게 왜 중요한지·시청자에게 어떤 의미인지까지 매번 짚을 것. "
    "감정이나 반응을 지어내거나 과장하지 말 것 — 화나야 한다거나 안타까워해야 "
    "한다고 말하지 말고, 사실 자체가 가진 구체적인 무게(숫자, 결과, 파장)를 "
    "명확한 언어로 전달하면 그것으로 충분합니다. 추상적인 정치 용어("
    "'정국 주도권', '정치적 함의')보다 시청자가 바로 실감할 수 있는 구체적 표현을 "
    "쓸 것.\n"
    "8) 실제 방송 기자가 카메라 앞에서 말하듯 쓸 것 — 보도자료·통신문 특유의 "
    "명사 나열식 긴 수식어 뭉치는 절대 금지. '임신 9주 이하 임신부를 대상으로 한 "
    "임신중지약 도입 및 초기 2년간 병원 처방, 조제 방침을 공식 발표했습니다' (X, "
    "명사를 계속 붙여 쓴 보도자료 문장) → '정부가 임신중지약을 도입하기로 했습니다. "
    "대상은 임신 9주 이하 임신부이고, 처음 2년은 병원에서만 처방받을 수 있습니다' "
    "(O, 짧은 문장 여러 개로 나눠 말하듯). 한 문장에 수식어가 두 개 이상 겹치면 "
    "무조건 문장을 끊어서 새로 시작할 것. 전망/마무리는 특히 '~지켜봐야 합니다', "
    "'~주목됩니다' 같은 막연한 관망형 문장으로 절대 끝내지 말 것 — 무엇이 어떻게 "
    "바뀌는지, 누구에게 어떤 영향인지를 구체적인 서술어로 끝맺을 것.\n\n"
    "카드별 역할 (유튜브 쇼츠 몰입 곡선):\n"
    "  - (hook 카드는 별도 엔진이 만듭니다. 당신은 hook을 쓰지 마세요.)\n"
    "  - summary: 이 사건의 '배경'. 이 인물·기관이 뭐 하는 곳인지, 왜 지금 이게 "
    "이슈인지 2~3문장으로 깔아줄 것.\n"
    "  - what: 실제로 무슨 일이 있었는지 + 그게 왜 특이하거나 중요한지. '그런데', "
    "'여기서 진짜 핵심은'으로 이어가며 2~3문장.\n"
    "  - factcheck: narration은 '확인된 사실은 이겁니다.'로 시작 → 교차 검증된 사실 "
    "한 가지 → '이게 무슨 뜻이냐면 …'으로 그 사실의 의미를 한 문장 더 설명. 이 '사실' "
    "문장에는 최소한 육하원칙의 핵심 — 언제(원문에 날짜가 있으면 '9월 15일'처럼 반드시 "
    "포함), 누가(이름 + 소속·직함, 예: '더불어민주당 김민석 대표'), 무엇을 했는지("
    "'~라고 밝혔다/발표했다/논의했다' 같은 구체적 행위) — 가 들어갈 것. 헤드라인을 "
    "따옴표로 그대로 옮겨 붙이는 것 (X) — 반드시 새 문장으로 풀어 쓸 것. "
    "그리고 화면 표용 facts_table도: '사실'(확인된 사실, 완결 문장), '주장'('○○측: …'), "
    "'전망'(아직 확정 안 된 관측). 각 줄 45자 이내, 완결형.\n"
    "  - sides: 입장이 갈리는 지점을 각 진영별로. '민주당(진보 쪽)은 …라는 이유로 "
    "…라고 봅니다.' / '국민의힘(보수 쪽)은 …라는 이유로 …라고 봅니다.' 각 진영에 "
    "'왜 그렇게 보는지' 근거를 붙여 2~3문장. 전환어만 있는 문장 금지. 편들지 말 것.\n"
    "  - outro: 이 영상의 마지막 인상을 남기는 자리 — '그래서 이게 왜 중요한지' 또는 "
    "'앞으로 어떻게 될지'를 구체적인 한 문장으로 짚을 것. [분석 1단계]의 "
    "why_it_matters를 활용하거나, hook이 던진 질문에 답하거나 긴장을 한 번 더 "
    "환기할 것. '여러 매체 종합했습니다' 같은 제작 과정 언급, '~주목됩니다'/"
    "'~지켜봐야 합니다'만 반복하는 두루뭉술한 전망은 금지 — 왜 주목해야 하는지, "
    "무엇을 지켜봐야 하는지까지 구체적으로. 그 다음에 '구독과 좋아요 눌러주시면 "
    "큰 힘이 됩니다' 류의 요청.\n\n"
    "subtitles(화면 자막): 음성과 별개. 각 카드의 '핵심 메시지'만 14자 이내로 "
    "압축한 짧은 명사구. 음성 문장을 그대로 쓰지 말 것. 한 화면에 하나의 메시지만. "
    "인용 부호·조사·연결어미로 끝내지 말고 명사 또는 '~다'로 끝낼 것. sides 자막은 "
    "'A 주장 vs B 주장' 형태로. 예: summary 음성이 '김승원은 정책을 총괄하는 "
    "참모입니다. 3일 사퇴했습니다'면 자막은 '김승원 정책실장 전격 사퇴'. "
    "{\"summary\":\"…\",\"what\":\"…\",\"factcheck\":\"…\",\"sides\":\"…\",\"outro\":\"…\"}\n\n"
    "title(화면 상단 2줄 제목): 실제 올라간 인기 영상 제목 형식을 따를 것 — "
    "1줄은 유명 인물/사건을 구체적으로(예: '한동훈 녹취록 공개', '이재명 대통령 임기 "
    "발언'), 2줄은 구어체 궁금증 종결(예: '유출 경위 조사할까?', '무슨 일일까요?', "
    "'진짜일까?', '왜 논란인가'). 각 줄 16자 이내. '지금 이 이슈', '핵심만', '쟁점 "
    "정리' 같은 맹탕 문구 절대 금지. 내용과 반드시 일치, 과장·비하 없이.\n\n"
    "출력은 JSON 하나만: "
    '{"title": ["1줄","2줄"], "summary": "...", "what": "...", '
    '"factcheck": "...", "sides": "...", "outro": "...", '
    '"subtitles": {"summary":"…","what":"…","factcheck":"…","sides":"…","outro":"…"}, '
    '"facts_table": {"사실":"...","주장":"...","전망":"..."}}. '
    "문자열 안 인용은 홑따옴표(')만."
)


# --------------------------------------------------------------------------- #
# STAGE 1 — understand the story before writing it.
# --------------------------------------------------------------------------- #
_ANALYSIS_SYSTEM = (
    "당신은 정치부 데스크입니다. 대본을 쓰는 게 아니라, 아래 원문 기사와 사실/주장/"
    "해석 분류를 읽고 '이해한 내용'만 구조화해서 정리합니다. 정치를 전혀 모르는 "
    "시청자에게 이 사건을 설명하려면 무엇을 알아야 하는지 기준으로 생각하세요.\n"
    "규칙:\n"
    "1) who: 등장하는 인물·기관 각각이 '무엇을 하는 사람·곳인지' 한 문장으로. "
    "예: {\"name\":\"김민석\",\"role\":\"더불어민주당 대표 — 여당 원내 1당의 대표\"}.\n"
    "2) what_happened: 실제로 일어난 일을 사실관계만으로 2문장 이내, 인용부호 "
    "없이 평서문으로 새로 쓸 것 (원문 문장을 그대로 옮기지 말 것).\n"
    "3) why_now: 왜 하필 오늘 이게 뉴스가 됐는지(발단이 된 사건) 1문장.\n"
    "4) why_it_matters: 이 사안이 시청자에게 구체적으로 왜 중요한지 1~2문장 — "
    "'정치인의 말은 논란이 될 수 있다' 같은 일반론 말고, 이 사건 고유의 이유.\n"
    "5) terms: 시청자가 모를 수 있는 용어·비유·과거 사건을 {\"용어\":\"한 줄 풀이\"}로, "
    "있는 만큼만 — 없으면 빈 객체.\n"
    "6) sides: 입장이 갈리면 [{\"who\":\"...\",\"position\":\"...\",\"why\":\"...\"}]로, "
    "없으면 빈 배열.\n"
    "7) confirmed_fact: 여러 출처가 교차 확인한 확실한 사실 한 문장, 완결형.\n"
    "원문에 없는 사실을 지어내지 말 것. 출력은 JSON 하나만:\n"
    '{"who":[{"name":"...","role":"..."}],"what_happened":"...","why_now":"...",'
    '"why_it_matters":"...","terms":{"...":"..."},'
    '"sides":[{"who":"...","position":"...","why":"..."}],"confirmed_fact":"..."}'
)


def _analysis_payload(meta: dict[str, Any]) -> str:
    def _bullets(items: list[str], cap: int) -> str:
        return "\n".join(f"- {re.sub(r'\\s+', ' ', t).strip()[:200]}" for t in items[:cap]) or "- (없음)"

    ent = meta.get("entities", {})
    who = ", ".join(
        [f"{n}(대통령)" if n == ent.get("president") else n
         for n in (ent.get("politicians") or [])][:8]
        + (ent.get("parties") or [])[:4]
    ) or "(특정 인물 없음)"
    return (
        f"[원문 기사]\n{meta.get('source_text', '').strip()[:3200]}\n\n"
        f"[사실로 분류된 문장]\n{_bullets(meta.get('facts', []), 10)}\n\n"
        f"[주장 — 누가 말한 것]\n{_bullets(meta.get('claims', []), 8)}\n\n"
        f"[해석·전망 — 사실 아님, 참고만]\n{_bullets(meta.get('interps', []), 6)}\n\n"
        f"[등장 인물·정당] {who}\n\n"
        "위 내용을 다 읽고 이해한 대로 JSON 하나로 정리하세요."
    )


def _parse_understanding(raw: str) -> dict[str, Any] | None:
    """-> the stage-1 structured understanding, or None if the model's output
    wasn't usable JSON — the caller falls back to writing from raw facts."""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", txt).strip()
    inner = (re.search(r"\{.*\}", txt, re.S) or [None])[0]

    def _repair(s: str) -> str:
        s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        s = re.sub(r",\s*([}\]])", r"\1", s)
        s = re.sub(r"[\x00-\x1f]+", " ", s)
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
    if not isinstance(obj, dict):
        return None
    if not (str(obj.get("what_happened") or "").strip()
            or str(obj.get("confirmed_fact") or "").strip()):
        return None                              # too empty to be useful
    return obj


def analyze_story(meta: dict[str, Any], cfg: Settings) -> dict[str, Any] | None:
    """Stage 1: read the source once and understand it — who's who, what
    actually happened, why it's news today, what needs glossing, where the
    sides disagree and why. Best-effort: on ANY failure (no key, HTTP error,
    unparseable/empty JSON) returns None, and the caller (`rewrite_segments`)
    writes straight from the raw facts/claims/interps exactly as it did
    before this existed."""
    provider = (getattr(cfg, "llm_provider", "") or "").strip()
    if not provider:
        return None
    from .llm import complete

    payload = _analysis_payload(meta)
    for attempt in range(2):
        try:
            raw = complete(payload, cfg, max_tokens=1300, system=_ANALYSIS_SYSTEM)
        except Exception as exc:  # pragma: no cover - network dependent
            log.info("story analysis: call %d failed (%s)", attempt + 1, str(exc)[:80])
            continue
        obj = _parse_understanding(raw)
        if obj:
            return obj
        log.info("story analysis: response %d unparseable, retrying", attempt + 1)
    log.info("story analysis: gave up — writing from raw facts/claims instead")
    return None


def _understanding_block(u: dict[str, Any] | None) -> str:
    """Stage 1's output, formatted for stage 2's prompt. Empty string when
    stage 1 didn't run or didn't return anything usable."""
    if not u:
        return ""
    who = [w for w in (u.get("who") or []) if isinstance(w, dict) and w.get("name")]
    who_lines = "\n".join(f"- {w.get('name', '')}: {w.get('role', '')}" for w in who) or "- (없음)"
    terms = u.get("terms") if isinstance(u.get("terms"), dict) else {}
    term_lines = "\n".join(f"- {k}: {v}" for k, v in terms.items() if k and v) or "- (없음)"
    sides = [s for s in (u.get("sides") or []) if isinstance(s, dict) and s.get("who")]
    side_lines = "\n".join(
        f"- {s.get('who', '')}: {s.get('position', '')} (이유: {s.get('why', '')})" for s in sides
    ) or "- (없음)"
    return (
        "[분석 1단계 — 이미 이해해 정리해 둔 내용. 이걸 바탕으로 새 문장을 쓸 것]\n"
        f"등장 인물/기관:\n{who_lines}\n"
        f"실제로 있었던 일: {u.get('what_happened', '')}\n"
        f"왜 하필 지금: {u.get('why_now', '')}\n"
        f"왜 중요한지: {u.get('why_it_matters', '')}\n"
        f"풀어줘야 할 용어:\n{term_lines}\n"
        f"입장 차이:\n{side_lines}\n"
        f"확인된 사실: {u.get('confirmed_fact', '')}\n\n"
    )


def _payload(meta: dict[str, Any], cards: list[dict[str, Any]],
            understanding: dict[str, Any] | None = None) -> str:
    def _bullets(items: list[str], cap: int = 8) -> str:
        return "\n".join(f"- {re.sub(r'\\s+', ' ', t).strip()[:160]}" for t in items[:cap]) or "- (없음)"

    ent = meta.get("entities", {})
    who = ", ".join(
        [f"{n}(대통령)" if n == ent.get("president") else n
         for n in (ent.get("politicians") or [])][:8]
        + (ent.get("parties") or [])[:4]
    ) or "(특정 인물 없음)"
    # once stage 1 has actually understood the story, the template's own
    # narration draft is a distraction (it's sometimes just a chopped article
    # quote) — keep it out entirely and lean on the understanding instead.
    ub = _understanding_block(understanding)
    ask = {"cards": [{"role": c["role"], "limit": _LLM_LIMIT.get(c["role"], 80)}
                     if ub else
                     {"role": c["role"], "limit": _LLM_LIMIT.get(c["role"], 80),
                      "draft": c.get("narration", "")}
                     for c in cards]}
    src_excerpt = meta.get("source_text", "").strip()[: (1200 if ub else 2600)]
    draft_note = ("" if ub else
                  "각 카드의 draft는 참고용 초안일 뿐입니다 — 그대로 다듬지 말고, 규칙대로 "
                  "(배경→무슨 일→왜 중요) 완전히 새 문장으로 풀어 쓸 것.\n")
    return (
        f"{ub}"
        f"[원문 기사{'  — 확인용, 문장을 그대로 옮기지 말 것' if ub else ''}]\n{src_excerpt}\n\n"
        f"[사실로 분류된 문장]\n{_bullets(meta.get('facts', []))}\n\n"
        f"[주장 — 누가 말한 것]\n{_bullets(meta.get('claims', []))}\n\n"
        f"[해석·전망 — 사실 아님, 참고만]\n{_bullets(meta.get('interps', []), 5)}\n\n"
        f"[등장 인물·정당] {who}\n"
        f"[보도 매체 성향] {', '.join(meta.get('leans', [])) or '(불명)'}\n"
        f"[이 기사의 핵심 인물/주제] {meta.get('topic', '') or '(없음)'}\n\n"
        f"[쓸 카드]\n{json.dumps(ask, ensure_ascii=False)}\n\n"
        f"{draft_note}"
        "각 카드를 규칙대로(배경→무슨 일→왜 중요) 새로 쓰고, 인기 영상 형식의 눈길 "
        "끄는 2줄 title도 지어 JSON으로만 답하세요. (hook은 쓰지 마세요.)\n"
        '예: {"title":["한동훈 녹취록 공개","유출 경위 조사할까?"],'
        '"summary":"한동훈은 국민의힘 대표를 지낸 인물인데, ...",'
        '"what":"그런데 이번에 공개된 녹취록에는 ...","factcheck":"확인된 사실은 이겁니다. ... '
        '이게 무슨 뜻이냐면 ...","sides":"민주당은 ...라는 이유로 ...","outro":"..."}'
    )


def _norm(v: str) -> str:
    return re.sub(r"\s+", " ", v).strip()


def _ends_cleanly(narr: str) -> bool:
    """A card the LLM wrote must end on a real predicate, not a bare noun
    phrase — a real production case: within the ~40자 length guideline but
    stopped at '...총괄하는 대한민국.' instead of '...정부 부처입니다.'. Reuses
    subtitle._complete's sentence-final-form check (다/요/까/죠/네/?/!)."""
    return _sentence_complete(narr.rstrip())


# The outro prompt (_SYSTEM above) explicitly bans "두루뭉술한 전망" endings —
# but that's a prompt instruction, not a guarantee, and a real production
# case shipped one anyway ("...안착할지 지켜봐야 합니다."). Enforced in code
# now: an outro that closes on nothing but a bare watch-and-see cliché, with
# no concrete "why/what happens next" attached, is rejected the same way an
# incomplete sentence is — falling back to the template's payoff-led outro
# instead of a filler line dressed as a conclusion.
_VAGUE_OUTRO_END = re.compile(
    r"(?:지켜봐야\s*(?:겠습니다|합니다|할\s*것입니다|하는지도\s*모릅니다)|"
    r"주목됩니다|주목할\s*만합니다|주목받고\s*있습니다|"
    r"관심이\s*집중되고\s*있습니다|귀추가\s*주목됩니다)\s*\.?\s*$"
)


def _is_vague_outro(narr: str) -> bool:
    return bool(_VAGUE_OUTRO_END.search(clean_text(narr)))


# "hook" intentionally excluded — owned by hook_engine.py, never LLM-written
_ROLE_KEYS = {"summary", "what", "reaction", "factcheck", "sides", "outro"}


def _title_lines(v: Any) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"\s*[/|·\n]\s*", v)
    if not isinstance(v, list):
        return []
    out = [_norm(str(x)).strip('"\'“”·.') for x in v if str(x).strip()]
    out = [x for x in out if 1 <= len(x) <= 18][:2]
    return out if len(out) == 2 else []


_FC_TAGS = ("사실", "주장", "전망")


def _parse(raw: str) -> tuple[dict[str, str], list[str], dict[str, str], dict[str, str]]:
    """-> ({role: narration}, [title1, title2], {사실/주장/전망: text},
    {role: subtitle}). Tolerates the shapes a small model actually emits: flat
    {"hook": "..."}, nested {"hook": {"narration": "..."}}, an echoed
    {"cards": [...]}, or a bare list.
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
        return {}, [], {}, {}
    title = _title_lines(obj.get("title")) if isinstance(obj, dict) else []
    ftab: dict[str, str] = {}
    _raw_ft = obj.get("facts_table") if isinstance(obj, dict) else None
    if isinstance(_raw_ft, dict):
        for k, v in _raw_ft.items():
            kk = str(k).strip()
            if kk in _FC_TAGS and str(v).strip():
                ftab[kk] = _norm(str(v))[:60]
    subs: dict[str, str] = {}
    _raw_sub = obj.get("subtitles") if isinstance(obj, dict) else None
    if isinstance(_raw_sub, dict):
        from .subtitle import valid_llm_subtitle
        for k, v in _raw_sub.items():
            kk = str(k).strip()
            vv = valid_llm_subtitle(str(v))
            if kk in _ROLE_KEYS and vv:
                subs[kk] = vv

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
    return out, title, ftab, subs


def rewrite_segments(
    segments: list[dict[str, Any]], meta: dict[str, Any], cfg: Settings,
    base_script: dict[str, Any] | None = None, feedback: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    """(`segments` with narration rewritten by the LLM, a punchy 2-line title)
    — or (unchanged input, []) on any problem.

    `feedback`: when the quality agent (quality_agent.py) graded a PRIOR
    attempt below its bar, its specific critique is passed here so this
    rewrite can actually fix what was wrong instead of blindly retrying."""
    provider = (getattr(cfg, "llm_provider", "") or "").strip()
    if not provider:
        return segments, []
    spoken = [s for s in segments if s.get("role") in _LLM_LIMIT and s.get("narration")]
    if not spoken:
        return segments, []

    from .llm import complete
    # STAGE 1 — understand the story first. Best-effort; None just means
    # stage 2 writes from the raw facts/claims/interps as it always did.
    story = analyze_story(meta, cfg)
    payload = _payload(meta, spoken, understanding=story)
    if feedback.strip():
        payload += (
            "\n\n[이전 시도에 대한 편집장 피드백 — 이번엔 반드시 고쳐서 다시 쓸 것]\n"
            f"{feedback.strip()}\n"
        )
    new: dict[str, str] = {}
    title: list[str] = []
    ftab: dict[str, str] = {}
    subs: dict[str, str] = {}
    last_exc = ""
    for attempt in range(3):                 # retry timeouts / unparseable re-gens
        try:
            raw = complete(payload, cfg, max_tokens=2200, system=_SYSTEM)
        except Exception as exc:  # pragma: no cover - network dependent
            last_exc = str(exc)
            log.info("llm rewrite: call %d failed (%s), retrying", attempt + 1, last_exc[:80])
            continue
        new, title, ftab, subs = _parse(raw)
        if new:
            break
        log.info("llm rewrite: response %d unparseable, retrying", attempt + 1)
    if not new:
        log.warning("llm rewrite: gave up after retries (%s) — keeping template",
                    last_exc or "unparseable")
        return segments, []

    def _clean_row(v: str) -> str:
        # same on-screen table as factcheck.rows() (44px, ~830px column, 2-line
        # cap) — same _ROW_BUDGET-equivalent, and the same quote/clause-safe
        # trim so a row the LLM writes over budget doesn't silently end
        # mid-sentence with no predicate the way a raw space-cut did.
        v = re.sub(r"[·…—]", " ", re.sub(r"\s+", " ", v)).strip(" ·,")
        out = clip_sentence(v, 52, ell="..")
        if not out.endswith("..") and not _ends_cleanly(out):
            # short enough that clip_sentence never had to cut anything, but
            # the model's own sentence still isn't grammatically finished
            # ("...협력이 더욱 깊어질") — a real production case. Mark it the
            # same way an actual truncation would be, rather than present a
            # broken sentence as complete.
            out = f"{out}.."
        return out

    # apply — only where the model returned a sane narration for a card we have
    cand = [dict(s) for s in segments]
    changed = 0
    for s in cand:
        narr = new.get(s.get("role", ""))
        lim = _LLM_LIMIT.get(s.get("role", ""))
        if not narr or lim is None:          # lim is None -> not LLM-owned (e.g. hook)
            continue
        if not (8 <= len(narr) <= int(lim * 1.8)):
            continue
        if not _ends_cleanly(narr):
            # the model wrote a real sentence-length-wise but stopped on a bare
            # noun with no predicate ("...총괄하는 대한민국.") — a real case seen
            # in production. Falls back to the template's narration for just
            # this card rather than ship a fragment that reads as finished.
            log.info("llm card %r ends without a predicate (%r) — keeping template",
                     s.get("role"), narr[-16:])
            continue
        if s.get("role") == "outro" and _is_vague_outro(narr):
            log.info("llm outro is a vague watch-and-see filler (%r) — keeping template",
                     narr[-20:])
            continue
        s["narration"] = narr
        s.pop("caption", None)               # re-derived cleanly in script_gen
        if subs.get(s["role"]):              # LLM-written on-screen subtitle
            s["_llm_sub"] = subs[s["role"]]
        changed += 1
    if not changed:
        return segments, []

    # LLM-written fact-check TABLE rows (clean sentences, not 44-char article
    # clips). Keep the template's "확인" row (source count) at the end.
    if ftab:
        fc = next((s for s in cand if s.get("role") == "factcheck"), None)
        if fc:
            tone = {"사실": "ok", "주장": "claim", "전망": "warn"}
            # carry the source attribution from the template rows the FACT CHECK
            # ENGINE built, so the on-screen table + quality check keep it
            _osrc = {r.get("tone"): r.get("source", "") for r in (fc.get("rows") or [])}
            _allsrc = ", ".join(m.get("name", "") for m in (meta.get("sources") or []) if m.get("name"))
            new_rows = [{"tag": k, "tone": tone[k], "text": _clean_row(v),
                         "source": _osrc.get(tone[k]) or _allsrc or "여러 매체"}
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

    log.info("llm narration rewrite: %d/%d cards via %s%s", changed, len(spoken), provider,
             " (analyzed first)" if story else " (no analysis stage)")
    return cand, title
