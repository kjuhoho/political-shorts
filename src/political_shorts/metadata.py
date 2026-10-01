"""Step 9 — build the publish metadata sidecar (title / description / tags).

Written next to the mp4 as ``<name>.meta.json`` and consumed by the publishers.
Title picks up the hook energy; the description carries the full source list,
image credits, the disclaimer, and any safety warnings.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings, settings
from .textutil import clean_text, truncate

BASE_TAGS = ["정치뉴스", "뉴스요약", "오늘의정치", "국회", "시사", "이슈정리", "shorts", "정치"]
BASE_HASHTAGS = ["#정치뉴스", "#뉴스요약", "#오늘의정치", "#이슈정리", "#shorts"]
ENTITY_HASHTAGS = {
    "이재명": "#이재명", "한동훈": "#한동훈", "조국": "#조국",
    "민주당": "#더불어민주당", "국민의힘": "#국민의힘", "조국혁신당": "#조국혁신당",
}


def _now_local(cfg: Settings) -> datetime:
    try:
        return datetime.now(ZoneInfo(cfg.timezone))
    except Exception:
        return datetime.now(timezone.utc)


def _title(script: dict[str, Any], date_str: str, style: str) -> str:
    headline = clean_text(script.get("headline", "정치 뉴스 요약"))
    tl = [clean_text(x) for x in (script.get("title") or []) if clean_text(x)]
    # ONE clean line, not "{on-screen title} | {raw headline in quotes}" —
    # a real user complaint: '실거주 유예 내년까지 연장할까? | 여당 "내년
    # 까지 연장을"#shorts' reads as two unrelated things stitched together
    # and is hard to parse on a phone. tl (the punchy 2-line on-screen
    # title) is already the whole title on its own; the raw headline used
    # to be appended purely for search-keyword coverage, but that's not
    # worth the readability cost on a Shorts title. tags[]/description
    # still carry the full headline+keywords for search.
    if style == "punchy" and tl:
        t = _curiosity_first(tl)
    else:
        t = f"[{date_str} 정치] {headline}"
    # Channel feedback: a LONG title with a tag glued on is weak on a phone — so the title
    # itself stays short (<=40 chars, curiosity first). The user still wants the lowercase,
    # attached "#shorts" at the very end (…제목#shorts), so that stays; 40 + 7 is well under 100.
    return _cut_words(t, 40).rstrip(" |·-") + "#shorts"


_CURIOUS = re.compile(r"\?|왜|일까|할까|인가|진짜|뭘까|무엇")


def _curiosity_first(lines: list[str]) -> str:
    """Curiosity leads — UNLESS the subject line names a real person or party,
    in which case the name leads and the question follows it.

    Measured 2026-09-30 on 144 Korean-politics Shorts that passed 100k views in
    the previous 30 days (YouTube Data API, ordered by viewCount): 98 of them
    (68%) carry a politician's name or their verbatim words inside the first 12
    characters, and only 35 (24%) contain a "?" at all — of the top 30, just 4
    end on one. Our titles were the exact inverse: 32 of 48 opened on a
    contentless stub ("무슨 일일까요?", "진짜 이유는?", "왜 논란일까?") and
    pushed the only word a scroller recognises — the name — behind it.

    So: "진짜 의도는? 이재명 '노무현 평전' 선물"  ->  "이재명 '노무현 평전'
    선물, 진짜 의도는?".  A faceless story ("실거주 유예 내년까지" /
    "연장할까?") has no name to lead with and keeps curiosity in front.
    """
    if len(lines) < 2 or not _CURIOUS.search(lines[-1]) or _CURIOUS.search(lines[0]):
        return " ".join(lines)
    subject = " ".join(lines[:-1])
    if _names_someone(subject):
        return f"{subject.rstrip(' ,')}, {lines[-1]}"
    return f"{lines[-1]} {subject}"


def _names_someone(text: str) -> bool:
    """True when the line leads with a politician / party / the president —
    the thing a scrolling viewer actually recognises."""
    from .hook import detect_entities

    t = clean_text(text)
    ent = detect_entities(t)
    return bool(ent.politicians or ent.parties or ent.president
                or any(w in t for w in ("대통령", "청와대", "대통령실")))


def _cut_words(text: str, limit: int) -> str:
    """Trim to `limit` chars at a word boundary — never mid-word, no ellipsis."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit] != " " and " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,·")


