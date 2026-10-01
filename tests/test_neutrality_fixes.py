"""Live audit 2026-09-30: the narrator spoke a party's demand as fact, and one card contradicted itself."""
from political_shorts.script_gen import _attribute_demands


def _card(text, role="sides"):
    return {"role": role, "kicker": "갈리는 입장", "narration": text, "caption": text}


def test_the_sentence_that_shipped_becomes_that_party_s_claim():
    segs = [_card("국민의힘 (장동혁 대표 등)은 DMZ 지뢰 사고와 관련해 이재명 대통령의 책임을 물어 탄핵 및 "
                  "국정조사를 추진해야 합니다. 한병도 원내대표는 과도한 공세라고 반박했습니다.")]
    changes = _attribute_demands(segs)
    assert "추진해야 한다고 주장했습니다." in segs[0]["narration"]
    assert "추진해야 합니다." not in segs[0]["narration"]
    assert segs[0]["caption"] == segs[0]["narration"]        # the subtitle follows the spoken line
    assert any("attributed" in c for c in changes)


def test_a_demand_with_no_one_behind_it_is_dropped():
    segs = [_card("여당은 신속한 처리를 요구했습니다. 초당적 협력이 필요합니다.")]
    _attribute_demands(segs)
    assert segs[0]["narration"] == "여당은 신속한 처리를 요구했습니다."


def test_but_never_at_the_cost_of_an_empty_card():
    segs = [_card("초당적 협력이 필요합니다.")]
    changes = _attribute_demands(segs)
    assert segs[0]["narration"] == "초당적 협력이 필요합니다."
    assert any("would empty" in c for c in changes)


def test_a_card_cannot_cite_a_camp_and_say_that_camp_was_not_found():
    segs = [_card("진보 성향 매체 보도에 따르면 한병도 원내대표는 과도한 공세라고 반박했습니다. "
                  "진보 성향 매체 보도에서는 이 사안에 대한 별도 입장을 확인하지 못했습니다.")]
    changes = _attribute_demands(segs)
    assert "확인하지 못했습니다" not in segs[0]["narration"]
    assert "한병도" in segs[0]["narration"]
    assert any("contradicting" in c for c in changes)


def test_an_honest_not_found_line_on_its_own_stays():
    segs = [_card("보수 성향 매체 보도는 조사했으나 이 사안에 대한 별도 입장을 확인하지 못했습니다.")]
    _attribute_demands(segs)
    assert "확인하지 못했습니다" in segs[0]["narration"]


def test_other_cards_are_left_alone():
    segs = [_card("철저한 진상 규명이 필요합니다.", role="outro")]
    assert _attribute_demands(segs) == []
    assert segs[0]["narration"] == "철저한 진상 규명이 필요합니다."
