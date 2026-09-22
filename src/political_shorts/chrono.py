"""Dates for the writer, and a mechanical guard for years.

Why: none of the prompts that write a script told the model what day it is, and the articles usually say
"20일" or "지난해", never the year. The model then filled in a year from its training data (2023-2025) and
videos shipped with wrong years. Two layers, both here:

  date_block()      the current date (KST), what "올해/지난해/내년" mean today, the publication date of the
                    articles, and the rule "never guess a year" — put in front of every writing prompt
  strip_years()     any explicit year that neither the current year nor the articles / research mention is
                    dropped first (a wrong year is worse than none) ...
  normalize_dates() ... and then EVERY 'M월 D일' is rewritten correct and complete: the month agrees with the
                    articles, the first date of the script carries its year (computed from the publication date).
  dateline()        a script that states no date at all gets '2026년 9월 21일 소식입니다.' — year and date are a
                    default of every video, not something the model may forget.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))
_WEEKDAY = "월화수목금토일"

# "2026년", "2024년도", and the spoken short forms "24년도" / "'24년" — only a number followed by 년 is a year
# ("2000억", "1,431개" are quantities; "24년 만에", "20년간" are durations and stay untouched)
_YEAR_RX = re.compile(r"(?<![\d,.])(?:(?P<y4>(?:19|20)\d{2})\s*년(?:도)?|['\u2018\u2019]\s*(?P<y2a>\d{2})\s*년(?:도)?"
                      r"|(?P<y2b>\d{2})\s*년도)")


def _year_of(m: re.Match) -> int:
    """The four-digit year of a _YEAR_RX match ('24년도' -> 2024)."""
    if m.group("y4"):
        return int(m.group("y4"))
    return 2000 + int(m.group("y2a") or m.group("y2b"))
# relative expressions spelled with a year: they must agree with today's date
_REL_RX = re.compile(r"(올해|금년|올|지난해|작년|전년|내년|명년|재작년)\s*\(?\s*((?:19|20)\d{2})\s*년?")


def today(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(KST)


def relative_years(now: datetime | None = None) -> dict[str, int]:
    y = today(now).year
    return {"올해": y, "금년": y, "올": y, "지난해": y - 1, "작년": y - 1, "전년": y - 1, "재작년": y - 2,
            "내년": y + 1, "명년": y + 1}


def article_dates(rows: list[Any], limit: int = 5) -> list[str]:
    """['2026-09-21 연합뉴스', …] — when the articles were published (KST)."""
    out: list[str] = []
    for r in rows:
        try:
            ts = int(r["published_ts"])
            src = str(r["source_name"] or "")
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if ts <= 0:
            continue
        line = f"{datetime.fromtimestamp(ts, KST):%Y-%m-%d} {src}".strip()
        if line not in out:
            out.append(line)
        if len(out) >= limit:
            break
    return out


def date_block(rows: list[Any] | None = None, now: datetime | None = None) -> str:
    t = today(now)
    y = t.year
    lines = [
        f"[오늘 날짜] 오늘은 {y}년 {t.month}월 {t.day}일({_WEEKDAY[t.weekday()]}요일)입니다. "
        f"올해는 {y}년, 작년(지난해)은 {y - 1}년, 내년은 {y + 1}년입니다.",
        "[연도 규칙] 연도와 날짜는 기본으로 정확하게 쓴다. 대본에 날짜를 쓸 때는 '2026년 9월 21일'처럼 연·월·일을 "
        "모두 쓰고, 연도는 위 [오늘 날짜]·[기사 발행일]과 기사·자료에 적힌 것만 근거로 한다(기억에 의존해 짐작 금지). "
        "기사에 '지난 15일', '지난해', '내년'처럼 상대적으로만 적혀 있으면 위 기준으로 정확히 계산해 연·월·일로 쓸 것. "
        "날짜를 전혀 알 수 없는 사건만 '이날'로 쓴다.",
    ]
    dates = article_dates(rows or [])
    if dates:
        lines.append("[기사 발행일] " + ", ".join(dates) + " — 기사 속 '20일', '지난 15일' 같은 표현은 이 날짜 기준입니다.")
    return "\n".join(lines) + "\n\n"


def _allowed_years(source_text: str, now: datetime | None = None) -> set[int]:
    allowed = {_year_of(m) for m in _YEAR_RX.finditer(source_text or "")}
    allowed |= {int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", source_text or "")}   # "2024. 3" style too
    allowed.add(today(now).year)
    return allowed


def unsupported_years(text: str, source_text: str, now: datetime | None = None) -> list[int]:
    """Years written in `text` that are neither this year nor found in the source material."""
    allowed = _allowed_years(source_text, now)
    return sorted({_year_of(m) for m in _YEAR_RX.finditer(text or "")} - allowed)


def wrong_relative_years(text: str, now: datetime | None = None) -> list[str]:
    """'올해 2024년', '지난해 2026년' — a relative word that disagrees with the year written next to it."""
    rel = relative_years(now)
    bad: list[str] = []
    for m in _REL_RX.finditer(text or ""):
        if rel.get(m.group(1)) != int(m.group(2)):
            bad.append(m.group(0).strip())
    return bad


# ---------------------------------------------------------------------------- month / day
# "2023년 5월 21일" in a story that broke on 21 September: removing the year leaves a wrong month behind.
# News says "21일" or "지난 15일"; the month comes from the article's publication date.
_MD_RX = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def pub_dates(rows: list[Any], limit: int = 6) -> list[tuple[int, int, int]]:
    """(year, month, day) of the articles' publication, KST, newest first."""
    out: list[tuple[int, int, int]] = []
    for r in rows:
        try:
            ts = int(r["published_ts"])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if ts > 0:
            d = datetime.fromtimestamp(ts, KST)
            key = (d.year, d.month, d.day)
            if key not in out:
                out.append(key)
    return sorted(out, reverse=True)[:limit]


