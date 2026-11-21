r"""Turn a folder of enrollment recordings into a Piper training dataset.

    python scripts/voice_build_dataset.py --audio recordings\ [--zip]

Thin wrapper around ``political_shorts.voice.dataset.build`` (same code the
``political_shorts voice dataset`` subcommand runs). ffmpeg/ffprobe only, so it
works under the 3.14 interpreter. Nothing leaves your machine.
"""
from __future__ import annotations

import argparse
import shutil
import sys

import _bootstrap  # noqa: F401

from political_shorts.config import settings
from political_shorts.voice.dataset import build


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", default="recordings", help="folder with NN.wav recordings")
    ap.add_argument("--script", default="tools/voice/enrollment_ko.txt")
    ap.add_argument("--out", default="dataset")
    ap.add_argument("--sample-rate", type=int, default=22050)
    ap.add_argument("--min-minutes", type=float, default=10.0)
    ap.add_argument("--zip", action="store_true", help="also make dataset.zip for Colab")
    args = ap.parse_args()

    rc = build(args.audio, script=args.script, out=args.out,
               sample_rate=args.sample_rate, min_minutes=args.min_minutes, cfg=settings)
    if args.zip and rc == 0:
        z = shutil.make_archive(str(settings.root / "dataset"), "zip",
                                root_dir=str(settings.root / args.out))
        print(f"  packed -> {z}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
