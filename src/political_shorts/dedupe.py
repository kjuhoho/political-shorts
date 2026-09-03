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
            # Prefer a story carried by BOTH a progressive and a conservative
            # outlet (plus wire) — easiest to narrate neutrally. Attack /
            # criticism stories are NOT avoided (neutrality lives in how we
            # explain them, not in topic selection) — see hook.make_title.
            real = c.leans - {"wire", "center"}
            balanced = 1 if len(real) >= 2 else 0
            weight = c.lead_row["source_weight"] if c.lead_row else 0
            return (balanced, c.size, len(c.leans), weight)

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
