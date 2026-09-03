"""Run the full pipeline once. Used by the Windows scheduled task.

    python scripts\run_pipeline.py            # collect + build up to MAX_ITEMS_PER_RUN
    python scripts\run_pipeline.py --no-collect
    python scripts\run_pipeline.py --publish  # only publishes if ENABLE_PUBLISH=true
"""
import argparse
import json

import _bootstrap  # noqa: F401  (adds src/ to sys.path)

from political_shorts.config import settings
from political_shorts.logging_setup import setup_logging
from political_shorts.pipeline import run_pipeline


def main() -> int:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-collect", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--no-publish", action="store_true", help="build only, never publish")
    ap.add_argument("--max", type=int, default=None)
    args = ap.parse_args()

    do_publish = True if args.publish else (False if args.no_publish else None)
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
