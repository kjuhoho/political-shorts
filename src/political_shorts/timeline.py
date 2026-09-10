"""TIMELINE ENGINE — the single source of truth for scene / audio / subtitle sync.

After TTS, `build()` turns the planned scenes + their measured narration lengths
into one contiguous timeline: every scene gets a rendered `clip_s`, an xfade
overlap with the next scene, and an absolute `start`/`end` on the finished
video. The renderer consumes it (no more per-clip duration math scattered in
`video.render_video`) and it is written into the metadata sidecar so the
quality checker can verify audio↔subtitle↔scene alignment against real numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# cross-fade seconds per transition kind (mirrors video._XF)
_XF_S = {"cut": 0.03, "dissolve": 0.14, "fade": 0.30, "slide": 0.20, "zoom": 0.18, "push": 0.20}
_TAIL = 0.16            # tiny hold after the voice before the cut


@dataclass
class TScene:
    i: int
    role: str
    scene_type: str
    caption: str
    narration: str
    clip_s: float              # rendered clip length (before the xfade overlap)
    audio_s: float             # measured TTS length (0.0 = silent scene)
    read_s: float              # subtitle read-time requirement
    transition: str            # transition INTO the next scene ("" for the last)
    xfade_s: float = 0.0       # overlap with the next scene
    start: float = 0.0         # absolute start on the finished video
    end: float = 0.0           # absolute end (== next scene's start)
    source: dict = field(default_factory=dict)


@dataclass
class Timeline:
    thumb_s: float
    scenes: list[TScene]
    total_s: float

    def to_list(self) -> list[dict[str, Any]]:
        return [
            {"i": s.i, "start": round(s.start, 2), "end": round(s.end, 2),
             "dur": round(s.end - s.start, 2), "role": s.role, "type": s.scene_type,
             "caption": s.caption, "audio_s": round(s.audio_s, 2),
             "read_s": round(s.read_s, 2), "transition": s.transition,
             "xfade_s": round(s.xfade_s, 3), "source": s.source}
            for s in self.scenes
        ]

    @property
    def clip_durs(self) -> list[float]:
        return [s.clip_s for s in self.scenes]

    @property
    def transitions(self) -> list[str]:
        return [s.transition for s in self.scenes[:-1]]


def _xf(kind: str, a: float, b: float) -> float:
    return max(0.03, min(_XF_S.get(kind, 0.14), a * 0.45, b * 0.45))


def build(segments: list[dict[str, Any]], narrations: list[Any], cfg: Any,
          *, thumb_s: float = 0.0) -> Timeline:
    from .tts import estimate_caption_seconds

    scenes: list[TScene] = []
    for i, seg in enumerate(segments):
        sc = seg.get("scene", {}) or {}
        nar = narrations[i] if i < len(narrations) else None
        audio_s = (nar.duration_s if (nar and getattr(nar, "wav_path", None)
                                      and nar.duration_s > 0.3) else 0.0)
        if audio_s:
            base = audio_s + _TAIL
        elif seg.get("narration"):
            base = estimate_caption_seconds(seg.get("caption", ""), cfg)
        else:
            base = 1.5
        read_s = float(sc.get("min_read_s", 0.0))
        clip_s = max(base, read_s, float(sc.get("min_s", 0.0)))
        src = seg.get("source")
        src = {"name": src} if isinstance(src, str) else (dict(src) if isinstance(src, dict) else {})
        scenes.append(TScene(
            i=i, role=seg.get("role", ""),
            scene_type=str(seg.get("scene_type") or seg.get("role", "")),
            caption=seg.get("caption", ""), narration=seg.get("narration", ""),
            clip_s=round(clip_s, 3), audio_s=round(audio_s, 3), read_s=round(read_s, 3),
            transition=str(sc.get("transition", "dissolve")),
            source=src,
        ))

    # mirror video._assemble's offset math so `start`/`end` match the render:
    #   durs = [thumb?, clip0, clip1, …]; acc = durs[0];
    #   for k>=1: t_k = xf; scene_k.start = acc - t_k; acc += durs[k] - t_k
    durs = ([thumb_s] if thumb_s else []) + [s.clip_s for s in scenes]
    kinds = (["dissolve"] if thumb_s else []) + [s.transition for s in scenes[:-1]]
    if not durs:
        return Timeline(thumb_s=thumb_s, scenes=[], total_s=thumb_s)

    starts = [0.0]
    acc = durs[0]
    xfades = [0.0]
    for k in range(1, len(durs)):
        t_k = _xf(kinds[k - 1], durs[k - 1], durs[k])
        starts.append(round(acc - t_k, 3))
        xfades.append(round(t_k, 3))
        acc += durs[k] - t_k
    total = round(acc, 3)

    off = 1 if thumb_s else 0
    for j, s in enumerate(scenes):
        s.start = starts[j + off]
        s.end = starts[j + off + 1] if (j + off + 1) < len(starts) else total
        s.xfade_s = xfades[j + off + 1] if (j + off + 1) < len(xfades) else 0.0
    return Timeline(thumb_s=thumb_s, scenes=scenes, total_s=total)


def retime(tl: Timeline, measured_scene_durs: list[float]) -> Timeline:
    """Recompute start/end/total from the clips' ACTUALLY MEASURED durations
    (ffprobe) so the timeline matches the rendered file exactly, whatever
    ffmpeg did with a short b-roll loop etc."""
    if len(measured_scene_durs) != len(tl.scenes):
        return tl
    for s, d in zip(tl.scenes, measured_scene_durs):
        if d and d > 0.2:
            s.clip_s = round(float(d), 3)
    durs = ([tl.thumb_s] if tl.thumb_s else []) + [s.clip_s for s in tl.scenes]
    kinds = (["dissolve"] if tl.thumb_s else []) + [s.transition for s in tl.scenes[:-1]]
    if not durs:
        return tl
    starts = [0.0]
    acc = durs[0]
    xfades = [0.0]
    for k in range(1, len(durs)):
        t_k = _xf(kinds[k - 1], durs[k - 1], durs[k])
        starts.append(round(acc - t_k, 3))
        xfades.append(round(t_k, 3))
        acc += durs[k] - t_k
    total = round(acc, 3)
    off = 1 if tl.thumb_s else 0
    for j, s in enumerate(tl.scenes):
        s.start = starts[j + off]
        s.end = starts[j + off + 1] if (j + off + 1) < len(starts) else total
        s.xfade_s = xfades[j + off + 1] if (j + off + 1) < len(xfades) else 0.0
    tl.total_s = total
    return tl
