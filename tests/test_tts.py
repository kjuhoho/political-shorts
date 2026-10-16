from dataclasses import replace
from pathlib import Path

from political_shorts.config import load_settings
from political_shorts.tts import synthesize_segments
from political_shorts import tts_providers


def test_registry_has_all_providers():
    assert set(tts_providers.REGISTRY) == {"edge", "fish", "elevenlabs", "azure", "gcloud", "openai"}


def test_fish_request_and_audio_file(tmp_path: Path, monkeypatch):
    cfg = replace(load_settings(), fish_api_key="test-key", fish_reference_id="voice-123", tts_rate=210)
    calls = []

    class Response:
        content = b"ID3" + b"\0" * 300

        def raise_for_status(self):
            pass

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(tts_providers.requests, "post", fake_post)
    paths = tts_providers.synth_fish(["안녕하세요"], tmp_path, cfg)
    assert paths == [tmp_path / "seg_00.mp3"]
    assert paths[0].read_bytes().startswith(b"ID3")
    assert calls[0][1]["headers"]["model"] == "s2.1-pro-free"
    assert calls[0][1]["json"] == {
        "text": "안녕하세요", "format": "mp3", "prosody": {"speed": 1.2},
        "reference_id": "voice-123",
    }


def test_fish_missing_key_fails_before_request(tmp_path: Path):
    cfg = replace(load_settings(), fish_api_key="")
    import pytest
    with pytest.raises(RuntimeError, match="FISH_API_KEY"):
        tts_providers.synth_fish(["안녕"], tmp_path, cfg)


def test_rate_percent_mapping():
    cfg = load_settings()
    assert tts_providers._rate_percent(replace(cfg, tts_rate=175)) == "+0%"
    assert tts_providers._rate_percent(replace(cfg, tts_rate=120)).startswith("-")
    assert tts_providers._rate_percent(replace(cfg, tts_rate=260)).startswith("+")


def test_disabled_tts_returns_silent(tmp_path: Path):
    cfg = replace(load_settings(), enable_tts=False)
    ns = synthesize_segments(["가", "나", "다"], tmp_path, cfg)
    assert len(ns) == 3
    assert all(n.wav_path is None and n.duration_s == 0.0 for n in ns)


def test_unknown_provider_falls_through_to_silent(tmp_path: Path, monkeypatch):
    # force every provider to raise so the chain ends at "silent"
    for name, fn in list(tts_providers.REGISTRY.items()):
        monkeypatch.setitem(
            tts_providers.REGISTRY, name,
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in test")),
        )
    cfg = replace(load_settings(), tts_provider="edge", enable_tts=True)
    import political_shorts.tts as tts_mod
    monkeypatch.setattr(tts_mod, "_synth_sapi", lambda *a, **k: [None, None])
    ns = synthesize_segments(["가", "나"], tmp_path, cfg)
    assert all(n.wav_path is None for n in ns)
