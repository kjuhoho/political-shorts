"""End-to-end orchestration: collect -> classify -> cluster -> (per story)
analyze -> script -> safety -> render -> metadata -> persist -> publish.
"""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .classify import classify_pending
from .collect import collect
from .config import Settings, settings
from .db import (
    connect,
    finish_job,
    init_db,
    log_publish,
    record_topic,
    save_script,
    save_video,
    set_cluster_status,
    start_job,
)
from .dedupe import build_clusters
from .logging_setup import get_logger
from .metadata import build_metadata, write_sidecar
from . import quality_agent
from .safety import review_script
from .script_gen import build_script
from .topics import recent_duplicate, signature_str, story_signature
from .video import FFmpegMissing, render_video

log = get_logger("pipeline")


@dataclass
class StoryOutcome:
    cluster_id: int
    headline: str = ""
    status: str = ""          # built | skipped | error
    video_path: str = ""
    reason: str = ""
    quality_score: int = 0
    quality_band: str = ""
    safety_warnings: list[str] = field(default_factory=list)
    publishes: list[dict[str, Any]] = field(default_factory=list)
    held: bool = False        # built but quality-held from publish — doesn't
                              # count toward the day's "found a good one" quota
    agent_score: int = 0      # the AI quality agent's score, whenever it ran
                              # (available) — set regardless of pass/fail, so
                              # a caller can pick the best of several SKIPPED
                              # candidates for the fallback-floor retry below


@dataclass
class RunReport:
    job_id: int = 0
    collected: int = 0
    classified: int = 0
    politics: int = 0
    clusters: int = 0
    built: int = 0
    skipped: int = 0
    published: int = 0
    stories: list[StoryOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["stories"] = [s.__dict__ for s in self.stories]
        return d


def _safe_slug(text: str, limit: int = 40) -> str:
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in text]
    slug = "".join(keep).strip("_")
    return (slug[:limit] or "story").lower()


def _theme_saturated(conn, sig, actor: str, cfg: Settings) -> str:
    """The story isn't a duplicate, but the channel just ran 2+ shorts on the
    same actor/theme (e.g. a week of one saga). Return a reason to hold it back,
    or '' to proceed. Bypassed on the no-fresh-story fallback pass."""
    from .db import recent_topics
    from .topics import signature_str

    recent = recent_topics(conn, int(__import__("time").time()) - 3 * 86400)
    words = set(signature_str(sig).split())
    hits = 0
    for r in list(recent)[:4]:
        prev = set((r["signature"] or "").split())
        if (actor and r["actor"] and r["actor"] == actor) or len(words & prev) >= 3:
            hits += 1
    return (f"최근 3일 영상 {hits}건과 주제·인물이 겹침 (다양성 확보)"
            if hits >= 2 else "")


