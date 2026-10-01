"""Bounded, no-key event-media discovery with conservative metadata approval.

An article's OG image or an actor-name search result is NOT permission to reuse.
Auto selection needs per-file rights, a direct article association, a capture
date and overlapping caption/narration. This is not face/scene recognition.
"""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import subprocess
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode, urljoin

import requests
from PIL import Image

VERSION = 1
API = 'https://commons.wikimedia.org/w/api.php'
LICENCES = {
    'https://creativecommons.org/licenses/by/4.0': 'CC-BY-4.0',
    'https://creativecommons.org/publicdomain/zero/1.0': 'CC0',
    'https://www.kogl.or.kr/open/info/license_info/by.do': 'KOGL-1',
}
STOP = {'대통령','정부','국회','오늘','관련','대한','위해','기자','사진','영상','자료','발표','정치','대한민국'}


def canonical(url):
    if not isinstance(url,str):
        return ''
    try:
        p = urlsplit(html.unescape(url))
        return urlunsplit(('https',p.netloc.lower(),p.path.rstrip('/'),p.query,''))
    except ValueError:
        return ''


def plain(value):
    return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]*>',' ',str(value)))).strip()


def terms(text):
    return set(re.findall(r'[가-힣A-Za-z]{2,}',plain(text))) - STOP


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.images, self.active, self.buffer = [], [], False, ''

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script' and a.get('type','').lower() == 'application/ld+json':
            self.active, self.buffer = True, ''
        if tag == 'meta' and a.get('property') in ('og:image','og:video','og:video:url'):
            self.images.append(a.get('content',''))

    def handle_data(self,data):
        if self.active:
            self.buffer += data

    def handle_endtag(self,tag):
        if tag == 'script' and self.active:
            try:
                self.scripts.append(json.loads(self.buffer))
            except ValueError:
                pass
            self.active = False


def schema_candidates(document, source_url):
    parser = Page()
    parser.feed(document)
    candidates = []

    def visit(node, attached=False):
        if isinstance(node,list):
            for child in node:
                visit(child,attached)
        elif isinstance(node,dict):
            types = node.get('@type',[])
            types = [types] if isinstance(types,str) else types
            if not isinstance(types,list):
                types = []
            if any(t in types for t in ('ImageObject','VideoObject')):
                owner = node.get('isPartOf',node.get('mainEntityOfPage',''))
                if isinstance(owner,dict):
                    owner = owner.get('@id',owner.get('url',''))
                creator = node.get('creator',node.get('author',{}))
                if isinstance(creator,list):
                    creator = creator[0] if creator else {}
                if isinstance(creator,dict):
                    creator = creator.get('name','')
                license_url = node.get('license','')
                if isinstance(license_url,dict):
                    license_url = license_url.get('url',license_url.get('@id',''))
                candidates.append(dict(origin='article-jsonld', url=node.get('contentUrl',node.get('url','')),
                    asset_page=source_url, article_link=source_url if attached else owner,
                    license_url=license_url, credit=plain(creator),
                    description=plain(node.get('caption',node.get('description',node.get('name','')))),
                    captured=node.get('dateCreated',''), source_url=source_url))
            is_article = any(t in types for t in ('NewsArticle','Article','ReportageNewsArticle'))
            article_url = node.get('url',node.get('@id',source_url))
            is_article = is_article and isinstance(article_url,str) and canonical(article_url)==canonical(source_url)
            for key,value in node.items():
                if isinstance(value,(dict,list)):
                    # Only direct article image/video links qualify, not a logo.
                    visit(value,is_article and key in ('image','video','associatedMedia'))
    for node in parser.scripts:
        visit(node)
    for url in parser.images:
        if not any(c['url'] == url for c in candidates):
            candidates.append(dict(origin='open-graph',url=urljoin(source_url,url),
                                   source_url=source_url,asset_page=source_url))
    # Don't let publisher logos or URL-less graph nodes crowd out actual media.
    candidates = [c for c in candidates if isinstance(c.get('url'),str) and c['url'].startswith('https://')]
    candidates.sort(key=lambda c:(c.get('article_link') != source_url,not bool(c.get('license_url'))))
    unique = {(c['url'],c.get('article_link',''),c.get('license_url','')):c for c in candidates}
    return list(unique.values())[:6]


