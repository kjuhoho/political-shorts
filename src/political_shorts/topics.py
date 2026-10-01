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

# Words that say nothing about WHICH story this is — they are in half the politics headlines ever written.
_BROAD_WORDS = {
    "국민의힘", "민주당", "더불어민주당", "조국혁신당", "개혁신당", "국회", "대통령", "대통령실", "청와대",
    "정부", "여야", "여당", "야당", "정치권", "검찰", "법원", "국민", "의원", "대표", "장관", "총리",
    "논란", "비판", "공방", "발언", "주장", "지적", "반발", "요구", "강조", "밝혀", "밝혔다", "입장",
    "본회의", "상임위", "국정감사", "국감", "인사청문회", "청문회", "후보자", "관련", "이번", "오늘",
}
# The same event, told again the next day, is the same video to a viewer. Two distinctive words in common
# inside this window is enough: on 2026-09-28 and 09-29 the DMZ mine blast went out twice ("지뢰사고" +
# "dmz"), because the lead actor differed (장동혁 / 이재명) and the full-signature overlap sat under the
# threshold.
_EVENT_DAYS = 3.0
_EVENT_MIN_SCORE = 2          # one compound name ("지뢰사고") is enough; two short words also are


_PARTICLES = ("에서", "에게", "으로", "까지", "부터", "이라", "라고", "에", "은", "는", "이", "가", "을",
              "를", "의", "와", "과", "도", "만", "로")


def _stem(word: str) -> str:
    """'지뢰사고에' and '지뢰사고' are the same event word; signatures keep the particle."""
    for p in _PARTICLES:
        if len(word) - len(p) >= 3 and word.endswith(p):
            return word[: -len(p)]
    return word


def _event_words(sig: set[str]) -> set[str]:
    """The words in a signature that actually name the event."""
    return {_stem(w) for w in sig if len(w) >= 2 and _stem(w) not in _BROAD_WORDS
            and _stem(w) not in _BROAD_ACTORS and not w.isdigit()}


def _event_score(shared: set[str]) -> int:
    """How strongly a set of shared words pins one event down. A long compound ('지뢰사고', '경찰개혁') names
    the event on its own; short words need company."""
    return sum(2 if len(w) >= 4 else 1 for w in shared)


def _shared_event(a: set[str], b: set[str]) -> set[str]:
    """Event words two stories have in common ('지뢰사고' also matches '지뢰사고에', '폭발' matches '폭발원인')."""
    wa, wb = _event_words(a), _event_words(b)
    out = wa & wb
    for x in wa - out:
        for y in wb - out:
            if len(x) >= 3 and len(y) >= 3 and (x.startswith(y) or y.startswith(x)):
                out.add(min(x, y, key=len))
    return out


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
    people: set[str] | None = None,
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
        # the lead actor of a REACTION story is the reactor ("청와대, 김승원 사퇴에 결정
        # 존중" leads with 청와대), so the actor test above misses it. A specific person
        # named in this story's headline who also anchors a recent video is the same
        # thread (`people` is already limited to the headline by the caller).
        for p in (people or set()) - _BROAD_ACTORS:
            if p in prev:
                return True, f"{when} 게시분과 같은 인물({p}) 관련 사안: {row['headline'][:40]}"
        # the same EVENT, whoever is reacting to it this time
        if row["published_ts"] >= time.time() - _EVENT_DAYS * 86400:
            shared = _shared_event(sig, prev)
            if _event_score(shared) >= _EVENT_MIN_SCORE:
                return True, (f"{when} 게시분과 같은 사건({', '.join(sorted(shared)[:3])}): "
                              f"{row['headline'][:40]}")
    return False, ""