def _month_days(source_text: str, pubs: list[tuple[int, int, int]],
                now: datetime | None) -> tuple[set[tuple[int, int]], dict[int, int]]:
    """(allowed (month, day) pairs, {day: the month it most likely belongs to})."""
    allowed = {(int(m.group(1)), int(m.group(2))) for m in _MD_RX.finditer(source_text or "")}
    t = today(now)
    base = pubs or [(t.year, t.month, t.day)]
    day_month: dict[int, int] = {}
    for _y, m, d in base:
        allowed.add((m, d))
    for x in re.findall(r"(?<![\d월])(\d{1,2})\s*일", source_text or ""):
        day = int(x)
        if not 1 <= day <= 31:
            continue
        for _y, m, _d in base:
            allowed.add((m, day))                       # same month as the article ...
            allowed.add((m - 1 or 12, day))             # ... or the month before ("지난 26일")
            day_month.setdefault(day, m)
    return allowed, day_month


def fix_month_days(text: str, source_text: str, pubs: list[tuple[int, int, int]],
                   now: datetime | None = None) -> tuple[str, list[tuple[str, str]]]:
    """Make every 'M월 D일' agree with the sources. A date the articles/research state is kept; a day the articles
    give as 'D일' gets the month of the article's publication date; a date nothing supports becomes '이날'.
    -> (text, [(before, after), …])"""
    if not text:
        return text, []
    allowed, day_month = _month_days(source_text, pubs, now)
    changes: list[tuple[str, str]] = []

    def _sub(m: re.Match) -> str:
        mo, d = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= d <= 31) or (mo, d) in allowed:
            return m.group(0)
        new = f"{day_month[d]}월 {d}일" if d in day_month else "이날"
        changes.append((m.group(0), new))
        return new

    return _MD_RX.sub(_sub, text), changes


# ------------------------------------------------------------ dates are a DEFAULT, and always correct
# Year and date are basic facts of a news video: every script states the full date ("2026년 9월 21일") at least
# once, computed from the articles' publication date — never left to the model, never dropped.
_FULL_RX = re.compile(r"(?:(?P<y>(?:19|20)\d{2}|['\u2018\u2019]?\d{2})\s*년(?:도)?\s*)?(?<!\d)"
                      r"(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일")


def resolve_year(month: int, day: int, pubs: list[tuple[int, int, int]], now: datetime | None = None) -> int:
    """The year in which month/day is closest to the articles' publication date (a 30 Dec date in a 2 Jan
    article is last year)."""
    t = today(now)
    base = date(*pubs[0]) if pubs else date(t.year, t.month, t.day)
    best: tuple[int, int] | None = None
    for y in (base.year - 1, base.year, base.year + 1):
        try:
            diff = abs((date(y, month, day) - base).days)
        except ValueError:
            continue
        if best is None or diff < best[0]:
            best = (diff, y)
    return best[1] if best else base.year


