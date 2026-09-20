"""Incident replays. Every defect the channel actually shipped (or nearly shipped) is written here
as a test, so fixing one thing can no longer quietly bring another back. Add a test here BEFORE
fixing the next incident.

Each test names the video / run it came from."""
import dataclasses
import json

from political_shorts import invariants, research
from political_shorts import llm as L
from political_shorts import pipeline as P
from political_shorts import script_gen as SG
from political_shorts.config import load_settings, settings
from political_shorts.db import connect, init_db, now, upsert_article
from political_shorts.textutil import url_hash

# The research a run really produced for the DMZ-festival story (two parties, a key statement, a cause)
WEB = {
    "why": [{"reason": "경기도는 재정 상황과 고비용 저성과 문제를 이유로 영화제 운영을 줄였다",
             "evidence": "9월 19일 경기도 설명", "source": "연합뉴스"}],
    "statements": [{"who": "김민석 대표", "when": "19일",
                    "text": "경기도가 국제 다큐멘터리 영화제와의 계약을 일방적으로 파기하고 축소하는 것은 문제가 있다",
                    "source": "한겨레"}],
    "positions": [{"who": "김민석 대표", "position": "축소를 계약 파기라고 비판", "why": "", "lean": "진보"},
                  {"who": "경기도", "position": "고비용 저성과를 이유로 축소", "why": "재정", "lean": "기타"}],
}


def _seg(role, narration):
    return {"role": role, "narration": narration}


# ---------------------------------------------------------------- video 1: critic-only (김민석 얼굴, 경기도 얘기)
def test_incident_video_that_voiced_only_one_party_is_flagged():
    """The province's rebuttal existed in the research, but the video quoted only the critic."""
    critic_only = [
        _seg("hook", "김민석 대표는 왜 경기도를 비판했을까요?"),
        _seg("what", "김민석 대표는 19일 경기도가 영화제와의 계약을 일방적으로 파기하고 축소하는 것은 문제가 있다고 밝혔습니다."),
        _seg("sides", "김민석 대표는 이를 계약 파기라며 비판했습니다."),
        _seg("outro", "이번 공방, 여러분은 어떻게 보시나요? 댓글로 남겨주세요."),
    ]
    codes = [v["code"] for v in invariants.check(critic_only, WEB)]
    assert "one_sided" in codes
    msg = next(v["message"] for v in invariants.check(critic_only, WEB) if v["code"] == "one_sided")
    assert "경기도" in msg


# ------------------------------------------------ video 2: province-only, no research, no quote ("발언은 왜 사라졌나")
def test_incident_province_only_video_without_the_critics_quote_is_flagged():
    province_only = [
        _seg("hook", "경기도는 왜 영화제 규모를 줄이기로 결정했을까요?"),
        _seg("summary", "DMZ국제다큐멘터리영화제는 경기도가 주최하는 국내 대표적인 다큐멘터리 영화제입니다."),
        _seg("factcheck", "경기도는 재정 상황과 고비용, 저성과 문제를 이유로 축소 운영하기로 결정했습니다."),
        _seg("outro", "경기도의 이번 영화제 축소 운영, 여러분은 어떻게 보시나요? 댓글로 의견을 남겨주세요."),
    ]
    codes = [v["code"] for v in invariants.check(province_only, WEB)]
    assert "one_sided" in codes and "missing_quote" in codes
    assert "missing_cause" not in codes                      # the province's reason WAS stated


def test_incident_a_video_written_with_no_research_at_all_is_held():
    """KlvYSxxzmis: the note extraction came back empty, the script came from one article, and it shipped."""
    segs = [_seg("hook", "경기도는 왜 영화제를 줄였을까요?"), _seg("outro", "여러분은 어떻게 보시나요? 댓글로 남겨주세요.")]
    assert [v["code"] for v in invariants.check(segs, {}, research_expected=True)] == ["research_missing"]
    assert invariants.check(segs, {}, research_expected=False) == []       # research switched off: nothing to hold


