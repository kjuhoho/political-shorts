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

from .chrono import date_block
from .config import Settings
from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("research")

# outlet -> lean, from the publisher domain of a fetched article (used to tell whether the
# material we hold is one-sided, and to go and get the other side)
LEAN_DOMAINS = {
    "left": ["hani.co.kr", "khan.co.kr", "ohmynews.com", "pressian.com"],
    "right": ["chosun.com", "donga.com", "joongang.co.kr", "munhwa.com", "segye.com"],
}
_LEAN_KO = {"left": "진보 성향", "right": "보수 성향"}


def _kw(text: str) -> set[str]:
    """Distinctive Korean words of a text (generic political vocabulary removed)."""
    from .topics import _STOP

    return set(re.findall(r"[가-힣]{2,}", text or "")) - set(_STOP)


def lean_of(url: str) -> str:
    """'left' | 'right' | '' from an article URL's domain."""
    host = urlparse(url or "").netloc.lower()
    for lean, domains in LEAN_DOMAINS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return lean
    return ""


_UA = "Mozilla/5.0 (compatible; political-shorts-research/1.0)"
_CACHE_TTL_S = 48 * 3600          # a story is researched once per two days (CI restores the cache)
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
        return {**it, "link": url, "text": text, "lean": lean_of(url)} if text else None

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


def _salvage_json(txt: str) -> dict[str, Any] | None:
    """A model answer cut off mid-way (output token limit) is still mostly valid: keep the longest
    prefix that ends at a comma between items and close whatever brackets are still open."""
    start = txt.find("{")
    if start < 0:
        return None
    s = txt[start:]
    stack: list[str] = []
    in_str = esc = False
    cuts: list[tuple[int, str]] = []                 # (index of a top-level-item comma, closers needed there)
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
        elif ch == "," and stack:
            cuts.append((i, "".join(reversed(stack))))
    for i, closers in reversed(cuts):
        try:
            obj = json.loads(s[:i] + closers)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


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
    return _salvage_json(txt)


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


_PLAN_SYSTEM = (
    "당신은 한국 정치 뉴스 리서치 플래너입니다. 기사 하나를 영상으로 만들기 전에, 시청자가 "
    "정말 궁금해할 것이 무엇이고 그 답을 어떤 검색어로 찾아야 하는지 정합니다.\n"
    "1) event: 실제로 일어난 핵심 '사건' 한 줄. 헤드라인이 누군가의 반응·논평·입장 표명이면(예: "
    "'청와대, 김승원 사퇴에 결정 존중') 그 반응이 아니라 반응의 대상이 된 사건(김승원 후보자가 "
    "사퇴한 일)을 event로 잡을 것.\n"
    "2) question: 시청자가 가장 궁금해할 핵심 질문 한 문장. 대부분 '왜 그런 일이 벌어졌나(원인)'. "
    "예: '김승원 후보자는 왜 사퇴했나?'\n"
    "3) queries: 검색어 3~4개, 뉴스 제목에 나올 법한 표현으로 각 25자 이내. 대부분은 원인과 발단(의혹, "
    "논란, 청문회, 쟁점, 경위)을 겨냥하고, **반드시 1개는 반대편의 입장을 겨냥**할 것 — 비판·반대·우려·"
    "반박·해명 중 이 사안에 맞는 말을 붙여(예: '정부 대북 의료지원 야당 비판', '경기도 영화제 축소 해명'). "
    "예: ['김승원 후보자 사퇴 이유', '김승원 후보자 의혹 청문회 논란', '김승원 후보자 사퇴 야당 반응'].\n"
    'JSON 하나만: {"event":"","question":"","queries":["",""]}'
)


