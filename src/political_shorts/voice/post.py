"""Layer 3 — make any voice track sit right in a Shorts mix.

One ffmpeg pass per segment: trim dead air at both ends, even out the level
with a gentle compressor, tame sibilance, then normalise loudness to the
platform target (‑14 LUFS by default). Runs on the ffmpeg the project already
requires — no Python audio deps, so it works under the 3.14 interpreter.

``process(path, cfg)`` returns the path of a cleaned 48 kHz mono PCM wav (a new
``*.post.wav`` next to the input, which is what the render step wants next); on
any ffmpeg trouble it returns the original path unchanged.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..config import Settings, settings
from ..logging_setup import get_logger

log = get_logger("voice.post")


def _ffmpeg(cfg: Settings) -> str | None:
    cand = cfg.ffmpeg_path or "ffmpeg"
    return cand if (shutil.which(cand) or Path(cand).exists()) else None


def _chain(lufs: float, deess: bool) -> str:
    trim = (
        "silenceremove=start_periods=1:start_silence=0.06:start_threshold=-45dB:"
        "detection=peak,areverse,"
        "silenceremove=start_periods=1:start_silence=0.06:start_threshold=-45dB:"
        "detection=peak,areverse"
    )
    comp = "acompressor=threshold=-18dB:ratio=3:attack=12:release=180:makeup=1.7"
    sib = "deesser=i=0.35" if deess else "equalizer=f=6800:t=q:w=1.8:g=-2.5"
    norm = f"loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11"
    return f"{trim},{comp},{sib},{norm}"


def _run(ffmpeg: str, src: Path, dst: Path, fil, cfg: Settings) -> bool:
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
        "-af", fil, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dst),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=90)
        return dst.exists() and dst.stat().st_size > 1024
    except subprocess.CalledProcessError as exc:
        log.debug("ffmpeg post failed (%s): %s", fil, (exc.stderr or "")[:200])
        return False
    except Exception as exc:
        log.debug("ffmpeg post error: %s", exc)
        return False


def process(path: Path | None, cfg: Settings | None = None) -> Path | None:
    cfg = cfg or settings
    if not path or not Path(path).exists():
        return path
    ffmpeg = _ffmpeg(cfg)
    if not ffmpeg:
        return path

    src = Path(path)
    lufs = float(getattr(cfg, "voice_lufs", -14.0))
    out = src.with_name(src.stem + ".post.wav")

    # full chain, then a deess-free variant, then just loudness — stop at the
    # first that produces a file.
    for filt in (_chain(lufs, True), _chain(lufs, False),
                 f"loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11"):
        if _run(ffmpeg, src, out, filt, cfg):
            return out
    log.info("voice post: left %s unprocessed", src.name)
    return src