def test_a_script_that_voices_both_parties_the_quote_and_the_cause_passes():
    good = [
        _seg("hook", "경기도는 왜 영화제 규모를 줄이기로 했을까요?"),
        _seg("summary", "경기도는 재정 상황과 고비용 저성과 문제를 이유로 영화제 운영을 줄였습니다."),
        _seg("what", "김민석 대표는 19일 경기도가 국제 다큐멘터리 영화제와의 계약을 일방적으로 파기하고 축소하는 것은 문제가 있다고 밝혔습니다."),
        _seg("sides", "경기도는 고비용 저성과를 이유로 축소했다고 설명했습니다. 반면 김민석 대표는 이를 계약 파기라며 비판했습니다."),
        _seg("outro", "이번 공방, 여러분은 어떻게 보시나요? 댓글로 남겨주세요."),
    ]
    assert invariants.check(good, WEB, research_expected=True) == []


# ---------------------------------- the face of a person the script never names (김민석 얼굴 + 경기도 이야기)
def test_incident_portrait_of_a_person_the_script_never_mentions_is_dropped(tmp_path, monkeypatch):
    from political_shorts.classify import classify_pending
    from political_shorts.dedupe import build_clusters

    cfg = dataclasses.replace(load_settings(), db_path=tmp_path / "face.sqlite3", output_dir=tmp_path,
                              data_dir=tmp_path, image_enabled=True)
    init_db(cfg.db_path)
    rows = [("연합뉴스", "wire", 1.0, "국회 본회의, 예산안 처리 두고 여야 충돌",
             "국회는 3일 오후 본회의를 열어 내년도 예산안 처리를 두고 여야가 충돌했다. 김민석 대표는 반대 입장을 밝혔다. "
             "찬반 표결 끝에 예산안은 가결됐다."),
            ("한겨레", "left", 0.7, "예산안 본회의 의결…민주당 '독소조항' 반발",
             "국회는 3일 오후 본회의를 열어 내년도 예산안을 의결했다. 더불어민주당은 일부 조항이 독소조항이라고 반발했다.")]
    with connect(cfg.db_path) as conn:
        for name, lean, w, title, summary in rows:
            url = f"https://example.com/{url_hash(title)[:10]}"
            upsert_article(conn, {"url_hash": url_hash(url), "url": url, "source_name": name, "source_lean": lean,
                                  "source_weight": w, "title": title, "summary": summary, "published_ts": now(),
                                  "collected_ts": now(), "raw": {}})
    classify_pending(cfg)
    cid = build_clusters(cfg)[0]

    class _Img:
        def __init__(self, q, kind):
            self.__dict__.update(path="x.jpg", title=q, author="a", license="CC", source_url="u",
                                 query=q, kind=kind, width=1, height=1, is_lead=False)

    import political_shorts.images as I
    monkeypatch.setattr(I, "collect_images", lambda *a, **k: [_Img("김민석", "portrait"), _Img("국회의사당", "photo")])
    script = SG.build_script(cid, cfg)
    spoken = " ".join([script["headline"]] + [s.get("narration", "") for s in script["segments"]])
    for im in script["images"]:
        if im["kind"] == "portrait":
            assert im["query"] in spoken                     # a face is shown only if that person is in the script
    if "김민석" not in spoken:
        assert not any(im["kind"] == "portrait" for im in script["images"])
    assert any(im["kind"] == "photo" for im in script["images"])    # non-portrait pictures are untouched


