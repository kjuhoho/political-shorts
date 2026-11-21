"""Pluggable text-to-speech back-ends.

Each provider function has the same shape::

    synth_<name>(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]

and returns one audio file per input text (mp3 or wav; ffmpeg accepts both) or
``None`` for a segment that failed. Raising means "this provider is unusable" so
the orchestrator in ``tts.py`` can fall back to the next one.

Only ``edge`` needs an extra dependency (``edge-tts``); the rest are plain REST
calls via ``requests``. ``sapi`` lives in ``tts.py`` itself (Windows-only).
"""
from __future__ import annotations

import asyncio
import base64
import html as _html
import re
import shutil
from pathlib import Path

import requests

from . import tts_usage as _usage
from .config import Settings
from .logging_setup import get_logger

log = get_logger("tts.provider")

_TIMEOUT = 60

DEFAULT_VOICES = {
    "edge": "ko-KR-SunHiNeural",       # also: ko-KR-InJoonNeural, ko-KR-HyunsuNeural
    "azure": "ko-KR-SunHiNeural",
    "gcloud": "ko-KR-Neural2-A",
    "openai": "alloy",
}


_GCLOUD_VOICE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-(Neural2|Wavenet|Standard|Chirp|Studio|Polyglot|News|Journey)", re.I)


def _voice(cfg: Settings, provider: str) -> str:
    """TTS_VOICE is one string but a chain has several engines, and voice names are engine-specific
    (ko-KR-SunHiNeural is not a Google voice). An engine ignores a TTS_VOICE that belongs to another one."""
    v = (cfg.tts_voice or "").strip()
    if v and bool(_GCLOUD_VOICE.match(v)) == (provider == "gcloud"):
        return v
    return DEFAULT_VOICES[provider]


def _rate_percent(cfg: Settings) -> str:
    """Map the SAPI-style wpm number to an edge/azure percentage string."""
    pct = int(round((cfg.tts_rate - 175) / 175 * 100))
    pct = max(-45, min(60, pct))
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def _seg_rate_percent(cfg: Settings, idx: int) -> str:
    """Base rate compounded with segment *idx*'s per-role prosody hint (a no-op
    when VOICE_PIPELINE is off — ``prosody_at`` then returns a neutral default)."""
    from .voice import context as _vctx

    base = int(_rate_percent(cfg).rstrip("%"))
    role = int(_vctx.prosody_at(idx).rate_pct.rstrip("%"))
    pct = round(((1 + base / 100.0) * (1 + role / 100.0) - 1) * 100)
    pct = max(-45, min(70, pct))
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def _seg_pitch_semitones(idx: int) -> float:
    from .voice import context as _vctx

    return _vctx.prosody_at(idx).pitch


def _natural(text: str) -> str:
    """Small tweaks that make the neural voice phrase a line more like speech:
    breathing punctuation instead of dashes/ellipses, and a definite sentence
    end so the last word isn't clipped or up-talked."""
    t = " ".join((text or "").split())
    for a, b in (("…", ". "), ("...", ". "), ("—", ", "), (" - ", ", "), (" · ", ", ")):
        t = t.replace(a, b)
    t = " ".join(t.split()).rstrip(" ,")
    if t and t[-1] not in ".!?":
        t += "."
    return t


