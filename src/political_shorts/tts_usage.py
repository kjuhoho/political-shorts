"""Bookkeeping for the TTS fallback chain (tts.py): what each metered engine has been sent this month, and which
engine is cooling down after a quota / auth error.

Why this exists: a free tier ends, and one of them does not stop at its end. Google Cloud TTS needs billing to be
switched on, and with billing on it keeps answering past the free characters and charges for them. "Use the next
engine when this one is used up" therefore cannot wait for the engine to say no. The chain counts the characters it
sends to each metered engine itself and stops at a cap below the published free allowance; an error the engine
does send (429 / quota text / rejected key) is honoured too, as a cooldown, so the next slot does not open with a
dozen failing requests.

The state is one small file, data/tts_usage.json, committed back by the workflow next to the other histories (a
runner is thrown away after every run). Edge, SAPI and piper are not metered and never touch the file.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logging_setup import get_logger

log = get_logger("tts.usage")

# About 80% of each published free allowance (checked 2026-11-20): Google Chirp 3 HD / Neural2 1M characters a
# month, Azure Speech F0 0.5M, ElevenLabs free 10k credits. The margin covers what we cannot see: SSML tags
# counted by the engine, a billing month that does not line up with ours, CJK counting rules.
DEFAULT_CAPS: dict[str, int] = {"gcloud": 800_000, "azure": 400_000, "elevenlabs": 8_000}
COOLDOWN_HOURS = 6                      # after a quota / auth refusal: skip the engine until about the next slot
_HARD_STATUS = {401, 402, 403, 429}
_QUOTA_WORDS = ("quota", "resource_exhausted", "exceeded", "out of call volume", "limit reached")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def month_key(now: datetime | None = None) -> str:
    return (now or _now()).astimezone(timezone.utc).strftime("%Y-%m")


def caps(cfg: Any) -> dict[str, int]:
    """Monthly character caps per metered engine. TTS_CAPS="gcloud:600000,azure:none" overrides the defaults
    ('none' / 'off' removes a cap, 0 keeps the engine switched off)."""
    out = dict(DEFAULT_CAPS)
    for part in str(getattr(cfg, "tts_caps", "") or "").split(","):
        name, _, val = part.partition(":")
        name, val = name.strip().lower(), val.strip().lower()
        if not name:
            continue
        if val in ("none", "off", "unlimited"):
            out.pop(name, None)
            continue
        try:
            out[name] = max(0, int(val))
        except ValueError:
            log.warning("TTS_CAPS: ignoring %r", part)
    return out


# ------------------------------------------------------------------ what one attempt did (set by the providers)
@dataclass
class Attempt:
    chars: int = 0                                  # characters the engine accepted (billed)
    statuses: list[int] = field(default_factory=list)
    quota_text: bool = False

    @property
    def hard(self) -> bool:
        """Quota, rate-limit or rejected key: the engine is not going to answer the next request either."""
        return self.quota_text or any(s in _HARD_STATUS for s in self.statuses)


_tl = threading.local()


def begin() -> None:
    _tl.attempt = Attempt()


def end() -> Attempt:
    att = getattr(_tl, "attempt", None) or Attempt()
    _tl.attempt = None
    return att


def note_chars(n: int) -> None:
    att = getattr(_tl, "attempt", None)
    if att is not None:
        att.chars += max(0, int(n))


def note_failure(exc: BaseException) -> None:
    att = getattr(_tl, "attempt", None)
    if att is None:
        return
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None) or getattr(exc, "status", None)
    if isinstance(code, int):
        att.statuses.append(code)
    try:
        body = (getattr(resp, "text", "") or "").lower()
    except Exception:
        body = ""
    if any(w in body for w in _QUOTA_WORDS):
        att.quota_text = True


def billable(text: str, *, keep_tags: bool = False) -> int:
    """Characters an engine bills for one request. Google counts SSML tags except <break>; for the others the
    tags are left out (an estimate - the caps keep a margin)."""
    s = re.sub(r"<break[^>]*/>", "", text or "") if keep_tags else re.sub(r"<[^>]+>", "", text or "")
    return len(s)


def scrub(exc: BaseException | str) -> str:
    """An exception text that is safe to log: Google's REST key rides in the URL (?key=...)."""
    return re.sub(r"(?i)(key=)[^&\s'\"]+", r"\1***", str(exc))


# ------------------------------------------------------------------------------------------------ the ledger
class Ledger:
    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        data = data or {}
        month = month_key()
        same_month = data.get("month") == month
        self.month = month
        self.chars: dict[str, int] = {k: int(v) for k, v in (data.get("chars") or {}).items()} if same_month else {}
        self.cooldown: dict[str, str] = dict(data.get("cooldown") or {})
        self._dirty = False

    @classmethod
    def load(cls, cfg: Any) -> "Ledger":
        path = Path(getattr(cfg, "data_dir", "data")) / "tts_usage.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            log.warning("tts_usage.json unreadable - starting from zero")
            data = {}
        return cls(path, data if isinstance(data, dict) else {})

    def used(self, name: str) -> int:
        return self.chars.get(name, 0)

    def add(self, name: str, n: int) -> None:
        if n > 0:
            self.chars[name] = self.used(name) + int(n)
            self._dirty = True

    def cooling_until(self, name: str) -> datetime | None:
        raw = self.cooldown.get(name)
        if not raw:
            return None
        try:
            until = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return until if until > _now() else None

    def start_cooldown(self, name: str) -> datetime:
        until = _now() + timedelta(hours=COOLDOWN_HOURS)
        self.cooldown[name] = until.isoformat(timespec="seconds")
        self._dirty = True
        return until

    def save(self) -> None:
        if not self._dirty:
            return
        live = {k: v for k, v in self.cooldown.items() if self.cooling_until(k)}
        payload = {"month": self.month, "chars": self.chars, "cooldown": live}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError as exc:
            log.warning("could not write %s: %s", self.path, exc)
