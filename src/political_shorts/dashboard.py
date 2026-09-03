"""Local, read-mostly control panel (Flask). Bind to 127.0.0.1 only.

Routes
  GET  /            overview: last runs, built videos, quick stats
  GET  /health      -> {"status": "ok"} (used by install.ps1 smoke test)
  GET  /articles    recent collected articles + politics flag
  GET  /cluster/<id>  script JSON + safety report for a story
  GET  /video/<id>  stream the mp4
  GET  /meta/<id>   pretty metadata sidecar
  POST /run         kick off a pipeline run in a background thread
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, render_template_string, request, send_file

from .config import settings
from .db import connect, init_db, recent_articles, recent_jobs, recent_videos
from .logging_setup import get_logger
from .pipeline import run_pipeline

log = get_logger("dashboard")

_run_lock = threading.Lock()
_last_run: dict = {"running": False, "started": 0, "report": None}


def create_app() -> Flask:
    app = Flask(__name__)
    init_db(settings.db_path)

    @app.get("/health")
    def health() -> Response:
        return jsonify(
            status="ok",
            version="1.0.0",
            publish_enabled=settings.enable_publish,
            tts_enabled=settings.enable_tts,
            feeds=len(settings.feeds),
        )

    @app.get("/")
    def index() -> str:
        with connect(settings.db_path) as conn:
            jobs = recent_jobs(conn, 12)
            videos = recent_videos(conn, 24)
            arts = conn.execute(
                "SELECT COUNT(*) n, SUM(is_politics) p FROM articles"
            ).fetchone()
        return render_template_string(
            TEMPLATE,
            jobs=jobs,
            videos=videos,
            n_articles=arts["n"] or 0,
            n_politics=arts["p"] or 0,
            last_run=_last_run,
            settings=settings,
        )

    @app.get("/articles")
    def articles() -> str:
        since = int(time.time()) - settings.collect_window_hours * 3600 * 3
        with connect(settings.db_path) as conn:
            rows = recent_articles(conn, since, politics_only=False)[:200]
        return render_template_string(ARTICLES_TEMPLATE, rows=rows)

    @app.get("/cluster/<int:cid>")
    def cluster(cid: int) -> Response:
        with connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM scripts WHERE cluster_id = ? ORDER BY id DESC LIMIT 1", (cid,)
            ).fetchone()
        if not row:
            abort(404)
        return jsonify(
            cluster_id=cid,
            approved=bool(row["approved"]),
            script=json.loads(row["payload_json"]),
            safety=json.loads(row["safety_json"]),
        )

    @app.get("/video/<int:vid>")
    def video(vid: int) -> Response:
        with connect(settings.db_path) as conn:
            row = conn.execute("SELECT video_path FROM videos WHERE id = ?", (vid,)).fetchone()
        if not row:
            abort(404)
        p = Path(row["video_path"])
        if not p.exists():
            abort(410)
        return send_file(p, mimetype="video/mp4", conditional=True)

    @app.get("/meta/<int:vid>")
    def meta(vid: int) -> Response:
        with connect(settings.db_path) as conn:
            row = conn.execute("SELECT meta_path FROM videos WHERE id = ?", (vid,)).fetchone()
        if not row:
            abort(404)
        p = Path(row["meta_path"])
        if not p.exists():
            abort(410)
        return Response(p.read_text(encoding="utf-8"), mimetype="application/json")

    @app.post("/run")
    def run() -> Response:
        do_collect = request.form.get("collect", "1") != "0"

        def _worker() -> None:
            with _run_lock:
                _last_run.update(running=True, started=int(time.time()), report=None)
                try:
                    rep = run_pipeline(settings, do_collect=do_collect)
                    _last_run["report"] = rep.to_dict()
                except Exception as exc:  # pragma: no cover
                    _last_run["report"] = {"errors": [str(exc)]}
                finally:
                    _last_run["running"] = False

        if _run_lock.locked():
            return redirect("/?busy=1")
        threading.Thread(target=_worker, daemon=True).start()
        time.sleep(0.4)
        return redirect("/")

    return app


def main() -> None:
    app = create_app()
    log.info(
        "dashboard on http://%s:%d  (publish=%s)",
        settings.dashboard_host, settings.dashboard_port, settings.enable_publish,
    )
    app.run(host=settings.dashboard_host, port=settings.dashboard_port, debug=False)


TEMPLATE = """
<!doctype html><meta charset="utf-8"><title>political-shorts</title>
<style>
 body{font:15px/1.5 system-ui,Segoe UI,Malgun Gothic,sans-serif;margin:0;background:#0b1120;color:#e5e7eb}
 header{padding:18px 24px;background:#111827;border-bottom:1px solid #1f2937}
 h1{margin:0;font-size:18px} main{padding:24px;max-width:1100px;margin:0 auto}
 .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
 .card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:14px}
 .card b{font-size:24px;display:block}
 table{width:100%;border-collapse:collapse;margin-bottom:28px;background:#111827;border-radius:10px;overflow:hidden}
 th,td{padding:9px 12px;text-align:left;border-bottom:1px solid #1f2937;font-size:13px}
 th{background:#0f172a;color:#94a3b8}
 a{color:#60a5fa} .ok{color:#4ade80}.err{color:#f87171}.warn{color:#fbbf24}
 button{background:#2563eb;color:#fff;border:0;padding:9px 16px;border-radius:8px;cursor:pointer}
 .pill{display:inline-block;padding:1px 8px;border-radius:999px;background:#1f2937;font-size:12px}
 video{width:220px;border-radius:8px;background:#000}
</style>
<header><h1>political-shorts &nbsp;<span class=pill>publish: {{ 'ON' if settings.enable_publish else 'OFF (dry-run)' }}</span>
 <span class=pill>tts: {{ 'on' if settings.enable_tts else 'off' }}</span></h1></header>
<main>
 <div class=grid>
  <div class=card><b>{{ n_articles }}</b>수집 기사</div>
  <div class=card><b>{{ n_politics }}</b>정치 분류</div>
  <div class=card><b>{{ videos|length }}</b>제작 영상</div>
  <div class=card><b>{{ settings.feeds|length }}</b>RSS 소스</div>
 </div>

 <form method=post action=/run style="margin-bottom:24px">
  <button type=submit>지금 파이프라인 실행</button>
  <label style="margin-left:12px"><input type=checkbox name=collect value=1 checked> 뉴스 수집 포함</label>
  {% if last_run.running %}<span class=warn>&nbsp;실행 중…</span>{% endif %}
  <a href=/articles style="margin-left:16px">기사 보기</a>
 </form>

 <h3>최근 실행</h3>
 <table><tr><th>#</th><th>시작</th><th>상태</th><th>수집</th><th>클러스터</th><th>제작</th><th>스킵</th><th>게시</th></tr>
 {% for j in jobs %}
  <tr><td>{{ j.id }}</td>
   <td>{{ j.started_ts | int }}</td>
   <td class="{{ 'ok' if j.status=='ok' else 'err' if j.status=='error' else '' }}">{{ j.status }}</td>
   <td>{{ j.collected }}</td><td>{{ j.clustered }}</td><td>{{ j.built }}</td>
   <td>{{ j.skipped }}</td><td>{{ j.published }}</td></tr>
 {% endfor %}
 </table>

 <h3>제작된 영상</h3>
 <table><tr><th>#</th><th>헤드라인</th><th>길이</th><th>미리보기</th><th>메타</th><th>스크립트/안전</th></tr>
 {% for v in videos %}
  <tr><td>{{ v.id }}</td><td>{{ v.lead_title }}</td><td>{{ '%.1f'|format(v.duration_s) }}s</td>
   <td><video src="/video/{{ v.id }}" controls preload=none></video></td>
   <td><a href="/meta/{{ v.id }}">meta.json</a></td>
   <td><a href="/cluster/{{ v.cluster_id }}">보기</a></td></tr>
 {% endfor %}
 </table>
</main>
"""

ARTICLES_TEMPLATE = """
<!doctype html><meta charset="utf-8"><title>articles</title>
<style>body{font:13px/1.5 system-ui,Malgun Gothic,sans-serif;background:#0b1120;color:#e5e7eb;padding:20px}
table{width:100%;border-collapse:collapse}td,th{padding:6px 10px;border-bottom:1px solid #1f2937;text-align:left}
a{color:#60a5fa}.p{color:#4ade80}</style>
<a href=/>&larr; 대시보드</a><h3>최근 기사 ({{ rows|length }})</h3>
<table><tr><th>매체</th><th>성향</th><th>정치</th><th>점수</th><th>제목</th></tr>
{% for r in rows %}
 <tr><td>{{ r.source_name }}</td><td>{{ r.source_lean }}</td>
  <td class=p>{{ '●' if r.is_politics else '' }}</td><td>{{ '%.1f'|format(r.politics_score) }}</td>
  <td><a href="{{ r.url }}" target=_blank rel=noopener>{{ r.title }}</a></td></tr>
{% endfor %}</table>
"""


if __name__ == "__main__":  # pragma: no cover
    main()
