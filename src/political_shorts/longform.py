"""LONGFORM PIPELINE — everything up to, but NOT including, the render.

A short covers ONE story in 30-60s. A longform video covers a THEME across
several related stories, in chapters, at 5-15 minutes. This module does:

    theme grouping -> chapter planning -> script assembly -> quality gate
    -> a plan artifact (JSON)

and deliberately stops there. Rendering is intentionally not implemented
(user: "일단은 영상 제작은 하지 말고 그 전단계 까지의 모든 과정") — see
`LongformPlan.render_ready` and the `NOT_IMPLEMENTED_RENDER` note below for
exactly where the next stage would attach.

DESIGN NOTE — why this reuses the shorts pipeline per chapter:
`script_gen.build_script()` already carries everything this project spent
its whole history getting right: two-stage LLM authorship (analyze, then
write), the fact-check engine's source-attributed units, jargon glossing,
sentence-completeness gates, the actor-attribution fixes, and the
`quality_agent` 95-point semantic gate. Writing a parallel longform script
generator would fork all of that and immediately start drifting. So each
chapter IS a `build_script()` result, and the longform layer only adds what
genuinely doesn't exist at shorts scale: theme selection across stories,
an opening that frames the whole theme, bridges between chapters, and a
closing synthesis.

KNOWN LIMITATION, on purpose: because each chapter comes from
`build_script()`, its narration is trimmed to a SHORTS length budget
(~40-60s per `storylen`). A real longform chapter has room to breathe and
should be longer. Expanding chapter text is the natural first job of
whoever builds the render stage — `est_seconds` here is therefore a FLOOR
(what the current, compressed text would run to), not a target.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .db import connect, recent_clusters
from .hook import strip_wire_marks
from .logging_setup import get_logger
from .textutil import clean_text
from .topics import story_signature

log = get_logger("longform")

# Where the render stage would attach. Nothing calls this yet, by design.
NOT_IMPLEMENTED_RENDER = (
    "longform render is intentionally not built yet — plan artifacts only. "
    "A render stage would consume LongformPlan.chapters[].segments the same "
    "way video.render_video consumes a shorts script's segments."
)

# Longform length bands, in MINUTES, by how much material the theme has.
# Conservative on purpose: a thin theme padded to 15 minutes is exactly the
# "공장형" output this project is trying not to make.
_BANDS = {
    "deep_dive": (3, 4, "집중 분석"),      # (min_chapters, max_chapters, label)
    "standard":  (5, 6, "종합 정리"),
    "roundup":   (7, 9, "주간 총정리"),
}

# A theme needs at least this much overlap with the seed story's keyword
# signature to join it. Tuned deliberately LOOSER than
# `topics.recent_duplicate`'s dedup threshold: dedup asks "is this the same
# story?" (must be strict), theme grouping asks "is this the same broad
# subject?" (should be generous), so the same number would be wrong here.
_THEME_OVERLAP = 0.28
_MIN_THEME_CHAPTERS = 3


@dataclass
class Chapter:
    cluster_id: int
    headline: str
    frame: str = ""
    n_sources: int = 0
    est_seconds: float = 0.0
    quality_score: int = 0
    quality_band: str = ""
    agent_score: int = 0
    agent_passed: bool = True
    bridge: str = ""          # the on-screen / spoken chapter lead-in
    segments: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)


@dataclass
class LongformPlan:
    theme: str = ""
    theme_keywords: list[str] = field(default_factory=list)
    length_class: str = ""
    length_label: str = ""
    title: list[str] = field(default_factory=list)
    opening: str = ""
    closing: str = ""
    chapters: list[Chapter] = field(default_factory=list)
    est_seconds: float = 0.0
    skipped: list[dict[str, Any]] = field(default_factory=list)
    render_ready: bool = False        # always False for now — see module docstring

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["render_note"] = NOT_IMPLEMENTED_RENDER
        return d


@dataclass
class Theme:
    keywords: list[str]
    cluster_ids: list[int]
    lead_title: str

    @property
    def size(self) -> int:
        return len(self.cluster_ids)


# --------------------------------------------------------------------------- #
# 1) theme grouping — which recent stories belong to one bigger subject
# --------------------------------------------------------------------------- #
def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def group_themes(cfg: Settings | None = None, window_hours: int = 72) -> list[Theme]:
    """Group recent clusters into broad themes, biggest theme first.

    Greedy single-pass grouping seeded by the biggest cluster: good enough
    here and, unlike a full clustering pass, it can't produce a theme with
    no clear lead story (the seed IS the lead, which is what the opening
    line and title get built from)."""
    cfg = cfg or settings
    since = int(__import__("time").time() - window_hours * 3600)
    with connect(cfg.db_path) as conn:
        rows = recent_clusters(conn, since)

    sigs: list[tuple[int, str, set[str]]] = []
    for r in rows:
        title = clean_text(r["lead_title"] or "")
        if not title:
            continue
        # entities aren't stored on the cluster row, so the signature comes
        # from the headline alone here — enough for broad-subject grouping
        # (it's the same token-keyword basis `story_signature` uses; only the
        # entity-derived keys are missing).
        sigs.append((int(r["id"]), title, story_signature(title, None)))

    themes: list[Theme] = []
    used: set[int] = set()
    for cid, title, sig in sigs:
        if cid in used:
            continue
        members = [cid]
        keys = set(sig)
        used.add(cid)
        for other_cid, _t, other_sig in sigs:
            if other_cid in used:
                continue
            if _overlap(sig, other_sig) >= _THEME_OVERLAP:
                members.append(other_cid)
                used.add(other_cid)
                keys |= other_sig
        themes.append(Theme(keywords=sorted(keys)[:12], cluster_ids=members,
                            lead_title=title))

    themes.sort(key=lambda t: t.size, reverse=True)
    log.info("longform: %d recent clusters -> %d themes (biggest=%d)",
             len(sigs), len(themes), themes[0].size if themes else 0)
    return themes


def _length_class(n_chapters: int) -> tuple[str, str]:
    for cls, (lo, hi, label) in _BANDS.items():
        if lo <= n_chapters <= hi:
            return cls, label
    if n_chapters > _BANDS["roundup"][1]:
        return "roundup", _BANDS["roundup"][2]
    return "deep_dive", _BANDS["deep_dive"][2]


# --------------------------------------------------------------------------- #
# 2) the longform-only text: opening / bridges / closing
# --------------------------------------------------------------------------- #
def theme_label(headline: str, limit: int = 24) -> str:
    """A short, readable subject phrase from a raw source headline.

    Korean wire headlines are typically TWO fragments glued with '...', often
    with one of them quoted ('오늘 증인 없는 청문회..."93명에 신약 투약"',
    '"악마" "독사"...김승원 청문회 여야 언쟁에 정회'). Embedding that raw in
    a spoken sentence nests quotes inside quotes and reads badly — the same
    defect the shorts YouTube title had to be fixed for.

    Three things this gets right, each learned from a real headline that
    broke a simpler version of this function:
      * split ONLY on the ellipsis — splitting on commas too cut
        '李대통령, 김지용 중수청장 후보 추가 검증 지시' down to a useless
        bare '李대통령';
      * drop quote MARKS but keep the words inside them — the quoted
        fragment is often the substantive part, so discarding it outright
        lost the actual subject;
      * take the LONGEST fragment, not the first — a headline that OPENS
        with a short quoted exclamation has its real content second.
    """
    h = strip_wire_marks(clean_text(headline))          # drop "(종합2보)" etc.
    h = re.sub(r"[\"“”'‘’]", "", h)
    parts = [p.strip(" .·-—,") for p in re.split(r"\.\.\.|…", h)]
    parts = [p for p in parts if len(p) >= 6]
    head = max(parts, key=len) if parts else h.strip(" .·-—,")
    if len(head) <= limit:
        return head
    cut = head.rfind(" ", 0, limit + 1)                 # never mid-word
    return (head[:cut] if cut >= limit - 8 else head[:limit]).rstrip(" ·,")


def _opening(theme: Theme, n_chapters: int) -> str:
    """Frames the WHOLE theme before chapter 1. Deliberately built from the
    deterministic template floor (not an LLM call): it states only what is
    structurally true — the subject and how many angles follow — so it can
    never invent a claim about the theme."""
    subject = theme_label(theme.lead_title)
    if n_chapters <= 1:
        return f"오늘은 '{subject}' 이야기를 자세히 짚어보겠습니다."
    return (f"오늘은 '{subject}' 이야기를 {n_chapters}개 갈래로 나눠서 "
            f"하나씩 짚어보겠습니다.")


def _bridge(i: int, chapter: Chapter) -> str:
    """One line between chapters. Same principle as `_opening` — structural
    only, no invented characterization of what follows."""
    return f"{i}. {theme_label(chapter.headline, limit=30)}"


def _closing(plan_chapters: list[Chapter]) -> str:
    n_src = len({s.get("name", "") for c in plan_chapters for s in c.sources if s.get("name")})
    return (f"여기까지 {len(plan_chapters)}개 갈래로 정리했습니다. "
            f"{n_src}개 매체 보도를 종합했고, 해석과 전망은 사실과 구분해 "
            f"표시했습니다. 원문은 더보기란에 있습니다.")


# --------------------------------------------------------------------------- #
# 3) public entry — build the plan, stop before the render
# --------------------------------------------------------------------------- #
def plan_longform(
    cfg: Settings | None = None,
    *,
    theme: Theme | None = None,
    max_chapters: int = 4,
    window_hours: int = 72,
    require_agent_pass: bool = True,
) -> LongformPlan:
    """Assemble a longform plan. Makes NO video and writes no mp4.

    Each chapter is a full `script_gen.build_script()` result, so this costs
    real LLM calls (the quality agent runs its own up-to-4-round loop per
    chapter) — `max_chapters` defaults low on purpose.

    `require_agent_pass`: a chapter whose quality-agent score never cleared
    the bar is recorded in `plan.skipped` and left OUT of the plan, matching
    the shorts pipeline's own standard. Set False to keep every chapter
    regardless (useful for inspecting a thin news day, not for publishing).
    """
    from .script_gen import build_script

    cfg = cfg or settings
    plan = LongformPlan()

    if theme is None:
        themes = [t for t in group_themes(cfg, window_hours)
                  if t.size >= _MIN_THEME_CHAPTERS]
        if not themes:
            log.info("longform: no theme has >=%d related stories in the last "
                     "%dh — nothing to plan", _MIN_THEME_CHAPTERS, window_hours)
            return plan
        theme = themes[0]

    plan.theme = clean_text(theme.lead_title)
    plan.theme_keywords = list(theme.keywords)

    for cid in theme.cluster_ids[:max_chapters]:
        try:
            script = build_script(cid, cfg)
        except Exception as exc:  # a bad cluster must not sink the whole plan
            log.warning("longform: cluster %d failed to script (%s) — skipping",
                        cid, exc)
            plan.skipped.append({"cluster_id": cid, "reason": f"{type(exc).__name__}: {exc}"})
            continue

        qa = script.get("quality_agent", {}) or {}
        ch = Chapter(
            cluster_id=cid,
            headline=script.get("headline", ""),
            frame=script.get("frame", ""),
            n_sources=int(script.get("n_sources", 0)),
            est_seconds=float(script.get("est_seconds", 0.0)),
            agent_score=int(qa.get("score", 0)),
            agent_passed=bool(qa.get("passed", True)),
            segments=script.get("segments", []),
            sources=script.get("sources", []),
        )
        if require_agent_pass and qa.get("available") and not qa.get("passed"):
            log.info("longform: chapter cluster=%d dropped — quality agent %s",
                     cid, ch.agent_score)
            plan.skipped.append({"cluster_id": cid, "headline": ch.headline,
                                 "reason": f"quality agent {ch.agent_score}"})
            continue
        plan.chapters.append(ch)

    if not plan.chapters:
        log.info("longform: theme %r produced no usable chapter", plan.theme)
        return plan

    plan.length_class, plan.length_label = _length_class(len(plan.chapters))
    plan.opening = _opening(theme, len(plan.chapters))
    plan.closing = _closing(plan.chapters)
    plan.title = [theme_label(theme.lead_title, limit=22), plan.length_label]
    for i, ch in enumerate(plan.chapters, 1):
        # stored on the chapter so a future render stage doesn't have to
        # re-derive chapter ordering to know what to put on screen
        ch.bridge = _bridge(i, ch)

    body = sum(c.est_seconds for c in plan.chapters)
    plan.est_seconds = round(body + len(plan.opening) / 7.0 + len(plan.closing) / 7.0, 1)
    plan.render_ready = False       # explicit: the render stage does not exist

    log.info("longform PLANNED theme=%r chapters=%d ~%.0fs (%s) skipped=%d",
             plan.theme, len(plan.chapters), plan.est_seconds,
             plan.length_class, len(plan.skipped))
    return plan


def write_plan(plan: LongformPlan, out_dir: Path | str, stamp: str = "") -> Path:
    """Persist the plan as JSON — the handoff artifact for a future render
    stage, and what a human reviews to judge the plan before any video
    exists."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or __import__("time").strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"longform_{stamp}.plan.json"
    path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    log.info("longform plan written -> %s", path)
    return path
