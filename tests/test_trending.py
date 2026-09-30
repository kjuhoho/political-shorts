"""Trend re-ranking must be conservative: only reorder on a real match."""
from political_shorts.trending import cluster_trend_score, rerank_by_trend


TRENDS = [
    {"term": "샤인 머스 캣", "traffic": 2000, "news": ["샤인머스캣 가격 폭락 원인은"]},
    {"term": "정청래 윤리감찰단", "traffic": 500,
     "news": ["'정청래 세력이 작업' 문자 파문 확산"]},
    {"term": "xbox", "traffic": 500, "news": []},
]


def test_noise_match_scores_zero():
    # a policy story shares only the weak token '가격'/2-char noise with 샤인머스캣
    s, _ = cluster_trend_score(
        ["패스트트랙 심사 기간 330일에서 90일로 단축", "국무회의 의결"], TRENDS)
    assert s < 1.5


def test_real_match_scores_high():
    s, term = cluster_trend_score(
        ["김민석, 정청래 윤리감찰단 부단장 강민구 해임 지시",
         "'정청래 세력이 작업' 문자 파문"], TRENDS)
    assert s >= 1.5 and "정청래" in term


def test_rerank_noop_without_signal(monkeypatch):
    import political_shorts.trending as tr
    monkeypatch.setattr(tr, "google_trending_kr", lambda *a, **k: TRENDS)
    # cluster_articles will fail (no such ids in a fresh test DB) -> graceful no-op
    ids = [9001, 9002, 9003]
    assert rerank_by_trend(ids) == ids


def test_rerank_disabled(monkeypatch):
    import dataclasses

    from political_shorts.config import settings
    off = dataclasses.replace(settings, trending_enabled=False)
    assert rerank_by_trend([1, 2, 3], off) == [1, 2, 3]


# --------------------------------------------------------------------------- #
# "명절 연휴 절도 기승" (262 views) must not be built before a story with a face
# --------------------------------------------------------------------------- #
def test_actor_score_separates_a_named_person_from_a_faceless_procedure():
    from political_shorts.trending import actor_score

    # the five worst live videos of 2026-09 were all faceless procedure lines
    assert actor_score("국회 신속처리안건(패스트트랙) 심사기간 330일→90일 단축") == 0.0
    assert actor_score("당정 '사법통제부' 명칭, '불송치 사건 심사부'로 변경") == 0.0
    assert actor_score("명절 연휴 절도 '기승'...작년 설 연휴 1천건 넘었다") == 0.0
    # …while the ones that held the channel's normal floor named someone
    assert actor_score('장동혁 "한동훈 복당, 당게 해결 때까지 언급 부적절"') == 1.0
    assert actor_score("이 대통령, 강훈식 사의로 청와대 참모진 개편 본격화") == 1.0   # 청와대/대통령
    assert actor_score("국민의힘, 예산안 단독 처리 규탄") == 0.4                     # party only
    assert actor_score("") == 0.0


def test_faceless_story_is_built_after_one_with_a_named_person(monkeypatch):
    """Channel numbers 2026-09 (48 public Shorts): of the 15 whose lead headline
    named nobody, 5 (33%) fell below the 950-view floor; of the 33 that named
    someone, 1 (3%) did (Fisher p=0.009). The faceless one still gets built —
    it just goes last."""
    import contextlib

    import political_shorts.db as db
    import political_shorts.trending as tr

    heads = {11: "국회 신속처리안건 심사기간 330일→90일 단축",      # no face
             12: "여당 '토허구역 실거주 의무 유예, 내년까지 연장을'",  # no face
             13: '오세훈 "집·땅 가진 국민, 싸워야 할 상대 아니다"'}   # 오세훈
    monkeypatch.setattr(tr, "google_trending_kr", lambda *a, **k: TRENDS)
    monkeypatch.setattr(db, "connect", lambda *a, **k: contextlib.nullcontext(None))
    monkeypatch.setattr(db, "cluster_articles",
                        lambda conn, cid: [{"title": heads[cid], "summary": ""}])
    assert tr.rerank_by_trend([11, 12, 13]) == [13, 11, 12]


def test_named_person_never_outranks_a_real_trend_match(monkeypatch):
    """The trend signal still wins — the face is only a tie-breaker."""
    import contextlib

    import political_shorts.db as db
    import political_shorts.trending as tr

    heads = {21: "김민석, 정청래 윤리감찰단 부단장 해임 지시",   # rides the 정청래 trend
             22: '오세훈 "집·땅 가진 국민, 싸워야 할 상대 아니다"'}
    monkeypatch.setattr(tr, "google_trending_kr", lambda *a, **k: TRENDS)
    monkeypatch.setattr(db, "connect", lambda *a, **k: contextlib.nullcontext(None))
    monkeypatch.setattr(db, "cluster_articles",
                        lambda conn, cid: [{"title": heads[cid], "summary": ""}])
    assert tr.rerank_by_trend([22, 21]) == [21, 22]
