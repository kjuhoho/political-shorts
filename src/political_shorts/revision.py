"""AUTO REVISION.

Given a `QualityReport`, decide what to do and, for the fixable middle band,
apply *targeted, reversible* changes to the script and ask for one re-render.

  >=90  PASS               게시
  80-89 MINOR_REVISION     게시 (경고만)
  70-79 REVISION_REQUIRED  자동 수정 1회 → 재렌더 → 재검사
  <70   REGENERATE         보류 (사람이 봐야 함 — 자동 재생성 안 함)

A fact-check review flag or a safety block always means "보류" regardless of
band. Fixes here never rewrite content — they only nudge pacing / variety /
length knobs the pipeline already understands.
"""
from __future__ import annotations

from typing import Any

MAX_PASSES = 1


def decide(qr) -> str:
    if not qr.fact_check_ok or not qr.political_safety_ok:
        return "HOLD"
    return {"PASS": "PASS", "MINOR_REVISION": "PASS",
            "REVISION_REQUIRED": "REVISE", "REGENERATE": "HOLD"}.get(qr.band, "HOLD")


def _codes(qr) -> set[str]:
    return {i.get("code") for i in getattr(qr, "issues", [])}


def apply(script: dict[str, Any], qr) -> tuple[dict[str, Any], list[str]]:
    """Mutate `script` in place for a re-render. Returns (script, [what changed])."""
    codes = _codes(qr)
    done: list[str] = []
    segs = script.get("segments", [])

    # too long / static spans -> pull subtitle read-time in (renderer caps the
    # silent tail from min_read_s, so scaling it down shortens the holds)
    if codes & {"length-off-band", "static-span"}:
        scaled = 0
        for s in segs:
            sc = s.get("scene")
            if sc and sc.get("min_read_s", 0) > 2.2:
                sc["min_read_s"] = round(sc["min_read_s"] * 0.85, 2)
                scaled += 1
        if scaled:
            done.append(f"자막 유지시간 15% 단축 ({scaled}개 장면)")

    # a genuinely over-long single scene -> also lower its ceiling
    for iss in getattr(qr, "issues", []):
        if iss.get("code") == "static-span" and iss.get("t_start") is not None:
            for s in segs:
                sc = s.get("scene") or {}
                if abs((sc.get("min_read_s") or 0)) > 5.0:
                    sc["min_read_s"] = 5.0
            done.append("과도하게 긴 장면 상한 5초로 제한")
            break

    # camera-move / transition monotony -> reshuffle the rotation phase so
    # scene._assign_moves lands differently on the next plan… but scenes are
    # already planned here, so nudge them directly.
    if codes & {"repeat-zoom", "repeat-transition"}:
        _ROT = ["in", "pan_right", "out", "pan_left"]
        prev = ""
        for k, s in enumerate(segs):
            sc = s.get("scene")
            if not sc:
                continue
            z = _ROT[(k + 2) % 4]
            if z == prev:
                z = _ROT[(k + 3) % 4]
            sc["zoom"] = z
            prev = z
        done.append("장면별 카메라 무빙 재배치")

    return script, done
