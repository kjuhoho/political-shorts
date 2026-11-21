"""The TTS fallback chain: engines are tried in order, a video gets ONE voice, and a metered engine is counted so a
free tier is left before it is spent (Google does not stop at the end of its free tier - it bills)."""
import base64
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from political_shorts import tts as T, tts_providers as P, tts_usage as U
from political_shorts.config import load_settings

TEXTS = ["첫 번째 문장입니다.", "두 번째 문장입니다.", "세 번째 문장입니다."]
CHARS = sum(len(t) for t in TEXTS)


def cfg_for(tmp_path, **kw):
    base = dict(enable_tts=True, voice_pipeline=False, data_dir=tmp_path, tts_provider="edge",
                tts_providers="gcloud,edge", tts_caps="", google_tts_api_key="k")
    base.update(kw)
    return replace(load_settings(), **base)


class _Http(Exception):
    """What requests/aiohttp raise: a status code on .response (requests) and a body to read."""
    def __init__(self, status, text=""):
        super().__init__(f"{status}")
        self.response = type("R", (), {"status_code": status, "text": text})()


def fake(name, plan):
    """An engine whose segments follow `plan`: 'ok', or an HTTP status for a refused segment. Records its calls and
    reports to tts_usage exactly like the real providers (chars on success, the failure otherwise)."""
    calls = []

    def fn(texts, out_dir, cfg):
        calls.append(list(texts))
        out = []
        for i, t in enumerate(texts):
            step = plan[i] if i < len(plan) else plan[-1]
            if step == "ok":
                p = out_dir / f"seg_{i:02d}.mp3"
                p.write_bytes(name.encode() * 200)                 # the bytes say which engine spoke
                U.note_chars(len(t))
                out.append(p)
            else:
                U.note_failure(_Http(step))
                out.append(None)
        return out

    fn.calls = calls
    return fn


@pytest.fixture
def engines(monkeypatch, tmp_path):
    def install(**plans):
        made = {}
        for name, plan in plans.items():
            made[name] = fake(name, plan)
            monkeypatch.setitem(P.REGISTRY, name, made[name])
        return made
    monkeypatch.setattr(T, "_synth_sapi", lambda *a, **k: pytest.fail("SAPI must not run"))
    return install


def _voices(paths):
    return {p.read_bytes()[:4] for p in paths if p}


# ---------------------------------------------------------------------------------------------- the order
def test_the_chain_is_the_listed_engines_then_edge_then_sapi(tmp_path):
    assert T._chain(cfg_for(tmp_path, tts_providers="gcloud, Azure")) == ["gcloud", "azure", "edge", "sapi"]
    assert T._chain(cfg_for(tmp_path, tts_providers="", tts_provider="edge")) == ["edge", "sapi"]
    assert T._chain(cfg_for(tmp_path, tts_providers="", tts_provider="piper")) == ["piper", "edge", "sapi"]
    assert T._chain(cfg_for(tmp_path, tts_providers="edge,gcloud")) == ["edge", "gcloud", "sapi"]


def test_the_first_engine_that_makes_every_segment_narrates_and_the_rest_are_not_called(tmp_path, engines):
    e = engines(gcloud=["ok"], edge=["ok"])
    paths, used = T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path), ssml=False)
    assert used == "gcloud" and _voices(paths) == {b"gclo"}
    assert e["edge"].calls == []


# ---------------------------------------------------------------- one voice per video, no half-narrated video
def test_a_half_narrated_video_is_thrown_away_and_the_next_engine_narrates_all_of_it(tmp_path, engines):
    e = engines(gcloud=["ok", "ok", 500], edge=["ok"])             # the third segment fails (not a quota error)
    paths, used = T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path), ssml=False)
    assert used == "edge"
    assert len(paths) == 3 and all(paths)
    assert _voices(paths) == {b"edge"}                             # all three from edge: never two voices in a video


def test_when_no_engine_is_complete_the_one_with_most_segments_wins_over_robotic_sapi(tmp_path, engines):
    engines(gcloud=["ok", 500, 500], edge=["ok", "ok", 500])
    paths, used = T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path), ssml=False)
    assert used == "edge" and sum(1 for p in paths if p) == 2      # sapi was not tried (the fixture fails if it is)


