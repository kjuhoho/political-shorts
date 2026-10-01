"""Bounded overseas evidence discovery, independent of the shorts configuration.

Topic matches are cross-check candidates, never proof of independent agreement.
Original English text is passed to the existing writer/reviewer without a new
translation API. Google News RSS is discovery-only, not quoted as evidence.
"""
import calendar
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import feedparser
import requests
import trafilatura

from .media_collect import Fetcher, failure_reason

CONCEPTS = {
    'entity:korea': ('South Korea','대한민국','한국'),
    'entity:northkorea': ('North Korea','북한'),
    'entity:un': ('United Nations','유엔'),
    'entity:unga': ('General Assembly','유엔총회'),
    'entity:australia': ('Australia','호주'),
    'entity:usa': ('United States','미국'),
    'entity:japan': ('Japan','일본'),
    'entity:china': ('China','중국'),
    'entity:ukraine': ('Ukraine','우크라이나'),
    'entity:russia': ('Russia','러시아'),
    'entity:iran': ('Iran','이란'),
    'entity:israel': ('Israel','이스라엘'),
    'entity:gaza': ('Gaza','가자지구'),
    'entity:uk': ('United Kingdom','영국'),
    'place:hormuz': ('Hormuz','호르무즈'),
    'topic:supply': ('supply chain','공급망'),
    'topic:nuclear': ('nuclear','핵무기','비핵화','핵실험'),
    'topic:sanctions': ('sanctions','제재'),
    'topic:tariffs': ('tariff','tariffs','관세'),
    'topic:ceasefire': ('ceasefire','휴전'),
    'topic:missile': ('missile','미사일'),
    'topic:trade': ('trade','무역'),
    'topic:energy': ('energy','에너지'),
    'topic:summit': ('summit','정상회담'),
    'topic:aid': ('humanitarian','인도적','인도주의'),
}
FEEDS = (
    dict(name='BBC World',kind='foreign_press',url='https://feeds.bbci.co.uk/news/world/rss.xml',
         hosts=('www.bbc.com','www.bbc.co.uk','bbc.com','bbc.co.uk','feeds.bbci.co.uk')),
    dict(name='UN News',kind='official_international',url='https://news.un.org/feed/subscribe/en/news/all/rss.xml',hosts=('news.un.org',)),
    dict(name='UK FCDO',kind='official_government',url='https://www.gov.uk/government/organisations/foreign-commonwealth-development-office.atom',hosts=('www.gov.uk',)),
    dict(name='UK Parliament Lords Library',kind='official_parliament',url='https://lordslibrary.parliament.uk/briefing-type/lords-research-briefing/feed',hosts=('lordslibrary.parliament.uk',)),
)


def concepts(text):
    found = set()
    for key,aliases in CONCEPTS.items():
        for alias in aliases:
            pattern = re.escape(alias)
            if alias.isascii():
                pattern = r'\b'+pattern+r'\b'
            if re.search(pattern,text,re.I):
                found.add(key)
                break
    # United Nations Command is NOT the United Nations Secretariat/Assembly.
    if '유엔사' in text or 'United Nations Command' in text:
        found.discard('entity:un')
    return found


def cross_match(*texts):
    shared = set.intersection(*(concepts(t) for t in texts))
    return (len(shared) >= 3 and any(k.startswith('entity:') for k in shared)
            and any(k.startswith(('topic:','place:')) for k in shared))


def english_query(text):
    found = concepts(text)
    keys = sorted(k for k in found if k.startswith('entity:'))[:2]
    keys += sorted(k for k in found if k.startswith(('topic:','place:')))[:2]
    return ' '.join('"'+CONCEPTS[k][0]+'"' for k in keys)


def event_review_valid(source, primary):
    check = source.get('event_review',{})
    return (check.get('same_event') is True and check.get('url') == source['url']
            and all(isinstance(check.get(key),str) and len(check[key]) >= 20 and check[key] in original
                    for key,original in (('source_quote',source['body']),('primary_quote',primary['body']))))


def record_event_reviews(story, report):
    checks = report.get('source_checks',[])
    checks = checks if isinstance(checks,list) else []
    for source in story['sources'][1:]:
        source.pop('event_review',None)
        if source.get('language') != 'en':
            continue
        for check in checks:
            if isinstance(check,dict) and check.get('url') == source['url']:
                candidate = {**source,'event_review':check}
                if event_review_valid(candidate,story['sources'][0]):
                    source['event_review'] = check
                    break


