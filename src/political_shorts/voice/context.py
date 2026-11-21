"""A tiny hand‑off channel so the TTS orchestrator can give the ``piper`` /
``myvoice`` back‑ends the per‑segment :class:`~.normalize.Prepared` objects
(prosody hints) without changing the shared ``(texts, out_dir, cfg)`` provider
signature. Set by ``tts.synthesize_segments`` for the duration of one call.
"""
from __future__ import annotations

import contextlib
import threading

_local = threading.local()


def set_prepared(items) -> None:
    _local.prepared = list(items) if items else None


def get_prepared():
    return getattr(_local, "prepared", None)


def prepared_at(idx: int):
    """The :class:`~.normalize.Prepared` for segment *idx*, or ``None`` when the
    voice pipeline isn't active (so providers can keep their plain-text path)."""
    items = get_prepared()
    if items and 0 <= idx < len(items):
        return items[idx]
    return None


def prosody_at(idx: int):
    """The :class:`~.normalize.Prosody` for segment *idx*, or a neutral default
    when the voice pipeline isn't active. Safe for any provider to call."""
    from .normalize import Prosody

    item = prepared_at(idx)
    return item.prosody if item is not None else Prosody()


@contextlib.contextmanager
def prepared(items):
    set_prepared(items)
    try:
        yield
    finally:
        set_prepared(None)
