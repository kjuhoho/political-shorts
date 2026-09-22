"""Dates for the writer, and a mechanical guard for years.

Why: none of the prompts that write a script told the model what day it is, and the articles usually say
"20일" or "지난해", never the year. The model then filled in a year from its training data (2023-2025) and
videos shipped with wrong years. Two layers, both here:

  date_block()      the current date (KST), what "올해/지난해/내년" mean today, the publication date of the
                    articles, and the rule "never guess a year" — put in front of every writing prompt
  strip_years()     after the script is written, any explicit year that neither the current year nor the
                    articles / research mention is REMOVED. A missing year is harmless; a wrong one is not.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))
_WEEKDAY = "월화수목금토일"

# "2026년", "2024년도" — only a number followed by 년 is a year ("2000억", "1,431개" are quantities)
_YEAR_RX = re.compile(r"(?<![\d,.])((?:19|20)\d{2})\s*년(도)?")
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
        "[연도 규칙] 연도는 기사·자료에 적힌 것만 쓰고, 적혀 있지 않은 연도는 절대 추측해서 붙이지 말 것. "
        "기사에 '지난 15일', '지난해', '내년'처럼 상대적으로만 적혀 있으면 위 기준으로 정확히 계산하거나 "
        "그 표현 그대로 쓸 것. 확신이 없으면 연도를 빼고 월·일만 쓸 것.",
    ]
    dates = article_dates(rows or [])
    if dates:
        lines.append("[기사 발행일] " + ", ".join(dates) + " — 기사 속 '20일', '지난 15일' 같은 표현은 이 날짜 기준입니다.")
    return "\n".join(lines) + "\n\n"


def _allowed_years(source_text: str, now: datetime | None = None) -> set[int]:
    allowed = {int(m.group(1)) for m in _YEAR_RX.finditer(source_text or "")}
    allowed |= {int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", source_text or "")}   # "2024. 3" style too
    allowed.add(today(now).year)
    return allowed


def unsupported_years(text: str, source_text: str, now: datetime | None = None) -> list[int]:
    """Years written in `text` that are neither this year nor found in the source material."""
    allowed = _allowed_years(source_text, now)
    return sorted({int(m.group(1)) for m in _YEAR_RX.finditer(text or "")} - allowed)


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


def strip_years(text: str, source_text: str, now: datetime | None = None) -> tuple[str, list[int]]:
    """-> (text without unsupported years, [years removed]). '2025년 9월 19일' -> '9월 19일'."""
    if not text:
        return text, []
    allowed = _allowed_years(source_text, now)
    removed: list[int] = []

    def _sub(m: re.Match) -> str:
        y = int(m.group(1))
        if y in allowed:
            return m.group(0)
        removed.append(y)
        return ""

    out = _YEAR_RX.sub(_sub, text)
    if removed:
        out = re.sub(r"[ \t]{2,}", " ", out).replace(" ,", ",").strip()      # doubled spaces left by the removal
    return out, removed
