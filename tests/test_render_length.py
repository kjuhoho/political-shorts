"""Incident 2026-09-23: a 59.9s story shipped as a 33.5s file.

The xfade join produced video=33.5s audio=59.9s. Every length guard read the CONTAINER duration, which reports
the longer stream (59.9s), so nothing was flagged; the music mix's `-shortest` then cut the whole file to 33.5s.
These tests pin the three places that let it through.
"""
import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from political_shorts import pipeline, quality, video
from political_shorts.config import settings


class _Probe:
    """Stand-in for ffprobe: answers with the stream/format durations of `files`."""

    def __init__(self, files: dict[str, dict], calls: list | None = None):
        self.files, self.calls = files, calls if calls is not None else []

    def run(self, cmd, capture_output=True, text=True, timeout=None, **kw):
        path = Path(cmd[-1]).name
        self.calls.append(path)
        st = self.files.get(path, {})
        streams = [{"codec_type": k, "duration": f"{v}"} for k, v in st.items()]
        longest = max(st.values()) if st else 0.0
        out = json.dumps({"streams": streams, "format": {"duration": f"{longest}"}})
        return subprocess.CompletedProcess(cmd, 0, out, "")


def _probe_env(monkeypatch, files, module=video):
    p = _Probe(files)
    monkeypatch.setattr(module.subprocess, "run", p.run)
    if module is video:
        monkeypatch.setattr(video, "_ffprobe_bin", lambda cfg: "ffprobe")
    else:
        monkeypatch.setattr(module.shutil, "which", lambda x: "ffprobe")
    return p


TRUNCATED = {"joined.mp4": {"video": 33.5, "audio": 59.9}}


def test_the_length_of_a_file_is_its_shorter_stream_not_the_container(monkeypatch):
    _probe_env(monkeypatch, TRUNCATED)
    assert video.playable_duration(Path("joined.mp4")) == 33.5
    assert video.stream_seconds(Path("joined.mp4")) == {"video": 33.5, "audio": 59.9}


def test_quality_measures_the_same_way(monkeypatch):
    _probe_env(monkeypatch, TRUNCATED, module=quality)
    cfg = dataclasses.replace(settings, ffmpeg_path="ffmpeg")
    assert quality._probe_duration(Path("joined.mp4"), cfg) == 33.5


def test_a_join_whose_video_ran_out_early_is_redone_as_a_plain_concat(monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(video, "_run", lambda cmd, **kw: ran.append(cmd))
    monkeypatch.setattr(video, "playable_duration", lambda p, cfg=None: 33.5)      # the broken join
    monkeypatch.setattr(video, "_concat", lambda ffmpeg, clips, out, fps: ran.append(["CONCAT", len(clips)]))
    clips = [Path(f"c{i}.mp4") for i in range(4)]
    removed = video._assemble("ffmpeg", clips, [15.0] * 4, Path("joined.mp4"),
                              dataclasses.replace(settings, video_xfade=True, video_fps=30))
    assert ["CONCAT", 4] in ran                      # fell back instead of shipping the short join
    assert removed == 0.0


def test_mixing_music_never_shortens_the_video(monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(video, "_run", lambda cmd, **kw: ran.append(list(cmd)))
    lengths = {"narration.mp4": 59.9, "out.mp4": 33.5}                             # -shortest cut the file
    monkeypatch.setattr(video, "playable_duration", lambda p, cfg=None: lengths[Path(p).name])
    monkeypatch.setattr(video, "_stream_durations", lambda p, cfg=None: "video=33.5s audio=59.9s")
    video._mix_bgm("ffmpeg", Path("narration.mp4"), Path("bgm.mp3"), 59.9, Path("out.mp4"), settings)
    assert len(ran) == 2                                                           # mixed again ...
    assert "-shortest" in ran[0] and "-shortest" not in ran[1]                     # ... without the cut


def test_a_mix_that_keeps_the_length_is_not_redone(monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(video, "_run", lambda cmd, **kw: ran.append(list(cmd)))
    monkeypatch.setattr(video, "playable_duration", lambda p, cfg=None: 59.9)
    video._mix_bgm("ffmpeg", Path("narration.mp4"), Path("bgm.mp3"), 59.9, Path("out.mp4"), settings)
    assert len(ran) == 1


class _QR:
    def __init__(self, code="", severity="critical"):
        self.score, self.band, self.publishable = 92, "PASS", True
        self.issues = [{"code": code, "severity": severity}] if code else []
        self.political_safety_ok = True


@pytest.mark.parametrize("code,defect", [("duration-mismatch", True), ("sub-sync", True), ("static-span", False)])
def test_a_broken_file_is_held_even_with_a_publishable_score(code, defect):
    assert pipeline._render_defect(_QR(code)) is defect
    # the normal (non-relax) rule now holds on it, the way the guarantee tier always did
    src = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "hold_publish = (not qr.publishable) or bool(_viol) or _render_defect(qr)" in src
