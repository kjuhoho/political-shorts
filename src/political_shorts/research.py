"""Research stage: turn "an article about X" into "what is actually going on with X".

The seed articles are only the skeleton. Once a story is picked, this module
gathers extra material so the script can explain why it happened, how it is
going, who says what (government / ruling / opposition / experts), the upsides
and downsides, and how people are reacting:

  * more news      — Google News search RSS (no key)
  * web + official — an LLM with live web search (Groq compound -> Gemini
                     grounded search), asked for a structured JSON of notes
  * YouTube        — related videos and the top comments (needs YOUTUBE_API_KEY)

Everything is best-effort: a source that is missing, rate-limited or empty is
skipped and the pack simply has less in it. The pack is UNVERIFIED reference
material — the writer is told to use only what is attributed, and to treat
comments as "reaction", never as fact.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import feedparser
import requests

from .config import Settings
from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("research")

_UA = "Mozilla/5.0 (compatible; political-shorts-research/1.0)"
_CACHE_TTL_S = 12 * 3600          # same story is researched once per half-day
_BLOCK_CHARS = 4200               # cap on what is handed to the LLM prompt


# --------------------------------------------------------------------- query
def build_query(headline: str, topic: str = "") -> str:
    """A short Korean search phrase: the headline minus brackets/quotes/tails,
    plus the main actor when the headline doesn't already name them."""
    h = clean_text(headline)
    h = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", h)
    h = re.sub(r"[\"'“”‘’…·]", " ", h)
    h = re.sub(r"\s+", " ", h).strip()
    if topic and topic not in h:
        h = f"{topic} {h}"
    return h[:60].strip()


# ---------------------------------------------------------------------- news
def gnews(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Extra coverage of the same story from Google News search (KR)."""
    if not query:
        return []
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=15)
        r.raise_for_status()
    except Exception as exc:  # pragma: no cover - network dependent
        log.info("research: google news failed (%s)", str(exc)[:80])
        return []
    out: list[dict[str, str]] = []
    for e in feedparser.parse(r.content).entries[: limit * 2]:
        title = clean_text(e.get("title", ""))
        if not title:
            continue
        source = ""
        m = re.match(r"^(.*)\s+-\s+([^-]{1,20})$", title)
        if m:
            title, source = m.group(1).strip(), m.group(2).strip()
        out.append({"title": title, "source": source, "link": e.get("link", "")})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------ article bodies
def bing_news(query: str, limit: int = 8) -> list[dict[str, str]]:
    """News search whose feed carries the DIRECT article URL (Google News links
    are redirects that can't be fetched), so bodies can actually be read."""
    if not query:
        return []
    url = f"https://www.bing.com/news/search?q={quote(query)}&format=rss&mkt=ko-KR"
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=15)
        r.raise_for_status()
    except Exception as exc:  # pragma: no cover - network dependent
        log.info("research: bing news failed (%s)", str(exc)[:80])
        return []
    out: list[dict[str, str]] = []
    for e in feedparser.parse(r.content).entries:
        link = e.get("link", "")
        direct = (parse_qs(urlparse(link).query).get("url") or [""])[0]
        title = clean_text(e.get("title", ""))
        if not (direct.startswith("http") and title):
            continue
        source = clean_text(e.get("news_source", "")) or urlparse(direct).netloc.replace("www.", "")
        out.append({"title": title, "source": source, "link": direct})
        if len(out) >= limit:
            break
    return out


class _Paragraphs(HTMLParser):
    """Collects <p> text, skipping script/style/nav-ish blocks (stdlib only)."""
    _SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paras: list[str] = []
        self._skip = 0
        self._p = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._p, self._buf = True, []

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag == "p" and self._p:
            txt = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if len(txt) >= 30:
                self.paras.append(txt)
            self._p = False

    def handle_data(self, data):
        if self._p and not self._skip:
            self._buf.append(data)


def article_text(url: str, limit: int = 2600) -> str:
    """The readable body of one public news page ('' when it can't be read)."""
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=12)
        if r.status_code != 200:
            return ""
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        parser = _Paragraphs()
        parser.feed(r.text)
    except Exception:  # pragma: no cover - network dependent
        return ""
    text = " ".join(parser.paras)
    return text[:limit] if len(text) >= 200 else ""


