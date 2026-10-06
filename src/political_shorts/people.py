"""Who is this person, and may we show their face? — answered by Wikidata, not by a hand-written list.

`hook.POLITICIANS` is a list someone typed. Everyone missing from it got no portrait at all: 강훈식, 한병도,
서영교, 문형배, 강신철 and most of the month's other names were simply invisible to the picture step, so their
stories fell back to a building (live audit 2026-09-30). Keeping the list up to date by hand is exactly the
whack-a-mole this project keeps losing.

So a name found next to a role in the headline ("강훈식 비서실장") is checked against Wikidata, and a photo is
used only when the entry says, unambiguously:

  * the label is EXACTLY that name (no fuzzy match — a namesake is a different person),
  * it is a human (P31 = Q5),
  * a South Korean one (P27 = Q884),
  * in public life (a political/judicial occupation, or any position held), and
  * it has a picture (P18).

Anything short of that: no face. A wrong face is far worse than a building, and this project has shipped one.
Answers (including "no") are cached on disk for a month, so a name costs one lookup, not one per video.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .logging_setup import get_logger

log = get_logger("people")

API = "https://www.wikidata.org/w/api.php"
HUMAN = "Q5"
SOUTH_KOREA = "Q884"
# politician, judge, civil servant, prosecutor, lawyer, diplomat, journalist-turned-official, businessperson
PUBLIC_LIFE = {"Q82955", "Q16533", "Q212238", "Q600751", "Q40348", "Q193391", "Q1930187", "Q43845"}
CACHE = "people_cache.json"
CACHE_DAYS = 30.0
CACHE_VER = "v2"              # bumped when the collection widens, so a cached short list isn't the answer


def _cache_path(cfg: Any) -> Path:
    return Path(getattr(cfg, "data_dir", "data")) / CACHE


def _load(cfg: Any) -> dict[str, Any]:
    try:
        return json.loads(_cache_path(cfg).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(cfg: Any, data: dict[str, Any]) -> None:
    p = _cache_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def _ids(claims: dict, prop: str) -> set[str]:
    out = set()
    for c in claims.get(prop, []) or []:
        v = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if isinstance(v, dict) and v.get("id"):
            out.add(v["id"])
    return out


def _values(claims: dict, prop: str) -> list[str]:
    return [(c.get("mainsnak") or {}).get("datavalue", {}).get("value")
            for c in claims.get(prop, []) or []
            if isinstance((c.get("mainsnak") or {}).get("datavalue", {}).get("value"), str)]


def _ask_wikidata(name: str, session: Any) -> dict[str, Any]:
    """{'image': 'File name.jpg', 'id': 'Q…', 'desc': …} or {} when this is not a public figure we can show."""
    r = session.get(API, params={"action": "wbsearchentities", "search": name, "language": "ko",
                                 "uselang": "ko", "format": "json", "limit": 5, "type": "item"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"wikidata search {r.status_code}")
    for hit in r.json().get("search", []):
        if hit.get("label") != name:                      # exact label only
            continue
        e = session.get(API, params={"action": "wbgetentities", "ids": hit["id"],
                                     "props": "claims|descriptions", "languages": "ko|en",
                                     "format": "json"}, timeout=20)
        if e.status_code != 200:
            raise RuntimeError(f"wikidata entity {e.status_code}")
        ent = (e.json().get("entities") or {}).get(hit["id"], {})
        claims = ent.get("claims", {})
        if HUMAN not in _ids(claims, "P31") or SOUTH_KOREA not in _ids(claims, "P27"):
            continue
        in_public_life = bool(_ids(claims, "P106") & PUBLIC_LIFE) or bool(claims.get("P39"))
        images = _values(claims, "P18")
        if not in_public_life or not images:
            continue
        desc = ((ent.get("descriptions") or {}).get("ko")
                or (ent.get("descriptions") or {}).get("en") or {}).get("value", "")
        return {"id": hit["id"], "image": images[0], "desc": desc}
    return {}


COMMONS = "https://commons.wikimedia.org/w/api.php"
# files in a person's category that are not a picture OF them
_NOT_A_PHOTO = ("signature", "서명", "성적표", "합격", "logo", "로고", "map", "지도", "seal", "coat of arms",
                "flag", "국기", "emblem", "문서", "document", "certificate", "chart", "graph", "poster")


def _entity_of_article(title: str, session: Any) -> str:
    """The Wikidata id of a ko.wikipedia article — the only safe way to the right person, because the Korean
    label alone is ambiguous: a plain search for '이재명' returns a voice actor before the president."""
    r = session.get("https://ko.wikipedia.org/w/api.php",
                    params={"action": "query", "prop": "pageprops", "titles": title, "format": "json"},
                    timeout=20)
    if r.status_code != 200:
        return ""
    for p in (r.json().get("query") or {}).get("pages", {}).values():
        qid = (p.get("pageprops") or {}).get("wikibase_item")
        if qid:
            return str(qid)
    return ""


def norm_file(name: str) -> str:
    """One spelling for one file: Wikidata gives 'A B.jpg', a thumbnail URL gives 'A_B.jpg', and the same
    photo was collected twice (checked live, 2026-10-06)."""
    return re.sub(r"\s+", " ", str(name or "").replace("_", " ")).strip()


def _usable(name: str) -> bool:
    low = name.lower()
    return low.endswith((".jpg", ".jpeg", ".png")) and not any(w in low for w in _NOT_A_PHOTO)


def _category_photos(category: str, session: Any, limit: int = 40) -> list[str]:
    """Photos anywhere under a person's Commons category (deepcat covers the per-year subcategories, where
    most of a politician's pictures actually live)."""
    r = session.get(COMMONS, params={"action": "query", "list": "search",
                                     "srsearch": f'deepcat:"{category}" filetype:bitmap',
                                     "srnamespace": 6, "srlimit": limit, "format": "json"}, timeout=25)
    if r.status_code != 200 or "error" in r.json():
        return []
    out = []
    for hit in (r.json().get("query") or {}).get("search", []):
        name = str(hit.get("title", ""))[5:]
        if _usable(name):
            out.append(name)
    return out


