"""SQLite persistence layer.

Tables
------
articles      one row per collected news item (deduplicated on url_hash)
clusters      a group of articles judged to be the same story
scripts       a generated short script (JSON payload) tied to a cluster
videos        a rendered mp4 + its metadata sidecar
jobs          one row per pipeline run
publish_log   one row per publish attempt (per platform)
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash      TEXT UNIQUE NOT NULL,
    url           TEXT NOT NULL,
    source_name   TEXT NOT NULL,
    source_lean   TEXT NOT NULL DEFAULT 'center',
    source_weight REAL NOT NULL DEFAULT 0.5,
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    published_ts  INTEGER,
    collected_ts  INTEGER NOT NULL,
    is_politics   INTEGER NOT NULL DEFAULT 0,
    politics_score REAL NOT NULL DEFAULT 0.0,
    cluster_id    INTEGER,
    raw_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_articles_collected ON articles(collected_ts);
CREATE INDEX IF NOT EXISTS idx_articles_cluster   ON articles(cluster_id);

CREATE TABLE IF NOT EXISTS clusters (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts    INTEGER NOT NULL,
    lead_title    TEXT NOT NULL,
    size          INTEGER NOT NULL DEFAULT 1,
    leans         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'new'  -- new|scripted|built|published|skipped
);

CREATE TABLE IF NOT EXISTS scripts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id   INTEGER NOT NULL,
    created_ts   INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    safety_json  TEXT NOT NULL DEFAULT '{}',
    approved     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (cluster_id) REFERENCES clusters(id)
);

CREATE TABLE IF NOT EXISTS videos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id    INTEGER NOT NULL,
    created_ts   INTEGER NOT NULL,
    video_path   TEXT NOT NULL,
    meta_path    TEXT NOT NULL,
    duration_s   REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY (script_id) REFERENCES scripts(id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts   INTEGER NOT NULL,
    finished_ts  INTEGER,
    status       TEXT NOT NULL DEFAULT 'running',  -- running|ok|error
    collected    INTEGER NOT NULL DEFAULT 0,
    clustered    INTEGER NOT NULL DEFAULT 0,
    built        INTEGER NOT NULL DEFAULT 0,
    skipped      INTEGER NOT NULL DEFAULT 0,
    published    INTEGER NOT NULL DEFAULT 0,
    log          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS publish_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     INTEGER NOT NULL,
    platform     TEXT NOT NULL,
    created_ts   INTEGER NOT NULL,
    dry_run      INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|error|dry-run
    remote_id    TEXT NOT NULL DEFAULT '',
    detail       TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (video_id) REFERENCES videos(id)
);

-- one row per story we have ALREADY turned into a published short, so a later
-- run covers a *different* issue instead of re-uploading the same one.
CREATE TABLE IF NOT EXISTS topic_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    published_ts INTEGER NOT NULL,
    signature    TEXT NOT NULL,            -- space-joined sorted keyword set
    actor        TEXT NOT NULL DEFAULT '', -- lead figure (pick_actor)
    headline     TEXT NOT NULL DEFAULT '',
    frame        TEXT NOT NULL DEFAULT '',
    remote_id    TEXT NOT NULL DEFAULT '',
    platform     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_topic_history_ts ON topic_history(published_ts);
"""


def now() -> int:
    return int(time.time())


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_MIGRATIONS = [
    # (added 2026-09-03) generally-newsworthy non-politics fallback stories
    "ALTER TABLE articles ADD COLUMN is_general INTEGER NOT NULL DEFAULT 0",
]


def init_db(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except Exception:
                pass   # column already exists — fine


# --------------------------------------------------------------------------- #
# articles
# --------------------------------------------------------------------------- #
def upsert_article(conn: sqlite3.Connection, art: dict[str, Any]) -> int:
    """Insert an article if its url_hash is new. Returns the row id.

    Returns 0 when the article already existed (nothing inserted)."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO articles
            (url_hash, url, source_name, source_lean, source_weight, title,
             summary, published_ts, collected_ts, is_politics, politics_score,
             raw_json)
        VALUES (:url_hash, :url, :source_name, :source_lean, :source_weight,
                :title, :summary, :published_ts, :collected_ts, :is_politics,
                :politics_score, :raw_json)
        """,
        {
            "url_hash": art["url_hash"],
            "url": art["url"],
            "source_name": art["source_name"],
            "source_lean": art.get("source_lean", "center"),
            "source_weight": float(art.get("source_weight", 0.5)),
            "title": art["title"],
            "summary": art.get("summary", ""),
            "published_ts": art.get("published_ts"),
            "collected_ts": art.get("collected_ts", now()),
            "is_politics": int(art.get("is_politics", 0)),
            "politics_score": float(art.get("politics_score", 0.0)),
            "raw_json": json.dumps(art.get("raw", {}), ensure_ascii=False),
        },
    )
    return cur.lastrowid if cur.rowcount else 0


