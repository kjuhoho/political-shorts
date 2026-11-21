"""Layer 3 (optional) — read the audio back and check it against the script.

If ``faster-whisper`` is importable (install it in a side 3.11 venv — it has no
3.14 wheels yet) and ``VOICE_QA=true``, every synth'd segment is transcribed and
compared, loosely, to the text we asked for. Mismatches are logged as warnings
so a human reviewer knows which lines to spot‑check — nothing is blocked.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config import Settings, settings
from ..logging_setup import get_logger

log = get_logger("voice.qa")


def _available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


_MODEL = None


def _model(cfg: Settings):
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        size = getattr(cfg, "voice_qa_model", "") or "small"
        _MODEL = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODEL


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", s or "")


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def check(pairs: list[tuple[Path | None, str]], cfg: Settings | None = None) -> list[dict]:
    """pairs = [(audio_path, expected_text), …]. Returns a list of flagged
    segments: [{"index", "score", "expected", "heard"}]."""
    cfg = cfg or settings
    if not getattr(cfg, "voice_qa", False) or not _available():
        return []
    thr = float(getattr(cfg, "voice_qa_threshold", 0.72))
    flags: list[dict] = []
    try:
        model = _model(cfg)
    except Exception as exc:
        log.warning("voice qa: model load failed: %s", exc)
        return []

    for i, (path, expected) in enumerate(pairs):
        if not path or not Path(path).exists() or not expected:
            continue
        try:
            segs, _ = model.transcribe(str(path), language="ko", vad_filter=True)
            heard = " ".join(s.text for s in segs).strip()
        except Exception as exc:
            log.debug("voice qa: seg %d transcribe failed: %s", i, exc)
            continue
        score = _ratio(expected, heard)
        if score < thr:
            flags.append({"index": i, "score": round(score, 2),
                          "expected": expected, "heard": heard})
            log.warning("voice qa: seg %d low match %.2f\n  want: %s\n  got : %s",
                        i, score, expected, heard)
    if not flags:
        log.info("voice qa: %d/%d segments ok", len(pairs), len(pairs))
    return flags
