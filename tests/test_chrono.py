"""Years: the writer is told today's date, and a year nothing supports never ships."""
from datetime import datetime, timezone

from political_shorts import chrono, quality_agent, research, script_gen as SG, script_llm
from political_shorts.config import settings

NOW = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)          # 10:00 KST Tuesday 22 Sep 2026


# ------------------------------------------------------------------------------ the date the writer sees
def test_date_block_states_today_and_what_relative_years_mean():
    b = chrono.date_block(now=NOW)
    assert "오늘은 2026년 9월 22일(화요일)" in b
    assert "올해는 2026년" in b and "지난해)은 2025년" in b and "내년은 2027년" in b
    assert "연·월·일을 모두 쓰고" in b and "짐작 금지" in b        # a date is written in full, never guessed


def test_date_block_lists_the_articles_publication_dates_in_kst():
    rows = [{"published_ts": int(datetime(2026, 9, 20, 23, 30, tzinfo=timezone.utc).timestamp()), "source_name": "연합뉴스"},
            {"published_ts": int(datetime(2026, 9, 21, 3, 0, tzinfo=timezone.utc).timestamp()), "source_name": "한겨레"},
            {"published_ts": 0, "source_name": "무시"}]
    b = chrono.date_block(rows, NOW)
    assert "2026-09-21 연합뉴스" in b                    # 23:30 UTC is already the 21st in Korea
    assert "2026-09-21 한겨레" in b and "무시" not in b


# ------------------------------------------------------------ years that nothing supports are removed
def test_incident_a_year_the_model_invented_is_removed_but_a_supported_one_stays():
    """The model, never told the date, wrote years from its training data (2023-2025) into 2026 videos."""
    src = "박 전 사무총장은 지난해 4월 사퇴했다. 감사원은 2023년 감사 결과를 내놨다. 판결은 20일 나왔다."
    out, gone = chrono.strip_years("2024년 9월 20일 서울행정법원은 원고 승소 판결을 내렸습니다. 2023년 감사 결과가 근거였습니다.", src, NOW)
    assert out == "9월 20일 서울행정법원은 원고 승소 판결을 내렸습니다. 2023년 감사 결과가 근거였습니다."
    assert gone == [2024]                                 # 2023 is in the article; 2024 is a guess


def test_this_year_is_always_allowed_and_quantities_are_not_years():
    out, gone = chrono.strip_years("2026년 9월 20일 예산 2000억 원, 1,431개 장비, 1990건이 접수됐습니다.", "", NOW)
    assert gone == [] and out == "2026년 9월 20일 예산 2000억 원, 1,431개 장비, 1990건이 접수됐습니다."


def test_a_wrong_year_next_to_a_relative_word_loses_only_the_year():
    out, gone = chrono.strip_years("올해 2024년 9월 국회는 예산안을 처리했습니다.", "", NOW)
    assert out == "올해 9월 국회는 예산안을 처리했습니다." and gone == [2024]
    assert chrono.wrong_relative_years("올해 2024년, 지난해 2025년, 내년 2027년", NOW) == ["올해 2024년"]


def test_fiscal_year_forms_and_several_years_in_one_sentence():
    out, gone = chrono.strip_years("2025년도 예산과 2019년 결정, 2026년도 계획", "", NOW)
    assert out == "예산과 결정, 2026년도 계획" and gone == [2025, 2019]
    assert chrono.unsupported_years("2025년 2026년", "2025년 통계", NOW) == []


# ------------------------------------------------------------------ wired into the writing prompts
def test_every_prompt_that_writes_or_judges_the_script_carries_the_date():
    meta = {"source_text": "기사", "facts": [], "claims": [], "interps": [], "entities": {},
            "date_block": chrono.date_block(now=NOW)}
    assert "[오늘 날짜] 오늘은 2026년 9월 22일" in script_llm._analysis_payload(meta)
    assert "[오늘 날짜] 오늘은 2026년 9월 22일" in script_llm._payload(meta, [{"role": "what", "narration": "가나다라마바"}])
    assert script_llm._payload({**meta, "date_block": ""}, []).startswith("[")             # nothing breaks without it
    assert "연도" in quality_agent._SYSTEM and "[날짜]" in quality_agent._SYSTEM


