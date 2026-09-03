"""Keyless, free image collection — Korean Wikipedia + Wikimedia Commons.

Sources, all deterministic and safe for an unattended public channel:
  * a rotating pool of Korea-politics LOCATIONS with real photos (National
    Assembly building, Yeouido skyline, Gwanghwamun Square, Yeouido park) — no
    identifiable individual is the subject, always relevant, never wrong.
  * a PORTRAIT of each politician named in the headline, taken from the lead
    image of their Korean Wikipedia article — but only when that article is a
    normal biography (``type == "standard"``) with a raster photo. Ambiguous
    names (disambiguation pages, e.g. 김용범) and missing articles resolve to
    nothing, so a face is never guessed.

Together a typical video gets 4-6 *different* real photos, one per card.
Cards left without a photo fall back to the drawn iconographic backdrop.
No API keys, no signup, no cost.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote

import requests

from .config import Settings, settings
from .hook import Entities, Frame, pick_actor
from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("images")

UA = ("political-shorts/1.0 "
      "(https://github.com/kjuhoho/political-shorts; sunlikebe@gmail.com)")
TIMEOUT = 15
MIN_W, MIN_H = 340, 340
MAX_BYTES = 14_000_000

PRESIDENT_NAME = "이재명"          # sitting president — bio page is a standard article
MAX_PORTRAITS = 3                  # people faces per video (rest are locations)

# verified: each Korean-Wikipedia page has a raster lead photo on Commons and no
# single identifiable person as its subject. Shuffled per story (see
# collect_images) so consecutive videos don't reuse the same 4 shots.
LOCATION_POOL = [
    "대한민국 국회의사당", "국회의사당역", "여의도", "여의도공원",
    "광화문광장", "서울광장", "청계천", "대법원", "헌법재판소",
    "서울특별시청", "경복궁", "숭례문", "국립중앙박물관", "북악산",
    "한강", "서울역",
]
# frame-relevant establishing shots, tried before the shuffled general pool
_FRAME_LOCATION = {
    "scandal": ["대법원", "헌법재판소", "광화문광장"],
    "vote": ["대한민국 국회의사당", "국회의사당역", "서울광장"],
    "clash": ["대한민국 국회의사당", "광화문광장", "서울특별시청"],
    "personnel": ["대한민국 국회의사당", "서울특별시청"],
    "poll": ["서울광장", "광화문광장"],
    "remark": ["대한민국 국회의사당", "광화문광장"],
}

_S = requests.Session()
_S.headers["User-Agent"] = UA


@dataclass
class ImageAsset:
    path: str
    title: str
    author: str
    license: str
    source_url: str
    query: str
    kind: str = "photo"          # photo | portrait
    width: int = 0
    height: int = 0

    def credit_line(self) -> str:
        return f"{self.title or 'image'} — {self.author or 'Unknown'} ({self.license or 'CC'}) {self.source_url}".strip()


# --------------------------------------------------------------------------- #
# wikipedia / commons lookups
# --------------------------------------------------------------------------- #
def _wp_summary(title: str) -> dict | None:
    for _ in range(3):
        try:
            r = _S.get(f"https://ko.wikipedia.org/api/rest_v1/page/summary/{title}", timeout=TIMEOUT)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json()
            if r.status_code in (404, 302):
                return None
        except Exception:
            pass
        time.sleep(0.8)
    return None


def _commons_info(filename: str) -> dict | None:
    try:
        r = _S.get(
            "https://commons.wikimedia.org/w/api.php",
            params={"action": "query", "titles": f"File:{filename}",
                    "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size",
                    "iiurlwidth": "1600", "format": "json"},
            timeout=TIMEOUT,
        )
        if not r.text.lstrip().startswith("{"):
            return None
        pages = list((r.json().get("query") or {}).get("pages", {}).values())
        info = (pages[0].get("imageinfo") or [None])[0] if pages else None
        if not info or info.get("mime") not in ("image/jpeg", "image/png"):
            return None
        meta = info.get("extmetadata") or {}
        return {
            "url": (info.get("thumburl") or info.get("url") or "").split("?")[0],
            "author": clean_text(re.sub("<[^>]+>", "", (meta.get("Artist") or {}).get("value", ""))),
            "license": (meta.get("LicenseShortName") or {}).get("value", "") or "CC",
            "source_url": info.get("descriptionurl", ""),
            "width": info.get("width", 0), "height": info.get("height", 0),
        }
    except Exception:
        return None


def _resolve(title: str, person: bool) -> dict | None:
    s = _wp_summary(title)
    if not s or s.get("type") != "standard":
        return None
    img = (s.get("originalimage") or {}).get("source") or (s.get("thumbnail") or {}).get("source", "")
    img = img.split("?")[0]
    if not img or ".svg" in img.lower() or "/commons/" not in img:
        return None
    filename = unquote(img.split("/commons/")[-1].split("/")[-1])
    filename = re.sub(r"^\d+px-", "", filename)
    if filename.startswith("thumb"):
        filename = unquote(img).split("/")[-2]
    info = _commons_info(filename)
    if not info:
        return None
    if person and info["width"] and info["width"] < 300:
        return None
    info["title"] = clean_text(re.sub(r"\.\w+$", "", filename).replace("_", " "))
    return info


# --------------------------------------------------------------------------- #
# download + cache
# --------------------------------------------------------------------------- #
def _download(url: str, cache_dir: Path) -> tuple[Path, int, int] | None:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    hit = cache_dir / f"{key}.jpg"
    if hit.exists() and hit.stat().st_size > 4096:
        try:
            from PIL import Image
            with Image.open(hit) as im:
                return hit, im.width, im.height
        except Exception:
            return hit, 0, 0
    try:
        r = _S.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.content
        if not (4096 < len(data) < MAX_BYTES):
            return None
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if im.width < MIN_W or im.height < MIN_H:
            return None
        im.convert("RGB").save(hit, "JPEG", quality=88)
        return hit, im.width, im.height
    except Exception as exc:
        log.debug("download failed %s: %s", url, exc)
        return None


def _make_asset(info: dict, query: str, kind: str, cache_dir: Path) -> ImageAsset | None:
    got = _download(info["url"], cache_dir)
    if not got:
        return None
    path, w, h = got
    a = ImageAsset(path=str(path), title=info.get("title", query), author=info["author"],
                   license=info["license"], source_url=info["source_url"], query=query,
                   kind=kind, width=w or info.get("width", 0), height=h or info.get("height", 0))
    path.with_suffix(".json").write_text(json.dumps(asdict(a), ensure_ascii=False), encoding="utf-8")
    return a


def collect_images(
    entities: Entities, frame: Frame, headline: str, cfg: Settings | None = None
) -> list[ImageAsset]:
    cfg = cfg or settings
    if not cfg.image_enabled:
        return []
    cache_dir = Path(cfg.image_cache_dir or "assets/cache/images")
    cache_dir.mkdir(parents=True, exist_ok=True)
    want = max(2, cfg.image_max_count)

    h = clean_text(headline)
    # People to try for a portrait, most-relevant first:
    #   1) the story's lead actor,
    #   2) the sitting president whenever the piece is president-related,
    #   3) anyone else named anywhere in the cluster (headline names first).
    # Every candidate still has to resolve to a normal Wikipedia biography with a
    # raster photo (`_resolve(..., person=True)`), so a face is never guessed.
    lead = pick_actor(headline, entities, frame)
    # Only picture PEOPLE the story is actually ABOUT: the lead actor (iff it's a
    # real person named in the headline — `pick_actor` falls back to
    # politicians[0] / an institution for the on-screen label even when nobody is
    # named, and that must never become the poster face), then any other
    # politician named in the headline (earliest first). The president gets a
    # portrait only when he himself is that headline-named lead.
    lead_is_person = bool(lead) and (lead in entities.politicians or lead == PRESIDENT_NAME)
    in_head = sorted((n for n in entities.politicians
                      if n and n in h and n != PRESIDENT_NAME), key=h.find)
    want_pres = (bool(entities.president) and lead == PRESIDENT_NAME
                 and PRESIDENT_NAME in h)
    names: list[str] = []
    for n in ([lead] if lead_is_person and lead in h else []) + \
             ([PRESIDENT_NAME] if want_pres else []) + in_head:
        if n and n not in names:
            names.append(n)

    # location titles: frame-specific first, then the general pool SHUFFLED with
    # a per-story seed so back-to-back videos don't show the same 4 photos.
    import random as _rnd
    pool = list(LOCATION_POOL)
    _rnd.Random(clean_text(headline)).shuffle(pool)
    locs: list[str] = []
    for t in _FRAME_LOCATION.get(frame.kind, []) + pool:
        if t not in locs:
            locs.append(t)

    assets: list[ImageAsset] = []
    used: set[str] = set()

    n_portraits = 0
    for name in names:
        if len(assets) >= want or n_portraits >= MAX_PORTRAITS:
            break
        info = _resolve(name, person=True)
        if info and info["url"] not in used:
            a = _make_asset(info, name, "portrait", cache_dir)
            if a:
                assets.append(a)
                used.add(info["url"])
                n_portraits += 1

    for title in locs:
        if len(assets) >= want:
            break
        info = _resolve(title, person=False)
        if info and info["url"] not in used:
            a = _make_asset(info, title, "photo", cache_dir)
            if a:
                assets.append(a)
                used.add(info["url"])

    log.info("images: %d [%s] for %s",
             len(assets), ", ".join(a.query for a in assets) or "none", headline[:44])
    return assets
