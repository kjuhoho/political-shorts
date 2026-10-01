"""Which music bed this video gets, and the credit line that must go with it.

Five videos audited on 2026-09-30 all carried the same track (Kevin MacLeod, "Investigations"), on top of the
same stock skyline shots — two videos in a row already feel like the same video. So: drop more tracks into
`assets/bgm/tracks/` and the pipeline rotates through them, one per video, deterministically.

A track is usable only with its credit, so each file needs a sidecar next to it:

    assets/bgm/tracks/quiet_tension.mp3
    assets/bgm/tracks/quiet_tension.credit.txt   ->  음악: "Quiet Tension" — Artist (CC BY 4.0) https://…

With no `tracks/` folder (or no credited file in it), BGM_PATH / BGM_CREDIT are used exactly as before.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

AUDIO = (".mp3", ".m4a", ".ogg", ".wav")


def _tracks_dir(cfg: Any) -> Path | None:
    base = Path(getattr(cfg, "bgm_path", "") or "")
    for cand in ((base.parent / "tracks") if base.name else None, Path("assets/bgm/tracks")):
        if cand and cand.is_dir():
            return cand
    return None


def credited_tracks(cfg: Any) -> list[tuple[Path, str]]:
    """[(file, credit line)] for every track that comes with its credit, oldest name first."""
    d = _tracks_dir(cfg)
    if not d:
        return []
    out: list[tuple[Path, str]] = []
    for f in sorted(d.iterdir()):
        if f.suffix.lower() not in AUDIO:
            continue
        side = f.with_suffix(".credit.txt")
        credit = side.read_text(encoding="utf-8").strip() if side.exists() else ""
        if credit:
            out.append((f, credit.splitlines()[0].strip()))
    return out


def pick(cfg: Any, seed: int = 0) -> tuple[Path, str]:
    """(track, credit) for this video. `seed` (the cluster id) keeps one story on one track across re-renders
    while consecutive videos move along the list."""
    tracks = credited_tracks(cfg)
    if tracks:
        return tracks[seed % len(tracks)]
    return Path(getattr(cfg, "bgm_path", "") or ""), str(getattr(cfg, "bgm_credit", "") or "")