def test_the_research_prompts_and_the_judge_are_given_the_date(monkeypatch):
    import political_shorts.llm as L
    seen = {}

    def fake_complete(prompt, cfg, max_tokens=400, system=""):
        seen.setdefault("prompts", []).append(prompt)
        return '{"event":"e","question":"q","queries":["a b"]}'

    monkeypatch.setattr(L, "complete", fake_complete)
    research.plan_research("헤드라인", "인물", "", settings)
    assert "[오늘 날짜]" in seen["prompts"][0]
    bodies = [{"title": "t", "source": "s", "link": "https://a", "text": "본문입니다. " * 30, "lean": ""}]
    research._extract_notes("주제", bodies, settings, {})
    assert "[오늘 날짜]" in seen["prompts"][1]
    quality_agent.review({"headline": "h", "segments": [{"role": "what", "narration": "무슨 일이 있었습니다."}]},
                         __import__("dataclasses").replace(settings, llm_provider="gemini", gemini_api_key="k"))
    assert "[오늘 날짜]" in seen["prompts"][2]


# ----------------------------------------------------- wired into the finished script
def test_strip_years_in_cleans_narration_caption_and_kicker_in_place():
    segs = [{"role": "what", "narration": "2024년 9월 20일 판결이 나왔습니다.", "caption": "2024년 9월 20일 판결", "kicker": "2025년 요약"},
            {"role": "outro", "narration": "여러분은 어떻게 보시나요?"}]
    gone = SG._strip_years_in(segs, "판결은 20일 나왔다")
    assert sorted(gone) == [2024, 2024, 2025]
    assert segs[0]["narration"] == "9월 20일 판결이 나왔습니다." and segs[0]["caption"] == "9월 20일 판결" and segs[0]["kicker"] == "요약"
    assert segs[1]["narration"] == "여러분은 어떻게 보시나요?"


def test_build_script_never_ships_an_unsupported_year(tmp_path, monkeypatch):
    """End to end: the writer (mocked) invents 2023/2024; neither the spoken text, the captions nor the title keep them."""
    import dataclasses
    import json

    from political_shorts import llm, quality_agent as QA
    from political_shorts.classify import classify_pending
    from political_shorts.db import connect, init_db, now, upsert_article
    from political_shorts.dedupe import build_clusters
    from political_shorts.textutil import url_hash

    cfg = dataclasses.replace(__import__("political_shorts.config", fromlist=["x"]).load_settings(),
                              db_path=tmp_path / "yr.sqlite3", output_dir=tmp_path, data_dir=tmp_path, image_enabled=False,
                              llm_provider="gemini", gemini_api_key="k")
    init_db(cfg.db_path)
    with connect(cfg.db_path) as conn:
        for name, lean, w, title, summary in [
                ("연합뉴스", "wire", 1.0, "국회 본회의, 예산안 처리 두고 여야 충돌",
                 "국회는 3일 오후 본회의를 열어 내년도 예산안 처리를 두고 여야가 충돌했다. 국민의힘은 합의 처리를 주장했고 "
                 "더불어민주당은 독소조항을 지적했다. 찬반 표결 끝에 예산안은 가결됐다."),
                ("한겨레", "left", 0.7, "예산안 본회의 의결…민주당 '독소조항' 반발",
                 "국회는 3일 오후 본회의를 열어 내년도 예산안을 의결했다. 더불어민주당은 일부 조항이 독소조항이라고 반발했다.")]:
            url = f"https://example.com/{url_hash(title)[:10]}"
            upsert_article(conn, {"url_hash": url_hash(url), "url": url, "source_name": name, "source_lean": lean,
                                  "source_weight": w, "title": title, "summary": summary, "published_ts": now(),
                                  "collected_ts": now(), "raw": {}})
    classify_pending(cfg)
    cid = build_clusters(cfg)[0]
    monkeypatch.setattr(QA, "review", lambda script, cfg: QA.AgentReport(available=True, score=96, issues=[]))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps({
        "title": ["2023년 예산안 처리", "무슨 일일까?"],
        "hook": "국회는 왜 2024년 예산안을 3일에 처리했을까요?",
        "summary": "2023년 9월 국회는 본회의를 열었습니다.",
        "what": "2024년 9월 3일 국회는 내년도 예산안을 처리했습니다.",
        "outro": "예산안 처리를 둘러싼 공방은 이어집니다. 여러분은 어떻게 보시나요? 댓글로 남겨주세요."}, ensure_ascii=False))
    script = SG.build_script(cid, cfg)
    everything = " ".join([" ".join(script["title"])] + [s.get("narration", "") + " " + s.get("caption", "") for s in script["segments"]])
    assert "2023년" not in everything and "2024년" not in everything
    import re
    assert not [y for y in re.findall(r"((?:19|20)\d{2})\s*년", everything) if int(y) != chrono.today().year]


