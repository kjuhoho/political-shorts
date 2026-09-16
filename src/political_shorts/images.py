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
from .hook import Entities, Frame, detect_topic, pick_actor
from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("images")

UA = ("political-shorts/1.0 "
      "(https://github.com/kjuhoho/political-shorts; sunlikebe@gmail.com)")
TIMEOUT = 15
MIN_W, MIN_H = 340, 340
MAX_BYTES = 14_000_000

PRESIDENT_NAME = "이재명"          # sitting president — bio page is a standard article
MAX_PORTRAITS = 5                  # people faces per video (user wants portraits to
                                  # dominate; locations are only filler / safe
                                  # backdrops for cards about no one in particular)

# verified: each Korean-Wikipedia page has a raster lead photo on Commons and no
# single identifiable person as its subject. Shuffled per story (see
# collect_images) so consecutive videos don't reuse the same 4 shots.
# NOTE: bare "대법원"/"헌법재판소" resolve to the *US Supreme Court* / *Taiwan
# Judicial Yuan* lead image on ko.wikipedia (generic concept articles), so they
# are deliberately NOT here. Only titles whose ko-wiki lead image is a real
# Korean raster photo belong in this pool.
LOCATION_POOL = [
    "대한민국 국회의사당", "국회의사당역", "여의도", "여의도공원",
    "광화문광장", "서울광장", "청계천",
    "서울특별시청", "경복궁", "숭례문", "국립중앙박물관", "북악산",
    "한강", "서울역",
]
# frame-relevant establishing shots, tried before the shuffled general pool
_FRAME_LOCATION = {
    "scandal": ["광화문광장", "대한민국 국회의사당", "서울광장"],
    "vote": ["대한민국 국회의사당", "국회의사당역", "서울광장"],
    "clash": ["대한민국 국회의사당", "광화문광장", "서울특별시청"],
    "personnel": ["대한민국 국회의사당", "서울특별시청"],
    "poll": ["서울광장", "광화문광장"],
    "remark": ["대한민국 국회의사당", "광화문광장"],
}
# topic-relevant establishing shots (hook.detect_topic), tried BEFORE the
# frame pool above — a story about 북한/평양 gets DMZ/Panmunjom imagery
# instead of a random Seoul landmark that has nothing to do with it (a real
# shipped case: "Seoul Station" signage on screen while the subtitle talked
# about a Pyongyang hospital). Every title here was verified by hand against
# the live ko.wikipedia lead image, not just that it resolves — a bare
# concept title can silently point at the WRONG country's building ("대법원"
# -> US Supreme Court, "헌법재판소" -> Taiwan's Judicial Yuan on ko.wikipedia,
# which is exactly why those two are still deliberately absent). "주한일본
# 대사관" is deliberately excluded too — its verified lead photo includes a
# comfort-women statue in frame, too charged for neutral filler on an
# unrelated story.
_TOPIC_LOCATION = {
    "north_korea": ["판문점", "평양", "임진각", "통일전망대", "휴전선"],
    "un": ["유엔본부"],
    "us": ["주한미국대사관"],
    "china": ["주한중국대사관"],
    "economy": ["기획재정부", "한국은행"],
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
    is_lead: bool = False        # portrait of the story's subject (pick_actor)

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
    if person and s and s.get("type") == "disambiguation":
        # a real shipped case: "김민석" alone is a disambiguation page (a
        # dozen athletes/singers/actors share the name) — the sitting
        # 더불어민주당 대표 (former PM) never got a portrait at all, so a
        # co-mentioned but unrelated person's photo silently became the
        # video's dominant face instead. "(정치인)" is Wikipedia's own
        # standard disambiguator for "politician" — only trusted here when
        # IT resolves to a genuine standard biography (never a guess: if the
        # suffix is itself ambiguous or missing, this still correctly
        # returns None below, same as before).
        s2 = _wp_summary(f"{title} (정치인)")
        if s2 and s2.get("type") == "standard":
            s = s2
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
    entities: Entities, frame: Frame, headline: str, cfg: Settings | None = None,
    body_text: str = "",
) -> list[ImageAsset]:
    cfg = cfg or settings
    if not cfg.image_enabled:
        return []
    cache_dir = Path(cfg.image_cache_dir or "assets/cache/images")
    cache_dir.mkdir(parents=True, exist_ok=True)
    want = max(2, cfg.image_max_count)

    h = clean_text(headline)
    topic = detect_topic(headline, body_text)
    # People to try for a portrait, most-relevant first:
    #   1) the story's lead actor (the poster face — only if a real person named
    #      in the headline; pick_actor falls back to politicians[0] / an
    #      institution for the on-screen label even when nobody is named, and
    #      that must never become the poster face),
    #   2) the sitting president whenever he himself is that headline-named lead,
    #   3) every other politician named in the HEADLINE (earliest first),
    #   4) then politicians named only in the cluster BODY — the user wants
    #      person-centric footage, so related people are fair game for the
    #      interior cards (video._assign_images still only lays a face under a
    #      card that names that person, or the subject on the setup cards).
    # Every candidate still has to resolve to a normal Wikipedia biography with a
    # raster photo (`_resolve(..., person=True)`), so a face is never guessed.
    lead = pick_actor(headline, entities, frame)
    # Only picture people who are KNOWN political figures — their Korean-Wikipedia
    # lead image is a proper portrait. Minor names resolved by shape alone tend to
    # pull an off-topic pre-politics photo (an athlete's medal shot, etc.), so we
    # deliberately fall back to a location for them rather than show that.
    lead_is_person = bool(lead) and (lead in entities.politicians or lead == PRESIDENT_NAME)
    in_head = sorted((n for n in entities.politicians
                      if n and n in h and n != PRESIDENT_NAME), key=h.find)
    # politicians named only in the cluster BODY — related people, capped: they
    # only ever surface on a card that names them (video._assign_images pass 1).
    in_body = [n for n in entities.politicians
               if n and n not in in_head and n != PRESIDENT_NAME and n != lead][:2]
    # `PRESIDENT_NAME in h` used to gate this too — a real shipped case: a
    # presidential press-conference headline read "이 대통령, 18일
    # 기자회견..." (an honorific short form), which never literally contains
    # "이재명", so the president got NO portrait at all and an unrelated
    # politician's photo silently became assets[0] instead. Safe to drop
    # here specifically: there is only ever ONE president, so once
    # `lead == PRESIDENT_NAME` is already true there's no "which person"
    # ambiguity left to guard against the way there still is for `subject`
    # below (entities.lead_actor's politicians[0] fallback can pick a
    # DIFFERENT politician than the one actually in this headline when
    # several are mentioned in the cluster's combined text — that ambiguity
    # is why `subject` still requires `lead in h`).
    want_pres = bool(entities.president) and lead == PRESIDENT_NAME
    subject = lead if (lead_is_person and lead in h) else ""   # the poster face
    names: list[str] = []
    for n in ([subject] if subject else []) + \
             ([PRESIDENT_NAME] if want_pres else []) + in_head + in_body:
        if n and n not in names:
            names.append(n)

    # location titles: TOPIC-specific first (detect_topic — 북한/유엔/미국/…),
    # then frame-specific, then the general pool SHUFFLED with a per-story
    # seed so back-to-back videos don't show the same 4 photos.
    import random as _rnd
    pool = list(LOCATION_POOL)
    _rnd.Random(clean_text(headline)).shuffle(pool)
    locs: list[str] = []
    for t in _TOPIC_LOCATION.get(topic, []) + _FRAME_LOCATION.get(frame.kind, []) + pool:
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
                # the president counts as the lead face whenever want_pres
                # fired, even on stories where `subject` itself stayed ""
                # (headline used an honorific short form) — no ambiguity
                # left to guard against once we already know it's him.
                a.is_lead = (name == subject) or (want_pres and name == PRESIDENT_NAME)
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

    log.info("images: %d [%s]%s for %s",
             len(assets), ", ".join(a.query for a in assets) or "none",
             f" topic={topic}" if topic else "", headline[:44])
    return assets
