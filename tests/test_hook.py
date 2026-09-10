from political_shorts.hook import (
    detect_entities, detect_frame, make_factcheck, make_hook, make_title,
    pick_actor, simplify, to_polite,
)


def test_pick_actor_role_then_name_order():
    # "직책 이름" order: the person is AFTER the role word (and office prefix),
    # so the office word must not be taken as the actor.
    h = "대통령실 정책실장 김승원 전격 사퇴…취임 두 달 만"
    a = pick_actor(h, detect_entities(h), detect_frame(h))
    assert a == "김승원"
    # "이름 직책" order still works
    h2 = "한동훈 정책위의장 사퇴…지도부 흔들"
    assert pick_actor(h2, detect_entities(h2), detect_frame(h2)) == "한동훈"


def test_title_leads_with_real_name_not_office():
    h = "대통령실 정책실장 김승원 전격 사퇴…취임 두 달 만"
    line1 = make_title(h, detect_entities(h), detect_frame(h))[0]
    assert "김승원" in line1
    assert "대통령실" not in line1


def test_to_polite_normalizes_sentence_end():
    assert to_polite("김승원이 3일 사퇴했다.") == "김승원이 3일 사퇴했습니다."
    assert to_polite("정부 출범 초기라 이례적이다") == "정부 출범 초기라 이례적입니다"
    assert to_polite("찬성보다 반대가 많았다") == "찬성보다 반대가 많았습니다"
    # mid-sentence '다' must be untouched
    assert to_polite('"찬성보다 많다"는 반응') == '"찬성보다 많다"는 반응'


def test_detect_entities_president_and_party():
    e = detect_entities("이재명 대통령, 청와대 정책실장 전격 교체", "국민의힘은 반발했다")
    assert e.president is True
    assert "국민의힘" in e.parties
    assert e.lead_actor  # non-empty


def test_detect_frame_personnel_vs_vote():
    assert detect_frame("정책실장 전격 사퇴…후임 인선 착수").kind == "personnel"
    assert detect_frame("예산안 본회의 통과…찬성 210표로 가결").kind == "vote"


def test_make_hook_punchy_names_actor():
    e = detect_entities("이재명 대통령 청와대 정책실장 교체")
    f = detect_frame("정책실장 교체 사퇴")
    cap, nar = make_hook("이재명 대통령, 청와대 정책실장 전격 교체", e, f, "punchy")
    assert len(cap) <= 46
    assert nar and nar != cap and len(nar) > len(cap)


def test_make_hook_neutral_style():
    cap, nar = make_hook("국회 예산안 처리", detect_entities("국회 예산안"),
                         detect_frame("예산안 처리"), "neutral")
    assert "국회 예산안 처리" in nar


def test_simplify_glosses_jargon():
    out = simplify("국회는 3일 본회의에서 예산안을 의결했다")
    assert "의결" not in out
    assert "통과" in out


def test_factcheck_rows_have_marks():
    class F:  # minimal stand-in for AnalysisResult
        def __init__(self):
            from political_shorts.analyze import Tagged, Kind
            self.facts = [Tagged("국회가 3일 예산안을 의결했다", Kind.FACT, 2.0, ["date"])]
            self.claims = [Tagged('야당은 "독소조항"이라고 말했다', Kind.CLAIM, 1.2, ["말했다"])]
            self.interpretations = [Tagged("여권 부담이 커질 전망이다", Kind.INTERPRETATION, 1.5, [])]

    rows = make_factcheck(F(), 3)
    tags = {r["tag"] for r in rows}
    assert "사실" in tags and "확인" in tags
    assert all(r["tone"] in {"ok", "claim", "warn", "info"} for r in rows)