# ------------------------------------------------------------------------ month and day
PUB = [(2026, 9, 21)]                 # the articles were published on 21 September 2026


def test_incident_a_wrong_month_is_corrected_to_the_articles_month():
    """강훈식 사의 (9/21): '2023년 5월 21일 아침 사퇴' - with the year removed, 5월 21일 was still wrong."""
    src = "강훈식 비서실장은 21일 사의를 표명했다. 성기홍 수석은 브리핑을 열었다."
    text, gone = chrono.strip_years("결국 2023년 5월 21일 아침 사퇴 의사를 전했습니다.", src, NOW)
    assert gone == [2023] and text == "결국 5월 21일 아침 사퇴 의사를 전했습니다."
    fixed, changes = chrono.fix_month_days(text, src, PUB, NOW)
    assert fixed == "결국 9월 21일 아침 사퇴 의사를 전했습니다." and changes == [("5월 21일", "9월 21일")]


def test_dates_the_sources_state_are_kept_and_unsupported_ones_become_that_day():
    src = "통일부는 14일 자료를 제출했다. 박 의원은 9월 16일 비판했다. 구속 기간은 26일 만료된다."
    keep, ch = chrono.fix_month_days("9월 14일 자료 제출, 9월 16일 비판, 9월 26일 만료", src, [(2026, 9, 20)], NOW)
    assert keep == "9월 14일 자료 제출, 9월 16일 비판, 9월 26일 만료" and ch == []        # 26일 is in the future: allowed
    text, ch = chrono.fix_month_days("3월 2일 국회는 표결했습니다.", src, [(2026, 9, 20)], NOW)   # 2일 appears nowhere
    assert text == "이날 국회는 표결했습니다." and ch == [("3월 2일", "이날")]


def test_the_previous_month_is_allowed_for_a_past_day():
    text, ch = chrono.fix_month_days("8월 30일 사건이 발생했습니다.", "지난 30일 사건이 발생했다.", [(2026, 9, 3)], NOW)
    assert text == "8월 30일 사건이 발생했습니다." and ch == []                            # "지난 30일" on 3 Sep = 30 Aug


def test_month_day_fix_ignores_quantities_and_non_dates():
    t = "예산 13월분? 아니고 3조 2000억 원, 5월 32일은 없는 날"
    assert chrono.fix_month_days(t, "", PUB, NOW)[0] == t
    assert chrono.pub_dates([{"published_ts": 0}, {"published_ts": None}, {}]) == []


def _budget_cluster(tmp_path):
    """Two articles about one budget vote, published today; -> (cfg, cluster id)."""
    import dataclasses

    from political_shorts.classify import classify_pending
    from political_shorts.config import load_settings
    from political_shorts.db import connect, init_db, now, upsert_article
    from political_shorts.dedupe import build_clusters
    from political_shorts.textutil import url_hash

    cfg = dataclasses.replace(load_settings(), db_path=tmp_path / "md.sqlite3", output_dir=tmp_path, data_dir=tmp_path,
                              image_enabled=False, llm_provider="gemini", gemini_api_key="k")
    init_db(cfg.db_path)
    with connect(cfg.db_path) as conn:
        for name, lean, w, title, summary in [
                ("연합뉴스", "wire", 1.0, "국회 본회의, 예산안 처리 두고 여야 충돌",
                 "국회는 3일 오후 본회의를 열어 내년도 예산안 처리를 두고 여야가 충돌했다. 국민의힘은 합의 처리를 주장했고 "
                 "더불어민주당은 독소조항을 지적했다. 찬반 표결 끝에 예산안은 가결됐다."),
                ("한겨레", "left", 0.7, "예산안 본회의 의결…민주당 '독소조항' 반발",
                 "국회는 3일 오후 본회의를 열어 내년도 예산안을 의결했다. 더불어민주당은 일부 조항이 독소조항이라고 반발했다.")]:
            url = f"https://example.com/{url_hash(title)[:10]}"
            upsert_article(conn, {"url_hash": url_hash(url), "url": url, "source_name": name, "source_lean": lean,
                                  "source_weight": w, "title": title, "summary": summary, "published_ts": now(),
                                  "collected_ts": now(), "raw": {}})
    classify_pending(cfg)
    return cfg, build_clusters(cfg)[0]


def _spoken(script):
    return " ".join(s.get("narration", "") + " " + s.get("caption", "") for s in script["segments"])


