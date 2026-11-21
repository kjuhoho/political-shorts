"""Build SSML for the engines that actually honour it (azure, gcloud).

Layer 1 already spelled out every number / name, so there's no ``<say-as>`` work
left — this module only adds *phrasing*: a real breath between sentences and
after contrast markers, a ``<prosody>`` wrapper for the per-role rate/pitch, and
``<emphasis>`` on text the writer put in quotes.

``edge`` (7.x) and ``piper`` do not take SSML; they use the plain
``Prepared.text`` plus their own rate/pitch knobs instead.
"""
from __future__ import annotations

import re
from xml.sax.saxutils import escape

from .normalize import Prepared, Prosody

# markers that a listener expects a tiny pause *after*
_CONTRAST = ("하지만", "그러나", "그런데", "반면", "반면에", "오히려", "다만", "결국", "정리하면")
_CONTRAST_RE = re.compile(r"(^|[\s,])(" + "|".join(map(re.escape, _CONTRAST)) + r")([\s,])")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_QUOTED = re.compile(r"([\"“”'『「][^\"“”'』」]{2,60}[\"“”'』」])")


def _pitch_st(p: Prosody) -> str:
    return f"{p.pitch:+.0f}st" if abs(p.pitch) >= 0.5 else "+0st"


def _inline(sentence: str) -> str:
    """Escape + mark quoted spans as emphasis + breathe after contrast words."""
    out, last = [], 0
    for m in _QUOTED.finditer(sentence):
        out.append(_contrast(escape(sentence[last:m.start()])))
        out.append(f'<emphasis level="moderate">{escape(m.group(1))}</emphasis>')
        last = m.end()
    out.append(_contrast(escape(sentence[last:])))
    return "".join(out)


def _contrast(text: str) -> str:
    return _CONTRAST_RE.sub(r'\1\2\3<break time="120ms"/>', text)


def body(prep: Prepared) -> str:
    """The inner SSML (no <speak>/<voice> wrapper) — <prosody> + breaks."""
    pr = prep.prosody
    gap = int(round(pr.sentence_silence * 1000))
    sentences = [s for s in _SENT_SPLIT.split(prep.text.strip()) if s]
    joined = f'<break time="{gap}ms"/>'.join(_inline(s) for s in sentences) or escape(prep.text)
    return f'<prosody rate="{pr.rate_pct}" pitch="{_pitch_st(pr)}">{joined}</prosody>'


def azure(prep: Prepared, voice: str, lang: str = "ko-KR") -> str:
    return (
        f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{lang}'>"
        f"<voice name='{voice}'>{body(prep)}</voice></speak>"
    )


def gcloud(prep: Prepared, lang: str = "ko-KR") -> str:
    return f"<speak>{body(prep)}</speak>"
