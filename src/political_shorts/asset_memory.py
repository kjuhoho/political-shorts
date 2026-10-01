"""What pictures and clips recent videos already used, so the next one looks different.

The National Assembly building was the backdrop of four videos out of five, Seoul City Hall of three, and the
same road clip of three (live audit, 2026-09-30) — the pools are small and every story reached for the same
first entry. A video records what it used; the next ones put those at the back of the queue until they age out.

Nothing here ever BLOCKS a picture: with a small pool, "used last week" still beats "no picture at all".
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

FILE = "asset_history.json"
KEEP_DAYS = 14.0            # how long a use is remembered
AVOID_DAYS = 6.0            # how long it is pushed to the back of the queue


def _path(cfg: Any) -> Path:
    return Path(getattr(cfg, "data_dir", "data")) / FILE


def _load(cfg: Any) -> dict[str, float]:
    try:
        raw = json.loads(_path(cfg).read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def remember(cfg: Any, keys: Iterable[str], now: float | None = None) -> None:
    """Record that these pictures/clips were just used (a key is a Wikipedia title, a query or a source URL)."""
    now = time.time() if now is None else now
    hist = _load(cfg)
    for k in keys:
        k = str(k or "").strip()
        if k:
            hist[k] = now
    cutoff = now - KEEP_DAYS * 86400
    hist = {k: v for k, v in hist.items() if v >= cutoff}
    p = _path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def recent(cfg: Any, days: float = AVOID_DAYS, now: float | None = None) -> set[str]:
    """Keys used within `days` — the ones to try last."""
    now = time.time() if now is None else now
    return {k for k, v in _load(cfg).items() if v >= now - days * 86400}


def freshest_first(items: list[str], cfg: Any, days: float = AVOID_DAYS, now: float | None = None) -> list[str]:
    """Same items, unused ones first, each group keeping its original order."""
    seen = recent(cfg, days, now)
    return [x for x in items if x not in seen] + [x for x in items if x in seen]


def keys_of(assets: Iterable[Any]) -> list[str]:
    """The memory keys of collected assets (ImageAsset or the dicts a script carries)."""
    out: list[str] = []
    for a in assets or []:
        get = a.get if isinstance(a, dict) else (lambda k, d=None: getattr(a, k, d))
        for field in ("query", "source_url", "title"):
            v = get(field, "")
            if v:
                out.append(str(v))
                break
    return out
