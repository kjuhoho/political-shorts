"""The YouTube title — one short line, curiosity first, no trailing tag."""
from political_shorts.metadata import _cut_words, _title


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
    # channel feedback: the curiosity line leads, and "#shorts" is no longer glued on
    assert t == "연장할까? 실거주 유예 내년까지"
    assert "|" not in t and '"' not in t and "#shorts" not in t


def test_title_leads_with_the_question_line_and_stays_short():
    script = {"headline": "김승원 사퇴", "title": ["김승원 법무장관 후보자", "오늘 국회서 직접 입 열까?"]}
    t = _title(script, "09월 19일", "punchy")
    assert t.startswith("오늘 국회서 직접 입 열까?") and "김승원" in t
    assert len(t) <= 40
    # no question line -> order is kept
    assert _title({"headline": "x", "title": ["예산안 통과", "국회 본회의"]}, "09월 17일", "punchy") \
        == "예산안 통과 국회 본회의"


def test_title_falls_back_to_date_headline_when_no_onscreen_title():
    script = {"headline": "예산안 국회 통과", "title": []}
    t = _title(script, "09월 17일", "punchy")
    assert t == "[09월 17일 정치] 예산안 국회 통과"


def test_title_neutral_style_ignores_onscreen_title():
    script = {"headline": "예산안 국회 통과", "title": ["압도적", "통과"]}
    t = _title(script, "09월 17일", "neutral")
    assert t == "[09월 17일 정치] 예산안 국회 통과"


def test_cut_words_never_cuts_mid_word():
    assert _cut_words("가나다 라마바 사아자", 8) == "가나다 라마바"
    assert _cut_words("짧은 제목", 40) == "짧은 제목"
    assert not _cut_words("아주긴한단어만있는제목입니다", 6).endswith(" ")
