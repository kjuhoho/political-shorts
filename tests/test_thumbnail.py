"""Until 2026-10-01 there was no cover at all: YouTube grabbed a frame, so the thumbnail was whatever the
opening card happened to be — the same building over and over."""
import dataclasses
from pathlib import Path

from PIL import Image

from political_shorts import thumbnail
from political_shorts.config import settings


def _photo(path: Path, size=(1200, 800), colour=(90, 120, 160)):
    Image.new("RGB", size, colour).save(path, "JPEG")
    return str(path)


def _script(tmp_path, **kw):
    s = {"headline": "강훈식 비서실장 전격 사의", "title": ["강훈식 전격 사의", "후임은 누구?"],
         "images": [{"kind": "portrait", "path": _photo(tmp_path / "face.jpg", (800, 1000)), "is_lead": True}]}
    s.update(kw)
    return s


def test_the_cover_is_what_youtube_accepts(tmp_path):
    out = thumbnail.build(_script(tmp_path), tmp_path / "v.jpg", settings)
    assert out and out.suffix == ".jpg" and out.stat().st_size <= thumbnail.MAX_BYTES
    with Image.open(out) as im:
        assert im.size == (thumbnail.W, thumbnail.H) == (1280, 720)
        assert im.format == "JPEG"


def test_it_is_built_on_the_story_s_own_picture(tmp_path):
    s = _script(tmp_path)
    assert thumbnail._lead_image(s) == s["images"][0]["path"]
    # no portrait: the establishing photo carries it
    s2 = _script(tmp_path, images=[{"kind": "photo", "path": _photo(tmp_path / "place.jpg")}])
    assert thumbnail._lead_image(s2).endswith("place.jpg")


def test_a_story_with_no_usable_picture_still_gets_a_cover(tmp_path):
    out = thumbnail.build(_script(tmp_path, images=[{"kind": "photo", "path": "C:/missing/none.jpg"}]),
                          tmp_path / "v.jpg", settings)
    assert out and out.exists()


def test_the_lines_come_from_the_title_and_stay_short(tmp_path):
    assert thumbnail._lines(_script(tmp_path)) == ["강훈식 전격 사의", "후임은 누구?"]
    assert thumbnail._lines({"title": ["한 줄#shorts"], "headline": "x"}) == ["한 줄"]
    assert thumbnail._lines({"title": [], "headline": "제목이 없을 때는 헤드라인에서 가져옵니다"})[0].startswith("제목이")


def test_a_broken_cover_never_blocks_a_video(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnail, "_lines", lambda s: 1 / 0)
    assert thumbnail.build(_script(tmp_path), tmp_path / "v.jpg", settings) is None


def test_the_publisher_sets_it_and_survives_a_refusal(tmp_path, monkeypatch):
    from political_shorts.publishers import youtube as Y

    cover = tmp_path / "cover.jpg"
    _photo(cover)
    calls = []

    class _Thumbs:
        def set(self, videoId, media_body):
            calls.append(videoId)
            raise RuntimeError("thumbnailNotVerified")      # a channel without the feature

    class _Videos:
        def insert(self, **kw):
            class _Req:
                def next_chunk(self):
                    return None, {"id": "VID123"}
            return _Req()

    class _YT:
        def videos(self):
            return _Videos()

        def thumbnails(self):
            return _Thumbs()

    import sys
    import types

    disc = types.ModuleType("googleapiclient.discovery")
    disc.build = lambda *a, **k: _YT()
    http = types.ModuleType("googleapiclient.http")
    http.MediaFileUpload = lambda *a, **k: object()
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", disc)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http)
    pub = Y.YouTubePublisher(dataclasses.replace(settings, enable_publish=True))
    monkeypatch.setattr(pub, "_credentials", lambda: object())
    res = pub._do_publish(tmp_path / "v.mp4", {"title": "t", "description": "d", "thumbnail": str(cover),
                                               "privacy_status": "public"})
    assert res.status == "ok" and res.remote_id == "VID123"   # the refusal did not sink the publish
    assert calls == ["VID123"]
