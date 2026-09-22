"""Decide, with the standard library only, whether a scheduled run still has work to do.

The channel posts twice a day, always: one in the morning slot (04:00-14:00 KST) and one in the evening slot
(14:00-04:00 KST). GitHub's cron is unreliable — runs arrive 2-5 hours late, or are dropped — so the workflow
is scheduled SEVERAL times per slot and each trigger first asks this gate "is the slot I am actually running in
already filled?". A filled slot ends the job in seconds; an empty one goes on to build and publish.

    python scripts/slot_gate.py            # reads data/topic_history.json, writes skip=true|false to $GITHUB_OUTPUT

Manual runs (workflow_dispatch) are never skipped.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
MORNING = (4, 14)          # hours of the KST day
EVENING = (14, 28)         # 28 == 04:00 the next day


def slot_window(now: datetime) -> tuple[str, float, float]:
    """(slot name, window start ts, window end ts) for the KST moment `now`."""
    now = now.astimezone(KST)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now.hour < MORNING[0]:                       # 00:00-03:59 still belongs to yesterday's evening
        day -= timedelta(days=1)
        name, (a, b) = "evening", EVENING
    elif now.hour < MORNING[1]:
        name, (a, b) = "morning", MORNING
    else:
        name, (a, b) = "evening", EVENING
    return name, (day + timedelta(hours=a)).timestamp(), (day + timedelta(hours=b)).timestamp()


def slot_filled(rows: list[dict], now: datetime) -> tuple[str, bool]:
    name, start, end = slot_window(now)
    return name, any(start <= float(r.get("published_ts", 0)) < end for r in rows)


def main() -> int:
    event = os.environ.get("EVENT_NAME", "")
    skip = False
    if event == "schedule":
        path = Path(__file__).resolve().parents[1] / "data" / "topic_history.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            rows = []
        name, filled = slot_filled(rows, datetime.now(timezone.utc))
        skip = filled
        print(f"slot={name} filled={filled} (KST {datetime.now(KST):%m-%d %H:%M}) -> {'skip' if skip else 'run'}")
    else:
        print(f"event={event or 'manual'} -> run")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"skip={'true' if skip else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
