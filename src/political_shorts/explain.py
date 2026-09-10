"""Deterministic "explain it to someone who doesn't follow politics" layer.

The LLM rewrite (`script_llm`) is the *preferred* way to get background +
plain-language explanation, but a free key is flaky and may be absent. This
module builds the same shape — (1) who/what is involved, (2) what happened,
(3) why it matters — from the frame, the detected entities, and a small hand
glossary, so the templated fallback still teaches instead of just clipping a
headline.

Nothing here invents facts: the glossary only explains what a *role /
institution / procedure* is (stable, dictionary-style), and the significance
lines are generic-but-true statements about that *kind* of event.
"""
from __future__ import annotations

import re

from .hook import Entities, Frame, josa, to_polite
from .textutil import clean_text, clip_sentence

# --------------------------------------------------------------------------- #
# 1) glossary — what a role / body / procedure IS (one plain phrase, ends on a
#    noun so a caller can append "입니다").
# --------------------------------------------------------------------------- #
ROLE_GLOSS: dict[str, str] = {
    "대통령": "행정부를 이끄는 국가의 최고 책임자",
    "국무총리": "대통령을 도와 각 부처를 총괄하는 행정부 2인자",
    "부총리": "총리를 도와 경제나 사회 정책을 총괄하는 자리",
    "정책실장": "대통령의 정책을 총괄하는 대통령실의 핵심 참모",
    "비서실장": "대통령을 가장 가까이서 보좌하며 참모진을 지휘하는 자리",
    "안보실장": "외교·국방·통일을 조율하는 국가안보의 컨트롤타워",
    "정무수석": "대통령실과 국회·정치권 사이를 조율하는 참모",
    "민정수석": "대통령실에서 사정·법무 현안을 담당하는 참모",
    "수석": "대통령을 보좌해 특정 분야를 책임지는 참모",
    "대변인": "조직의 공식 입장을 언론에 대신 전하는 자리",
    "장관": "정부 한 부처를 책임지고 이끄는 자리",
    "차관": "장관을 보좌하며 부처 실무를 총괄하는 자리",
    "원내대표": "국회에서 소속 정당 의원들을 이끌고 협상을 지휘하는 자리",
    "당대표": "당을 대표하고 주요 결정을 이끄는 정당의 최고 책임자",
    "대표": "당을 대표하고 주요 결정을 이끄는 정당의 최고 책임자",
    "국회의장": "국회를 대표하고 본회의를 주재하는 자리",
    "위원장": "국회 상임위원회를 이끌며 법안 심사를 주재하는 자리",
    "검찰총장": "전국 검찰을 지휘하는 검찰 조직의 최고 수장",
    "경찰청장": "전국 경찰을 지휘하는 경찰 조직의 최고 수장",
    "대법원장": "대법원을 이끌고 사법부를 대표하는 자리",
}

BODY_GLOSS: dict[str, str] = {
    "본회의": "국회의원 전원이 모여 법안을 최종 표결하는 자리",
    "법사위": "다른 상임위를 거친 법안을 마지막으로 심사하는 상임위",
    "운영위": "국회 운영과 대통령실 업무를 담당하는 상임위",
    "예결위": "정부 예산안과 결산을 심사하는 상임위",
    "대통령실": "대통령을 보좌해 국정 전반을 기획하고 조율하는 조직",
    "헌법재판소": "법률이 헌법에 맞는지, 탄핵이 정당한지를 판단하는 기관",
    "헌재": "법률이 헌법에 맞는지, 탄핵이 정당한지를 판단하는 기관",
    "대법원": "재판의 최종 판단을 내리는 우리나라 최고 법원",
    "공수처": "고위공직자의 범죄를 전담해 수사하는 기관",
    "감사원": "정부가 예산을 제대로 썼는지 감시하고 감사하는 기관",
    "선관위": "선거가 공정하게 치러지도록 관리하는 기관",
    "국회": "국민이 뽑은 의원들이 법을 만들고 정부를 견제하는 기관",
    "검찰": "범죄를 수사하고 재판에 넘길지를 결정하는 기관",
}

PROC_GLOSS: dict[str, str] = {
    "인사청문회": "장관 등 후보자의 자격을 국회가 공개적으로 검증하는 절차",
    "국정감사": "국회가 매년 정부가 일을 제대로 했는지 따지는 감사",
    "필리버스터": "소수당이 표결을 막으려 무제한으로 발언하는 합법적 지연 전술",
    "재의요구권": "대통령이 국회를 통과한 법안을 다시 논의하라고 돌려보내는 권한, 사실상 거부권",
    "거부권": "대통령이 국회를 통과한 법안의 공포를 거부하고 되돌려 보내는 권한",
    "특검": "기존 검찰 대신 독립적인 특별검사가 사건을 수사하도록 하는 제도",
    "탄핵": "위법을 저지른 고위공직자를 국회 의결로 파면하는 절차",
    "체포동의안": "회기 중인 국회의원을 체포하려면 국회 동의를 받도록 한 절차",
    "임명동의안": "총리 등 일부 고위직 임명에 국회의 동의가 필요한 절차",
    "패스트트랙": "국회에서 쟁점 법안을 정해진 기간 안에 처리하도록 하는 신속처리 제도",
}

# very stable "who is this person" facts only — roles that change are left out
# (the headline's own "정책실장 김승원" gives a fresher description via ROLE_GLOSS)
WHO: dict[str, str] = {
    "이재명": "현재 대한민국 대통령",
    "윤석열": "탄핵으로 물러난 전 대통령",
    "문재인": "이재명 대통령 이전에 민주당 소속으로 집권했던 전 대통령",
}

