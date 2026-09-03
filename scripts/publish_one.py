"""Publish a single already-built video + its .meta.json sidecar.

    python scripts/publish_one.py output/manual_c1.mp4
    python scripts/publish_one.py output/x.mp4 --publish-at 2026-09-03T07:00:00+09:00

`--publish-at` uploads it as private and lets YouTube flip it public server-side
at that time — survives the PC being off / offline at the publish moment.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401

from political_shorts.config import settings
from political_shorts.db import connect, record_topic
from political_shorts.logging_setup import get_logger
from political_shorts.publishers import get_publishers
from political_shorts.topics import story_signature, signature_str

log = get_logger("publish_one")


def _remember_topic(meta: dict, remote_id: str, platform: str) -> None:
    """So a later run (automated or hand-authored) never re-covers this story."""
    try:
        headline = meta.get("headline") or meta.get("title", "")
        sig = story_signature(headline, meta.get("entities"), meta.get("frame", ""))
        with connect(settings.db_path) as conn:
            record_topic(conn, signature_str(sig), headline, meta.get("frame", ""),
                         remote_id, platform, str(meta.get("topic") or ""))
        print(f"  topic remembered (dedup): actor={meta.get('topic') or '-'}")
    except Exception as exc:
        log.warning("could not record topic_history: %s", exc)


def _to_rfc3339(s: str) -> str:
    """Accept 'YYYY-MM-DDTHH:MM' / with offset / 'Z'. Assume the configured
    timezone when no offset is given. Return RFC3339 UTC ('...Z') for the API."""
    s = s.strip().replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"bad --publish-at (use e.g. 2026-09-03T07:00): {s!r}")
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo
        try:
            dt = dt.replace(tzinfo=ZoneInfo(settings.timezone))
        except Exception:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0Z")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="path to the .mp4 (its .meta.json must sit next to it)")
    ap.add_argument("--platform", default="youtube")
    ap.add_argument("--publish-at", default="",
                    help="schedule server-side publish, e.g. 2026-09-03T07:00")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.is_absolute():
        video = settings.root / video
    meta_path = video.with_suffix(".meta.json")
    if not video.exists():
        print(f"video not found: {video}", file=sys.stderr)
        return 2
    if not meta_path.exists():
        print(f"sidecar not found: {meta_path}", file=sys.stderr)
        return 2

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if args.publish_at:
        meta["publish_at"] = _to_rfc3339(args.publish_at)
    print(f"publishing {video.name}")
    print(f"  title  : {meta['title']}")
    print(f"  privacy: {meta.get('privacy_status')}")
    if meta.get("publish_at"):
        print(f"  publishAt: {meta['publish_at']}  (uploads private, auto-public then)")

    published = False
    for pub in get_publishers(settings):
        if pub.name != args.platform:
            continue
        res = pub.publish(video, meta)
        print(json.dumps(res.__dict__, ensure_ascii=False, indent=2))
        if res.status == "ok":
            published = True
            _remember_topic(meta, res.remote_id, pub.name)
    return 0 if published else 1


if __name__ == "__main__":
    raise SystemExit(main())
