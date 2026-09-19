"""_assign_images — photos must be person-centric (the subject + related people),
with a face never sitting under a card that names someone else."""
from political_shorts.video import _assign_images


def _seg(role, caption="", narration=""):
    return {"role": role, "caption": caption, "narration": narration, "kicker": ""}


SEGMENTS = [
    _seg("hook", "김승원 둘러싼 공방"),
    _seg("summary", "국회법 개정안이 처리됐다"),
    _seg("what", "한동훈 의원이 특검 수사를 요구했다"),
    _seg("reaction", "김승원 의원은 사실이 아니라고 반박했다"),
    _seg("factcheck"),
    _seg("outro", "자세한 내용은 더보기란에"),
]


def _imgs(portraits, photos):
    return (
        [{"path": f"P_{n}", "kind": "portrait", "query": n} for n in portraits]
        + [{"path": f"L_{n}", "kind": "photo", "query": n} for n in photos]
    )


def test_named_card_gets_that_person_not_someone_else():
    imgs = _imgs(["김승원", "한동훈"], ["국회", "광화문", "대법원"])
    out = _assign_images(SEGMENTS, imgs, topic="김승원")
    # the card that names 한동훈 shows 한동훈 — never 김승원, never a building
    assert out[2] == "P_한동훈"
    # 한동훈's face appears ONLY on the card that names him
    assert out.count("P_한동훈") == 1


def test_photos_are_person_centric_not_location_dominated():
    imgs = _imgs(["이재명"], ["국회", "광화문", "한강", "서울역"])
    out = _assign_images(SEGMENTS, imgs, topic="이재명")
    faces = sum(1 for p in out if p and p.startswith("P_"))
    # a story about one person should be mostly that person's face, not scenery
    assert faces >= len(SEGMENTS) - 2
    assert out[0] == "P_이재명"          # the hook leads on the subject


def test_no_portraits_falls_back_to_locations_cleanly():
    imgs = _imgs([], ["국회", "광화문", "한강"])
    out = _assign_images(SEGMENTS, imgs, topic="패스트트랙")
    assert all(p is None or p.startswith("L_") for p in out)
    assert len(out) == len(SEGMENTS)


def test_thumb_lines_keep_three_to_five_big_words():
    from political_shorts.video import _thumb_lines
    assert _thumb_lines(["김승원 법무장관 후보자 청문회 논란 총정리", "오늘 국회서 직접 입 열까?"]) == ["김승원 법무장관 후보자", "직접 입 열까?"]
    assert _thumb_lines(["한동훈 녹취록 공개"]) == ["한동훈 녹취록 공개"]
    assert _thumb_lines([]) == []
    two = _thumb_lines(["대통령 vs 대법원장", "정면충돌?"])
    assert two == ["대통령 vs 대법원장", "정면충돌?"]
