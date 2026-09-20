"""Offline pipeline test: seed the DB with fake articles, run everything
except collect + render, and assert a script + safety report come out."""
import time
from dataclasses import replace

import json

from political_shorts import llm, pipeline, quality_agent, subtitle
from political_shorts.classify import classify_pending
from political_shorts.config import load_settings
from political_shorts.db import init_db, connect, upsert_article, now
from political_shorts.dedupe import build_clusters
from political_shorts.pipeline import RunReport, _process_story
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
    # background's trim PRIORITY (now sacrificed last, not first — see
    # test_script_gen.py's test_fit_duration_* for that exact behavior) is a
    # separate concern from whether THIS fixture's specific text happens to
    # survive at this exact budget; a card the completeness check can't
    # honestly salvage is correctly dropped rather than shipped broken (see
    # test_fit_duration_never_ships_a_fabricated_complete_looking_summary).
    if "summary" in roles:
        summary = next(s for s in script["segments"] if s["role"] == "summary")
        assert subtitle._complete(summary["narration"])
    assert "factcheck" in roles
    assert script["n_sources"] >= 2

    # the outro now leads with the story's actual stakes, not production-
    # process commentary ("여러 매체 보도를 종합했습니다") — that trust
    # signal already lives on the factcheck "확인" row.
    outro = next(s for s in script["segments"] if s["role"] == "outro")
    assert not outro["narration"].startswith("여러 매체 보도를 종합했습니다")
    assert subtitle._complete(outro["narration"])

    rep = review_script(script, cfg)
    # multi-source, multi-lean, attributed reaction -> should pass
    assert rep.passed is True, rep.blocks


def test_outro_significance_line_never_repeated_elsewhere_when_llm_is_off(tmp_path):
    # user: "서론 본론 결론 형태의 글 구조가 힘든가" — traced to a real CI
    # video where the "what" card and the outro spoke the EXACT SAME
    # sentence (explain.significance(frame)), because the raw template path
    # (LLM off/unavailable that run) called it in both places. With no
    # second distinct fact, "what" used to exist solely to carry that same
    # line — it must now be dropped instead, leaving exactly one occurrence
    # of the frame's significance text, in the outro alone.
    cfg = replace(load_settings(), db_path=tmp_path / "dup1.sqlite3",
                  output_dir=tmp_path, data_dir=tmp_path, image_enabled=False)
    assert not (cfg.llm_provider or "").strip()          # pure template path
    init_db(cfg.db_path)
    with connect(cfg.db_path) as conn:
        for name, lean, w, title, summary in FAKE:
            if "야구" in title:
                continue
            url = f"https://example.com/{url_hash(title)[:10]}"
            upsert_article(conn, {
                "url_hash": url_hash(url), "url": url, "source_name": name,
                "source_lean": lean, "source_weight": w, "title": title,
                "summary": summary, "published_ts": now(), "collected_ts": now(),
                "raw": {},
            })
    classify_pending(cfg)
    ids = build_clusters(cfg)
    script = build_script(ids[0], cfg)

    from political_shorts.explain import SIGNIFICANCE
    sig_text = SIGNIFICANCE[script["frame"]]
    narrations = [s.get("narration", "") for s in script["segments"]]
    hits = [n for n in narrations if sig_text in n]
    assert len(hits) == 1, f"significance line appeared {len(hits)} times: {hits}"
    outro = next(s for s in script["segments"] if s["role"] == "outro")
    assert sig_text in outro["narration"]
    # a "what" card may exist (length padding adds one once the factcheck card
    # stopped carrying stock filler) but it must never restate the
    # significance line — that lives in the outro alone.
    assert all(sig_text not in s.get("narration", "")
               for s in script["segments"] if s["role"] == "what")


