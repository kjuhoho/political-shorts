"""Step 4 — separate each sentence into FACT / CLAIM / INTERPRETATION.

FACT           verifiable event or figure, ideally with attribution
                 ("국회는 ... 의결했다", "찬성 210표", "3일 오전")
CLAIM          something an actor asserted; must carry attribution
                 ("... 라고 말했다", "...측은 ... 주장했다")
INTERPRETATION  the writer's read: outlook, blame, "논란", "분석"
                 -> never spoken as fact in the script; only as labelled context
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .textutil import clean_text, split_sentences

# --- lexical cues ---------------------------------------------------------- #
ATTRIB_VERBS = [
    "말했다", "밝혔다", "강조했다", "주장했다", "지적했다", "반박했다", "설명했다",
    "덧붙였다", "언급했다", "촉구했다", "비판했다", "논평했다", "전했다", "발표했다",
    "라고", "라며", "이라고", "이라며",
]
FACT_VERBS = [
    "의결했다", "통과했다", "가결됐다", "부결됐다", "표결", "처리했다", "상정했다",
    "발의했다", "임명했다", "지명했다", "사퇴했다", "해임했다", "개의했다", "산회했다",
    "열렸다", "개최됐다", "방문했다", "회동했다", "서명했다", "공포했다", "제출했다",
]
INTERP_CUES = [
    "전망된다", "관측된다", "분석된다", "보인다", "풀이된다", "해석된다", "예상된다",
    "논란", "파장", "후폭풍", "정면충돌", "정치적 부담", "고심", "딜레마", "촉각",
    "실권", "압박 수위", "속내", "노림수", "역풍", "묘수", "악재", "호재",
    "것으로 보인다", "가능성이 크다", "불가피해 보인다",
]
NUM_RE = re.compile(r"\d")
DATE_RE = re.compile(r"(\d{1,2}일|\d{1,2}월|\d{4}년|오전|오후|어제|오늘|내일|이날)")
QUOTE_RE = re.compile(r"[\"“”'‘’].+?[\"“”'‘’]")


class Kind(str, Enum):
    FACT = "fact"
    CLAIM = "claim"
    INTERPRETATION = "interpretation"


@dataclass
class Tagged:
    text: str
    kind: Kind
    score: float
    cues: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    facts: list[Tagged] = field(default_factory=list)
    claims: list[Tagged] = field(default_factory=list)
    interpretations: list[Tagged] = field(default_factory=list)

    @property
    def all(self) -> list[Tagged]:
        return self.facts + self.claims + self.interpretations


def _hits(text: str, terms: list[str]) -> list[str]:
    return [t for t in terms if t in text]


def tag_sentence(sentence: str) -> Tagged:
    s = clean_text(sentence)
    interp = _hits(s, INTERP_CUES)
    attrib = _hits(s, ATTRIB_VERBS)
    factv = _hits(s, FACT_VERBS)
    has_num = bool(NUM_RE.search(s))
    has_date = bool(DATE_RE.search(s))
    has_quote = bool(QUOTE_RE.search(s))

    interp_score = 1.5 * len(interp)
    claim_score = 1.2 * len(attrib) + (0.8 if has_quote else 0.0)
    fact_score = 1.3 * len(factv) + (0.6 if has_date else 0.0) + (0.5 if has_num else 0.0)

    # An attributed sentence that is also an outlook -> interpretation wins only
    # if the interpretation cue is strong and there is no hard fact verb.
    if interp_score >= max(claim_score, fact_score) and not factv:
        return Tagged(s, Kind.INTERPRETATION, round(interp_score, 2), interp)
    if claim_score >= fact_score and attrib:
        return Tagged(s, Kind.CLAIM, round(claim_score, 2), attrib + (["quote"] if has_quote else []))
    if fact_score > 0:
        cues = factv + (["date"] if has_date else []) + (["num"] if has_num else [])
        return Tagged(s, Kind.FACT, round(fact_score, 2), cues)
    # Nothing fired: treat as weak interpretation so it never becomes a stated fact.
    return Tagged(s, Kind.INTERPRETATION, 0.0, [])


def analyze(*texts: str) -> AnalysisResult:
    seen: set[str] = set()
    res = AnalysisResult()
    for block in texts:
        for sent in split_sentences(block):
            key = sent[:80]
            if key in seen:
                continue
            seen.add(key)
            tagged = tag_sentence(sent)
            if tagged.kind is Kind.FACT:
                res.facts.append(tagged)
            elif tagged.kind is Kind.CLAIM:
                res.claims.append(tagged)
            else:
                res.interpretations.append(tagged)
    res.facts.sort(key=lambda t: t.score, reverse=True)
    res.claims.sort(key=lambda t: t.score, reverse=True)
    res.interpretations.sort(key=lambda t: t.score, reverse=True)
    return res
