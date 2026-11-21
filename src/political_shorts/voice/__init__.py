"""Voice pipeline — three layers wrapped around whatever TTS engine is chosen.

    Layer 1  normalize   숫자·고유명사·약어 → 소리, 역할별 호흡/속도 힌트
    Layer 2  engine      piper(내 목소리, 오프라인) / myvoice(클라우드 클론) …
    Layer 3  post + qa    라우드니스·컴프·디에서 정리, Whisper 재전사 대조

``tts.synthesize_segments`` calls :func:`normalize.prepare` before the engine
and :func:`post.process` after, when ``VOICE_PIPELINE=true``. Engines that need
the per‑segment prosody read it from :mod:`.context`.
"""
from . import context, dataset, lexicon, normalize, numbers, post, qa, ssml  # noqa: F401
from .normalize import Prepared, Prosody, normalize_text, prepare  # noqa: F401

__all__ = [
    "context", "dataset", "lexicon", "normalize", "numbers", "post", "qa", "ssml",
    "Prepared", "Prosody", "normalize_text", "prepare",
]
