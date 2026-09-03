"""Offline pipeline test: seed the DB with fake articles, run everything
except collect + render, and assert a script + safety report come out."""
import time
from dataclasses import replace

from political_shorts.classify import classify_pending
from political_shorts.config import load_settings
from political_shorts.db import init_db, connect, upsert_article, now
from political_shorts.dedupe import build_clusters
from political_shorts.safety import review_script
from political_shorts.script_gen import build_script
from political_shorts.textutil import url_hash

# Same-event coverage really does share a lead paragraph across outlets; the
# headlines diverge but the bodies overlap heavily. That is what the clusterer
# is tuned for.
FAKE = [
    ("연합뉴스", "wire", 1.0, "국회 본회의, 예산안 처리 두고 여야 충돌",
     "국회는 3일 오후 본회의를 열어 내년도 예산안 처리를 두고 여야가 충돌했다. "
     "국민의힘은 합의 처리를 주장했고 더불어민주당은 독소조항을 지적했다. "
     "찬반 표결 끝에 예산안은 가결됐다."),
    ("동아일보", "right", 0.7, "[속보] 예산안 국회 본회의 통과…여야 정면충돌",
     "국회는 3일 오후 본회의에서 내년도 예산안을 처리했다. 여야는 예산안 처리를 두고 "
     "정면충돌했다. 국민의힘은 합의 처리라고 밝혔다. 예산안은 표결 끝에 가결됐다."),
    ("한겨레", "left", 0.7, "예산안 본회의 의결…민주당 '독소조항' 반발",
     "국회는 3일 오후 본회의를 열어 내년도 예산안을 의결했다. 더불어민주당은 일부 "
     "조항이 독소조항이라고 반발했다. 예산안 처리를 두고 여야가 충돌했다."),
    ("연합뉴스", "wire", 1.0, "프로야구 개막전 전 구장 매진",
     "한국야구위원회는 3일 프로야구 개막전 입장권이 전 구장 매진됐다고 밝혔다. "
     "손흥민 시구 소식도 전해졌다."),
]


def test_offline_pipeline(tmp_path):
    cfg = replace(load_settings(), db_path=tmp_path / "t.sqlite3",
                  output_dir=tmp_path, data_dir=tmp_path,
                  image_enabled=False)  # no network in the offline test
    init_db(cfg.db_path)

    with connect(cfg.db_path) as conn:
        for name, lean, w, title, summary in FAKE:
            url = f"https://example.com/{url_hash(title)[:10]}"
            upsert_article(conn, {
                "url_hash": url_hash(url), "url": url, "source_name": name,
                "source_lean": lean, "source_weight": w, "title": title,
                "summary": summary, "published_ts": now(), "collected_ts": now(),
                "raw": {},
            })

    evaluated, politics = classify_pending(cfg)
    assert evaluated == 4
    assert politics == 3  # the baseball item is filtered out

    ids = build_clusters(cfg)
    assert len(ids) >= 1

    script = build_script(ids[0], cfg)
    roles = [s["role"] for s in script["segments"]]
    assert roles[0] == "hook"
    assert "what" in roles
    assert "factcheck" in roles
    assert script["n_sources"] >= 2

    rep = review_script(script, cfg)
    # multi-source, multi-lean, attributed reaction -> should pass
    assert rep.passed is True, rep.blocks
