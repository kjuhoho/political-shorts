"""What the Korean-politics Shorts that actually travel are doing right now.

    python scripts/market_shorts.py --days 30 --min-views 100000
    python scripts/market_shorts.py --json data/market_snapshot.json

Our own view counts are pinned (see scripts/channel_stats.py: the best video of
2026-09 was 1.32x the median and 83% of them sat within a fifth of it), so the
channel cannot learn anything from itself — a difference between our own buckets
is not a difference, it is the feed handing every upload the same test audience.
This script goes and looks at the videos that DID travel instead: it searches the
YouTube Data API for Korean politics Shorts published in the window, keeps the
ones above `--min-views`, and measures the two things a scroller decides on —
what the first 12 characters of the title are, and how long the video runs.

Read-only. Same plain API key as channel_stats.py; no channel access of any kind.

Baseline taken 2026-09-30 (n=144 Shorts above 100k views in the previous 30 days):
  first 12 chars : 44 open on a politician's name, 36 on a verbatim quote,
                   18 more carry a name inside them -> 98/144 (68%)
  "?" in title   : 35/144 (24%); of the top 30 by views, 4 end on one
  length         : median 60s, mean 69s; 57/144 (40%) run 51-70s
Re-run it before concluding that anything we changed stopped working.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import statistics
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (adds src/ to sys.path)

from political_shorts.config import settings
from political_shorts.hook import POLITICIANS

API = "https://www.googleapis.com/youtube/v3/"
# broad enough to catch both camps and both the wire outlets and the commentary
# channels; narrow enough that the results are still politics
QUERIES = ["이재명", "국민의힘 민주당", "정치 뉴스", "장동혁", "국정감사", "검찰개혁"]
SHORT_MAX_S = 180           # YouTube's own Shorts ceiling since Oct 2024
LEAD = 12                   # "the first 12 characters" — about what a thumb reads
_QUOTE_OPEN = ('"', "“", "'", "‘", "「")
_BRACKET = re.compile(r"^\s*(?:\[[^\]]*\]|\([^)]*\)|[✅❗‼🔥])\s*")
_HASHTAG = re.compile(r"#\S+")


def _api(endpoint: str, key: str, **params: Any) -> dict[str, Any]:
    params["key"] = key
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.loads(r.read())


def iso_seconds(iso: str) -> int:
    """'PT1M23S' -> 83."""
    total, num = 0, ""
    for ch in iso.replace("PT", ""):
        if ch.isdigit():
            num += ch
        else:
            total += int(num or 0) * {"H": 3600, "M": 60, "S": 1}.get(ch, 0)
            num = ""
    return total


def lead_shape(title: str) -> str:
    """What the first 12 characters of the title actually are.

    'quote' a verbatim line in quotation marks, 'name' a politician (or the
    president) named there, 'other' an abstract phrase. The whole finding this
    script exists for: 98 of 144 travelling Shorts were 'quote' or 'name', while
    32 of our own 48 opened on a contentless stub ("무슨 일일까요?").
    """
    # NFKC first: Korean outlets write 李 as U+F9E1 (the compatibility ideograph),
    # not U+674E, and "李, 농지조사에 담긴 충격적 의미" (1.8M views) then reads as
    # an abstract phrase instead of a named president
    t = unicodedata.normalize("NFKC", title)
    t = _BRACKET.sub("", _HASHTAG.sub("", t)).strip()
    head = t[:LEAD + 1]
    if head[:1] in _QUOTE_OPEN:
        return "quote"
    if any(n in head for n in POLITICIANS) or any(
            w in head for w in ("대통령", "청와대", "대통령실", "李")):
        return "name"
    return "other"


def length_bucket(sec: int) -> str:
    for lo, hi in ((0, 35), (36, 50), (51, 70), (71, 90), (91, SHORT_MAX_S)):
        if lo <= sec <= hi:
            return f"{lo}-{hi}s"
    return ">%ds" % SHORT_MAX_S


def collect(days: int, min_views: int, key: str) -> list[dict[str, Any]]:
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids: set[str] = set()
    for q in QUERIES:
        with contextlib.suppress(Exception):
            hits = _api("search", key, part="snippet", q=q, type="video", videoDuration="short",
                        order="viewCount", regionCode="KR", relevanceLanguage="ko",
                        maxResults=25, publishedAfter=after)
            ids |= {it["id"]["videoId"] for it in hits.get("items", [])}
    rows: list[dict[str, Any]] = []
    all_ids = sorted(ids)
    for i in range(0, len(all_ids), 50):
        data = _api("videos", key, part="snippet,statistics,contentDetails",
                    id=",".join(all_ids[i:i + 50]))
        for it in data.get("items", []):
            sec = iso_seconds(it.get("contentDetails", {}).get("duration", ""))
            views = int(it.get("statistics", {}).get("viewCount", 0) or 0)
            if sec > SHORT_MAX_S or views < min_views:
                continue
            rows.append({"video_id": it["id"], "channel": it["snippet"].get("channelTitle", ""),
                         "title": it["snippet"].get("title", ""), "seconds": sec, "views": views,
                         "likes": int(it.get("statistics", {}).get("likeCount", 0) or 0),
                         "comments": int(it.get("statistics", {}).get("commentCount", 0) or 0),
                         "lead_shape": lead_shape(it["snippet"].get("title", "")),
                         "has_question_mark": "?" in it["snippet"].get("title", "")})
    return sorted(rows, key=lambda r: -r["views"])


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "note": "search returned nothing — quota, or the queries went stale"}
    n = len(rows)
    shapes = {s: sum(1 for r in rows if r["lead_shape"] == s) for s in ("name", "quote", "other")}
    lens: dict[str, int] = {}
    for r in rows:
        lens[length_bucket(r["seconds"])] = lens.get(length_bucket(r["seconds"]), 0) + 1
    return {
        "n": n,
        "lead_shape": shapes,
        "named_or_quoted_share": round((shapes["name"] + shapes["quote"]) / n, 2),
        "question_mark_share": round(sum(1 for r in rows if r["has_question_mark"]) / n, 2),
        "median_seconds": statistics.median(r["seconds"] for r in rows),
        "mean_seconds": round(statistics.fmean(r["seconds"] for r in rows)),
        "by_length": dict(sorted(lens.items(), key=lambda kv: -kv[1])),
        "median_like_rate": round(statistics.median(
            r["likes"] / max(1, r["views"]) for r in rows), 4),
    }


def main() -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-views", type=int, default=100_000)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--show", type=int, default=20)
    args = ap.parse_args()
    key = (settings.youtube_api_key or "").strip()
    if not key:
        print("YOUTUBE_API_KEY is not set — market read unavailable")
        return 1
    rows = collect(args.days, args.min_views, key)
    rep = {"generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
           "days": args.days, "min_views": args.min_views,
           "summary": summarise(rows), "videos": rows}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    s = rep["summary"]
    print(f"{s['n']} KR politics Shorts above {args.min_views} views in {args.days} days")
    print(f"first {LEAD} chars: " + ", ".join(f"{k}={v}" for k, v in s["lead_shape"].items())
          + f"  -> named/quoted {int(s['named_or_quoted_share'] * 100)}%")
    print(f"title has '?': {int(s['question_mark_share'] * 100)}%   "
          f"length: median {s['median_seconds']}s, mean {s['mean_seconds']}s   "
          f"like rate {s['median_like_rate']}")
    print("by length: " + " | ".join(f"{k} {v}" for k, v in s["by_length"].items()))
    for r in rows[:args.show]:
        print(f"{r['views']:>9} {r['seconds']:>4}s [{r['lead_shape']:<5}] "
              f"[{r['channel'][:14]}] {r['title'][:56]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
