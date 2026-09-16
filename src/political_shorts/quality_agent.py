"""AI QUALITY AGENT — a second, semantic quality gate layered on top of
quality.py's mechanical checks (sync/length/layout/source disclosure).

quality.py cannot judge whether the STORY a script tells actually makes
sense to someone who doesn't follow politics. This session found that
repeatedly: scripts scored 90-100/100 on quality.py while the hook asked
about the wrong person, the background card had silently vanished, or the
outro closed on a banned vague cliché — none of that is a mechanical defect
quality.py's checks are built to catch. This module has an LLM re-read the
WHOLE assembled script the way that viewer would, using the same rubric this
session used manually (batch 6's method note: automated scores miss real
defects; only actually reading it catches them).

Best-effort, like every other LLM call in this codebase: on any failure (no
provider, unparseable response, network error after retries) this returns
an UNAVAILABLE report that does NOT block anything — `AgentReport.passed`
is True in that case. This is an ADDITIONAL gate, never a replacement for
quality.py's own gate, and never a single point of failure for the whole
pipeline if the provider is down.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("quality_agent")

PASS_SCORE = 95

_SYSTEM = (
    "당신은 정치 숏폼 채널의 수석 에디터입니다. 아래는 곧 게시될 영상 대본 "
    "전체입니다. 정치를 전혀 모르는 시청자가 처음부터 끝까지 본다고 가정하고 "
    "읽은 뒤 평가하세요. 어떻게 만들어졌는지가 아니라 결과물 자체만 봅니다.\n"
    "평가 기준 (100점 만점, 감점 방식):\n"
    "1) 훅이 실제 이 영상의 주제·주인공과 일치하는가 — 본문과 무관한 사람이나 "
    "사건을 묻고 있지 않은가 (25점)\n"
    "2) 배경 설명이 실제로 존재하고, 이 사건·인물·용어를 몰라도 이해되게 "
    "설명하는가 (25점)\n"
    "3) 모든 문장이 서술어로 끝나는 완결된 문장인가 — 명사나 조사에서 뚝 "
    "끊기지 않는가 (20점)\n"
    "4) 전문용어·약어·기관명이 처음 나올 때 풀이 없이 그냥 쓰이지 않는가 "
    "(15점)\n"
    "5) 마무리가 '~지켜봐야 합니다'류의 막연한 관망이 아니라 구체적인가 "
    "(10점)\n"
    "6) 보도자료체 명사 나열이 아니라 실제 기자가 말하듯 자연스러운 구어체인가 "
    "(5점)\n"
    "감점 사유는 짧고 구체적으로 — 어느 카드(역할)의 어떤 문장이 문제인지 "
    "지적할 것. 원문에 없는 새로운 사실·수치를 요구하지 말고, 표현·구조 "
    "문제만 지적할 것. 완벽하지 않아도 이 6가지 기준에서 실제로 심각한 문제가 "
    "없다면 95점 이상을 줄 것 — 사소한 문체 취향 차이로 감점하지 말 것.\n"
    "출력은 JSON 하나만: "
    '{"score": 0부터 100 사이 정수, "issues": [{"role": '
    '"hook|summary|what|factcheck|sides|outro", "problem": "구체적 문제 설명"}]}'
)


@dataclass
class AgentReport:
    available: bool = False
    score: int = 0
    issues: list[dict[str, str]] = field(default_factory=list)
    raw_error: str = ""

    @property
    def passed(self) -> bool:
        """Never blocks anything on its own account — an agent that
        couldn't run at all (no provider, gave up after retries) must not
        take the whole pipeline down with it. Only a genuine sub-PASS_SCORE
        grade fails."""
        return (not self.available) or self.score >= PASS_SCORE

    def feedback_text(self) -> str:
        lines = [f"- ({i.get('role', '')}) {i.get('problem', '')}"
                for i in self.issues if i.get("problem")]
        return "\n".join(lines)


def _script_text(script: dict[str, Any]) -> str:
    parts = [f"제목: {clean_text(script.get('headline', ''))}"]
    for seg in script.get("segments", []):
        role = seg.get("role", "")
        narr = clean_text(seg.get("narration", ""))
        if narr:
            parts.append(f"[{role}] {narr}")
        for row in seg.get("rows", []) or []:
            if isinstance(row, dict):
                parts.append(f"[{role}-표] {row.get('tag', '')}: {row.get('text', '')}")
    return "\n".join(parts)


def _parse(raw: str) -> dict[str, Any] | None:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0) if m else raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def review(script: dict[str, Any], cfg: Settings) -> AgentReport:
    """Grade the ASSEMBLED script (headline + every card's narration/rows).
    Independent of rendering — runs on the text alone, before any video is
    cut, so a bad script never wastes render time."""
    provider = (getattr(cfg, "llm_provider", "") or "").strip()
    if not provider:
        return AgentReport(available=False)

    payload = _script_text(script)
    if not payload.strip():
        return AgentReport(available=False)

    from .llm import complete

    last_exc = ""
    for attempt in range(2):
        try:
            raw = complete(payload, cfg, max_tokens=700, system=_SYSTEM)
        except Exception as exc:  # pragma: no cover - network dependent
            last_exc = str(exc)
            log.info("quality agent call %d failed (%s), retrying", attempt + 1, last_exc[:80])
            continue
        data = _parse(raw)
        if data is None:
            log.info("quality agent response %d unparseable, retrying", attempt + 1)
            continue
        score = data.get("score")
        if not isinstance(score, (int, float)):
            log.info("quality agent response %d had no numeric score, retrying", attempt + 1)
            continue
        issues = [i for i in (data.get("issues") or [])
                 if isinstance(i, dict) and i.get("problem")]
        return AgentReport(available=True, score=int(max(0, min(100, score))), issues=issues)

    log.warning("quality agent gave up after retries (%s) — gate skipped this run",
                last_exc or "unparseable")
    return AgentReport(available=False, raw_error=last_exc)