def test_sapi_runs_only_when_nothing_else_made_a_sound(tmp_path, monkeypatch, engines):
    engines(gcloud=[500], edge=[500])
    monkeypatch.setattr(T, "_synth_sapi", lambda texts, d, c: [d / "s.wav"] * len(texts))
    paths, used = T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path), ssml=False)
    assert used == "sapi" and all(paths)


def test_nothing_working_means_silent_cards_not_a_crash(tmp_path, monkeypatch, engines):
    engines(gcloud=[500], edge=[500])
    monkeypatch.setattr(T, "_synth_sapi", lambda texts, d, c: [None] * len(texts))
    paths, used = T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path), ssml=False)
    assert used == "none" and not any(paths)


def test_a_segment_without_words_does_not_make_a_video_incomplete():
    p = Path("x.mp3")
    assert T._is_complete([p, None], ["말", ""])
    assert not T._is_complete([None], [""])                        # nothing to say is not a narrated video
    assert not T._is_complete([p, None], ["말", "말"])


# ------------------------------------------------------------------------- quota: refused -> cool down
def test_a_quota_refusal_hands_the_video_over_and_the_next_one_does_not_even_try_that_engine(tmp_path, engines):
    e = engines(gcloud=["ok", "ok", 429], edge=["ok"])
    cfg = cfg_for(tmp_path)
    paths, used = T._run_chain(TEXTS, tmp_path, cfg, ssml=False)
    assert used == "edge" and len(e["gcloud"].calls) == 1
    state = json.loads((tmp_path / "tts_usage.json").read_text(encoding="utf-8"))
    assert state["chars"]["gcloud"] == len(TEXTS[0]) + len(TEXTS[1])      # what Google accepted is booked
    assert "gcloud" in state["cooldown"]

    paths, used = T._run_chain(TEXTS, tmp_path, cfg, ssml=False)           # the next video
    assert used == "edge" and len(e["gcloud"].calls) == 1                  # gcloud was skipped, not called again


def test_a_quota_word_in_the_body_counts_even_without_a_429(tmp_path, engines, monkeypatch):
    def quota_fn(texts, out_dir, cfg):
        U.note_failure(_Http(400, '{"error": {"status": "RESOURCE_EXHAUSTED"}}'))
        return [None] * len(texts)
    monkeypatch.setitem(P.REGISTRY, "gcloud", quota_fn)
    engines(edge=["ok"])
    T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path), ssml=False)
    assert "gcloud" in json.loads((tmp_path / "tts_usage.json").read_text(encoding="utf-8"))["cooldown"]


def test_an_ordinary_failure_is_not_a_cooldown(tmp_path, engines):
    engines(gcloud=["ok", "ok", 500], edge=["ok"])
    T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path), ssml=False)
    state = json.loads((tmp_path / "tts_usage.json").read_text(encoding="utf-8"))
    assert state["cooldown"] == {}                                         # a 500 / timeout is retried next time


def test_the_cooldown_ends(tmp_path, engines, monkeypatch):
    e = engines(gcloud=["ok", "ok", 429], edge=["ok"])
    cfg = cfg_for(tmp_path)
    T._run_chain(TEXTS, tmp_path, cfg, ssml=False)
    later = datetime.now(timezone.utc) + timedelta(hours=U.COOLDOWN_HOURS + 1)
    monkeypatch.setattr(U, "_now", lambda: later)
    e["gcloud"].calls.clear()
    T._run_chain(TEXTS, tmp_path, cfg, ssml=False)
    assert len(e["gcloud"].calls) == 1                                     # tried again


# ------------------------------------------------- the monthly cap: Google bills past its free tier by itself
def test_a_video_that_would_pass_the_cap_never_reaches_the_engine(tmp_path, engines):
    (tmp_path / "tts_usage.json").write_text(
        json.dumps({"month": U.month_key(), "chars": {"gcloud": 799_990}, "cooldown": {}}), encoding="utf-8")
    e = engines(gcloud=["ok"], edge=["ok"])
    paths, used = T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path), ssml=False)
    assert used == "edge" and e["gcloud"].calls == []


def test_what_a_metered_engine_accepts_is_booked_and_survives_a_restart(tmp_path, engines):
    engines(gcloud=["ok"], edge=["ok"])
    cfg = cfg_for(tmp_path)
    T._run_chain(TEXTS, tmp_path, cfg, ssml=False)
    T._run_chain(TEXTS, tmp_path, cfg, ssml=False)
    assert U.Ledger.load(cfg).used("gcloud") == 2 * CHARS


