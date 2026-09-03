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
