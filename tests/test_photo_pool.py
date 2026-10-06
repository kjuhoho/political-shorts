"""More photos, from more places, and several of them inside ONE video.

User, 2026-10-06: "사진 자료 수집과 더 많은 사진을 수집해야해". Rotating the one photo we had per person only
changed which still a video was built around; the video still showed that single face on half its cards.
"""
import dataclasses

import pytest

from political_shorts import images, people
from political_shorts.config import settings
from political_shorts.video import _assign_images

SHOTS = ["Lee Jae-myung portrait 2026.jpg", "President Lee Jae-myung 2025 (cropped).jpg",
         "이재명-경기도.jpg", "Lee Jae-myung and Takaichi summit 2026.jpg"]


@pytest.fixture
def cfg(tmp_path):
    return dataclasses.replace(settings, data_dir=tmp_path, image_cache_dir=str(tmp_path / "c"))


def _resolved(monkeypatch, files, article="이재명", lead=None):
    monkeypatch.setattr(images, "_resolve", lambda name, person=False: {
        "url": "https://commons/lead.jpg", "width": 800, "title": "Lee Jae-myung portrait 2026",
        "file": lead or files[0].replace(" ", "_"), "article": article})
    monkeypatch.setattr(people, "photo_files", lambda *a, **k: list(files))
    monkeypatch.setattr(people, "is_person", lambda *a, **k: True)
    monkeypatch.setattr(images, "_commons_info",
                        lambda f: {"url": f"https://commons/{f}", "width": 900, "height": 1200})


# --------------------------------------------------------------------------- #
# several shots of one person, in one video
# --------------------------------------------------------------------------- #
def test_one_video_gets_several_different_shots_of_the_same_person(monkeypatch, cfg):
    _resolved(monkeypatch, SHOTS)
    got = images._portrait_infos("이재명", cfg, 3)
    assert len(got) == 3
    assert len({g["url"] for g in got}) == 3


def test_the_same_photo_spelled_two_ways_is_one_photo(monkeypatch, cfg):
    # Wikidata says "A B.jpg", the article's thumbnail URL says "A_B.jpg" — the same file came back twice
    _resolved(monkeypatch, ["Lee Jae-myung portrait 2026.jpg"], lead="Lee_Jae-myung_portrait_2026.jpg")
    got = images._portrait_infos("이재명", cfg, 3)
    assert len(got) == 1


def test_someone_with_one_photo_still_gets_that_one(monkeypatch, cfg):
    _resolved(monkeypatch, ["Jang Dong-hyeok's Portrait (2026.5).png"],
              lead="Jang_Dong-hyeok's_Portrait_(2026.5).png")
    got = images._portrait_infos("장동혁", cfg, 3)
    assert len(got) == 1 and got[0]["url"]


def test_asking_for_one_is_exactly_what_it_was_before(monkeypatch, cfg):
    _resolved(monkeypatch, SHOTS)
    assert images._rotating_portrait("이재명", cfg)["url"] == images._portrait_infos("이재명", cfg, 1)[0]["url"]


# --------------------------------------------------------------------------- #
# the name has to resolve to the PERSON
# --------------------------------------------------------------------------- #
def test_an_article_that_is_not_a_person_is_not_his_face(monkeypatch, cfg):
    """'조국' is the ko.wikipedia article about the FATHERLAND; his own 20 Commons photos sit under
    '조국 (정치인)'. Resolving the concept article meant no face at all — or worse, its photo."""
    seen = {}

    def fake_resolve(name, person=False):
        seen["last"] = name
        if name == "조국":
            return {"url": "https://commons/flag.jpg", "width": 800, "title": "A flag",
                    "file": "flag.jpg", "article": "조국"}
        return {"url": "https://commons/chokuk.jpg", "width": 800, "title": "Cho Kuk's Portrait",
                "file": "Cho_Kuk's_Portrait.jpg", "article": "조국 (정치인)"}

    monkeypatch.setattr(images, "_resolve", fake_resolve)
    monkeypatch.setattr(people, "is_person", lambda art, *a, **k: art != "조국")
    monkeypatch.setattr(people, "photo_files", lambda *a, **k: ["Cho Kuk's Portrait.jpg"])
    got = images._portrait_infos("조국", cfg, 1)
    assert seen["last"] == "조국 (정치인)"
    assert got and got[0]["url"] == "https://commons/chokuk.jpg"


def test_no_face_at_all_rather_than_a_non_persons_photo(monkeypatch, cfg):
    monkeypatch.setattr(images, "_resolve", lambda name, person=False: (
        {"url": "https://commons/flag.jpg", "width": 800, "title": "A flag",
         "file": "flag.jpg", "article": "조국"} if name == "조국" else None))
    monkeypatch.setattr(people, "is_person", lambda art, *a, **k: False)
    assert images._portrait_infos("조국", cfg, 1) == []


def test_a_lookup_that_fails_keeps_the_portrait_we_already_have(monkeypatch, cfg):
    """is_person() must fail OPEN: a flaky Wikidata call must not drop a face the pipeline used yesterday."""
    monkeypatch.setattr(people, "_entity_of_article", lambda *a, **k: "")
    assert people.is_person("이재명", cfg, session=object()) is True