# ---------------------------------- one event, split by actor: the OTHER side's coverage reaches the writer
def test_incident_the_other_sides_articles_are_attached_as_related_coverage(tmp_path):
    from political_shorts.classify import classify_pending  # noqa: F401
    from political_shorts.db import upsert_article as up

    cfg = dataclasses.replace(load_settings(), db_path=tmp_path / "rel.sqlite3", output_dir=tmp_path, data_dir=tmp_path)
    init_db(cfg.db_path)
    arts = [(1, "김민석, 경기도 DMZ다큐영화제 축소에 계약 파기 비판", "김민석 대표는 경기도의 영화제 축소를 비판했다."),
            (2, "경기도, DMZ영화제 축소운영 비판에 고비용 저성과 반박", "경기도는 고비용 저성과를 이유로 축소했다고 반박했다."),
            (3, "여야, 예산안 처리 두고 본회의 충돌", "국회는 예산안을 처리했다.")]
    with connect(cfg.db_path) as conn:
        for cid, title, summary in arts:
            url = f"https://example.com/{cid}"
            up(conn, {"url_hash": url_hash(url), "url": url, "source_name": f"매체{cid}", "source_lean": "left" if cid == 1 else "wire",
                      "source_weight": 1.0, "title": title, "summary": summary, "published_ts": now(),
                      "collected_ts": now(), "raw": {}})
            conn.execute("UPDATE articles SET cluster_id=? WHERE url_hash=?", (cid, url_hash(url)))
        rows = list(conn.execute("SELECT * FROM articles WHERE cluster_id = 1"))
    related = SG._related_reports(1, rows, cfg)
    assert [r["title"].startswith("경기도") for r in related] == [True]        # the province's rebuttal, not the budget story
    assert "고비용" in related[0]["summary"]


# ------------------------------------------------------------------ owner controls
def test_a_blocked_topic_is_never_made(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "blocked_topics.json").write_text(json.dumps([["DMZ", "영화제"]]), encoding="utf-8")
    cfg = dataclasses.replace(load_settings(), db_path=tmp_path / "blk.sqlite3", root=tmp_path)
    init_db(cfg.db_path)
    with connect(cfg.db_path) as conn:
        for cid, title in ((1, "경기도, 'DMZ영화제' 축소운영 비판에 반박"), (2, "예산안 국회 통과")):
            url = f"https://example.com/{cid}"
            upsert_article(conn, {"url_hash": url_hash(url), "url": url, "source_name": "s", "source_lean": "wire",
                                  "source_weight": 1.0, "title": title, "summary": "요약", "published_ts": now(),
                                  "collected_ts": now(), "raw": {}})
            conn.execute("UPDATE articles SET cluster_id=? WHERE url_hash=?", (cid, url_hash(url)))
    assert P._drop_blocked(cfg, [1, 2]) == [2]
    (tmp_path / "config" / "blocked_topics.json").write_text("[]", encoding="utf-8")
    assert P._drop_blocked(cfg, [1, 2]) == [1, 2]
    (tmp_path / "config" / "blocked_topics.json").unlink()
    assert P._drop_blocked(cfg, [1, 2]) == [1, 2]                                  # no file = nothing blocked


# ================== round 2: the follow-ups found by reviewing round 1 ==================
def test_incident_a_faithful_paraphrase_is_not_a_missing_quote():
    """Whole-word comparison called '정리한/정리했다', '파기를/파기라고' different words, so a video that DID carry
    the quote could be held as 'missing_quote'."""
    web = {"statements": [{"who": "김민석 대표", "when": "20일",
                           "text": "대통령이 임기 제한 문제를 분명하게 정리했기 때문에 이제 본격적인 개헌 논의로 전환해야 한다"}]}
    narr = [_seg("what", "김민석 대표는 20일 기자회견에서 대통령이 임기 제한 문제를 분명히 정리한 만큼 본격적인 "
                         "개헌 논의로 전환할 때라고 밝혔습니다.")]
    assert invariants.check(narr, web) == []
    unrelated = [_seg("what", "국회는 오늘 예산안을 처리했고 여야는 표결 끝에 합의했습니다.")]
    assert [v["code"] for v in invariants.check(unrelated, web)] == ["missing_quote"]     # a real omission is still caught


