"""Engaging, template-driven copy: a curiosity-gap HOOK, middle-school-level
plain-language rewriting, and a fact-check block. No LLM required.

The goal is "tabloid headline energy, factual body": the hook grabs attention
and names the real actors (party, president, minister), the body explains it
like you would to a 14-year-old, and a fact-check card at the end keeps the
whole thing honest.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from .textutil import clean_text, clip_sentence, truncate

# --------------------------------------------------------------------------- #
# entity lexicons
# --------------------------------------------------------------------------- #
PRESIDENT_TERMS = ["이재명 대통령", "이재명", "대통령실", "청와대", "대통령"]

POLITICIANS = [
    "이재명", "한동훈", "조국", "우원식", "추경호", "박찬대", "나경원", "안철수",
    "이준석", "김두관", "홍준표", "오세훈", "김문수", "원희룡", "장동혁", "김민석",
    "정청래", "박성준", "송언석", "김병기", "전현희", "김용범", "조희대",
    # current-affairs figures (Sep 2026 news cycle)
    "용혜인", "김승원", "강선우", "정점식", "송영길", "천하람", "윤호중", "박범계",
    "조정식", "권성동", "권영세", "윤상현", "이언주", "전용기", "최민희", "박선원",
    "신현영",
]
POLI_ROLES = [
    "대통령", "국무총리", "부총리", "장관", "차관", "원내대표", "당대표",
    "국회의장", "정책실장", "비서실장", "수석", "대변인", "위원장",
]

PARTIES = {
    "더불어민주당": "민주당", "민주당": "민주당", "국민의힘": "국민의힘",
    "국힘": "국민의힘", "조국혁신당": "조국혁신당", "개혁신당": "개혁신당",
    "진보당": "진보당", "정의당": "정의당", "기본소득당": "기본소득당",
}

INSTITUTIONS = [
    "국회", "본회의", "법사위", "운영위", "정무위", "예결위", "대통령실",
    "헌법재판소", "헌재", "대법원", "검찰", "경찰청", "공수처", "감사원",
    "선관위", "중앙선거관리위원회",
]


@dataclass
class Entities:
    president: bool = False
    politicians: list[str] = field(default_factory=list)
    parties: list[str] = field(default_factory=list)     # normalized short names
    institutions: list[str] = field(default_factory=list)

    @property
    def lead_actor(self) -> str:
        if self.politicians:
            return self.politicians[0]
        if self.president:
            return "이재명 대통령"
        if self.parties:
            return self.parties[0]
        if self.institutions:
            return self.institutions[0]
        return "정치권"


def detect_entities(*texts: str) -> Entities:
    text = clean_text(" ".join(texts))
    ent = Entities()
    ent.president = any(t in text for t in PRESIDENT_TERMS[:3]) or (
        "대통령" in text and "이재명" in text
    )
    ent.politicians = [p for p in POLITICIANS if p in text]
    seen: set[str] = set()
    for raw, short in PARTIES.items():
        if raw in text and short not in seen:
            seen.add(short)
            ent.parties.append(short)
    ent.institutions = [i for i in INSTITUTIONS if i in text]
    return ent


# --------------------------------------------------------------------------- #
# story frame
# --------------------------------------------------------------------------- #
FRAMES: dict[str, list[str]] = {
    "personnel": ["사퇴", "교체", "경질", "사임", "지명", "임명", "후보자", "인선", "개각", "물러", "지명철회"],
    "clash": ["충돌", "공방", "정면", "격돌", "설전", "신경전", "맞섰", "맞불", "반박", "발끈", "직격", "정면충돌"],
    "scandal": ["의혹", "논란", "파문", "스캔들", "특검", "수사", "압수수색", "기소", "구속", "소환", "해명",
                "겸직", "버티기", "부적격", "위법", "특혜", "자격 시비", "고발", "리스크"],
    "vote": ["통과", "부결", "가결", "처리", "상정", "표결", "의결", "부의", "거부권", "재의요구", "필리버스터", "본회의 통과"],
    "remark": ["발언", "주장", "밝혔", "경고", "촉구", "비판", "일갈", "작심", "쓴소리"],
    "poll": ["지지율", "여론조사", "골든크로스", "데드크로스", "하락", "반등", "%"],
}
FRAME_ORDER = ["scandal", "personnel", "vote", "clash", "poll", "remark"]

# "사퇴/지명" next to these is a *dispute about* stepping down / being named,
# not the act itself — don't let it drag a controversy into the personnel frame.
_NOT_PERSONNEL = re.compile(
    r"사퇴\s*(?:여부|거부|요구|론|설|압박|촉구|불가|공방|논란)"
    r"|거부|버티|안\s*물러|유지하겠|유지 의사|겸직"
    r"|지명\s*(?:철회|반대|논란)"
)


@dataclass
class Frame:
    kind: str = "generic"
    hits: list[str] = field(default_factory=list)


def detect_frame(*texts: str) -> Frame:
    text = clean_text(" ".join(texts))
    disputed = bool(_NOT_PERSONNEL.search(text))
    best = Frame()
    best_score = 0
    for kind in FRAME_ORDER:
        hits = [k for k in FRAMES[kind] if k in text]
        if disputed and kind == "personnel":
            hits = [k for k in hits if k not in ("사퇴", "지명", "후보자", "물러")]
        if len(hits) > best_score:
            best_score = len(hits)
            best = Frame(kind, hits)
    return best


# --------------------------------------------------------------------------- #
# particles — pick 이/가, 은/는, 을/를, 와/과 by the last syllable's 받침
# --------------------------------------------------------------------------- #
def _has_batchim(word: str) -> bool:
    w = (word or "").rstrip("\"'’”) ").strip()
    if not w:
        return False
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    return ch.isdigit() and ch in "0136780"


def josa(word: str, pair: tuple[str, str]) -> str:
    """pair = (with-받침, without-받침), e.g. ('이','가')."""
    return word + (pair[0] if _has_batchim(word) else pair[1])


# --------------------------------------------------------------------------- #
# hook templates. {a_ga}/{a_neun}/{a_reul} already include the actor + particle.
# --------------------------------------------------------------------------- #
# Hook = OPEN A LOOP, don't summarise. Modeled on high-view neutral news shorts
# (YTN "[지금이뉴스]" 재조명, quote-led openers) — a question the viewer wants
# answered, no partisan colour, no 사이다/충격/발칵.  `{quote}` = a short verbatim
# quote pulled from the story when one exists (used first when available).
HOOKS: dict[str, list[str]] = {
    "personnel": [
        "{a_neun} 왜 갑자기 자리에서 내려왔을까요?",
        "{a_ga} 물러났습니다. 무슨 일이 있었던 걸까요?",
        "{a_ui} 교체, 그 배경을 짚어봤습니다.",
    ],
    "clash": [
        "{party}와 {partyB}가 부딪힌 지점은 딱 하나였습니다.",
        "쟁점은 '{issue}'. 양쪽 말이 이렇게 갈립니다.",
        "{a_ui} 한마디에서 시작된 공방, 핵심만 짚어봤습니다.",
    ],
    "scandal": [
        "'{issue}', 지금 어디까지가 사실일까요?",
        "{a_reul} 둘러싼 의혹, 하나씩 따져봤습니다.",
        "'{issue}' 논란, 무엇이 쟁점인지 정리했습니다.",
    ],
    "vote": [
        "'{issue}'가 {result}됐습니다. 그래서 뭐가 달라질까요?",
        "이 표결 하나로 바뀌는 것들, 짚어봤습니다.",
        "'{issue}' {result}, 핵심만 요약했습니다.",
    ],
    "poll": [
        "{a_ui} 지지율, 방향이 바뀌었습니다. 숫자를 봤습니다.",
        "이번 여론조사, 무엇을 읽어야 할까요?",
    ],
    "remark": [
        "{a_ga} 던진 이 한마디, 왜 이렇게 시끄러울까요?",
        "이 발언 한 줄이 파장을 부른 이유를 짚어봤습니다.",
    ],
    "generic": [
        "오늘 정치권에서 가장 많이 오르내린 이야기입니다.",
        "지금 이 이슈, 핵심만 30초로 정리했습니다.",
    ],
}
# used first when the story carries a strong short quote
_QUOTE_HOOKS = {
    "remark": "\"{quote}\" 이 한마디, 왜 파장이 컸을까요?",
    "clash": "\"{quote}\" 여기서 공방이 시작됐습니다.",
    "personnel": "\"{quote}\" 그리고 {a_ga} 자리에서 내려왔습니다.",
    "scandal": "\"{quote}\" 이 발언을 두고 논란이 붙었습니다.",
    "generic": "\"{quote}\" 이 말에서 시작된 이슈, 정리했습니다.",
}
_QUOTE_RE = re.compile(r'["“‘\']([^"“”‘’\']{6,42})["”’\']')


def _lead_quote(*texts: str) -> str:
    """A short verbatim quote to lead the hook with, or '' — mirrors the
    quote-led titles of the top-performing neutral news shorts."""
    for t in texts:
        for m in _QUOTE_RE.finditer(clean_text(t)):
            q = m.group(1).strip(" .,")
            if 6 <= len(q) <= 42 and not q.endswith(("기자", "특파원")):
                return q
    return ""

_TITLE_RE = re.compile(
    r"([가-힣]{2,4})\s*(?:청와대|대통령실|신임|전|前)?\s*"
    r"(대통령|국무총리|부총리|장관|차관|정책실장|비서실장|안보실장|수석|대변인|"
    r"원내대표|당대표|위원장|의원|청장|총장|처장|본부장|사장|회장|시장|지사)"
)

# snappy second line — short, keeps the open loop, no hype
HOOK_TAIL = [
    "핵심만 짚어봤습니다.",
    "30초로 정리했습니다.",
    "무슨 일인지 보겠습니다.",
]


def _issue_phrase(headline: str, frame: Frame) -> str:
    """A short noun-ish phrase for the {issue} slot."""
    h = clean_text(headline)
    # take the chunk before the first strong punctuation / ellipsis
    h = re.split(r"[…·\-—\"'“”]|하며|라며|밝혀|주장", h)[0].strip()
    return truncate(h, 18) or "이번 사안"


def _result_word(frame: Frame) -> str:
    for w in ("가결", "통과", "부결", "처리", "무산", "상정", "의결"):
        if w in frame.hits:
            return "통과" if w in ("가결", "통과", "의결", "처리") else "무산" if w in ("부결", "무산") else w
    return "처리"


def pick_actor(headline: str, entities: Entities, frame: Frame) -> str:
    """Who the hook is really about — for personnel stories that's the person
    named in the headline next to a job title, NOT just the first politician
    mentioned anywhere (which is often the president being referenced)."""
    h = clean_text(headline)
    if frame.kind in ("personnel", "remark", "clash"):
        m = _TITLE_RE.search(h)
        if m:
            return m.group(1)
    # the politician named EARLIEST in the headline is the subject — not just the
    # first one that happens to sort first in the lexicon (that picked 이재명 for
    # a "조국 '이재명 유죄 가능성' 발언" headline).
    named = [n for n in entities.politicians if n in h]
    if named:
        return min(named, key=h.find)
    return entities.lead_actor


def make_hook(headline: str, entities: Entities, frame: Frame, style: str = "punchy") -> tuple[str, str]:
    """Return (caption, narration)."""
    if style == "neutral":
        cap = truncate(clean_text(headline), 44)
        return cap, f"{clean_text(headline)}. 오늘 정치 뉴스, 사실 위주로 정리합니다."

    actor = pick_actor(headline, entities, frame)
    parties = entities.parties + ["", ""]
    party = parties[0] or "여당"
    slots = {
        "actor": actor,
        "a_ga": josa(actor, ("이", "가")),
        "a_neun": josa(actor, ("은", "는")),
        "a_reul": josa(actor, ("을", "를")),
        "a_ui": actor + "의",
        "party": party,
        "p_ga": josa(party, ("이", "가")),
        "partyB": parties[1] or "야당",
        "issue": _issue_phrase(headline, frame),
        "result": _result_word(frame),
    }
    quote = _lead_quote(headline)
    if quote and (frame.kind in _QUOTE_HOOKS or "generic" in _QUOTE_HOOKS):
        tmpl = _QUOTE_HOOKS.get(frame.kind, _QUOTE_HOOKS["generic"])
        line1 = tmpl.format(quote=quote, **slots)
    else:
        line1 = random.choice(HOOKS.get(frame.kind) or HOOKS["generic"]).format(**slots)
    line2 = random.choice(HOOK_TAIL)
    return truncate(line1, 46), f"{line1} {line2}"


# --------------------------------------------------------------------------- #
# thumbnail title — big, punchy, stays on screen the whole video
# --------------------------------------------------------------------------- #
# Line 1 = the subject.  Line 2 = a NEUTRAL open loop (no 발칵/충격/아웃).
# Mirrors "…재조명 / 무슨 일 / 되짚어봤습니다" endings of top neutral news shorts.
_TITLE_TMPL = {
    "personnel": [("{actor} 사퇴", "무슨 일인가"),
                  ("{actor} 왜 물러났나", "배경 정리"),
                  ("{actor} 교체", "그 이유는")],
    "clash": [("{party} vs {partyB}", "무엇이 쟁점인가"),
              ("'{issue}' 공방", "양쪽 입장은"),
              ("{actor} 발언 파장", "짚어봤습니다")],
    "scandal": [("'{issue}' 논란", "어디까지 사실인가"),
                ("{actor} 의혹", "쟁점 정리"),
                ("'{issue}'", "핵심만 정리")],
    "vote": [("'{issue}' {result}", "무엇이 달라지나"),
             ("'{issue}' {result}", "쉽게 정리"),
             ("국회 통과 법안", "핵심 3가지")],
    "poll": [("{actor} 지지율 변화", "숫자로 보기"),
             ("여론조사 결과", "무엇을 읽나")],
    "remark": [("{actor} 발언", "왜 파장인가"),
               ("이 한마디", "무슨 뜻이었나")],
    "generic": [("오늘의 정치 이슈", "핵심만"),
                ("지금 이 이슈", "30초 정리")],
}


def make_title(headline: str, entities: Entities, frame: Frame) -> list[str]:
    """1-2 short punchy lines for the persistent on-screen title."""
    actor = pick_actor(headline, entities, frame)
    parties = entities.parties + ["", ""]
    slots = {
        "actor": actor, "party": parties[0] or "여당", "partyB": parties[1] or "야당",
        "issue": _issue_phrase(headline, frame), "result": _result_word(frame),
    }
    l1, l2 = random.choice(_TITLE_TMPL.get(frame.kind) or _TITLE_TMPL["generic"])
    out = [truncate(l1.format(**slots), 14), truncate(l2.format(**slots), 14)]
    return [x for x in out if x]


# --------------------------------------------------------------------------- #
# plain-language rewriting (middle-school level)
# --------------------------------------------------------------------------- #
# Only safe substitutions: whole verb forms, and parentheticals that read fine
# inside a compound word. NO bare-noun replacements (they break words like
# "중폭개각" -> "중폭장관들을 바꾸는 것").
JARGON = {
    "의결했다": "통과시켰습니다",
    "가결됐다": "통과됐습니다",
    "부결됐다": "통과되지 못했습니다",
    "상정했다": "안건으로 올렸습니다",
    "부의했다": "본회의에 올렸습니다",
    "재의요구권": "거부권",
    "인사청문회": "인사청문회(장관 자격 검증)",
    "국정감사": "국정감사(국회의 연례 정부 점검)",
    "필리버스터": "필리버스터(무제한 토론)",
    "유임된지": "유임된 지",
    "표명한지": "표명한 지",
}
DROP_PREFIX = re.compile(r"^(한편|또한|아울러|이와 관련|앞서|이날|이에)\s*[,]?\s*")
_TITLE_SPACE = re.compile(r"(청와대|대통령|국무총리|국회)(정책실장|비서실장|안보실장|수석|대변인|의장|사무총장)")
_WIRE_MARK = re.compile(r"\s*\((?:종합\s*\d*\s*보?|전문|상보|속보|\d+보|재종합|1신|2신)\)")


def strip_wire_marks(text: str) -> str:
    return _WIRE_MARK.sub("", text or "")


def simplify(sentence: str, add_lead: bool = False, limit: int = 72) -> str:
    s = strip_wire_marks(clean_text(sentence))
    s = DROP_PREFIX.sub("", s)
    s = _TITLE_SPACE.sub(r"\1 \2", s)
    for jar, plain in JARGON.items():
        s = s.replace(jar, plain)
    s = clip_sentence(s, limit)          # end on a natural boundary, no mid-word cut
    if add_lead and not s.startswith(("쉽게", "한마디로", "정리하면")):
        s = "쉽게 말하면, " + s
    return s


# --------------------------------------------------------------------------- #
# fact-check block
# --------------------------------------------------------------------------- #
def make_factcheck(analysis, n_sources: int) -> list[dict]:
    """Rows for the fact-check card. `tag` = a short Hangul marker (emoji fonts
    aren't reliable in the caption font); `tone` picks the row colour."""
    rows: list[dict] = []
    if analysis.facts:
        rows.append({"tag": "사실", "tone": "ok",
                     "text": clip_sentence(simplify(analysis.facts[0].text, limit=46), 46, ell="..")})
    if analysis.claims:
        rows.append({"tag": "주장", "tone": "claim",
                     "text": "한쪽 주장: " + clip_sentence(analysis.claims[0].text, 32, ell="..")})
    if analysis.interpretations and analysis.interpretations[0].score > 0:
        rows.append({"tag": "해석", "tone": "warn",
                     "text": "아직 전망: " + clip_sentence(analysis.interpretations[0].text, 32, ell="..")})
    rows.append({"tag": "확인", "tone": "info",
                 "text": f"{n_sources}개 매체 종합, 원문은 더보기란"})
    return rows[:4]
