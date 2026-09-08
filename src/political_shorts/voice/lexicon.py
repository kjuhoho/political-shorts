"""Pronunciation lexicon — fixed spoken forms for names, acronyms, dates.

Loaded once from ``config/voice_lexicon.yaml`` (+ an optional, git‑ignored
``config/voice_lexicon.local.yaml`` for channel‑private entries). Entries are
applied longest‑key‑first so ``국회 법제사법위원회`` wins over ``법사위``.

Anything that still looks like a bare Latin acronym after the dictionary pass is
spelled out letter by letter (``GTX`` → ``지티엑스``) so the neural voice never
guesses.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from ..logging_setup import get_logger

log = get_logger("voice.lexicon")

_ROOT = Path(__file__).resolve().parents[3]
_MAIN = _ROOT / "config" / "voice_lexicon.yaml"
_LOCAL = _ROOT / "config" / "voice_lexicon.local.yaml"

# English letter -> Korean name
_LETTER = {
    "A": "에이", "B": "비", "C": "시", "D": "디", "E": "이", "F": "에프",
    "G": "지", "H": "에이치", "I": "아이", "J": "제이", "K": "케이", "L": "엘",
    "M": "엠", "N": "엔", "O": "오", "P": "피", "Q": "큐", "R": "아르",
    "S": "에스", "T": "티", "U": "유", "V": "브이", "W": "더블유", "X": "엑스",
    "Y": "와이", "Z": "제트",
}
_ACRONYM_RE = re.compile(r"(?<![A-Za-z])[A-Z]{2,6}(?![A-Za-z])")


def _load_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # a broken lexicon must not break a render
        log.warning("lexicon %s unreadable: %s", path.name, exc)
        return {}
    out: dict[str, str] = {}
    for section, items in raw.items():
        if isinstance(items, dict):
            for k, v in items.items():
                if k and v:
                    out[str(k)] = str(v)
    return out


@lru_cache(maxsize=1)
def _entries() -> list[tuple[str, str]]:
    merged = _load_file(_MAIN)
    merged.update(_load_file(_LOCAL))          # local overrides win
    # longest key first so specific phrases beat their own abbreviations
    return sorted(merged.items(), key=lambda kv: len(kv[0]), reverse=True)


def spell_acronym(word: str) -> str:
    return "".join(_LETTER.get(c, c) for c in word)


def apply(text: str) -> str:
    """Substitute every known term, then spell out any leftover Latin acronym."""
    if not text:
        return text
    for key, val in _entries():
        if key in text:
            text = text.replace(key, val)
    text = _ACRONYM_RE.sub(lambda m: spell_acronym(m.group(0)), text)
    return text


def reload() -> None:
    """Drop the cache — call after editing the YAML in a long‑running process."""
    _entries.cache_clear()