def test_what_card_drops_the_significance_line_when_a_second_fact_exists(tmp_path):
    # the mirror case: WITH a genuine second fact, "what" must carry ONLY
    # that fact — not the fact plus the same significance line the outro
    # is about to say.
    cfg = replace(load_settings(), db_path=tmp_path / "dup2.sqlite3",
                  output_dir=tmp_path, data_dir=tmp_path, image_enabled=False)
    rich_fake = [
        ("연합뉴스", "wire", 1.0, "국회 본회의, 예산안 처리 두고 여야 충돌",
         "국회는 3일 오후 본회의를 열어 내년도 예산안 처리를 두고 여야가 충돌했다. "
         "국민의힘은 합의 처리를 주장했고 더불어민주당은 독소조항을 지적했다. "
         "찬반 표결 끝에 예산안은 가결됐다. 이번 예산안 규모는 역대 최대인 700조원으로 집계됐다."),
        ("동아일보", "right", 0.7, "[속보] 예산안 국회 본회의 통과…여야 정면충돌",
         "국회는 3일 오후 본회의에서 내년도 예산안을 처리했다. 여야는 예산안 처리를 두고 "
         "정면충돌했다. 국민의힘은 합의 처리라고 밝혔다. 예산안은 표결 끝에 가결됐다. "
         "이번에 통과된 예산안 규모는 700조원으로 역대 최대 규모다."),
    ]
    init_db(cfg.db_path)
    with connect(cfg.db_path) as conn:
        for name, lean, w, title, summary in rich_fake:
            url = f"https://example.com/{url_hash(title)[:10]}"
            upsert_article(conn, {
                "url_hash": url_hash(url), "url": url, "source_name": name,
                "source_lean": lean, "source_weight": w, "title": title,
                "summary": summary, "published_ts": now(), "collected_ts": now(),
                "raw": {},
            })
    classify_pending(cfg)
    ids = build_clusters(cfg)
    script = build_script(ids[0], cfg)

    roles = [s["role"] for s in script["segments"]]
    assert "what" in roles, "fixture must actually exercise the body-exists branch"

    from political_shorts.explain import SIGNIFICANCE
    sig_text = SIGNIFICANCE[script["frame"]]
    what = next(s for s in script["segments"] if s["role"] == "what")
    outro = next(s for s in script["segments"] if s["role"] == "outro")
    assert sig_text not in what["narration"]
    assert sig_text in outro["narration"]


def _llm_cfg(tmp_path, name="qa.sqlite3"):
    return replace(load_settings(), db_path=tmp_path / name,
                   output_dir=tmp_path, data_dir=tmp_path, image_enabled=False,
                   llm_provider="gemini", gemini_api_key="k")


def _seed_rich_cluster(cfg):
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
    classify_pending(cfg)
    ids = build_clusters(cfg)
    return ids[0]


def test_quality_agent_regenerates_with_feedback_then_passes(tmp_path, monkeypatch):
    # a below-bar first attempt must trigger exactly one regenerate pass,
    # and the second (passing) attempt must be the one that ships.
    cfg = _llm_cfg(tmp_path, "qa1.sqlite3")
    cluster_id = _seed_rich_cluster(cfg)

    calls = {"n": 0}

    def fake_review(script, cfg):
        calls["n"] += 1
        if calls["n"] == 1:
            return quality_agent.AgentReport(
                available=True, score=60,
                issues=[{"role": "hook", "problem": "훅이 본문과 무관합니다."}])
        return quality_agent.AgentReport(available=True, score=97, issues=[])

    monkeypatch.setattr(quality_agent, "review", fake_review)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps(
        {"what": "여야가 합의해 예산안을 통과시켰습니다."}))

    script = build_script(cluster_id, cfg)
    qa = script["quality_agent"]
    assert qa["available"] is True
    assert qa["passed"] is True
    assert qa["attempts"] == 2
    assert calls["n"] == 2


