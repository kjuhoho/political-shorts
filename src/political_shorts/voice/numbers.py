"""Korean number reading — turn digits into how a newsreader would *say* them.

The neural voices in ``tts_providers`` mostly get this right for bare integers
but stumble on the shapes political copy is full of: ``27.3%``, ``1조 2000억``,
``48석``, ``2026년 9월 8일``, ``오후 3시 30분``, ``13대 1``, ``-2.1%p``.

Everything here is pure Python (no deps) so it runs under the project's 3.14
interpreter. Entry point: :func:`read_numbers`.
"""
from __future__ import annotations

import re

_SINO_DIGIT = ["영", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
_SINO_SMALL = ["", "십", "백", "천"]
_SINO_BIG = ["", "만", "억", "조", "경"]

# prenominal native forms (…한 시, …두 명), 1‑99
_NAT_ONES = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
_NAT_TENS = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]

# counters that sound wrong in Sino and right in native, when the count is < 100
_NATIVE_COUNTERS = ("시", "시간", "명", "곳", "개", "가지", "번", "살", "척", "대", "건")
# …but "대" is ambiguous (30대 = thirties, Sino).  Handled case‑by‑case below.


def _sino_group(n: int) -> str:
    """Read a 0‑9999 group with 천/백/십, dropping a leading 1 (10 → 십, not 일십)."""
    out = ""
    for pos in (3, 2, 1, 0):
        d = (n // (10 ** pos)) % 10
        if d == 0:
            continue
        if d == 1 and pos > 0:
            out += _SINO_SMALL[pos]
        else:
            out += _SINO_DIGIT[d] + _SINO_SMALL[pos]
    return out


def sino(n: int) -> str:
    """Sino‑Korean reading of a non‑negative integer (일, 이, 십, 백, 만, 억…)."""
    if n == 0:
        return "영"
    groups: list[str] = []
    idx = 0
    while n > 0:
        n, rem = divmod(n, 10000)
        if rem:
            g = _sino_group(rem)
            # 10 000 alone reads "만", not "일만"; same for the lowest 만 group,
            # but 억/조/경 keep their 일 (일억, 일조).
            if rem == 1 and idx == 1:
                g = ""
            groups.append(g + _SINO_BIG[idx])
        idx += 1
    return " ".join(reversed(groups)).strip() or "영"


def native(n: int) -> str:
    """Native‑Korean prenominal reading for 1‑99 (한, 두, 스물세 …)."""
    if not 1 <= n <= 99:
        return sino(n)
    t, o = divmod(n, 10)
    return (_NAT_TENS[t] + _NAT_ONES[o]) or _NAT_ONES[o]


def _int(s: str) -> int:
    return int(s.replace(",", "").replace("−", "-").strip())


def _read_decimal(whole: str, frac: str) -> str:
    head = sino(_int(whole)) if whole not in ("", "-") else ""
    tail = " ".join(_SINO_DIGIT[int(c)] for c in frac)
    sign = "마이너스 " if whole.startswith("-") else ""
    return f"{sign}{head} 점 {tail}".strip()


# --- ordered rules; each consumes its match and yields spoken text ----------- #

def _rule_range(m: re.Match) -> str:
    a, b, unit = _int(m.group("a")), _int(m.group("b")), (m.group("unit") or "")
    if unit in ("시", "시간") or (unit and unit in _NATIVE_COUNTERS and b < 100 and unit != "대"):
        return f"{native(a)}에서 {native(b)} {unit}".rstrip()
    sep = f"{unit} " if unit else ""            # "3~4석" → "삼 석에서 사 석"? no → "삼에서 사 석"
    return f"{sino(a)}에서 {sino(b)}{(' ' + unit) if unit else ''}".rstrip()


def _rule_ratio(m: re.Match) -> str:
    return f"{sino(_int(m.group('a')))} 대 {sino(_int(m.group('b')))}"


def _rule_date(m: re.Match) -> str:
    y, mo, d = m.group("y"), m.group("mo"), m.group("d")
    parts = []
    if y:
        parts.append(f"{sino(_int(y))} 년")
    if mo:
        parts.append(f"{sino(_int(mo))} 월")   # 6월 → 육 월, 10월 → 십 월 (읽기 표준)
    if d:
        parts.append(f"{sino(_int(d))} 일")
    return " ".join(parts)


def _rule_clock(m: re.Match) -> str:
    h, mi = m.group("h"), m.group("mi")
    out = f"{native(_int(h))} 시"
    if mi:
        out += " 정각" if _int(mi) == 0 else f" {sino(_int(mi))} 분"
    return out


# a numeric literal: comma‑grouped (12,000 / 1,234) or plain (2026) — never a
# trailing separator, so "151," and "3.5" don't get their tail eaten.
_N = r"\d{1,3}(?:,\d{3})+|\d+"


def _rule_won(m: re.Match) -> str:
    """`1조 2,000억 원`, `3000억`, `4조원` … keep the 조/억 words, read the rest."""
    txt = re.sub(_N, lambda g: sino(_int(g.group(0))), m.group(0))
    txt = txt.replace("조", " 조 ").replace("억", " 억 ").replace("만", " 만 ")
    return re.sub(r"\s{2,}", " ", txt).strip()


def _rule_percent(m: re.Match) -> str:
    sign = "마이너스 " if m.group("sign") else ""
    whole, frac = m.group("whole"), m.group("frac")
    num = _read_decimal(whole, frac) if frac else sino(_int(whole))
    tail = " 퍼센트 포인트" if m.group("p") else " 퍼센트"
    return f"{sign}{num}{tail}"


def _rule_counter(m: re.Match) -> str:
    n, unit = _int(m.group("n")), m.group("unit")
    if unit == "대" and n >= 10:                       # 30대, 6070대 → thirties (Sino, tight)
        return f"{sino(n)}대"
    if unit in ("시", "시간") or (unit in _NATIVE_COUNTERS and n < 100):
        return f"{native(n)} {unit}"
    return f"{sino(n)} {unit}"


def _rule_decimal(m: re.Match) -> str:
    return _read_decimal(m.group("whole"), m.group("frac"))


def _rule_plain(m: re.Match) -> str:
    sign = "마이너스 " if m.group(0).lstrip().startswith(("-", "−")) else ""
    return sign + sino(_int(m.group("n")))


_RULES: list[tuple[re.Pattern, callable]] = [
    # 3~4명 / 3-4석  (range)
    (re.compile(rf"(?<!\d)(?P<a>{_N})\s*[~〜–—]\s*(?P<b>{_N})\s*(?P<unit>[가-힣]{{0,3}})"), _rule_range),
    # 2026년 9월 8일 / 9월 8일
    (re.compile(rf"(?:(?P<y>\d{{4}})\s*년)?\s*(?:(?P<mo>\d{{1,2}})\s*월)?\s*(?P<d>\d{{1,2}})\s*일(?!\w)"), _rule_date),
    (re.compile(r"(?P<y>\d{4})\s*년(?!\s*\d)"), lambda m: f"{sino(_int(m.group('y')))} 년"),
    (re.compile(r"(?<!\d)(?P<mo>\d{1,2})\s*월(?!\s*\d)(?!요일)"), lambda m: f"{sino(_int(m.group('mo')))} 월"),
    # 오후 3시 30분 / 3시
    (re.compile(r"(?<!\d)(?P<h>\d{1,2})\s*시(?:\s*(?P<mi>\d{1,2})\s*분)?"), _rule_clock),
    (re.compile(r"(?<!\d)(?P<n>\d{1,3})\s*분(?!기|의|석)"), lambda m: f"{sino(_int(m.group('n')))} 분"),
    # money with 조/억 units
    (re.compile(rf"(?<!\d)(?:{_N})\s*조(?:\s*(?:{_N})\s*억)?(?:\s*(?:{_N})\s*만)?\s*원?"), _rule_won),
    (re.compile(rf"(?<!\d)(?:{_N})\s*억(?:\s*(?:{_N})\s*만)?\s*원?"), _rule_won),
    # 13대 1  (ratio)
    (re.compile(rf"(?<!\d)(?P<a>{_N})\s*대\s*(?P<b>{_N})(?!\d)"), _rule_ratio),
    # -2.1%p / 27.3% / 12%
    (re.compile(rf"(?P<sign>[-−])?(?P<whole>{_N})(?:\.(?P<frac>\d+))?\s*%(?P<p>p|포인트|P)?"), _rule_percent),
    # 48석 / 3명 / 68세 / 5곳 / 30대
    (re.compile(rf"(?<!\d)(?P<n>{_N})\s*(?P<unit>석|명|세|살|곳|개|가지|번|건|척|위|판|쇄|대|주|년생)"), _rule_counter),
    # 3.5 (bare decimal)
    (re.compile(rf"(?<![\d.])(?P<whole>[-−]?{_N})\.(?P<frac>\d+)(?![\d.])"), _rule_decimal),
    # bare integer, keep a leading minus
    (re.compile(rf"(?<![\d.\w])(?P<n>[-−]?{_N})(?![\d.])"), _rule_plain),
]


def read_numbers(text: str) -> str:
    """Rewrite every numeric shape in *text* the way a newsreader would say it."""
    if not text or not any(c.isdigit() for c in text):
        return text
    for pat, fn in _RULES:
        text = pat.sub(fn, text)
    return re.sub(r"\s{2,}", " ", text).strip()