def test_incident_the_provinces_reason_in_the_critics_sentence_is_flagged():
    """The published DMZ video: '김민석 대표 측은 재정 부족을 이유로 …계약 파기라고 비판' — 재정 부족 is the PROVINCE's reason."""
    mixed = [_seg("sides", "더불어민주당 김민석 대표 측은 재정 부족을 이유로 문화 기반을 축소하는 것은 계약 파기라고 비판합니다.")]
    codes = [v["code"] for v in invariants.check(mixed, WEB)]
    assert "speaker_mix" in codes
    ok = [_seg("sides", "경기도는 재정 상황과 고비용 저성과를 이유로 축소했다고 설명했습니다. "
                        "반면 김민석 대표는 이를 계약 파기라며 비판했습니다.")]
    assert "speaker_mix" not in [v["code"] for v in invariants.check(ok, WEB)]
    # naming the criticised party's action inside the critic's sentence is normal, not a mix-up
    action = [_seg("sides", "김민석 대표는 경기도가 재정 상황을 이유로 축소한 것을 계약 파기라고 비판했습니다.")]
    assert "speaker_mix" not in [v["code"] for v in invariants.check(action, WEB)]


def test_incident_same_event_needs_a_shared_proper_noun_similar_text_and_a_time_window(tmp_path):
    """'경기도 영화제' (the province) must not merge with '경기 침체' (the economy); a real rebuttal must merge; a story
    from days ago must not."""
    cfg = dataclasses.replace(load_settings(), db_path=tmp_path / "ev.sqlite3", output_dir=tmp_path, data_dir=tmp_path)
    init_db(cfg.db_path)
    t0 = now()
    arts = [
        (1, "김민석, 경기도 DMZ다큐영화제 축소에 계약 파기 비판", "김민석 대표는 경기도의 DMZ다큐영화제 축소를 계약 파기라고 비판했다.", t0),
        (2, "경기도, DMZ영화제 축소운영 비판에 고비용 저성과 반박", "경기도는 DMZ영화제 축소운영이 고비용 저성과 때문이라고 반박했다.", t0 + 3600),
        (3, "경기 침체 속 내년 예산 축소 전망", "올해 경기 침체로 내년 정부 예산이 축소될 전망이다.", t0),
        (4, "경기도, DMZ영화제 축소 논란 지난달에도 있었다", "경기도는 DMZ영화제 예산을 지난달에도 줄였다.", t0 - 5 * 86400),
    ]
    with connect(cfg.db_path) as conn:
        for cid, title, summary, ts in arts:
            url = f"https://example.com/{cid}"
            upsert_article(conn, {"url_hash": url_hash(url), "url": url, "source_name": f"매체{cid}", "source_lean": "wire",
                                  "source_weight": 1.0, "title": title, "summary": summary, "published_ts": ts,
                                  "collected_ts": now(), "raw": {}})
            conn.execute("UPDATE articles SET cluster_id=? WHERE url_hash=?", (cid, url_hash(url)))
        rows = list(conn.execute("SELECT * FROM articles WHERE cluster_id = 1"))
    related = SG._related_reports(1, rows, cfg)
    titles = [r["title"] for r in related]
    assert any("고비용" in t for t in titles)                          # the province's rebuttal joins
    assert not any("경기 침체" in t for t in titles)                    # a different '경기' does not
    assert not any("지난달" in t for t in titles)                       # a 5-day-old story does not
    assert related[0]["cluster_id"] == 2


def test_incident_a_folded_in_story_is_recorded_as_covered_when_the_video_is_published():
    """After the video absorbs the province's rebuttal, that rebuttal must not come back as its own video."""
    from political_shorts import topics
    assert 'script.get("absorbed")' in __import__("inspect").getsource(P._process_story)
    sig = topics.story_signature("경기도, DMZ영화제 축소운영 비판에 고비용 저성과 반박", None, "")
    prev = topics.story_signature("김민석, 경기도 DMZ다큐영화제 축소에 계약 파기 비판", {"politicians": ["김민석"]}, "")
    # the recorded signature of the absorbed headline is what a later, near-identical headline is compared against
    later = topics.story_signature("경기도, DMZ영화제 축소운영 비판에 \"고비용·저성과\" 반박", None, "")
    assert topics._overlap(later, sig) >= 0.6 and topics._overlap(later, prev) < 0.6


