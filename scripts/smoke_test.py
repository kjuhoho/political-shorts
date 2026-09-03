"""End-to-end smoke test used by install.ps1.

Steps (each is best-effort and reported):
  1. import every module
  2. collect from real RSS (needs internet) — counts inserted rows
  3. classify + cluster
  4. build a script + run the safety gate on the top cluster
  5. render a short MP4 (needs ffmpeg) OR synthesize a 2-card demo if no data
  6. hit the dashboard /health endpoint in-process

Exit code 0 = every *critical* step passed (imports, script, safety, health).
Video/collect are reported but not required (offline machines still pass).
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import _bootstrap  # noqa: F401

from political_shorts.console import enable_utf8

enable_utf8()


def _ok(msg: str) -> None:
    print(f"  [ OK ] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def main() -> int:
    critical_failures = 0

    print("1) imports")
    try:
        from political_shorts import (
            analyze, classify, collect, config, db, dedupe, metadata,
            pipeline, safety, script_gen, tts, video,
        )
        from political_shorts.publishers import get_publishers
        _ok("all modules import")
    except Exception:
        _fail("import error\n" + traceback.format_exc())
        return 1

    import tempfile
    from dataclasses import replace

    from political_shorts.config import load_settings
    from political_shorts.db import init_db

    # Isolated DB + output dir so the smoke test is a true clean run every time.
    tmp = Path(tempfile.mkdtemp(prefix="pshorts_smoke_"))
    settings = replace(
        load_settings(), db_path=tmp / "smoke.sqlite3", data_dir=tmp, output_dir=tmp
    )
    init_db(settings.db_path)
    _ok(f"isolated db at {settings.db_path}")

    print("2) collect (needs internet)")
    inserted = 0
    try:
        r = collect.collect(settings)
        inserted = r.inserted
        if r.fetched_feeds:
            _ok(f"{r.fetched_feeds}/{r.fetched_feeds + r.failed_feeds} feeds, "
                f"{r.total_entries} entries, {r.inserted} new")
        for e in r.errors:
            _warn(f"feed: {e}")
        if not r.fetched_feeds:
            _warn("no feeds fetched (offline?)")
    except Exception:
        _warn("collect failed\n" + traceback.format_exc())

    print("3) classify + cluster")
    try:
        ev, pol = classify.classify_pending(settings)
        ids = dedupe.build_clusters(settings)
        _ok(f"classified {ev} (politics {pol}), clusters {len(ids)}")
    except Exception:
        _fail("classify/cluster error\n" + traceback.format_exc())
        critical_failures += 1
        ids = []

    print("4) script + safety")
    demo_script = None
    try:
        if ids:
            demo_script = script_gen.build_script(ids[0], settings)
        else:
            demo_script = {
                "cluster_id": 0,
                "headline": "스모크 테스트용 예시 헤드라인",
                "n_sources": 2,
                "leans": ["wire", "left"],
                "segments": [
                    {"role": "hook", "caption": "스모크 테스트", "narration": "테스트 나레이션입니다."},
                    {"role": "fact", "caption": "국회 본회의 3일 개의", "narration": "국회는 3일 오전 본회의를 열었다.",
                     "source": "예시통신", "multi_source": True, "cues": ["date"]},
                    {"role": "outro", "caption": "출처 더보기란", "narration": "출처는 설명란을 확인하세요."},
                ],
                "sources": [
                    {"name": "예시통신", "url": "https://example.com/a", "lean": "wire"},
                    {"name": "예시신문", "url": "https://example.com/b", "lean": "left"},
                ],
                "disclaimer": "테스트용 고지문.",
            }
        rep = safety.review_script(demo_script, settings)
        _ok(f"script segs={len(demo_script['segments'])} safety.passed={rep.passed} "
            f"blocks={len(rep.blocks)} warnings={len(rep.warnings)}")
        if not rep.passed:
            _warn("safety blocked demo: " + "; ".join(rep.blocks))
    except Exception:
        _fail("script/safety error\n" + traceback.format_exc())
        critical_failures += 1

    print("5) render mp4 (needs ffmpeg)")
    try:
        import shutil

        if shutil.which(settings.ffmpeg_path) and demo_script:
            out = settings.output_dir / "smoke_test.mp4"
            res = video.render_video(demo_script, out, settings)
            if out.exists() and out.stat().st_size > 1000:
                _ok(f"{out.name} {out.stat().st_size // 1024} KB, {res.duration_s}s")
            else:
                _warn("render produced no/empty file")
        else:
            _warn("ffmpeg not found — skipping render")
    except Exception:
        _warn("render failed\n" + traceback.format_exc())

    print("6) dashboard /health")
    try:
        from political_shorts.dashboard import create_app

        app = create_app()
        client = app.test_client()
        resp = client.get("/health")
        data = resp.get_json()
        if resp.status_code == 200 and data.get("status") == "ok":
            _ok(f"health ok: feeds={data.get('feeds')} publish={data.get('publish_enabled')}")
        else:
            _fail(f"health returned {resp.status_code} {data}")
            critical_failures += 1
    except Exception:
        _fail("dashboard error\n" + traceback.format_exc())
        critical_failures += 1

    print()
    if critical_failures:
        print(f"SMOKE TEST: {critical_failures} critical failure(s)")
        return 1
    print("SMOKE TEST: PASS (critical steps ok)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
