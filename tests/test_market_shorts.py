"""scripts/market_shorts.py — the outside numbers the strategist compares us to.

Our own view counts are pinned, so the only usable evidence about titles and
length comes from videos that actually travelled. These tests pin the two
measurements that evidence rests on.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import market_shorts as MS  # noqa: E402


def test_lead_shape_reads_the_first_twelve_characters_like_a_thumb_does():
    """2026-09-30, n=144 Shorts above 100k views: 44 opened on a name, 36 on a
    verbatim quote, 18 more carried a name inside the first 12 characters."""
    assert MS.lead_shape("이재명 김현지 동거 유동규 충격 발언 #이재명#shorts") == "name"
    assert MS.lead_shape('"이게 진짜 우연이라고?" 형수 기자회견 직후 이재명 표정') == "quote"
    assert MS.lead_shape("“누구 이름입니까?” 조경식 입에서 이재명 나오자") == "quote"
    # a channel tag in front must not hide the name behind it
    assert MS.lead_shape("[웃음주의] 한동훈 당황하다#한동훈") == "name"
    assert MS.lead_shape("李, 농지조사에 담긴 충격적 의미") == "name"
    assert MS.lead_shape("청와대 참모진 개편 본격화") == "name"          # the president counts
    # ours: the name is there, but past the 12th character, behind a stub
    assert MS.lead_shape("언제가 가장 위험할까? 명절 연휴 빈집털이#shorts") == "other"
    assert MS.lead_shape("절제 당부한 까닭은? 김민석 유시민#shorts") == "other"


def test_length_buckets_match_the_ones_the_baseline_was_measured_in():
    """Baseline: 57 of 144 (40%) ran 51-70s; median 60s. A bucket edge that
    drifts makes the next snapshot incomparable to this one."""
    assert MS.length_bucket(18) == "0-35s"
    assert MS.length_bucket(50) == "36-50s"
    assert MS.length_bucket(60) == "51-70s"
    assert MS.length_bucket(90) == "71-90s"
    assert MS.length_bucket(109) == "91-180s"


def test_summary_reports_shares_with_n_and_never_hides_an_empty_search():
    rows = [{"lead_shape": "name", "has_question_mark": False, "seconds": 60, "views": 500_000,
             "likes": 20_000, "comments": 900},
            {"lead_shape": "quote", "has_question_mark": True, "seconds": 41, "views": 300_000,
             "likes": 9_000, "comments": 400},
            {"lead_shape": "other", "has_question_mark": False, "seconds": 102, "views": 150_000,
             "likes": 3_000, "comments": 100}]
    s = MS.summarise(rows)
    assert s["n"] == 3 and s["named_or_quoted_share"] == 0.67
    assert s["question_mark_share"] == 0.33 and s["median_seconds"] == 60
    assert s["by_length"]["51-70s"] == 1
    assert MS.summarise([])["n"] == 0 and "note" in MS.summarise([])


def test_iso_durations():
    assert MS.iso_seconds("PT1M49S") == 109
    assert MS.iso_seconds("PT18S") == 18
    assert MS.iso_seconds("") == 0
