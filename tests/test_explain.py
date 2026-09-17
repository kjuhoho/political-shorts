"""explain.SIGNIFICANCE feeds the deterministic outro fallback whenever the
LLM's own outro gets rejected — it must never itself use the vague
"관망형" pattern script_llm._is_vague_outro() bans from the LLM."""
from political_shorts.explain import SIGNIFICANCE, significance
from political_shorts.script_llm import _is_vague_outro


class _Frame:
    def __init__(self, kind):
        self.kind = kind


def test_no_significance_entry_ends_vague():
    # a real shipped case: "personnel" ended "...주목됩니다" and "generic"
    # ended "...살펴볼 필요가 있습니다" — the exact pattern script_llm bans
    # from the LLM, quietly reintroduced by the deterministic fallback that
    # runs whenever the LLM's outro gets rejected for using it.
    for kind, text in SIGNIFICANCE.items():
        assert not _is_vague_outro(text), f"{kind!r}: {text!r} reads as a vague forecast"


def test_significance_returns_a_real_sentence_for_every_frame():
    for kind in ("personnel", "appoint", "vote", "clash", "scandal", "poll", "remark", "generic"):
        out = significance(_Frame(kind))
        assert out and not _is_vague_outro(out)
