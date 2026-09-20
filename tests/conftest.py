import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network_research(monkeypatch):
    """The research stage calls the network (news search, web-search LLM,
    YouTube). No test may reach out — research tests import the real functions
    at module load and drive them with their own fakes."""
    from political_shorts import research

    def _offline(*a, **k):
        raise RuntimeError("network is disabled in tests")

    monkeypatch.setattr(research, "build_pack", lambda *a, **k: {})
    monkeypatch.setattr(research.requests, "get", _offline)
    monkeypatch.setattr(research.requests, "post", _offline)


@pytest.fixture(autouse=True)
def _fresh_llm_process_state():
    """The LLM layer keeps process-wide state (stage-1 analysis cache, rate-limit cooldowns).
    Reset it so one test's answers or 'cooling down' never leak into the next."""
    from political_shorts import llm, script_llm
    script_llm._ANALYSIS_CACHE.clear()
    llm._COOLDOWN.clear()
    yield
    script_llm._ANALYSIS_CACHE.clear()
    llm._COOLDOWN.clear()


@pytest.fixture(autouse=True)
def _research_off_by_default(monkeypatch):
    """Tests build scripts with a mocked LLM and no network. With research 'expected' the
    invariants would (correctly) hold every such script, so settings loaded inside a test start
    with research OFF; tests of the research/invariants layers switch it on explicitly."""
    monkeypatch.setenv("RESEARCH_ENABLED", "false")