def commons_candidates(data, source_url):
    result = []
    if not isinstance(data,dict) or not isinstance(data.get('query',{}),dict):
        return result
    if not isinstance(data.get('query',{}).get('pages',{}),dict):
        return result
    for page in data.get('query',{}).get('pages',{}).values():
        for info in page.get('imageinfo',[])[:1]:
            ext = info.get('extmetadata',{})
            value = lambda key: ext.get(key,{}).get('value','')
            links = re.findall(r'href=[\"\']([^\"\']+)',value('Credit'))
            article = next((s for s in links if canonical(s)==canonical(source_url)),'')
            result.append(dict(origin='commons',url=info.get('url',''),
                asset_page=info.get('descriptionurl',''),source_url=source_url,article_link=article,
                license_url=value('LicenseUrl'),credit=plain(value('Artist')),
                description=plain(value('ImageDescription')),captured=plain(value('DateTimeOriginal'))))
    return result


def failure_reason(exc):
    # Keep actionable HTTP/budget codes without logging URLs or response bodies.
    message = str(exc)
    return message if isinstance(exc,ValueError) and re.fullmatch('[a-z0-9_]{1,70}',message) else type(exc).__name__


def approve(candidate, source, narrations):
    """Explain every rejection; never turn a keyword hit into human review."""
    c = candidate
    if not isinstance(c,dict) or any(not isinstance(c.get(k,''),str) for k in
        ('url','asset_page','source_url','article_link','license_url','credit','description','captured')):
        return None,'invalid_metadata_type'
    if not c.get('license_url') or canonical(c['license_url']) not in LICENCES:
        return None,'missing_or_unsupported_per_asset_license'
    if canonical(c.get('article_link','')) != canonical(source['url']):
        return None,'no_exact_article_association'
    if not c.get('credit') or len(c['credit']) > 100:
        return None,'missing_or_unbounded_creator'
    if re.search(r'자료\s*사진|자료\s*화면|합성|상상도|조감도|illustration|file photo|archive|AI.generated',c.get('description',''),re.I):
        return None,'archival_or_synthetic_not_event_media'
    try:
        captured = date.fromisoformat(str(c.get('captured',''))[:10])
        published = date.fromisoformat(source['published'][:10])
    except ValueError:
        return None,'capture_date_missing_upload_date_not_substituted'
    if not 0 <= (published-captured).days <= 2:
        return None,'capture_date_not_near_source_publication'
    title_terms = terms(source['title'])
    if len(title_terms & terms(c.get('description',''))) < 2:
        return None,'caption_does_not_identify_this_story'
    for narration in narrations:
        from .international import cross_match
        if (len(terms(narration) & title_terms & terms(c['description'])) >= 2
                or cross_match(narration,source['title'],c['description'])):
            return dict(license=LICENCES[canonical(c['license_url'])],
                        anchor=narration, captured=captured.isoformat()),None
    return None,'no_matching_narration'


class Fetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.requests, self.bytes = 0,0
        self.deadline = time.monotonic()+120

    def get(self,url,limit,redirect_hosts=(),redirects=2):
        if self.requests >= 18 or self.bytes >= 50_000_000 or time.monotonic() > self.deadline:
            raise ValueError('collection_budget_exhausted')
        p = urlsplit(url)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None,443):
            raise ValueError('unsafe_url')
        addresses = socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('non_public_host')
        self.requests += 1
        with self.session.get(url,timeout=(4,12),stream=True,allow_redirects=False,
             headers={'User-Agent':'TodayEnterLongform/1.0 (https://github.com/kjuhoho/political-shorts)'}) as response:
            if response.status_code in (301,302,303,307,308) and redirects > 0:
                target = urljoin(url,response.headers.get('Location',''))
                if urlsplit(target).hostname in redirect_hosts:
                    return self.get(target,limit,redirect_hosts,redirects-1)
            if response.status_code != 200:
                raise ValueError('http_'+str(response.status_code))
            chunks, size = [],0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                self.bytes += len(chunk)
                if size > limit or self.bytes > 50_000_000 or time.monotonic() > self.deadline:
                    raise ValueError('download_budget_exceeded')
                chunks.append(chunk)
            return b''.join(chunks)


def save_media(data, folder, ffmpeg='ffmpeg'):
    digest = hashlib.sha256(data).hexdigest()
    folder.mkdir(parents=True,exist_ok=True)
    if data.startswith((b'\xff\xd8\xff',b'\x89PNG\r\n\x1a\n')):
        path = folder/(digest+('.jpg' if data[:2]==b'\xff\xd8' else '.png'))
        path.write_bytes(data)
        with Image.open(path) as image:
            if image.width < 640 or image.height < 360 or image.width*image.height > 30_000_000:
                raise ValueError('image_dimensions_outside_budget')
            image.verify()
        return path,digest
    if data[4:8] == b'ftyp' or data.startswith(b'\x1a\x45\xdf\xa3'):
        original = folder/(digest+'.download')
        original.write_bytes(data)
        path = folder/(digest+'.mp4')
        # Decode locally, disable remote protocols and discard all source audio.
        subprocess.run([ffmpeg,'-y','-v','error','-protocol_whitelist','file,pipe',
            '-i',str(original),'-t','20','-an','-map','0:v:0',
            '-vf','scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30',
            '-c:v','libx264','-preset','veryfast','-crf','23',str(path)],
            check=True,capture_output=True,timeout=45)
        return path,hashlib.sha256(path.read_bytes()).hexdigest()
    raise ValueError('unsupported_media_signature')