def test_quality_agent_gives_up_after_4_attempts(tmp_path, monkeypatch):
    cfg = _llm_cfg(tmp_path, "qa2.sqlite3")
    cluster_id = _seed_rich_cluster(cfg)

    monkeypatch.setattr(quality_agent, "review", lambda script, cfg: quality_agent.AgentReport(
        available=True, score=50, issues=[{"role": "outro", "problem": "막연합니다."}]))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps(
        {"what": "여야가 합의해 예산안을 통과시켰습니다."}))

    script = build_script(cluster_id, cfg)
    qa = script["quality_agent"]
    assert qa["available"] is True
    assert qa["passed"] is False
    assert qa["attempts"] == 4


def test_quality_agent_ships_the_best_attempt_not_the_last(tmp_path, monkeypatch):
    # a real observed failure mode: scores don't climb monotonically
    # (45->85->85->45). When nothing reaches PASS_SCORE, the highest-scoring
    # attempt actually tried must ship — not whichever happened to run last.
    cfg = _llm_cfg(tmp_path, "qa5.sqlite3")
    cluster_id = _seed_rich_cluster(cfg)

    scores = [45, 85, 70, 45]                     # attempt 2 is the peak
    calls = {"n": 0}

    def fake_review(script, cfg):
        i = calls["n"]
        calls["n"] += 1
        return quality_agent.AgentReport(
            available=True, score=scores[i],
            issues=[{"role": "hook", "problem": f"문제 {i + 1}"}])

    monkeypatch.setattr(quality_agent, "review", fake_review)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps(
        {"what": "여야가 합의해 예산안을 통과시켰습니다."}))

    script = build_script(cluster_id, cfg)
    qa = script["quality_agent"]
    assert qa["passed"] is False
    assert qa["score"] == 85                       # the peak, not scores[-1] (45)


def test_quality_agent_feedback_accumulates_across_attempts(tmp_path, monkeypatch):
    # a real observed failure mode: passing only the LATEST critique let a
    # rewrite silently re-break something an earlier attempt already fixed.
    # Every rewrite must see every problem ever flagged, not just the last.
    cfg = _llm_cfg(tmp_path, "qa6.sqlite3")
    cluster_id = _seed_rich_cluster(cfg)

    seen_feedback = []

    def fake_complete(payload, cfg, **k):
        # stage-1 analyze_story() also calls complete() (a different system
        # prompt) — only the stage-2 rewrite call carries the feedback block.
        if "카드별 역할" not in k.get("system", ""):
            return "not json"                       # stage-1: let it fail, harmless
        seen_feedback.append(payload.split("편집장 피드백]")[-1] if "편집장 피드백" in payload else "")
        return json.dumps({"what": "여야가 합의해 예산안을 통과시켰습니다."})

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(quality_agent, "review", lambda script, cfg: quality_agent.AgentReport(
        available=True, score=50,
        issues=[{"role": "hook", "problem": f"문제 {len(seen_feedback)}"}]))

    build_script(cluster_id, cfg)
    assert len(seen_feedback) == 4
    assert seen_feedback[0] == ""                              # attempt 1: nothing yet
    assert "문제 1" in seen_feedback[1]                         # attempt 2 sees attempt 1's issue
    assert "문제 1" in seen_feedback[3] and "문제 2" in seen_feedback[3] and "문제 3" in seen_feedback[3]


def test_pipeline_skips_a_cluster_the_quality_agent_never_passed(tmp_path, monkeypatch):
    cfg = _llm_cfg(tmp_path, "qa3.sqlite3")
    cluster_id = _seed_rich_cluster(cfg)

    monkeypatch.setattr(quality_agent, "review", lambda script, cfg:
                        quality_agent.AgentReport(available=True, score=40, issues=[]))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps(
        {"what": "여야가 합의해 예산안을 통과시켰습니다."}))

    out = _process_story(cluster_id, cfg, do_publish=False, report=RunReport())
    assert out.status == "skipped"
    assert "품질 평가" in out.reason
    assert out.agent_score == 40                  # recorded regardless of pass/fail


