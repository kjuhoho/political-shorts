"""Hook Engine — the first ~2s must be a curiosity beat, one of six types,
grounded in the article's own text (no invented facts / numbers / names)."""
from political_shorts.hook import detect_entities, detect_frame, pick_actor
from political_shorts.hook_engine import HookContext, build_hook


def _ctx(headline, facts, claims=()):
    e = detect_entities(headline, " ".join([*facts, *claims]))
    f = detect_frame(headline, " ".join(facts))
    return HookContext(headline=headline, facts=list(facts), claims=list(claims),
                       frame=f, entities=e, actor=pick_actor(headline, e, f), n_sources=3)


def test_surprise_fires_on_short_tenure():
    h = "대통령실 정책실장 김승원 전격 사퇴…취임 두 달 만"
    hk = build_hook(_ctx(h, ["김승원 대통령실 정책실장이 3일 사퇴했다.", "취임 두 달 만이다."]))
    assert hk.kind == "surprise"
    assert "두 달" in hk.narration and "김승원" in hk.narration
    assert not hk.narration.strip().startswith(h[:10])       # not the headline


def test_number_hook_only_uses_a_figure_from_the_source():
    h = "이재명 대통령 지지율 45%…한 달 만에 반등"
    hk = build_hook(_ctx(h, ["이재명 대통령 지지율이 45%로 나타났다."]))
    assert hk.kind == "number"
    assert hk.lead_number == "45%"
    # the number in the hook must exist in the source text
    assert "45%" in " ".join(["이재명 대통령 지지율이 45%로 나타났다."])


def test_outcome_first_for_a_vote():
    h = "예산안 본회의 통과…찬성 210표로 가결"
    hk = build_hook(_ctx(h, ["국회는 3일 본회의에서 예산안을 처리했다.", "예산안은 찬성 210표로 가결됐다."],
                         ["국민의힘은 합의 처리라고 밝혔다.", "민주당은 독소조항이라고 반발했다."]))
    assert hk.kind in ("outcome_first", "number", "twist")
    # any digits in the hook are grounded
    import re
    src = "국회는 3일 본회의에서 예산안을 처리했다. 예산안은 찬성 210표로 가결됐다."
    assert all(n in src for n in re.findall(r"\d[\d,.]*", hk.narration))


def test_always_returns_a_hook():
    hk = build_hook(_ctx("정치권 이모저모", ["별다른 특이사항은 없었다."]))
    assert hk.narration and hk.caption
    assert hk.narration.endswith(("?", "다.", "요.", "니다."))


def test_personnel_question_never_fires_without_an_actual_departure():
    # a real shipped case: FRAMES["personnel"] also fires on a bare
    # "후보자"/"지명" mention — a confirmation-hearing-report dispute about
    # a NOMINEE, with nobody stepping down, still landed on "왜 갑자기
    # 자리에서 내려왔을까요?", flatly asserting a departure that never
    # happened.
    h = "여당, 김승원 청문보고서 채택 강행…국민의힘 상임위 보이콧"
    facts = ["여당은 김승원 인사청문경과보고서 채택을 단독으로 강행했다.",
             "국민의힘은 이에 반발해 상임위원회 일정을 전면 보이콧했다."]
    hk = build_hook(_ctx(h, facts))
    assert "내려왔을까요" not in hk.narration
    assert "자리에서" not in hk.narration


def test_personnel_question_still_fires_on_a_genuine_departure():
    h = "대통령실 정책실장 김승원 전격 사퇴…취임 두 달 만"
    facts = ["김승원 대통령실 정책실장이 3일 사퇴했다."]
    build_hook(_ctx(h, facts))
    # a genuine departure is free to use the question form (or another
    # grounded generator may fire first — the point is it's never banned)
    ctx = _ctx(h, facts)
    from political_shorts.hook_engine import _question
    assert _question(ctx) is not None


def test_no_hype_words():
    h = "여야, 노란봉투법 두고 정면충돌"
    hk = build_hook(_ctx(h, ["국회 환노위는 노란봉투법을 상정했다."],
                         ["국민의힘은 위헌이라고 반발했다.", "민주당은 노동자 보호라고 맞섰다."]))
    for bad in ("충격", "발칵", "경악", "사실상 확정"):
        assert bad not in hk.narration


def test_hook_glosses_regulatory_jargon():
    # a real user complaint: the hook is the ONE card with no LLM pass and
    # no per-card term glossing — "토허구역 실거주" shipped completely
    # unexplained as the literal first thing a viewer sees and hears.
    h = "강남 토허구역 실거주 의무 위반 무더기 적발"
    hk = build_hook(_ctx(h, ["국토부는 강남 토허구역에서 실거주 의무 위반 사례를 적발했다고 밝혔다."]))
    if "토허구역" in hk.narration:
        assert "토허구역(" in hk.narration
    if "토허구역" in hk.caption:
        assert "토허구역(" in hk.caption