def date_facts(pubs: list[tuple[int, int, int]], now: datetime | None = None) -> str:
    """'2026년 9월 21일 2026년 9월 22일' — the dates this video is allowed to state, for the source text the
    quality checks compare against (a full date is not an 'added number')."""
    t = today(now)
    days = [(t.year, t.month, t.day), *pubs]
    seen: list[str] = []
    for y, m, d in days:
        s = f"{y}년 {m}월 {d}일"
        if s not in seen:
            seen.append(s)
    return " ".join(seen)


def normalize_dates(text: str, source_text: str, pubs: list[tuple[int, int, int]],
                    state: dict[str, bool], now: datetime | None = None) -> tuple[str, list[tuple[str, str]]]:
    """Every 'M월 D일' in `text` becomes correct and complete:
       - the month agrees with the sources (a day the article gives as '21일' gets its publication month; a date
         nothing supports becomes '이날'),
       - the FIRST date of the script (tracked in `state`) is written with its year, computed from the
         publication date (or taken from the source when it states one); a year the writer attached is
         replaced by that computed year, never trusted.
    -> (text, [(before, after)])"""
    if not text:
        return text, []
    allowed, day_month = _month_days(source_text, pubs, now)
    t = today(now)
    base_year = pubs[0][0] if pubs else t.year
    src_years = {int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", source_text or "")}
    # dates the sources spell out as 'M월 D일' — apart from the publication days themselves, which the writer
    # gets from us, not from the article
    explicit = {(int(x.group(1)), int(x.group(2))) for x in _MD_RX.finditer(source_text or "")}
    explicit -= {(m, d) for _y, m, d in pubs} | {(t.month, t.day)}
    changes: list[tuple[str, str]] = []

    def _sub(m: re.Match) -> str:
        given, mo, d = m.group("y"), int(m.group("m")), int(m.group("d"))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return m.group(0)
        if given:
            given = re.sub(r"\D", "", given)
            given = str(2000 + int(given)) if len(given) == 2 else given          # "24년 4월 21일" -> 2024
        kept = (mo, d) in explicit
        if (mo, d) not in allowed:
            if d not in day_month:
                changes.append((m.group(0), "이날"))
                return "이날"
            mo = day_month[d]
        stated = re.search(rf"((?:19|20)\d{{2}})\s*년\s*{mo}\s*월\s*{d}\s*일", source_text or "")
        if stated:
            year = int(stated.group(1))
        elif given and kept and int(given) < base_year and int(given) in src_years:
            year = int(given)                       # an older event: the source itself names that year
        else:
            year = resolve_year(mo, d, pubs, now)
        if given or not state.get("first_done"):
            out = f"{year}년 {mo}월 {d}일"
        else:
            out = f"{mo}월 {d}일"
        state["first_done"] = True
        if out != m.group(0):
            changes.append((m.group(0), out))
        return out

    return _FULL_RX.sub(_sub, text), changes


def dateline(pubs: list[tuple[int, int, int]], now: datetime | None = None) -> str:
    """'2026년 9월 21일 소식입니다.' — the default date sentence for a script that states no date at all."""
    t = today(now)
    y, m, d = pubs[0] if pubs else (t.year, t.month, t.day)
    return f"{y}년 {m}월 {d}일 소식입니다."


def strip_years(text: str, source_text: str, now: datetime | None = None) -> tuple[str, list[int]]:
    """-> (text without unsupported years, [years removed]). '2025년 9월 19일' -> '9월 19일'."""
    if not text:
        return text, []
    allowed = _allowed_years(source_text, now)
    removed: list[int] = []

    def _sub(m: re.Match) -> str:
        y = _year_of(m)
        if y in allowed:
            return m.group(0)
        removed.append(y)
        return ""

    out = _YEAR_RX.sub(_sub, text)
    if removed:
        out = re.sub(r"[ \t]{2,}", " ", out).replace(" ,", ",").strip()      # doubled spaces left by the removal
    return out, removed
