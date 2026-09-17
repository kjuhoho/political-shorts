"""The YouTube title — one clean line, not a stitched-together mess."""
from political_shorts.metadata import _title


def test_title_is_one_clean_line_no_headline_quote_tail():
    # a real user complaint: '실거주 유예 내년까지 연장할까? | 여당 "내년
    # 까지 연장을"#shorts' — the on-screen title plus a raw, quote-laden
    # headline stitched on with " | " reads as two unrelated things and is
    # hard to parse on a phone.
    script = {
        "headline": '여당 "내년까지 연장을"',
        "title": ["실거주 유예 내년까지", "연장할까?"],
    }
    t = _title(script, "09월 17일", "punchy")
    assert t == "실거주 유예 내년까지 연장할까?#shorts"
    assert "|" not in t
    assert '"' not in t


def test_title_falls_back_to_date_headline_when_no_onscreen_title():
    script = {"headline": "예산안 국회 통과", "title": []}
    t = _title(script, "09월 17일", "punchy")
    assert t == "[09월 17일 정치] 예산안 국회 통과#shorts"


def test_title_neutral_style_ignores_onscreen_title():
    script = {"headline": "예산안 국회 통과", "title": ["압도적", "통과"]}
    t = _title(script, "09월 17일", "neutral")
    assert t == "[09월 17일 정치] 예산안 국회 통과#shorts"