def resolve_link(link: str) -> str:
    """Google News search links are opaque redirects; ask Google's own endpoint
    for the publisher URL. Returns the link unchanged if it is already direct,
    or '' when it can't be resolved (the article is then skipped)."""
    if "news.google.com" not in link:
        return link
    try:
        m = re.search(r"/articles/([^?/]+)", link)
        if not m:
            return ""
        gid = m.group(1)
        page = requests.get(f"https://news.google.com/rss/articles/{gid}",
                            headers={"User-Agent": _UA}, timeout=12)
        sig = re.search(r'data-n-a-sg="([^"]+)"', page.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page.text)
        if not (sig and ts):
            return ""
        inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,'
                 'null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                 f'"{gid}",{ts.group(1)},"{sig.group(1)}"]')
        req = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                          headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                                   "User-Agent": _UA},
                          data="f.req=" + quote(req), timeout=12)
        blob = json.loads(r.text.split("\n\n")[1])
        url = json.loads(blob[0][2])[1]
        return url if isinstance(url, str) and url.startswith("http") else ""
    except Exception:  # pragma: no cover - network dependent
        return ""


def fetch_bodies(items: list[dict[str, str]], want: int = 5) -> list[dict[str, str]]:
    """Read up to `want` articles in parallel; keeps only the ones that had a body."""
    if not items:
        return []
    def _read(it: dict[str, str]) -> dict[str, str] | None:
        url = resolve_link(it["link"])
        text = article_text(url) if url else ""
        return {**it, "link": url, "text": text} if text else None

    with ThreadPoolExecutor(max_workers=6) as ex:
        got = list(ex.map(_read, items[: want * 3]))
    return [g for g in got if g][:want]


# ----------------------------------------------------------------- web notes
_WEB_PROMPT = (
    "당신은 한국 정치 전문 리서처입니다. 아래 주제를 인터넷에서 검색해서, 뉴스 기사 한 편을 "
    "쓰기 위한 조사 노트를 만들어 주세요. 오늘 날짜는 {today}입니다.\n"
    "주제: {headline}\n"
    "핵심 인물/키워드: {topic}\n\n"
    "다음을 조사하세요:\n"
    "1) 배경: 이 일이 왜 일어났는지, 관련 인물·제도·이전 경위\n"
    "2) 경위: 날짜순 주요 사건\n"
    "3) 현재 상황: 지금 어디까지 진행됐는지\n"
    "4) 공식 발언·입장: 대통령실·정부, 여당, 야당, 관련 기관의 공식 논평·브리핑·발언. "
    "정치인의 발언은 요약하지 말고 실제로 한 말을 끝까지 정확히 적을 것(누가·언제·어디서)\n"
    "5) 전문가·학계 평가\n"
    "6) 장점(기대 효과)과 단점(우려·비판)을 객관적으로\n"
    "7) 여론·온라인 반응(언론 여론조사, 커뮤니티·SNS 반응의 대체적 분위기)\n\n"
    "규칙: 검색으로 실제 확인한 내용만 쓰고, 확인 못 한 항목은 빈 배열/빈 문자열로 둘 것. "
    "지어내지 말 것. 각 항목에 출처(매체명 또는 기관명)를 붙일 것. 특정 진영 편을 들지 말 것.\n"
    "JSON 하나로만 답하세요:\n"
    '{{"background":"","timeline":["날짜: 사건"],"status":"",'
    '"positions":[{{"who":"","position":"","why":"","source":""}}],'
    '"statements":[{{"who":"","text":"","when":"","source":""}}],'
    '"experts":[{{"who":"","view":"","source":""}}],'
    '"pros":[""],"cons":[""],'
    '"reactions":[{{"where":"","summary":""}}],'
    '"sources":[{{"title":"","url":""}}]}}'
)


def _json_obj(raw: str) -> dict[str, Any] | None:
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", txt).strip()
    m = re.search(r"\{.*\}", txt, re.S)
    for cand in (txt, m.group(0) if m else ""):
        if not cand:
            continue
        for fix in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
            try:
                obj = json.loads(fix)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    return None


def _has_content(obj: dict[str, Any]) -> bool:
    """True when the notes hold anything real — a model that never searched
    tends to echo the empty JSON skeleton, which must not count as an answer."""
    for k, v in obj.items():
        if k == "sources":
            continue
        if isinstance(v, str) and len(v.strip()) >= 20:
            return True
        if isinstance(v, list) and any(str(x).strip() for x in v):
            return True
    return False


def _usable(text: str) -> bool:
    obj = _json_obj(text)
    return _has_content(obj) if obj is not None else len(text.strip()) >= 80


