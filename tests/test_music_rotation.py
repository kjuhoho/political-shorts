"""Five audited videos (2026-09-30) all used the same music bed, which is half of why two of them in a row
feel like the same video. Tracks rotate — but only ones that bring their own credit line."""
import dataclasses
from pathlib import Path

from political_shorts import music
from political_shorts.config import settings


def _library(tmp_path, names):
    d = tmp_path / "bgm" / "tracks"
    d.mkdir(parents=True)
    for n in names:
        (d / f"{n}.mp3").write_bytes(b"x")
        (d / f"{n}.credit.txt").write_text(f'음악: "{n}" — Artist (CC BY 4.0)\n', encoding="utf-8")
    return dataclasses.replace(settings, bgm_path=str(tmp_path / "bgm" / "bed.mp3"), bgm_credit="old credit")


def test_consecutive_videos_get_different_beds(tmp_path):
    cfg = _library(tmp_path, ["aa", "bb", "cc"])
    picked = [music.pick(cfg, seed)[0].stem for seed in (1, 2, 3, 4)]
    assert picked == ["bb", "cc", "aa", "bb"]
    assert len(set(picked[:3])) == 3


def test_the_credit_follows_the_track(tmp_path):
    cfg = _library(tmp_path, ["aa", "bb"])
    track, credit = music.pick(cfg, 1)
    assert track.stem == "bb" and credit == '음악: "bb" — Artist (CC BY 4.0)'


def test_a_track_without_its_credit_is_not_used(tmp_path):
    cfg = _library(tmp_path, ["aa"])
    (cfg_dir := Path(cfg.bgm_path).parent / "tracks").joinpath("nocredit.mp3").write_bytes(b"x")
    assert [p.stem for p, _ in music.credited_tracks(cfg)] == ["aa"]
    assert cfg_dir.exists()


def test_with_no_track_library_nothing_changes(tmp_path):
    cfg = dataclasses.replace(settings, bgm_path=str(tmp_path / "bed.mp3"), bgm_credit="음악: 기존 크레딧")
    track, credit = music.pick(cfg, 7)
    assert track == Path(cfg.bgm_path) and credit == "음악: 기존 크레딧"


def test_the_description_credits_the_track_that_was_used(tmp_path):
    from political_shorts.metadata import build_metadata

    cfg = dataclasses.replace(settings, bgm_enabled=True, bgm_credit="음악: 예전 트랙")
    script = {"headline": "예산안 통과", "title": ["예산안 통과"], "segments": [], "sources": [],
              "source_text": "", "bgm_credit": '음악: "bb" — Artist (CC BY 4.0)'}
    desc = build_metadata(script, {"passed": True, "warnings": []}, tmp_path / "x.mp4", cfg)["description"]
    assert '음악: "bb" — Artist (CC BY 4.0)' in desc and "예전 트랙" not in desc