def test_min_agent_score_lets_a_lower_score_through_the_same_gate(tmp_path, monkeypatch):
    # the fallback-of-the-day retry passes a LOWER floor explicitly — user:
    # "평가진행 후 실패하더라도, 가장 높은 점수의 영상을 올리는 것으로
    # 변경하자(하지만 95점 넘는 영상 제작되면 바로 올리기도 가능)". A score
    # that the DEFAULT floor (95) would skip must be let through once
    # min_agent_score is set at or below it — same gate, not a new one.
    cfg = _llm_cfg(tmp_path, "qa9.sqlite3")
    cluster_id = _seed_rich_cluster(cfg)

    monkeypatch.setattr(quality_agent, "review", lambda script, cfg:
                        quality_agent.AgentReport(available=True, score=85, issues=[]))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps(
        {"what": "여야가 합의해 예산안을 통과시켰습니다."}))

    class _ReachedRendering(Exception):
        pass

    monkeypatch.setattr(pipeline, "render_video",
                        lambda *a, **k: (_ for _ in ()).throw(_ReachedRendering()))

    # default floor (95) still skips an 85 — unchanged behavior
    out_default = _process_story(cluster_id, cfg, do_publish=False, report=RunReport())
    assert out_default.status == "skipped"
    assert out_default.agent_score == 85

    # the SAME 85 clears a lowered floor and proceeds all the way to
    # rendering (proven by our sentinel exception firing, not skipped)
    out_floored = _process_story(cluster_id, cfg, do_publish=False, report=RunReport(),
                                 min_agent_score=85)
    assert out_floored.status == "error"
    assert "_ReachedRendering" in out_floored.reason


def test_forced_script_skips_build_script_entirely(tmp_path, monkeypatch):
    # the fallback-of-the-day retry passes `forced_script` (the cached
    # script that actually earned the promoted candidate its score) instead
    # of letting _process_story rebuild from scratch — build_script() must
    # not be called at all in that path, since calling it again is exactly
    # what let a real 85-scoring cluster slip to 80 on a live CI dry-run.
    cfg = _llm_cfg(tmp_path, "qa9b.sqlite3")
    cluster_id = _seed_rich_cluster(cfg)

    def _should_not_be_called(*a, **k):
        raise AssertionError("build_script() was called despite forced_script being given")

    monkeypatch.setattr(pipeline, "build_script", _should_not_be_called)

    class _ReachedRendering(Exception):
        pass

    monkeypatch.setattr(pipeline, "render_video",
                        lambda *a, **k: (_ for _ in ()).throw(_ReachedRendering()))

    forced = {
        "cluster_id": cluster_id, "headline": "헤드라인", "title": ["a", "b"],
        "topic": "정치", "frame": "clash", "entities": {}, "segments": [],
        "n_sources": 3, "counts": {"facts": 3, "claims": 2, "interpretations": 2},
        "sources": [{"name": "연합뉴스", "url": "u", "lean": "wire"}],
        "factcheck": {}, "disclaimer": "공개 보도를 정리한 자동 제작물입니다.",
        "length_band": [25, 60], "style": "punchy",
        "quality_agent": {"available": True, "score": 85, "passed": False,
                          "attempts": 4, "issues": []},
    }

    out = _process_story(cluster_id, cfg, do_publish=False, report=RunReport(),
                         min_agent_score=85, forced_script=forced)
    assert out.status == "error"
    assert "_ReachedRendering" in out.reason        # reached rendering — gate passed
    assert out.agent_score == 85
    assert out.headline == "헤드라인"                 # came from forced_script, not a rebuild