_EXTRACT_SYSTEM = (
    "당신은 한국 정치 전문 리서처입니다. 아래에 같은 사안을 다룬 여러 기사 본문이 주어집니다. "
    "이 본문들에 실제로 적힌 내용만으로, 뉴스 기사 한 편을 쓰기 위한 조사 노트를 JSON으로 "
    "정리하세요.\n"
    "규칙:\n"
    "1) 본문에 없는 내용은 절대 쓰지 말고, 없으면 빈 문자열/빈 배열로 둘 것.\n"
    "2) statements: 정치인·정부·정당의 발언은 요약하지 말고 본문에 적힌 말을 끝까지 그대로 "
    "옮길 것(누가·언제·어디서). 출처는 매체명.\n"
    "3) positions: 정부·여당·야당·기관의 입장과 그 이유. experts: 전문가·학계 평가.\n"
    "4) pros / cons: 이 사안의 장점(기대 효과)과 단점(우려·비판)을 각각 본문 근거가 있는 "
    "것만, 객관적으로. 한쪽 편을 들지 말 것.\n"
    "5) reactions: 여론조사·시민단체·온라인 반응. 검증되지 않은 반응은 그렇게 표시.\n"
    "6) background: 왜 이 일이 일어났는지 배경과 경위 2~3문장(날짜 포함). timeline은 날짜순.\n"
    '출력은 JSON 하나만: {"background":"","timeline":["날짜: 사건"],"status":"",'
    '"positions":[{"who":"","position":"","why":"","source":""}],'
    '"statements":[{"who":"","text":"","when":"","source":""}],'
    '"experts":[{"who":"","view":"","source":""}],"pros":[""],"cons":[""],'
    '"reactions":[{"where":"","summary":""}]}'
)


def _extract_notes(headline: str, bodies: list[dict[str, str]], cfg: Settings) -> dict[str, Any]:
    from .llm import complete

    docs = "\n\n".join(f"[기사{i} — {b['source']}] {b['title']}\n{b['text']}"
                       for i, b in enumerate(bodies, 1))
    prompt = (f"[주제] {clean_text(headline)}\n\n{docs[:9000]}\n\n"
              "위 기사들만 근거로 조사 노트 JSON을 작성하세요.")
    try:
        raw = complete(prompt, cfg, max_tokens=1800, system=_EXTRACT_SYSTEM)
    except Exception as exc:  # pragma: no cover - network dependent
        log.info("research: note extraction failed (%s)", str(exc)[:100])
        return {}
    obj = _json_obj(raw)
    return obj if obj is not None and _has_content(obj) else {}


def web_notes(headline: str, topic: str, cfg: Settings, query: str = "") -> dict[str, Any]:
    """Structured research notes for one story. Reads the actual bodies of extra
    articles and extracts from them (grounded in real text); only if none could
    be read does it fall back to a live-web-search LLM."""
    q = query or build_query(headline, topic)
    found = gnews(q, limit=12) + bing_news(q)
    bodies = fetch_bodies(found)
    log.info("research: %d articles found, %d readable bodies", len(found), len(bodies))
    if bodies:
        notes = _extract_notes(headline, bodies, cfg)
        if notes:
            notes["sources"] = [{"title": b["title"], "url": b["link"]} for b in bodies]
            log.info("research: notes extracted from %d article bodies", len(bodies))
            return notes
    from .llm import web_search

    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    prompt = _WEB_PROMPT.format(today=today, headline=clean_text(headline), topic=topic or "(없음)")
    raw = web_search(prompt, cfg, accept=_usable)
    if not raw:
        return {}
    obj = _json_obj(raw)
    if obj is None:
        # the searcher answered in prose — keep it as a single background note
        return {"background": clean_text(raw)[:1500]}
    return obj


# ------------------------------------------------------------------- youtube
def youtube(query: str, cfg: Settings, days: int = 7) -> dict[str, Any]:
    """Related videos + top comments. Needs YOUTUBE_API_KEY; otherwise {}."""
    key = (getattr(cfg, "youtube_api_key", "") or "").strip()
    if not key or not query:
        return {}
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/search", timeout=15, params={
            "part": "snippet", "q": query, "type": "video", "order": "relevance",
            "publishedAfter": since, "regionCode": "KR", "relevanceLanguage": "ko",
            "maxResults": 6, "key": key})
        r.raise_for_status()
        items = r.json().get("items", [])
    except Exception as exc:  # pragma: no cover - network dependent
        log.info("research: youtube search failed (%s)", str(exc)[:80])
        return {}
    videos = [{"id": (i.get("id") or {}).get("videoId", ""),
               "title": clean_text((i.get("snippet") or {}).get("title", "")),
               "channel": clean_text((i.get("snippet") or {}).get("channelTitle", ""))}
              for i in items]
    videos = [v for v in videos if v["id"] and v["title"]]
    comments: list[str] = []
    for v in videos[:3]:
        try:
            c = requests.get("https://www.googleapis.com/youtube/v3/commentThreads", timeout=15,
                             params={"part": "snippet", "videoId": v["id"], "order": "relevance",
                                     "maxResults": 12, "textFormat": "plainText", "key": key})
            if c.status_code != 200:            # comments disabled on the video
                continue
            for it in c.json().get("items", []):
                t = ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet", {})
                text = clean_text(t.get("textDisplay", ""))
                if 12 <= len(text) <= 300:      # authors are never kept
                    comments.append(text)
        except Exception:  # pragma: no cover - network dependent
            continue
    return {"videos": [{"title": v["title"], "channel": v["channel"]} for v in videos],
            "comments": comments[:24]}


