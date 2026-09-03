"""Pick the story that's actually HOT right now, not just the most-reported one.

Free signal, no API key: Google Trends "trending now" RSS for Korea
(``https://trends.google.com/trending/rss?geo=KR``) — the live list of search
terms spiking in the last few hours, each with an approximate search volume and
a few related news headlines. We boost a story cluster when its people /
keywords intersect that list, then re-rank the build order so the freshest,
most-searched issue is made first.

Best-effort throughout: any network / parse failure just leaves the original
newsworthiness order (cluster size + lean spread) untouched.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

from .config import Settings, settings
from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("trending")

_RSS = "https://trends.google.com/trending/rss?geo=KR"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) political-shorts/1.0"
_STOP = {
    "대통령", "국회", "의원", "장관", "정부", "여야", "정치", "논란", "의혹",
    "속보", "단독", "종합", "오늘", "기자", "뉴스", "사진", "영상", "관련",
}


def _traffic_int(s: str) -> int:
    m = re.search(r"([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else 0


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.split(r"[^0-9A-Za-z가-힣]+", clean_text(text).lower())
        if len(t) >= 2 and t not in _STOP and not t.isdigit()
    }


def _parse_rss(xml: str) -> list[dict]:
    out: list[dict] = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
        tm = re.search(r"<title>(.*?)</title>", block, re.S)
        if not tm:
            continue
        term = clean_text(tm.group(1))
        traf = re.search(r"approx_traffic>\s*(.*?)\s*<", block)
        traffic = _traffic_int(traf.group(1) if traf else "")
        news = [clean_text(x) for x in re.findall(r"<ht:news_item_title>(.*?)</ht:news_item_title>", block, re.S)]
        if term:
            out.append({"term": term, "traffic": traffic, "news": news})
    return out


def google_trending_kr(cfg: Settings | None = None, ttl_min: int = 60) -> list[dict]:
    """[{term, traffic, news:[...]}] — cached to a file for ``ttl_min`` minutes."""
    cfg = cfg or settings
    cache = Path(cfg.image_cache_dir or "assets/cache/images").parent / "trending_kr.json"
    try:
        if cache.exists() and (time.time() - cache.stat().st_mtime) < ttl_min * 60:
            return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        r = requests.get(_RSS, headers={"User-Agent": _UA}, timeout=12)
        r.raise_for_status()
        items = _parse_rss(r.text)
        if items:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        log.info("google trending KR: %d terms (top: %s)",
                 len(items), ", ".join(i["term"] for i in items[:5]))
        return items
    except Exception as exc:
        log.warning("trending fetch failed (%s) — ranking by newsworthiness only", exc)
        try:
            return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else []
        except Exception:
            return []


_TREND_MIN = 1.5   # below this a cluster is treated as "not trending" (no reorder)


def cluster_trend_score(texts: list[str], trending: list[dict]) -> tuple[float, str]:
    """(score, matched-term). Deliberately STRICT — Korea's trending-now list is
    mostly entertainment/sport, so a loose token overlap (샤인머스캣 vs a policy
    story) must score ~0. A real hit needs the whole trend phrase to appear in
    the cluster text, or a shared word of 3+ characters."""
    if not trending:
        return 0.0, ""
    import math

    joined = clean_text(" ".join(texts))
    bag = _tokens(joined)
    best, hit = 0.0, ""
    for it in trending:
        term = it["term"].strip()
        term_toks = _tokens(term)
        # a genuine match: the full phrase is present, OR a 3+char word is shared
        phrase_hit = len(term) >= 3 and term in joined
        strong_common = {t for t in (bag & term_toks) if len(t) >= 3}
        # also allow: a 3+char word shared with the trend's related news headlines
        news_toks = _tokens(" ".join(it.get("news", [])[:4]))
        news_common = {t for t in (bag & news_toks & term_toks) if len(t) >= 3}
        if not (phrase_hit or strong_common or news_common):
            continue
        cov = len(bag & term_toks) / max(1, len(term_toks))
        vol = math.log10(max(10, it.get("traffic", 0) or 10))        # 1..~4
        score = (2.0 if phrase_hit else 1.2 * cov + 0.4 * len(strong_common | news_common))
        score *= 0.7 + 0.3 * vol / 4
        if score > best:
            best, hit = score, term
    return round(best, 2), hit


def rerank_by_trend(cluster_ids: list[int], cfg: Settings | None = None) -> list[int]:
    """Stable re-sort of the build order: hottest (by Google Trends overlap)
    first, ties keep the original newsworthiness order. No-op on any failure."""
    cfg = cfg or settings
    if not cluster_ids or not getattr(cfg, "trending_enabled", True):
        return cluster_ids
    trending = google_trending_kr(cfg)
    if not trending:
        return cluster_ids
    try:
        from .db import cluster_articles, connect

        scored: list[tuple[float, int, int]] = []
        any_hot = False
        with connect(cfg.db_path) as conn:
            for i, cid in enumerate(cluster_ids):
                rows = cluster_articles(conn, cid)
                texts = [r["title"] for r in rows] + [(r["summary"] or "")[:200] for r in rows[:3]]
                s, term = cluster_trend_score(texts, trending)
                if s < _TREND_MIN:              # weak/noise match -> not trending
                    s = 0.0
                else:
                    any_hot = True
                    log.info("cluster %d rides trend '%s' (score %.1f): %s",
                             cid, term, s, (rows[0]["title"][:50] if rows else ""))
                scored.append((s, i, cid))
        if not any_hot:
            log.info("no cluster matches a current KR trend — keeping newsworthiness order")
            return cluster_ids
        scored.sort(key=lambda t: (-t[0], t[1]))     # hot first, ties keep order
        ordered = [cid for _, _, cid in scored]
        if ordered != cluster_ids:
            log.info("build order re-ranked by trend: %s -> %s",
                     cluster_ids[:5], ordered[:5])
        return ordered
    except Exception as exc:
        log.warning("trend re-rank failed (%s) — keeping original order", exc)
        return cluster_ids