def test_run_pipeline_promotes_the_best_candidate_when_nothing_hits_95(tmp_path, monkeypatch):
    # user: "평가진행 후 실패하더라도, 가장 높은 점수의 영상을 올리는 것으로
    # 변경하자" — with two candidates, neither reaching 95 (60 and 90), the
    # 90-scorer must be the one that ends up built, not the 60-scorer and
    # not nothing.
    cfg = _llm_cfg(tmp_path, "qa10.sqlite3")
    init_db(cfg.db_path)
    with connect(cfg.db_path) as conn:
        for name, lean, w, title, summary in FAKE:
            if "야구" in title:
                continue
            url = f"https://example.com/a/{url_hash(title)[:10]}"
            upsert_article(conn, {
                "url_hash": url_hash(url), "url": url, "source_name": name,
                "source_lean": lean, "source_weight": w, "title": title,
                "summary": summary, "published_ts": now(), "collected_ts": now(),
                "raw": {},
            })
        for i, (name, lean, w) in enumerate([("연합뉴스", "wire", 1.0), ("한겨레", "left", 0.7)]):
            title = "국회 인사청문회 여야 정면충돌"
            url = f"https://example.com/b/{i}/{url_hash(title)[:6]}"
            upsert_article(conn, {
                "url_hash": url_hash(url), "url": url, "source_name": name,
                "source_lean": lean, "source_weight": w, "title": title,
                "summary": "국회는 3일 인사청문회를 열어 후보자 자격을 두고 여야가 정면충돌했다.",
                "published_ts": now(), "collected_ts": now(), "raw": {},
            })

    id_to_score: dict[int, int] = {}
    call_count: dict[int, int] = {}
    score_seq = [60, 90]

    def fake_build_script(cluster_id, _cfg=None, **_kw):
        call_count[cluster_id] = call_count.get(cluster_id, 0) + 1
        if cluster_id not in id_to_score:
            id_to_score[cluster_id] = score_seq[min(len(id_to_score), len(score_seq) - 1)]
        score = id_to_score[cluster_id]
        # Simulate real run-to-run LLM variance: a THIRD build_script() call
        # for the SAME cluster scores much lower — this is exactly the real
        # defect a live CI dry-run exposed (a cluster that scored 85 on its
        # first pass scored only 80 on a rebuild, missing its own floor).
        # Two legitimate calls per cluster are expected from the pipeline's
        # OWN pre-existing retry structure in this scenario (the initial
        # politics walk, then the variety-off re-walk once nothing clears
        # the 95 bar) — a THIRD call would only happen if the fallback-of-
        # the-day step rebuilt instead of reusing the cached script, which
        # is exactly what this test must prove it no longer does.
        if call_count[cluster_id] > 2:
            score = 10
        return {
            "cluster_id": cluster_id, "headline": f"헤드라인 {cluster_id}", "title": ["a", "b"],
            "topic": "정치", "frame": "clash", "entities": {}, "segments": [],
            "n_sources": 3, "counts": {"facts": 3, "claims": 2, "interpretations": 2},
            "sources": [{"name": "연합뉴스", "url": "u", "lean": "wire"}],
            "factcheck": {}, "disclaimer": "공개 보도를 정리한 자동 제작물입니다.",
            "length_band": [25, 60], "style": "punchy",
            "quality_agent": {"available": True, "score": score, "passed": score >= 95,
                              "attempts": 4, "issues": []},
        }

    monkeypatch.setattr(pipeline, "build_script", fake_build_script)

    class _FakeRender:
        duration_s = 40.0
        timeline = None

    monkeypatch.setattr(pipeline, "render_video", lambda *a, **k: _FakeRender())
    monkeypatch.setattr(pipeline, "build_metadata", lambda *a, **k: {})

    import political_shorts.quality as quality_mod
    monkeypatch.setattr(quality_mod, "check", lambda *a, **k:
                        quality_mod.QualityReport(score=90, band="PASS", issues=[]))

    report = pipeline.run_pipeline(cfg, do_collect=False, do_publish=False, max_items=1)

    assert report.built == 1
    built = next(s for s in report.stories if s.status == "built")
    assert built.agent_score == 90                # the promoted fallback, not the 60-scorer
    assert built.held is False
    # proves the fix: the fallback promotion step never called build_script()
    # a THIRD time for the winning cluster — it reused the cached 90-scoring
    # script instead of rebuilding (a rebuild would have scored 10 per the
    # variance simulation above and been rejected even by the lowered floor,
    # which is exactly the failure a live CI dry-run exposed).
    assert call_count[built.cluster_id] <= 2