# ---------------------------------------------------------------------- pack
def _cache_path(cfg: Settings, query: str) -> Path:
    d = Path(cfg.data_dir) / "research_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha1(query.encode("utf-8")).hexdigest()[:16] + ".json")


def build_pack(headline: str, topic: str, cfg: Settings) -> dict[str, Any]:
    """Gather everything available for one story. {} when research is off or
    nothing at all came back. Cached so the several rewrite attempts (and
    duplicate clusters) of one story don't re-query."""
    if not getattr(cfg, "research_enabled", True):
        return {}
    query = build_query(headline, topic)
    if not query:
        return {}
    path = _cache_path(cfg, query)
    try:
        if path.exists() and time.time() - path.stat().st_mtime < _CACHE_TTL_S:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    pack: dict[str, Any] = {
        "query": query,
        "news": gnews(query),
        "web": web_notes(headline, topic, cfg),
        "youtube": youtube(query, cfg),
    }
    got = [k for k in ("news", "web", "youtube") if pack.get(k)]
    log.info("research %r: %s", query, ", ".join(got) or "nothing found")
    if not got:
        return {}
    try:
        path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return pack


# --------------------------------------------------------------- prompt text
def _lines(items: list[str], cap: int, width: int = 220) -> str:
    return "\n".join(f"- {re.sub(r'\s+', ' ', str(t)).strip()[:width]}"
                     for t in items[:cap] if str(t).strip())


def pack_block(pack: dict[str, Any]) -> str:
    """The research pack formatted for the LLM prompt ('' when empty)."""
    if not pack:
        return ""
    web = pack.get("web") or {}
    parts: list[str] = []

    def add(label: str, body: str) -> None:
        if body and body.strip():
            parts.append(f"{label}\n{body.strip()}")

    add("배경(조사)", str(web.get("background") or ""))
    add("경위(날짜순)", _lines(web.get("timeline") or [], 8))
    add("현재 상황", str(web.get("status") or ""))
    add("공식 발언(누가 한 말인지, 끝까지)", _lines(
        [f"{s.get('who', '')}({s.get('when', '')}): \"{s.get('text', '')}\" [{s.get('source', '')}]"
         for s in web.get("statements") or [] if isinstance(s, dict) and s.get("text")], 6, 320))
    add("입장(정부·여당·야당 등)", _lines(
        [f"{p.get('who', '')}: {p.get('position', '')} (이유: {p.get('why', '')}) [{p.get('source', '')}]"
         for p in web.get("positions") or [] if isinstance(p, dict) and p.get("who")], 6, 300))
    add("전문가 평가", _lines(
        [f"{e.get('who', '')}: {e.get('view', '')} [{e.get('source', '')}]"
         for e in web.get("experts") or [] if isinstance(e, dict) and e.get("view")], 4, 260))
    add("장점·기대 효과", _lines(web.get("pros") or [], 4))
    add("단점·우려·비판", _lines(web.get("cons") or [], 4))
    reactions = [f"{r.get('where', '')}: {r.get('summary', '')}"
                 for r in web.get("reactions") or [] if isinstance(r, dict) and r.get("summary")]
    yt = pack.get("youtube") or {}
    if yt.get("comments"):
        reactions.append("유튜브 댓글 발췌: " + " / ".join(c[:90] for c in yt["comments"][:6]))
    add("여론·온라인 반응(검증되지 않은 반응)", _lines(reactions, 6, 420))
    add("추가 보도(제목 — 매체)", _lines(
        [f"{n['title']} — {n['source']}" if n.get("source") else n["title"]
         for n in pack.get("news") or []], 8, 120))
    if yt.get("videos"):
        add("관련 유튜브 영상", _lines([f"{v['title']} ({v['channel']})" for v in yt["videos"]], 4, 100))
    if not parts:
        return ""
    return (
        "[추가 자료조사 — 검색으로 모은 참고 자료(검증 전). 원문 기사와 함께 근거로 쓰되, "
        "출처가 분명한 것만 쓰고 지어내지 말 것. 댓글·커뮤니티 반응은 사실이 아니라 "
        "'이런 반응이 있다'로만 다룰 것]\n" + "\n\n".join(parts)
    )[:_BLOCK_CHARS] + "\n\n"