def _category_members(category: str, session: Any, limit: int = 50) -> list[str]:
    """The files sitting DIRECTLY in the category — deepcat is a search index and quietly misses files (and
    returns nothing at all when the extension is unavailable), so the plain listing runs beside it."""
    r = session.get(COMMONS, params={"action": "query", "list": "categorymembers",
                                     "cmtitle": f"Category:{category}", "cmtype": "file",
                                     "cmlimit": limit, "format": "json"}, timeout=25)
    if r.status_code != 200:
        return []
    out = []
    for m in ((r.json().get("query") or {}).get("categorymembers") or []):
        name = str(m.get("title", ""))[5:]
        if _usable(name):
            out.append(name)
    return out


def _name_tokens(labels: list[str]) -> list[list[str]]:
    """['이재명', 'Lee Jae-myung'] -> [['이재명'], ['lee', 'jae', 'myung']] — the forms a Commons file name
    uses for this person."""
    out = []
    for lab in labels:
        parts = [p for p in re.split(r"[\s\-_.()]+", str(lab).lower()) if len(p) > 1]
        if parts:
            out.append(parts)
    return out


def _mentions(filename: str, tokens: list[list[str]]) -> bool:
    low = re.sub(r"[\s\-_.()]+", " ", filename.lower())
    return any(all(p in low for p in parts) for parts in tokens)


def _article_images(title: str, tokens: list[list[str]], session: Any, limit: int = 40) -> list[str]:
    """Pictures used in the person's OWN ko.wikipedia article. A biography also illustrates other people
    (a rival at the same debate), and this project has shipped a wrong face before, so a file is taken only
    when its own name says it is this person."""
    if not tokens:
        return []
    r = session.get("https://ko.wikipedia.org/w/api.php",
                    params={"action": "query", "prop": "images", "titles": title,
                            "imlimit": limit, "format": "json"}, timeout=20)
    if r.status_code != 200:
        return []
    out = []
    for p in ((r.json().get("query") or {}).get("pages") or {}).values():
        for im in p.get("images", []) or []:
            name = str(im.get("title", ""))[5:]
            if _usable(name) and _mentions(name, tokens):
                out.append(name)
    return out


# a file name that reads like a meeting or a summit is usually two or more people in frame; those are kept,
# but they go behind the ones that are plainly this person
_GROUP_SHOT = ("meets", "meeting", "with ", " and ", "summit", "회담", "접견", "면담", "정상", "ceremony",
               "attends", "visit", "session", "cabinet",
               # Commons file names are in whatever language the uploader used
               "首脳会談", "会談", "首相", "大統領", "共同", "오찬", "만찬", "환담", "악수")
_SOLO = ("portrait", "초상", "프로필", "profile")


def _solo_first(files: list[str]) -> list[str]:
    """Portraits first, group shots last — same files, better order."""
    def rank(f: str) -> int:
        low = f.lower()
        if any(w in low for w in _SOLO):
            return 0
        return 2 if any(w in low for w in _GROUP_SHOT) else 1
    return sorted(files, key=rank)


