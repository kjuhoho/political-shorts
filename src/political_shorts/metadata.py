"""Step 9 — build the publish metadata sidecar (title / description / tags).

Written next to the mp4 as ``<name>.meta.json`` and consumed by the publishers.
Title picks up the hook energy; the description carries the full source list,
image credits, the disclaimer, and any safety warnings.
"""
from __future__ import annotations

import json
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
    if style == "punchy" and tl:
        # the big on-screen thumbnail title + the real headline for search
        t = f"{' '.join(tl)} | {headline}"
    else:
        t = f"[{date_str} 정치] {headline}"
    # YouTube titles cap at 100 chars; keep room for the tag, which the user
    # wants lowercase and attached with no space:  ...제목#shorts
    return truncate(t, 92).rstrip(" |·-") + "#shorts"


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
    lines.append(f"{date_str} 이슈, 통신·진보·보수 매체를 종합해 30초로 정리했습니다.")
    lines.append("")
    lines.append("▶ 30초 요약")
    _KLABEL = {"outro": "마무리", "hook": "오늘의 이슈"}
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

    images = script.get("images", [])
    if images or (cfg.bgm_enabled and cfg.bgm_credit):
        lines.append("")
        lines.append("■ 이미지·음악 출처 (Creative Commons / 공용)")
        for im in images:
            who = clean_text(im.get("author", "")) or "Unknown"
            lic = im.get("license", "") or "CC"
            ttl = truncate(clean_text(im.get("title", "")), 50) or "image"
            lines.append(f"- {ttl} — {who} ({lic}) {im.get('source_url', '')}")
        if cfg.bgm_enabled and cfg.bgm_credit:
            lines.append(f"- {cfg.bgm_credit}")

    lines.append("")
    lines.append("■ 고지")
    lines.append(script.get("disclaimer", ""))
    if safety.get("warnings"):
        lines.append("")
        lines.append("■ 균형 관련 참고")
        for wmsg in safety["warnings"]:
            lines.append(f"- {wmsg}")

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

    pinned_comment = (
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
