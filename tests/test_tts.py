from dataclasses import replace
from pathlib import Path

from political_shorts.config import load_settings
from political_shorts.tts import synthesize_segments
from political_shorts import tts_providers


def test_registry_has_all_providers():
    assert set(tts_providers.REGISTRY) == {"edge", "elevenlabs", "azure", "gcloud", "openai"}


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