def _process_story(
    cluster_id: int, cfg: Settings, do_publish: bool, report: RunReport,
    enforce_variety: bool = True, min_agent_score: int | None = None,
) -> StoryOutcome:
    out = StoryOutcome(cluster_id=cluster_id)
    try:
        script = build_script(cluster_id, cfg)
        out.headline = script["headline"]

        # Skip a story with too little material to actually explain, not just
        # summarize — a real case that shipped: 1 source, 1 extracted fact, 1
        # claim, 1 interpretation. There wasn't enough to write a background
        # card OR a "what happened" card OR a "sides" card from, so all three
        # got silently dropped by the length-budget trim, leaving hook -> a
        # raw fact dump -> outro with zero context for a viewer who doesn't
        # follow politics. Better to skip it and wait for more coverage than
        # publish something with nothing to explain.
        counts = script.get("counts", {}) or {}
        material = (counts.get("facts", 0) + counts.get("claims", 0)
                   + counts.get("interpretations", 0))
        if script.get("n_sources", 0) <= 1 and material <= 3:
            out.status = "skipped"
            out.reason = f"소재 부족 (출처 {script.get('n_sources', 0)}개, 사실/주장/해석 합계 {material}개)"
            with connect(cfg.db_path) as conn:
                set_cluster_status(conn, cluster_id, "skipped")
            log.info("cluster %d SKIPPED (thin material): src=%d facts=%d claims=%d interp=%d",
                     cluster_id, script.get("n_sources", 0), counts.get("facts", 0),
                     counts.get("claims", 0), counts.get("interpretations", 0))
            return out

        # AI QUALITY AGENT gate: build_script already gave the script up to 4
        # attempts to clear quality_agent.PASS_SCORE (95), feeding its own
        # critique back into a rewrite each time it fell short. `min_agent_
        # score` defaults to PASS_SCORE (95) for a normal call — the
        # fallback-of-the-day retry (see run_pipeline) passes a LOWER floor
        # explicitly, for the one specific case the user asked for: "가장
        # 높은 점수의 영상을 올리는 것으로 변경... (하지만 95점 넘는 영상
        # 제작되면 바로 올리기도 가능)" — 95+ still auto-publishes exactly
        # as before; only when NOTHING in the whole run reached it does the
        # best-scoring candidate get a second pass at this same gate with
        # the floor lowered to _FALLBACK_FLOOR, never below it. An agent
        # that couldn't run at all (no provider, gave up after retries)
        # never blocks — `passed`/this floor is irrelevant when unavailable.
        qa = script.get("quality_agent", {}) or {}
        out.agent_score = int(qa.get("score", 0)) if qa.get("available") else 0
        floor = min_agent_score if min_agent_score is not None else quality_agent.PASS_SCORE
        if qa.get("available") and out.agent_score < floor:
            out.status = "skipped"
            out.reason = f"AI 품질 평가 미달 ({qa.get('score', 0)}점, {qa.get('attempts', 0)}회 시도)"
            with connect(cfg.db_path) as conn:
                set_cluster_status(conn, cluster_id, "skipped")
            issues_str = " | ".join(f"({i.get('role')}) {i.get('problem')}"
                                    for i in (qa.get("issues") or []))
            log.info("cluster %d SKIPPED (quality agent): score=%s attempts=%d issues=%d: %s",
                     cluster_id, qa.get("score"), qa.get("attempts", 0), len(qa.get("issues", [])),
                     issues_str[:400] or "(none recorded)")
            return out

        # Skip a story we've already turned into a short in the last few days —
        # ongoing issues keep re-clustering, but the channel should move on.
        sig = story_signature(script["headline"], script.get("entities"), script.get("frame", ""))
        actor = str(script.get("topic") or "")
        with connect(cfg.db_path) as conn:
            is_dup, why = recent_duplicate(conn, sig, cfg, actor=actor)
            sat = _theme_saturated(conn, sig, actor, cfg) if (enforce_variety and not is_dup) else ""
        if is_dup or sat:
            out.status = "skipped"
            out.reason = f"이미 다룬 이슈 ({why})" if is_dup else sat
            with connect(cfg.db_path) as conn:
                set_cluster_status(conn, cluster_id, "skipped")
            log.info("cluster %d SKIPPED (%s): %s", cluster_id,
                     "duplicate" if is_dup else "topic variety", why or sat)
            return out

        safety = review_script(script, cfg)
        out.safety_warnings = safety.warnings

        # FACT CHECK ENGINE flag: a serious-allegation word riding on a
        # single/low-confidence source. Recorded and logged — NOT an auto-
        # block. User: "출처가 1개라서 안되는 것은 아님... 자동으로 차단하는
        # 시스템은 필요하지 않음, 나의 결정에 따라 올리냐 올리지 않느냐는
        # 내가 판단" — publish/hold is the user's own call, made in advance
        # by choosing to run this pipeline unattended; this flag's job is to
        # make that call informed (it still shows up in safety_warnings /
        # the run log / the video's own metadata), not to make the call
        # itself. safety.passed (hate speech, defamation, etc. — a
        # different, still-enforced check) is untouched.
        fc = script.get("factcheck", {}) or {}
        needs_review = bool(fc.get("review_required"))
        if needs_review:
            out.safety_warnings = [*out.safety_warnings,
                                   f"POLITICAL_CONTENT_REVIEW_NOTE — {fc.get('review_reason', '')}"]
            log.warning("cluster %d POLITICAL_CONTENT_REVIEW_NOTE (not blocking): %s",
                        cluster_id, fc.get("review_reason", ""))

        with connect(cfg.db_path) as conn:
            script_id = save_script(
                conn, cluster_id, script, safety.to_dict(),
                approved=safety.passed,
            )

        if not safety.passed:
            out.status = "skipped"
            out.reason = "; ".join(safety.blocks)
            with connect(cfg.db_path) as conn:
                set_cluster_status(conn, cluster_id, "skipped")
            log.warning("cluster %d SKIPPED: %s", cluster_id, out.reason)
            return out

        stamp = __import__("time").strftime("%Y%m%d_%H%M%S")
        name = f"{stamp}_c{cluster_id}_{_safe_slug(script['headline'])}.mp4"
        video_path = cfg.output_dir / name

        from .quality import check as _qc
        from . import revision as _rev

        def _render_and_check() -> tuple[Any, dict[str, Any], Any]:
            rn = render_video(script, video_path, cfg)
            mt = build_metadata(script, safety.to_dict(), video_path, cfg)
            if getattr(rn, "timeline", None) is not None:
                mt["timeline"] = rn.timeline.to_list()
                mt["duration_s"] = rn.duration_s
            return rn, mt, _qc(script, mt, video_path, cfg, safety.to_dict())

        render, meta, qr = _render_and_check()
        history = [qr.to_dict()]

        # AUTO REVISION — one targeted pass for the fixable middle band.
        if _rev.decide(qr) == "REVISE" and getattr(cfg, "auto_revision", True):
            script, changes = _rev.apply(script, qr)
            if changes:
                log.info("cluster %d auto-revision: %s", cluster_id, "; ".join(changes))
                render, meta, qr2 = _render_and_check()
                history.append(qr2.to_dict())
                if qr2.score >= qr.score:
                    qr = qr2
                meta["revision"] = {"applied": changes, "score_before": history[0]["score"],
                                    "score_after": qr.score}

        meta["quality"] = qr.to_dict()
        meta["quality_history"] = history
        out.quality_score = qr.score
        out.quality_band = qr.band
        if not qr.publishable:
            out.safety_warnings = [*out.safety_warnings,
                                   f"QUALITY {qr.band} ({qr.score}/100) — 자동 게시 보류"]
        meta_path = write_sidecar(meta, video_path)

        with connect(cfg.db_path) as conn:
            video_id = save_video(
                conn, script_id, str(video_path), str(meta_path), render.duration_s
            )
            set_cluster_status(conn, cluster_id, "built")

        out.status = "built"
        out.video_path = str(video_path)
        report.built += 1
        log.info("cluster %d BUILT -> %s (%.1fs)", cluster_id, name, render.duration_s)

        # needs_review no longer holds publish on its own (see the note
        # above) — only quality.py's own publishable check does.
        hold_publish = not qr.publishable
        out.held = hold_publish
        if do_publish and hold_publish:
            out.reason = f"보류: {qr.band} (quality {qr.score}/100) — 빌드 완료, 게시 안 함"
            log.warning("cluster %d built but held from publish (quality=%d/%s)",
                        cluster_id, qr.score, qr.band)
        if do_publish and not hold_publish:
            from .publishers import get_publishers

            any_ok = False
            for pub in get_publishers(cfg):
                res = pub.publish(video_path, meta)
                with connect(cfg.db_path) as conn:
                    log_publish(
                        conn, video_id, res.platform, res.dry_run, res.status,
                        res.remote_id, res.detail,
                    )
                out.publishes.append(res.__dict__)
                if res.status == "ok":
                    report.published += 1
                    any_ok = True
            with connect(cfg.db_path) as conn:
                set_cluster_status(conn, cluster_id, "published")
                if any_ok:
                    rid = next((p["remote_id"] for p in out.publishes if p["status"] == "ok"), "")
                    record_topic(conn, signature_str(sig), script["headline"],
                                 script.get("frame", ""), rid, "youtube", actor)

        return out

    except FFmpegMissing as exc:
        out.status = "error"
        out.reason = str(exc)
        report.errors.append(f"cluster {cluster_id}: {exc}")
        log.error("cluster %d ERROR: %s", cluster_id, exc)
        return out
    except Exception as exc:
        out.status = "error"
        out.reason = f"{type(exc).__name__}: {exc}"
        report.errors.append(f"cluster {cluster_id}: {out.reason}")
        log.error("cluster %d ERROR\n%s", cluster_id, traceback.format_exc())
        return out


