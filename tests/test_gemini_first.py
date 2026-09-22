"""Shorts use Gemini first (user, 2026-09-22): the Groq free tier is shared with the longform workflow and was
already failing with 413/429. Groq stays as the fallback."""
import dataclasses
from pathlib import Path

from political_shorts import llm
from political_shorts.config import settings

ROOT = Path(__file__).resolve().parents[1]


def test_the_workflow_picks_gemini_whenever_its_key_exists():
    wf = (ROOT / ".github/workflows/daily-short.yml").read_text(encoding="utf-8")
    block = wf[wf.index('if [ -n "$PS_GEMINI_KEY" ]; then\n'):]
    assert block.index("LLM_PROVIDER=gemini") < block.index("LLM_PROVIDER=groq") < block.index("LLM_PROVIDER=openai")


def test_fallback_and_web_search_try_gemini_before_groq():
    assert llm._FALLBACK_ORDER.index("gemini") < llm._FALLBACK_ORDER.index("groq")
    assert llm._tiers()[0][0] == "gemini"
    assert llm._WEB_SEARCH_ORDER == ["gemini", "groq"]


def test_the_tiers_are_gemini_flash_then_groq_120b_then_gemini_lite(monkeypatch):
    """User, 2026-09-22: Gemini first; when the Flash models are spent, Groq's 120b before the weaker Flash-Lite."""
    calls = []

    def fake_gemini(prompt, cfg, max_tokens, system, models=None):
        calls.append(("gemini", tuple(models)))
        if any("lite" not in m for m in models):
            raise RuntimeError("gemini call failed (429 per day)")
        return "lite answer"

    def fake_groq(prompt, cfg, max_tokens, system, models=None):
        calls.append(("groq", tuple(models)))
        raise RuntimeError("groq call failed (413 request too large)")

    monkeypatch.setattr(llm, "_gemini", fake_gemini)
    monkeypatch.setattr(llm, "_groq", fake_groq)
    monkeypatch.setattr(llm, "_COOLDOWN", {})
    monkeypatch.setenv("GROQ_API_KEY", "q")
    cfg = dataclasses.replace(settings, llm_provider="gemini", llm_model="", gemini_api_key="g")
    assert llm.complete("x", cfg) == "lite answer"
    assert calls == [("gemini", tuple(llm._GEMINI_FLASH)), ("groq", ("openai/gpt-oss-120b",)),
                     ("gemini", tuple(llm._GEMINI_LITE))]


def test_the_shorts_never_use_the_groq_models_the_longform_uses():
    """The Groq free quota is per organisation and per model; the longform uses 20b / llama / qwen."""
    assert llm._GROQ_MODELS == ["openai/gpt-oss-120b"]
    assert [ms for prov, ms in llm._tiers() if prov == "groq"] == [["openai/gpt-oss-120b"]]


def test_a_tier_whose_models_are_all_cooling_down_costs_no_request(monkeypatch):
    import time

    sent = []
    monkeypatch.setattr(llm, "_gemini", lambda p, c, m, s, models=None: sent.append(("gemini", tuple(models))) or "ok")
    monkeypatch.setattr(llm, "_groq", lambda p, c, m, s, models=None: sent.append(("groq", tuple(models))) or "ok")
    monkeypatch.setattr(llm, "_COOLDOWN", {f"gemini:{m}": time.time() + 999 for m in llm._GEMINI_FLASH})
    monkeypatch.setenv("GROQ_API_KEY", "q")
    cfg = dataclasses.replace(settings, llm_provider="gemini", llm_model="", gemini_api_key="g")
    assert llm.complete("x", cfg) == "ok"
    assert sent == [("groq", ("openai/gpt-oss-120b",))]


