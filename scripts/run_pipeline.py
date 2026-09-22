"""Run the full pipeline once. Used by the Windows scheduled task.

    python scripts\run_pipeline.py            # collect + build up to MAX_ITEMS_PER_RUN
    python scripts\run_pipeline.py --no-collect
    python scripts\run_pipeline.py --publish  # only publishes if ENABLE_PUBLISH=true

The channel is published by GitHub Actions (one video per morning/evening slot, one topic history, one quality
setup). A local run that also publishes posts a second video on the same channel that the cloud knows nothing
about - the same story twice, or a third video in a day. So this script does nothing that publishes unless it runs
in GitHub Actions or ALLOW_LOCAL_PUBLISH=1 is set on purpose.
"""
import argparse
import json
import os

import _bootstrap  # noqa: F401  (adds src/ to sys.path)

from political_shorts.config import settings
from political_shorts.logging_setup import setup_logging
from political_shorts.pipeline import run_pipeline


def local_publish_blocked(do_publish: bool | None, publish_enabled: bool, env=None) -> bool:
    """True when this run would publish from a machine that is not GitHub Actions and was not told to."""
    env = os.environ if env is None else env
    would_publish = publish_enabled if do_publish is None else do_publish
    return bool(would_publish) and not env.get("GITHUB_ACTIONS") and env.get("ALLOW_LOCAL_PUBLISH") != "1"


def main() -> int:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-collect", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--no-publish", action="store_true", help="build only, never publish")
    ap.add_argument("--max", type=int, default=None)
    args = ap.parse_args()

    do_publish = True if args.publish else (False if args.no_publish else None)
    if local_publish_blocked(do_publish, bool(getattr(settings, "enable_publish", False))):
        print("local publishing is off: the channel is published by GitHub Actions "
              "(set ALLOW_LOCAL_PUBLISH=1 to publish from this machine on purpose). Nothing was run.")
        return 0
    rep = run_pipeline(
        settings,
        do_collect=not args.no_collect,
        do_publish=do_publish,
        max_items=args.max,
    )
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    return 0 if (rep.built or not rep.errors) else 1


if __name__ == "__main__":
    raise SystemExit(main())
