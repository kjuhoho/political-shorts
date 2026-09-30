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


def test_a_video_that_never_reached_the_feed_is_separated_not_averaged_in():
    """2026-09-30 incident: four Shorts sat at 4/5/6/10 views, 26x below the
    next-worst video. Three of them happened to land on a Wednesday, and that
    alone dragged the Wednesday bucket to a median of 698 while every other
    weekday sat near 1,100 — a "Wednesday is bad" finding that does not exist
    (Wednesday is 1,058 without them). Zero-distribution videos are a separate
    event and never get averaged with videos the feed actually carried."""
    rows = [{"views": 5, "weekday": "수"}, {"views": 6, "weekday": "수"},
            {"views": 1100, "weekday": "수"}, {"views": 1016, "weekday": "수"},
            {"views": 1200, "weekday": "목"}]
    live = [r for r in rows if r["views"] >= CS.NO_DISTRIBUTION]
    out = CS._bucket(live, lambda r: r["weekday"])
    wed = next(b for b in out if b["bucket"] == "수")
    assert wed["n"] == 2 and wed["median_views"] == 1058.0     # not 553, not 698


def test_pinned_views_are_flagged_so_no_bucket_gets_read_as_a_finding():
    """Every 2026-09 bucket — length, frame, weekday, slot — landed within a few
    percent of 1,100 views, and the best video of the month was 1.31x the median.
    A feed that is actually promoting is heavy-tailed, so that flatness means the
    view column carries no signal at all and must say so out loud."""
    pinned = [{"views": v, "likes": 20, "comments": 0} for v in
              (989, 1003, 1019, 1035, 1048, 1058, 1065, 1085, 1093, 1103, 1112, 1140, 1527)]
    d = CS._distribution(pinned)
    assert d["pinned"] and d["max_over_median"] < 2.0 and d["share_in_band"] >= 0.6
    assert d["zero_comment_share"] == 1.0
    # a channel with real reach: one video runs away from the pack -> not pinned
    spread = [{"views": v, "likes": 20, "comments": 3} for v in
              (400, 700, 900, 1100, 1200, 1400, 9000, 42000, 310000)]
    assert not CS._distribution(spread)["pinned"]


def test_too_few_videos_says_so_instead_of_guessing():
    assert CS._distribution([{"views": 1, "likes": 0, "comments": 0}] * 3)["pinned"] is False
    assert "too few" in CS._distribution([{"views": 1, "likes": 0, "comments": 0}] * 3)["note"]