def test_build_script_corrects_a_wrong_month_and_year_end_to_end(tmp_path, monkeypatch):
    import json

    from political_shorts import llm, quality_agent as QA

    cfg, cid = _budget_cluster(tmp_path)
    month = chrono.today().month
    wrong_month = 5 if month != 5 else 4
    monkeypatch.setattr(QA, "review", lambda script, cfg: QA.AgentReport(available=True, score=96, issues=[]))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps({
        "summary": f"2023년 {wrong_month}월 3일 국회는 본회의를 열었습니다.",
        "what": f"2024년 {wrong_month}월 3일 국회는 내년도 예산안을 처리했습니다.",
        "outro": "예산안 처리를 둘러싼 공방은 이어집니다. 여러분은 어떻게 보시나요? 댓글로 남겨주세요."}, ensure_ascii=False))
    script = SG.build_script(cid, cfg)
    spoken = _spoken(script)
    assert "2023년" not in spoken and "2024년" not in spoken
    assert f"{wrong_month}월 3일" not in spoken                     # the wrong month is gone ...
    assert f"{month}월 3일" in spoken                               # ... replaced by the month the articles were published in
    assert f"{chrono.today().year}년 {month}월 3일" in spoken       # ... and the date is complete: the year is the real one


def test_every_script_states_a_full_date_by_default_and_it_is_not_an_added_number(tmp_path, monkeypatch):
    """The writer gave no date at all: the video still opens with the real one, and the number check that flags
    figures missing from the sources does not flag it."""
    import json
    import re

    from political_shorts import llm, quality_agent as QA

    cfg, cid = _budget_cluster(tmp_path)
    monkeypatch.setattr(QA, "review", lambda script, cfg: QA.AgentReport(available=True, score=96, issues=[]))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps({
        "summary": "국회는 본회의를 열어 예산안을 처리했습니다.",
        "what": "국민의힘은 합의 처리를, 민주당은 독소조항 삭제를 주장했습니다.",
        "outro": "예산안 처리를 둘러싼 공방은 이어집니다. 여러분은 어떻게 보시나요? 댓글로 남겨주세요."}, ensure_ascii=False))
    script = SG.build_script(cid, cfg)
    t = chrono.today()
    full = f"{t.year}년 {t.month}월 {t.day}일"
    narr = " ".join(s.get("narration", "") for s in script["segments"])
    assert f"{full} 소식입니다" in narr
    assert full in script["source_text"]
    for n in re.findall(r"\d[\d,.]*", narr):                        # quality.py's added-number rule, applied here
        assert len(n) < 2 or n in script["source_text"], n


# ------------------------------------------------- year and date are a DEFAULT, and correct (not deleted)
PUBS = [(2026, 9, 21)]


def test_normalize_dates_turns_a_wrong_year_and_month_into_the_right_full_date():
    """'2023년 5월 21일' for a story of 21 September 2026 is corrected, not merely stripped of its year."""
    out, changes = chrono.normalize_dates("2023년 5월 21일 강훈식 실장이 사의를 밝혔습니다.", "21일 사의를 밝혔다", PUBS,
                                          {"first_done": False}, NOW)
    assert out.startswith("2026년 9월 21일 강훈식")
    assert changes


def test_the_first_date_carries_the_year_and_later_ones_do_not():
    state = {"first_done": False}
    first, _ = chrono.normalize_dates("9월 21일 사의를 밝혔습니다.", "", PUBS, state, NOW)
    later, _ = chrono.normalize_dates("9월 21일 오후 브리핑이 열렸습니다.", "", PUBS, state, NOW)
    assert first.startswith("2026년 9월 21일")
    assert later.startswith("9월 21일")                  # the year is stated once, not on every date
    bare, _ = chrono.normalize_dates("21일 사의를 밝혔습니다.", "", PUBS, {"first_done": False}, NOW)
    assert bare == "21일 사의를 밝혔습니다."               # a bare day is not a date mention: left alone


def test_a_year_january_dates_belong_to_the_year_closest_to_publication():
    assert chrono.resolve_year(12, 30, [(2027, 1, 2)], NOW) == 2026
    assert chrono.resolve_year(1, 3, [(2027, 1, 2)], NOW) == 2027
    assert chrono.resolve_year(9, 21, [], NOW) == 2026          # no publication date: today decides


def test_an_older_event_keeps_the_year_the_source_itself_names():
    src = "2016년 10월 24일 JTBC 보도 이후 국정농단 사건이 불거졌다. 21일 사의를 밝혔다."
    out, _ = chrono.normalize_dates("2016년 10월 24일 보도가 시작이었습니다.", src, PUBS, {"first_done": False}, NOW)
    assert out.startswith("2016년 10월 24일")


