"""The channel posts twice a day, always: the slot gate and the guarantee ladder."""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from political_shorts import pipeline as P

KST = timezone(timedelta(hours=9))
_spec = importlib.util.spec_from_file_location("slot_gate", Path(__file__).resolve().parents[1] / "scripts" / "slot_gate.py")
G = importlib.util.module_from_spec(_spec)
sys.modules["slot_gate"] = G
_spec.loader.exec_module(G)


def _kst(m, d, h, mi=0):
    return datetime(2026, m, d, h, mi, tzinfo=KST)


# ------------------------------------------------------------------------------------ slot windows
def test_slot_windows_follow_the_kst_clock():
    assert G.slot_window(_kst(9, 20, 9, 30))[0] == "morning"
    assert G.slot_window(_kst(9, 20, 3, 59))[0] == "evening"          # 03:59 still belongs to yesterday's evening
    assert G.slot_window(_kst(9, 20, 4, 0))[0] == "morning"
    assert G.slot_window(_kst(9, 20, 13, 59))[0] == "morning"
    assert G.slot_window(_kst(9, 20, 14, 0))[0] == "evening"
    assert G.slot_window(_kst(9, 20, 23, 30))[0] == "evening"
    name, a, b = G.slot_window(_kst(9, 21, 1, 0))                     # 01:00 on the 21st = evening of the 20th
    assert name == "evening" and a == _kst(9, 20, 14).timestamp() and b == _kst(9, 21, 4).timestamp()


def test_a_filled_slot_skips_and_an_empty_one_runs():
    rows = [{"published_ts": _kst(9, 20, 9, 5).timestamp()}]           # a morning post
    assert G.slot_filled(rows, _kst(9, 20, 10, 40)) == ("morning", True)     # a late morning trigger -> skip
    assert G.slot_filled(rows, _kst(9, 20, 17, 37)) == ("evening", False)    # the evening still needs its post
    rows.append({"published_ts": _kst(9, 20, 21, 41).timestamp()})
    assert G.slot_filled(rows, _kst(9, 20, 22, 30)) == ("evening", True)
    assert G.slot_filled(rows, _kst(9, 21, 2, 10)) == ("evening", True)      # after midnight, same evening
    assert G.slot_filled(rows, _kst(9, 21, 7, 37)) == ("morning", False)     # the next morning is a new slot
    assert G.slot_filled([], _kst(9, 20, 7, 37)) == ("morning", False)


def test_manual_runs_are_never_skipped(monkeypatch, tmp_path, capsys):
    out = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
    assert G.main() == 0
    assert "skip=false" in out.read_text(encoding="utf-8")


def test_the_workflow_is_scheduled_several_times_per_slot_and_gated():
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-short.yml").read_text(encoding="utf-8")
    crons = [ln for ln in wf.splitlines() if ln.strip().startswith("- cron:")]
    assert len(crons) >= 6                                            # GitHub drops/delays cron: never rely on one trigger
    assert not any('cron: "0 ' in c for c in crons)                    # nothing on the hour (the busiest, most delayed minute)
    assert "needs: gate" in wf and "needs.gate.outputs.skip != 'true'" in wf


# -------------------------------------------------------------------------- the guarantee ladder
class _Rep:
    def __init__(self):
        self.stories = []


def _run_ladder(monkeypatch, cache, cluster_ids, publishable, guarantee=True, do_publish=True):
    calls = []

    class Cfg:
        guarantee_publish = guarantee

    def fake_process(cid, cfg, do_pub, report, **kw):
        calls.append((cid, kw.get("relax"), kw.get("min_agent_score"), kw.get("allow_thin")))
        class O:
            status = "built"
            held = cid not in publishable
        return O()

    monkeypatch.setattr(P, "_process_story", fake_process)
    report = _Rep()
    ready = lambda: sum(1 for s in report.stories if s.status == "built" and not s.held)  # noqa: E731
    P._guarantee_publish(Cfg(), do_publish, report, cache, cluster_ids, ready, 1)
    return calls


def _sc(ai, viol=0):
    return {"quality_agent": {"score": ai}, "invariant_violations": [{"code": "x"}] * viol}


def test_ladder_tries_the_best_built_candidate_first_with_every_style_bar_waived(monkeypatch):
    cache = {1: _sc(70, 2), 2: _sc(90, 0), 3: _sc(85, 0)}
    calls = _run_ladder(monkeypatch, cache, [1, 2, 3, 4], publishable={2})
    assert calls == [(2, True, 0, True)]                              # fewest violations, then highest AI score; stops once one ships


def test_ladder_keeps_walking_when_a_candidate_is_still_unusable(monkeypatch):
    cache = {1: _sc(90), 2: _sc(85), 3: _sc(80)}
    calls = _run_ladder(monkeypatch, cache, [1, 2, 3], publishable={3})
    assert [c[0] for c in calls] == [1, 2, 3]                          # 1 and 2 were unsafe/broken -> next


def test_ladder_builds_untried_clusters_when_nothing_was_built(monkeypatch):
    calls = _run_ladder(monkeypatch, {}, [7, 8, 9], publishable={8})
    assert [c[0] for c in calls] == [7, 8] and all(c[1] is True and c[3] is True for c in calls)


def test_ladder_does_nothing_when_disabled_or_not_publishing_or_already_satisfied(monkeypatch):
    assert _run_ladder(monkeypatch, {1: _sc(90)}, [1], {1}, guarantee=False) == []
    assert _run_ladder(monkeypatch, {1: _sc(90)}, [1], {1}, do_publish=False) == []
    calls = []

    class Cfg:
        guarantee_publish = True

    monkeypatch.setattr(P, "_process_story", lambda *a, **k: calls.append(1))
    P._guarantee_publish(Cfg(), True, _Rep(), {1: _sc(90)}, [1], lambda: 1, 1)      # a story already shipped
    assert calls == []


def test_relaxed_hold_rule_blocks_only_unsafe_or_broken_files():
    import inspect
    src = inspect.getsource(P._process_story)
    assert "qr.score < _RELAX_MIN_QUALITY or _render_defect(qr)" in src and "political_safety_ok" in src
    assert P._RELAX_MIN_QUALITY == 70
