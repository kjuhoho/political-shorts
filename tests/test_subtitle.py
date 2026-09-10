"""Subtitle text.

`readable_chunks` = the FULL-SCRIPT subtitle (same words as the voice, split
into clean readable chunks). `beat_caption` / `topic_label` = the SHORT
compressed phrase for the top bar only."""
from political_shorts.subtitle import (
    beat_caption, emphasis_terms, readable_chunks, read_seconds,
    topic_label, valid_llm_subtitle,
)


def test_readable_chunks_keep_every_word():
    s = "이재명 대통령이 임기 중 연임 개헌 논의를 꺼내면서, 정치권에서 논란이 커지고 있습니다."
    chunks = readable_chunks(s)
    assert len(chunks) >= 2
    assert "".join(c.replace(" ", "") for c in chunks) == s.replace(" ", "")


def test_readable_chunks_break_at_a_clean_boundary_not_mid_word():
    s = "정부가 오늘 새로운 개헌 논의를 공식적으로 시작했다고 대변인이 밝혔습니다."
    for c in readable_chunks(s):
        assert not c.rstrip().endswith(("공식적으", "시작했", "대변인이"))
        assert len(c) <= 46


def test_short_line_is_one_chunk():
    assert readable_chunks("3일 사퇴했습니다.") == ["3일 사퇴했습니다."]


def test_no_clean_seam_stays_whole():
    s = "대통령실의 정책을 총괄하는 핵심 참모가 물러났습니다."
    assert readable_chunks(s) == [s]      # rather than cut "물러났" mid-word


def test_read_seconds_has_a_floor():
    assert read_seconds("짧다") >= 1.6
    assert read_seconds("아" * 40) > read_seconds("아" * 10)


def test_caption_is_not_the_full_narration():
    nar = ("김승원은 대통령의 정책을 총괄하는 대통령실의 핵심 참모입니다. "
           "취임 두 달 만인 3일 사퇴했습니다.")
    cap = beat_caption(nar, "summary")
    assert cap != nar
    assert len(cap) <= 28
    assert "사퇴" in cap and "김승원" in cap        # the point survives


def test_caption_drops_the_reporting_tail():
    cap = beat_caption("국민의힘은 인사 검증이 부실했다고 지적했습니다.", "sides")
    assert "지적했습니다" not in cap
    assert "국민의힘" in cap


def test_caption_never_ends_on_a_dangling_particle():
    cap = beat_caption("이재명 대통령이 개헌 필요성을 언급하면서 국회의 협조를 요청했습니다.", "what")
    assert not cap.rstrip().endswith(("을", "를", "이", "가", "은", "는", "에", "로", "며", "고"))


def test_hook_caption_stays_close_to_the_hook():
    cap = beat_caption("김승원, 취임 두 달 만에 물러난 건 정치권에서도 흔치 않은 일입니다.", "hook")
    assert "김승원" in cap and "취임 두 달" in cap and len(cap) <= 40


def test_emphasis_terms_are_figures_and_decisive_words_only():
    terms = emphasis_terms("지지율이 45%로, 8%포인트 오르며 골든크로스를 기록했고 대표는 사퇴했다")
    assert "45%" in terms and "사퇴" in terms
    assert "대표" not in terms                       # never a person/role for RED


def test_valid_llm_subtitle_rejects_junk():
    assert valid_llm_subtitle("김승원 정책실장 전격 사퇴") == "김승원 정책실장 전격 사퇴"
    assert valid_llm_subtitle("") == ""
    assert valid_llm_subtitle("이것은 너무 길어서 자막으로 쓰기에는 분명히 부적절한 아주 긴 문장입니다") == ""
