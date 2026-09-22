"""Daily, standalone longform production worker.

This worker reads public RSS metadata, asks Groq to write only from those
sources, renders the approved script, and leaves a publish-ready package. It
does not import or modify the shorts pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import feedparser
import yaml
from groq import Groq

from longform_system.renderer import render
from longform_system.title_system import build_title_package

ROOT = Path(__file__).resolve().parents[1]
POLITICAL = ("대통령", "국회", "정부", "장관", "여당", "야당", "민주당", "국민의힘",
             "법원", "검찰", "선거", "외교", "안보", "북한", "예산", "정책", "청와대")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def collect_sources(limit: int = 18) -> list[dict[str, str]]:
    config = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")) or {}
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for feed in config.get("feeds", []):
        parsed = feedparser.parse(feed.get("url", ""))
        for entry in parsed.entries[:12]:
            title = _clean(entry.get("title", ""))
            summary = _clean(entry.get("summary", "") or entry.get("description", ""))
            url = str(entry.get("link", "")).strip()
            if not title or not url or url in seen:
                continue
            text = f"{title} {summary}"
            score = sum(word in text for word in POLITICAL)
            if score < 1:
                continue
            seen.add(url)
            rows.append({"name": str(feed.get("name", "출처")), "title": title,
                         "summary": summary[:900], "url": url, "lean": str(feed.get("lean", "center")),
                         "score": str(score)})
    rows.sort(key=lambda row: int(row["score"]), reverse=True)
    return rows[:limit]


def select_stories(rows: list[dict[str, str]], count: int = 3) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    used_terms: set[str] = set()
    for row in rows:
        terms = {word for word in re.findall(r"[가-힣]{2,}", row["title"]) if word not in {"정부", "국회", "관련"}}
        if selected and len(terms & used_terms) >= max(2, len(terms) // 2):
            continue
        selected.append(row)
        used_terms |= terms
        if len(selected) == count:
            return selected
    return selected


def write_script(stories: list[dict[str, str]], out: Path) -> tuple[str, str]:
    if len(stories) < 3:
        raise RuntimeError("자동 승인 보류: 교차 확인 가능한 정치 이슈가 3개 미만입니다")
    evidence = "\n\n".join(
        f"[{i + 1}] 매체: {s['name']}\n제목: {s['title']}\n요약: {s['summary']}\n원문: {s['url']}"
        for i, s in enumerate(stories)
    )
    prompt = f"""당신은 오늘의엔터의 중립 정치 브리핑 편집자다. 아래 제공된 기사 자료 밖의 사실, 숫자,
인용, 혐의를 절대 추가하지 마라. 각 사건의 사실과 각 주체의 주장을 구분하고, 불확실한 내용은 단정하지 마라.
정당·인물 비방, 자극적 표현, 의견 유도는 금지한다.

자료:\n{evidence}

JSON만 출력하라:
{{"theme":"한 줄 주제","script":"Markdown"}}
script은 한국어 750~850 어절로 다음 순서를 정확히 지켜라.
## 0:00–0:20 | 훅
## 0:20–0:50 | 맥락
## 0:50–3:30 | 핵심 1
## 1:45–2:40 | 핵심 2
## 2:40–3:30 | 핵심 3
## 3:30–4:20 | 시사점
## 4:20–5:00 | 요약 + 예고
각 핵심에는 [SHORTS_HOOK] 한 문장을 넣고, 각 핵심 뒤에는 제공된 매체명과 원문 URL을 포함한
[화면 출처 텍스트: ...] 줄을 넣어라. 전망은 제공된 자료에서 확인 가능한 다음 절차만 말하라."""
    key = os.environ.get("LONGFORM_GROQ_API_KEY", "")
    if not key:
        raise RuntimeError("LONGFORM_GROQ_API_KEY secret is not available")
    client = Groq(api_key=key)
    available = {model.id for model in client.models.list().data}
    preferred = ("openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.1-8b-instant")
    model = next((candidate for candidate in preferred if candidate in available), "")
    if not model:
        raise RuntimeError("Groq 계정에서 사용 가능한 롱폼 대본 모델을 찾지 못했습니다")
    raw = client.chat.completions.create(
        model=model, temperature=0.15, max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    ).choices[0].message.content or ""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise RuntimeError("자동 승인 보류: 대본 생성 결과가 JSON 형식이 아닙니다")
    data = json.loads(match.group(0))
    script, theme = str(data.get("script", "")).strip(), str(data.get("theme", "")).strip()
    words = len(re.findall(r"\S+", re.sub(r"(?m)^\[.*$|^#.*$", "", script)))
    if not (700 <= words <= 900):
        raise RuntimeError(f"자동 승인 보류: 대본 분량 {words}어절")
    out.write_text(script, encoding="utf-8")
    return theme, script


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "longform_output")
    args = ap.parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stories = select_stories(collect_sources())
    script_path = args.output_dir / f"{stamp}.script.md"
    theme, _ = write_script(stories, script_path)
    plan = {"theme": theme, "chapters": [{"headline": s["title"], "sources": [s]} for s in stories]}
    package = build_title_package(plan)
    video = args.output_dir / f"{stamp}.mp4"
    manifest = render(script_path, video, ROOT / "assets/fonts/DoHyeon-Regular.ttf", "ko-KR-SunHiNeural")
    if not (240 <= manifest["duration_s"] <= 360):
        raise RuntimeError(f"자동 승인 보류: 렌더 길이 {manifest['duration_s']:.0f}초")
    meta = {"title": package.title, "description": package.description, "tags": package.tags,
            "privacy_status": "public", "category_id": "25", "theme": theme,
            "sources": stories, "render": manifest}
    video.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "built", "video": str(video), "title": package.title}, ensure_ascii=False))
    if args.publish:
        from longform_system.youtube_upload import upload
        print(json.dumps(upload(video, meta), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
