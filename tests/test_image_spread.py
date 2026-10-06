"""The subject's face landed on 11 cards out of 12 and the video was one still photo end to end
(user, 2026-10-06). The face opens the story and returns for key beats; the rest of the cards show something."""
from collections import Counter

from political_shorts.video import _assign_images

LEAD = "face.jpg"


def _images(n_photos=8):
    return [{"path": LEAD, "kind": "portrait", "is_lead": True, "query": "김민석"}] + \
           [{"path": f"p{i}.jpg", "kind": "photo", "query": f"장소{i}"} for i in range(n_photos)]


def _cards(n=12):
    roles = ["hook", "summary", "what", "sides", "factcheck", "reaction"]
    return [{"role": roles[i % len(roles)], "caption": f"카드 {i}", "narration": f"내용 {i}"} for i in range(n)]


def test_the_face_opens_the_video_but_does_not_fill_it():
    picks = _assign_images(_cards(12), _images(), "김민석")
    counts = Counter(p for p in picks if p)
    assert picks[0] == LEAD                               # it still opens
    assert counts[LEAD] <= 6                              # half the cards at most, not 11 of 12
    assert len(counts) >= 6                               # the rest of the cards show something else


def test_every_card_still_gets_a_picture():
    picks = _assign_images(_cards(12), _images(), "김민석")
    assert all(p for p in picks)


def test_with_nothing_else_collected_the_face_still_carries_the_video():
    only_face = [{"path": LEAD, "kind": "portrait", "is_lead": True, "query": "김민석"}]
    picks = _assign_images(_cards(6), only_face, "김민석")
    assert picks.count(LEAD) == 6                          # a face beats a blank backdrop


def test_a_card_that_names_someone_still_gets_that_person():
    imgs = _images(4) + [{"path": "other.jpg", "kind": "portrait", "query": "장동혁"}]
    cards = _cards(6)
    cards[3]["narration"] = "장동혁 대표는 반박했습니다."
    picks = _assign_images(cards, imgs, "김민석")
    assert picks[3] == "other.jpg"
