"""Keyless / free b-roll VIDEO for the context (non-person) cards.

Person cards keep their punch-in still portrait — that's the convention real
high-view shorts use. Only the establishing / location cards get motion here.

Sources, in priority order:
  1. Wikimedia Commons video  — keyless, CC-licensed, has some real Korean
     politics scenes (국회의사당, 본회의장, 서울 거리). Thin/uneven, so it
     often returns nothing and we fall back to a still photo.
  2. Pexels video  — needs a FREE api key (`PEXELS_API_KEY`; no per-use cost).
     Generic stock only (flags, parliament exteriors, city, ballots), never a
     named Korean person.

Everything is best-effort: any failure → fewer/zero clips → the renderer uses
the existing still image / drawn backdrop for that card. Nothing here can make
a render fail.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import asdict
from pathlib import Path

import requests

from .config import Settings, settings
from .images import ImageAsset
from .hook import Entities, Frame
from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("footage")

UA = ("political-shorts/1.0 "
      "(https://github.com/kjuhoho/political-shorts; sunlikebe@gmail.com)")
TIMEOUT = 20

# Commons is OFF by default (broll_allow_commons): its Korean-politics video is
# thin AND skewed to the Dec-2024 martial-law night — broadcast rips with a
# station "LIVE" bug, dramatic, charged. Only calm establishing shots are wanted
# here, so searches stay on buildings/skylines and results are title-filtered.
_COMMONS_TERMS = {
    "vote": ["National Assembly Building of South Korea", "여의도 국회의사당"],
    "clash": ["National Assembly Building of South Korea"],
    "scandal": ["Supreme Court of Korea building", "Seoul courthouse exterior"],
    "personnel": ["Government Complex Sejong", "여의도 국회의사당"],
    "poll": ["Gwanghwamun Square", "Seoul street timelapse"],
    "remark": ["National Assembly Building of South Korea"],
}
_COMMONS_GENERIC = ["Seoul skyline", "Seoul city timelapse", "Han River Seoul",
                    "여의도 국회의사당", "Gwanghwamun Square"]
# any hit whose file title matches this is dropped (charged / branded / people)
_COMMONS_BLOCK = re.compile(
    r"martial|계엄|비상계엄|protest|rally|시위|집회|demonstrat|climb|fence|펜스|"
    r"생중계|live\b|뉴스|news|broadcast|briefing|press|기자회견|"
    r"이재명|윤석열|한동훈|조국|김건희|이준석|장동혁", re.I)

# Pexels stock queries — generic, safe, never a named person. Every term is
# Korea/Seoul-anchored: bare "government building" / "courthouse" pull Western
# (often Roman) architecture that looks wrong on a Korean politics short.
_PEXELS_TERMS = {
    "vote": ["korea national assembly", "seoul government building", "korea parliament"],
    "clash": ["seoul national assembly", "korea government building"],
    "scandal": ["seoul courthouse", "korea government building", "seoul city night"],
    "personnel": ["seoul government building", "korea government office"],
    "poll": ["seoul street crowd", "seoul people walking", "korea city street"],
    "remark": ["korea press conference", "seoul podium press", "korea news briefing"],
}
_PEXELS_GENERIC = ["seoul south korea city", "korean flag waving",
                   "seoul cityscape", "seoul street aerial", "seoul skyline night"]

_S = requests.Session()
_S.headers["User-Agent"] = UA


# --------------------------------------------------------------------------- #
# download + cache
# --------------------------------------------------------------------------- #
def _download(url: str, cache_dir: Path, max_bytes: int, suffix: str) -> Path | None:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    hit = cache_dir / f"{key}{suffix}"
    if hit.exists() and hit.stat().st_size > 65536:
        return hit
    try:
        with _S.get(url, timeout=TIMEOUT, stream=True) as r:
            r.raise_for_status()
            clen = int(r.headers.get("content-length") or 0)
            if clen and clen > max_bytes:
                log.debug("skip %s (%d bytes > cap)", url, clen)
                return None
            tmp = hit.with_suffix(hit.suffix + ".part")
            total = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(262144):
                    total += len(chunk)
                    if total > max_bytes:
                        fh.close(); tmp.unlink(missing_ok=True)
                        log.debug("skip %s (streamed past cap)", url)
                        return None
                    fh.write(chunk)
            if total < 65536:
                tmp.unlink(missing_ok=True)
                return None
            tmp.replace(hit)
            return hit
    except Exception as exc:
        log.debug("download failed %s: %s", url, exc)
        return None


def _asset(path: Path, title: str, author: str, license_: str, source_url: str,
           query: str) -> ImageAsset:
    a = ImageAsset(path=str(path), title=clean_text(title or "b-roll"),
                   author=clean_text(author or ""), license=license_ or "CC",
                   source_url=source_url or "", query=query, kind="video")
    try:
        path.with_suffix(path.suffix + ".json").write_text(
            json.dumps(asdict(a), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return a


# --------------------------------------------------------------------------- #
# Wikimedia Commons  (keyless)
# --------------------------------------------------------------------------- #
def _commons_videos(term: str, cap_bytes: int, cache_dir: Path) -> list[ImageAsset]:
    try:
        r = _S.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json",
                "generator": "search", "gsrnamespace": "6", "gsrlimit": "10",
                "gsrsearch": f'{term} filemime:video/webm',
                "prop": "imageinfo",
                "iiprop": "url|size|mediatype|mime|extmetadata|user",
            },
            timeout=TIMEOUT,
        )
        if not r.text.lstrip().startswith("{"):
            return []
        pages = (r.json().get("query") or {}).get("pages", {}) or {}
    except Exception as exc:
        log.debug("commons search failed (%s): %s", term, exc)
        return []

    out: list[ImageAsset] = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [None])[0]
        if not ii or ii.get("mediatype") != "VIDEO":
            continue
        size = ii.get("size") or 0
        if size and size > cap_bytes:
            continue
        # skip low-res sources — they look blocky upscaled to a 1080x1920 short
        if (ii.get("width") or 0) < 900 or (ii.get("height") or 0) < 600:
            continue
        if _COMMONS_BLOCK.search(p.get("title", "")):
            log.debug("commons: drop charged/branded clip %s", p.get("title"))
            continue
        url = (ii.get("url") or "").split("?")[0]
        if not url.lower().endswith((".webm", ".ogv", ".mp4")):
            continue
        meta = ii.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "") or "CC"
        got = _download(url, cache_dir, cap_bytes, Path(url).suffix.lower() or ".webm")
        if not got:
            continue
        out.append(_asset(
            got, title=re.sub(r"^File:", "", p.get("title", "")),
            author=re.sub("<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")) or ii.get("user", ""),
            license_=lic, source_url=ii.get("descriptionurl", url), query=term))
        break                                    # one good clip per term is plenty
    return out


# --------------------------------------------------------------------------- #
# Pexels  (free api key)
# --------------------------------------------------------------------------- #
def _pexels_videos(term: str, key: str, cap_bytes: int, cache_dir: Path) -> list[ImageAsset]:
    try:
        r = _S.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": term, "per_page": 5, "orientation": "portrait", "size": "medium"},
            timeout=TIMEOUT,
        )
        if r.status_code != 200 or not r.text.lstrip().startswith("{"):
            log.debug("pexels %s -> %s", term, r.status_code)
            return []
        vids = r.json().get("videos") or []
    except Exception as exc:
        log.debug("pexels search failed (%s): %s", term, exc)
        return []

    for v in vids:
        files = sorted(
            (f for f in v.get("video_files", []) if (f.get("height") or 0) >= 900),
            key=lambda f: (f.get("height") or 0),
        )
        pick = files[0] if files else None
        if not pick or not pick.get("link"):
            continue
        got = _download(pick["link"], cache_dir, cap_bytes, ".mp4")
        if not got:
            continue
        return [_asset(
            got, title=term, author=(v.get("user") or {}).get("name", "Pexels"),
            license_="Pexels", source_url=v.get("url", ""), query=term)]
    return []


# --------------------------------------------------------------------------- #
# public
# --------------------------------------------------------------------------- #
def collect_footage(
    entities: Entities, frame: Frame, headline: str, cfg: Settings | None = None
) -> list[ImageAsset]:
    cfg = cfg or settings
    if not getattr(cfg, "broll_enabled", False):
        return []
    cache_dir = Path(cfg.broll_cache_dir or "assets/cache/footage")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cap_bytes = max(4, int(getattr(cfg, "broll_max_mb", 40))) * 1_000_000
    want = max(1, int(getattr(cfg, "broll_max_count", 3)))

    rnd = random.Random(clean_text(headline))
    pexels_terms = list(dict.fromkeys(
        _PEXELS_TERMS.get(frame.kind, []) + _PEXELS_GENERIC))
    rnd.shuffle(pexels_terms)

    assets: list[ImageAsset] = []
    seen: set[str] = set()

    # 1) Pexels — the primary source (clean generic stock, no station bugs).
    key = (getattr(cfg, "pexels_api_key", "") or "").strip()
    if key:
        for term in pexels_terms:
            if len(assets) >= want:
                break
            for a in _pexels_videos(term, key, cap_bytes, cache_dir):
                if a.path not in seen:
                    assets.append(a); seen.add(a.path)
            time.sleep(0.3)

    # 2) Commons — opt-in only; thin + skewed to charged broadcast footage.
    if getattr(cfg, "broll_allow_commons", False) and len(assets) < want:
        commons_terms = list(dict.fromkeys(
            _COMMONS_TERMS.get(frame.kind, []) + _COMMONS_GENERIC))
        for term in commons_terms:
            if len(assets) >= want:
                break
            for a in _commons_videos(term, cap_bytes, cache_dir):
                if a.path not in seen:
                    assets.append(a); seen.add(a.path)
            time.sleep(0.3)

    log.info("footage: %d clip(s) [%s] for %s",
             len(assets), ", ".join(a.query for a in assets) or "none", headline[:44])
    return assets[:want]


if __name__ == "__main__":                       # tiny smoke test
    import dataclasses
    import sys
    from .hook import detect_entities, detect_frame
    hl = sys.argv[1] if len(sys.argv) > 1 else "국회 본회의서 예산안 처리"
    cfg = dataclasses.replace(settings, broll_enabled=True, broll_allow_commons=True)
    got = collect_footage(detect_entities(hl), detect_frame(hl), hl, cfg)
    for a in got:
        print(f"{a.query:>28}  {a.license:<12}  {a.path}")
