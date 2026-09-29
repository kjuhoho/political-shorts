"""What the channel's published videos actually did — public numbers joined to what we chose when we made them.

    python scripts/channel_stats.py                 # last 30 days, table + aggregates
    python scripts/channel_stats.py --days 90 --json data/channel_stats.json

Reads the ids of everything the pipeline published from data/topic_history.json, asks the YouTube Data API for
each video's title, length and public counts (views / likes / comments — the only numbers a plain API key can
see; watch time and click-through need an OAuth Analytics scope we do not have), and joins them to the frame,
actor and slot the pipeline chose. Read-only: it never touches the channel.

The aggregates are deliberately blunt: with a handful of videos per bucket, a difference is noise. Every bucket
carries its n, and `weak` marks the ones too small to draw anything from.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (adds src/ to sys.path)

from political_shorts.config import settings

KST = timezone(timedelta(hours=9))
API = "https://www.googleapis.com/youtube/v3/videos"
MIN_BUCKET = 4          # fewer videos than this in a bucket: report it, never conclude from it


def fetch_videos(ids: list[str], key: str) -> dict[str, dict[str, Any]]:
    """{video_id: {title, published_at, seconds, views, likes, comments}} for the ids the API knows."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), 50):
        chunk = [v for v in ids[i:i + 50] if v and not v.startswith("BLOCKED")]
        if not chunk:
            continue
        url = f"{API}?" + urllib.parse.urlencode(
            {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk), "key": key})
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
        for item in data.get("items", []):
            st, sn = item.get("statistics", {}), item.get("snippet", {})
            out[item["id"]] = {
                "title": sn.get("title", ""),
                "published_at": sn.get("publishedAt", ""),
                "seconds": _iso_seconds(item.get("contentDetails", {}).get("duration", "")),
                "views": int(st.get("viewCount", 0) or 0),
                "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
            }
    return out


def _iso_seconds(iso: str) -> int:
    """'PT1M23S' -> 83."""
    total, num = 0, ""
    for ch in iso.replace("PT", ""):
        if ch.isdigit():
            num += ch
        else:
            total += int(num or 0) * {"H": 3600, "M": 60, "S": 1}.get(ch, 0)
            num = ""
    return total


def our_videos(history: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    """One row per published video (several stories can share one), newest first."""
    cutoff = (datetime.now(KST) - timedelta(days=days)).timestamp()
    rows: dict[str, dict[str, Any]] = {}
    for x in history:
        vid, ts = str(x.get("remote_id") or ""), float(x.get("published_ts") or 0)
        if not vid or ts < cutoff:
            continue
        row = rows.setdefault(vid, {"video_id": vid, "published_ts": ts, "headlines": [],
                                    "frame": x.get("frame", ""), "actor": x.get("actor", "")})
        row["headlines"].append(x.get("headline", ""))
        row["published_ts"] = min(row["published_ts"], ts)
    for row in rows.values():
        t = datetime.fromtimestamp(row["published_ts"], KST)
        row["kst"] = t.strftime("%m-%d %H:%M")
        row["slot"] = "morning" if 4 <= t.hour < 14 else "evening"
        row["weekday"] = "월화수목금토일"[t.weekday()]
        row["absorbed"] = len(row["headlines"])
    return sorted(rows.values(), key=lambda r: r["published_ts"], reverse=True)


def _bucket(rows: list[dict[str, Any]], key) -> list[dict[str, Any]]:
    groups: dict[Any, list[int]] = defaultdict(list)
    for r in rows:
        if r.get("views") is not None:
            groups[key(r)].append(r["views"])
    out = [{"bucket": str(k), "n": len(v), "median_views": round(statistics.median(v), 1),
            "mean_views": round(statistics.fmean(v), 1), "weak": len(v) < MIN_BUCKET}
           for k, v in groups.items()]
    return sorted(out, key=lambda b: b["median_views"], reverse=True)


def title_shape(title: str) -> str:
    t = title.replace("#shorts", "").strip()
    if "?" in t:
        return "question"
    if any(ch.isdigit() for ch in t):
        return "number"
    return "statement"


def report(days: int) -> dict[str, Any]:
    key = (settings.youtube_api_key or "").strip()
    history = json.loads((settings.data_dir / "topic_history.json").read_text(encoding="utf-8"))
    rows = our_videos(history, days)
    if not key:
        return {"error": "YOUTUBE_API_KEY is not set — public counts unavailable", "videos": rows}
    stats = fetch_videos([r["video_id"] for r in rows], key)
    for r in rows:
        r.update(stats.get(r["video_id"], {}))
        r["title_shape"] = title_shape(r.get("title", ""))
        r["engagement"] = round((r.get("likes", 0) + r.get("comments", 0)) / max(1, r.get("views", 0)), 4)
    live = [r for r in rows if r.get("views") is not None and r.get("title")]
    return {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "days": days,
        "videos": rows,
        "missing_from_api": [r["video_id"] for r in rows if not r.get("title")],   # private / deleted
        "totals": {"videos": len(live), "views": sum(r.get("views", 0) for r in live),
                   "median_views": round(statistics.median([r["views"] for r in live]), 1) if live else 0},
        "by_frame": _bucket(live, lambda r: r.get("frame") or "(none)"),
        "by_slot": _bucket(live, lambda r: r["slot"]),
        "by_weekday": _bucket(live, lambda r: r["weekday"]),
        "by_title_shape": _bucket(live, lambda r: r["title_shape"]),
        "by_length": _bucket(live, lambda r: "≤40s" if r.get("seconds", 0) <= 40 else
                             ("41-60s" if r["seconds"] <= 60 else ">60s")),
        "top": sorted(live, key=lambda r: r.get("views", 0), reverse=True)[:5],
        "bottom": sorted(live, key=lambda r: r.get("views", 0))[:5],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    rep = report(args.days)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    if rep.get("error"):
        print(rep["error"])
        return 1
    t = rep["totals"]
    print(f"{t['videos']} videos, {t['views']} views, median {t['median_views']} (last {args.days} days)")
    print(f"{'date':<12}{'views':>7}{'likes':>7}{'cmts':>6}{'len':>5}  {'frame':<10}{'slot':<9}title")
    for r in rep["videos"]:
        if not r.get("title"):
            continue
        print(f"{r['kst']:<12}{r['views']:>7}{r['likes']:>7}{r['comments']:>6}{r['seconds']:>5}  "
              f"{(r.get('frame') or '-'):<10}{r['slot']:<9}{r['title'][:52]}")
    for name in ("by_frame", "by_slot", "by_weekday", "by_title_shape", "by_length"):
        parts = [f"{b['bucket']} n={b['n']} med={b['median_views']}{' (weak)' if b['weak'] else ''}"
                 for b in rep[name]]
        print(f"\n{name}: " + " | ".join(parts))
    if rep["missing_from_api"]:
        print("\nnot public / not found: " + ", ".join(rep["missing_from_api"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
