"""Which experiment arm each published video was built with, keyed by its YouTube id.

The channel's views are pinned at ~1,100, so the only way to learn what moves them is to try something and
compare. That needs a record of WHAT each video was: the opener (a person's quote, a question, a number ...).
Kept in its own small file - not a new column in topic_history - because that file is the CI's dedup state and
its schema is shared with the slot gate; this one can fail or be missing without affecting a run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FILE = "variant_history.json"


def _path(cfg: Any) -> Path:
    return Path(getattr(cfg, "data_dir", "data")) / FILE


def load(cfg: Any) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(_path(cfg).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def record(cfg: Any, remote_id: str, **tags: str) -> None:
    """Remember the arm of one published video. A missing id is ignored, never an error."""
    remote_id = str(remote_id or "").strip()
    if not remote_id:
        return
    hist = load(cfg)
    hist[remote_id] = {k: str(v) for k, v in tags.items() if v}
    p = _path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hist, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
