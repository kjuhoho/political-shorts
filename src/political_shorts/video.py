"""Step 8 — render the 1080x1920 vertical MP4.

Per segment:
  * a CC photo fills the frame (cover-crop) with a slow Ken Burns zoom
  * an RGBA overlay adds a dark scrim + running headline + coloured kicker chip
    + the caption (or the fact-check rows) + a source footer
  * the segment's narration wav sets the clip length

Then the clips are concatenated and the BGM bed is mixed under.
Falls back to a dark gradient card when a segment has no image.
No moviepy: Pillow + system ffmpeg only.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import (
    Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps,
)

from .config import Settings, settings
from .logging_setup import get_logger
from .textutil import clean_text
from .tts import Narration, estimate_caption_seconds, synthesize_segments

log = get_logger("video")

# One neutral accent for every card — NO party colours (see NEUTRALITY.md).
# Same news-caption yellow as the thumbnail / persistent title.
_ACCENT = (245, 202, 66)
ROLE_ACCENT = {k: _ACCENT for k in
              ("hook", "summary", "what", "reaction", "factcheck", "outro")}
ROLE_LABEL = {
    "hook": "오늘의 이슈", "summary": "한 줄 요약", "what": "무슨 일이냐면",
    "reaction": "양쪽 반응", "factcheck": "팩트체크", "outro": "",
}
FG = (245, 246, 248)
SUBTLE = (200, 208, 220)
BG_TOP = (17, 24, 39)
BG_BOTTOM = (2, 6, 23)
# fact-check pill colours = a neutral legend (사실 / 주장 / 해석 / 확인),
# muted so nothing reads as an alarm or a party colour.
TONE_COL = {
    "ok": (46, 160, 90), "claim": (206, 158, 40),
    "warn": (120, 130, 148), "info": (74, 120, 176),
}


class FFmpegMissing(RuntimeError):
    pass


@dataclass
class RenderResult:
    video_path: Path
    duration_s: float
    n_segments: int


def _ffmpeg(cfg: Settings) -> str:
    exe = cfg.ffmpeg_path or "ffmpeg"
    if shutil.which(exe) or Path(exe).exists():
        return exe
    raise FFmpegMissing(
        "ffmpeg not found. Set FFMPEG_PATH in .env or install it "
        "(winget install --id Gyan.FFmpeg -e)."
    )


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({proc.returncode}):\n{' '.join(map(str, cmd))}\n{proc.stderr[-1600:]}"
        )


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    out: list[str] = []
    for para in (text or "").split("\n"):
        words = para.split(" ")
        cur = ""
        for word in words:
            trial = (cur + " " + word).strip()
            if not cur or draw.textlength(trial, font=font) <= max_w:
                if not cur and draw.textlength(trial, font=font) > max_w:
                    acc = ""
                    for ch in word:
                        if draw.textlength(acc + ch, font=font) <= max_w:
                            acc += ch
                        else:
                            out.append(acc)
                            acc = ch
                    cur = acc
                else:
                    cur = trial
            else:
                out.append(cur)
                cur = word
        out.append(cur)
    return [ln for ln in out if ln != ""] or [""]


def _gradient_bg(w: int, h: int) -> Image.Image:
    top = Image.new("RGB", (w, h), BG_TOP)
    bot = Image.new("RGB", (w, h), BG_BOTTOM)
    mask = Image.new("L", (w, h))
    px = mask.load()
    for y in range(h):
        v = int(255 * (y / max(1, h - 1)))
        for x in range(w):
            px[x, y] = v
    return Image.composite(bot, top, mask)


def _round_rect(draw: ImageDraw.ImageDraw, box, radius, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _scrim(w: int, h: int) -> Image.Image:
    """Smooth, monotonic dark gradient: clear across the top third, easing to a
    near-opaque band over the bottom ~35% where the caption sits. Built as a
    1px-wide alpha ramp then stretched, so it is fast and free of banding."""
    col = Image.new("L", (1, h), 0)
    for y in range(h):
        f = y / (h - 1)
        if f < 0.28:
            a = int(90 * _smoothstep(f / 0.28))          # faint top wash
        elif f < 0.62:
            a = int(90 + 60 * _smoothstep((f - 0.28) / 0.34))
        else:
            a = int(150 + 95 * _smoothstep((f - 0.62) / 0.30))
        col.putpixel((0, y), a)
    alpha = col.resize((w, h))
    layer = Image.new("RGBA", (w, h), (6, 9, 18, 255))
    layer.putalpha(alpha)
    return layer


TITLE_YELLOW = (255, 226, 61)
STROKE_DARK = (8, 10, 16)


def _text_stroke(draw, xy, text, font, fill, stroke_w, stroke_fill, anchor=None):
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke_w,
              stroke_fill=stroke_fill, anchor=anchor)


def _overlay_png(
    seg: dict[str, Any], idx: int, total: int, script: dict[str, Any], out_png: Path, cfg: Settings
) -> None:
    w, h = cfg.video_width, cfg.video_height
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.alpha_composite(_scrim(w, h))
    draw = ImageDraw.Draw(img)

    role = seg.get("role", "what")
    accent = ROLE_ACCENT.get(role, ROLE_ACCENT["what"])
    margin = int(w * 0.06)
    max_w = w - 2 * margin

    f_title = _font(cfg.font_title or cfg.font_bold_path, 84)
    f_num = _font(cfg.font_label or cfg.font_bold_path, 58)
    f_foot = _font(cfg.font_path, 30)
    f_rtag = _font(cfg.font_label or cfg.font_bold_path, 36)
    f_row = _font(cfg.font_body or cfg.font_path, 44)

    # thin progress ticks along the very top
    tick_w = (w - 2 * margin - (total - 1) * 7) / max(1, total)
    for i in range(total):
        x0 = margin + i * (tick_w + 7)
        _round_rect(draw, [x0, 14, x0 + tick_w, 20], 3, accent if i <= idx else (255, 255, 255, 70))

    # ---- 1) PERSISTENT TITLE (identical on every card) ----------------------
    # Placed in the upper-middle so it survives the square/4:5 thumbnail crop.
    title_lines = (script.get("title") or [script.get("headline", "")])[:2]
    while f_title.size > 54 and any(
        draw.textlength(t, font=f_title) > max_w for t in title_lines
    ):
        f_title = _font(cfg.font_title or cfg.font_bold_path, f_title.size - 4)
    line_h = int(f_title.size * 1.16)
    tblock = line_h * len(title_lines)
    ty = int(h * 0.185)
    # a rounded dark plate behind the title so it reads over any photo
    _round_rect(draw, [0, ty - 26, w, ty + tblock + 22], 0, (6, 8, 14, 224))
    y = ty
    for t in title_lines:
        _text_stroke(draw, (margin, y), t, f_title, (*TITLE_YELLOW, 255), 4, (*STROKE_DARK, 255))
        y += line_h

    # ---- 2) small numbered section chip (just under the title) ------------
    num = seg.get("num")
    topic = script.get("topic") or ""
    label = f"{num}  {topic}".strip() if num else (topic or seg.get("kicker", ""))
    if label:
        fc = _font(cfg.font_label or cfg.font_bold_path, 40)
        ly = ty + tblock + 30
        cw = draw.textlength(label, font=fc)
        _round_rect(draw, [margin - 12, ly - 8, margin + cw + 20, ly + fc.size + 14], 10,
                    (*accent, 235))
        draw.text((margin + 4, ly + 2), label, font=fc, fill=(12, 14, 20, 255))

    # ---- 3) BODY / CAPTION ------------------------------------------------
    if role == "factcheck" and seg.get("rows"):
        rows = seg["rows"][:4]
        y = int(h * 0.42)
        for r in rows:
            tcol = TONE_COL.get(r.get("tone", "info"), TONE_COL["info"])
            _round_rect(draw, [margin, y, margin + 100, y + 54], 12, (*tcol, 255))
            draw.text((margin + 16, y + 7), r.get("tag", ""), font=f_rtag, fill=(10, 12, 20, 255))
            tx = margin + 122
            wrapped = _wrap(draw, r.get("text", ""), f_row, max_w - 122)[:2]
            for k, ln in enumerate(wrapped):
                _text_stroke(draw, (tx, y + 2 + k * 52), ln, f_row, (*FG, 255), 3, (*STROKE_DARK, 220))
            y += 70 + 52 * len(wrapped)
    else:
        # Big, CENTERED caption on a rounded dark plate — the look of
        # high-view Korean news/issue shorts (auto-caption style).
        cap = seg.get("caption", "")
        clen = len(cap)
        bsize = 84 if clen <= 18 else 76 if clen <= 30 else 64 if clen <= 44 else 54
        f_body = _font(cfg.font_body or cfg.font_bold_path, bsize)
        lines = _wrap(draw, cap, f_body, int(w * 0.86))[:4]
        lh = int(bsize * 1.32)
        block = lh * len(lines)
        y0 = int(h * 0.47) - block // 2
        widest = max((draw.textlength(ln, font=f_body) for ln in lines), default=0)
        pad_x, pad_y = 40, 30
        px0 = max(24, int(w / 2 - widest / 2) - pad_x)
        _round_rect(draw, [px0, y0 - pad_y, w - px0, y0 + block + pad_y - int(lh - bsize)],
                    30, (8, 10, 16, 214))
        # a short accent tab centred above the plate
        draw.rectangle([int(w / 2 - 46), y0 - pad_y - 12, int(w / 2 + 46), y0 - pad_y - 4],
                       fill=(*accent, 255))
        yy = y0
        for ln in lines:
            _text_stroke(draw, (int(w / 2), yy), ln, f_body, (*FG, 255), 5,
                         (*STROKE_DARK, 245), anchor="ma")
            yy += lh

    # ---- 4) FOOTER ------------------------------------------------------
    srcs = [s["name"] for s in script.get("sources", [])[:3]]
    src_txt = ", ".join(srcs) + (" 외" if len(script.get("sources", [])) > 3 else "")
    fy = h - 130
    draw.line([(margin, fy - 16), (w - margin, fy - 16)], fill=(255, 255, 255, 45), width=2)
    draw.text((margin, fy), f"출처: {src_txt}", font=f_foot, fill=(*SUBTLE, 235))
    note = {
        "what": "· 단일 출처 — 확인 필요" if not seg.get("multi_source", True) else "",
        "reaction": "· 보도 인용", "factcheck": "· 해석·전망은 사실과 구분",
        "hook": "· 자동 생성 · 사실확인 필수",
    }.get(role, "")
    if note:
        draw.text((margin, fy + 36), note, font=f_foot,
                  fill=(248, 113, 113, 240) if "확인" in note else (*SUBTLE, 210))

    img.save(out_png, "PNG")


# ========================================================================== #
# OPENING THUMBNAIL  — the designed first frame (what YouTube grabs as the
# Shorts poster). Broadcast-neutral palette on purpose: charcoal + off-white +
# the same news-caption yellow as the card titles. NO party colours — heavy red
# reads as 국민의힘/우파, strong blue as 민주당. Frame type changes wording, not hue.
# ========================================================================== #
INK = (14, 16, 20)          # near-black ground
PANEL = (26, 29, 35)        # charcoal panel / wedge
GOLD = (245, 202, 66)       # the "이슈" yellow (same family as TITLE_YELLOW)
LINE = (206, 172, 92)       # muted gold hairline accents
PAPER = (245, 246, 249)
# words that get the highlight box / a beat of emphasis in the headline
_HOT = ("논란", "충격", "발칵", "결국", "왜", "파문", "의혹", "사퇴", "거부",
        "경악", "초유", "정면", "폭탄", "위기", "터졌", "직격", "반발",
        "구속", "해임", "전격", "속보", "단독", "무슨")
_BADGE = {
    "scandal": "이슈", "clash": "쟁점", "personnel": "인사", "vote": "표결",
    "poll": "여론", "remark": "발언", "generic": "이슈",
}


def _fit_font(draw, text, path, start, max_w, min_size=54, step=4):
    f = _font(path, start)
    while f.size > min_size and draw.textlength(text, font=f) > max_w:
        f = _font(path, f.size - step)
    return f


def _duotone(img: Image.Image, dark, light) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = ImageEnhance.Contrast(g).enhance(1.15)
    return ImageOps.colorize(g, black=dark, white=light, mid=(
        (dark[0] + light[0]) // 2, (dark[1] + light[1]) // 2, (dark[2] + light[2]) // 2))


def _grain(w: int, h: int, amount: int = 16) -> Image.Image:
    small = Image.effect_noise((w // 3, h // 3), 100).resize((w, h))
    a = small.point(lambda v: abs(v - 128) * amount // 128).convert("L")
    layer = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    layer.putalpha(a)
    return layer


def _vignette(w: int, h: int, strength: int = 150) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([-w * 0.28, -h * 0.16, w * 1.28, h * 1.16], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(min(w, h) // 5))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, strength))
    layer.putalpha(ImageOps.invert(mask).point(lambda v: v * strength // 255))
    return layer


def _feathered_portrait(path: str, box_w: int, box_h: int) -> Image.Image | None:
    """Portrait scaled to fill box; the TOP and LEFT edges fade out so the face
    melts into the backing panel / headline instead of showing a hard rectangle.
    Right and bottom stay crisp (they bleed off-frame or sit on the panel)."""
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return None
    scale = max(box_w / im.width, box_h / im.height) * 1.02
    im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    left = (im.width - box_w) // 2
    top = int((im.height - box_h) * 0.16)          # bias toward the head
    im = im.crop((left, max(0, top), left + box_w, max(0, top) + box_h))
    im = ImageEnhance.Contrast(im).enhance(1.12)
    im = ImageEnhance.Color(im).enhance(1.06)

    mask = Image.new("L", (box_w, box_h), 255)
    grad = Image.linear_gradient("L")                       # 0 at top -> 255 bottom
    top_fade = grad.resize((box_w, int(box_h * 0.30)))
    mask.paste(top_fade, (0, 0))
    left_fade = grad.rotate(90, expand=True).resize((int(box_w * 0.16), box_h))
    lf = Image.new("L", (box_w, box_h), 255)
    lf.paste(left_fade, (0, 0))
    mask = ImageChops.darker(mask, lf)
    mask = mask.filter(ImageFilter.GaussianBlur(box_w // 14))
    out = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _wedge(w: int, h: int, col, alpha: int = 235) -> Image.Image:
    """A hard diagonal charcoal block anchored bottom-left — the 'graphic' base
    the headline sits on. A thin gold hairline reads as a broadcast lower-third."""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon([(0, int(h * 0.40)), (int(w * 0.92), int(h * 0.64)),
               (w, h), (0, h)], fill=(*col, alpha))
    d.line([(0, int(h * 0.40)), (int(w * 0.92), int(h * 0.64))],
           fill=(*LINE, 170), width=5)
    return layer


def _thumbnail_png(script: dict[str, Any], bg_path: str | None, portrait_path: str | None,
                   out_png: Path, cfg: Settings) -> None:
    w, h = cfg.video_width, cfg.video_height
    frame = script.get("frame", "generic")

    # 1) base image: the scene photo as a moody neutral B&W + vignette + grain
    if bg_path and Path(bg_path).exists():
        try:
            src = Image.open(bg_path).convert("RGB")
        except Exception:
            src = _gradient_bg(w, h)
    else:
        src = _gradient_bg(w, h)
    scale = max(w / src.width, h / src.height) * 1.05
    src = src.resize((int(src.width * scale), int(src.height * scale)), Image.LANCZOS)
    src = src.crop(((src.width - w) // 2, (src.height - h) // 2,
                    (src.width - w) // 2 + w, (src.height - h) // 2 + h))
    base = _duotone(src, dark=(16, 17, 20), light=(228, 230, 234))
    base = ImageEnhance.Brightness(base).enhance(0.78).convert("RGBA")
    base.alpha_composite(_vignette(w, h, 175))

    d = ImageDraw.Draw(base)
    d.rectangle([0, 0, w, 10], fill=(*LINE, 255))

    face = _feathered_portrait(portrait_path, int(w * 0.72), int(h * 0.60)) if portrait_path else None
    if face is not None:
        # 2a) diagonal charcoal wedge + an angled panel the face sits on
        base.alpha_composite(_wedge(w, h, PANEL, 238))
        d = ImageDraw.Draw(base)
        pw, ph = face.size
        px, py = w - pw + int(w * 0.02), h - ph
        d.polygon([(px - int(w * 0.05), py + int(ph * 0.16)),
                   (w, py - int(ph * 0.04)), (w, h), (px - int(w * 0.13), h)],
                  fill=(*INK, 255))
        d.line([(px - int(w * 0.05), py + int(ph * 0.16)),
                (w, py - int(ph * 0.04))], fill=(*LINE, 210), width=6)
        base.alpha_composite(face, (px, py))
    else:
        # 2b) no portrait: a clean charcoal lower band (no dead diagonal). The
        #     headline drops onto the seam so the band isn't an empty void.
        band_y = int(h * 0.60)
        d.rectangle([0, band_y, w, h], fill=(*PANEL, 238))
        d.line([(0, band_y), (w, band_y)], fill=(*LINE, 170), width=5)

    base.alpha_composite(_grain(w, h, 13))
    d = ImageDraw.Draw(base)
    margin = int(w * 0.055)

    # 4) angular top badge  —  ▐ 정치 이슈  (charcoal chip, gold tab, no '·')
    badge = f"정치 {_BADGE.get(frame, '이슈')}"
    fb = _font(cfg.font_label or cfg.font_bold_path, 46)
    bw = d.textlength(badge, font=fb)
    by = int(h * 0.125)
    d.polygon([(margin, by), (margin + bw + 54, by),
               (margin + bw + 34, by + 66), (margin, by + 66)], fill=(*INK, 240))
    d.rectangle([margin, by, margin + 10, by + 66], fill=(*GOLD, 255))
    d.text((margin + 26, by + 9), badge, font=fb, fill=(*PAPER, 255))

    # 5) THE HEADLINE — big stacked BlackHanSans. Line 1 gold; the punch line
    #    sits in a solid gold box with near-black text (news-caption look).
    lines = [clean_text(x) for x in (script.get("title") or []) if clean_text(x)][:3]
    if not lines:
        lines = [clean_text(script.get("topic") or script.get("headline", ""))[:14]]
    max_tw = int(w * 0.86)
    title_path = cfg.font_title or cfg.font_bold_path
    fonts = [_fit_font(d, ln, title_path, 138, max_tw, min_size=66) for ln in lines]
    lh = [int(f.size * 1.22) for f in fonts]
    ty = int(h * 0.185) if portrait_path else int(h * 0.44)

    box_i = next((i for i, ln in enumerate(lines) if any(k in ln for k in _HOT)),
                 len(lines) - 1)
    y = ty
    for i, (ln, f) in enumerate(zip(lines, fonts)):
        tw = d.textlength(ln, font=f)
        if i == box_i and len(lines) > 1:
            pad = 20
            d.rounded_rectangle([margin - 6, y - 6, margin + tw + pad * 2, y + f.size + 20],
                                12, fill=(*GOLD, 255))
            d.text((margin + pad, y), ln, font=f, fill=(*INK, 255))
        else:
            fill = (*GOLD, 255) if i == 0 else (*PAPER, 255)
            _text_stroke(d, (margin, y), ln, f, fill, 9, (*INK, 255))
        y += lh[i] + (14 if i < len(lines) - 1 else 0)

    # 6) thin bottom rail  —  restrained, kept clear of the ~4% the push-in crops
    ry = h - 150
    d.line([(margin, ry), (w - margin, ry)], fill=(255, 255, 255, 65), width=3)
    d.rectangle([margin, ry - 3, margin + 120, ry + 3], fill=(*GOLD, 255))
    d.text((margin, ry + 16), "핵심만 30초, 여러 매체 종합",
           font=_font(cfg.font_path, 30), fill=(*PAPER, 230))

    base.convert("RGB").save(out_png, "JPEG", quality=92)


def _thumb_clip(ffmpeg: str, thumb_jpg: Path, out_mp4: Path, cfg: Settings,
                seconds: float) -> None:
    """A short, almost-still hold on the thumbnail with a very slow push-in.
    Starts at zoom 1.0 so the very first frame (the Shorts poster) shows the
    whole composition uncropped."""
    w, h, fps = cfg.video_width, cfg.video_height, cfg.video_fps
    z = f"(1.0+0.045*min(t/{seconds:.2f}\\,1))"
    vf = (f"[0:v]scale={w}:{h},"
          f"crop=w='iw/{z}':h='ih/{z}':x='(iw-iw/{z})/2':y='(ih-ih/{z})/2',"
          f"scale={w}:{h},setsar=1,fps={fps}[v]")
    _run([
        ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-i", str(thumb_jpg),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex", vf, "-map", "[v]", "-map", "1:a",
        "-t", f"{seconds:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "160k", str(out_mp4),
    ])


_FRAME_TINT = {
    "personnel": (37, 99, 235), "clash": (220, 38, 38), "scandal": (139, 92, 246),
    "vote": (22, 163, 74), "poll": (14, 165, 233), "remark": (202, 138, 4),
    "generic": (71, 85, 105),
}


def _draw_icon(dr: ImageDraw.ImageDraw, kind: str, cx: int, cy: int, s: int, col) -> None:
    """A big, faint, flat glyph that says what kind of story this is."""
    lw = max(10, s // 12)
    if kind == "clash":                                   # two speech bubbles facing off
        dr.rounded_rectangle([cx - s, cy - s, cx - s // 6, cy - s // 6], s // 6, outline=col, width=lw)
        dr.polygon([(cx - s + s // 4, cy - s // 6), (cx - s + s // 2, cy - s // 6), (cx - s + s // 4, cy + s // 8)], fill=col)
        dr.rounded_rectangle([cx + s // 6, cy + s // 6, cx + s, cy + s], s // 6, outline=col, width=lw)
        dr.polygon([(cx + s - s // 4, cy + s // 6), (cx + s - s // 2, cy + s // 6), (cx + s - s // 4, cy - s // 8)], fill=col)
    elif kind == "scandal":                               # magnifying glass
        r = int(s * 0.7)
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=lw)
        dr.line([(cx + int(r * 0.7), cy + int(r * 0.7)), (cx + int(r * 1.5), cy + int(r * 1.5))], fill=col, width=lw + 6)
    elif kind == "vote":                                  # ballot box + check
        dr.rectangle([cx - s, cy - s // 2, cx + s, cy + s], outline=col, width=lw)
        dr.line([(cx - s // 2, cy - s // 2), (cx + s // 2, cy - s // 2)], fill=col, width=lw)
        dr.line([(cx - s // 2, cy + s // 5), (cx - s // 8, cy + s // 2), (cx + s // 2, cy - s // 6)], fill=col, width=lw + 4, joint="curve")
    elif kind == "poll":                                  # ascending bars
        for k in range(4):
            bx = cx - s + k * (s // 2)
            dr.rectangle([bx, cy + s - (k + 1) * (s // 2), bx + s // 3, cy + s], fill=col)
    elif kind == "remark":                                # megaphone
        dr.polygon([(cx - s, cy - s // 3), (cx + s // 4, cy - s), (cx + s // 4, cy + s), (cx - s, cy + s // 3)], outline=col, width=lw)
        dr.arc([cx + s // 6, cy - s, cx + s, cy + s], -60, 60, fill=col, width=lw)
    elif kind == "personnel":                             # office chair
        dr.rounded_rectangle([cx - s // 2, cy - s, cx + s // 2, cy], s // 8, outline=col, width=lw)
        dr.rectangle([cx - s * 3 // 4, cy, cx + s * 3 // 4, cy + s // 5], fill=col)
        dr.line([(cx, cy + s // 5), (cx, cy + s * 3 // 4)], fill=col, width=lw)
        dr.line([(cx - s // 2, cy + s), (cx + s // 2, cy + s)], fill=col, width=lw)
    else:                                                 # National Assembly dome
        dr.arc([cx - s, cy - s // 2, cx + s, cy + s * 3 // 2], 180, 360, fill=col, width=lw)
        for k in range(5):
            px = cx - s + k * (s // 2)
            dr.line([(px, cy + s // 2), (px, cy + s * 3 // 2)], fill=col, width=lw)
        dr.line([(cx - s - 30, cy + s * 3 // 2), (cx + s + 30, cy + s * 3 // 2)], fill=col, width=lw)


def _stylized_backdrop(frame_kind: str, idx: int, w: int, h: int, tmp: Path) -> Path:
    """A clean drawn backdrop with a big faint iconographic glyph. No people,
    always on-topic, never wrong. Frame-tinted; wash + glyph position rotate."""
    p = tmp / f"bd_{frame_kind}_{idx}.jpg"
    if p.exists():
        return p
    tint = _FRAME_TINT.get(frame_kind, _FRAME_TINT["generic"])
    v = idx % 4
    left = v in (0, 3)
    hi = v in (0, 1)
    img = Image.new("RGB", (w, h), (9, 12, 22))
    wash = Image.new("RGB", (w, h), tuple(int(c * (0.20 + 0.06 * (v % 2))) + 7 for c in tint))
    m = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(m)
    y0, y1 = (int(h * 0.16), int(h * 0.5)) if hi else (int(h * 0.5), int(h * 0.16))
    if left:
        md.polygon([(0, h), (w, h), (w, y0), (0, y1)], fill=135)
    else:
        md.polygon([(0, h), (w, h), (w, y1), (0, y0)], fill=135)
    img = Image.composite(wash, img, m)
    dr = ImageDraw.Draw(img, "RGBA")
    cx = int(w * (0.28 if left else 0.72))
    cy = int(h * (0.24 + 0.10 * v))
    _draw_icon(dr, frame_kind, cx, cy, int(w * 0.30), (*tint, 44))
    for gx in range(6):
        for gy in range(6):
            px = (int(w * 0.64) if left else int(w * 0.04)) + gx * 72
            py = int(h * 0.06) + gy * 62
            dr.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(*tint, 18))
    img.save(p, "JPEG", quality=90)
    return p


def _segment_bg(image_path: str | None, frame_kind: str, idx: int,
                w: int, h: int, tmp: Path) -> tuple[Path, bool]:
    """Return (path to an RGB jpg sized to the frame, is_real_photo)."""
    if image_path and Path(image_path).exists():
        try:
            im = Image.open(image_path).convert("RGB")
            scale = max(w / im.width, h / im.height) * 1.06
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
            left, top = (im.width - w) // 2, (im.height - h) // 2
            im = im.crop((left, top, left + w, top + h))
            out = tmp / (Path(image_path).stem + f"_cov{idx}.jpg")
            im.save(out, "JPEG", quality=90)
            return out, True
        except Exception as exc:
            log.debug("cover failed for %s: %s", image_path, exc)
    return _stylized_backdrop(frame_kind, idx, w, h, tmp), False


def _assign_images(
    segments: list[dict], images: list[dict], topic: str = ""
) -> list[str | None]:
    """One distinct photo per card. A PORTRAIT is only laid under a card that
    actually names that person (so 이재명's face never sits beneath a "김용범"
    label); location shots fill everything else. A portrait that matches no card
    just goes unused. Photos repeat only when there genuinely aren't enough; a
    card with nothing gets a drawn backdrop."""
    portraits = [(im["path"], (im.get("query") or "").strip())
                 for im in images if im.get("path") and im.get("kind") == "portrait"]
    photos = [im["path"] for im in images if im.get("path") and im.get("kind") != "portrait"]
    if not portraits and not photos:
        return [None] * len(segments)

    out: list[str | None] = []
    used: set[str] = set()
    fi = 0

    def _take_photo(i: int) -> str | None:
        if not photos:
            return None
        for k in range(len(photos)):
            cur = photos[(i + k) % len(photos)]
            if cur not in used:
                used.add(cur)
                return cur
        return photos[i % len(photos)]

    def _take_portrait(seg: dict) -> str | None:
        hay = f"{seg.get('caption', '')} {seg.get('narration', '')} {seg.get('kicker', '')} {topic}"
        for path, who in portraits:
            if who and who in hay and path not in used:
                used.add(path)
                return path
        return None

    for seg in segments:
        role = seg.get("role")
        pic = _take_portrait(seg) if role in ("hook", "what", "reaction") else None
        if pic is None:
            pic = _take_photo(fi); fi += 1
        out.append(pic)
    return out


def _segment_clip(
    ffmpeg: str, base_img: Path, is_photo: bool, overlay: Path,
    nar: Narration, duration: float, out_mp4: Path, cfg: Settings, idx: int
) -> None:
    w, h, fps = cfg.video_width, cfg.video_height, cfg.video_fps

    if cfg.ken_burns:
        # Cheap Ken-Burns: over-scale, then crop-pan with a cosine ease PLUS a
        # quick punch-in on the cut. The punch-in is baked into a per-frame
        # `scale` (eval=frame) so there's no second dynamic crop to choke on;
        # zoompan is far too slow.
        over = 1.18 if is_photo else 1.12
        sw, sh = int(w * over), int(h * over)
        prog = f"(0.5-0.5*cos(PI*min(t/{duration:.2f}\\,1)))"
        yb = 0.5 if idx % 2 == 0 else 0.32
        px = f"(iw-{w})*{prog}" if idx % 2 == 0 else f"(iw-{w})*(1-{prog})"
        pz = "(1+0.05*exp(-t*6))"                    # +5% at the cut, gone ~0.5s
        vbg = (f"[0:v]scale=w='{sw}*{pz}':h='{sh}*{pz}':eval=frame,"
               f"crop={w}:{h}:x='{px}':y='(ih-{h})*{yb}',"
               f"setsar=1,fps={fps}[bg]")
    else:
        vbg = f"[0:v]scale={w}:{h},setsar=1,fps={fps}[bg]"

    filt = f"{vbg};[bg][1:v]overlay=0:0:format=auto[v]"
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(base_img),
        "-loop", "1", "-i", str(overlay),
    ]
    if nar.wav_path:
        cmd += ["-i", str(nar.wav_path)]
        amap = ["-map", "2:a", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        amap = ["-map", "2:a", "-c:a", "aac", "-b:a", "160k"]
    cmd += [
        "-filter_complex", filt, "-map", "[v]", *amap,
        "-t", f"{duration:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        str(out_mp4),
    ]
    _run(cmd)


def render_video(script: dict[str, Any], out_path: Path, cfg: Settings | None = None) -> RenderResult:
    cfg = cfg or settings
    ffmpeg = _ffmpeg(cfg)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    segments = script["segments"]
    total = len(segments)

    workdir = Path(tempfile.mkdtemp(prefix="pshorts_"))
    try:
        narrations = synthesize_segments([s["narration"] for s in segments], workdir / "audio", cfg)
        seg_images = _assign_images(segments, script.get("images", []), script.get("topic", ""))

        frame_kind = script.get("frame", "generic")
        clip_paths: list[Path] = []
        total_dur = 0.0

        # ---- designed opening frame (the Shorts poster) --------------------
        if getattr(cfg, "thumb_enabled", True):
            imgs = script.get("images", [])
            portrait = next((im["path"] for im in imgs
                             if im.get("kind") == "portrait" and im.get("path")
                             and Path(im["path"]).exists()), None)
            scene = next((im["path"] for im in imgs
                          if im.get("kind") != "portrait" and im.get("path")
                          and Path(im["path"]).exists()), None) or portrait
            thumb_jpg = workdir / "thumb.jpg"
            try:
                _thumbnail_png(script, scene, portrait, thumb_jpg, cfg)
                hold = float(getattr(cfg, "thumb_hold_seconds", 1.7))
                tclip = workdir / "clip_thumb.mp4"
                _thumb_clip(ffmpeg, thumb_jpg, tclip, cfg, hold)
                clip_paths.append(tclip)
                total_dur += hold
            except Exception as exc:  # never let the poster break a render
                log.warning("thumbnail frame skipped: %s", exc)

        for i, seg in enumerate(segments):
            overlay = workdir / f"ov_{i:02d}.png"
            _overlay_png(seg, i, total, script, overlay, cfg)
            base, is_photo = _segment_bg(seg_images[i], frame_kind, i,
                                         cfg.video_width, cfg.video_height, workdir)

            nar = narrations[i]
            if nar.wav_path and nar.duration_s > 0.3:
                duration = nar.duration_s + 0.16     # tiny tail -> quick cut
            elif seg.get("narration"):
                duration = estimate_caption_seconds(seg.get("caption", ""), cfg)
            else:
                duration = 1.5                       # caption-only end card
            total_dur += duration

            clip = workdir / f"clip_{i:02d}.mp4"
            _segment_clip(ffmpeg, base, is_photo, overlay, nar, duration, clip, cfg, i)
            clip_paths.append(clip)

        concat_txt = workdir / "concat.txt"
        concat_txt.write_text("".join(f"file '{p.as_posix()}'\n" for p in clip_paths), encoding="utf-8")
        narration_mp4 = workdir / "narration.mp4"
        _run([
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(cfg.video_fps),
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(narration_mp4),
        ])

        n_imgs = sum(1 for p in seg_images if p)
        if _bgm_usable(cfg):
            _mix_bgm(ffmpeg, narration_mp4, Path(cfg.bgm_path), total_dur, out_path, cfg)
            log.info("video rendered %s (%.1fs, %d segs, %d imgs, +bgm)",
                     out_path.name, total_dur, total, n_imgs)
        else:
            shutil.move(str(narration_mp4), str(out_path))
            log.info("video rendered %s (%.1fs, %d segs, %d imgs)",
                     out_path.name, total_dur, total, n_imgs)

        return RenderResult(out_path, round(total_dur, 2), total)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _bgm_usable(cfg: Settings) -> bool:
    if not cfg.bgm_enabled:
        return False
    if not Path(cfg.bgm_path or "").exists():
        log.warning("BGM_ENABLED but BGM_PATH not found: %s", cfg.bgm_path)
        return False
    return True


def _mix_bgm(ffmpeg: str, video_in: Path, bgm: Path, duration: float, out_path: Path, cfg: Settings) -> None:
    fade = max(0.0, float(cfg.bgm_fade_seconds))
    fade_start = max(0.0, duration - fade)
    bed = (
        f"[1:a]volume={cfg.bgm_volume_db}dB,afade=t=in:st=0:d={min(fade, 1.5):.2f},"
        f"afade=t=out:st={fade_start:.2f}:d={fade:.2f}[bed]"
    )
    if cfg.bgm_duck:
        filt = (f"{bed};[bed][0:a]sidechaincompress=threshold=0.02:ratio=12:attack=8:release=350[ducked];"
                f"[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    else:
        filt = f"{bed};[0:a][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    _run([
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(video_in), "-stream_loop", "-1", "-i", str(bgm),
        "-filter_complex", filt, "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", "-shortest", str(out_path),
    ])


def probe_duration(path: Path, cfg: Settings | None = None) -> float:
    cfg = cfg or settings
    ffprobe = (cfg.ffmpeg_path or "ffmpeg").replace("ffmpeg", "ffprobe")
    if not (shutil.which(ffprobe) or Path(ffprobe).exists()):
        return 0.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return 0.0
