"""The cover image YouTube shows in search, on the channel page and in suggestions.

Until now there was none: YouTube took a frame of the video, which meant the thumbnail was whatever the opening
card happened to be — the same building over and over (live audit 2026-09-30). A made cover also gets the
title in type big enough to read at thumbnail size, which a video frame never does.

16:9 1280x720 (what `thumbnails.set` wants), under 2MB: the person's face when the story has one, else the
story's establishing photo, darkened, with the short title lines over it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("thumbnail")

W, H = 1280, 720
MAX_BYTES = 2_000_000


def _lead_image(script: dict[str, Any]) -> str:
    """The picture the cover is built on: the story's face, else its first photo."""
    images = script.get("images") or []
    for kind in ("portrait", "photo"):
        for im in images:
            if im.get("kind") == kind and im.get("path") and Path(str(im["path"])).exists():
                if kind != "portrait" or im.get("is_lead") or True:
                    return str(im["path"])
    return ""


def _lines(script: dict[str, Any]) -> list[str]:
    """Two short lines at most — a thumbnail is read in half a second."""
    raw = [clean_text(str(x)).replace("#shorts", "").strip() for x in (script.get("title") or [])]
    out = [x for x in raw if x][:2]
    if not out:
        out = [clean_text(script.get("headline", ""))[:18]]
    return out


def build(script: dict[str, Any], out_path: Path, cfg: Any) -> Path | None:
    """Write the cover next to the video and return its path (None when it cannot be drawn)."""
    try:
        from PIL import Image, ImageDraw, ImageFilter

        from .video import _font, _wrap

        src = _lead_image(script)
        if src:
            base = Image.open(src).convert("RGB")
            scale = max(W / base.width, H / base.height)
            base = base.resize((max(W, int(base.width * scale)), max(H, int(base.height * scale))),
                               Image.LANCZOS)
            left = (base.width - W) // 2
            # a portrait is framed on its upper half: that is where the face is
            top = int((base.height - H) * (0.18 if base.height > base.width else 0.5))
            img = base.crop((left, top, left + W, top + H))
        else:
            img = Image.new("RGB", (W, H), (17, 24, 39))

        # darken the lower two thirds so the type reads on any photo
        shade = Image.new("L", (W, H), 0)
        ImageDraw.Draw(shade).rectangle([0, int(H * 0.30), W, H], fill=215)
        img = Image.composite(Image.new("RGB", (W, H), (8, 12, 22)), img,
                              shade.filter(ImageFilter.GaussianBlur(70)))

        draw = ImageDraw.Draw(img)
        lines = _lines(script)
        font = _font(cfg.font_title or cfg.font_bold_path, 96)
        wrapped: list[str] = []
        for ln in lines:
            wrapped.extend(_wrap(draw, ln, font, W - 120))
        wrapped = wrapped[:3]
        gap = 14
        heights = [draw.textbbox((0, 0), ln, font=font)[3] for ln in wrapped] or [0]
        block = sum(heights) + gap * (len(wrapped) - 1)
        y = H - 70 - block
        for ln, h in zip(wrapped, heights):
            for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3)):      # outline, so white type survives a bright photo
                draw.text((60 + dx, y + dy), ln, font=font, fill=(8, 12, 22))
            draw.text((60, y), ln, font=font, fill=(245, 246, 248))
            y += h + gap

        out_path = out_path.with_suffix(".jpg")
        for quality in (88, 78, 68):
            img.save(out_path, "JPEG", quality=quality, optimize=True)
            if out_path.stat().st_size <= MAX_BYTES:
                break
        log.info("thumbnail %s (%d KB, from %s)", out_path.name, out_path.stat().st_size // 1024,
                 Path(src).name if src else "plain background")
        return out_path
    except Exception as exc:                      # a missing cover must never hold a video back
        log.warning("thumbnail not built: %s", exc)
        return None
