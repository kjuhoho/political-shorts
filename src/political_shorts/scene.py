"""Scene Duration Controller.

No single frame is allowed to sit static for long. A *scene* runs ~1.5-3.5s.
Sentences longer than that are split further — at CLAUSE boundaries (comma,
connective, -는데 / -지만 / -며), at an event-change connector (그런데 / 하지만 /
결국 / 특히), or, only as a last resort, at the nearest word break — never on a
stopwatch alone. Each resulting scene is a self-contained thought with its own
caption and its own slice of narration (re-synthesised downstream).

`plan()` then attaches an explicit visual plan to every scene:
    background kind · hold-or-swap the key image · emphasis keywords ·
    camera move (zoom/pan) · transition into the next scene.
"""
from __future__ import annotations

import re
from typing import Any

from .textutil import clean_text

SCENE_MIN_S = 1.5
SCENE_MAX_S = 3.5
SCENE_TARGET_S = 2.6
_SEC_PER_CHAR = 1.0 / 7.0          # matches script_gen._KR_CHARS_PER_SEC
_PAD = 0.24
_MAX_SCENES = 44                   # hard safety cap on the render cost


def est_seconds(text: str) -> float:
    return len(text) / 7.0 + _PAD


# a scene that OPENS with one of these is a new beat -> the cut into it is hard
_EVENT_LEAD = re.compile(
    r"^(그런데|하지만|그러나|반면|특히|결국|즉|다만|문제는|여기서|이 때문에|이에|반대로|정작)\b"
)

# clause break points, best-first. Each matches the whitespace AT the break;
# we cut at m.end().
_CLAUSE_CUTS = (
    re.compile(r"(?<=[,])\s+"),
    re.compile(r"\s+(?=그런데|하지만|그러나|반면|특히|결국|다만|문제는|여기서|이에|정작)"),
    re.compile(r"(?<=는데)\s+"),
    re.compile(r"(?<=지만)\s+"),
    re.compile(r"(?<=면서)\s+"),
    re.compile(r"(?<=으며)\s+"),
    re.compile(r"(?<=하며)\s+"),
    re.compile(r"(?<=라며)\s+"),
    re.compile(r"(?<=고)\s+(?=[가-힣])"),
)

# words worth a RED emphasis + a punchier camera move
_EMPH = re.compile(
    r"\d[\d,.]*\s?%|\d[\d,.]*\s?(?:퍼센트|명|석|표|건|년|개월|억원?|조원?|만명|만 명|배|주년|위|차)"
    r"|과반|절반|첫|최초|사상\s?처음|역대|만장일치|전원|무산|부결|가결|통과|불발|철회|"
    r"급증|급감|사퇴|경질|전격|취임"
)


def emphasis_of(text: str) -> list[str]:
    seen: list[str] = []
    for m in _EMPH.finditer(text or ""):
        tok = m.group(0).strip()
        if tok and tok not in seen:
            seen.append(tok)
    return seen[:3]


# --------------------------------------------------------------------------- #
# 1) split a long line into <=SCENE_MAX_S clause-scenes
# --------------------------------------------------------------------------- #
def _boundaries(text: str) -> list[int]:
    b: set[int] = set()
    for rx in _CLAUSE_CUTS:
        for m in rx.finditer(text):
            if 6 <= m.end() <= len(text) - 6:
                b.add(m.end())
    return sorted(b)


def _split_long(text: str, max_s: float = SCENE_MAX_S) -> list[str]:
    text = text.strip()
    if est_seconds(text) <= max_s or len(text) < 14:
        return [text]
    bs = _boundaries(text)
    if bs:
        mid = len(text) / 2
        cut = min(bs, key=lambda c: abs(c - mid))
        a, b = text[:cut].strip(" ,·"), text[cut:].strip(" ,·")
        if a and b:
            return _split_long(a, max_s) + _split_long(b, max_s)
    # last resort — nearest word break to the middle, but never between a
    # number and the next number ("'5년 | 10년") or right after an opening quote
    mid = len(text) // 2
    cands = [m.start() for m in re.finditer(r"\s+", text) if 6 <= m.start() <= len(text) - 6]
    cands.sort(key=lambda c: abs(c - mid))
    for sp in cands:
        before, after = text[:sp].rstrip(), text[sp:].lstrip()
        if re.search(r"\d\s*[년월개원배%]?$", before) and re.match(r"[\"'(]?\d", after):
            continue
        if before.endswith(("\"", "'", "(", "‘", "“")):
            continue
        return _split_long(before, max_s) + _split_long(after, max_s)
    return [text]


