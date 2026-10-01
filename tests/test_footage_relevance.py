"""Incident 2026-09-30 (live audit): background clips had nothing to do with the search term.

The credits in the public description showed it: `[영상] korea national assembly →
traditional-korean-dance-festival-outdoors` ran over a story about National Assembly witnesses, and that frame
became the thumbnail. Also `pyongyang north korea → high-rise-buildings-overlooking-urban-street` and
`seoul national assembly → changdeokgung-palace-traditional-architecture-in-spring`.
"""
from political_shorts import footage


def _vid(slug: str, alt: str = "") -> dict:
    return {"url": f"https://www.pexels.com/video/{slug}-123456/", "alt": alt,
            "video_files": [{"height": 1280, "link": "https://x/f.mp4"}]}


def test_the_three_clips_that_actually_shipped_are_rejected():
    assert not footage._describes(_vid("traditional-korean-dance-festival-outdoors"), "korea national assembly")
    assert not footage._describes(_vid("high-rise-buildings-overlooking-urban-street"), "pyongyang north korea")
    assert not footage._describes(_vid("changdeokgung-palace-traditional-architecture-in-spring"),
                                  "seoul national assembly")
    assert not footage._describes(_vid("urban-intersection-with-tall-red-building"), "korea government building")


def test_a_clip_that_is_what_we_asked_for_is_kept():
    assert footage._describes(_vid("south-korea-national-assembly-building-exterior"), "korea national assembly")
    assert footage._describes(_vid("drone-shot-of-pyongyang-at-dusk"), "pyongyang north korea")
    assert footage._describes(_vid("press-conference-microphones-podium"), "korea press conference")
    assert footage._describes(_vid("stock-market-trading-screen"), "stock market trading board")


def test_the_country_and_city_words_alone_never_count_as_a_match():
    """Every Korean clip says 'korea' somewhere — matching on it is how the dance festival got through."""
    assert footage._anchors("korea national assembly") == {"national", "assembly"}
    assert footage._anchors("seoul courthouse") == {"courthouse"}
    assert not footage._describes(_vid("korean-street-food-market-at-night"), "korea national assembly")


def test_an_ambience_term_matches_anything_because_that_is_what_it_is_for():
    for term in ("seoul south korea city", "seoul street aerial", "korean flag waving"):
        assert footage._anchors(term) == set()
        assert footage._describes(_vid("anything-at-all"), term)


def test_alt_text_and_tags_count_too():
    v = _vid("clip-4821", alt="The National Assembly building in Seoul")
    assert footage._describes(v, "korea national assembly")
    v2 = _vid("clip-9931")
    v2["tags"] = [{"title": "courthouse"}, {"title": "law"}]
    assert footage._describes(v2, "seoul courthouse")


def test_search_skips_the_unrelated_clip_and_takes_the_next_one(monkeypatch, tmp_path):
    taken = {}

    class _R:
        status_code, text = 200, "{}"

        @staticmethod
        def json():
            return {"videos": [_vid("traditional-korean-dance-festival-outdoors"),
                               _vid("seoul-national-assembly-plenary-session")]}

    monkeypatch.setattr(footage._S, "get", lambda *a, **k: _R())
    monkeypatch.setattr(footage, "_download", lambda url, d, cap, suf: taken.setdefault("path", tmp_path / "v.mp4"))
    monkeypatch.setattr(footage, "_asset", lambda got, **kw: kw | {"path": str(got)})
    out = footage._pexels_videos("korea national assembly", "key", 1, tmp_path)
    assert out and out[0]["source_url"].endswith("seoul-national-assembly-plenary-session-123456/")


def test_without_strict_the_first_result_is_taken(monkeypatch, tmp_path):
    class _R:
        status_code, text = 200, "{}"

        @staticmethod
        def json():
            return {"videos": [_vid("traditional-korean-dance-festival-outdoors")]}

    monkeypatch.setattr(footage._S, "get", lambda *a, **k: _R())
    monkeypatch.setattr(footage, "_download", lambda url, d, cap, suf: tmp_path / "v.mp4")
    monkeypatch.setattr(footage, "_asset", lambda got, **kw: kw | {"path": str(got)})
    assert footage._pexels_videos("seoul street aerial", "key", 1, tmp_path, strict=False)