def plan_research(headline: str, topic: str, context: str, cfg: Settings) -> dict[str, Any]:
    """Decide what the viewer wants to know and which searches answer it — the
    step that keeps the research from just re-finding more articles about the
    same reaction. Falls back to the headline query when no LLM is available."""
    base_q = build_query(headline, topic)
    # even without an LLM the plan asks for the other side: one plain query, one aimed at criticism / rebuttal
    fallback = {"event": clean_text(headline), "question": "",
                "queries": [base_q, f"{base_q[:40]} 비판 반박"]}
    from .llm import complete

    prompt = (f"{date_block()}[헤드라인] {clean_text(headline)}\n[핵심 인물] {topic or '(없음)'}\n"
              f"[기사에서 확인된 내용]\n{(context or '')[:1800]}\n\n위 사건의 조사 계획 JSON을 작성하세요.")
    try:
        raw = complete(prompt, cfg, max_tokens=500, system=_PLAN_SYSTEM)
    except Exception as exc:  # pragma: no cover - network dependent
        log.info("research: planning failed (%s)", str(exc)[:80])
        return fallback
    obj = _json_obj(raw) or {}
    queries = [re.sub(r"\s+", " ", str(q)).strip()[:40] for q in (obj.get("queries") or []) if str(q).strip()]
    if not queries:
        return fallback
    return {"event": clean_text(str(obj.get("event") or headline))[:120],
            "question": clean_text(str(obj.get("question") or ""))[:120],
            "queries": queries[:4]}


_EXTRACT_SYSTEM = (
    "당신은 한국 정치 전문 리서처입니다. 아래에 같은 사안을 다룬 여러 기사 본문이 주어집니다. "
    "이 본문들에 실제로 적힌 내용만으로, 뉴스 기사 한 편을 쓰기 위한 조사 노트를 JSON으로 "
    "정리하세요. [핵심 질문]이 주어지면 그 질문에 답하는 것이 가장 중요합니다.\n"
    "규칙:\n"
    "1) 본문에 없는 내용은 절대 쓰지 말고, 없으면 빈 문자열/빈 배열로 둘 것.\n"
    "2) why: [핵심 질문]에 대한 답 — 이 일이 '왜' 일어났는지 원인·발단·계기를 본문에 적힌 대로, "
    "시간 순서의 인과로. 각 항목은 {reason: 원인 한 문장, evidence: 본문의 구체적 근거(날짜·"
    "인물·수치·발언), source: 매체명}. 반드시 지킬 것: 정부·정당·인물의 '반응·논평·입장 표명'"
    "(예: '결정을 존중한다', '유감이다')은 원인이 아니므로 why에 넣지 말고 positions/"
    "reactions에 넣을 것. 본문이 원인을 밝히지 않았으면 why는 빈 배열로 두고, 추측하지 말 것.\n"
    "3) statements: 정치인·정부·정당의 발언은 요약하지 말고 본문에 적힌 말을 끝까지 그대로 "
    "옮길 것(누가·언제·어디서). 출처는 매체명.\n"
    "4) positions: 정부·여당·야당·기관의 입장과 그 이유. 찬성·지지하는 쪽과 반대·비판하는 쪽을 둘 다 "
    "빠짐없이 별도 항목으로 넣을 것(한쪽만 있으면 있는 쪽만). experts: 전문가·학계 평가.\n"
    "5) pros / cons: 이 사안의 장점(기대 효과)과 단점(우려·비판)을 각각 본문 근거가 있는 "
    "것만, 객관적으로. 한쪽 편을 들지 말 것.\n"
    "6) reactions: 여론조사·시민단체·온라인 반응. 검증되지 않은 반응은 그렇게 표시.\n"
    "7) background: 사건의 배경과 경위 2~3문장(날짜 포함). timeline은 날짜순.\n"
    "8) 성향: 기사 헤더에 (진보 성향 매체)/(보수 성향 매체)가 있으면 positions·statements·pros·cons·"
    "reactions의 각 항목에 lean 필드('진보'/'보수'/'기타')를 그 기사의 성향대로 넣을 것. 같은 사안에 "
    "대해 진보 매체와 보수 매체의 입장이 다르면 둘 다 빠짐없이 positions에 넣을 것. positions의 who는 "
    "그 입장을 실제로 가진 주체이고, position·why는 그 주체 본인의 말만(다른 주체의 사정 금지).\n"
    '출력은 JSON 하나만: {"why":[{"reason":"","evidence":"","source":""}],'
    '"background":"","timeline":["날짜: 사건"],"status":"",'
    '"positions":[{"who":"","position":"","why":"","lean":"","source":""}],'
    '"statements":[{"who":"","text":"","when":"","lean":"","source":""}],'
    '"experts":[{"who":"","view":"","source":""}],"pros":[""],"cons":[""],'
    '"reactions":[{"where":"","summary":""}]}'
)


