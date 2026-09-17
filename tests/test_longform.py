"""LONGFORM PIPELINE — theme grouping, chapter assembly, plan artifact.
Everything up to the render, which deliberately does not exist yet."""
import json
from dataclasses import replace

from political_shorts import longform
from political_shorts.config import load_settings
from political_shorts.db import connect, create_cluster, init_db


def _cfg(tmp_path, name="lf.sqlite3"):
    return replace(load_settings(), db_path=tmp_path / name,
                   output_dir=tmp_path, data_dir=tmp_path, image_enabled=False)


def _seed_clusters(cfg, titles):
    init_db(cfg.db_path)
    ids = []
    with connect(cfg.db_path) as conn:
        for t in titles:
            ids.append(create_cluster(conn, t, size=2, leans=["wire", "left"]))
    return ids


def test_group_themes_puts_related_stories_together(tmp_path):
    cfg = _cfg(tmp_path, "t1.sqlite3")
    _seed_clusters(cfg, [
        "김민석 교섭단체 요건 완화 제안",
        "김민석 교섭단체 15석 제안에 조국혁신당 반발",
        "교섭단체 요건 완화 두고 여야 공방",
        "프로야구 개막전 전 구장 매진",           # unrelated
    ])
    themes = longform.group_themes(cfg)
    assert themes
    # the three 교섭단체 stories group; the baseball one must not join them
    biggest = themes[0]
    assert biggest.size >= 2
    assert not any("야구" in k for k in biggest.keywords)


def test_group_themes_empty_db_is_not_a_crash(tmp_path):
    cfg = _cfg(tmp_path, "t2.sqlite3")
    init_db(cfg.db_path)
    assert longform.group_themes(cfg) == []


def test_plan_returns_empty_when_no_theme_is_big_enough(tmp_path):
    # one lone story can't carry a multi-chapter longform
    cfg = _cfg(tmp_path, "t3.sqlite3")
    _seed_clusters(cfg, ["김민석 교섭단체 요건 완화 제안"])
    plan = longform.plan_longform(cfg)
    assert plan.chapters == []
    assert plan.render_ready is False


def test_plan_assembles_chapters_from_build_script(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, "t4.sqlite3")
    ids = _seed_clusters(cfg, [
        "김민석 교섭단체 요건 완화 제안",
        "김민석 교섭단체 15석 제안에 조국혁신당 반발",
        "교섭단체 요건 완화 두고 여야 공방",
    ])

    def fake_build_script(cluster_id, _cfg=None):
        return {
            "headline": f"헤드라인 {cluster_id}",
            "frame": "clash",
            "n_sources": 3,
            "est_seconds": 48.0,
            "segments": [{"role": "hook", "narration": "훅입니다."}],
            "sources": [{"name": "연합뉴스", "url": "u", "lean": "wire"}],
            "quality_agent": {"available": True, "score": 97, "passed": True,
                              "attempts": 1, "issues": []},
        }

    import political_shorts.script_gen as sg
    monkeypatch.setattr(sg, "build_script", fake_build_script)

    plan = longform.plan_longform(cfg, max_chapters=3)
    assert len(plan.chapters) == 3
    assert plan.theme
    assert plan.opening and plan.closing
    assert plan.est_seconds > sum(c.est_seconds for c in plan.chapters)  # + intro/outro
    # every chapter carries its own numbered lead-in
    assert plan.chapters[0].bridge.startswith("1.")
    assert plan.chapters[2].bridge.startswith("3.")
    # the render stage does not exist — this must never claim otherwise
    assert plan.render_ready is False


def test_plan_drops_a_chapter_the_quality_agent_failed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, "t5.sqlite3")
    _seed_clusters(cfg, [
        "김민석 교섭단체 요건 완화 제안",
        "김민석 교섭단체 15석 제안에 조국혁신당 반발",
        "교섭단체 요건 완화 두고 여야 공방",
    ])
    calls = {"n": 0}

    def fake_build_script(cluster_id, _cfg=None):
        calls["n"] += 1
        passed = calls["n"] != 2          # the 2nd chapter fails the gate
        return {
            "headline": f"헤드라인 {cluster_id}", "frame": "clash",
            "n_sources": 3, "est_seconds": 48.0,
            "segments": [{"role": "hook", "narration": "훅입니다."}],
            "sources": [{"name": "연합뉴스", "url": "u", "lean": "wire"}],
            "quality_agent": {"available": True, "score": 97 if passed else 60,
                              "passed": passed, "attempts": 4, "issues": []},
        }

    import political_shorts.script_gen as sg
    monkeypatch.setattr(sg, "build_script", fake_build_script)

    plan = longform.plan_longform(cfg, max_chapters=3)
    assert len(plan.chapters) == 2
    assert len(plan.skipped) == 1
    assert "quality agent 60" in plan.skipped[0]["reason"]


