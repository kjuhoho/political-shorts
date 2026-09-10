"""QUALITY CHECKER — run after render, score the finished video, gate publish.

Deterministic checks against the script, the metadata (incl. the TIMELINE
ENGINE output), and — when ffprobe is available — the rendered file itself.
Produces a `QualityReport` with a 100-point score:

    CONTENT 25 · SUBTITLE 20 · VISUAL 20 · AUDIO 15 · STORY 10 · SOURCE 10

Band:  >=90 PASS · 80-89 MINOR_REVISION · 70-79 REVISION_REQUIRED · <70 REGENERATE
A fact-check or political-safety failure blocks auto-publish regardless of score.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("quality")

_MAX = {"content": 25, "subtitle": 20, "visual": 20, "audio": 15, "story": 10, "source": 10}
_STOP = set("은 는 이 가 을 를 에 의 로 과 와 도 만 한 그 이 저 것 수 등 및 더 좀".split())


@dataclass
class QualityReport:
    score: int = 0
    band: str = "REGENERATE"
    categories: dict[str, list[int]] = field(default_factory=dict)   # {cat: [got, max]}
    issues: list[dict[str, Any]] = field(default_factory=list)
    fact_check_ok: bool = True
    political_safety_ok: bool = True

    @property
    def publishable(self) -> bool:
        return (self.band in ("PASS", "MINOR_REVISION")
                and self.fact_check_ok and self.political_safety_ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score, "band": self.band, "publishable": self.publishable,
            "categories": {k: {"got": v[0], "max": v[1]} for k, v in self.categories.items()},
            "issues": self.issues, "fact_check_ok": self.fact_check_ok,
            "political_safety_ok": self.political_safety_ok,
        }


def _toks(s: str) -> set[str]:
    return {t for t in re.findall(r"[가-힣]{2,}|\d[\d,.]*", clean_text(s)) if t not in _STOP}


def _probe_duration(video_path: Path, cfg) -> float:
    exe = (getattr(cfg, "ffmpeg_path", "") or "ffmpeg").replace("ffmpeg", "ffprobe")
    if not (shutil.which(exe) or Path(exe).exists()):
        return 0.0
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "json", str(video_path)],
                             capture_output=True, text=True, timeout=20)
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def check(script: dict[str, Any], meta: dict[str, Any], video_path: Path,
          cfg: Any, safety: dict[str, Any] | None = None) -> QualityReport:
    r = QualityReport()
    issues = r.issues
    segs = script.get("segments", [])
    tl = meta.get("timeline", []) or []
    sources = script.get("sources", []) or meta.get("sources", []) or []
    fc = script.get("factcheck", {}) or {}
    band = script.get("length_band", [25, 95])
    src_text = clean_text(script.get("source_text", "") or (
        f"{script.get('headline','')} " + " ".join(s.get("text", "") for s in sources)))
    got: dict[str, float] = {k: v for k, v in _MAX.items()}

    def ding(cat: str, pts: float, code: str, msg: str, t0=None, t1=None, sev="warn"):
        got[cat] = max(0.0, got[cat] - pts)
        issues.append({"severity": sev, "code": code, "message": msg,
                       "t_start": t0, "t_end": t1})

    # ---------- CONTENT ----------
    # the grounded LLM legitimately paraphrases + adds background, so a low raw
    # token overlap is fine; only TOTAL drift + actual FABRICATION are dinged.
    narr_all = " ".join(s.get("narration", "") for s in segs)
    if src_text and narr_all:
        overlap = len(_toks(narr_all) & _toks(src_text)) / max(1, len(_toks(narr_all)))
        if overlap < 0.10:
            ding("content", 8, "content-drift",
                 f"대본이 기사와 거의 무관 ({overlap:.0%}) — 근거 이탈 의심", sev="critical")
        elif overlap < 0.22:
            ding("content", 3, "content-thin", f"대본-기사 어휘 겹침 낮음 ({overlap:.0%})")
    # a NUMBER in the narration that isn't in the source == fabrication
    bad_nums = sorted({n for s in segs for n in re.findall(r"\d[\d,.]*", s.get("narration", ""))
                       if len(n) >= 2 and n not in src_text})
    if bad_nums:
        ding("content", 9, "added-number",
             f"기사에 없는 수치가 대본에 있음: {', '.join(bad_nums[:4])}", sev="critical")
    # a party / politician NAME in the narration that isn't in the source
    _NAMES = ("이재명", "한동훈", "윤석열", "조국", "김민석", "이준석", "한덕수", "추경호",
              "박찬대", "우원식", "장동혁", "국민의힘", "더불어민주당", "민주당",
              "조국혁신당", "개혁신당", "정의당")
    bad_names = sorted({n for s in segs for n in _NAMES
                        if n in s.get("narration", "") and n not in src_text
                        and (n[:-1] not in src_text)})
    if bad_names:
        ding("content", 8, "added-name",
             f"기사에 없는 인물/정당이 대본에 있음: {', '.join(bad_names[:4])}", sev="critical")
    if not sources:
        ding("content", 6, "no-source", "출처가 하나도 없음", sev="critical")

    # ---------- SUBTITLE ----------
    spoken = [s for s in segs if s.get("narration") and s.get("role") != "factcheck"]
    missing_cap = [s for s in spoken if not clean_text(s.get("caption", ""))]
    if missing_cap:
        ding("subtitle", 8, "caption-missing",
             f"자막 없는 장면 {len(missing_cap)}개", sev="critical")
    not_verbatim = [s for s in spoken
                    if clean_text(s.get("caption", "")) != clean_text(s.get("narration", ""))]
    if not_verbatim:
        ding("subtitle", 6, "caption-not-verbatim",
             f"자막이 음성 대본과 다른 장면 {len(not_verbatim)}개 (전체 대본 자막 규칙)")
    too_long = [s for s in spoken if len(clean_text(s.get("caption", ""))) > 58]
    if too_long:
        ding("subtitle", 4, "caption-too-long",
             f"한 화면 자막이 3줄을 넘길 만큼 긴 장면 {len(too_long)}개")
    # sync: the clip must not end before the voice
    for t in tl:
        if t.get("audio_s", 0) and t.get("dur", 0) + 0.15 < t.get("audio_s", 0):
            ding("subtitle", 3, "sub-sync",
                 f"{t['start']}s 장면이 음성보다 짧음 (dur {t['dur']}s < audio {t['audio_s']}s)",
                 t.get("start"), t.get("end"), sev="critical")

    # ---------- VISUAL ----------
    n_scenes = len(segs)
    if n_scenes < 4:
        ding("visual", 6, "too-few-scenes", f"장면이 너무 적음 ({n_scenes})")
    if n_scenes > 34:
        ding("visual", 4, "too-many-scenes", f"장면이 너무 많음 ({n_scenes}) — 컷이 잘게 쪼개짐")
    # static span
    for t in tl:
        if (t.get("dur", 0) > 7.5 and t.get("role") != "factcheck"):
            ding("visual", 3, "static-span",
                 f"{t['start']}s 장면이 {t['dur']}s로 김 — 정지 느낌", t.get("start"), t.get("end"))
    # ---------- repetition detector ----------
    def _run_of(key: str, n: int, code: str, msg: str, pts: float):
        run, start_i = 1, 0
        for i in range(1, len(tl)):
            same = tl[i].get(key) and tl[i].get(key) == tl[i - 1].get(key)
            run = run + 1 if same else 1
            if same and run == n:
                ding("visual", pts, code, f"{msg}: {tl[i].get(key)} ×{n}",
                     tl[start_i].get("start"), tl[i].get("end"))
                return
            if not same:
                start_i = i
    _run_of("transition", 3, "repeat-transition", "같은 전환 연속", 2)
    _run_of("zoom", 3, "repeat-zoom", "같은 카메라 무빙 연속", 3)
    _run_of("layout", 5, "repeat-layout", "같은 레이아웃 연속", 2)
    _run_of("media", 4, "repeat-media", "같은 배경 종류 연속", 3)
    # a long stretch on the drawn backdrop (no real photo/video)
    bd = sum(1 for t in tl if t.get("media") == "backdrop")
    if tl and bd / len(tl) > 0.7:
        ding("visual", 4, "backdrop-heavy",
             f"장면 {bd}/{len(tl)}이 그린 배경 — 관련 사진/영상 부족")

    # ---------- AUDIO ----------
    silent = [s for s in segs if not s.get("narration") and s.get("role") != "outro"]
    if silent:
        ding("audio", 6, "silent-scene", f"음성 없는 본문 장면 {len(silent)}개", sev="critical")
    no_audio = [t for t in tl if t.get("role") not in ("outro",)
                and not t.get("audio_s") and t.get("caption")]
    if len(no_audio) > 1:
        ding("audio", 5, "tts-gap", f"TTS가 안 붙은 장면 {len(no_audio)}개")
    actual = _probe_duration(Path(video_path), cfg)
    tl_total = float(meta.get("duration_s") or (tl[-1]["end"] if tl else 0))
    if actual and tl_total and abs(actual - tl_total) > 1.2:
        ding("audio", 4, "duration-mismatch",
             f"실제 길이 {actual:.1f}s vs 타임라인 {tl_total:.1f}s", sev="critical")

    # ---------- STORY ----------
    roles = {s.get("role") for s in segs}
    if "hook" not in roles:
        ding("story", 4, "no-hook", "훅 장면이 없음")
    if "factcheck" not in roles:
        ding("story", 3, "no-factcheck", "확인된 사실(팩트체크) 장면이 없음")
    if "outro" not in roles:
        ding("story", 3, "no-conclusion", "마무리 장면이 없음")
    dur = actual or tl_total or script.get("est_seconds", 0)
    if dur and not (band[0] - 6 <= dur <= band[1] + 10):
        ding("story", 3, "length-off-band",
             f"길이 {dur:.0f}s가 유형 목표({band[0]:.0f}-{band[1]:.0f}s) 밖")

    # ---------- SOURCE ----------
    if len(sources) < 2:
        ding("source", 4, "single-source", f"출처가 {len(sources)}개 — 교차 확인 부족")
    real_leans = {s.get("lean") for s in sources} - {"wire", "center", None, ""}
    if len(real_leans) < 2 and len(sources) >= 2:
        ding("source", 2, "lean-skew", "편집 성향이 한쪽으로 치우침")
    fc_rows = next((s.get("rows") for s in segs if s.get("role") == "factcheck"), None)
    if fc_rows and not any(r.get("source") for r in fc_rows):
        ding("source", 3, "row-no-attrib", "팩트체크 표에 출처 표기가 없음")
    if not clean_text(script.get("disclaimer", "")):
        ding("source", 2, "no-disclaimer", "고지 문구 누락")

    # ---------- fact-check + safety gates ----------
    r.fact_check_ok = not fc.get("review_required")
    if not r.fact_check_ok:
        issues.append({"severity": "block", "code": "fact-review",
                       "message": f"POLITICAL_CONTENT_REVIEW_REQUIRED — {fc.get('review_reason','')}",
                       "t_start": None, "t_end": None})
    r.political_safety_ok = bool((safety or {}).get("passed", True))
    if not r.political_safety_ok:
        issues.append({"severity": "block", "code": "safety",
                       "message": "; ".join((safety or {}).get("blocks", [])),
                       "t_start": None, "t_end": None})

    # ---------- score ----------
    r.categories = {k: [int(round(got[k])), _MAX[k]] for k in _MAX}
    r.score = int(round(sum(got.values())))
    r.band = ("PASS" if r.score >= 90 else "MINOR_REVISION" if r.score >= 80
              else "REVISION_REQUIRED" if r.score >= 70 else "REGENERATE")
    log.info("quality score=%d band=%s fact_ok=%s safety_ok=%s issues=%d",
             r.score, r.band, r.fact_check_ok, r.political_safety_ok, len(issues))
    return r
