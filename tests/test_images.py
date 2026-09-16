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


def test_resolve_retries_a_disambiguated_politician_name(monkeypatch):
    # a real shipped case: "김민석" alone is a Wikipedia disambiguation page
    # (a dozen athletes/singers/actors share the name) — the sitting
    # 더불어민주당 대표 never got a portrait at all, so an unrelated,
    # merely-co-mentioned politician's photo silently became the video's
    # dominant face instead.
    def fake_summary(title):
        if title == "김민석":
            return {"type": "disambiguation"}
        if title == "김민석 (정치인)":
            return {"type": "standard",
                    "originalimage": {"source": "https://upload.wikimedia.org/wikipedia/commons/f/fa/Kim.jpg"}}
        return None

    monkeypatch.setattr(images, "_wp_summary", fake_summary)
    monkeypatch.setattr(images, "_commons_info", lambda filename: {
        "url": "https://x/Kim.jpg", "author": "a", "license": "CC",
        "source_url": "https://commons.wikimedia.org/wiki/File:Kim.jpg",
        "width": 800, "height": 1000,
    })
    info = images._resolve("김민석", person=True)
    assert info is not None
    assert info["url"] == "https://x/Kim.jpg"


def test_resolve_never_guesses_when_the_suffix_is_also_ambiguous(monkeypatch):
    # the disambiguation retry must not just always succeed — if "(정치인)"
    # is ITSELF ambiguous or missing, this must still resolve to nothing
    # rather than guess.
    def fake_summary(title):
        if title == "홍길동":
            return {"type": "disambiguation"}
        return None                                     # "(정치인)" doesn't exist either

    monkeypatch.setattr(images, "_wp_summary", fake_summary)
    assert images._resolve("홍길동", person=True) is None


def test_president_gets_the_lead_portrait_even_with_an_honorific_headline(monkeypatch, tmp_path):
    # a real shipped case: "이 대통령, 18일 기자회견..." never literally
    # contains "이재명" (only "이 대통령", the honorific short form), so the
    # president got NO portrait at all and an unrelated, only-incidentally-
    # mentioned politician's photo silently became the dominant face.
    cfg = dataclasses.replace(settings, image_enabled=True, image_max_count=3,
                              image_cache_dir=str(tmp_path))

    def fake_resolve(title, person):
        if person and title == "이재명":
            return {"url": "https://x/lee.jpg", "author": "a", "license": "CC",
                    "source_url": "https://commons.wikimedia.org/wiki/File:lee.jpg",
                    "width": 800, "height": 1000, "title": title}
        if person:
            return None
        return {"url": f"https://x/{title}.jpg", "author": "a", "license": "CC",
                "source_url": "https://commons.wikimedia.org/wiki/File:x.jpg",
                "width": 1600, "height": 1600, "title": title}

    monkeypatch.setattr(images, "_resolve", fake_resolve)
    monkeypatch.setattr(images, "_download", lambda url, cache_dir: (tmp_path / "x.jpg", 800, 1000))
    hl = "이 대통령, 18일 기자회견...청와대 \"주요 현안에 분명한 입장 밝힐 것\""
    body = "이재명 대통령은 18일 기자회견을 통해 주요 현안에 입장을 밝힐 예정이다."
    ent, fr = detect_entities(hl, body), detect_frame(hl, body)
    got = images.collect_images(ent, fr, hl, cfg, body_text=body)
    lead = next((a for a in got if a.query == "이재명"), None)
    assert lead is not None and lead.is_lead is True
