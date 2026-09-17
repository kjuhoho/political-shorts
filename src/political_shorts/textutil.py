"""Small text helpers shared across modules (Korean-aware, dependency-light)."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata

_WS = re.compile(r"\s+")
_HTML = re.compile(r"<[^>]+>")

# Korean news boilerplate: datelines, bylines, agency footers.
_BYLINE_LEAD = re.compile(
    r"^\s*[\[(][^\])]{0,20}(=|·)?\s*(연합뉴스|뉴스1|뉴시스|뉴스원|경향신문|서울신문|"
    r"[가-힣]{2,10})[^\])]{0,10}[\])]\s*"
)
_BYLINE_REPORTER = re.compile(r"^.{0,45}?[가-힣]{2,4}(?:\s?[·,]\s?[가-힣]{2,4}){0,3}\s*기자\s*=\s*")
_BYLINE_TRAIL = re.compile(
    r"\s*(?:[가-힣]{2,4}\s*기자|사진\s*=?.*|\(끝\)|ⓒ.*|무단\s*전재.*|재배포\s*금지.*)\s*$"
)
_EMAIL = re.compile(r"\b[\w.\-]+@[\w.\-]+\.\w+\b")
_URL_TRACKING = re.compile(r"[?&](utm_[^=]+|fbclid|gclid|igshid|spm)=[^&]*", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|(?<=다\.)\s+|\n+")
_NON_WORD = re.compile(r"[^0-9A-Za-z가-힣]+")


def strip_html(text: str) -> str:
    return _HTML.sub(" ", text or "")


# Hanja abbreviations Korean headlines love, mapped to Hangul.
_HANJA = {
    "靑": "청와대", "與": "여", "野": "야", "北": "북한", "美": "미국", "日": "일본",
    "中": "중국", "英": "영국", "獨": "독일", "佛": "프랑스", "露": "러시아",
    "檢": "검찰", "警": "경찰", "軍": "군",
    # Surname shorthands — headlines write "李대통령" / "韓총리" for the
    # sitting officeholder. Without these, `clean_text` leaves the Hanja in
    # place and it reaches a caption as tofu (the bundled fonts have no CJK
    # Han glyph) or an unreadable label. script_gen kept its own private
    # copy of exactly this map for its on-screen chips; these belong here,
    # where `clean_text` -> `dehanja` applies them on every text path.
    "李": "이", "尹": "윤", "文": "문", "朴": "박", "安": "안",
    "洪": "홍", "秋": "추", "韓": "한",
}
_HANJA_RE = re.compile("|".join(map(re.escape, sorted(_HANJA, key=len, reverse=True))))

# "中" alone is genuinely ambiguous, and the blind per-character pass above
# got it wrong two different ways — a real shipped bug plus one found while
# fixing it:
#   1) immediately after a plain Hangul word (no space), "中" is almost
#      always the native suffix "-중" ("-하는 중", "-중이다"), never China:
#      "국감中" ("국감 중" = during the [National Assembly] audit) came out
#      as "국감중국".
#   2) paired with another country-hanja ("美中" = US-China), Korean drops
#      the "-국"/"-본" suffix on BOTH sides for the idiomatic short compound
#      ("미중", not "미국중국") — found while testing fix #1, same root
#      cause (no context awareness), so fixed alongside it rather than left
#      half-broken.
# Each hand-verified explicitly rather than derived from a general "always
# shorten" rule — a wrong guess here is a wrong country name on screen.
_HANJA_PAIR = {
    "美中": "미중", "中美": "중미", "韓美": "한미", "美韓": "미한",
    "韓中": "한중", "中韓": "중한", "韓日": "한일", "日韓": "일한",
    "中日": "중일", "日中": "일중", "韓露": "한러", "美露": "미러",
    "韓英": "한영",
}
_HANJA_PAIR_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(_HANJA_PAIR, key=len, reverse=True)))
_ZHONG_DURING = re.compile(r"(?<=[가-힣])中")


def dehanja(text: str) -> str:
    text = text or ""
    text = _HANJA_PAIR_RE.sub(lambda m: _HANJA_PAIR[m.group(0)], text)
    text = _ZHONG_DURING.sub("중", text)
    return _HANJA_RE.sub(lambda m: _HANJA[m.group(0)], text)


# Regulatory / legal abbreviations that read as pure jargon to someone who
# doesn't follow politics — a real viewer complaint ("토허구역 실거주 이런 표현이
# 일반 사람들한테 바로 인지될수 있다고 생각해?"): nothing downstream can be trusted
# to gloss these on its own. The LLM rewrite is told to explain unfamiliar
# terms on first use (script_llm.py rule 1) but that's best-effort and never
# reaches the deterministic template fallback or the Hook Engine (which has
# no LLM pass at all). Glossed once, short, applied to already-assembled
# text (never to a fragment still headed for clipping) so nothing cuts a
# gloss off mid-parenthesis.
JARGON = {
    "토지거래허가구역": "토지거래허가구역(집을 사면 실제로 살아야 하는 규제 지역)",
    "토허구역": "토허구역(집을 사면 실제로 살아야 하는 규제 지역)",
    "종부세": "종부세(비싼 부동산 보유자에게 매기는 세금)",
    "공수처": "공수처(고위공직자 비리를 수사하는 독립 기관)",
    # corruption / financial-crime compound terms — legally precise but not
    # intuitively clear on sight, and this channel covers them often. A real
    # user complaint: "공천헌금과 같은 용어는 실제 나도... 무슨 뜻인지
    # 정확히 모름" — glossed with a plain, factually neutral phrase (never
    # "뇌물"/"비리" outright unless that's literally the word used — these
    # are each a distinct legal charge, and overclaiming one as another is
    # exactly the kind of exaggeration this project's neutrality rule bans).
    "공천헌금": "공천헌금(공천을 대가로 건넨 돈)",
    "정치자금법 위반": "정치자금법 위반(정치자금을 불법으로 주고받은 것)",
    "정치자금법위반": "정치자금법위반(정치자금을 불법으로 주고받은 것)",
    "알선수재": "알선수재(청탁을 들어주는 대가로 돈을 받은 것)",
    "배임수재": "배임수재(직위를 이용해 부정하게 돈을 받은 것)",
    "업무상횡령": "업무상횡령(업무 중 맡은 돈을 가로챈 것)",
    "직권남용": "직권남용(자신의 권한을 부당하게 사용한 것)",
    "배임": "배임(맡은 일을 저버려 손해를 끼친 것)",
}
_JARGON_RE = re.compile(
    "(?:" + "|".join(re.escape(k) for k in sorted(JARGON, key=len, reverse=True)) + r")(?!\()"
)


def gloss_jargon(text: str) -> str:
    """Expand a known jargon abbreviation into 'term(짧은 뜻풀이)' the first —
    and, since this is stateless, every — time it appears. Only call this on
    text that is already in its final, assembled form; calling it before a
    length-budget clip risks the clip cutting the gloss in half."""
    return _JARGON_RE.sub(lambda m: JARGON[m.group(0)], text or "")


_BULLET = re.compile(r"\s*[▲△▶▷◀◁◆◇■□●○◦※★☆∙・]\s*")


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = strip_html(text)
    text = html.unescape(text)  # entities sometimes sit inside tags
    text = unicodedata.normalize("NFKC", text)
    text = dehanja(text)
    text = text.replace("​", "").replace("\xa0", " ")
    # wire copy uses ▲ / ※ to enumerate items — turn them into plain separators
    text = _BULLET.sub(", ", text).lstrip(" ,")
    return _WS.sub(" ", text).strip()


def strip_byline(text: str) -> str:
    """Remove agency datelines / reporter bylines / redistribution footers that
    Korean outlets prepend and append to article bodies."""
    t = clean_text(text)
    prev = None
    while prev != t:
        prev = t
        t = _BYLINE_LEAD.sub("", t)
        t = _BYLINE_REPORTER.sub("", t)
    t = _EMAIL.sub("", t).rstrip()
    prev = None
    while prev != t:
        prev = t
        t = _BYLINE_TRAIL.sub("", t).rstrip()
    return _WS.sub(" ", t).strip(" -=·—")


def canonical_url(url: str) -> str:
    url = (url or "").strip()
    url = _URL_TRACKING.sub("", url)
    url = url.rstrip("?&").rstrip("/")
    return url


def url_hash(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()


def normalize_title(title: str) -> str:
    """Lowercased, punctuation-free form for fuzzy comparison."""
    t = clean_text(title).lower()
    # Drop common bracketed prefixes: [속보] [단독] [영상] <포토> etc.
    t = re.sub(r"^[\[\(<][^\]\)>]{1,12}[\]\)>]\s*", "", t)
    t = _NON_WORD.sub(" ", t)
    return _WS.sub(" ", t).strip()


def tokens(text: str) -> list[str]:
    return [tok for tok in _NON_WORD.sub(" ", (text or "").lower()).split() if len(tok) > 1]


def char_shingles(text: str, n: int = 3) -> set[str]:
    s = normalize_title(text).replace(" ", "")
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def split_sentences(text: str) -> list[str]:
    # Preserve hard line breaks as sentence boundaries before clean_text() eats
    # them, then also split on terminal punctuation and the Korean "-다." ending.
    text = re.sub(r"\s*\n+\s*", " ⁋ ", text or "")
    text = clean_text(text).replace("⁋", "\n")
    if not text:
        return []
    parts = _SENT_SPLIT.split(text)
    out: list[str] = []
    for p in parts:
        p = p.strip(" \t\r\n·-—")
        if len(p) >= 2:
            out.append(p)
    return out


def truncate(text: str, limit: int, ellipsis: str = "…") -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis


_CLAUSE_BREAK = re.compile(r"(?<=다)[\.\s]|(?<=요)[\.\s]|[!?]\s|(?<=[가-힣])[,、]\s|"
                           r"(?<=[가-힣])(며|고|면서|는데|지만)\s")

# curly pairs count open vs close; straight marks double as both, so an ODD
# count means "still inside a quote".
_CURLY_QUOTE_PAIRS = (("“", "”"), ("‘", "’"))
_STRAIGHT_QUOTES = ('"', "'")


def _quote_safe_end(text: str, idx: int, lookahead: int = 40) -> int:
    """Nudge a cut index so `text[:idx]` never ends mid-quotation — a spoken
    line that trails off inside someone's quote ("...말을 꺼낸) reads as
    broken, not just long. Extend to the nearby closing mark; if none is
    close by, retreat to just before the quote opened instead."""
    head = text[:idx]
    for open_ch, close_ch in _CURLY_QUOTE_PAIRS:
        if head.count(open_ch) > head.count(close_ch):
            close_at = text.find(close_ch, idx, idx + lookahead)
            if close_at != -1:
                return close_at + 1
            open_at = head.rfind(open_ch)
            return open_at if open_at > 0 else idx
    for q in _STRAIGHT_QUOTES:
        if head.count(q) % 2 == 1:
            close_at = text.find(q, idx, idx + lookahead)
            if close_at != -1:
                return close_at + 1
            open_at = head.rfind(q)
            return open_at if open_at > 0 else idx
    return idx


def clip_sentence(text: str, limit: int, ell: str = "…") -> str:
    """Trim to <= limit chars but END ON A NATURAL BOUNDARY, never mid-word
    and never mid-quote.

    Prefers a sentence end (…다. / …요. / ? / !), then a clause break
    (comma, 며/고/지만…). Only falls back to a hard cut + ``ell`` if nothing
    fits. Pass ``ell=".."`` for text drawn in a font that lacks the … glyph
    (Jua, DoHyeon, Black Han Sans all do).
    """
    text = clean_text(text).strip()
    if len(text) <= limit:
        return text.rstrip(" ,·")
    window = text[: limit + 1]
    ends = [m.end() for m in re.finditer(r"[다요][\.。]|[!?]|[다요](?=\s|$)", window)]
    if ends and ends[-1] >= limit * 0.55:
        idx = _quote_safe_end(text, ends[-1])
        return text[:idx].rstrip(" ,·")
    breaks = [m.start() for m in _CLAUSE_BREAK.finditer(window)]
    if breaks and breaks[-1] >= limit * 0.5:
        idx = _quote_safe_end(text, breaks[-1] + 1)
        tail = "" if idx != breaks[-1] + 1 else ell
        return text[:idx].rstrip(" ,·") + tail
    sp = window.rfind(" ")
    cut = sp if sp >= limit * 0.5 else limit - 1
    idx = _quote_safe_end(text, cut)
    tail = "" if idx != cut else ell
    return text[:idx].rstrip(" ,·") + tail