def recent_articles(
    conn: sqlite3.Connection, since_ts: int, politics_only: bool = True,
    mode: str = "",
) -> list[sqlite3.Row]:
    """mode: "" -> honour politics_only (default, = domestic politics).
             "general" -> only generally-newsworthy non-politics fallback items."""
    q = "SELECT * FROM articles WHERE collected_ts >= ?"
    if mode == "general":
        q += " AND is_general = 1 AND is_politics = 0"
    elif politics_only:
        q += " AND is_politics = 1"
    q += " ORDER BY published_ts DESC, collected_ts DESC"
    return list(conn.execute(q, (since_ts,)))


def set_article_politics(
    conn: sqlite3.Connection, article_id: int, is_politics: bool, score: float,
    is_general: bool = False,
) -> None:
    conn.execute(
        "UPDATE articles SET is_politics = ?, politics_score = ?, is_general = ? WHERE id = ?",
        (int(is_politics), float(score), int(is_general), article_id),
    )


def assign_cluster(conn: sqlite3.Connection, article_ids: list[int], cluster_id: int) -> None:
    conn.executemany(
        "UPDATE articles SET cluster_id = ? WHERE id = ?",
        [(cluster_id, aid) for aid in article_ids],
    )


# --------------------------------------------------------------------------- #
# clusters
# --------------------------------------------------------------------------- #
def create_cluster(
    conn: sqlite3.Connection, lead_title: str, size: int, leans: list[str]
) -> int:
    cur = conn.execute(
        "INSERT INTO clusters (created_ts, lead_title, size, leans) VALUES (?,?,?,?)",
        (now(), lead_title, size, ",".join(sorted(set(leans)))),
    )
    return int(cur.lastrowid)


def set_cluster_status(conn: sqlite3.Connection, cluster_id: int, status: str) -> None:
    conn.execute("UPDATE clusters SET status = ? WHERE id = ?", (status, cluster_id))


def cluster_articles(conn: sqlite3.Connection, cluster_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM articles WHERE cluster_id = ? ORDER BY source_weight DESC",
            (cluster_id,),
        )
    )


# --------------------------------------------------------------------------- #
# scripts / videos
# --------------------------------------------------------------------------- #
def save_script(
    conn: sqlite3.Connection,
    cluster_id: int,
    payload: dict[str, Any],
    safety: dict[str, Any],
    approved: bool,
) -> int:
    cur = conn.execute(
        """INSERT INTO scripts (cluster_id, created_ts, payload_json, safety_json, approved)
           VALUES (?,?,?,?,?)""",
        (
            cluster_id,
            now(),
            json.dumps(payload, ensure_ascii=False),
            json.dumps(safety, ensure_ascii=False),
            int(approved),
        ),
    )
    return int(cur.lastrowid)


def save_video(
    conn: sqlite3.Connection,
    script_id: int,
    video_path: str,
    meta_path: str,
    duration_s: float,
) -> int:
    cur = conn.execute(
        """INSERT INTO videos (script_id, created_ts, video_path, meta_path, duration_s)
           VALUES (?,?,?,?,?)""",
        (script_id, now(), video_path, meta_path, float(duration_s)),
    )
    return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
def start_job(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO jobs (started_ts, status) VALUES (?, 'running')", (now(),))
    return int(cur.lastrowid)


def finish_job(conn: sqlite3.Connection, job_id: int, status: str, **counts: Any) -> None:
    fields = ["finished_ts = ?", "status = ?"]
    values: list[Any] = [now(), status]
    for key in ("collected", "clustered", "built", "skipped", "published", "log"):
        if key in counts:
            fields.append(f"{key} = ?")
            values.append(counts[key])
    values.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)


def recent_jobs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)))


# --------------------------------------------------------------------------- #
# publish log
# --------------------------------------------------------------------------- #
def log_publish(
    conn: sqlite3.Connection,
    video_id: int,
    platform: str,
    dry_run: bool,
    status: str,
    remote_id: str = "",
    detail: str = "",
) -> int:
    cur = conn.execute(
        """INSERT INTO publish_log
           (video_id, platform, created_ts, dry_run, status, remote_id, detail)
           VALUES (?,?,?,?,?,?,?)""",
        (video_id, platform, now(), int(dry_run), status, remote_id, detail[:2000]),
    )
    return int(cur.lastrowid)


def record_topic(
    conn: sqlite3.Connection,
    signature: str,
    headline: str = "",
    frame: str = "",
    remote_id: str = "",
    platform: str = "",
    actor: str = "",
) -> int:
    cur = conn.execute(
        """INSERT INTO topic_history
           (published_ts, signature, actor, headline, frame, remote_id, platform)
           VALUES (?,?,?,?,?,?,?)""",
        (now(), signature, actor, headline[:300], frame, remote_id, platform),
    )
    return int(cur.lastrowid)


def recent_topics(conn: sqlite3.Connection, since_ts: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM topic_history WHERE published_ts >= ? ORDER BY published_ts DESC",
            (since_ts,),
        )
    )


def recent_videos(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT v.*, s.cluster_id, c.lead_title
               FROM videos v
               JOIN scripts s ON s.id = v.script_id
               JOIN clusters c ON c.id = s.cluster_id
               ORDER BY v.id DESC LIMIT ?""",
            (limit,),
        )
    )