def _select_sentences(text: str, kws: set[str], budget: int = 1300) -> str:
    """The sentences of an article that matter for the story (keyword overlap, quotes, reporting verbs,
    cause words), in reading order, within `budget` chars. Sending whole bodies burned the free daily
    token quota twice as fast for no better notes."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?다요])\s+", text or "") if len(s.strip()) >= 15]

    def score(s: str) -> int:
        sc = 2 * len(_kw(s) & kws)
        if re.search(r"[“\"‘']", s):
            sc += 3
        if re.search(r"밝혔|말했|강조|주장|비판|요구|반박|해명|설명", s):
            sc += 2
        if re.search(r"때문|이유|배경|탓|따라|계기|발단", s):
            sc += 2
        return sc

    keep: list[int] = []
    used = 0
    for i in sorted(range(len(sents)), key=lambda i: -score(sents[i])):
        if used + len(sents[i]) <= budget:
            keep.append(i)
            used += len(sents[i])
    return " ".join(sents[i] for i in sorted(keep))


_QUOTE_RX = re.compile(
    r"((?<![가-힣])[가-힣]{2,4}\s?(?:대표|장관|의원|대통령|위원장|총리|후보자|대변인|지사|시장|비서실장|실장|원내대표))"
    r"[은는이가]?[^“\"]{0,50}[“\"]([^”\"]{10,220})[”\"]\s*(?:라고|라면서|며|고)?\s*"
    r"(?:밝혔|말했|강조했|주장했|비판했|요구했|반박했|설명했|촉구했)")
_CAUSE_RX = re.compile(r"[^.!?]*(?:때문|이유로|탓에|계기로|배경에는|영향으로)[^.!?]*[.!?]?")
_LEAN_NOTE = {"left": "진보", "right": "보수"}


def _rule_notes(bodies: list[dict[str, str]]) -> dict[str, Any]:
    """Notes without any LLM: quotes ("<who> … \"…\"라고 밝혔다") and cause sentences ("… 때문에 …") straight
    from the article text. Used only when every model failed, so a story still carries a real quote and
    a stated cause instead of being written from one article alone."""
    statements: list[dict[str, str]] = []
    why: list[dict[str, str]] = []
    for b in bodies:
        text = b.get("text", "") or ""
        lean = _LEAN_NOTE.get(b.get("lean", ""), "기타")
        for m in _QUOTE_RX.finditer(text):
            who = re.sub(r"\s+", " ", m.group(1)).strip()
            quote = m.group(2).strip()
            if not any(s["text"] == quote for s in statements):
                statements.append({"who": who, "text": quote, "when": "", "lean": lean, "source": b.get("source", "")})
        for m in _CAUSE_RX.finditer(text):
            sent = re.sub(r"\s+", " ", m.group(0)).strip()
            if 15 <= len(sent) <= 200 and not any(w["reason"] == sent for w in why):
                why.append({"reason": sent, "evidence": "", "source": b.get("source", "")})
    if not (statements or why):
        return {}
    positions = [{"who": s["who"], "position": s["text"][:60], "why": "", "lean": s["lean"], "source": s["source"]}
                 for s in statements[:4]]
    return {"statements": statements[:5], "why": why[:3], "positions": positions, "rule_based": True}


def _extract_notes(headline: str, bodies: list[dict[str, str]], cfg: Settings,
                   plan: dict[str, Any] | None = None) -> dict[str, Any]:
    from .llm import complete

    plan = plan or {}
    def _tag(b: dict[str, str]) -> str:
        return f" ({_LEAN_KO[b['lean']]} 매체)" if b.get("lean") in _LEAN_KO else ""

    def _tag2(b: dict[str, str]) -> str:
        if b.get("text"):
            return _tag(b)
        return f" ({_LEAN_KO[b['lean']]} 매체, 제목만)" if b.get("lean") in _LEAN_KO else " (제목만)"

    kws = _kw(" ".join([clean_text(headline), str(plan.get("event", "")), str(plan.get("question", "")),
                        " ".join(plan.get("queries") or [])]))
    docs = "\n\n".join(
        f"[기사{i} — {b['source']}{_tag2(b)}] {b['title']}\n"
        f"{_select_sentences(b['text'], kws) if b['text'] else '(본문을 읽지 못함 — 제목에 명시된 내용만 근거로 삼고 추측하지 말 것)'}"
        for i, b in enumerate(bodies, 1))
    head = f"[주제] {clean_text(headline)}\n"
    if plan.get("event"):
        head += f"[핵심 사건] {plan['event']}\n"
    if plan.get("question"):
        head += f"[핵심 질문] {plan['question']}\n"
    prompt = f"{date_block()}{head}\n{docs[:7500]}\n\n위 기사들만 근거로 조사 노트 JSON을 작성하세요."
    for max_tokens in (3800, 6000):
        try:
            raw = complete(prompt, cfg, max_tokens=max_tokens, system=_EXTRACT_SYSTEM)
        except Exception as exc:  # pragma: no cover - network dependent
            log.info("research: note extraction failed (%s)", str(exc)[:100])
            return _fallback_notes(bodies)
        obj = _json_obj(raw)
        if obj is not None and _has_content(obj):
            return obj
        log.info("research: notes unusable (%d chars, ends %r) — %s", len(raw or ""), (raw or "")[-30:],
                 "retrying with more room" if max_tokens == 3800 and not (raw or "").rstrip().endswith("}")
                 else "giving up")
        if (raw or "").rstrip().endswith("}"):        # complete but empty: a bigger budget won't help
            break
    return _fallback_notes(bodies)


def _fallback_notes(bodies: list[dict[str, str]]) -> dict[str, Any]:
    notes = _rule_notes([b for b in bodies if b.get("text")])
    if notes:
        log.info("research: models gave no notes — rule-based extraction found %d quote(s), %d cause sentence(s)",
                 len(notes.get("statements", [])), len(notes.get("why", [])))
    return notes


def _gather(queries: list[str]) -> list[dict[str, str]]:
    """Search every planned query, merge, drop repeats (same title) — earlier
    queries (the cause-focused ones) keep their place at the front."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for q in queries:
        for it in gnews(q, limit=8) + bing_news(q, limit=4):
            key = re.sub(r"\W+", "", it["title"])[:30]
            if key and key not in seen:
                seen.add(key)
                out.append(it)
    return out