def build_metadata(
    script: dict[str, Any], safety: dict[str, Any], video_path: Path, cfg: Settings | None = None
) -> dict[str, Any]:
    cfg = cfg or settings
    now = _now_local(cfg)
    date_str = now.strftime("%m월 %d일")
    style = script.get("style", cfg.headline_style)
    headline = clean_text(script.get("headline", "오늘의 정치 이슈"))

    title = _title(script, date_str, style)

    lines: list[str] = []
    lines.append(headline)
    lines.append(f"{now.year}년 {date_str} 정치 이슈를 통신·진보·보수 매체를 종합해, "
                 "정치를 잘 모르는 분도 이해하도록 쉽게 풀어 설명합니다.")
    lines.append("")
    lines.append("▶ 한눈에 보기")
    _KLABEL = {"outro": "마무리", "hook": "오늘의 이슈",
               "factcheck": "확인된 사실", "sides": "갈리는 입장"}
    for seg in script.get("segments", []):
        k = seg.get("kicker") or _KLABEL.get(seg.get("role", ""), "")
        cap = truncate(clean_text(seg.get("caption", "")), 64)
        if k and cap:
            lines.append(f"- {k}: {cap}")
        elif cap:
            lines.append(f"- {cap}")
    lines.append("")
    lines.append("■ 출처 (여러 성향 매체 종합)")
    for s in script.get("sources", []):
        lean = {"left": "(진보 성향)", "right": "(보수 성향)", "wire": "(통신·방송)"}.get(s.get("lean", ""), "")
        lines.append(f"- {s['name']} {lean}: {s['url']}")

    extra = [x for x in (script.get("research_sources") or []) if x.get("url")]
    if extra:
        lines.append("")
        lines.append("■ 추가 조사 자료 (이 영상을 위해 더 읽은 기사)")
        for x in extra[:8]:
            lines.append(f"- {truncate(clean_text(x.get('title', '')), 50) or '기사'}: {x['url']}")

    images = script.get("images", [])
    has_video = any(im.get("kind") == "video" for im in images)
    has_pexels = any((im.get("license", "") == "Pexels") for im in images)
    if images or (cfg.bgm_enabled and cfg.bgm_credit):
        lines.append("")
        head = "■ 영상·이미지·음악 출처" if has_video else "■ 이미지·음악 출처"
        lines.append(f"{head} (Creative Commons / 공용 / Pexels)")
        for im in images:
            who = clean_text(im.get("author", "")) or "Unknown"
            lic = im.get("license", "") or "CC"
            ttl = truncate(clean_text(im.get("title", "")), 50) or "image"
            kind = "영상" if im.get("kind") == "video" else "이미지"
            lines.append(f"- [{kind}] {ttl} — {who} ({lic}) {im.get('source_url', '')}")
        if has_pexels:
            # Pexels API guidelines: show a prominent link to Pexels.
            lines.append("- 영상 클립 제공: Pexels — https://www.pexels.com")
        if cfg.bgm_enabled and cfg.bgm_credit:
            lines.append(f"- {cfg.bgm_credit}")

    lines.append("")
    lines.append("■ 고지")
    lines.append(script.get("disclaimer", ""))
    # safety["warnings"] stays OUT of the description. It is our own production note ("한쪽 정당만 언급됨 —
    # 상대측 반응 보강 권장"), and on 2026-09-28 it shipped to viewers verbatim. It is still kept in the
    # sidecar below ("safety"), where the run log and any later review can read it.

    lines.append("")
    lines.append("이 이슈, 여러분은 어떻게 보시나요? 댓글로 알려주세요.")
    lines.append("")
    ent = script.get("entities", {})
    tags = list(BASE_TAGS)
    hashtags = list(BASE_HASHTAGS)
    for name in (ent.get("politicians", []) + ent.get("parties", [])):
        if name in ENTITY_HASHTAGS and ENTITY_HASHTAGS[name] not in hashtags:
            hashtags.append(ENTITY_HASHTAGS[name])
            tags.append(name)
    if ent.get("president") and "#이재명" not in hashtags:
        hashtags.append("#이재명")
    lines.append(" ".join(hashtags))
    description = "\n".join(lines).strip()

    engage = clean_text(script.get("engage_question", ""))
    pinned_comment = (
        (f"{engage}\n\n" if engage else "") +
        "공개 보도를 쉽게 풀어 정리한 자동 제작 영상입니다. "
        "인용·수치는 설명란 원문 링크에서 꼭 확인해 주세요. "
        "사실 오류·균형 관련 지적은 댓글로 남겨주시면 반영하겠습니다."
    )

    return {
        "title": title,
        "description": description,
        "tags": tags[:400],
        "hashtags": hashtags,
        "category_id": cfg.youtube_category_id,
        "privacy_status": cfg.youtube_privacy_status,
        "made_for_kids": False,
        "pinned_comment": pinned_comment,
        "language": "ko",
        "cluster_id": script.get("cluster_id"),
        "frame": script.get("frame"),
        "topic": script.get("topic", ""),
        "entities": script.get("entities", {}),
        "headline": headline,
        "generated_at": now.isoformat(),
        "safety": {"passed": safety.get("passed"), "warnings": safety.get("warnings", [])},
        "sources": script.get("sources", []),
        "image_credits": [
            {"title": im.get("title"), "author": im.get("author"),
             "license": im.get("license"), "url": im.get("source_url")}
            for im in images
        ],
        "video_file": Path(video_path).name,
    }


def write_sidecar(meta: dict[str, Any], video_path: Path) -> Path:
    side = Path(video_path).with_suffix(".meta.json")
    side.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return side
