"""Hear the voice pipeline on any line of text — no full render needed.

    python scripts/voice_preview.py "지지율이 27.3% 올랐습니다" --role what
    python scripts/voice_preview.py --file tools/voice/enrollment_ko.txt --role hook
    python scripts/voice_preview.py "속보입니다" --engine piper --all-roles
    python scripts/voice_preview.py "..." --raw     # skip Layer 1/3, engine only

Thin wrapper around ``political_shorts.voice.tools.preview`` (same code the
``political_shorts voice preview`` subcommand uses). Writes WAVs to
output/voice_preview/ and opens the first one. Talks only to whatever TTS the
.env is configured for; nothing is published.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from political_shorts.config import settings
from political_shorts.logging_setup import setup_logging
from political_shorts.voice import normalize
from political_shorts.voice.tools import ROLES, preview


def main() -> int:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--file", help="read the text from a file (first non-# line, or --line N)")
    ap.add_argument("--line", type=int, default=0, help="with --file: 1-based line number")
    ap.add_argument("--role", default="what", choices=ROLES)
    ap.add_argument("--all-roles", action="store_true", help="one clip per role, to compare")
    ap.add_argument("--engine", default="", help="override TTS_PROVIDER (edge|piper|myvoice|…)")
    ap.add_argument("--raw", action="store_true", help="bypass Layer 1/3 (engine only)")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    text = args.text
    if args.file:
        lines = [ln.strip() for ln in Path(args.file).read_text(encoding="utf-8").splitlines()]
        body = [ln for ln in lines if ln and not ln.startswith("#")]
        text = lines[args.line - 1] if args.line else (body[0] if body else "")
        parts = text.split(" ", 1)
        if len(parts) == 2 and parts[0].isdigit():
            text = parts[1]
    if not text:
        ap.error("give some text, or --file")

    roles = ROLES if args.all_roles else [args.role]
    if not args.raw:
        for r in roles:
            print(f"[{r:9}] {normalize.prepare(text, r).text}")
    else:
        print(f"[raw] {text}")

    res = preview(text, roles=roles, engine=args.engine, raw=args.raw, cfg=settings)
    made: list[Path] = []
    for role, path, secs in res:
        if path:
            made.append(path)
            print(f"  {role}: {path}  ({secs:.2f}s)")
        else:
            print(f"  {role}: (no audio — engine chain fell through)")

    if made and not args.no_open and os.name == "nt":
        os.startfile(made[0])  # noqa: S606  (local preview convenience)
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
