"""The president's face was the same shot in every video (user, 2026-10-06): one photo per person was all the
pipeline ever looked up. Several are collected now, and consecutive videos take different ones."""
import dataclasses

from political_shorts import asset_memory, images, people
from political_shorts.config import settings

FILES = ["President_Lee_Jae_Myung_20260306.jpg", "President Lee Jae-myung 2025 (cropped).jpg",
         "Sanae Takaichi meets Lee Jae Myung 13 January 2026.jpg", "이재명 지사 2019 (cropped).jpg"]


def _env(monkeypatch, tmp_path):
    cfg = dataclasses.replace(settings, data_dir=tmp_path, image_cache_dir=str(tmp_path / "c"))
    monkeypatch.setattr(images, "_resolve", lambda name, person=False: {
        "url": "https://commons/lead.jpg", "width": 800, "title": "President Lee Jae Myung 20260306",
        "file": FILES[0], "article": "이재명"})
    monkeypatch.setattr(people, "photo_files", lambda name, c, article_title="", session=None: list(FILES))
    monkeypatch.setattr(images, "_commons_info",
                        lambda f: {"url": f"https://commons/{f}", "width": 900, "height": 1200})
    return cfg


def test_three_videos_in_a_row_use_three_different_photos(monkeypatch, tmp_path):
    cfg = _env(monkeypatch, tmp_path)
    seen = []
    for _ in range(3):
        got = images._rotating_portrait("이재명", cfg)
        seen.append(got["title"])
        asset_memory.remember(cfg, [got["title"]])
    assert len(set(seen)) == 3, seen


def test_a_person_with_only_one_photo_still_gets_it(monkeypatch, tmp_path):
    cfg = _env(monkeypatch, tmp_path)
    monkeypatch.setattr(people, "photo_files", lambda *a, **k: [FILES[0]])
    got = images._rotating_portrait("이재명", cfg)
    assert got["title"] == "President Lee Jae Myung 20260306"
    asset_memory.remember(cfg, [got["title"]])
    assert images._rotating_portrait("이재명", cfg)["title"] == "President Lee Jae Myung 20260306"


def test_solo_portraits_come_before_photos_with_other_people_in_them():
    ordered = people._solo_first(FILES)
    assert "meets" not in ordered[0].lower()
    assert "meets" in ordered[-1].lower()
    assert sorted(ordered) == sorted(FILES)               # ordering only, nothing dropped


def test_documents_and_logos_are_not_portraits():
    assert any(w in people._NOT_A_PHOTO for w in ("성적표", "합격", "signature", "logo"))