def web_notes(headline: str, topic: str, cfg: Settings, query: str = "",
              plan: dict[str, Any] | None = None,
              seed_leans: list[str] | None = None) -> dict[str, Any]:
    """Structured research notes for one story. Reads the actual bodies of extra
    articles (found through the planned, cause-focused searches) and extracts
    from them; only if none could be read does it fall back to a live-web-search
    LLM."""
    queries = list((plan or {}).get("queries") or []) or [query or build_query(headline, topic)]
    found = _gather(queries)
    bodies = fetch_bodies(found, want=6)
    log.info("research: %d queries, %d articles found, %d readable bodies",
             len(queries), len(found), len(bodies))
    # BALANCE: if everything we hold leans one way, go and read the other side too
    have = ({x for x in (seed_leans or []) if x in _LEAN_KO}
            | {b.get("lean") for b in bodies if b.get("lean") in _LEAN_KO})
    missing = sorted(set(_LEAN_KO) - have)
    if missing:
        kws = _kw(" ".join(queries) + " " + str((plan or {}).get("event", "")) + " " + (topic or ""))
        more: list[dict[str, str]] = []
        for side in missing:
            for dom in LEAN_DOMAINS[side][:3]:
                for it in gnews(f"{queries[0]} site:{dom}", limit=3):
                    # a site: search returns that outlet's newest pieces even when it has not covered
                    # this story at all — only an article sharing >=2 distinctive words with the story counts
                    if len(_kw(it["title"]) & kws) >= 2:
                        more.append({**it, "want_lean": side})
        seen_links = {b["link"] for b in bodies}
        read = [b for b in fetch_bodies(more, want=3) if b["link"] not in seen_links]
        read_titles = {re.sub(r"\W+", "", b["title"]) for b in read}
        # outlets that block scraping still show their stance in the headline: keep those as title-only
        titled = [{**it, "text": "", "lean": it["want_lean"]} for it in more
                  if re.sub(r"\W+", "", it["title"]) not in read_titles][:3]
        bodies = bodies + read + titled
        log.info("research: one-sided material (missing %s) — added %d readable + %d title-only article(s) "
                 "from the other side", ",".join(missing), len(read), len(titled))
    if bodies:
        notes = _extract_notes(headline, bodies, cfg, plan)
        if notes:
            notes["sources"] = [{"title": b["title"], "url": b["link"]} for b in bodies if b.get("text")]
            still = (set(seed_leans or []) | {b.get("lean") for b in bodies}) & set(_LEAN_KO)
            notes["missing_leans"] = sorted(set(_LEAN_KO) - still)
            log.info("research: notes extracted from %d article bodies (why=%d)",
                     len(bodies), len(notes.get("why") or []))
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
        if r.status_code != 200:
            # the API's own reason (keyInvalid / accessNotConfigured / quotaExceeded …);
            # never str(exc) here — a requests error message carries the full URL incl. key
            try:
                err = (r.json().get("error") or {})
                why = f"{err.get('message', '')} [{', '.join(e.get('reason', '') for e in err.get('errors', []))}]"
            except Exception:
                why = ""
            log.info("research: youtube search failed (HTTP %s) %s", r.status_code, why[:220])
            return {}
        items = r.json().get("items", [])
    except Exception as exc:  # pragma: no cover - network dependent
        log.info("research: youtube search failed (%s)", type(exc).__name__)
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


