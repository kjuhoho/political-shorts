"""Scene planner — one clean readable subtitle chunk per scene, held long
enough to read; each scene carries a visual plan (camera move + transition)."""
from political_shorts import scene


def _plan(*narrs, role="what"):
    segs = [{"role": role, "narration": n, "num": 1} for n in narrs]
    return scene.plan(segs)


def test_long_sentence_is_split_into_readable_chunks():
    long = ("이재명 대통령이 임기 중 연임 개헌 논의를 꺼내면서, "
            "정치권에서 논란이 커지고 있습니다.")
    out = _plan(long)
    assert len(out) >= 2
    for s in out:
        # a chunk fits ~2-3 lines and is never cut mid-word
        assert len(s["narration"]) <= 46
        assert not s["narration"].rstrip().endswith(("물러나", "지적했", "커지고"))
        assert s["scene"]["min_read_s"] >= 1.6
    # the chunks reconstruct the sentence — nothing dropped or compressed
    joined = "".join(s["narration"].replace(" ", "") for s in out)
    assert joined == long.replace(" ", "").rstrip(".") + "." or joined == long.replace(" ", "")


def test_caption_equals_the_narration_verbatim():
    out = _plan("정부가 오늘 새로운 개헌 논의를 공식적으로 시작했습니다.")
    for s in out:
        assert s["caption"] == s["narration"]        # subtitle == spoken words


def test_sentence_with_no_clean_seam_stays_whole():
    # no comma / connective to break on -> one chunk, not cut mid-word
    out = _plan("대통령의 정책을 총괄하는 대통령실의 핵심 참모가 사퇴했습니다.")
    assert len(out) == 1
    assert out[0]["narration"].endswith("사퇴했습니다.")


def test_short_sentence_is_one_scene():
    out = _plan("3일 사퇴했습니다.")
    assert len(out) == 1
    assert out[0]["scene"]["zoom"] in ("in", "out", "pan_left", "pan_right", "punch")


def test_event_lead_gets_a_hard_cut_into_it():
    out = _plan("예산안이 통과됐습니다.", "그런데 이걸 두고 평가는 엇갈립니다.")
    assert out[0]["scene"]["transition"] == "cut"


def test_continuation_chunk_holds_the_media():
    out = _plan("이재명 대통령이 임기 중 연임 개헌 논의를 꺼내면서, "
                "정치권에서 논란이 커지고 있습니다.")
    assert len(out) >= 2
    assert out[0]["scene"]["hold_media"] is False
    assert all(s["scene"]["hold_media"] for s in out[1:])
