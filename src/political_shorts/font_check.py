"""Tiny helper: can Pillow load the configured Korean font?"""
from __future__ import annotations

from pathlib import Path


def font_ok(path: str) -> bool:
    try:
        from PIL import ImageFont

        if not Path(path).exists():
            return False
        ImageFont.truetype(path, size=32)
        return True
    except Exception:
        return False
