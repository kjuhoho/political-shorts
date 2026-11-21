from dataclasses import replace
from pathlib import Path

from political_shorts.config import load_settings
from political_shorts.tts import synthesize_segments
from political_shorts.voice import normalize, numbers, ssml
from political_shorts.voice.lexicon import apply as lex_apply
from political_shorts.voice.lexicon import spell_acronym
from political_shorts.voice.tools import doctor, preview


# --- numbers ---------------------------------------------------------------- #
def test_percent_and_decimal():
    assert numbers.read_numbers("지지율 27.3%") == "지지율 이십칠 점 삼 퍼센트"
    assert numbers.read_numbers("-2.1%p") == "마이너스 이 점 일 퍼센트 포인트"


def test_seats_and_counts_keep_the_number():
    assert numbers.read_numbers("108석") == "백팔 석"
    assert numbers.read_numbers("300명") == "삼백 명"
    assert "천이백삼십사" in numbers.read_numbers("1,234")


def test_money_units_are_spoken_not_dropped():
    out = numbers.read_numbers("1조 2000억 원")
    assert "조" in out and "억" in out and "일" in out and "이천" in out


def test_clock_uses_native_hours():
    assert numbers.read_numbers("오후 3시 30분") == "오후 세 시 삼십 분"


def test_date():
    assert numbers.read_numbers("2026년 9월 8일") == "이천이십육 년 구 월 팔 일"


def test_a_date_keeps_the_space_before_it():
    """'청와대는 21일' was read as '청와대는이십일 일' (the date rule swallowed the space) - one glued word per date."""
    assert numbers.read_numbers("청와대는 21일 입장을 냈다") == "청와대는 이십일 일 입장을 냈다"
    assert numbers.read_numbers("이 대통령이 21일 밝혔다") == "이 대통령이 이십일 일 밝혔다"
    assert numbers.read_numbers("지난 2026년 9월 18일 회견") == "지난 이천이십육 년 구 월 십팔 일 회견"
    assert numbers.read_numbers("여야는 3일 본회의를 열었다") == "여야는 삼 일 본회의를 열었다"


def test_no_digits_is_untouched():
    assert numbers.read_numbers("숫자가 전혀 없는 문장") == "숫자가 전혀 없는 문장"


# --- lexicon -------------------------------------------------------------- #
def test_acronym_spelled_out():
    assert spell_acronym("GTX") == "지티엑스"
    assert "지티엑스" in lex_apply("GTX 노선 예산")


def test_lexicon_fixed_reading():
    assert lex_apply("5·18 기념식") == "오일팔 기념식"


# --- normalize (full layer 1) ------------------------------------------- #
def test_prepare_runs_all_steps_and_closes_sentence():
    p = normalize.prepare("여론조사에서 48.5% 를 기록했다", role="what")
    assert p.text == "여론조사에서 사십팔 점 오 퍼센트 를 기록했다."
    assert p.prosody.sentence_silence > 0


def test_prepare_role_prosody_differs():
    hook = normalize.prepare("속보입니다", role="hook").prosody
    fact = normalize.prepare("확인해보겠습니다", role="factcheck").prosody
    assert hook.length_scale < fact.length_scale       # hook faster than fact-check


def test_ellipsis_becomes_breath():
    assert normalize.normalize_text("그런데... 결과는") == "그런데, 결과는."


# --- orchestrator flag ------------------------------------------------- #
def test_voice_pipeline_on_still_degrades_to_silent(tmp_path: Path, monkeypatch):
    from political_shorts import tts_providers
    import political_shorts.tts as tts_mod

    for name in list(tts_providers.REGISTRY):
        monkeypatch.setitem(
            tts_providers.REGISTRY, name,
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline test")),
        )
    monkeypatch.setattr(tts_mod, "_synth_sapi", lambda *a, **k: [None, None])
    cfg = replace(load_settings(), enable_tts=True, voice_pipeline=True, tts_provider="myvoice")
    ns = synthesize_segments(["국힘 108석", "그리고 27%"], tmp_path, cfg,
                             roles=["hook", "what"])
    assert len(ns) == 2 and all(n.wav_path is None for n in ns)


# --- ssml ------------------------------------------------------------------- #
def test_ssml_azure_has_prosody_and_breaks():
    prep = normalize.prepare("정부가 발표했다. 하지만 야당은 반대했다.", role="factcheck")
    xml = ssml.azure(prep, "ko-KR-SunHiNeural")
    assert xml.startswith("<speak") and "</speak>" in xml
    assert "<voice name='ko-KR-SunHiNeural'>" in xml
    assert "<prosody rate=" in xml and 'pitch="' in xml
    assert '<break time="' in xml          # sentence gap + contrast pause


def test_ssml_quoted_span_becomes_emphasis():
    prep = normalize.prepare('그는 "절대 물러서지 않겠다" 고 말했다', role="reaction")
    assert '<emphasis level="moderate">' in ssml.gcloud(prep)


def test_ssml_escapes_xml():
    # build a Prepared directly so clean_text() doesn't strip the angle brackets
    prep = normalize.Prepared("a < b & c", normalize.Prosody.for_role("what"), "what")
    out = ssml.gcloud(prep)
    assert "&lt;" in out and "&amp;" in out and "< b &" not in out


# --- tools ---------------------------------------------------------------- #
def test_doctor_runs_and_returns_int(capsys):
    rc = doctor(load_settings())
    assert isinstance(rc, int)
    assert "voice doctor" in capsys.readouterr().out


def test_dataset_build_missing_recordings_returns_1(tmp_path, capsys):
    from political_shorts.voice.dataset import build

    (tmp_path / "script.txt").write_text("01 첫 문장입니다\n02 두 번째 문장\n", encoding="utf-8")
    rc = build(str(tmp_path / "none"), script=str(tmp_path / "script.txt"),
               out=str(tmp_path / "ds"), cfg=load_settings())
    assert rc == 1
    assert "missing recordings" in capsys.readouterr().out


def test_preview_degrades_without_engine(tmp_path, monkeypatch):
    from political_shorts import tts_providers
    import political_shorts.tts as tts_mod

    for name in list(tts_providers.REGISTRY):
        monkeypatch.setitem(
            tts_providers.REGISTRY, name,
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline test")),
        )
    monkeypatch.setattr(tts_mod, "_synth_sapi", lambda *a, **k: [None])
    cfg = replace(load_settings(), enable_tts=True)
    res = preview("국민의힘 108석", roles=["hook"], cfg=cfg, out_dir=tmp_path)
    assert res == [("hook", None, 0.0)]
