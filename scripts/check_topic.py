"""Is this story already covered? — check before making a new short.

    python scripts/check_topic.py "김성수 대법관 후보 처남 전세 특혜 의혹"
    python scripts/check_topic.py                # just list what's been published

Reads `topic_history` (written on every successful publish, automated or via
publish_one.py) and reports whether the given headline/keywords would be treated
as a duplicate of something posted in the last TOPIC_DEDUP_DAYS.
"""
import sys
import time
from datetime import datetime

import _bootstrap  # noqa: F401

from political_shorts.config import settings
from political_shorts.db import connect
from political_shorts.hook import detect_entities, detect_frame
from political_shorts.topics import recent_duplicate, story_signature


def main() -> int:
    query = " ".join(sys.argv[1:]).strip()

    with connect(settings.db_path) as conn:
        rows = list(conn.execute(
            "SELECT published_ts, actor, frame, headline, remote_id "
            "FROM topic_history ORDER BY published_ts DESC LIMIT 40"))

    print(f"=== published topics (last {len(rows)}) ===")
    for r in rows:
        when = datetime.fromtimestamp(r["published_ts"]).strftime("%m-%d %H:%M")
        print(f"  {when}  [{r['actor'] or '-':<10}] {r['headline'][:52]}  ({r['remote_id']})")

    if not query:
        return 0

    ent = detect_entities(query)
    frame = detect_frame(query)
    sig = story_signature(query, {"president": ent.president, "politicians": ent.politicians,
                                  "parties": ent.parties, "institutions": ent.institutions},
                          frame.kind)
    actor = ent.politicians[0] if ent.politicians else ""
    with connect(settings.db_path) as conn:
        dup, why = recent_duplicate(conn, sig, settings, actor=actor)

    print()
    print(f"query : {query}")
    print(f"result: {'DUPLICATE — pick a different story' if dup else 'OK — not covered recently'}")
    if why:
        print(f"        {why}")
    return 1 if dup else 0


if __name__ == "__main__":
    raise SystemExit(main())
