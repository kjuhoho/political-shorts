"""Step 1 — collect news items from the configured RSS feeds."""
from __future__ import annotations

import calendar
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests

from .config import Feed, Settings, settings
from .db import connect, init_db, now, upsert_article
from .logging_setup import get_logger
from .textutil import canonical_url, clean_text, strip_byline, truncate, url_hash

log = get_logger("collect")

# Several outlets 403 a non-browser UA on their public RSS, so present a common
# desktop-browser UA. We only ever GET public feed URLs.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15


@dataclass
class CollectResult:
    fetched_feeds: int = 0
    failed_feeds: int = 0
    total_entries: int = 0
    inserted: int = 0
    errors: list[str] = field(default_factory=list)


def _entry_timestamp(entry: Any) -> int | None:
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                return calendar.timegm(val)  # struct_time is UTC
            except (TypeError, ValueError):
                pass
    return None


def _fetch_feed(feed: Feed) -> list[dict[str, Any]]:
    """Fetch one feed with requests (so we control TLS + UA), parse with feedparser."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    resp = requests.get(feed.url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    out: list[dict[str, Any]] = []
    for entry in parsed.entries:
        link = canonical_url(entry.get("link", ""))
        title = clean_text(entry.get("title", ""))
        if not link or not title:
            continue
        summary = truncate(
            strip_byline(entry.get("summary", "") or entry.get("description", "")), 600
        )
        out.append(
            {
                "url": link,
                "url_hash": url_hash(link),
                "source_name": feed.name,
                "source_lean": feed.lean,
                "source_weight": feed.weight,
                "title": title,
                "summary": summary,
                "published_ts": _entry_timestamp(entry),
                "collected_ts": now(),
                "raw": {
                    "id": entry.get("id", ""),
                    "author": entry.get("author", ""),
                    "tags": [t.get("term", "") for t in entry.get("tags", []) if t.get("term")],
                },
            }
        )
    return out


def collect(cfg: Settings | None = None) -> CollectResult:
    cfg = cfg or settings
    init_db(cfg.db_path)
    result = CollectResult()
    if not cfg.feeds:
        log.warning("no feeds configured in config/sources.yaml")
        return result

    cutoff = now() - cfg.collect_window_hours * 3600

    with connect(cfg.db_path) as conn:
        for feed in list(cfg.feeds) + list(cfg.fallback_feeds):
            try:
                entries = _fetch_feed(feed)
            except Exception as exc:  # network / parse errors are non-fatal
                result.failed_feeds += 1
                msg = f"{feed.name}: {type(exc).__name__}: {exc}"
                result.errors.append(msg)
                log.warning("feed failed  %s", msg)
                continue

            result.fetched_feeds += 1
            result.total_entries += len(entries)
            for art in entries:
                ts = art["published_ts"]
                if ts is not None and ts < cutoff:
                    continue
                if upsert_article(conn, art):
                    result.inserted += 1
            log.info("feed ok      %-16s entries=%d", feed.name, len(entries))

    log.info(
        "collect done feeds=%d/%d entries=%d inserted=%d",
        result.fetched_feeds,
        result.fetched_feeds + result.failed_feeds,
        result.total_entries,
        result.inserted,
    )
    return result


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    r = collect()
    print(
        f"feeds={r.fetched_feeds} failed={r.failed_feeds} "
        f"entries={r.total_entries} inserted={r.inserted}"
    )
    for e in r.errors:
        print("  !", e)
