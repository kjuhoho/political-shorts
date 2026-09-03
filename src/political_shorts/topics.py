"""Story-level de-duplication against what we've already published.

An ongoing story ("김용범 사퇴", "용혜인 겸직 논란") keeps generating fresh
articles, so every scheduled run would otherwise build another short about the
same issue. Before building, we reduce a story to a small keyword *signature*
(named entities + salient headline nouns) plus its lead actor, and compare that
against everything published in the last few days. A close match is skipped so
the run moves on to the next distinct issue.
"""
from __future__ import annotations

import sqlite3
import time

from .config import Settings, settings
from .db import recent_topics
from .logging_setup import get_logger
from .textutil import clean_text, tokens

log = get_logger("topics")

# generic political vocabulary that says nothing about *which* story this is
_STOP = {
    "대통령", "국회", "의원", "장관", "정부", "여야", "정치", "논란", "의혹", "발언",
    "속보", "단독", "종합", "오늘", "관련", "위해", "밝혀", "대한", "이번", "그는",
    "예정", "확인", "입장", "이날", "지난", "현안", "상황", "이라고", "라고", "한다",
    "했다", "밝혔다", "말했다", "대해", "대통령실", "청와대", "국민의힘", "민주당",
}
# figures so prolific that "same actor" alone means little
_BROAD_ACTORS = {"이재명", "김민석", "한동훈", "장동혁"}


def _entities_strong(entities: dict | None) -> set[str]:
    """Named politicians + parties that actually pin down the story
    (the sitting president is background noise — he's in everything)."""
    ent = entities or {}
    out: set[str] = set()
    for field in ("politicians", "parties"):
        for name in ent.get(field, []) or []:
            n = clean_text(str(name))
            if n and n != "이재명":
                out.add(n)
    return out


def story_signature(headline: str, entities: dict | None, frame: str = "") -> set[str]:
    """The full keyword set that identifies *this* story."""
    keys = _entities_strong(entities)
    ent = entities or {}
    for name in ent.get("institutions", []) or []:
        n = clean_text(str(name))
        if n:
            keys.add(n)
    if ent.get("president"):
        keys.add("이재명")
    for tok in tokens(clean_text(headline)):
        if len(tok) >= 2 and tok not in _STOP and not tok.isdigit():
            keys.add(tok)
    return keys


def signature_str(sig: set[str]) -> str:
    return " ".join(sorted(sig))


def _overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient — robust when one set is much smaller."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def recent_duplicate(
    conn: sqlite3.Connection,
    sig: set[str],
    cfg: Settings | None = None,
    *,
    actor: str = "",
) -> tuple[bool, str]:
    """(is_dup, reason). True when a very similar story was published recently.

    Two independent triggers:
      * high overlap of the full keyword signature, or
      * same lead actor + strongly overlapping named entities (catches an
        ongoing thread whose headline wording has moved on — "김용범 사퇴" then
        "김용범 후임 인선").
    """
    cfg = cfg or settings
    days = float(getattr(cfg, "topic_dedup_days", 5) or 0)
    if days <= 0 or not sig:
        return False, ""
    thr = float(getattr(cfg, "topic_dedup_threshold", 0.6))
    actor = clean_text(actor)
    since = int(time.time() - days * 86400)

    for row in recent_topics(conn, since):
        prev = set((row["signature"] or "").split())
        when = time.strftime("%m-%d %H:%M", time.localtime(row["published_ts"]))
        score = _overlap(sig, prev)
        if score >= thr:
            return True, f"{when} 게시분과 키워드 {score:.0%} 일치: {row['headline'][:40]}"
        # same specific lead figure within the window == same news thread
        prev_actor = clean_text(row["actor"]) if "actor" in row.keys() else ""
        if actor and actor == prev_actor and actor not in _BROAD_ACTORS:
            return True, f"{when} 게시분과 동일 인물({actor}) 후속: {row['headline'][:40]}"
    return False, ""