def is_person(article_title: str, cfg: Any, session: Any = None) -> bool:
    """Is this ko.wikipedia article about a human? Unknown counts as yes — a lookup that fails must not throw
    away a portrait we already resolved. Only a positive "this is not a person" is acted on, which is what
    catches '조국' resolving to the article about the fatherland (checked live, 2026-10-06)."""
    article_title = (article_title or "").strip()
    if not article_title:
        return True
    cache = _load(cfg)
    key = f"human::{article_title}"
    hit = cache.get(key)
    if isinstance(hit, dict) and time.time() - float(hit.get("ts", 0)) < CACHE_DAYS * 86400:
        return bool(hit.get("human", True))
    if session is None:
        from .images import _S as session
    try:
        qid = _entity_of_article(article_title, session)
        if not qid:
            return True
        r = session.get(API, params={"action": "wbgetentities", "ids": qid, "props": "claims",
                                     "format": "json"}, timeout=20)
        if r.status_code != 200:
            return True
        claims = ((r.json().get("entities") or {}).get(qid) or {}).get("claims", {})
        if not claims.get("P31"):
            return True
        human = HUMAN in _ids(claims, "P31")
    except Exception as exc:
        log.debug("human check failed for %s: %s", article_title, exc)
        return True
    cache[key] = {"ts": time.time(), "human": human}
    _save(cfg, cache)
    if not human:
        log.info("article %s is not a person — no face from it", article_title)
    return human


def photo_files(name: str, cfg: Any, article_title: str = "", session: Any = None) -> list[str]:
    """Every usable photo of this person, best first — the article's own lead image, then their Commons
    category. One photo per person meant the same shot in every video (user, 2026-10-06)."""
    name = (name or "").strip()
    if not name:
        return []
    cache = _load(cfg)
    key = f"photos::{CACHE_VER}::{article_title or name}"
    hit = cache.get(key)
    if isinstance(hit, dict) and time.time() - float(hit.get("ts", 0)) < CACHE_DAYS * 86400:
        return list(hit.get("files") or [])
    if session is None:
        from .images import _S as session
    files: list[str] = []
    try:
        lead = photo_file(name, cfg, session) if not article_title else ""
        if lead:
            files.append(lead)
        qid = _entity_of_article(article_title, session) if article_title else ""
        cats: list[str] = []
        tokens: list[list[str]] = [[name.lower()]] if name else []
        if qid:
            r = session.get(API, params={"action": "wbgetentities", "ids": qid,
                                         "props": "claims|sitelinks|labels", "languages": "ko|en",
                                         "format": "json"}, timeout=20)
            ent = ((r.json().get("entities") or {}).get(qid) or {}) if r.status_code == 200 else {}
            claims = ent.get("claims", {})
            labels = [(v or {}).get("value", "") for v in (ent.get("labels") or {}).values()]
            tokens = _name_tokens([l for l in labels if l] or [name])
            for f in _values(claims, "P18"):
                if f not in files:
                    files.append(f)
            cats = list(_values(claims, "P373"))
            # the Commons category is often only a sitelink, with no P373 claim at all
            sl = str(((ent.get("sitelinks") or {}).get("commonswiki") or {}).get("title", ""))
            if sl.startswith("Category:") and sl[9:] not in cats:
                cats.append(sl[9:])
        for cat in cats:
            for f in _category_photos(cat, session) + _category_members(cat, session):
                if f not in files:
                    files.append(f)
        for f in _article_images(article_title, tokens, session) if article_title else []:
            if f not in files:
                files.append(f)
    except Exception as exc:
        log.debug("photo list failed for %s: %s", name, exc)
        return files
    files = list(dict.fromkeys(norm_file(f) for f in files if f))
    files = _solo_first(files)
    cache[key] = {"ts": time.time(), "files": files}
    _save(cfg, cache)
    log.info("portraits available for %s: %d", article_title or name, len(files))
    return files


def photo_file(name: str, cfg: Any, session: Any = None) -> str:
    """The Commons file name of this person's photo, or '' — cached, including the empty answer."""
    name = (name or "").strip()
    if not (2 <= len(name) <= 4):
        return ""
    cache = _load(cfg)
    hit = cache.get(name)
    if isinstance(hit, dict) and time.time() - float(hit.get("ts", 0)) < CACHE_DAYS * 86400:
        return str(hit.get("image", ""))
    if session is None:
        from .images import _S as session                 # the project's session carries the required UA
    try:
        found = _ask_wikidata(name, session)
    except Exception as exc:
        log.debug("wikidata lookup failed for %s: %s", name, exc)
        return str(hit.get("image", "")) if isinstance(hit, dict) else ""
    cache[name] = {"ts": time.time(), "image": found.get("image", ""), "id": found.get("id", ""),
                   "desc": found.get("desc", "")}
    _save(cfg, cache)
    if found.get("image"):
        log.info("portrait source: %s -> %s (%s)", name, found["image"], found.get("desc", "")[:40])
    return str(found.get("image", ""))
