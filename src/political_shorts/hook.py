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
    # real polling vocabulary only — "하락"/"반등"/"%" match house prices, stock
    # indices, budget ratios… and dragged non-poll stories into the poll frame.
    "poll": ["지지율", "지지도", "여론조사", "골든크로스", "데드크로스",
             "응답률", "응답자", "설문", "조사에서", "조사 결과", "지지 후보"],
}
FRAME_ORDER = ["scandal", "personnel", "vote", "clash", "poll", "remark"]
# vote/poll/remark misfire on stray keywords ("처리"/"밝혔"/"조사"); only accept
# them on a distinctive cue, or on 2+ hits.
_STRONG = {
    "vote": ("가결", "부결", "의결", "표결", "본회의 통과", "필리버스터", "재의요구"),
    "poll": ("지지율", "지지도", "여론조사"),
    "remark": ("작심", "쓴소리", "일갈", "직격"),
}

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
    # "A, B에게 '…'" one-person-attacks-another headline -> always a clash, even
    # when it contains '의혹' (so make_title doesn't say "A의 의혹").
    if texts and _ATTACK_RE.match(clean_text(texts[0])):
        return Frame("clash", ["공방"])
    disputed = bool(_NOT_PERSONNEL.search(text))
    best = Frame()
    best_score = 0
    for kind in FRAME_ORDER:
        hits = [k for k in FRAMES[kind] if k in text]
        if disputed and kind == "personnel":
            hits = [k for k in hits if k not in ("사퇴", "지명", "후보자", "물러")]
        if kind in _STRONG and len(hits) < 2 and not any(s in text for s in _STRONG[kind]):
            hits = []                       # weak single hit -> not this frame
        if len(hits) > best_score:
            best_score = len(hits)
            best = Frame(kind, hits)
    # "personnel" lumps resignation and APPOINTMENT together, but the templates
    # assume someone stepped down ("물러났다/왜?"). Split them: a nomination
    # story with no resignation word is its own frame.
    if best.kind == "personnel" and re.search(r"지명|임명|발탁|내정|후보에|낙점|인선", text) \
            and not re.search(r"사퇴|사임|경질|물러|하차|낙마|해임|사의|자진", text):
        return Frame("appoint", best.hits)
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
    "appoint": [
        "{a_ga} 이 자리에 발탁됐습니다. 어떤 인물일까요?",
        "새 인선, {a_neun} 왜 낙점됐을까요?",
        "{a_ui} 지명, 무엇을 노린 인사인지 짚어봤습니다.",
    ],
    "clash": [
        "{a_reul} 둘러싼 공방, 무엇이 쟁점인지 짚어봤습니다.",
        "쟁점은 '{issue}'. 양쪽 말이 이렇게 갈립니다.",
        "{party}와 {partyB}의 입장이 이렇게 갈립니다.",
    ],
    "scandal": [
        "'{issue}', 지금 어디까지가 사실일까요?",
        "{a_reul} 둘러싼 의혹, 하나씩 따져봤습니다.",
        "'{issue}' 논란, 무엇이 쟁점인지 정리했습니다.",
    ],
    "vote": [
        "'{issue}'가 {result}됐습니다. 그래서 뭐가 달라질까요?",
        "이 표결 하나로 바뀌는 것들, 짚어봤습니다.",
        "'{issue}' {result}, 내 삶엔 뭐가 바뀔까요?",
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
        "이게 지금 왜 논란인지, 하나씩 풀어봤습니다.",
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
# a lead quote must be neutral: skip name-calling / hype so the hook never
# opens with "'철없는 관종' 이 말에서 시작된…"
_SLUR_QUOTE = ("관종", "철없", "발버둥", "빨갱이", "수구", "꼴통", "토착왜구", "내로남불",
               "쓰레기", "머저리", "얼간이", "3류", "삼류", "듣보", "미친", "정신 나간",
               "존재감", "약", "약 빤", "코미디", "개그", "치졸", "저질", "양아치")


def _lead_quote(*texts: str) -> str:
    """A short, NEUTRAL verbatim quote to lead the hook with, or '' — mirrors the
    quote-led titles of the top neutral news shorts (never a slur / jab)."""
    for t in texts:
        for m in _QUOTE_RE.finditer(clean_text(t)):
            q = m.group(1).strip(" .,")
            if not (6 <= len(q) <= 42) or q.endswith(("기자", "특파원")):
                continue
            if any(w in q for w in _SLUR_QUOTE):
                continue
            return q
    return ""


# "A, B에게/B 발언에 '…'" — one figure criticising another. We DON'T skip these
# (see NEUTRALITY.md — balance is in the narration, not topic choice); we just
# frame the title around the person under scrutiny, neutrally, as a question.
_ATTACK_RE = re.compile(
    r"^([가-힣]{2,4})\s*,\s*(?:.{0,14}?\b)?([가-힣]{2,4})\s*"
    r"(?:에게|에|을|를|향해|겨냥|측|의)?\s*(?:[\"'“”‘’]|발언|주장|글|비판|공세|저격|직격)"
)
_ISSUE_WORD = ("청탁", "특혜", "의혹", "비자금", "뇌물", "탈세", "탈루", "겸직", "위증",
               "거짓말", "위장전입", "표절", "음주", "막말", "실언", "이해충돌",
               "레임덕", "내로남불", "책임론", "발언", "논란")


# common nouns the "A, B …" regex can grab that are NOT the person under
# scrutiny ("홍익표, '이 대통령 연임 논란'에…" must not yield actor="논란")
_NOT_TARGET = {
    "논란", "의혹", "발언", "공방", "파장", "사태", "비판", "해명", "반박", "주장",
    "입장", "공세", "맹공", "저격", "직격", "경고", "일침", "후폭풍", "책임론",
    "언급", "지적", "질문", "폭로", "녹취", "녹취록", "기자회견", "글", "댓글",
}


def attack_target(headline: str) -> str:
    """For 'A, B …공격…' return B (the person the story is really scrutinising);
    '' if the headline isn't that shape or B is a bare topic noun."""
    m = _ATTACK_RE.match(clean_text(headline))
    if not m:
        return ""
    b = re.sub(r"(에게|에|을|를|측|의|이|가|은|는|께)$", "", m.group(2))
    b = b if 2 <= len(b) <= 4 else m.group(2)
    return "" if b in _NOT_TARGET else b


def issue_word(headline: str, *more: str) -> str:
    """A short noun for what the dispute is ABOUT (goes in the neutral title)."""
    blob = clean_text(" ".join((headline, *more)))
    for w in _ISSUE_WORD:
        if w in blob:
            return w
    return "논란"

_TITLE_RE = re.compile(
    r"([가-힣]{2,4})\s*(?:청와대|대통령실|신임|전|前)?\s*"
    r"(대통령|국무총리|부총리|장관|차관|정책실장|비서실장|안보실장|수석|대변인|"
    r"원내대표|당대표|위원장|의원|청장|총장|처장|본부장|사장|회장|시장|지사|"
    r"변호사|교수|재판관|대법관|헌법재판관)"
)
# the "직책 이름" order — "대통령실 정책실장 김승원", "국무총리 김민석". The name
# comes AFTER the role word (and any office prefix), so _TITLE_RE would wrongly
# grab the office word ("대통령실") as the name. Capture the trailing name.
_NAME_AFTER_ROLE = re.compile(
    r"(?:대통령실|청와대|국회|정부|여당|야당|국민의힘|더불어민주당|민주당|"
    r"조국혁신당|개혁신당|신임|전|前|초대|차기|새)?\s*"
    r"(?:국무총리|부총리|정책실장|비서실장|안보실장|국정상황실장|정무수석|경제수석|"
    r"사회수석|홍보수석|민정수석|시민사회수석|대변인|원내대표|사무총장|비서실장|"
    r"장관|차관|수석|의장|위원장|처장|청장|본부장|원장|시장|지사|대표)\s+"
    r"([가-힣]{2,4})(?=\s|$|[,.·…'\"”’)\]]|씨|은|는|이|가|을|를|와|과|의|도|만|께서)"
)
# nouns that can sit right after a role word but are NOT a person's name
_NOT_A_NAME = {"사퇴", "사의", "교체", "경질", "내정", "지명", "임명", "발탁", "후보",
               "논란", "파문", "의혹", "출신", "권한", "대행", "겸직", "인선", "임기",
               "발언", "회의", "주재", "참석", "회견", "결정", "지시", "보고", "인사"}
# office / institution words that a role regex can swallow as a fake "name"
_OFFICE_WORD = {"대통령실", "청와대", "국회", "국회의장", "정부", "여당", "야당",
                "정치권", "당정", "여야", "검찰", "경찰", "법원", "공수처", "헌재",
                "감사원", "권익위", "선관위", "정부청사"}
# name right before an appointment verb: "…후보에 김지용 변호사 지명"
_NOMINEE_RE = re.compile(
    r"([가-힣]{2,4})\s*(?:변호사|교수|전\s*[가-힣]{2,4}|후보자?)?\s*"
    r"(?:를|을|에)?\s*(?:지명|발탁|내정|낙점|임명)"
)
# a "{X}청장/처장/총장" where X is an agency abbrev, not a person
_ORG_PREFIX = {"중수", "국세", "관세", "경찰", "소방", "산림", "특허", "조달", "통계",
               "기상", "병무", "해경", "검찰", "감사", "국정", "선관", "방사", "질병"}

# snappy second line — short, keeps the open loop, no hype
HOOK_TAIL = [
    "왜 이렇게 됐는지 짚어봤습니다.",
    "무슨 일인지 하나씩 보겠습니다.",
    "쉽게 풀어서 설명해 드립니다.",
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
    # "A, B에게 '…공격…'" -> the story is really ABOUT B (the person under
    # scrutiny); we explain the claim + B's response neutrally.
    tgt = attack_target(headline)
    if tgt:
        return tgt
    if frame.kind == "appoint":
        m = _NOMINEE_RE.search(h)
        if m and m.group(1) not in _ORG_PREFIX:
            return m.group(1)
    if frame.kind in ("personnel", "appoint", "remark", "clash"):
        # "직책 이름" order first ("대통령실 정책실장 김승원" -> 김승원), so the
        # office word isn't mistaken for the name.
        m = _NAME_AFTER_ROLE.search(h)
        if m and m.group(1) not in _NOT_A_NAME and m.group(1) not in _ORG_PREFIX \
                and m.group(1) not in _OFFICE_WORD:
            return m.group(1)
        # then "이름 직책" order, skipping agency abbrevs ("국세청장" -> 국세) and
        # office words ("대통령실 정책실장" -> 대통령실). "김지용 변호사 지명" wins.
        for m in _TITLE_RE.finditer(h):
            if m.group(1) not in _ORG_PREFIX and m.group(1) not in _OFFICE_WORD:
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
# line 1 = the concrete subject, line 2 = a curiosity hook that makes the
# thumbnail worth a tap (no 충격/발칵 hype, no slur, no false certainty).
# line 1 = the concrete subject (a real name up front), line 2 = a spoken-style
# curiosity ending — mirrors the user's own high-view titles ("한동훈 녹취록
# 공개 / 유출 경위 조사할까?"). NEVER "핵심만 / 쟁점 정리 / 30초 정리".
_TITLE_TMPL = {
    "personnel": [("{actor} 자리서 물러났다", "무슨 일일까?"),
                  ("{actor} 사퇴", "왜 지금일까?"),
                  ("{actor} 교체", "진짜 이유는?")],
    "appoint": [("{actor} 발탁", "왜 이 사람일까?"),
                ("{actor} 지명", "무슨 뜻일까?")],
    "clash": [("{actor} 놓고 정면 충돌", "쟁점이 뭘까?"),
              ("{actor} 둘러싼 공방", "누구 말이 맞을까?"),
              ("{actor} '{issueword}' 논란", "사실일까?")],
    "scandal": [("'{issue}' 의혹", "어디까지 사실일까?"),
                ("{actor} 겨눈 의혹", "진짜 문제가 뭘까?")],
    "vote": [("'{issue}' {result}", "내 삶엔 뭐가 바뀔까?"),
             ("'{issue}' {result}", "무슨 뜻일까?")],
    "poll": [("{actor} 지지율 출렁", "숫자가 말하는 건?"),
             ("'{issue}' 여론조사", "국민 생각은 어떨까?")],
    "remark": [("{actor} 이 한마디", "왜 이렇게 시끄러울까?"),
               ("{actor} 발언 파장", "무슨 뜻이었을까?")],
    "generic": [("{issue}", "무슨 일일까요?"),
                ("{issue}", "왜 논란일까?")],
}


def make_title(headline: str, entities: Entities, frame: Frame) -> list[str]:
    """1-2 short NEUTRAL lines for the persistent on-screen title. For an
    'A criticises B' story this frames around B + the issue as a question
    ("김승원 '청탁' 논란 / 사실은?"), never "A의 의혹"."""
    actor = pick_actor(headline, entities, frame)
    iw = issue_word(headline)
    h = clean_text(headline)
    # headline's leading noun phrase — used for {issue} and when there's no
    # usable actor. e.g. "국회 신속처리안건 90일 단축..." -> "국회 신속처리안건"
    head_np = re.split(r"[…·\-—\"'“”,]|하며|라며|밝혀|주장|지적|공세|비판", h)[0].strip()
    head_np = truncate(head_np, 16) or "오늘의 정치 이슈"
    # a bare-noun actor ("논란", "여야") -> lead with the headline phrase instead
    if actor in _NOT_TARGET or not (2 <= len(actor) <= 6) or actor in {"여야", "여당", "야당"}:
        return [head_np, random.choice(["무슨 일일까요?", "왜 논란일까?", "진짜일까?"])]
    tmpls = _TITLE_TMPL.get(frame.kind) or _TITLE_TMPL["generic"]
    if frame.kind == "clash" and iw == "논란":
        tmpls = [t for t in tmpls if "{issueword}" not in t[0]] or tmpls
    if frame.kind == "poll" and not re.search(r"지지율|지지도|여론|설문|조사", h):
        tmpls = _TITLE_TMPL["generic"]            # not really a polling story
    if frame.kind == "remark" and not re.search(r"발언|한마디|말|주장|밝혀|경고|촉구", h):
        tmpls = _TITLE_TMPL["generic"]
    parties = entities.parties + ["", ""]
    slots = {
        "actor": actor, "party": parties[0] or "여당", "partyB": parties[1] or "야당",
        "issue": _issue_phrase(headline, frame) if frame.kind != "generic" else head_np,
        "result": _result_word(frame), "issueword": iw,
    }
    l1, l2 = random.choice(tmpls)
    out = [truncate(l1.format(**slots), 16), truncate(l2.format(**slots), 16)]
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


_COPULA_PLAIN = (("이다", "입니다"), ("아니다", "아닙니다"), ("된다", "됩니다"),
                 ("한다", "합니다"), ("있다", "있습니다"), ("없다", "없습니다"),
                 ("낸다", "냅니다"), ("진다", "집니다"))


def to_polite(s: str) -> str:
    """Sentence-final plain style -> 합쇼체 so the narration is all one register
    ('물러났다.' -> '물러났습니다.', '이례적이다' -> '이례적입니다'). Only the final
    predicate; mid-sentence '다' ('찬성보다') and quotes are left alone."""
    s = (s or "").rstrip()
    tail = "." if s.endswith(".") else ""
    core = s[:-1] if tail else s
    core = core.rstrip()
    m = re.search(r"([가-힣])다$", core)
    if m and (ord(m.group(1)) - 0xAC00) % 28 == 20:   # penult syllable carries ㅆ 받침 = past tense
        return core[:-1] + "습니다" + tail
    for a, b in _COPULA_PLAIN:
        if core.endswith(a):
            return core[:-len(a)] + b + tail
    return s


def simplify(sentence: str, add_lead: bool = False, limit: int = 72) -> str:
    s = strip_wire_marks(clean_text(sentence))
    s = DROP_PREFIX.sub("", s)
    s = _TITLE_SPACE.sub(r"\1 \2", s)
    for jar, plain in JARGON.items():
        s = s.replace(jar, plain)
    s = clip_sentence(s, limit)          # end on a natural boundary, no mid-word cut
    s = to_polite(s)
    if add_lead and not s.startswith(("쉽게", "한마디로", "정리하면")):
        s = "쉽게 말하면, " + s
    return s


# --------------------------------------------------------------------------- #
# fact-check block
# --------------------------------------------------------------------------- #
# a wire byline stuck to the front of a sentence with no space ("연합뉴스한성숙…")
_FC_BYLINE = re.compile(
    r"^(?:\[[^\]]*\]\s*)?(?:연합뉴스|뉴시스|뉴스1|SBS|KBS|MBC|YTN|JTBC|채널A|"
    r"경향신문|서울신문|한겨레|동아일보|조선일보|중앙일보|국민일보|세계일보|오마이뉴스)\s*")


_FC_TAIL = re.compile(
    r"[,·(]?\s*[가-힣]{0,10}?(라며|하며|밝히며|말하며|면서|는데|지만|따르면|위해|대해|"
    r"관해|향해|바탕으로|통해|이라고|라고|와|과|고|며|면|은|는|이|가|을|를|에|의|도|만)$")


def _fc_text(s: str, limit: int = 44) -> str:
    """Fact-check row text safe for the caption font: no byline, no '·…—' (the
    bundled fonts render them as tofu), ends on a noun/predicate — not '…따뜻하고'."""
    s = _FC_BYLINE.sub("", clean_text(s))
    s = s.replace("·", ", ").replace("…", " ").replace("ㆍ", ", ").replace("—", "-").replace("~", "-")
    s = re.sub(r"\s+", " ", s).strip(" ,")
    s = clip_sentence(s, limit, ell="").rstrip(" ,·.\"'()")
    for _ in range(4):
        if not s or s[-1] in "다요죠까)":
            break
        t = _FC_TAIL.sub("", s).rstrip(" ,·(")
        if t == s or len(t) < 8:
            break
        s = t
    return s


def _fc_dup(a: str, b: str) -> bool:
    ta = set(re.findall(r"[가-힣]{2,}", a))
    tb = set(re.findall(r"[가-힣]{2,}", b))
    return bool(ta) and len(ta & tb) / len(ta) >= 0.6


def make_factcheck(analysis, n_sources: int) -> list[dict]:
    """Rows for the fact-check card. `tag` = a short Hangul marker (emoji fonts
    aren't reliable in the caption font); `tone` picks the row colour."""
    rows: list[dict] = []
    fact_t = _fc_text(simplify(analysis.facts[0].text, limit=54)) if analysis.facts else ""
    if fact_t:
        rows.append({"tag": "사실", "tone": "ok", "text": fact_t})
    if analysis.claims:
        ct = _fc_text(analysis.claims[0].text)
        if len(ct) >= 8 and not _fc_dup(ct, fact_t):
            rows.append({"tag": "주장", "tone": "claim", "text": ct})
    if analysis.interpretations and analysis.interpretations[0].score > 0:
        it = _fc_text(analysis.interpretations[0].text)
        if len(it) >= 8 and not _fc_dup(it, fact_t):
            rows.append({"tag": "전망", "tone": "warn", "text": it})
    rows.append({"tag": "확인", "tone": "info",
                 "text": f"{n_sources}개 매체 종합, 원문은 더보기란"})
    return rows[:4]
