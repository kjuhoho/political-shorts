"""Image collection — topic-relevant filler photos, not just generic Seoul
landmarks. A real shipped case: a 북한/평양 story got 광화문광장/서울역/국회
photos with zero connection to the actual subject."""
import dataclasses

from political_shorts import images
from political_shorts.config import settings
from political_shorts.hook import detect_entities, detect_frame


HL = "정부, 평양 병원에 의료장비 166종 지원 추진...야 \"부적절\""
BODY = "북한 강동군병원에 의료장비 지원을 추진한다. 유엔 제재 면제를 승인받았다."


def _ctx():
    return detect_entities(HL, BODY), detect_frame(HL, BODY)


def _fake_resolve(title, person):
    if person:
        return None                    # no portraits in this test — locations only
    return {"url": f"https://x/{title}.jpg", "author": "a", "license": "CC",
            "source_url": "https://commons.wikimedia.org/wiki/File:x.jpg",
            "width": 1600, "height": 1600, "title": title}


def test_collect_images_tries_topic_locations_before_the_generic_pool(monkeypatch, tmp_path):
    cfg = dataclasses.replace(settings, image_enabled=True, image_max_count=3,
                              image_cache_dir=str(tmp_path))
    monkeypatch.setattr(images, "_resolve", _fake_resolve)
    monkeypatch.setattr(images, "_download", lambda url, cache_dir: (tmp_path / "x.jpg", 800, 800))
    ent, fr = _ctx()
    got = images.collect_images(ent, fr, HL, cfg, body_text=BODY)
    queries = [a.query for a in got]
    assert queries                                     # something came back
    assert queries[0] in images._TOPIC_LOCATION["north_korea"]


def test_collect_images_falls_back_to_generic_pool_with_no_topic(monkeypatch, tmp_path):
    cfg = dataclasses.replace(settings, image_enabled=True, image_max_count=2,
                              image_cache_dir=str(tmp_path))
    monkeypatch.setattr(images, "_resolve", _fake_resolve)
    monkeypatch.setattr(images, "_download", lambda url, cache_dir: (tmp_path / "x.jpg", 800, 800))
    hl = "정책실장 김승원 전격 사퇴"
    ent, fr = detect_entities(hl), detect_frame(hl)
    got = images.collect_images(ent, fr, hl, cfg)
    queries = [a.query for a in got]
    assert queries and not any(q in images._TOPIC_LOCATION["north_korea"] for q in queries)
