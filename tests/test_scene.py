"""Scene Duration Controller — every scene 1.5-3.5s, split at clause / event
boundaries, each with a visual plan; adjacent scenes never move the same way."""
from political_shorts import scene


def _plan(*narrs, role="what"):
    segs = [{"role": role, "narration": n, "num": 1} for n in narrs]
    return scene.plan(segs)


def test_long_sentence_is_split_into_scene_length_pieces():
    long = ("그런데 이번 사퇴로 정부 초기에 핵심 인사가 물러나면서 국정 운영에 공백과 "
            "부담이 생길 수 있어 정치권의 관심이 크게 쏠리고 있는 상황입니다.")
    out = _plan(long)
    assert len(out) >= 3
    for s in out:
        assert scene.est_seconds(s["narration"]) <= scene.SCENE_MAX_S + 0.8
    # the pieces reconstruct the sentence (nothing dropped)
    joined = "".join(s["narration"].replace(" ", "") for s in out)
    assert joined == long.replace(" ", "")


def test_short_sentence_is_one_scene():
    out = _plan("3일 사퇴했습니다.")
    assert len(out) == 1
    assert out[0]["scene"]["zoom"] in ("in", "out", "pan_left", "pan_right", "punch")


def test_adjacent_scenes_do_not_repeat_the_camera_move():
    out = _plan("첫 번째 문장은 조금 길게 이어지는 배경 설명입니다.",
                "두 번째 문장도 마찬가지로 이어지는 설명을 담고 있습니다.",
                "세 번째 문장 역시 계속해서 이어지는 내용입니다.")
    zooms = [s["scene"]["zoom"] for s in out]
    for a, b in zip(zooms, zooms[1:]):
        assert a != b


def test_event_lead_gets_a_hard_cut_into_it():
    out = _plan("예산안이 통과됐습니다.", "그런데 이걸 두고 평가는 엇갈립니다.")
    assert out[0]["scene"]["transition"] == "cut"


def test_continuation_subscene_holds_the_media():
    out = _plan("이 문장은 두 조각으로 갈라질 만큼 충분히 길게 만들어 둔 배경 설명 문장입니다.")
    assert len(out) >= 2
    assert out[0]["scene"]["hold_media"] is False
    assert all(s["scene"]["hold_media"] for s in out[1:])
