"""AUTO REVISION — decide + targeted fixes for the fixable band."""
from political_shorts import revision
from political_shorts.quality import QualityReport


def _qr(band, fact_ok=True, safety_ok=True, issues=None):
    r = QualityReport(score={"PASS": 92, "MINOR_REVISION": 84,
                             "REVISION_REQUIRED": 74, "REGENERATE": 60}[band], band=band)
    r.fact_check_ok = fact_ok
    r.political_safety_ok = safety_ok
    r.issues = issues or []
    return r


def test_decide():
    assert revision.decide(_qr("PASS")) == "PASS"
    assert revision.decide(_qr("MINOR_REVISION")) == "PASS"
    assert revision.decide(_qr("REVISION_REQUIRED")) == "REVISE"
    assert revision.decide(_qr("REGENERATE")) == "HOLD"
    assert revision.decide(_qr("PASS", fact_ok=False)) == "HOLD"
    assert revision.decide(_qr("MINOR_REVISION", safety_ok=False)) == "HOLD"


def test_apply_shortens_read_time_on_length_issue():
    script = {"segments": [
        {"role": "what", "scene": {"min_read_s": 5.6}},
        {"role": "what", "scene": {"min_read_s": 3.0}},
        {"role": "hook", "scene": {"min_read_s": 2.0}},
    ]}
    qr = _qr("REVISION_REQUIRED", issues=[{"code": "length-off-band"}])
    script, changes = revision.apply(script, qr)
    assert changes
    assert script["segments"][0]["scene"]["min_read_s"] < 5.6
    assert script["segments"][2]["scene"]["min_read_s"] == 2.0     # under the 2.2 gate, untouched


def test_apply_reshuffles_camera_on_repeat_zoom():
    script = {"segments": [{"role": "what", "scene": {"zoom": "in"}} for _ in range(5)]}
    qr = _qr("REVISION_REQUIRED", issues=[{"code": "repeat-zoom"}])
    script, changes = revision.apply(script, qr)
    zooms = [s["scene"]["zoom"] for s in script["segments"]]
    for a, b in zip(zooms, zooms[1:]):
        assert a != b
