"""Layer 2 — "my voice" back‑ends.

``piper``   : offline, free, CPU real‑time. Runs the standalone ``piper``
              binary against a voice model you trained once on your own
              recordings (see ``tools/voice/``). No Python audio deps, so it is
              unaffected by the 3.14 interpreter.
``myvoice`` : dispatcher. ``VOICE_MYVOICE_BACKEND=piper`` (default) → piper;
              ``=xtts_api`` → POST to a self‑hosted zero‑shot cloning endpoint
              (RunPod / Replicate style) for use *before* a piper model exists.

Both raise on "not configured" so ``tts.synthesize_segments`` falls back to the
next provider in its chain (edge → sapi → silent).
"""
from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import requests

from ..config import Settings
from ..logging_setup import get_logger
from . import context
from .normalize import Prosody

log = get_logger("voice.myvoice")
_TIMEOUT = 90


def _prosody_for(idx: int) -> Prosody:
    return context.prosody_at(idx)


# --------------------------------------------------------------------------- #
# piper
# --------------------------------------------------------------------------- #
def _piper_bin(cfg: Settings) -> str:
    cand = getattr(cfg, "piper_bin", "") or "piper"
    resolved = shutil.which(cand) or (cand if Path(cand).exists() else None)
    if not resolved:
        raise RuntimeError("piper binary not found (set PIPER_BIN)")
    return resolved


def _piper_model(cfg: Settings) -> Path:
    raw = getattr(cfg, "piper_model", "")
    if not raw:
        raise RuntimeError("PIPER_MODEL not set (path to your trained .onnx)")
    p = Path(raw)
    if not p.exists():
        raise RuntimeError(f"PIPER_MODEL missing: {p}")
    if not p.with_suffix(p.suffix + ".json").exists() and not p.with_suffix(".onnx.json").exists():
        log.warning("piper: no %s.json beside the model — piper may refuse it", p.name)
    return p


def synth_piper(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    binary = _piper_bin(cfg)
    model = _piper_model(cfg)
    base_len = float(getattr(cfg, "piper_length_scale", 1.0))
    noise = float(getattr(cfg, "piper_noise_scale", 0.667))
    noise_w = float(getattr(cfg, "piper_noise_w", 0.8))

    out: list[Path | None] = []
    for i, text in enumerate(texts):
        target = out_dir / f"seg_{i:02d}.wav"
        pr = _prosody_for(i)
        cmd = [
            binary, "-m", str(model), "-f", str(target),
            "--length_scale", f"{base_len * pr.length_scale:.3f}",
            "--sentence_silence", f"{pr.sentence_silence:.2f}",
            "--noise_scale", f"{noise:.3f}", "--noise_w", f"{noise_w:.3f}",
        ]
        try:
            subprocess.run(
                cmd, input=(text or " ").encode("utf-8"),
                capture_output=True, check=True, timeout=_TIMEOUT,
            )
            out.append(target if target.exists() and target.stat().st_size > 1024 else None)
        except Exception as exc:
            stderr = getattr(exc, "stderr", b"") or b""
            log.warning("piper seg %d failed: %s %s", i, exc, stderr[:200])
            out.append(None)
    if not any(out):
        raise RuntimeError("piper produced no audio")
    return out


# --------------------------------------------------------------------------- #
# xtts_api  — generic self-hosted zero-shot cloning endpoint
# --------------------------------------------------------------------------- #
def synth_xtts_api(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    url = getattr(cfg, "xtts_api_url", "")
    if not url:
        raise RuntimeError("XTTS_API_URL not set")
    ref = getattr(cfg, "xtts_speaker_wav_url", "") or getattr(cfg, "xtts_speaker_id", "")
    headers = {"content-type": "application/json"}
    token = getattr(cfg, "xtts_api_token", "")
    if token:
        headers["authorization"] = f"Bearer {token}"

    out: list[Path | None] = []
    for i, text in enumerate(texts):
        payload = {"text": text, "language": "ko"}
        if getattr(cfg, "xtts_speaker_wav_url", ""):
            payload["speaker_wav_url"] = cfg.xtts_speaker_wav_url
        elif getattr(cfg, "xtts_speaker_id", ""):
            payload["speaker_id"] = cfg.xtts_speaker_id
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            if ct.startswith("audio/"):
                data = r.content
            else:
                body = r.json()
                b64 = body.get("audio_base64") or body.get("audio") or ""
                data = base64.b64decode(b64) if b64 else b""
            target = out_dir / f"seg_{i:02d}.wav"
            if data and len(data) > 1024:
                target.write_bytes(data)
                out.append(target)
            else:
                out.append(None)
        except Exception as exc:
            log.warning("xtts_api seg %d failed: %s", i, exc)
            out.append(None)
    if not any(out):
        raise RuntimeError("xtts_api produced no audio")
    return out


def synth_myvoice(texts: list[str], out_dir: Path, cfg: Settings) -> list[Path | None]:
    backend = (getattr(cfg, "voice_myvoice_backend", "") or "piper").lower()
    if backend == "xtts_api":
        return synth_xtts_api(texts, out_dir, cfg)
    return synth_piper(texts, out_dir, cfg)
