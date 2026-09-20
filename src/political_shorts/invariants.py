"""Mechanical post-conditions a finished script must satisfy — facts, not opinions.

Why this exists: the AI quality agent is a noisy judge (the same script scored 85 on every
attempt for reasons that were misreadings of our own rubric), so fixing one defect kept
surfacing another. These checks are deterministic. Each one is a real incident, written down
as a rule, and each is replayed in tests/test_regressions.py so a later "fix" cannot quietly
bring it back:

  research_missing  a published video had NO research in it (the note extraction came back cut
                    off, the script was written from one seed article, and it still went out)
  one_sided         the research found two parties with different positions, but the video
                    voiced only one (the DMZ-festival videos: critic-only, then province-only)
  missing_quote     the research found what the key person actually said, yet no quote reached
                    the script ("김민석 대표의 발언들은 왜 사라졌나")
  missing_cause     the research found WHY it happened, yet the script never says it

Violations are fed back to the writer as feedback for the next attempt; whatever survives the
last attempt HOLDS the video from auto-publish (pipeline.py) instead of shipping it.
"""
from __future__ import annotations

import re
from typing import Any

from .textutil import clean_text


def _toks(text: str) -> set[str]:
    from .topics import _STOP

    return set(re.findall(r"[가-힣]{2,}", clean_text(text or ""))) - set(_STOP)


def _covered(reference: str, narrations: list[str], need: float = 0.6) -> bool:
    """True if ONE narration carries at least `need` of the reference's distinctive words."""
    ref = _toks(reference)
    if len(ref) < 3:
        return True                      # too short to test fairly
    return any(len(ref & _toks(n)) / len(ref) >= need for n in narrations)


def _key(who: Any) -> str:
    """'김민석 대표' -> '김민석', '청와대(강유정 수석대변인)' -> '청와대'."""
    return (re.split(r"[\s(（]", str(who or "").strip())[0] or "").strip()


def check(segments: list[dict[str, Any]], research_web: dict[str, Any] | None, *,
          research_expected: bool = False) -> list[dict[str, str]]:
    """-> [{"code", "message"}]; empty when every invariant holds. The message is written to be
    pasted straight into the writer's next-attempt feedback."""
    web = research_web or {}
    narrs = [s.get("narration", "") or "" for s in segments if s.get("narration")]
    text = " ".join(narrs)
    out: list[dict[str, str]] = []

    if research_expected and not web:
        out.append({"code": "research_missing",
                    "message": "자료조사 결과가 비어 있어 기사 1개로만 쓴 대본입니다 — 조사 자료 없이는 발행하지 않습니다."})
        return out

    # two parties with different positions -> the sides card must voice each of them
    actors: list[str] = []
    for p in web.get("positions") or []:
        if isinstance(p, dict) and p.get("position"):
            k = _key(p.get("who"))
            if k and k not in actors:
                actors.append(k)
    if len(actors) >= 2:
        sides_text = " ".join(s.get("narration", "") or "" for s in segments if s.get("role") == "sides")
        missing = [a for a in actors[:3] if a not in sides_text]
        if missing:
            out.append({"code": "one_sided",
                        "message": f"입장이 갈리는 사안인데 sides 카드에 {', '.join(missing)}의 입장이 없습니다 — "
                                   f"자료에 있는 모든 주체({', '.join(actors[:3])})를 각각 그 주체를 주어로 한 문장으로 넣을 것."})

    # what the key person actually said must appear
    quotes = [s for s in web.get("statements") or []
              if isinstance(s, dict) and len(str(s.get("text", ""))) >= 12]
    if quotes and not any(_covered(str(q["text"]), narrs) for q in quotes[:3]):
        q = quotes[0]
        out.append({"code": "missing_quote",
                    "message": f"핵심 인물의 발언이 대본에 없습니다 — what 카드에 "
                               f"'{q.get('who', '')}은(는) \"{str(q.get('text', ''))[:60]}\"라고 밝혔습니다' 형태로 "
                               f"발언 내용을 끝까지 인용할 것."})

    # WHY it happened must be stated when the research found it
    whys = [str(w.get("reason", "")) for w in web.get("why") or [] if isinstance(w, dict) and w.get("reason")]
    if whys and not any(_covered(w, [text], 0.4) for w in whys[:3]):
        out.append({"code": "missing_cause",
                    "message": f"자료조사로 확인된 원인이 대본에 없습니다 — summary/what에 넣을 것: {whys[0][:80]}"})
    return out
