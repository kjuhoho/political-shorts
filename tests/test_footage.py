"""B-roll collection is opt-in, keyless-first (Commons), and best-effort."""
import dataclasses
import json

import pytest

from political_shorts import footage
from political_shorts.config import settings
from political_shorts.hook import detect_entities, detect_frame


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


HL = "국회 본회의서 예산안 처리 두고 여야 충돌"


def _frame_entities():
    return detect_entities(HL), detect_frame(HL)


def test_disabled_returns_nothing_and_makes_no_calls(monkeypatch):
    called = []
    monkeypatch.setattr(footage._S, "get", lambda *a, **k: called.append(a) or _Resp({}))
    ent, fr = _frame_entities()
    assert footage.collect_footage(ent, fr, HL, settings) == []
    assert called == []


def test_commons_only_no_key(monkeypatch, tmp_path):
    cfg = dataclasses.replace(settings, broll_enabled=True, pexels_api_key="",
                              broll_allow_commons=True,
                              broll_cache_dir=str(tmp_path), broll_max_count=2)

    commons_payload = {
        "query": {"pages": {"1": {
            "title": "File:National Assembly.webm",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/x/National_Assembly.webm",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:NA.webm",
                "mediatype": "VIDEO", "mime": "video/webm",
                "size": 5_000_000, "width": 1920, "height": 1080,
                "extmetadata": {"LicenseShortName": {"value": "CC BY 3.0"},
                                "Artist": {"value": "<a>Someone</a>"}},
            }],
        }}}
    }
    monkeypatch.setattr(footage._S, "get", lambda url, **k: _Resp(commons_payload))
    monkeypatch.setattr(footage, "_download",
                        lambda url, cache_dir, cap, suffix: tmp_path / f"clip{suffix}")

    ent, fr = _frame_entities()
    got = footage.collect_footage(ent, fr, HL, cfg)
    assert got and all(a.kind == "video" for a in got)
    assert got[0].license == "CC BY 3.0"
    assert len(got) <= 2


def test_low_resolution_commons_clip_is_skipped(monkeypatch, tmp_path):
    cfg = dataclasses.replace(settings, broll_enabled=True, pexels_api_key="",
                              broll_allow_commons=True,
                              broll_cache_dir=str(tmp_path))
    tiny = {"query": {"pages": {"1": {
        "title": "File:tiny.webm",
        "imageinfo": [{"url": "https://x/tiny.webm", "mediatype": "VIDEO",
                       "size": 1_000_000, "width": 480, "height": 360,
                       "extmetadata": {}}],
    }}}}
    monkeypatch.setattr(footage._S, "get", lambda *a, **k: _Resp(tiny))
    hits = []
    monkeypatch.setattr(footage, "_download",
                        lambda *a, **k: hits.append(1) or (tmp_path / "x.webm"))
    ent, fr = _frame_entities()
    assert footage.collect_footage(ent, fr, HL, cfg) == []
    assert hits == []                       # never even attempted the download


def test_martial_law_and_branded_commons_clips_are_blocked(monkeypatch, tmp_path):
    cfg = dataclasses.replace(settings, broll_enabled=True, pexels_api_key="",
                              broll_allow_commons=True,
                              broll_cache_dir=str(tmp_path))
    payload = {"query": {"pages": {
        "1": {"title": "File:Helicopters descend during the 2024 South Korea martial law.webm",
              "imageinfo": [{"url": "https://x/ml.webm", "mediatype": "VIDEO",
                             "size": 5_000_000, "width": 1920, "height": 1080,
                             "extmetadata": {}}]},
        "2": {"title": "File:이재명 climbs the National Assembly fence LIVE.webm",
              "imageinfo": [{"url": "https://x/fence.webm", "mediatype": "VIDEO",
                             "size": 5_000_000, "width": 1920, "height": 1080,
                             "extmetadata": {}}]},
    }}}
    monkeypatch.setattr(footage._S, "get", lambda *a, **k: _Resp(payload))
    dl = []
    monkeypatch.setattr(footage, "_download",
                        lambda *a, **k: dl.append(1) or (tmp_path / "x.webm"))
    ent, fr = _frame_entities()
    assert footage.collect_footage(ent, fr, HL, cfg) == []
    assert dl == []                         # neither charged clip was downloaded