def collect(stories, scenes, output, fetcher=None):
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    key = hashlib.sha256(json.dumps([VERSION,stories,scenes],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    report_path = output/'media-collection.json'
    registry = output/'auto-media-registry.json'
    if report_path.exists():
        previous = json.loads(report_path.read_text(encoding='utf-8'))
        if previous.get('key') == key:
            # Resume never silently refetches already attempted discovery.
            return previous
    fetcher = fetcher or Fetcher()
    report = dict(version=VERSION,key=key,status='started',candidates=[],selected=0)
    report_path.write_text(json.dumps(report),encoding='utf-8')
    registry.write_text('[]',encoding='utf-8')
    selected = []
    for issue,story in enumerate(stories[:3],1):
        narrations = [text for label,text in scenes if re.search(rf'핵심\s*{issue}(?:\D|$)',label)][1:]
        # The first scene is reserved for the source card by the renderer.
        candidates = []
        from .international import event_review_valid
        overseas = sorted((s for s in story['sources'][1:] if s.get('language')=='en'
                           and event_review_valid(s,story['sources'][0])),
                          key=lambda s:not s.get('kind','').startswith('official'))
        source_rows = [story['sources'][0],*overseas[:1]]
        for source in source_rows:
            try:
                document = fetcher.get(source['url'],2_000_000).decode('utf-8',errors='replace')
                candidates += schema_candidates(document,source['url'])
            except (ValueError,requests.RequestException,OSError) as exc:
                report['candidates'].append(dict(issue=issue,status='source_fetch_failed',reason=failure_reason(exc)))
            try:
                query = ' '.join(sorted(terms(source['title']),key=lambda t:(-len(t),t))[:4])
                url = API+'?'+urlencode(dict(action='query',format='json',generator='search',
                    gsrsearch=query,gsrnamespace=6,gsrlimit=3,prop='imageinfo',iiprop='url|extmetadata'))
                candidates += commons_candidates(json.loads(fetcher.get(url,2_000_000)),source['url'])
            except (ValueError,requests.RequestException,OSError) as exc:
                report['candidates'].append(dict(issue=issue,status='commons_fetch_failed',reason=failure_reason(exc)))
        accepted = 0
        for c in candidates:
            source = next(s for s in source_rows if s['url']==c['source_url'])
            verdict, reason = approve(c,source,narrations)
            if source.get('cross_check_status') == 'candidate_not_confirmation':
                from .international import event_review_valid
                if not event_review_valid(source,story['sources'][0]):
                    verdict,reason = None,'overseas_event_not_grounded_by_existing_review'
            audit = dict(issue=issue,candidate=c,status='held',reason=reason)
            report['candidates'].append(audit)
            if verdict is None or accepted >= 2:
                if verdict:
                    audit['reason'] = 'per_issue_limit'
                continue
            try:
                url = c['url']
                # Only same-origin media or Wikimedia originals. No arbitrary CDN
                # or media player scraping; add another provider deliberately.
                if urlsplit(url).hostname not in (urlsplit(source['url']).hostname,'upload.wikimedia.org'):
                    raise ValueError('media_host_not_allowed')
                data = fetcher.get(url,12_000_000)
                path,digest = save_media(data,output/'collected-media')
                row = dict(reviewed=False,approval='automatic-metadata-v1',proof=c,
                    source_url=source['url'],asset_page=c['asset_page'],credit=c['credit'],
                    license=verdict['license'],license_url=c['license_url'],
                    sha256=digest,path=path.relative_to(output).as_posix(),anchor=verdict['anchor'],
                    caption=f"보도 연결 자료 / 자료 생성일 {verdict['captured']} / 메타데이터 기준")
                selected.append(row)
                accepted += 1
                audit.update(status='selected',reason=None,sha256=digest)
                registry.write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
            except (ValueError,OSError,requests.RequestException,subprocess.SubprocessError) as exc:
                audit.update(status='held',reason='download_or_decode_'+failure_reason(exc))
    report.update(status='complete',selected=len(selected),requests=fetcher.requests,bytes=fetcher.bytes)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--script',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args = ap.parse_args()
    from .renderer import clean_script
    stories = json.loads((args.script.parent/'sources.json').read_text(encoding='utf-8'))
    result = collect(stories,clean_script(args.script),args.output)
    print(json.dumps({k:result.get(k) for k in ('status','selected','requests','bytes')}))