def _save(data: bytes, path: Path) -> Path | None:
    if not data or len(data) < 256:
        return None
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------- #
# edge-tts  (free, no key, Microsoft neural voices)
# --------------------------------------------------------------------------- #
def synth_edge(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    import edge_tts  # raises -> orchestrator falls back

    voice = _voice(cfg, "edge")

    async def _one(idx: int, text: str) -> Path | None:
        target = out_dir / f"seg_{idx:02d}.mp3"
        hz = round(_seg_pitch_semitones(idx) * 12)
        pitch = f"+{hz}Hz" if hz >= 0 else f"{hz}Hz"
        try:
            comm = edge_tts.Communicate(
                text, voice=voice, rate=_seg_rate_percent(cfg, idx), pitch=pitch,
            )
            await comm.save(str(target))
            return target if target.exists() and target.stat().st_size > 256 else None
        except Exception as exc:
            log.warning("edge seg %d failed: %s", idx, exc)
            _usage.note_failure(exc)
            return None

    async def _run() -> list[Path | None]:
        return await asyncio.gather(*(_one(i, t) for i, t in enumerate(texts)))

    return asyncio.run(_run())


# --------------------------------------------------------------------------- #
# ElevenLabs
# --------------------------------------------------------------------------- #
def _elevenlabs_voice(cfg: Settings) -> str:
    if cfg.elevenlabs_voice_id:
        return cfg.elevenlabs_voice_id
    r = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": cfg.elevenlabs_api_key},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    voices = r.json().get("voices", [])
    if not voices:
        raise RuntimeError("no ElevenLabs voices on this account")
    log.info("elevenlabs: using first available voice %s", voices[0].get("name"))
    return voices[0]["voice_id"]


def synth_elevenlabs(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    if not cfg.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    voice_id = _elevenlabs_voice(cfg)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": cfg.elevenlabs_api_key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    out: list[Path | None] = []
    for i, text in enumerate(texts):
        try:
            r = requests.post(
                url,
                headers=headers,
                json={
                    "text": text,
                    "model_id": cfg.elevenlabs_model,
                    "voice_settings": {"stability": 0.45, "similarity_boost": 0.8},
                },
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            _usage.note_chars(len(text))
            out.append(_save(r.content, out_dir / f"seg_{i:02d}.mp3"))
        except Exception as exc:
            log.warning("elevenlabs seg %d failed: %s", i, exc)
            _usage.note_failure(exc)
            out.append(None)
    return out


# --------------------------------------------------------------------------- #
# Azure Cognitive Services Speech
# --------------------------------------------------------------------------- #
def synth_azure(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    if not (cfg.azure_speech_key and cfg.azure_speech_region):
        raise RuntimeError("AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set")
    endpoint = (
        f"https://{cfg.azure_speech_region}.tts.speech.microsoft.com/"
        "cognitiveservices/v1"
    )
    voice = _voice(cfg, "azure")
    headers = {
        "Ocp-Apim-Subscription-Key": cfg.azure_speech_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "political-shorts",
    }
    out: list[Path | None] = []
    from .voice import context as _vctx, ssml as _vssml

    for i, text in enumerate(texts):
        prep = _vctx.prepared_at(i)
        if prep is not None:
            ssml = _vssml.azure(prep, voice)
        else:
            st = _seg_pitch_semitones(i)
            pitch = f"{st:+.0f}st" if abs(st) >= 0.5 else "+0st"
            ssml = (
                "<speak version='1.0' xml:lang='ko-KR'>"
                f"<voice name='{voice}'><prosody rate='{_seg_rate_percent(cfg, i)}' pitch='{pitch}'>"
                f"{_html.escape(text)}"
                "</prosody></voice></speak>"
            )
        try:
            r = requests.post(endpoint, headers=headers, data=ssml.encode("utf-8"), timeout=_TIMEOUT)
            r.raise_for_status()
            _usage.note_chars(_usage.billable(ssml))
            out.append(_save(r.content, out_dir / f"seg_{i:02d}.mp3"))
        except Exception as exc:
            log.warning("azure seg %d failed: %s", i, exc)
            _usage.note_failure(exc)
            out.append(None)
    return out


# --------------------------------------------------------------------------- #
# Google Cloud Text-to-Speech (API key)
# --------------------------------------------------------------------------- #
def synth_gcloud(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    if not cfg.google_tts_api_key:
        raise RuntimeError("GOOGLE_TTS_API_KEY not set")
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={cfg.google_tts_api_key}"
    voice = _voice(cfg, "gcloud")
    from .voice import context as _vctx, ssml as _vssml

    out: list[Path | None] = []
    for i, text in enumerate(texts):
        prep = _vctx.prepared_at(i)
        speak_input = {"ssml": _vssml.gcloud(prep)} if prep is not None else {"text": text}
        try:
            r = requests.post(
                url,
                json={
                    "input": speak_input,
                    "voice": {"languageCode": "ko-KR", "name": voice},
                    "audioConfig": {"audioEncoding": "MP3", "speakingRate": max(0.5, min(1.6, cfg.tts_rate / 175))},
                },
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            _usage.note_chars(_usage.billable(speak_input.get("ssml") or speak_input["text"],
                                              keep_tags="ssml" in speak_input))
            audio = base64.b64decode(r.json()["audioContent"])
            out.append(_save(audio, out_dir / f"seg_{i:02d}.mp3"))
        except Exception as exc:
            # the REST key rides in the URL ("?key=..."): never let it into a log file
            log.warning("gcloud seg %d failed: %s", i, _usage.scrub(exc))
            _usage.note_failure(exc)
            out.append(None)
    return out


# --------------------------------------------------------------------------- #
# OpenAI TTS
# --------------------------------------------------------------------------- #
def synth_openai(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    if not cfg.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    url = "https://api.openai.com/v1/audio/speech"
    headers = {"Authorization": f"Bearer {cfg.openai_api_key}", "Content-Type": "application/json"}
    voice = cfg.tts_voice or cfg.openai_tts_voice or DEFAULT_VOICES["openai"]
    out: list[Path | None] = []
    for i, text in enumerate(texts):
        try:
            r = requests.post(
                url,
                headers=headers,
                json={
                    "model": cfg.openai_tts_model,
                    "voice": voice,
                    "input": text,
                    "response_format": "mp3",
                },
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            out.append(_save(r.content, out_dir / f"seg_{i:02d}.mp3"))
        except Exception as exc:
            log.warning("openai seg %d failed: %s", i, exc)
            _usage.note_failure(exc)
            out.append(None)
    return out


from .voice.providers_myvoice import synth_myvoice, synth_piper  # noqa: E402

REGISTRY = {
    "edge": synth_edge,
    "elevenlabs": synth_elevenlabs,
    "azure": synth_azure,
    "gcloud": synth_gcloud,
    "openai": synth_openai,
    "piper": synth_piper,        # offline "my voice" (trained .onnx)
    "myvoice": synth_myvoice,    # piper, or a cloud zero-shot clone before training
}


def available(name: str) -> bool:
    if name == "edge":
        return shutil.which("python") is not None and _edge_importable()
    return name in REGISTRY


def _edge_importable() -> bool:
    try:
        import edge_tts  # noqa: F401

        return True
    except Exception:
        return False