def test_commons_not_touched_unless_opted_in(monkeypatch, tmp_path):
    cfg = dataclasses.replace(settings, broll_enabled=True, pexels_api_key="",
                              broll_allow_commons=False, broll_cache_dir=str(tmp_path))
    calls = []
    monkeypatch.setattr(footage._S, "get",
                        lambda url, **k: calls.append(url) or _Resp({"query": {"pages": {}}}))
    ent, fr = _frame_entities()
    footage.collect_footage(ent, fr, HL, cfg)
    assert not any("commons.wikimedia.org" in u for u in calls)


def test_pexels_used_when_key_present(monkeypatch, tmp_path):
    cfg = dataclasses.replace(settings, broll_enabled=True, pexels_api_key="KEY",
                              broll_cache_dir=str(tmp_path), broll_max_count=1)
    pex = {"videos": [{
        "url": "https://pexels.com/video/1", "user": {"name": "Cam"},
        "video_files": [{"height": 1080, "link": "https://pexels.com/x.mp4"}],
    }]}

    def fake_get(url, **k):
        if "commons.wikimedia.org" in url:
            return _Resp({"query": {"pages": {}}})
        return _Resp(pex)

    monkeypatch.setattr(footage._S, "get", fake_get)
    monkeypatch.setattr(footage, "_download",
                        lambda *a, **k: tmp_path / "pex.mp4")
    ent, fr = _frame_entities()
    got = footage.collect_footage(ent, fr, HL, cfg)
    assert got and got[0].license == "Pexels" and got[0].kind == "video"


def test_network_error_is_swallowed(monkeypatch, tmp_path):
    cfg = dataclasses.replace(settings, broll_enabled=True,
                              broll_cache_dir=str(tmp_path))

    def boom(*a, **k):
        raise RuntimeError("no net")

    monkeypatch.setattr(footage._S, "get", boom)
    ent, fr = _frame_entities()
    assert footage.collect_footage(ent, fr, HL, cfg) == []


def test_assign_images_prefers_video_for_context_cards():
    from political_shorts.video import _assign_images

    segs = [{"role": "hook", "caption": "김승원 공방", "narration": "", "kicker": ""},
            {"role": "what", "caption": "국회법 개정", "narration": "", "kicker": ""},
            {"role": "outro", "caption": "더보기", "narration": "", "kicker": ""}]
    imgs = [
        {"path": "P_김승원", "kind": "portrait", "query": "김승원", "is_lead": True},
        {"path": "V_assembly.webm", "kind": "video", "query": "국회"},
        {"path": "L_gwanghwamun.jpg", "kind": "photo", "query": "광화문"},
    ]
    out = _assign_images(segs, imgs, topic="김승원")
    # the content card takes the b-roll clip before the still photo
    assert out[1] == "V_assembly.webm"


def test_assign_images_interleaves_video_and_photo_context():
    from political_shorts.video import _assign_images

    # 6 context cards, 3 b-roll clips + 3 stills available. Before the fix the
    # context list was videos+photos so the first cards all got video and the
    # repetition detector flagged a "broll x4" run.
    segs = ([{"role": "hook", "caption": "훅", "narration": "", "kicker": ""}]
            + [{"role": "what", "caption": f"본문 {i}", "narration": "", "kicker": ""}
               for i in range(6)])
    imgs = [{"path": f"V_{i}.webm", "kind": "video", "query": "국회"} for i in range(3)]
    imgs += [{"path": f"L_{i}.jpg", "kind": "photo", "query": "거리"} for i in range(3)]
    out = _assign_images(segs, imgs, topic="")
    ctx = [p for p in out[1:] if p]
    vids = [p for p in ctx if p.startswith("V_")]
    stills = [p for p in ctx if p.startswith("L_")]
    assert vids and stills                          # both kinds actually used
    # no run of 3+ b-roll clips back to back
    run = 0
    for p in ctx:
        run = run + 1 if p.startswith("V_") else 0
        assert run < 3
