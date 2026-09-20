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
