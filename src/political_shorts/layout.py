"""LAYOUT ENGINE.

`render_caption()` draws the body (the subtitle plate + decoration) of an
overlay frame, choosing one of six layouts by the scene's `scene_type`. The
FULL-SCRIPT subtitle is drawn identically in every layout — only the framing
changes — so readability never regresses. Any failure falls back to
LAYOUT_01. Shared chrome (scrim, progress bar, top label, footer) stays in
`video._overlay_png`.

  LAYOUT_01  일반 뉴스     default centred plate
  LAYOUT_02  인물 중심     + name/role lower-third
  LAYOUT_03  발언/Quote    + gold edge, open-quote, "— 발언자"
  LAYOUT_04  숫자/통계     the figure pulled out large above the line
  LAYOUT_05  비교          center divider + "이렇게 갈립니다"
  LAYOUT_06  쟁점/정리     key nouns underlined, sits a little higher
"""
from __future__ import annotations

import re
from typing import Any

from .textutil import clean_text

_TYPE_TO_LAYOUT = {
    "PERSON": "02", "QUOTE": "03", "NUMBER": "04",
    "COMPARISON": "05", "ISSUE": "06", "CONCLUSION": "06",
}
_ROLE_WORD = (r"대통령|국무총리|부총리|장관|차관|정책실장|비서실장|안보실장|수석|대변인|"
              r"원내대표|당대표|대표|의원|위원장|처장|청장|본부장|원장|시장|지사")
_SPK_RE = re.compile(rf"([가-힣]{{2,4}})\s*(?:{_ROLE_WORD})?\s*(?:은|는|이|가|측은|측이)?\s*[\"“'‘]")
_NAMEROLE_RE = re.compile(rf"([가-힣]{{2,4}})\s*({_ROLE_WORD})")
_FIG_RE = re.compile(r"\d[\d,.]*\s?(?:%|퍼센트|명|석|표차|표|억\s?원|억|조\s?원|조|만\s?원|만|배|주년|위|건|차)"
                     r"|과반|절반|만장일치|전원")


def layout_id(scene_type: str) -> str:
    return _TYPE_TO_LAYOUT.get(str(scene_type or "").upper(), "01")


