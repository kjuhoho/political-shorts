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
    enforce_variety: bool = True,
) -> StoryOutcome:
    out = StoryOutcome(cluster_id=cluster_id)
    try:
        script = build_script(cluster_id, cfg)
        out.headline = script["headline"]

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

        # FACT CHECK ENGINE gate: a serious-allegation word on a single, low-
        # confidence source -> build for review, never auto-publish.
        fc = script.get("factcheck", {}) or {}
        needs_review = bool(fc.get("review_required"))
        if needs_review:
            out.safety_warnings = [*out.safety_warnings,
                                   f"POLITICAL_CONTENT_REVIEW_REQUIRED — {fc.get('review_reason', '')}"]
            log.warning("cluster %d POLITICAL_CONTENT_REVIEW_REQUIRED: %s",
                        cluster_id, fc.get("review_reason", ""))

        with connect(cfg.db_path) as conn:
            script_id = save_script(
                conn, cluster_id, script, safety.to_dict(),
                approved=safety.passed and not needs_review,
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

        hold_publish = needs_review or not qr.publishable
        if do_publish and hold_publish:
            out.reason = (f"보류: {'REVIEW_REQUIRED' if needs_review else qr.band} "
                          f"(quality {qr.score}/100) — 빌드 완료, 게시 안 함")
            log.warning("cluster %d built but held from publish (%s, quality=%d/%s)",
                        cluster_id, "review" if needs_review else "quality", qr.score, qr.band)
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

        # Walk clusters hottest-first, skipping stories we've already covered /
        # that get blocked, until `limit` fresh shorts are built.
        for cid in cluster_ids:
            if report.built >= limit:
                break
            report.stories.append(_process_story(cid, cfg, do_publish, report))

        # Politics dry (nothing new, or everything a duplicate)? Fall back to a
        # generally-newsworthy APOLITICAL story so the channel still posts.
        if report.built == 0:
            gen_ids = build_clusters(cfg, mode="general")
            if gen_ids:
                log.info("no fresh politics story — trying %d general-interest clusters", len(gen_ids))
                try:
                    gen_ids = rerank_by_trend(gen_ids, cfg)
                except Exception:
                    pass
                for cid in gen_ids:
                    if report.built >= limit:
                        break
                    report.stories.append(_process_story(cid, cfg, do_publish, report))

        # Still nothing — the variety filter may have held everything back
        # (a week where every top story is one saga). Re-walk politics with it
        # off so the channel always posts something.
        if report.built == 0:
            log.info("variety filter held back every story — re-walking politics without it")
            for cid in cluster_ids:
                if report.built >= limit:
                    break
                report.stories.append(
                    _process_story(cid, cfg, do_publish, report, enforce_variety=False))

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
