"""Step 3 — group near-duplicate articles into story clusters.

Two articles belong together when their headline + summary are similar enough
by a blend of:
  * title fuzz (rapidfuzz token_set / token_sort)  — reordered / partial headlines
  * title syllable-bigram Jaccard                   — reworded headlines
  * body (title+summary) token Jaccard              — shared vocabulary
  * body syllable-bigram Jaccard                    — shared phrasing

Korean headlines share little literal surface text even for the same event, so
the body text carries real weight here.

Single-source stories still form a cluster of size 1 so the rest of the
pipeline has a uniform unit to work with.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

try:
    from rapidfuzz.fuzz import token_set_ratio, token_sort_ratio
except Exception:  # pragma: no cover - fallback if rapidfuzz missing
    from difflib import SequenceMatcher

    def token_set_ratio(a: str, b: str) -> float:  # type: ignore
        return SequenceMatcher(None, a, b).ratio() * 100.0

    token_sort_ratio = token_set_ratio  # type: ignore

from .config import Settings, settings
from .db import assign_cluster, connect, create_cluster, recent_articles
from .logging_setup import get_logger
from .textutil import jaccard, normalize_title, tokens

log = get_logger("dedupe")

SIM_THRESHOLD = 0.42

# "A, B에게 '…공격…'" or he-said-she-said headlines — a personal attack / clash
# that can't be covered neutrally in 30s. The ranker sends these to the back.
_ATTACK_QUOTE = ("도망", "받아야", "사퇴하라", "물러나라", "책임져", "거짓말",
                 "내로남불", "적반하장", "후안무치", "궤변", "말바꾸기", "뒤집",
                 "겁박", "적폐", "직무유기", "우롱", "해명하라", "사죄하라",
                 "레임덕", "배은망덕", "자다가 봉창", "몰염치", "무능", "위선")
_QUOTE_SPAN = re.compile(r"[\"'“‘]([^\"'”’]{3,60})[\"'”’]")
# starts "이름, …" and then addresses / reacts to another person
_ATTACK_RE = re.compile(
    r"^[가-힣]{2,4}\s*,\s*.{0,20}?[가-힣]{2,4}\s*(?:에게|에|을|를|향해|측에|의)?\s*"
    r"(?:[\"'“”‘’]|발언|주장|글|비판|공세)"
)


def _is_attack_headline(title: str) -> bool:
    t = (title or "").strip()
    quotes = _QUOTE_SPAN.findall(t)
    if any(w in q for q in quotes for w in _ATTACK_QUOTE):
        return True
    if _ATTACK_RE.match(t) and (len(quotes) >= 1 or "비판" in t or "공세" in t or "직격" in t):
        return True
    return False


@dataclass
class Cluster:
    article_ids: list[int] = field(default_factory=list)
    lead_row: sqlite3.Row | None = None
    leans: set[str] = field(default_factory=set)

    @property
    def size(self) -> int:
        return len(self.article_ids)


def _field(row: Any, key: str, default: str = "") -> str:
    """Read a column from a sqlite3.Row or a plain dict, tolerating absence."""
    try:
        val = row[key]
    except (KeyError, IndexError):
        return default
    return default if val is None else str(val)


def _bigrams(text: str) -> set[str]:
    s = normalize_title(text).replace(" ", "")
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def similarity(a: Any, b: Any) -> float:
    ta, tb = _field(a, "title"), _field(b, "title")
    ca = f"{ta} {_field(a, 'summary')}"
    cb = f"{tb} {_field(b, 'summary')}"

    title_fz = max(token_set_ratio(ta, tb), token_sort_ratio(ta, tb)) / 100.0
    title_bg = jaccard(_bigrams(ta), _bigrams(tb))
    body_tj = jaccard(set(tokens(ca)), set(tokens(cb)))
    body_bg = jaccard(_bigrams(ca), _bigrams(cb))

    return 0.45 * title_fz + 0.15 * title_bg + 0.25 * body_tj + 0.15 * body_bg


def _cluster_rows(rows: list[sqlite3.Row]) -> list[Cluster]:
    """Single-link agglomeration over the similarity graph."""
    n = len(rows)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            if similarity(rows[i], rows[j]) >= SIM_THRESHOLD:
                union(i, j)

    groups: dict[int, Cluster] = {}
    for idx, row in enumerate(rows):
        root = find(idx)
        cl = groups.setdefault(root, Cluster())
        cl.article_ids.append(row["id"])
        cl.leans.add(row["source_lean"])
        if cl.lead_row is None or row["source_weight"] > cl.lead_row["source_weight"]:
            cl.lead_row = row
    return list(groups.values())


def build_clusters(cfg: Settings | None = None, mode: str = "") -> list[int]:
    """Cluster not-yet-clustered articles from the collect window.

    mode "" -> domestic politics (default).  mode "general" -> the apolitical
    "generally newsworthy" fallback pool (only used when politics is dry).
    Returns the list of new cluster ids, most newsworthy first."""
    cfg = cfg or settings
    since = __import__("time").time() - cfg.collect_window_hours * 3600

    with connect(cfg.db_path) as conn:
        rows = [
            r
            for r in recent_articles(conn, int(since), politics_only=True, mode=mode)
            if r["cluster_id"] is None
        ]
        if not rows:
            log.info("dedupe: nothing to cluster (mode=%s)", mode or "politics")
            return []

        clusters = _cluster_rows(rows)

        def _rank(c: "Cluster") -> tuple:
            title = c.lead_row["title"] if c.lead_row else ""
            # 0) DE-PRIORITISE a "one person attacking another" headline — those
            #    can't be covered neutrally in 30s. Build one only if nothing
            #    else is left. (neutrality > freshness)
            not_attack = 0 if _is_attack_headline(title) else 1
            # 1) BALANCE — a story carried by both a progressive and a
            #    conservative outlet (plus wire) is the safest to cover neutrally.
            real = c.leans - {"wire", "center"}
            balanced = 1 if len(real) >= 2 else 0
            weight = c.lead_row["source_weight"] if c.lead_row else 0
            return (not_attack, balanced, c.size, len(c.leans), weight)

        clusters.sort(key=_rank, reverse=True)

        new_ids: list[int] = []
        for cl in clusters:
            lead_title = cl.lead_row["title"] if cl.lead_row else "(untitled)"
            cid = create_cluster(conn, lead_title, cl.size, sorted(cl.leans))
            assign_cluster(conn, cl.article_ids, cid)
            new_ids.append(cid)
            log.info(
                "cluster #%d size=%d leans=%s :: %s",
                cid,
                cl.size,
                ",".join(sorted(cl.leans)),
                lead_title[:70],
            )
    return new_ids
