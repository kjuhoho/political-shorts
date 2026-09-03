"""Step 7 (optional) — narration synthesis.

Provider is chosen by ``TTS_PROVIDER`` (edge | elevenlabs | azure | gcloud |
openai | sapi). The orchestrator tries that provider, then ``edge`` (free, no
key), then Windows ``sapi``, then gives up and returns silent segments so the
video still builds with caption-only cards.

``synthesize_segments`` writes one audio file per segment (wav or mp3 — ffmpeg
takes both) and returns their paths + measured durations so the video timeline
matches the narration exactly.
"""
from __future__ import annotations

import contextlib
import json as _json
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, settings
from .logging_setup import get_logger
from .tts_providers import REGISTRY as _PROVIDERS

log = get_logger("tts")


@dataclass
class Narration:
    index: int
    text: str
    wav_path: Path | None      # kept name for back-compat; may be an .mp3
    duration_s: float


def _wav_duration(path: Path) -> float:
    """Duration in seconds. Fast path for PCM wav, ffprobe for everything else."""
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        pass
    ffprobe = (settings.ffmpeg_path or "ffmpeg").replace("ffmpeg", "ffprobe")
    if not (shutil.which(ffprobe) or Path(ffprobe).exists()):
        return 0.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=20,
        )
        return float(_json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# engine: pyttsx3
# --------------------------------------------------------------------------- #
def _pyttsx3_available() -> bool:
    try:
        import pyttsx3  # noqa: F401

        return True
    except Exception:
        return False


def _synth_pyttsx3(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    """One fresh engine per segment.

    Reusing a single engine across multiple ``runAndWait()`` calls is a known
    source of hangs / "run loop already started" errors in pyttsx3, so we pay
    the (small) re-init cost instead.
    """
    import pyttsx3

    def _voice_id(engine) -> str | None:
        if not cfg.tts_voice:
            return None
        for v in engine.getProperty("voices"):
            hay = f"{v.name or ''} {v.id or ''}".lower()
            if cfg.tts_voice.lower() in hay:
                return v.id
        return None

    paths: list[Path | None] = []
    for i, text in enumerate(texts):
        target = out_dir / f"seg_{i:02d}.wav"
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", cfg.tts_rate)
            vid = _voice_id(engine)
            if vid:
                engine.setProperty("voice", vid)
            engine.save_to_file(text, str(target))
            engine.runAndWait()
            with contextlib.suppress(Exception):
                engine.stop()
            del engine
            paths.append(target if target.exists() and target.stat().st_size > 128 else None)
        except Exception as exc:
            log.warning("pyttsx3 seg %d failed: %s", i, exc)
            paths.append(None)
    return paths


# --------------------------------------------------------------------------- #
# engine: PowerShell System.Speech
# --------------------------------------------------------------------------- #
# One PowerShell process synthesises every segment: `Add-Type` / voice
# selection cold-start is paid once, then it loops seg_NN.txt -> seg_NN.wav.
_PS_BATCH = r"""
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = {rate}
try {{ $s.SelectVoice("{voice}") }} catch {{}}
for ($i = 0; $i -lt {count}; $i++) {{
    $n = '{{0:D2}}' -f $i
    $txtPath = Join-Path '{dir}' ("seg_" + $n + ".txt")
    $wavPath = Join-Path '{dir}' ("seg_" + $n + ".wav")
    try {{
        $txt = [System.IO.File]::ReadAllText($txtPath, [System.Text.Encoding]::UTF8)
        $s.SetOutputToWaveFile($wavPath)
        $s.Speak($txt)
    }} catch {{
        Write-Output ("ERR " + $i + " " + $_.Exception.Message)
    }}
}}
$s.Dispose()
"""


def _synth_powershell(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return [None] * len(texts)
    # SAPI rate is roughly -10..10; map from a words-per-minute-ish setting.
    rate = max(-8, min(8, round((cfg.tts_rate - 175) / 12)))
    voice = cfg.tts_voice or "Microsoft Heami Desktop"

    for i, text in enumerate(texts):
        (out_dir / f"seg_{i:02d}.txt").write_text(text, encoding="utf-8")
        # start clean so a stale wav is never mistaken for a fresh render
        stale = out_dir / f"seg_{i:02d}.wav"
        if stale.exists():
            stale.unlink()

    script = _PS_BATCH.format(
        rate=rate,
        voice=voice,
        count=len(texts),
        dir=str(out_dir).replace("'", "''"),
    )
    try:
        proc = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30 + 20 * len(texts),
        )
        if proc.stdout.strip():
            log.warning("powershell tts notes: %s", proc.stdout.strip()[:400])
    except Exception as exc:
        log.warning("powershell tts batch failed: %s", exc)
        return [None] * len(texts)

    out: list[Path | None] = []
    for i in range(len(texts)):
        wav = out_dir / f"seg_{i:02d}.wav"
        out.append(wav if wav.exists() and wav.stat().st_size > 128 else None)
    return out


def _synth_sapi(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    """Windows-native offline TTS: batched PowerShell first, pyttsx3 as backup."""
    paths = _synth_powershell(texts, out_dir, cfg)
    if not any(paths) and _pyttsx3_available():
        paths = _synth_pyttsx3(texts, out_dir, cfg)
    return paths


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def synthesize_segments(
    texts: list[str], out_dir: Path, cfg: Settings | None = None
) -> list[Narration]:
    cfg = cfg or settings
    out_dir.mkdir(parents=True, exist_ok=True)

    if not cfg.enable_tts:
        log.info("tts disabled -> silent cards")
        return [Narration(i, t, None, 0.0) for i, t in enumerate(texts)]

    engine_used = "none"
    paths: list[Path | None] = [None] * len(texts)

    # Fallback chain: configured provider -> edge (free) -> Windows SAPI -> silent.
    chain: list[str] = []
    for name in (cfg.tts_provider, "edge", "sapi"):
        if name and name not in chain:
            chain.append(name)

    for name in chain:
        if any(paths):
            break
        try:
            if name == "sapi":
                paths = _synth_sapi(texts, out_dir, cfg)
            else:
                fn = _PROVIDERS.get(name)
                if fn is None:
                    log.warning("unknown TTS_PROVIDER %r, skipping", name)
                    continue
                paths = fn(texts, out_dir, cfg)
            if any(paths):
                engine_used = name
        except Exception as exc:
            log.warning("tts provider %r unavailable: %s", name, exc)

    if not any(paths):
        engine_used = "silent"
        log.warning("no working TTS provider -> silent caption cards")

    narrations: list[Narration] = []
    for i, (text, p) in enumerate(zip(texts, paths)):
        dur = _wav_duration(p) if p else 0.0
        narrations.append(Narration(i, text, p, dur))
    log.info(
        "tts done engine=%s ok=%d/%d",
        engine_used,
        sum(1 for n in narrations if n.wav_path),
        len(narrations),
    )
    return narrations


def estimate_caption_seconds(text: str, cfg: Settings) -> float:
    """Fallback duration when there is no audio: ~7.5 KR chars/sec, clamped."""
    chars = max(1, len(text))
    return max(1.6, min(6.5, 0.7 + chars / 7.5))
