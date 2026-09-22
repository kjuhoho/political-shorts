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
    assert llm._WEB_SEARCH_ORDER == ["gemini", "groq"]


def test_a_gemini_failure_falls_back_to_groq(monkeypatch):
    calls = []

    def fake(provider, prompt, cfg, max_tokens, system):
        calls.append(provider)
        if provider == "gemini":
            raise RuntimeError("gemini call failed (429)")
        return "ok"

    monkeypatch.setattr(llm, "_call", fake)
    monkeypatch.setenv("GROQ_API_KEY", "q")
    cfg = dataclasses.replace(settings, llm_provider="gemini", gemini_api_key="g")
    assert llm.complete("x", cfg) == "ok"
    assert calls == ["gemini", "groq"]


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