def test_run_pipeline_publishes_nothing_when_best_is_below_the_fallback_floor(tmp_path, monkeypatch):
    # the mirror case — best available is 60, below _FALLBACK_FLOOR (85):
    # nothing should ship, not even the "best" one.
    cfg = _llm_cfg(tmp_path, "qa11.sqlite3")
    init_db(cfg.db_path)
    with connect(cfg.db_path) as conn:
        for name, lean, w, title, summary in FAKE:
            if "야구" in title:
                continue
            url = f"https://example.com/{url_hash(title)[:10]}"
            upsert_article(conn, {
                "url_hash": url_hash(url), "url": url, "source_name": name,
                "source_lean": lean, "source_weight": w, "title": title,
                "summary": summary, "published_ts": now(), "collected_ts": now(),
                "raw": {},
            })

    def fake_build_script(cluster_id, _cfg=None, **_kw):
        return {
            "cluster_id": cluster_id, "headline": f"헤드라인 {cluster_id}", "title": ["a", "b"],
            "topic": "정치", "frame": "clash", "entities": {}, "segments": [],
            "n_sources": 3, "counts": {"facts": 3, "claims": 2, "interpretations": 2},
            "sources": [{"name": "연합뉴스", "url": "u", "lean": "wire"}],
            "factcheck": {}, "disclaimer": "공개 보도를 정리한 자동 제작물입니다.",
            "length_band": [25, 60], "style": "punchy",
            "quality_agent": {"available": True, "score": 60, "passed": False,
                              "attempts": 4, "issues": []},
        }

    monkeypatch.setattr(pipeline, "build_script", fake_build_script)
    render_calls = {"n": 0}
    monkeypatch.setattr(pipeline, "render_video",
                        lambda *a, **k: render_calls.__setitem__("n", render_calls["n"] + 1))

    report = pipeline.run_pipeline(cfg, do_collect=False, do_publish=False, max_items=1)

    assert report.built == 0
    assert render_calls["n"] == 0                  # never even got to rendering


def test_thin_material_never_even_calls_the_quality_agent(tmp_path, monkeypatch):
    # the pipeline-level thin-material gate makes the quality-agent loop's
    # work moot for this cluster — build_script shouldn't burn LLM calls on
    # a script that's getting discarded regardless of how it scores.
    cfg = _llm_cfg(tmp_path, "qa4.sqlite3")
    init_db(cfg.db_path)
    with connect(cfg.db_path) as conn:
        title = "국회, 새 법안 처리"
        url = f"https://example.com/{url_hash(title)[:10]}"
        upsert_article(conn, {
            "url_hash": url_hash(url), "url": url, "source_name": "연합뉴스",
            "source_lean": "wire", "source_weight": 1.0, "title": title,
            "summary": "국회는 3일 본회의를 열어 새 법안을 처리했다.",
            "published_ts": now(), "collected_ts": now(), "raw": {},
        })
    classify_pending(cfg)
    ids = build_clusters(cfg)

    calls = {"n": 0}
    monkeypatch.setattr(quality_agent, "review",
                        lambda script, cfg: calls.__setitem__("n", calls["n"] + 1) or
                        quality_agent.AgentReport(available=True, score=99, issues=[]))
    script = build_script(ids[0], cfg)
    assert calls["n"] == 0
    assert script["quality_agent"]["available"] is False


