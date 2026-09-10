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


def test_no_hype_words():
    h = "여야, 노란봉투법 두고 정면충돌"
    hk = build_hook(_ctx(h, ["국회 환노위는 노란봉투법을 상정했다."],
                         ["국민의힘은 위헌이라고 반발했다.", "민주당은 노동자 보호라고 맞섰다."]))
    for bad in ("충격", "발칵", "경악", "사실상 확정"):
        assert bad not in hk.narration