def enrich(stories, output, fetcher=None, current=None):
    from .research import blocked
    def excluded(text):
        normalized = re.sub(r'(?i)DMZ\s+(?:international\s+)?(?:documentary\s+)?film\s+festival',
                            'DMZ 영화제',text)
        return blocked(normalized)
    current = current or datetime.now(timezone.utc)
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    key = hashlib.sha256(json.dumps([1,current.date().isoformat(),stories],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    cache = output/'international-research.json'
    if cache.exists():
        old = json.loads(cache.read_text(encoding='utf-8'))
        if old.get('key') == key and old.get('status') == 'complete':
            return old['stories']
    result = copy.deepcopy(stories)
    audit = dict(key=key,status='started',feeds=[],candidates=[],google_discovery=[],stories=result)
    fetcher = fetcher or Fetcher()
    rows = []
    for feed in FEEDS:
        try:
            parsed = feedparser.parse(fetcher.get(feed['url'],1_500_000,redirect_hosts=feed['hosts']))
            audit['feeds'].append(dict(name=feed['name'],entries=len(parsed.entries)))
            for entry in parsed.entries[:30]:
                stamp = entry.get('published_parsed') or entry.get('updated_parsed')
                if not stamp:
                    continue
                when = datetime.fromtimestamp(calendar.timegm(stamp),timezone.utc)
                url = entry.get('link','').replace('http://','https://',1)
                # UN feeds link through a redirect wrapper; use the published GUID.
                guid = entry.get('id','')
                if guid.startswith('https://news.un.org/en/story/'):
                    url = guid
                title = entry.get('title','')
                if not current-timedelta(days=7) <= when <= current+timedelta(minutes=10):
                    continue
                if urlsplit(url).hostname not in feed['hosts'] or excluded(title):
                    continue
                rows.append(dict(name=feed['name'],kind=feed['kind'],language='en',
                    url=url,title=title,published=when.isoformat(),body='',hosts=feed['hosts']))
        except (ValueError,OSError,requests.RequestException) as exc:
            audit['feeds'].append(dict(name=feed['name'],error=failure_reason(exc)))
    fetched = {}
    for story in result[:3]:
        lead = story['sources'][0]
        context = lead['title']+' '+lead['body'][:1200]
        query = english_query(context)
        # Google headlines never become source bodies or licence permission.
        if query:
            try:
                search_url = 'https://news.google.com/rss/search?'+urlencode(dict(q=query+' when:7d',hl='en-US',gl='US',ceid='US:en'))
                parsed = feedparser.parse(fetcher.get(search_url,1_000_000))
                audit['google_discovery'].append(dict(query=query,results=[dict(title=e.get('title',''),url=e.get('link',''),
                    status='discovery_only_original_not_verified') for e in parsed.entries[:5]]))
            except (ValueError,OSError,requests.RequestException) as exc:
                audit['google_discovery'].append(dict(query=query,error=failure_reason(exc)))
        candidates = []
        for row in rows:
            gap = abs((datetime.fromisoformat(row['published'])-datetime.fromisoformat(lead['published'])).total_seconds())
            if gap <= 2*86400 and cross_match(context,row['title']):
                candidates.append(row)
        # At most one official and one press item; neither implies neutrality.
        used_kinds, added = set(),0
        for row in candidates[:4]:
            group = 'press' if row['kind']=='foreign_press' else 'official'
            if group in used_kinds or added >= 2:
                continue
            record = dict(url=row['url'],story=story['headline'],status='held')
            audit['candidates'].append(record)
            try:
                if row['url'] not in fetched:
                    raw = fetcher.get(row['url'],2_000_000,redirect_hosts=row['hosts'])
                    fetched[row['url']] = trafilatura.extract(raw,include_comments=False) or ''
                body = fetched[row['url']]
                if len(body)<400 or excluded(body) or not cross_match(context,row['title'],body):
                    record['reason'] = 'insufficient_or_unrelated_body'
                    continue
                source = {k:v for k,v in row.items() if k != 'hosts'}
                source.update(body=body[:10000],cross_check_status='candidate_not_confirmation')
                if any(s['url']==source['url'] for s in story['sources']):
                    continue
                story['sources'].append(source)
                record['status'] = 'cross_check_candidate'
                used_kinds.add(group)
                added += 1
            except (ValueError,OSError,requests.RequestException) as exc:
                record['reason'] = failure_reason(exc)
        story['international_status'] = 'candidates_available' if added else 'not_found_or_unverified'
    audit.update(status='complete',requests=fetcher.requests,bytes=fetcher.bytes)
    cache.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    original = json.loads(args.sources.read_text(encoding='utf-8'))
    enriched = enrich(original,args.output)
    print(json.dumps(dict(stories=len(enriched),overseas_sources=sum(
        s.get('language')=='en' for story in enriched for s in story['sources']))))
