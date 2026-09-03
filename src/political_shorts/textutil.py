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
}
_HANJA_RE = re.compile("|".join(map(re.escape, sorted(_HANJA, key=len, reverse=True))))


def dehanja(text: str) -> str:
    return _HANJA_RE.sub(lambda m: _HANJA[m.group(0)], text or "")


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


def clip_sentence(text: str, limit: int, ell: str = "…") -> str:
    """Trim to <= limit chars but END ON A NATURAL BOUNDARY, never mid-word.

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
        return text[: ends[-1]].rstrip(" ,·")
    breaks = [m.start() for m in _CLAUSE_BREAK.finditer(window)]
    if breaks and breaks[-1] >= limit * 0.5:
        return text[: breaks[-1] + 1].rstrip(" ,·") + ell
    sp = window.rfind(" ")
    cut = sp if sp >= limit * 0.5 else limit - 1
    return text[:cut].rstrip(" ,·") + ell
