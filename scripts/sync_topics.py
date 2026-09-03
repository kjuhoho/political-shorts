"""Move the dedup history in/out of a small committable JSON file.

    python scripts/sync_topics.py export   # topic_history table -> data/topic_history.json
    python scripts/sync_topics.py import   # data/topic_history.json -> topic_history table

GitHub Actions runs `import` after checkout (seed the fresh DB so it never
re-makes a video published elsewhere) and `export` + commit at the end. Keeping
only this file in git — not the whole multi-MB SQLite DB.
"""
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from political_shorts.config import settings
from political_shorts.db import connect, init_db

JSON_PATH = settings.root / "data" / "topic_history.json"
COLS = ["published_ts", "signature", "actor", "headline", "frame", "remote_id", "platform"]


def do_export() -> int:
    with connect(settings.db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(COLS)} FROM topic_history ORDER BY published_ts")]
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"exported {len(rows)} topics -> {JSON_PATH}")
    return 0


def do_import() -> int:
    if not JSON_PATH.exists():
        print(f"{JSON_PATH} not found — nothing to import")
        return 0
    rows = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    init_db(settings.db_path)
    added = 0
    with connect(settings.db_path) as conn:
        have = {r[0] for r in conn.execute(
            "SELECT remote_id FROM topic_history WHERE remote_id != ''")}
        for r in rows:
            if r.get("remote_id") and r["remote_id"] in have:
                continue
            conn.execute(
                f"INSERT INTO topic_history ({', '.join(COLS)}) "
                f"VALUES ({', '.join('?' for _ in COLS)})",
                [r.get(c, "") for c in COLS])
            added += 1
    print(f"imported {added} new topics from {JSON_PATH}")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "export":
        return do_export()
    if cmd == "import":
        return do_import()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
