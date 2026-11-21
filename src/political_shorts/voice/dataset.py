"""Turn a folder of enrollment recordings into a Piper training dataset.

For every ``NN`` line in the read script it expects ``<audio>/NN.wav`` (``01.wav``,
``02.wav`` …). Each file is checked (clipping, level, length), resampled to
22 050 Hz mono 16-bit with the ends tidied, and written to ``<out>/wav/NN.wav``;
``<out>/metadata.csv`` gets ``NN|<sentence>`` lines in the LJSpeech layout Piper's
preprocessor reads.

ffmpeg/ffprobe only — no Python audio packages, so it runs under the 3.14
interpreter. Nothing leaves the machine.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from ..config import Settings, settings

_NUM = re.compile(r"^(\d{1,3})[\s.\-_]+(.+)$")


def _tool(cfg: Settings, name: str) -> str | None:
    base = cfg.ffmpeg_path or "ffmpeg"
    cand = base if name == "ffmpeg" else base.replace("ffmpeg", "ffprobe")
    return shutil.which(cand) or (cand if Path(cand).exists() else None)


def _probe_seconds(ffprobe: str, path: Path) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _max_volume(ffmpeg: str, path: Path) -> float:
    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", out.stderr)
    return float(m.group(1)) if m else 0.0


def _read_script(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = _NUM.match(ln)
        if m:
            rows[f"{int(m.group(1)):02d}"] = m.group(2).strip()
    return rows


def build(
    audio: str | Path,
    *,
    script: str | Path = "tools/voice/enrollment_ko.txt",
    out: str | Path = "dataset",
    sample_rate: int = 22050,
    min_minutes: float = 10.0,
    cfg: Settings | None = None,
) -> int:
    """Returns 0 when the dataset is training-ready, 1 otherwise (prints a report)."""
    cfg = cfg or settings
    ffmpeg, ffprobe = _tool(cfg, "ffmpeg"), _tool(cfg, "ffprobe")
    if not (ffmpeg and ffprobe):
        print("ffmpeg/ffprobe not found (set FFMPEG_PATH in .env)")
        return 1

    spath = Path(script)
    if not spath.is_absolute():
        spath = cfg.root / spath
    lines = _read_script(spath)
    if not lines:
        print(f"no numbered lines in {spath}")
        return 1

    audio_dir = Path(audio)
    if not audio_dir.is_absolute():
        audio_dir = cfg.root / audio_dir
    out_dir = Path(out)
    if not out_dir.is_absolute():
        out_dir = cfg.root / out_dir
    wav_dir = out_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str]] = []
    total = 0.0
    missing, clipped, tooquiet = [], [], []

    print(f"{'id':>4}  {'dur':>6}  {'peak':>7}  text")
    for sid, text in sorted(lines.items()):
        src = next((audio_dir / f"{sid}{ext}" for ext in (".wav", ".flac", ".m4a", ".mp3")
                    if (audio_dir / f"{sid}{ext}").exists()), None)
        if not src:
            missing.append(sid)
            print(f"{sid:>4}  {'--':>6}  {'--':>7}  (missing)")
            continue
        peak = _max_volume(ffmpeg, src)
        flag = ""
        if peak > -1.0:
            clipped.append(sid)
            flag = "  <clip>"
        if peak < -6.0:
            tooquiet.append(sid)
            flag += "  <low>"
        dst = wav_dir / f"{sid}.wav"
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
             "-af",
             "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-40dB:"
             "detection=peak,areverse,"
             "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-40dB:"
             "detection=peak,areverse,apad=pad_dur=0.15",
             "-ar", str(sample_rate), "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
            check=True, capture_output=True,
        )
        clean = _probe_seconds(ffprobe, dst)
        total += clean
        rows.append((sid, text))
        print(f"{sid:>4}  {clean:6.2f}  {peak:7.1f}  {text[:48]}{flag}")

    (out_dir / "metadata.csv").write_text(
        "\n".join(f"{sid}|{text}" for sid, text in rows) + "\n", encoding="utf-8"
    )

    mins = total / 60.0
    print(f"\n{len(rows)} clips, {mins:.1f} min -> {out_dir}  (metadata.csv + wav/)")
    if missing:
        print(f"  missing recordings: {', '.join(missing)}")
    if clipped:
        print(f"  CLIPPING (re-record a touch quieter): {', '.join(clipped)}")
    if tooquiet:
        print(f"  very quiet (move closer / raise gain): {', '.join(tooquiet)}")
    if not rows or mins < min_minutes:
        print(f"  ! only {mins:.1f} min — aim for >= {min_minutes:.0f} min before training")
        return 1
    print("  looks good — pack it (--zip) and upload to the Colab notebook.")
    return 0
