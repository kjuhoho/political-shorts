"""Unit tests for the template-path (no-LLM) narration seeds in script_gen —
these are the fallback that ships when the LLM rewrite is unavailable, so they
must not emit broken Korean."""
from political_shorts import script_gen as sg
from political_shorts.analyze import Tagged, Kind


def _c(text):
    return Tagged(text, Kind.CLAIM, 1.0, [])


def test_reaction_line_skips_unattributed_first_claim():
    # claims[0] has no party/person — the line must still find the attributable
    # claims that follow, not bail out.
    claims = [
        _c("대통령실은 일신상의 이유라고 밝혔다."),
        _c("국민의힘은 인사 검증 부실을 지적했다."),
        _c("더불어민주당은 정책 혼선을 우려했다."),
    ]
    line = sg._reaction_line(claims)
    assert "국민의힘" in line and "민주당" in line
    # the reporting verb is trimmed, not quoted verbatim
    assert "지적했다" not in line
    assert "  " not in line  # no doubled spaces / stray gaps


def test_sides_line_no_interp_fallback_garbage():
    # a headline echoed into interps must NOT be spliced into "…는 전망" grammar
    interps = [Tagged("대통령실 정책실장 김승원 전격 사퇴…취임 두 달 만",
                      Kind.INTERPRETATION, 1.0, [])]
    assert sg._sides_line([], interps) == ""


def test_sides_line_builds_from_party_split():
    claims = [
        _c("국민의힘은 인사 검증 부실을 지적했다."),
        _c("더불어민주당은 정책 혼선을 우려했다."),
    ]
    line = sg._sides_line(claims, [])
    assert line.startswith("그런데 이 사안을 보는 눈은")
    assert "국민의힘" in line and "민주당" in line


def test_quote_ratio_flags_a_mostly_quoted_sentence():
    # the real "what" card that shipped broken: a nested-quote sentence with
    # almost no plain narrative around it.
    quoted = ("김 대표는 이날 출연해 “‘괜히 탄핵 이야기를 꺼낸 것이 경솔했다’고 할 수 "
              "있다”고 말했다.")
    plain = "국회는 3일 본회의를 열어 예산안을 의결했다."
    assert sg._quote_ratio(quoted) > sg._quote_ratio(plain)
    assert sg._quote_ratio(plain) == 0.0


def test_spoken_never_appends_a_period_to_a_predicateless_fragment():
    # a real shipped card: _SENT_CHUNK matched a fragment long enough to pass
    # the 40%-length ratio check, but it had no predicate ("...전략과" — 과 is
    # "and", not a verb) — the old code blindly appended "." and shipped it
    # looking finished. Must now fall through and drop it instead.
    assert sg._spoken("더불어민주당의 원내 전략과") == ""
    # the untruncated original (real predicate) still passes straight through
    full = "더불어민주당의 원내 전략과 국민의힘의 대응 방식이 이번 논란의 핵심 배경입니다."
    assert sg._spoken(full) == full


def test_context_score_deprioritizes_quote_heavy_sentences():
    quoted = ("김 대표는 이날 출연해 “‘괜히 탄핵 이야기를 꺼낸 것이 경솔했다’고 할 수 "
              "있다”고 말했다.")
    plain = "국회는 처음으로 이례적인 절차를 밟아 예산안을 처리했다."   # hits _CONTEXT_HINTS
    assert sg._context_score(plain) > sg._context_score(quoted)


def _fit_segs():
    # summary (background) is deliberately the LONGEST card here — under the
    # old priority it was dropped first regardless of length; now the extra
    # "what" repeat and length-trimming absorb the pressure instead.
    return [
        {"role": "hook", "narration": "김승원이 왜 사퇴했을까요?"},
        {"role": "summary", "narration": "김승원은 대통령의 정책을 총괄하는 정책실장입니다. "
                                         "정부 출범 초기에 임명된 핵심 참모로, 인사 검증과 "
                                         "정책 조율을 담당해 왔습니다."},
        {"role": "what", "narration": "김승원 정책실장이 취임 두 달 만에 물러났습니다."},
        {"role": "what", "narration": "정부 출범 초기 실장급 인사가 물러난 것은 이례적입니다."},
        {"role": "factcheck", "narration": "확인된 사실은 이겁니다. 김승원은 3일 사퇴했습니다."},
        {"role": "outro", "narration": "구독과 좋아요 눌러주시면 큰 힘이 됩니다."},
    ]


def test_fit_duration_protects_summary_over_a_second_what_beat():
    out = sg._fit_duration(_fit_segs(), budget=18.0)
    roles = [s["role"] for s in out]
    assert roles.count("what") <= 1                 # the extra "what" repeat goes first
    summary = next((s for s in out if s["role"] == "summary"), None)
    assert summary is not None and summary.get("narration")   # background survives


def test_fit_duration_shrinks_summary_rather_than_deleting_it_when_possible():
    segs = _fit_segs()
    full_summary = next(s["narration"] for s in segs if s["role"] == "summary")
    out = sg._fit_duration(_fit_segs(), budget=18.0)
    summary = next((s for s in out if s["role"] == "summary"), None)
    assert summary is not None
    # shorter than the original (it absorbed some of the trim), not gone
    assert 0 < len(summary["narration"]) < len(full_summary)


def test_fit_duration_keeps_summary_even_when_both_what_beats_must_go():
    # under real budget pressure summary now outranks even a SINGLE "what" —
    # the old priority (summary always dies first) is fully inverted.
    out = sg._fit_duration(_fit_segs(), budget=12.0)
    roles = [s["role"] for s in out]
    assert "what" not in roles
    summary = next((s for s in out if s["role"] == "summary"), None)
    assert summary is not None and summary.get("narration")
