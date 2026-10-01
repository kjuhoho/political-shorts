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