def _merge_short(pieces: list[str], min_s: float = SCENE_MIN_S,
                 ceil: float = SCENE_MAX_S + 0.7) -> list[str]:
    """Fold a sub-`min_s` piece into the neighbour that keeps the result
    smallest and under `ceil` (prefer merging forward). A piece that can't
    merge without blowing the ceiling is left short — a brief scene still beats
    a long static one."""
    ps = [p.strip(" ,·") for p in pieces if p.strip(" ,·")]
    i = 0
    while i < len(ps) and len(ps) > 1:
        if est_seconds(ps[i]) >= min_s:
            i += 1
            continue
        opts: list[tuple[float, int, int]] = []
        if i + 1 < len(ps) and est_seconds(ps[i] + " " + ps[i + 1]) <= ceil:
            opts.append((est_seconds(ps[i] + " " + ps[i + 1]), i, i + 1))
        if i - 1 >= 0 and est_seconds(ps[i - 1] + " " + ps[i]) <= ceil:
            opts.append((est_seconds(ps[i - 1] + " " + ps[i]), i - 1, i))
        if not opts:
            i += 1
            continue
        _, a, b = min(opts)
        ps[a] = (ps[a].rstrip(" .·,") + " " + ps[b]).strip()
        del ps[b]
        i = max(0, a)
    return ps


def _tidy(piece: str) -> str:
    p = re.sub(r"\s+", " ", piece).strip(" ,·")
    # a scene ending on a bare noun-conjunction reads better with a soft stop
    if p and p[-1] not in ".?!" and not p.endswith(("다", "요", "죠", "까")):
        p = p  # keep connective endings (…했지만 / …밝히며) — natural mid-sentence pause
    return p


# --------------------------------------------------------------------------- #
# 2) camera move + transition, so consecutive scenes never look identical
# --------------------------------------------------------------------------- #
_ZOOM_ROT = ("in", "pan_right", "out", "pan_left")


def _assign_moves(scenes: list[dict[str, Any]]) -> None:
    prev_zoom = ""
    for i, sc in enumerate(scenes):
        S = sc["scene"]
        if S.get("_punch"):
            z = "punch"
        else:
            z = _ZOOM_ROT[i % len(_ZOOM_ROT)]
            if z == prev_zoom:
                z = _ZOOM_ROT[(i + 2) % len(_ZOOM_ROT)]
        if z == prev_zoom and z != "punch":
            z = "out" if prev_zoom != "out" else "in"
        S["zoom"] = z
        prev_zoom = z

        nxt = scenes[i + 1] if i + 1 < len(scenes) else None
        if nxt is None:
            S["transition"] = "fade"
        elif _EVENT_LEAD.match(clean_text(nxt.get("narration", ""))):
            S["transition"] = "cut"          # a beat change ("그런데 …") is a hard cut
        elif nxt.get("role") != sc.get("role"):
            S["transition"] = "fade"
        else:
            S["transition"] = "dissolve"


# --------------------------------------------------------------------------- #
# 3) public entry
# --------------------------------------------------------------------------- #
def plan(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """segments (already one-sentence-per-card) -> scenes, each 1.5-3.5s with a
    full visual plan under `scene`."""
    scenes: list[dict[str, Any]] = []
    for seg in segments:
        role = seg.get("role", "")
        nar = clean_text(seg.get("narration", ""))

        if not nar:
            sc = dict(seg)
            sc["scene"] = {"emphasis": emphasis_of(seg.get("caption", "")),
                           "hold_media": False, "min_s": SCENE_MIN_S, "max_s": SCENE_MIN_S}
            scenes.append(sc)
            continue

        # the hook is ONE punch beat — never clause-split it, even at ~4s.
        if role == "hook":
            sc = dict(seg)
            sc["scene"] = {"emphasis": emphasis_of(nar), "hold_media": False,
                           "min_s": SCENE_MIN_S, "max_s": SCENE_MAX_S,
                           "_punch": bool(_EMPH.match(nar))}
            sc["sub"] = 0
            scenes.append(sc)
            continue

        # factcheck: the on-screen 사실/주장/전망 TABLE stays put, but the spoken
        # explanation ("확인된 사실은 … 이게 무슨 뜻이냐면 …") is long — split it into
        # a FEW longer beats (~4.5s, at most 4) so the same table isn't re-cut a
        # dozen times.
        cap_s = 4.6 if role == "factcheck" else SCENE_MAX_S
        pieces = _merge_short(_split_long(nar, cap_s), ceil=cap_s + 0.7) or [nar]
        if role == "factcheck" and len(pieces) > 4:
            head = pieces[:3]
            head.append(" ".join(p.rstrip(" .·,") for p in pieces[3:]).strip())
            pieces = head
        for j, piece in enumerate(pieces):
            piece = _tidy(piece)
            if not piece:
                continue
            sc = dict(seg)
            sc["narration"] = piece
            # factcheck keeps its table caption ("팩트체크"); every other card's
            # caption IS the spoken line (read-along lockstep)
            if role != "factcheck":
                sc["caption"] = piece
            emph = emphasis_of(piece)
            sc["scene"] = {
                "emphasis": emph,
                "hold_media": j > 0,          # sub-scene keeps the same image
                "min_s": SCENE_MIN_S, "max_s": SCENE_MAX_S,
                # a scene that OPENS on a figure / decisive word gets the punch
                "_punch": bool(emph) and bool(_EMPH.match(piece)),
            }
            if j:                       # only the first scene of a beat is chipped
                sc["kicker"] = ""
                sc.pop("num", None)
            sc["sub"] = j
            scenes.append(sc)
            if len(scenes) >= _MAX_SCENES:
                break
        if len(scenes) >= _MAX_SCENES:
            break

    _assign_moves(scenes)
    for sc in scenes:
        sc["scene"].pop("_punch", None)
    return scenes
