"""Independent title and metadata selection for Today's Enter longform.

This module deliberately has no import from the shorts package.  It consumes
only a plain longform plan dictionary and emits a reproducible title package
for the longform renderer/uploader.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


MAX_TITLE_CHARS = 55
BLOCKED_PHRASES = (
    "충격", "발칵", "경악", "폭로", "단독", "완전", "끝장", "참패",
    "사이다", "충돌", "대폭발", "반드시 봐야",
)


@dataclass(frozen=True)
class TitleCandidate:
    title: str
    score: int
    reasons: list[str]


@dataclass(frozen=True)
class TitlePackage:
    title: str
    description: str
    tags: list[str]
    candidates: list[TitleCandidate]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip(" ·|-—")
    return text.replace('"', "").replace("'", "")


def _shorten(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit + 1)
    return (text[:cut] if cut >= max(8, limit - 12) else text[:limit]).rstrip(" ·,|")


def _chapter_topics(plan: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for chapter in plan.get("chapters", []) or []:
        headline = _clean(chapter.get("headline", ""))
        if headline and headline not in items:
            items.append(headline)
    return items[:3]


def _score(title: str, theme: str, topics: list[str]) -> TitleCandidate:
    reasons: list[str] = []
    score = 100
    if not (20 <= len(title) <= MAX_TITLE_CHARS):
        score -= 18
        reasons.append("권장 제목 길이(20~55자)를 벗어남")
    if any(word in title for word in BLOCKED_PHRASES):
        score -= 40
        reasons.append("자극적 표현 포함")
    if theme and any(token in title for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", theme)):
        reasons.append("주제 키워드 포함")
    else:
        score -= 25
        reasons.append("주제 키워드가 제목에 없음")
    if "오늘" in title or "브리핑" in title or "정리" in title:
        reasons.append("브리핑 형식이 명확함")
    else:
        score -= 8
        reasons.append("브리핑 형식이 불명확함")
    if topics and not any(token in title for topic in topics for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", topic)):
        score -= 8
        reasons.append("핵심 이슈와 직접 연결이 약함")
    return TitleCandidate(title=title, score=max(0, score), reasons=reasons)


def build_title_package(plan: dict[str, Any], *, channel_name: str = "오늘의엔터") -> TitlePackage:
    """Return the highest-scoring neutral title plus publish-ready metadata."""
    theme = _clean(plan.get("theme", ""))
    topics = _chapter_topics(plan)
    lead = _shorten(topics[0] if topics else theme, 31)
    subject = _shorten(theme or lead, 25)
    candidates_raw = [
        f"{subject} | 오늘 정치 5분 브리핑",
        f"오늘 정치 5분 정리 | {lead}",
        f"{subject}, 지금 확인할 핵심 쟁점 3가지",
    ]
    unique_titles = list(dict.fromkeys(_shorten(title, MAX_TITLE_CHARS) for title in candidates_raw))
    candidates = sorted((_score(title, theme, topics) for title in unique_titles),
                        key=lambda item: item.score, reverse=True)
    chosen = candidates[0]
    source_lines: list[str] = []
    for chapter in plan.get("chapters", []) or []:
        for source in chapter.get("sources", []) or []:
            name, url = _clean(source.get("name", "")), _clean(source.get("url", ""))
            if name and url:
                line = f"- {name}: {url}"
                if line not in source_lines:
                    source_lines.append(line)
    agenda = "\n".join(f"{i + 1}. {_shorten(topic, 68)}" for i, topic in enumerate(topics))
    description = (
        f"{channel_name}의 진영 없는 정치 브리핑입니다.\n\n"
        f"오늘의 핵심 이슈\n{agenda or '- 확인된 주요 정치 이슈'}\n\n"
        "영상에서는 확인된 사실, 각 주체의 주장, 이후 관전 포인트를 구분해 설명합니다.\n"
        "원문 출처\n" + ("\n".join(source_lines) or "- 제작 시 원문 출처를 추가합니다.")
    )
    tags = ["오늘의엔터", "정치브리핑", "정치뉴스", "팩트체크", "뉴스요약"]
    return TitlePackage(title=chosen.title, description=description, tags=tags, candidates=candidates)
