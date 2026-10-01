"""The YouTube title — one short line, curiosity first, "#shorts" attached at the end."""
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
    assert t == "연장할까? 실거주 유예 내년까지#shorts"
    assert "|" not in t and '"' not in t and t.endswith("#shorts")


def test_title_leads_with_the_name_when_there_is_one_question_second():
    """Measured 2026-09-30 on the 144 Korean-politics Shorts that passed 100k
    views in the previous 30 days: 98 (68%) put a politician's name or their
    verbatim words inside the first 12 characters, and only 35 (24%) contain a
    "?" at all — of the top 30, 4 end on one. Ours were the inverse: 32 of 48
    opened on a contentless stub ("무슨 일일까요?") and buried the name."""
    script = {"headline": "김승원 사퇴", "title": ["김승원 법무장관 후보자", "오늘 국회서 직접 입 열까?"]}
    t = _title(script, "09월 19일", "punchy")
    assert t.startswith("김승원") and t.endswith("오늘 국회서 직접 입 열까?#shorts")
    assert len(t) <= 40 + len("#shorts")
    # the president counts as a name even when no politician is spelled out
    assert _title({"headline": "x", "title": ["청와대 참모진 개편", "누가 남을까?"]},
                  "09월 27일", "punchy") == "청와대 참모진 개편, 누가 남을까?#shorts"
    # no question line -> order is kept
    assert _title({"headline": "x", "title": ["예산안 통과", "국회 본회의"]}, "09월 17일", "punchy") \
        == "예산안 통과 국회 본회의#shorts"


def test_faceless_title_keeps_curiosity_in_front():
    """Only a name earns the front of the title. A procedure story has nobody to
    lead with, so the question still goes first rather than opening on a
    lifeless noun phrase. (명절 연휴 빈집털이: 262 views, the worst live video
    of 2026-09 — the fix for that one is story choice, not word order.)"""
    script = {"headline": "명절 절도", "title": ["명절 연휴 빈집털이", "언제가 가장 위험할까?"]}
    assert _title(script, "09월 26일", "punchy") == "언제가 가장 위험할까? 명절 연휴 빈집털이#shorts"


def test_title_falls_back_to_date_headline_when_no_onscreen_title():
    script = {"headline": "예산안 국회 통과", "title": []}
    t = _title(script, "09월 17일", "punchy")
    assert t == "[09월 17일 정치] 예산안 국회 통과#shorts"


def test_title_neutral_style_ignores_onscreen_title():
    script = {"headline": "예산안 국회 통과", "title": ["압도적", "통과"]}
    t = _title(script, "09월 17일", "neutral")
    assert t == "[09월 17일 정치] 예산안 국회 통과#shorts"


def test_cut_words_never_cuts_mid_word():
    assert _cut_words("가나다 라마바 사아자", 8) == "가나다 라마바"
    assert _cut_words("짧은 제목", 40) == "짧은 제목"
    assert not _cut_words("아주긴한단어만있는제목입니다", 6).endswith(" ")


def test_incident_our_own_balance_note_never_reaches_the_description():
    """2026-09-28 shipped '■ 균형 관련 참고 / - 한쪽 정당만 언급됨(국민의힘) — 상대측 반응 보강 권장' to viewers.
    It is a production note: it belongs in the sidecar and the run log, not under the video."""
    from pathlib import Path

    from political_shorts.metadata import build_metadata

    script = {"headline": "예산안 국회 통과", "title": ["예산안 통과"], "segments": [], "sources": [],
              "source_text": "", "disclaimer": "공개 보도를 정리한 자동 제작 영상입니다."}
    safety = {"passed": True, "warnings": ["한쪽 정당만 언급됨(국민의힘) — 상대측 반응 보강 권장"]}
    meta = build_metadata(script, safety, Path("x.mp4"))
    assert "균형 관련 참고" not in meta["description"]
    assert "상대측 반응 보강" not in meta["description"]
    assert meta["safety"]["warnings"] == safety["warnings"]        # still recorded for us