def build_pack(headline: str, topic: str, cfg: Settings, context: str = "",
               seed_leans: list[str] | None = None) -> dict[str, Any]:
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
    plan = plan_research(headline, topic, context, cfg)
    pack: dict[str, Any] = {
        "query": query,
        "plan": plan,
        "news": gnews(query),
        "web": web_notes(headline, topic, cfg, plan=plan, seed_leans=seed_leans),
        # the event (not the headline's reaction) is what people talk about on YouTube
        "youtube": youtube(build_query(plan.get("event") or headline, topic), cfg),
    }
    log.info("research plan: event=%r question=%r queries=%s",
             plan.get("event", "")[:50], plan.get("question", "")[:50], plan.get("queries"))
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


def _lt(x: dict[str, Any]) -> str:
    lean = str(x.get("lean") or "")
    return f" [{lean} 성향 매체]" if lean in ("진보", "보수") else ""


def pack_block(pack: dict[str, Any]) -> str:
    """The research pack formatted for the LLM prompt ('' when empty)."""
    if not pack:
        return ""
    web = pack.get("web") or {}
    parts: list[str] = []

    def add(label: str, body: str) -> None:
        if body and body.strip():
            parts.append(f"{label}\n{body.strip()}")

    plan = pack.get("plan") or {}
    _miss = [_LEAN_KO[m] for m in (web.get("missing_leans") or []) if m in _LEAN_KO]
    if _miss:
        add("성향 균형 안내", f"{', '.join(_miss)} 매체 보도는 조사했지만 이 사안에 대한 내용을 확인하지 못했습니다. "
            "sides 카드에서 그 사실을 한 문장으로 밝히고, 확인되지 않은 쪽의 입장을 지어내지 말 것.")
    if plan.get("question"):
        add("핵심 질문(영상이 답해야 할 것)", f"{plan['question']}  (사건: {plan.get('event', '')})")
    add("원인·발단 — 왜 일어났나(반응·논평은 원인이 아님)", _lines(
        [f"{w.get('reason', '')} (근거: {w.get('evidence', '')}) [{w.get('source', '')}]"
         for w in web.get("why") or [] if isinstance(w, dict) and w.get("reason")], 6, 360))
    add("배경(조사)", str(web.get("background") or ""))
    add("경위(날짜순)", _lines(web.get("timeline") or [], 8))
    add("현재 상황", str(web.get("status") or ""))
    add("공식 발언(누가 한 말인지, 끝까지)", _lines(
        [f"{s.get('who', '')}{_lt(s)}({s.get('when', '')}): \"{s.get('text', '')}\" [{s.get('source', '')}]"
         for s in web.get("statements") or [] if isinstance(s, dict) and s.get("text")], 6, 320))
    add("입장(정부·여당·야당 등)", _lines(
        [f"{p.get('who', '')}{_lt(p)}: {p.get('position', '')} (이유: {p.get('why', '')}) [{p.get('source', '')}]"
         for p in web.get("positions") or [] if isinstance(p, dict) and p.get("who") and p.get("position")], 6, 300))
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
