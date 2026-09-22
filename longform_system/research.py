"""Dated, full-text evidence collection; never imports the shorts runtime."""
import calendar
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import trafilatura
import yaml

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo('Asia/Seoul')


def now():
    return datetime.now(KST)


def blocked(text):
    # Shared policy DATA is read-only. Missing/invalid policy fails closed.
    groups = json.loads((ROOT / 'config/blocked_topics.json').read_text(encoding='utf-8'))
    return any(all(term.casefold() in text.casefold() for term in group) for group in groups)


def fetch(url):
    response = requests.get(url, timeout=18, headers={'User-Agent': 'Mozilla/5.0'})
    response.raise_for_status()
    if len(response.content) > 4_000_000:
        raise ValueError('oversized source')
    return response.content


def collect():
    cutoff = now() - timedelta(days=7)
    feeds = yaml.safe_load((ROOT / 'config/sources.yaml').read_text(encoding='utf-8'))['feeds']

    def feed_rows(feed):
        try:
            parsed = feedparser.parse(fetch(feed['url']))
        except Exception:
            return []
        rows = []
        for entry in parsed.entries[:18]:
            date = entry.get('published_parsed') or entry.get('updated_parsed')
            if not date:
                continue
            published = datetime.fromtimestamp(calendar.timegm(date), KST)
            title, url = entry.get('title', ''), entry.get('link', '')
            if not cutoff <= published <= now() + timedelta(minutes=10) or not url or blocked(title):
                continue
            rows.append(dict(name=feed['name'], title=title, url=url, lean=feed.get('lean'),
                             published=published.isoformat(), weight=feed.get('weight', .5)))
        return rows

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = [r for group in pool.map(feed_rows, feeds) for r in group]
    rows = list({r['url']: r for r in rows}.values())
    rows.sort(key=lambda r: (r['published'][:10], r['weight']), reverse=True)

    def full_text(row):
        try:
            body = trafilatura.extract(fetch(row['url']), include_comments=False) or ''
            if len(body) < 400 or blocked(body):
                return None
            return {**row, 'body': body[:18000]}
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        articles = [r for r in pool.map(full_text, rows[:54]) if r]
    if len(articles) < 3:
        raise RuntimeError('Recent dated full-text evidence unavailable')
    return articles


def select(articles):
    selected, used = [], set()
    stop = {'대통령', '정부', '국회', '오늘', '관련', '대한', '위해', '정치'}
    def tokens(row):
        return set(re.findall(r'[가-힣A-Za-z]{2,}', row['title'])) - stop
    def importance(row):
        coverage = len({a['name'] for a in articles if len(tokens(row) & tokens(a)) >= 2})
        age = max(0, (now()-datetime.fromisoformat(row['published'])).total_seconds()/3600)
        public_interest = sum(term in row['title'] for term in ('예산','법안','본회의','금리','물가','선거','국정','안보','외교','회담','정책'))
        return coverage*5 + public_interest*2 + float(row['weight'])*3 - age/6
    for lead in sorted(articles, key=importance, reverse=True):
        terms = tokens(lead)
        if lead['url'] in used or any(len(terms & tokens(s['sources'][0])) >= 2 for s in selected):
            continue
        related = sorted((a for a in articles if a['url'] != lead['url'] and a['name'] != lead['name']),
                         key=lambda a: len(terms & tokens(a)), reverse=True)
        related = [a for a in related if len(terms & tokens(a)) >= 2][:1]
        sources = [lead, *related]
        selected.append({'headline': lead['title'], 'sources': sources, 'selection_score': round(importance(lead),2)})
        used.update(a['url'] for a in sources)
        if len(selected) == 3:
            return selected
    raise RuntimeError('Three distinct current issues unavailable')


def evidence(story):
    return '\n\n'.join(f"출처 {s['name']} | 발행 {s['published']} | {s['url']}\n{s['title']}\n{s['body'][:2000]}"
                       for s in story['sources'])