def test_thin_sourced_story_is_skipped_before_it_can_ship_undercontextualized(tmp_path, monkeypatch):
    # a real shipped case: 1 source, 1 fact, 1 claim, 1 interpretation — not
    # enough material for a background/what/sides card, so all three got
    # silently dropped by the length-budget trim, leaving hook -> a raw fact
    # dump -> outro with zero context. Better to skip it up front than ship
    # something with nothing to actually explain.
    cfg = replace(load_settings(), db_path=tmp_path / "t2.sqlite3",
                  output_dir=tmp_path, data_dir=tmp_path)
    init_db(cfg.db_path)
    thin_script = {
        "headline": "정부, 새 정책 발표", "entities": {}, "frame": "vote",
        "topic": "정부", "n_sources": 1,
        "counts": {"facts": 1, "claims": 1, "interpretations": 1},
        "segments": [], "factcheck": {},
    }
    monkeypatch.setattr(pipeline, "build_script", lambda *a, **k: thin_script)
    out = _process_story(1, cfg, do_publish=False, report=RunReport())
    assert out.status == "skipped"
    assert "소재 부족" in out.reason


def test_well_sourced_story_is_not_caught_by_the_thin_material_gate(tmp_path, monkeypatch):
    cfg = replace(load_settings(), db_path=tmp_path / "t3.sqlite3",
                  output_dir=tmp_path, data_dir=tmp_path)
    init_db(cfg.db_path)
    rich_script = {
        "headline": "국회, 예산안 통과", "entities": {}, "frame": "vote",
        "topic": "국회", "n_sources": 3,
        "counts": {"facts": 4, "claims": 3, "interpretations": 2},
        "segments": [{"role": "hook", "narration": "예산안이 통과됐습니다."}],
        "factcheck": {},
    }
    monkeypatch.setattr(pipeline, "build_script", lambda *a, **k: rich_script)
    out = _process_story(1, cfg, do_publish=False, report=RunReport())
    assert "소재 부족" not in out.reason


def test_an_already_covered_story_spends_no_llm_calls(tmp_path, monkeypatch):
    """Duplicate / saturated stories used to be built in full (research + writer + up to 4
    quality rewrites = 15-30 LLM calls) and only THEN thrown away. The check now runs first."""
    from political_shorts import pipeline as P
    from political_shorts import script_gen as SG

    cfg = replace(load_settings(), db_path=tmp_path / "early.sqlite3", output_dir=tmp_path,
                  data_dir=tmp_path, image_enabled=False, llm_provider="groq")
    init_db(cfg.db_path)
    with connect(cfg.db_path) as conn:
        for name, lean, w, title, summary in FAKE:
            if "야구" in title:
                continue
            url = f"https://example.com/{url_hash(title)[:10]}"
            upsert_article(conn, {
                "url_hash": url_hash(url), "url": url, "source_name": name,
                "source_lean": lean, "source_weight": w, "title": title,
                "summary": summary, "published_ts": now(), "collected_ts": now(), "raw": {}})
    classify_pending(cfg)
    cid = build_clusters(cfg)[0]

    llm_calls = []
    monkeypatch.setattr(llm, "complete", lambda *a, **k: llm_calls.append(1) or "{}")

    # (a) not covered -> the LLM stages are allowed to run
    monkeypatch.setattr(P, "recent_duplicate", lambda *a, **k: (False, ""))
    monkeypatch.setattr(P, "_theme_saturated", lambda *a, **k: "")
    SG.build_script(cid, cfg, skip_llm_if=lambda p: False)
    assert llm_calls, "control: with no veto the writer does call the LLM"

    # (b) already covered -> vetoed before any LLM call
    llm_calls.clear()
    seen = {}

    def veto(p):
        seen.update(p)
        return True

    SG.build_script(cid, cfg, skip_llm_if=veto)
    assert llm_calls == []
    assert seen["headline"] and seen["frame"] and "politicians" in seen["entities"]
