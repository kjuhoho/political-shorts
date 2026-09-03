"""Command-line entry point.

    python -m political_shorts collect
    python -m political_shorts classify
    python -m political_shorts cluster
    python -m political_shorts run           [--no-collect] [--publish] [--max N]
    python -m political_shorts build <cluster_id>
    python -m political_shorts dashboard
    python -m political_shorts schedule add  [--at HH:MM] [--slot NAME] [--publish] [--max N] [--no-collect]
    python -m political_shorts schedule remove [--slot NAME | --all]
    python -m political_shorts schedule status
    python -m political_shorts auth youtube
    python -m political_shorts serve-media   [--host H] [--port P]
    python -m political_shorts doctor
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import settings
from .logging_setup import get_logger, setup_logging

log = get_logger("cli")


def _print_report(rep) -> None:
    d = rep.to_dict() if hasattr(rep, "to_dict") else rep
    print(json.dumps(d, ensure_ascii=False, indent=2))


def cmd_collect(_args: argparse.Namespace) -> int:
    from .collect import collect

    r = collect(settings)
    print(f"inserted={r.inserted} feeds_ok={r.fetched_feeds} feeds_failed={r.failed_feeds}")
    for e in r.errors:
        print("  !", e)
    return 0


def cmd_classify(_args: argparse.Namespace) -> int:
    from .classify import classify_pending

    evaluated, politics = classify_pending(settings)
    print(f"evaluated={evaluated} politics={politics}")
    return 0


def cmd_cluster(_args: argparse.Namespace) -> int:
    from .dedupe import build_clusters

    ids = build_clusters(settings)
    print(f"new_clusters={ids}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import run_pipeline

    do_publish = None
    if args.publish:
        do_publish = True
    elif args.no_publish:
        do_publish = False
    rep = run_pipeline(
        settings,
        do_collect=not args.no_collect,
        do_publish=do_publish,
        max_items=args.max,
    )
    _print_report(rep)
    return 0 if not rep.errors or rep.built else 1


def cmd_build(args: argparse.Namespace) -> int:
    from .metadata import build_metadata, write_sidecar
    from .safety import review_script
    from .script_gen import build_script
    from .video import render_video

    script = build_script(args.cluster_id, settings)
    safety = review_script(script, settings)
    print(json.dumps({"script": script, "safety": safety.to_dict()}, ensure_ascii=False, indent=2))
    if not safety.passed:
        print("BLOCKED — not rendering.", file=sys.stderr)
        return 2
    out = settings.output_dir / f"manual_c{args.cluster_id}.mp4"
    res = render_video(script, out, settings)
    meta = build_metadata(script, safety.to_dict(), out, settings)
    write_sidecar(meta, out)
    print(f"rendered {out} ({res.duration_s}s)")
    return 0


def cmd_dashboard(_args: argparse.Namespace) -> int:
    from .dashboard import main as dash_main

    dash_main()
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    from . import scheduler

    if args.sub == "add":
        return scheduler.register(
            args.at,
            args.slot,
            collect=not args.no_collect,
            publish=args.publish,
            max_items=args.max,
        )
    if args.sub == "remove":
        if args.all:
            return scheduler.unregister_all()
        return scheduler.unregister(args.slot)
    print(scheduler.status())
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    import shutil

    print("political-shorts doctor")
    print(f"  python           : {sys.version.split()[0]}")
    print(f"  root             : {settings.root}")
    print(f"  db               : {settings.db_path} (exists={settings.db_path.exists()})")
    print(f"  feeds configured : {len(settings.feeds)}")
    ff = shutil.which(settings.ffmpeg_path) or settings.ffmpeg_path
    ff_ok = bool(shutil.which(settings.ffmpeg_path) or Path(settings.ffmpeg_path).exists())
    print(f"  ffmpeg           : {ff}  ({'ok' if ff_ok else 'MISSING'})")
    for mod in ("feedparser", "requests", "yaml", "flask", "PIL", "rapidfuzz", "pyttsx3", "edge_tts"):
        try:
            __import__(mod)
            print(f"  import {mod:<12}: ok")
        except Exception as exc:
            print(f"  import {mod:<12}: FAIL ({exc})")
    print(f"  tts enabled      : {settings.enable_tts}")
    print(f"  tts provider     : {settings.tts_provider} (voice={settings.tts_voice or 'default'})")
    _bgm = settings.bgm_path if settings.bgm_enabled else "(disabled)"
    _bgm_ok = (not settings.bgm_enabled) or Path(settings.bgm_path).exists()
    print(f"  bgm              : {_bgm}  ({'ok' if _bgm_ok else 'FILE MISSING'})")
    print(f"  publish enabled  : {settings.enable_publish}")
    print(f"  llm provider     : {settings.llm_provider or '(none)'} available={settings.llm_available}")
    from .font_check import font_ok

    print(f"  korean font      : {'ok' if font_ok(settings.font_path) else 'MISSING ' + settings.font_path}")
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    if args.platform == "youtube":
        try:
            from .publishers.youtube import YouTubePublisher
        except Exception as exc:  # pragma: no cover
            print(f"import failed: {exc}\nRun:  pip install -r requirements-publish.txt", file=sys.stderr)
            return 1
        pub = YouTubePublisher(settings)
        ok, reason = pub._is_configured()
        if not ok:
            print(f"not ready: {reason}", file=sys.stderr)
            print("See PUBLISHING.md -> YouTube section.", file=sys.stderr)
            return 2
        print("Opening a browser for Google sign-in / consent ...")
        creds = pub._credentials()
        print(f"OK. Token cached at {settings.youtube_token_file}")
        print(f"   valid={creds.valid} scopes={list(getattr(creds, 'scopes', []) or [])}")
        return 0
    print("Instagram / TikTok use long-lived tokens, not a local OAuth flow.")
    print("Follow PUBLISHING.md to obtain the token, then put it in .env:")
    print("  INSTAGRAM_ACCESS_TOKEN=...   /   TIKTOK_ACCESS_TOKEN=...")
    return 0


def cmd_serve_media(args: argparse.Namespace) -> int:
    from .media_server import serve

    serve(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="political_shorts", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("collect", help="fetch RSS feeds").set_defaults(func=cmd_collect)
    sub.add_parser("classify", help="tag articles as domestic politics").set_defaults(func=cmd_classify)
    sub.add_parser("cluster", help="group near-duplicate stories").set_defaults(func=cmd_cluster)

    r = sub.add_parser("run", help="full pipeline")
    r.add_argument("--no-collect", action="store_true", help="skip RSS fetch")
    r.add_argument("--publish", action="store_true", help="force publish (still needs ENABLE_PUBLISH)")
    r.add_argument("--no-publish", action="store_true", help="build only, never publish (for testing)")
    r.add_argument("--max", type=int, default=None, help="max shorts this run")
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("build", help="build one cluster by id")
    b.add_argument("cluster_id", type=int)
    b.set_defaults(func=cmd_build)

    sub.add_parser("dashboard", help="run local web dashboard").set_defaults(func=cmd_dashboard)

    s = sub.add_parser("schedule", help="Windows Task Scheduler helper")
    s.add_argument("sub", choices=["add", "remove", "status"])
    s.add_argument("--at", default="07:30", help="HH:MM daily (default 07:30)")
    s.add_argument("--slot", default="", help="name suffix for this run, e.g. morning / evening")
    s.add_argument("--no-collect", action="store_true")
    s.add_argument("--publish", action="store_true", help="pass --publish to the run")
    s.add_argument("--max", type=int, default=None, help="max shorts per run")
    s.add_argument("--all", action="store_true", help="remove: delete every PoliticalShorts* task")
    s.set_defaults(func=cmd_schedule)

    a = sub.add_parser("auth", help="one-time platform sign-in (YouTube OAuth)")
    a.add_argument("platform", choices=["youtube", "instagram", "tiktok"])
    a.set_defaults(func=cmd_auth)

    sm = sub.add_parser("serve-media", help="serve output/ over HTTP for IG/TikTok pull")
    sm.add_argument("--host", default="0.0.0.0")
    sm.add_argument("--port", type=int, default=8770)
    sm.set_defaults(func=cmd_serve_media)

    sub.add_parser("doctor", help="environment self-check").set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