def test_a_new_month_starts_from_zero(tmp_path):
    (tmp_path / "tts_usage.json").write_text(
        json.dumps({"month": "2020-01", "chars": {"gcloud": 10_000_000}}), encoding="utf-8")
    assert U.Ledger.load(cfg_for(tmp_path)).used("gcloud") == 0


def test_a_corrupt_ledger_is_not_fatal(tmp_path, engines):
    (tmp_path / "tts_usage.json").write_text("{not json", encoding="utf-8")
    engines(gcloud=["ok"], edge=["ok"])
    assert T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path), ssml=False)[1] == "gcloud"


def test_unmetered_engines_never_touch_the_ledger(tmp_path, engines):
    engines(edge=["ok"])
    T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path, tts_providers="edge"), ssml=False)
    assert not (tmp_path / "tts_usage.json").exists()


def test_caps_can_be_overridden_removed_or_zeroed(tmp_path):
    c = U.caps(cfg_for(tmp_path, tts_caps="gcloud:600000, azure:none, elevenlabs:0, bogus:x"))
    assert c["gcloud"] == 600_000 and "azure" not in c and c["elevenlabs"] == 0
    assert U.caps(cfg_for(tmp_path)) == U.DEFAULT_CAPS


def test_a_zero_cap_switches_the_engine_off(tmp_path, engines):
    e = engines(gcloud=["ok"], edge=["ok"])
    assert T._run_chain(TEXTS, tmp_path, cfg_for(tmp_path, tts_caps="gcloud:0"), ssml=False)[1] == "edge"
    assert e["gcloud"].calls == []


# ------------------------------------------------------------------------------------ the real Google call
class _Resp:
    def __init__(self, status, body=b"", text=""):
        self.status_code, self.content, self.text = status, body, text

    def json(self):
        return {"audioContent": base64.b64encode(b"x" * 400).decode()}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error: for url: "
                                     "https://texttospeech.googleapis.com/v1/text:synthesize?key=SECRETKEY123",
                                     response=self)


def test_google_books_the_billed_characters_flags_the_429_and_never_logs_its_key(tmp_path, monkeypatch, caplog):
    answers = iter([_Resp(200), _Resp(200), _Resp(429, text="quota")])
    monkeypatch.setattr(P.requests, "post", lambda *a, **k: next(answers))
    caplog.set_level(logging.WARNING)
    U.begin()
    out = P.synth_gcloud(TEXTS, tmp_path, cfg_for(tmp_path, google_tts_api_key="SECRETKEY123"))
    att = U.end()
    assert [bool(p) for p in out] == [True, True, False]
    assert att.chars == len(TEXTS[0]) + len(TEXTS[1]) and att.hard
    assert "SECRETKEY123" not in caplog.text and "key=***" in caplog.text


def test_google_counts_ssml_tags_but_not_breaks():
    ssml = "<speak><prosody rate='+3%'>안녕<break time='300ms'/>하세요</prosody></speak>"
    assert U.billable(ssml, keep_tags=True) == len(ssml) - len("<break time='300ms'/>")
    assert U.billable(ssml) == len("안녕하세요")                         # the others bill the words only


def test_a_voice_name_belongs_to_one_engine(tmp_path):
    edge_voice = cfg_for(tmp_path, tts_voice="ko-KR-InJoonNeural")
    assert P._voice(edge_voice, "edge") == "ko-KR-InJoonNeural"
    assert P._voice(edge_voice, "gcloud") == P.DEFAULT_VOICES["gcloud"]   # not a Google voice: ignored there
    g_voice = cfg_for(tmp_path, tts_voice="ko-KR-Neural2-C")
    assert P._voice(g_voice, "gcloud") == "ko-KR-Neural2-C"
    assert P._voice(g_voice, "edge") == P.DEFAULT_VOICES["edge"]


# ------------------------------------------------------------------------------------------- end to end
def test_synthesize_segments_uses_the_chain(tmp_path, engines):
    engines(gcloud=["ok", "ok", 429], edge=["ok"])
    ns = T.synthesize_segments(TEXTS, tmp_path, cfg_for(tmp_path))
    assert len(ns) == 3 and all(n.wav_path for n in ns)
    assert {n.wav_path.read_bytes()[:4] for n in ns} == {b"edge"}