# --------------------------------------------------------------------------- #
# more sources per person
# --------------------------------------------------------------------------- #
def test_a_file_named_after_someone_else_is_not_taken_from_his_article():
    """A biography also illustrates rivals; this project has shipped a wrong face."""
    tokens = people._name_tokens(["이재명", "Lee Jae-myung"])
    assert people._mentions("President Lee Jae Myung 20260306.jpg", tokens)
    assert people._mentions("이재명-경기도.jpg", tokens)
    assert not people._mentions("Yoon Suk-yeol 2022.jpg", tokens)
    assert not people._mentions("Kim Moon-soo portrait.jpg", tokens)


def test_a_japanese_named_summit_file_ranks_behind_a_portrait():
    ordered = people._solo_first(["2025年8月23日日韓首脳会談 (13).jpg", "Lee Jae-myung portrait.jpg"])
    assert "portrait" in ordered[0].lower()


def test_one_spelling_per_file():
    assert people.norm_file("President_Lee_Jae_Myung_20260306.jpg") == "President Lee Jae Myung 20260306.jpg"


def test_documents_and_logos_are_still_not_photos():
    assert not people._usable("Lee Jae-myung signature.svg")
    assert not people._usable("이재명 성적표.jpg")
    assert people._usable("이재명-경기도.jpg")


# --------------------------------------------------------------------------- #
# the collected photos reach the screen
# --------------------------------------------------------------------------- #
def _cards(n=12):
    roles = ["hook", "summary", "what", "sides", "factcheck", "reaction"]
    return [{"role": roles[i % len(roles)], "caption": f"카드 {i}", "narration": f"내용 {i}"} for i in range(n)]


def test_the_subjects_cards_rotate_through_his_photos():
    """Before: the subject's single photo on half the cards. Now: the same person, different shots."""
    imgs = [{"path": "face1.jpg", "kind": "portrait", "is_lead": True, "query": "이재명"},
            {"path": "face2.jpg", "kind": "portrait", "query": "이재명"},
            {"path": "face3.jpg", "kind": "portrait", "query": "이재명"}] + \
           [{"path": f"p{i}.jpg", "kind": "photo", "query": f"장소{i}"} for i in range(6)]
    picks = _assign_images(_cards(12), imgs, "이재명")
    faces = [p for p in picks if p and p.startswith("face")]
    assert picks[0] == "face1.jpg"                       # the poster face still opens the video
    assert len(set(faces)) == 3                          # all three shots are used
    assert max(faces.count(f) for f in set(faces)) <= 3  # and none of them carries the video alone


def test_with_one_photo_the_face_still_carries_an_empty_video():
    only_face = [{"path": "face1.jpg", "kind": "portrait", "is_lead": True, "query": "이재명"}]
    assert _assign_images(_cards(6), only_face, "이재명").count("face1.jpg") == 6


# --------------------------------------------------------------------------- #
# more non-person material
# --------------------------------------------------------------------------- #
def test_the_location_pool_is_big_enough_to_rotate_through_a_week():
    # 2 videos a day x 7 days, several shots each: a 30-title pool ran out by midweek
    assert len(images.LOCATION_POOL) >= 50
    assert len(set(images.LOCATION_POOL)) == len(images.LOCATION_POOL)


def test_a_prices_story_gets_a_market_not_a_landmark():
    assert any(x in images._FRAME_LOCATION["economy"] for x in ("남대문시장", "광장시장"))
    assert "남대문시장" in images._TOPIC_LOCATION["economy"]


def test_a_named_ministry_still_gets_its_own_building():
    assert images.MINISTRY_LOCATION["해수부"] == "해양수산부"
    assert images.MINISTRY_LOCATION["문체부"] == "문화체육관광부"


def test_a_place_name_does_not_outrank_a_topic_shot():
    """'강원' is in the dateline of every DMZ story — the provincial office must not open a 북한 story."""
    assert not any(len(w) <= 3 and w in ("부산", "대구", "광주", "제주", "강원", "경북", "울산")
                   for w in images.MINISTRY_LOCATION)
    assert images.REGION_LOCATION["강원"] == "강원특별자치도청"

def test_rotation_never_beats_relevance(monkeypatch, tmp_path):
    """A wider pool made this bite: every new title is 'unused', so a market jumped ahead of the story's own
    topic shot as soon as 판문점 had run this week. Recency rotates WITHIN a tier, never across tiers."""
    from political_shorts import asset_memory
    from political_shorts.hook import detect_entities, detect_frame

    cfg = dataclasses.replace(settings, image_enabled=True, image_max_count=3,
                              data_dir=tmp_path, image_cache_dir=str(tmp_path / "c"))
    asset_memory.remember(cfg, images._TOPIC_LOCATION["north_korea"])      # all of them used this week
    monkeypatch.setattr(images, "_resolve", lambda title, person=False: {
        "url": f"https://commons/{title}.jpg", "author": "", "license": "CC", "source_url": "",
        "width": 800, "height": 800, "title": title, "file": f"{title}.jpg", "article": title})
    monkeypatch.setattr(images, "_download", lambda url, cache_dir: (tmp_path / "x.jpg", 800, 800))
    hl = "정부, 평양 병원에 의료장비 지원 추진"
    got = images.collect_images(detect_entities(hl), detect_frame(hl), hl, cfg)
    assert got and got[0].query in images._TOPIC_LOCATION["north_korea"]