def test_a_failing_cluster_does_not_sink_the_whole_plan(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, "t6.sqlite3")
    _seed_clusters(cfg, [
        "김민석 교섭단체 요건 완화 제안",
        "김민석 교섭단체 15석 제안에 조국혁신당 반발",
        "교섭단체 요건 완화 두고 여야 공방",
    ])
    calls = {"n": 0}

    def fake_build_script(cluster_id, _cfg=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {
            "headline": f"헤드라인 {cluster_id}", "frame": "clash",
            "n_sources": 3, "est_seconds": 48.0,
            "segments": [{"role": "hook", "narration": "훅입니다."}],
            "sources": [{"name": "연합뉴스", "url": "u", "lean": "wire"}],
            "quality_agent": {"available": True, "score": 97, "passed": True,
                              "attempts": 1, "issues": []},
        }

    import political_shorts.script_gen as sg
    monkeypatch.setattr(sg, "build_script", fake_build_script)

    plan = longform.plan_longform(cfg, max_chapters=3)
    assert len(plan.chapters) == 2            # the other two still made it
    assert any("RuntimeError" in s["reason"] for s in plan.skipped)


def test_write_plan_produces_readable_json_with_the_render_note(tmp_path):
    plan = longform.LongformPlan(
        theme="교섭단체 요건 완화", length_class="deep_dive",
        length_label="집중 분석", est_seconds=240.0,
        chapters=[longform.Chapter(cluster_id=1, headline="헤드라인")],
    )
    path = longform.write_plan(plan, tmp_path, stamp="20260917_120000")
    assert path.name == "longform_20260917_120000.plan.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["theme"] == "교섭단체 요건 완화"
    assert data["render_ready"] is False
    assert "not built yet" in data["render_note"]
    # the chapter's bridge field must survive asdict() (it was briefly a
    # setattr-only attribute, which asdict() silently dropped)
    assert "bridge" in data["chapters"][0]


def test_theme_label_strips_the_quote_laden_headline_tail():
    # a real observed defect in this module's own first integration run:
    # embedding a raw wire headline in the spoken opening nested quotes
    # inside quotes — 오늘은 '오늘 증인 없는 청문회..."93명에 신약 투약"'
    # 이야기를... — the same class of problem the shorts YouTube title had.
    out = longform.theme_label('오늘 증인 없는 청문회..."93명에 신약 투약"')
    assert out == "오늘 증인 없는 청문회"
    assert '"' not in out and "..." not in out


def test_theme_label_keeps_the_subject_when_the_headline_has_a_comma():
    # a real headline that a comma-splitting version of this reduced to a
    # useless bare "李대통령", losing the entire subject
    out = longform.theme_label(
        "李대통령, 김지용 중수청장 후보 추가 검증 지시...청문요청서 제출도 미뤄질 듯(종합)")
    assert "김지용" in out or "중수청장" in out     # the subject survived
    assert "李" not in out                          # Hanja resolved to 한글
    assert "(종합)" not in out


def test_theme_label_takes_the_substantive_half_of_a_quote_led_headline():
    # a headline that OPENS with a short quoted exclamation — its real
    # content is the SECOND fragment, so "first fragment" was wrong
    out = longform.theme_label('"악마" "독사"...김승원 청문회 여야 언쟁에 정회(종합)')
    assert "김승원" in out
    assert "악마" not in out


def test_theme_label_never_cuts_mid_word():
    out = longform.theme_label("교섭단체 요건 완화 논의 본격화 여야 공방 계속", limit=14)
    assert not out.endswith(" ")
    assert out in "교섭단체 요건 완화 논의 본격화 여야 공방 계속"
    # cut landed on a word boundary, not inside a word
    assert len(out) <= 14


def test_opening_reads_correctly_for_a_single_chapter():
    theme = longform.Theme(keywords=["a"], cluster_ids=[1], lead_title="교섭단체 요건 완화")
    one = longform._opening(theme, 1)
    assert "1개 갈래로 나눠서" not in one          # awkward for a single chapter
    many = longform._opening(theme, 4)
    assert "4개 갈래로 나눠서" in many


def test_length_class_scales_with_chapter_count():
    assert longform._length_class(3)[0] == "deep_dive"
    assert longform._length_class(5)[0] == "standard"
    assert longform._length_class(8)[0] == "roundup"
    assert longform._length_class(20)[0] == "roundup"     # clamped, not crashed
