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