# --------------------------------------------------------------------------- #
# 2) why THIS KIND of event matters — generic but factually safe
# --------------------------------------------------------------------------- #
SIGNIFICANCE: dict[str, str] = {
    "personnel": "정부 초기에 핵심 인사가 물러나면 국정 운영에 공백과 부담이 생길 수 있어 주목됩니다",
    "appoint": "누구를 그 자리에 앉히느냐에 따라 앞으로의 정책 방향이 크게 달라질 수 있습니다",
    "vote": "법이 바뀌면 예산과 제도가 함께 바뀌어 국민 생활에 실제로 영향을 줍니다",
    "clash": "여야가 정면으로 부딪히면 관련 법안과 정책 처리가 그만큼 늦어질 수 있습니다",
    "scandal": "의혹이 사실로 확인되면 정치적 책임론과 수사로 번질 수 있어 파장이 큽니다",
    "poll": "여론의 흐름은 다음 선거와 정국의 주도권을 가늠하는 잣대가 됩니다",
    "remark": "정치인의 말 한마디는 지지층 결집이나 큰 논란으로 번질 수 있습니다",
    "generic": "이번 일은 앞으로의 정국에 영향을 줄 수 있어 함께 살펴볼 필요가 있습니다",
}

# what the confirmed fact MEANS ("이게 무슨 뜻이냐면 …")
MEANING: dict[str, str] = {
    "personnel": "임명된 지 얼마 안 돼 물러난 만큼, 인사 검증이나 내부 갈등을 둘러싼 논란이 이어질 수 있다는 뜻입니다",
    "appoint": "이 인선이 확정되면 해당 분야 정책이 새 인물의 방향대로 움직이게 된다는 뜻입니다",
    "vote": "국회 문턱을 넘었다는 것은 이제 실제 시행 단계로 들어간다는 뜻입니다",
    "clash": "양측 주장이 팽팽해서, 한동안은 절충보다 대치가 이어질 가능성이 크다는 뜻입니다",
    "scandal": "아직 수사와 검증이 진행 중이라, 사실로 확정되기 전까지는 '의혹' 단계라는 뜻입니다",
    "poll": "숫자 하나로 단정하기보다 흐름과 오차범위를 함께 봐야 한다는 뜻입니다",
    "remark": "발언 자체보다 그 말이 불러온 정치적 반응이 더 중요해졌다는 뜻입니다",
    "generic": "지금은 방향이 정해지는 중이라, 다음 전개를 지켜봐야 한다는 뜻입니다",
}

_ROLE_WORDS = sorted(ROLE_GLOSS, key=len, reverse=True)
_BODY_WORDS = sorted(BODY_GLOSS, key=len, reverse=True)
_PROC_WORDS = sorted(PROC_GLOSS, key=len, reverse=True)


def _first_in(text: str, words: list[str]) -> str:
    return next((w for w in words if w in text), "")


def _eun_neun(word: str) -> str:
    return josa(word, ("은", "는"))


def who_is(actor: str, headline: str, entities: Entities) -> str:
    """One sentence introducing the person/body the story is about, or ''.

    Priority: a role named next to them in the headline ("정책실장 김승원" ->
    "김승원은 대통령의 정책을 총괄하는 …입니다") > a very stable WHO fact >
    an institution the headline is about.
    """
    h = clean_text(headline)
    if not actor:
        return ""
    # role sitting within ~6 chars of the name, either order
    for role in _ROLE_WORDS:
        if role not in h:
            continue
        i, j = h.find(actor), h.find(role)
        if i >= 0 and abs(i - j) <= 8:
            return f"{_eun_neun(actor)} {ROLE_GLOSS[role]}입니다."
    if actor in WHO:
        return f"{_eun_neun(actor)} {WHO[actor]}입니다."
    body = _first_in(h, _BODY_WORDS)
    if body and body == actor:
        return f"{_eun_neun(body)} {BODY_GLOSS[body]}입니다."
    return ""


def term_gloss(headline: str) -> str:
    """Explain the first procedure/body term the headline leans on, or ''."""
    h = clean_text(headline)
    proc = _first_in(h, _PROC_WORDS)
    if proc:
        return f"{_eun_neun(proc)} {PROC_GLOSS[proc]}입니다."
    body = _first_in(h, _BODY_WORDS)
    if body:
        return f"{_eun_neun(body)} {BODY_GLOSS[body]}입니다."
    return ""


def background(actor: str, headline: str, entities: Entities, frame: Frame) -> str:
    """The 'summary' card's background line(s): who/what + (if useful) a term."""
    who = who_is(actor, headline, entities)
    term = term_gloss(headline)
    # drop the term gloss when its subject already appears in the 'who' line
    if who and term:
        subj = term.split()[0].rstrip("은는이가")
        if subj and subj in who:
            term = ""
    return " ".join(p for p in (who, term) if p)


def significance(frame: Frame) -> str:
    return to_polite(SIGNIFICANCE.get(frame.kind, SIGNIFICANCE["generic"]))


def meaning(frame: Frame) -> str:
    return MEANING.get(frame.kind, MEANING["generic"])


def why_it_matters(fact_sentence: str, frame: Frame) -> str:
    """The 'what' card's closing line — the fact, then why the *kind* of event
    matters. `fact_sentence` should already be a clean, polite sentence."""
    f = to_polite(clip_sentence(clean_text(fact_sentence), 90)).rstrip(" .")
    sig = significance(frame)
    if f:
        return f"{f}. {sig}"
    return sig
