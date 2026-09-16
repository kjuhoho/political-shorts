"""AI QUALITY AGENT — a second, semantic quality gate. Best-effort like every
other LLM call in this codebase: unavailable never blocks (passed=True)."""
import dataclasses
import json

from political_shorts import llm, quality_agent
from political_shorts.config import settings


def _cfg(**kw):
    return dataclasses.replace(settings, llm_provider="gemini", gemini_api_key="k", **kw)


def _script(**over):
    base = {
        "headline": "예산안 국회 본회의 통과",
        "segments": [
            {"role": "hook", "narration": "예산안이 통과됐습니다."},
            {"role": "summary", "narration": "국회는 법을 만드는 곳입니다."},
        ],
    }
    base.update(over)
    return base


def test_no_provider_is_unavailable_and_never_blocks():
    rep = quality_agent.review(_script(), settings)   # provider ""
    assert rep.available is False
    assert rep.passed is True


def test_valid_response_parses_score_and_issues(monkeypatch):
    payload = json.dumps({"score": 62, "issues": [
        {"role": "hook", "problem": "훅이 본문과 무관한 인물을 언급합니다."},
    ]})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    rep = quality_agent.review(_script(), _cfg())
    assert rep.available is True
    assert rep.score == 62
    assert rep.passed is False                       # below PASS_SCORE (95)
    assert "훅이 본문과" in rep.feedback_text()


def test_score_at_or_above_bar_passes(monkeypatch):
    payload = json.dumps({"score": 97, "issues": []})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    rep = quality_agent.review(_script(), _cfg())
    assert rep.passed is True


def test_json_fence_is_stripped(monkeypatch):
    payload = "```json\n" + json.dumps({"score": 90, "issues": []}) + "\n```"
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    rep = quality_agent.review(_script(), _cfg())
    assert rep.available is True and rep.score == 90


def test_unparseable_response_gives_up_without_blocking(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "I can't do that")
    rep = quality_agent.review(_script(), _cfg())
    assert rep.available is False
    assert rep.passed is True                         # never blocks on its own failure


def test_network_error_gives_up_without_blocking(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(llm, "complete", boom)
    rep = quality_agent.review(_script(), _cfg())
    assert rep.available is False
    assert rep.passed is True


def test_score_is_clamped_into_0_100(monkeypatch):
    payload = json.dumps({"score": 140, "issues": []})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    rep = quality_agent.review(_script(), _cfg())
    assert rep.score == 100


def test_empty_script_is_unavailable():
    rep = quality_agent.review({"headline": "", "segments": []}, _cfg())
    assert rep.available is False