def run_pipeline(
    cfg: Settings | None = None,
    *,
    do_collect: bool = True,
    do_publish: bool | None = None,
    max_items: int | None = None,
) -> RunReport:
    cfg = cfg or settings
    init_db(cfg.db_path)
    report = RunReport()
    do_publish = cfg.enable_publish if do_publish is None else do_publish
    limit = max_items or cfg.max_items_per_run

    with connect(cfg.db_path) as conn:
        report.job_id = start_job(conn)

    try:
        if do_collect:
            cres = collect(cfg)
            report.collected = cres.inserted
            report.errors.extend(cres.errors)

        evaluated, politics = classify_pending(cfg)
        report.classified = evaluated
        report.politics = politics

        cluster_ids = build_clusters(cfg)
        report.clusters = len(cluster_ids)

        # Re-rank so the story that's actually TRENDING on Google right now goes
        # first (best-effort; no-op if the trends feed is unreachable).
        try:
            from .trending import rerank_by_trend
            cluster_ids = rerank_by_trend(cluster_ids, cfg)
        except Exception as exc:  # never let ranking break a run
            log.warning("trend rerank skipped: %s", exc)

        # "built" alone isn't the goal — a story quality.py holds from
        # publish (single-source-allegation stories are no longer held on
        # their own, but a genuinely low-quality render still is) still
        # counts as "built" but leaves the day's actual quota unfilled. User:
        # "하루 1개 영상 원칙은 유지하고 발행 가능한 1개를 찾을때까지 진행"
        # — keep walking candidates until a truly publishable one turns up,
        # not just the first one that happens to render.
        def _ready() -> int:
            return sum(1 for s in report.stories if s.status == "built" and not s.held)

        # A safety valve, not a quality compromise: without SOME cap, a
        # genuinely tough news day (many thin/held/quality-agent-failing
        # candidates in a row) could walk every single cluster before giving
        # up, each one costing a real render or several quality_agent LLM
        # rounds — a real risk of blowing the CI job's time budget. Bounding
        # total attempts just means "stop looking today", same as running
        # out of candidates naturally would — it never lowers the bar on
        # what counts as publishable.
        _MAX_ATTEMPTS = 12

        def _attempts_left() -> bool:
            return len(report.stories) < _MAX_ATTEMPTS

        # Walk clusters hottest-first, skipping stories we've already covered /
        # that get blocked, until `limit` publishable shorts are found.
        for cid in cluster_ids:
            if _ready() >= limit or not _attempts_left():
                break
            report.stories.append(_process_story(cid, cfg, do_publish, report))

        # Nothing publishable yet (nothing fresh, everything a duplicate, or
        # every candidate held on quality)? Fall back to a generally-
        # newsworthy APOLITICAL story so the channel still posts.
        if _ready() == 0 and _attempts_left():
            gen_ids = build_clusters(cfg, mode="general")
            if gen_ids:
                log.info("no publishable politics story — trying %d general-interest clusters", len(gen_ids))
                try:
                    gen_ids = rerank_by_trend(gen_ids, cfg)
                except Exception:
                    pass
                for cid in gen_ids:
                    if _ready() >= limit or not _attempts_left():
                        break
                    report.stories.append(_process_story(cid, cfg, do_publish, report))

        # Still nothing publishable — the variety filter may have held
        # everything back (a week where every top story is one saga). Re-walk
        # politics with it off so the channel always posts something.
        if _ready() == 0 and _attempts_left():
            log.info("variety filter held back every story — re-walking politics without it")
            for cid in cluster_ids:
                if _ready() >= limit or not _attempts_left():
                    break
                report.stories.append(
                    _process_story(cid, cfg, do_publish, report, enforce_variety=False))

        # FALLBACK-OF-THE-DAY: nothing reached the 95 bar across every
        # candidate tried above. User: "평가를 진행하는 것은 두고 평가진행
        # 후 실패하더라도, 가장 높은 점수의 영상을 올리는 것으로 변경하자
        # (하지만 95점 넘는 영상 제작되면 바로 올리기도 가능)" — a video
        # scoring over 95 still auto-publishes exactly as before (unchanged
        # by any of this); only when the WHOLE run found nothing that good
        # does the single best-scoring candidate get one more pass at the
        # SAME gate with the floor lowered to _FALLBACK_FLOOR (asked
        # separately, not "any score": batch 28's diagnostic run showed a
        # real, observed defect in every 40-84-scored candidate that day —
        # 85 is the line below which nothing ships, full stop).
        # Re-runs build_script() for that cluster rather than reusing the
        # earlier script object (simpler; the alternative is threading the
        # exact script through every skip path) — accepted trade-off: the
        # quality agent has real run-to-run variance, so this SECOND
        # attempt's score can differ from the one that made it "best" the
        # first time, and `_process_story`'s own gate re-checks it fairly
        # either way.
        _FALLBACK_FLOOR = 85
        if _ready() == 0:
            candidates = [s for s in report.stories
                         if s.status == "skipped" and s.reason.startswith("AI 품질 평가 미달")]
            best = max(candidates, key=lambda s: s.agent_score, default=None)
            if best is not None and best.agent_score >= _FALLBACK_FLOOR:
                log.info("no candidate reached the AI bar today (best=%d/95) — "
                         "retrying cluster %d as the day's fallback-best publish (floor=%d)",
                         best.agent_score, best.cluster_id, _FALLBACK_FLOOR)
                report.stories.append(_process_story(
                    best.cluster_id, cfg, do_publish, report,
                    enforce_variety=False, min_agent_score=_FALLBACK_FLOOR))

        report.skipped = sum(1 for s in report.stories if s.status == "skipped")

        status = "error" if report.errors and report.built == 0 else "ok"
        with connect(cfg.db_path) as conn:
            finish_job(
                conn, report.job_id, status,
                collected=report.collected, clustered=report.clusters,
                built=report.built, skipped=report.skipped, published=report.published,
                log=json.dumps(report.to_dict(), ensure_ascii=False)[:60000],
            )
        log.info(
            "RUN DONE job=%d collected=%d clusters=%d built=%d skipped=%d published=%d errors=%d",
            report.job_id, report.collected, report.clusters, report.built,
            report.skipped, report.published, len(report.errors),
        )
        return report

    except Exception as exc:
        with connect(cfg.db_path) as conn:
            finish_job(conn, report.job_id, "error", log=traceback.format_exc()[:60000])
        log.exception("pipeline crashed")
        report.errors.append(f"fatal: {exc}")
        return report
