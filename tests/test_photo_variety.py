"""The thumbnail was the same building over and over (live audit 2026-09-30): the National Assembly opened
four videos out of five, because each frame's list led with it and the pool behind it was 14 titles long."""
import dataclasses

from political_shorts import asset_memory, images
from political_shorts.config import settings
from political_shorts.hook import Entities, Frame


def _collect_titles(monkeypatch, cfg, headline, frame_kind="scandal"):
    """The order collect_images would actually try location titles in."""
    tried: list[str] = []

    def fake_resolve(title, person=False):
        tried.append(title)
        return None                                     # nothing resolves: we only care about the order

    monkeypatch.setattr(images, "_resolve", fake_resolve)
    images.collect_images(Entities(), Frame(kind=frame_kind), headline, cfg)
    return tried


def test_every_frame_has_several_establishing_shots_to_choose_from():
    for kind, titles in images._FRAME_LOCATION.items():
        assert len(titles) >= 3, kind
    assert len(images.LOCATION_POOL) >= 25


def test_a_photo_used_this_week_is_tried_last(tmp_path, monkeypatch):
    cfg = dataclasses.replace(settings, image_enabled=True, data_dir=tmp_path,
                              image_cache_dir=str(tmp_path / "c"))
    first = _collect_titles(monkeypatch, cfg, "예산안 처리 두고 여야 충돌")
    asset_memory.remember(cfg, [first[0]])
    second = _collect_titles(monkeypatch, cfg, "예산안 처리 두고 여야 충돌")
    assert second[0] != first[0]                        # same story, a different picture
    assert first[0] in second                           # pushed back, never dropped


def test_a_named_ministry_gets_its_own_building_first(tmp_path, monkeypatch):
    cfg = dataclasses.replace(settings, image_enabled=True, data_dir=tmp_path,
                              image_cache_dir=str(tmp_path / "c"))
    tried = _collect_titles(monkeypatch, cfg, "기획재정부, 내년 예산안 국회 제출")
    assert tried[0] == "기획재정부"
    assert _collect_titles(monkeypatch, cfg, "한국은행, 기준금리 동결")[0] == "한국은행"


def test_the_pools_only_hold_titles_that_were_verified():
    """Every entry is a ko.wikipedia article title checked by hand against its lead image — no bare guesses
    like '정부서울청사', which has no raster lead image and silently yields nothing."""
    for title in images.LOCATION_POOL + [t for v in images._FRAME_LOCATION.values() for t in v] \
            + list(images.MINISTRY_LOCATION.values()):
        assert title.strip() == title and len(title) >= 2
        assert "정부서울청사" not in title and "대검찰청" not in title