class _Resp:
    def __init__(self, status, payload, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


def _gemini_env(monkeypatch, responder):
    posted = []

    def fake_post(url, params=None, json=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        posted.append((model, json))
        return responder(model, json, len(posted))

    monkeypatch.setattr(llm, "requests", type("m", (), {"post": staticmethod(fake_post)}))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "_COOLDOWN", {})
    return posted, dataclasses.replace(settings, llm_provider="gemini", llm_model="", gemini_api_key="g")


_OK = {"candidates": [{"content": {"parts": [{"text": '{"ok":1}'}]}, "finishReason": "STOP"}]}
_DAY = ('{"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"violations": [{"quotaId": '
        '"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}, {"retryDelay": "20s"}]}}')


def test_incident_thinking_ate_the_answer_so_thinking_is_low_with_headroom(monkeypatch):
    """Probe 2026-09-22: gemini-3.5-flash spent 673 of 700 tokens thinking and returned broken JSON."""
    posted, cfg = _gemini_env(monkeypatch, lambda m, j, n: _Resp(200, _OK))
    assert llm._gemini("x", cfg, 700, "", models=["gemini-3.5-flash"]) == '{"ok":1}'
    gen = posted[0][1]["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "low"}
    assert gen["maxOutputTokens"] == 700 + llm._THINKING_HEADROOM


def test_a_spent_daily_quota_is_not_retried_and_not_asked_again_this_run(monkeypatch):
    def responder(model, j, n):
        if model == "gemini-3.6-flash":
            return _Resp(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota"}},
                         text=_DAY)
        return _Resp(200, _OK)

    posted, cfg = _gemini_env(monkeypatch, responder)
    assert llm._gemini("x", cfg, 300, "", models=list(llm._GEMINI_FLASH)) == '{"ok":1}'
    assert [m for m, _ in posted] == ["gemini-3.6-flash", "gemini-3.5-flash"]      # one request, no retry
    posted.clear()
    assert llm._gemini("x", cfg, 300, "", models=list(llm._GEMINI_FLASH)) == '{"ok":1}'
    assert [m for m, _ in posted] == ["gemini-3.5-flash"]                          # the spent model is left alone


def test_an_overloaded_model_gets_one_retry_then_a_rest(monkeypatch):
    def responder(model, j, n):
        if model == "gemini-3.6-flash":
            return _Resp(503, {"error": {"status": "UNAVAILABLE", "message": "high demand"}})
        return _Resp(200, _OK)

    posted, cfg = _gemini_env(monkeypatch, responder)
    llm._gemini("x", cfg, 300, "", models=list(llm._GEMINI_FLASH))
    assert [m for m, _ in posted] == ["gemini-3.6-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
    posted.clear()
    llm._gemini("x", cfg, 300, "", models=list(llm._GEMINI_FLASH))
    assert [m for m, _ in posted] == ["gemini-3.5-flash"]


def test_a_model_that_rejects_the_thinking_setting_is_asked_again_without_it(monkeypatch):
    def responder(model, j, n):
        if "thinkingConfig" in j["generationConfig"]:
            return _Resp(400, {"error": {"status": "INVALID_ARGUMENT", "message": "Unknown name thinkingConfig"}})
        return _Resp(200, _OK)

    monkeypatch.setattr(llm, "_NO_THINKING_CFG", set())
    posted, cfg = _gemini_env(monkeypatch, responder)
    assert llm._gemini("x", cfg, 300, "", models=["gemini-3.5-flash"]) == '{"ok":1}'
    assert len(posted) == 2 and "thinkingConfig" not in posted[1][1]["generationConfig"]


def test_every_request_is_counted_for_the_run_summary(monkeypatch):
    import collections

    monkeypatch.setattr(llm, "USAGE", collections.Counter())
    posted, cfg = _gemini_env(monkeypatch, lambda m, j, n: _Resp(200, _OK))
    llm._gemini("x", cfg, 300, "", models=["gemini-3.5-flash"])
    assert llm.USAGE["gemini:gemini-3.5-flash:200"] == 1
    assert "gemini:gemini-3.5-flash:200=1" in llm.usage_summary()


def test_web_search_asks_gemini_first_and_groq_only_when_gemini_has_nothing(monkeypatch):
    order = []
    monkeypatch.setitem(llm._WEB_SEARCH, "gemini", lambda *a: order.append("gemini") or "")
    monkeypatch.setitem(llm._WEB_SEARCH, "groq", lambda *a: order.append("groq") or "답")
    assert llm.web_search("q", settings) == "답"
    assert order == ["gemini", "groq"]


def test_the_full_flash_models_come_before_the_lite_ones_and_no_retired_model_is_listed():
    """Incident 2026-09-22: every call used flash-lite because the 2.x models 404 and the alias was out of quota."""
    models = llm._GEMINI_MODELS
    first_lite = min(i for i, m in enumerate(models) if "lite" in m)
    assert all("lite" not in m for m in models[:first_lite]) and first_lite >= 2
    assert not any(m.startswith(("gemini-2.0", "gemini-2.5")) for m in models)


def _groq_env(monkeypatch, status_for):
    import political_shorts.llm as L

    posted = []

    class _R:
        def __init__(self, status):
            self.status_code = status

        def json(self):
            if self.status_code == 200:
                return {"choices": [{"message": {"content": '{"ok":1}'}}]}
            return {"error": {"message": {413: "Request too large for model", 429:
                    "Rate limit reached on tokens per minute (TPM). Please try again in 8.2s."}[self.status_code]}}

    def fake_post(url, headers=None, json=None, timeout=None):
        posted.append(len(json["messages"][1]["content"]))
        return _R(status_for(json))

    monkeypatch.setattr(L, "requests", type("m", (), {"post": staticmethod(fake_post)}))
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    monkeypatch.setattr(L, "_COOLDOWN", {})
    monkeypatch.setattr(L, "_GROQ_TOO_BIG", {})
    monkeypatch.setenv("GROQ_API_KEY", "k")
    return posted, dataclasses.replace(settings, llm_provider="groq", llm_model="")


def test_a_request_groq_rejected_as_too_large_is_not_sent_again_at_that_size(monkeypatch):
    """Evening run 2026-09-22: three 413s from gpt-oss-120b for the same kind of long prompt."""
    import pytest

    posted, cfg = _groq_env(monkeypatch, lambda j: 413 if len(j["messages"][1]["content"]) > 1000 else 200)
    with pytest.raises(RuntimeError):
        llm._groq("가" * 2000, cfg, 300, "")
    with pytest.raises(RuntimeError):
        llm._groq("가" * 3000, cfg, 300, "")               # bigger than a known 413: no request at all
    assert posted == [2000]
    assert llm._groq("짧은 요청", cfg, 300, "") == '{"ok":1}'   # a small request still goes through


def test_a_groq_rate_limit_is_waited_out_on_the_next_tier_not_retried(monkeypatch):
    import pytest

    posted, cfg = _groq_env(monkeypatch, lambda j: 429)
    with pytest.raises(RuntimeError):
        llm._groq("x", cfg, 300, "")
    with pytest.raises(RuntimeError):
        llm._groq("x", cfg, 300, "")                        # still cooling down: no request
    assert len(posted) == 1