def test_incident_an_empty_model_answer_still_yields_a_quote_and_a_cause():
    """Groq's quota gone + Gemini failing left research empty and the video was written from one article."""
    bodies = [{"title": "개헌", "source": "연합뉴스", "link": "https://a/1", "lean": "",
               "text": "더불어민주당 김민석 대표는 20일 국회에서 “대통령이 임기 제한 입장을 분명히 정리한 만큼 본격적인 개헌 논의로 "
                       "전환해야 한다”고 밝혔습니다. 경기도는 재정 부담 때문에 운영 규모를 줄였다고 설명했다."}]
    notes = research._rule_notes(bodies)
    assert notes["rule_based"] and notes["statements"][0]["who"] == "김민석 대표"
    assert "개헌 논의로 전환해야 한다" in notes["statements"][0]["text"]
    assert any("재정 부담 때문에" in w["reason"] for w in notes["why"])
    assert notes["positions"][0]["who"] == "김민석 대표"
    assert research._rule_notes([{"text": "특별한 내용 없는 짧은 문장입니다.", "source": "x"}]) == {}


def test_incident_note_extraction_falls_back_to_the_rules_when_every_model_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("quota")
    monkeypatch.setattr(L, "complete", boom)
    bodies = [{"title": "기사", "source": "매체", "link": "https://a/1", "lean": "left",
               "text": "김민석 대표는 “경기도가 계약을 일방적으로 파기하고 축소하는 것은 문제가 있다”고 비판했습니다. " * 2}]
    notes = research._extract_notes("영화제 축소", bodies, settings, {"event": "영화제 축소", "queries": ["영화제 축소 이유"]})
    assert notes and notes["statements"][0]["lean"] == "진보"


def test_incident_note_prompts_carry_only_the_relevant_sentences():
    """Whole bodies burned the free daily token quota twice as fast."""
    filler = "이 문장은 사건과 아무 관련이 없는 배경 설명입니다. " * 30
    text = filler + "김민석 대표는 “경기도가 계약을 일방적으로 파기하는 것은 문제가 있다”고 밝혔습니다. " + filler
    kws = research._kw("경기도 영화제 계약 파기 김민석")
    picked = research._select_sentences(text, kws, budget=300)
    assert "계약을 일방적으로 파기하는 것은 문제" in picked and len(picked) <= 300


def test_incident_5_18_is_said_as_a_name_not_read_as_two_numbers():
    """'5·18' became '5, 18' (voiced five, eighteen) and quality.py flagged a number the article never had."""
    assert SG._glyph_safe("여당이 5·18 전문 수록을 내걸었다") == "여당이 오일팔 전문 수록을 내걸었다"
    assert SG._glyph_safe("4·19와 6·25") == "사일구와 육이오"
    assert SG._glyph_safe("A·B 협의체") == "A, B 협의체"                 # ordinary middle dots still become ", "
    assert SG._glyph_safe("가격은 2.5·3배") == "가격은 2.5, 3배"           # not a known date: unchanged behaviour


def test_incident_a_render_that_is_out_of_sync_is_re_rendered_once():
    """CI builds sometimes rendered every scene ~1/3 of its voice length (dur/audio 0.3) while the same code was
    fine locally; the video was correctly held, but a good story was thrown away for a transient fault."""
    class QR:
        def __init__(self, issues):
            self.issues = issues

    sync = QR([{"severity": "critical", "code": "sub-sync"}])
    mismatch = QR([{"severity": "critical", "code": "duration-mismatch"}])
    assert P._render_defect(sync) and P._render_defect(mismatch)
    assert not P._render_defect(QR([{"severity": "warn", "code": "sub-sync"}]))              # a warning is not a defect
    assert not P._render_defect(QR([{"severity": "critical", "code": "added-number"}]))       # a script problem needs a rewrite
    assert not P._render_defect(QR([]))
    assert "re-rendering once" in __import__("inspect").getsource(P._process_story)
