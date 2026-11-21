"""Layer 1 — turn a narration line into something a voice can *say* naturally.

``prepare(text, role)`` runs, in order:

1. ``textutil.clean_text``      — unescape, de‑HTML, 한자 약물 → 한글
2. :mod:`.lexicon`              — fixed readings for names / acronyms / dates
3. :mod:`.numbers`              — 27.3% → "이십칠 점 삼 퍼센트", 1조 2000억 …
4. breathing punctuation        — …/— → ", ", collapse spaces, close the sentence

It also attaches a light per‑role delivery hint (:class:`Prosody`) — energetic
hook, calm explainer, measured fact‑check — that the ``piper`` back‑end applies
directly and SSML‑capable back‑ends can map to ``<prosody>``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..textutil import clean_text
from . import lexicon, numbers

# role -> (length_scale multiplier, pitch semitones, silence after each sentence)
# length_scale > 1 == slower.  Deltas are deliberately small; big swings read as
# a caricature, not a person.
_PROSODY: dict[str, tuple[float, float, float]] = {
    "hook":      (0.96, +1.0, 0.28),
    "summary":   (0.99,  0.0, 0.34),
    "what":      (1.00,  0.0, 0.40),
    "reaction":  (1.00, -1.0, 0.38),
    "factcheck": (1.05,  0.0, 0.46),
    "outro":     (1.02,  0.0, 0.40),
}
_DEFAULT = (1.00, 0.0, 0.38)

_PUNCT_END = ".!?"


@dataclass(frozen=True)
class Prosody:
    length_scale: float = 1.0     # piper: >1 slower
    pitch: float = 0.0            # semitones
    sentence_silence: float = 0.38
    rate_pct: str = "+0%"         # for edge / azure SSML

    @classmethod
    def for_role(cls, role: str | None) -> "Prosody":
        ls, pit, sil = _PROSODY.get(role or "", _DEFAULT)
        pct = round((1.0 / ls - 1.0) * 100)
        return cls(ls, pit, sil, f"+{pct}%" if pct >= 0 else f"{pct}%")


@dataclass(frozen=True)
class Prepared:
    text: str
    prosody: Prosody
    role: str | None = None


def _breathe(t: str) -> str:
    """Ellipses and dashes become a comma‑pause; the line ends on real
    punctuation so the last word isn't up‑talked or clipped."""
    t = t.replace("…", ", ").replace("...", ", ").replace("..", ", ")
    for d in ("—", " – ", " - ", " · ", "·"):
        t = t.replace(d, ", ")
    t = re.sub(r"\s*,\s*", ", ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,")
    t = re.sub(r"\s+([.!?,])", r"\1", t)
    if t and t[-1] not in _PUNCT_END:
        t += "."
    return t


def normalize_text(text: str) -> str:
    """The text transform on its own (no prosody) — handy for tests / captions."""
    t = clean_text(text or "")
    if not t:
        return ""
    t = lexicon.apply(t)
    t = numbers.read_numbers(t)
    return _breathe(t)


def prepare(text: str, role: str | None = None) -> Prepared:
    return Prepared(normalize_text(text), Prosody.for_role(role), role)
