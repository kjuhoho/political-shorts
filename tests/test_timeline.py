"""TIMELINE ENGINE — contiguous start/end from measured audio + read-time, in
the same offset math the renderer uses."""
from dataclasses import dataclass

from political_shorts import timeline as T


@dataclass
class _Nar:
    wav_path: str | None
    duration_s: float


class _Cfg:
    pass


def _segs(*specs):
    out = []
    for i, (role, cap, tx, mr) in enumerate(specs):
        out.append({"role": role, "narration": cap, "caption": cap,
                    "scene": {"transition": tx, "min_read_s": mr, "min_s": 1.5}})
    return out


def test_timeline_is_contiguous_and_matches_the_offset_math():
    segs = _segs(("hook", "첫 문장입니다.", "fade", 3.0),
                 ("what", "두 번째 문장은 조금 더 깁니다.", "dissolve", 4.0),
                 ("what", "세 번째.", "cut", 2.0),
                 ("outro", "마지막 문장입니다.", "fade", 2.5))
    nars = [_Nar("a.wav", 2.2), _Nar("b.wav", 3.1), _Nar("c.wav", 1.0), _Nar("d.wav", 2.0)]
    tl = T.build(segs, nars, _Cfg(), thumb_s=1.6)

    assert len(tl.scenes) == 4
    for a, b in zip(tl.scenes, tl.scenes[1:]):
        assert abs(b.start - a.end) < 1e-6            # no gap, no overlap in the model
    assert tl.scenes[0].start > 0                     # after the poster
    assert abs(tl.scenes[-1].end - tl.total_s) < 1e-6
    # each clip is at least the read-time
    for s, (_, _, _, mr) in zip(tl.scenes, [
        ("hook", "", "", 3.0), ("what", "", "", 4.0), ("what", "", "", 2.0), ("outro", "", "", 2.5)]):
        assert s.clip_s >= mr - 1e-6


def test_silent_scene_gets_a_floor():
    segs = _segs(("outro", "구독과 좋아요", "fade", 0.0))
    tl = T.build(segs, [_Nar(None, 0.0)], _Cfg(), thumb_s=0.0)
    assert tl.scenes[0].clip_s >= 1.5


def test_to_list_shape():
    segs = _segs(("hook", "문장", "fade", 2.0))
    tl = T.build(segs, [_Nar("a.wav", 1.8)], _Cfg())
    row = tl.to_list()[0]
    assert {"i", "start", "end", "dur", "role", "caption", "audio_s", "transition"} <= row.keys()