def render_caption(draw, seg: dict[str, Any], cfg, w: int, h: int, accent: tuple) -> None:
    """Draw the subtitle body. `draw` is a PIL ImageDraw on the RGBA overlay."""
    from .video import (FG, SUBTLE, STROKE_DARK, _IMPACT_TOKEN, _draw_caption_line,
                        _font, _round_rect, _style, _text_stroke, _wrap)

    sst = _style().get("subtitle", {})
    stype = str(seg.get("scene_type") or "").upper()
    lid = layout_id(stype)
    cap = clean_text(seg.get("caption", ""))
    if not cap:
        return

    def _bf(s: int):
        return _font(cfg.font_body or cfg.font_bold_path, s)

    ladder = sst.get("size_ladder", [[14, 92], [24, 84], [34, 76], [46, 68], [999, 62]])
    bsize = next((s for c, s in ladder if len(cap) <= c), ladder[-1][1])
    min_size = int(sst.get("min_size", 56))
    max_lines = int(sst.get("max_lines", 3))
    lh_mult = float(sst.get("line_height", 1.38))
    wrap_w = int(w * sst.get("wrap_width_pct", 0.88))
    f_body, f_hot = _bf(bsize), _bf(int(bsize * 1.16))
    lines = _wrap(draw, cap, f_body, wrap_w)
    while len(lines) > max_lines and bsize > min_size:
        bsize -= 6
        f_body, f_hot = _bf(bsize), _bf(int(bsize * 1.16))
        lines = _wrap(draw, cap, f_body, wrap_w)
    lines = lines[:max_lines]
    lh = int(bsize * lh_mult)
    block = lh * len(lines)

    y_pct = float(sst.get("y_center_pct", 0.46))
    if lid == "06":
        y_pct -= 0.02                                   # issue/summary a touch higher
    y0 = int(h * y_pct) - block // 2

    def _lw(ln: str) -> float:
        parts = [t for t in ln.split(" ") if t]
        return (sum(draw.textlength(t, font=(f_hot if _IMPACT_TOKEN.match(t) else f_body))
                    for t in parts)
                + draw.textlength(" ", font=f_body) * max(0, len(parts) - 1))

    widest = max((_lw(ln) for ln in lines), default=0)
    pad_x, pad_y = sst.get("panel_pad", [44, 34])
    px0 = max(20, int(w / 2 - widest / 2) - pad_x)
    plate = [px0, y0 - pad_y, w - px0, y0 + block + pad_y - int(lh - bsize)]
    _round_rect(draw, plate, int(sst.get("panel_radius", 32)),
                tuple(sst.get("panel_rgba", (8, 10, 16, 222))))

    # ---- per-layout decoration ------------------------------------------
    try:
        if lid == "03":                                 # QUOTE
            draw.rectangle([px0, plate[1], px0 + 8, plate[3]], fill=(*accent, 255))
            fq = _font(cfg.font_title or cfg.font_bold_path, 118)
            draw.text((px0 - 4, plate[1] - 76), "“", font=fq, fill=(*accent, 230))
            spk = clean_text(seg.get("speaker", ""))
            if not spk:
                m = _SPK_RE.search(cap)
                spk = m.group(1) if m else ""
            if spk:
                fs = _font(cfg.font_label or cfg.font_bold_path, 34)
                sw = draw.textlength(f"— {spk}", font=fs)
                draw.text((w - px0 - sw, plate[3] + 12), f"— {spk}", font=fs, fill=(*SUBTLE, 235))

        elif lid == "02":                               # PERSON — name/role lower-third
            m = _NAMEROLE_RE.search(f"{seg.get('narration','')} {cap}")
            who, rolew = (m.group(1), m.group(2)) if m else (clean_text(seg.get("speaker", "")), "")
            if who:
                fn = _font(cfg.font_label or cfg.font_bold_path, 40)
                txt = f"{who}  {rolew}".strip()
                tw = draw.textlength(txt, font=fn)
                by = plate[3] + 14
                _round_rect(draw, [px0, by, px0 + tw + 36, by + fn.size + 18], 9, (*accent, 240))
                draw.text((px0 + 16, by + 4), txt, font=fn, fill=(12, 14, 20, 255))

        elif lid == "04":                               # NUMBER — pull the figure out big
            fm = _FIG_RE.search(cap)
            if fm:
                fig = fm.group(0).replace(" ", "")
                fsz = int(bsize * 1.6)
                ff = _font(cfg.font_body or cfg.font_bold_path, fsz)
                fw = draw.textlength(fig, font=ff)
                _text_stroke(draw, (int(w / 2 - fw / 2), plate[1] - fsz - 26),
                             fig, ff, (*_style().get("subtitle", {}).get("impact_color", (243, 61, 61)), 255),
                             7, (*STROKE_DARK, 250))

        elif lid == "05":                               # COMPARISON — center divider + label
            draw.line([(int(w / 2), int(h * 0.14)), (int(w / 2), int(h * 0.86))],
                      fill=(255, 255, 255, 34), width=3)
            fl = _font(cfg.font_label or cfg.font_bold_path, 30)
            lab = "이렇게 갈립니다"
            lw = draw.textlength(lab, font=fl)
            _round_rect(draw, [int(w / 2 - lw / 2 - 14), plate[1] - 46,
                               int(w / 2 + lw / 2 + 14), plate[1] - 6], 8, (*accent, 235))
            draw.text((int(w / 2 - lw / 2), plate[1] - 44), lab, font=fl, fill=(12, 14, 20, 255))

        elif lid == "06":                               # ISSUE / CONCLUSION — subtle key tab
            draw.rectangle([int(w / 2 - 60), plate[1] - 12, int(w / 2 + 60), plate[1] - 4],
                           fill=(*accent, 255))
        else:                                           # LAYOUT_01
            draw.rectangle([int(w / 2 - 46), plate[1] - 12, int(w / 2 + 46), plate[1] - 4],
                           fill=(*accent, 255))
    except Exception:  # pragma: no cover — decoration must never break a frame
        pass

    # ---- the FULL-SCRIPT subtitle text (identical in every layout) ------
    yy = y0 + int(bsize * 0.82)
    for ln in lines:
        _draw_caption_line(draw, int(w / 2), yy, ln, f_body, f_hot, 5)
        yy += lh
