"""Step 7 (optional) — narration synthesis.

Engines are chosen by ``TTS_PROVIDERS`` (a list, tried in order: ``gcloud,azure,edge``) or, when that is blank,
by the single ``TTS_PROVIDER`` (edge | elevenlabs | azure | gcloud | openai | piper | sapi). ``edge`` (free, no
key) and Windows ``sapi`` are always appended as the last stops, and if nothing makes a sound the video still builds
with caption-only cards.

The hand-over between engines is automatic and keeps one voice per video:
  * an engine must make EVERY segment of a video; otherwise its audio is thrown away and the next engine
    narrates the whole video (a quota that ends halfway must not leave a hole or a second voice),
  * metered engines (tts_usage.DEFAULT_CAPS: gcloud / azure / elevenlabs) are counted per month and skipped
    once a video would pass their cap - Google keeps going and bills past its free tier, so it cannot be left to
    stop on its own,
  * an engine that answers with a quota / rate-limit / key refusal cools down for a few hours (tts_usage.py).

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

from . import tts_usage
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
# engine chain
# --------------------------------------------------------------------------- #
def _chain(cfg: Settings) -> list[str]:
    listed = [n.strip().lower() for n in (getattr(cfg, "tts_providers", "") or "").split(",") if n.strip()]
    names: list[str] = []
    for name in (*(listed or [cfg.tts_provider]), "edge", "sapi"):
        if name and name not in names:
            names.append(name)
    return names


def _is_complete(paths: list[Path | None], speak: list[str]) -> bool:
    """Every segment that has words has audio (and there is at least one)."""
    need = [p for p, t in zip(paths, speak) if (t or "").strip()]
    return bool(need) and all(need)


def _skip_reason(name: str, speak: list[str], caps: dict[str, int], ledger: tts_usage.Ledger, ssml: bool) -> str:
    """Why a metered engine must not be tried for this video ('' = go ahead). Unmetered engines never skip."""
    cap = caps.get(name)
    if cap is None:
        return ""
    until = ledger.cooling_until(name)
    if until:
        return f"cooling down until {until:%m-%d %H:%M} UTC after a quota/key refusal"
    need = sum(len(t) + (80 if ssml else 0) for t in speak)            # SSML wrapping counts on Google
    used = ledger.used(name)
    if used + need > cap:
        return f"this month's cap of {cap:,} characters would be passed ({used:,} used, about {need:,} needed)"
    return ""


def _settle(name: str, att: tts_usage.Attempt, caps: dict[str, int], ledger: tts_usage.Ledger) -> None:
    """Book what a metered engine accepted this attempt, and start a cooldown if it refused us."""
    if name not in caps:
        return
    ledger.add(name, att.chars)
    if att.hard:
        until = ledger.start_cooldown(name)
        log.warning("tts %s refused (quota / rate limit / key) - skipping it until %s UTC",
                    name, until.strftime("%m-%d %H:%M"))
    ledger.save()


def _run_chain(speak: list[str], out_dir: Path, cfg: Settings, ssml: bool) -> tuple[list[Path | None], str]:
    ledger = tts_usage.Ledger.load(cfg)
    caps = tts_usage.caps(cfg)
    best: tuple[int, str, list[Path | None]] | None = None
    for name in _chain(cfg):
        if name == "sapi" and best is not None:
            break                           # robotic last resort: only when no other engine made a sound
        why = _skip_reason(name, speak, caps, ledger, ssml)
        if why:
            log.info("tts %s skipped: %s", name, why)
            continue
        tts_usage.begin()
        try:
            if name == "sapi":
                got = _synth_sapi(speak, out_dir, cfg)
            else:
                fn = _PROVIDERS.get(name)
                if fn is None:
                    log.warning("unknown TTS provider %r, skipping", name)
                    tts_usage.end()
                    continue
                got = fn(speak, out_dir, cfg)
        except Exception as exc:
            log.warning("tts provider %r unavailable: %s", name, tts_usage.scrub(exc))
            tts_usage.end()
            continue
        _settle(name, tts_usage.end(), caps, ledger)
        if _is_complete(got, speak):
            return got, name
        made = sum(1 for p in got if p)
        if made:
            log.warning("tts %s made %d/%d segments - not using a half-narrated video, trying the next engine",
                        name, made, len(speak))
            if best is None or made > best[0]:
                best = (made, name, got)
    if best is not None:
        log.warning("no engine made every segment - using %s's %d/%d (the rest stay caption-only)",
                    best[1], best[0], len(speak))
        return best[2], best[1]
    return [None] * len(speak), "none"


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def synthesize_segments(
    texts: list[str], out_dir: Path, cfg: Settings | None = None,
    *, roles: list[str | None] | None = None,
) -> list[Narration]:
    cfg = cfg or settings
    out_dir.mkdir(parents=True, exist_ok=True)

    if not cfg.enable_tts:
        log.info("tts disabled -> silent cards")
        return [Narration(i, t, None, 0.0) for i, t in enumerate(texts)]

    # --- Layer 1: normalise numbers / names / acronyms, attach role prosody ---
    prepared = None
    speak = list(texts)
    if getattr(cfg, "voice_pipeline", False):
        try:
            from .voice import prepare as _vprepare

            prepared = [
                _vprepare(t, roles[i] if roles and i < len(roles) else None)
                for i, t in enumerate(texts)
            ]
            speak = [p.text for p in prepared]
        except Exception as exc:
            log.warning("voice layer-1 skipped: %s", exc)
            prepared, speak = None, list(texts)

    from .voice import context as _vctx

    with _vctx.prepared(prepared):
        paths, engine_used = _run_chain(speak, out_dir, cfg, ssml=prepared is not None)

    if not any(paths):
        engine_used = "silent"
        log.warning("no working TTS provider -> silent caption cards")

    # --- Layer 3: loudness / compression / de-ess on the engine's output ------
    if prepared is not None and any(paths):
        try:
            from .voice import post as _vpost

            paths = [_vpost.process(p, cfg) if p else None for p in paths]
        except Exception as exc:
            log.warning("voice layer-3 skipped: %s", exc)

    narrations: list[Narration] = []
    for i, p in enumerate(paths):
        dur = _wav_duration(p) if p else 0.0
        narrations.append(Narration(i, texts[i] if i < len(texts) else "", p, dur))

    # optional Whisper read-back check — logs mismatches, never blocks
    if prepared is not None and getattr(cfg, "voice_qa", False):
        try:
            from .voice import qa as _vqa

            _vqa.check([(n.wav_path, speak[i]) for i, n in enumerate(narrations)], cfg)
        except Exception as exc:
            log.debug("voice qa skipped: %s", exc)

    log.info(
        "tts done engine=%s ok=%d/%d%s",
        engine_used,
        sum(1 for n in narrations if n.wav_path),
        len(narrations),
        " +voice" if prepared is not None else "",
    )
    return narrations


def estimate_caption_seconds(text: str, cfg: Settings) -> float:
    """Fallback duration when there is no audio: ~7.5 KR chars/sec, clamped."""
    chars = max(1, len(text))
    return max(1.6, min(6.5, 0.7 + chars / 7.5))