def test_a_year_the_sources_never_mention_is_replaced_by_the_computed_one():
    src = "10월 24일 표결 예정. 21일 협상."
    out, _ = chrono.normalize_dates("2025년 10월 24일 표결이 예정돼 있습니다.", src, PUBS, {"first_done": False}, NOW)
    assert out.startswith("2026년 10월 24일")


def test_dateline_and_date_facts_use_the_publication_date():
    assert chrono.dateline(PUBS, NOW) == "2026년 9월 21일 소식입니다."
    assert chrono.dateline([], NOW) == "2026년 9월 22일 소식입니다."
    facts = chrono.date_facts(PUBS, NOW)
    assert "2026년 9월 21일" in facts and "2026년 9월 22일" in facts


def test_a_script_with_no_date_gets_a_dateline_and_one_with_a_date_does_not():
    segs = [{"role": "hook", "narration": "무슨 일일까요", "caption": "무슨 일?"},
            {"role": "summary", "narration": "대통령실 비서실장이 사의를 밝혔습니다.", "caption": "비서실장 사의"}]
    SG._strip_years_in(segs, "21일 사의", PUBS)
    assert segs[1]["narration"].startswith("2026년 9월 21일 소식입니다. 대통령실")
    assert segs[1]["caption"].startswith("2026년 9월 21일 소식입니다.")
    assert segs[0]["narration"] == "무슨 일일까요"                     # the hook stays a hook

    dated = [{"role": "hook", "narration": "무슨 일일까요"},
             {"role": "summary", "narration": "9월 21일 비서실장이 사의를 밝혔습니다."}]
    SG._strip_years_in(dated, "21일 사의", PUBS)
    assert dated[1]["narration"] == "2026년 9월 21일 비서실장이 사의를 밝혔습니다."
    assert "소식입니다" not in " ".join(s["narration"] for s in dated)


def test_the_description_states_the_year(monkeypatch):
    from pathlib import Path

    from political_shorts import metadata

    monkeypatch.setattr(metadata, "_now_local", lambda cfg: datetime(2026, 9, 22, 10, 0))
    script = {"headline": "예산안 국회 통과", "title": ["예산안 통과"], "segments": [], "sources": [], "source_text": ""}
    meta = metadata.build_metadata(script, {"passed": True, "warnings": []}, Path("x.mp4"))
    assert "2026년 09월 22일 정치 이슈를" in meta["description"]


# ---------------------------------------- incident: "2024년 4월 21일 관저" in a story of 21 September 2026 (shipped)
def test_incident_the_dinner_video_said_2024_4_21_and_now_says_the_real_date():
    src = "미셸 스틸 주한미국대사가 21일 여야 지도부를 관저로 초청해 만찬을 함께했다"
    said = "미셸 스틸 대사는 2024년 4월 21일 주한미국대사 관저에서 여야 지도부와 만찬을 가졌습니다."
    stripped, _ = chrono.strip_years(said, src, NOW)
    fixed, _ = chrono.normalize_dates(stripped, src, PUBS, {"first_done": False}, NOW)
    assert fixed == "미셸 스틸 대사는 2026년 9월 21일 주한미국대사 관저에서 여야 지도부와 만찬을 가졌습니다."


def test_the_short_spoken_years_are_years_too_but_durations_are_not():
    src = "예산안 논의"
    assert chrono.strip_years("24년도 예산안을 처리했다", src, NOW)[0] == "예산안을 처리했다"
    assert chrono.strip_years("'24년 예산안을 처리했다", src, NOW)[0] == "예산안을 처리했다"
    assert chrono.unsupported_years("24년도 예산", src, NOW) == [2024]
    for duration in ("20년 만에 처음 열렸다", "10년간 이어진 갈등", "3년 전 일이다"):
        assert chrono.strip_years(duration, src, NOW)[0] == duration


def test_a_short_year_the_source_itself_uses_is_supported():
    assert chrono.unsupported_years("24년도 예산", "정부는 24년도 예산 집행을 점검했다", NOW) == []
    assert chrono.unsupported_years("2024년도 예산", "정부는 24년도 예산 집행을 점검했다", NOW) == []


def test_a_short_year_before_a_full_date_is_absorbed_into_the_corrected_date():
    out, _ = chrono.normalize_dates("24년 4월 21일 관저에서 만찬을 가졌습니다.", "21일 만찬", PUBS,
                                    {"first_done": False}, NOW)
    assert out == "2026년 9월 21일 관저에서 만찬을 가졌습니다."
