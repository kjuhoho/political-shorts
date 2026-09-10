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
