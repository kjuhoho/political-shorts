"""scripts/channel_stats.py — the numbers the channel-strategist agent reasons from."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import channel_stats as CS  # noqa: E402

KST = timezone(timedelta(hours=9))


def _ts(days_ago: float, hour: int) -> float:
    d = (datetime.now(KST) - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return d.timestamp()


HISTORY = [
    {"published_ts": _ts(1, 9), "headline": "A", "frame": "scandal", "actor": "김A", "remote_id": "v1"},
    {"published_ts": _ts(1, 9), "headline": "A2 (같은 영상에 흡수)", "frame": "scandal", "actor": "", "remote_id": "v1"},
    {"published_ts": _ts(1, 16), "headline": "B", "frame": "clash", "actor": "이B", "remote_id": "v2"},
    {"published_ts": _ts(40, 9), "headline": "old", "frame": "vote", "actor": "", "remote_id": "vold"},
]


def test_one_row_per_video_with_slot_and_absorbed_count():
    rows = CS.our_videos(HISTORY, days=30)
    assert [r["video_id"] for r in rows] == ["v2", "v1"]          # newest first, the 40-day-old one dropped
    v1 = next(r for r in rows if r["video_id"] == "v1")
    assert v1["absorbed"] == 2 and v1["slot"] == "morning" and v1["frame"] == "scandal"
    assert next(r for r in rows if r["video_id"] == "v2")["slot"] == "evening"


def test_iso_durations():
    assert CS._iso_seconds("PT1M23S") == 83
    assert CS._iso_seconds("PT45S") == 45
    assert CS._iso_seconds("PT1H2M3S") == 3723
    assert CS._iso_seconds("") == 0


def test_title_shapes():
    assert CS.title_shape("왜 사퇴했나? 강훈식#shorts") == "question"
    assert CS.title_shape("정치 이슈 4가지 정리") == "number"
    assert CS.title_shape("강훈식 비서실장 사의") == "statement"


def test_small_buckets_are_marked_weak_and_never_hidden():
    rows = [{"views": 100, "frame": "scandal"}, {"views": 300, "frame": "scandal"},
            {"views": 50, "frame": "clash"}]
    out = CS._bucket(rows, lambda r: r["frame"])
    assert [b["bucket"] for b in out] == ["scandal", "clash"]      # ordered by median views
    assert all(b["weak"] for b in out)                             # n=2 and n=1: nothing to conclude
    assert out[0]["n"] == 2 and out[0]["median_views"] == 200.0


def test_report_joins_public_counts_and_flags_videos_the_api_does_not_return(monkeypatch, tmp_path):
    import json

    import political_shorts.config as C

    (tmp_path / "topic_history.json").write_text(json.dumps(HISTORY, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(CS, "settings", type("S", (), {"youtube_api_key": "k", "data_dir": tmp_path})())
    monkeypatch.setattr(CS, "fetch_videos", lambda ids, key: {
        "v1": {"title": "왜 그랬을까? A#shorts", "published_at": "", "seconds": 38, "views": 900,
               "likes": 20, "comments": 5},
    })
    rep = CS.report(days=30)
    assert rep["totals"]["videos"] == 1 and rep["totals"]["views"] == 900
    assert rep["missing_from_api"] == ["v2"]                        # made private by hand, say
    v1 = next(r for r in rep["videos"] if r["video_id"] == "v1")
    assert v1["title_shape"] == "question" and v1["engagement"] == round(25 / 900, 4)
    assert [b["bucket"] for b in rep["by_length"]] == ["≤40s"]
    assert C is not None


def test_no_api_key_says_so_instead_of_pretending(monkeypatch, tmp_path):
    import json

    (tmp_path / "topic_history.json").write_text(json.dumps(HISTORY, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(CS, "settings", type("S", (), {"youtube_api_key": "", "data_dir": tmp_path})())
    rep = CS.report(days=30)
    assert "YOUTUBE_API_KEY" in rep["error"] and rep["videos"]
