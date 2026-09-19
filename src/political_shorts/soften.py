"""Expression softening — rewrite harsh wording instead of refusing the video.

Slurs, profanity and abusive nicknames used to trip safety.HARMFUL and stop a
whole video. The words themselves are rarely the point of a story (they usually
sit inside someone's quoted remark), so they are swapped for a milder or neutral
equivalent and the video goes on. What stays blocked is the truly dangerous part:
calls for violence (safety.HARMFUL's "죽여", "처단하자" …) — those are NOT here.

Serious allegation words ("성추행", "뇌물" …) are deliberately left alone: they are
fine in reporting as long as the line says it is an allegation, which the writer
prompt already requires.
"""
from __future__ import annotations

import re
from typing import Any

# harsher wording  ->  milder / neutral wording (longest keys are applied first)
SOFTEN: dict[str, str] = {
    # abusive nicknames for political camps / groups
    "빨갱이": "좌파 성향 인사",
    "수구꼴통": "강경 보수 인사",
    "토착왜구": "친일 논란 인사",
    "쓰레기 정당": "비판받는 정당",
    "국개": "국회의원",
    "틀딱": "노년층",
    "급식충": "청소년",
    "맘충": "육아 중인 어머니",
    "전라디언": "호남 출신",
    "착짱죽짱": "혐오 표현",
    "정신병자": "이상한 사람",
    # profanity (mostly appears inside quoted remarks)
    "씨발": "젠장",
    "시발": "젠장",
    "개새끼": "나쁜 사람",
    "병신": "모자란 사람",
    "지랄": "난리",
    "좆같": "형편없",
    "존나": "엄청",
    "새끼": "녀석",
}

_ORDERED = sorted(SOFTEN, key=len, reverse=True)
_RX = re.compile("|".join(re.escape(k) for k in _ORDERED))

# only these script fields are user-visible prose; ids, urls, roles, sources are left alone
_TEXT_KEYS = {"headline", "title", "narration", "caption", "kicker", "text", "topic_label", "pinned_comment"}


def soften_text(text: str) -> tuple[str, list[str]]:
    """-> (softened text, [words that were replaced])."""
    if not text:
        return text, []
    hits: list[str] = []

    def _sub(m: re.Match) -> str:
        hits.append(m.group(0))
        return SOFTEN[m.group(0)]

    return _RX.sub(_sub, text), hits


def soften_script(script: Any) -> list[str]:
    """Soften every prose field of a script in place. Returns the words replaced."""
    changed: list[str] = []

    def walk(node: Any, key: str = "") -> Any:
        if isinstance(node, str):
            if key in _TEXT_KEYS:
                new, hits = soften_text(node)
                changed.extend(hits)
                return new
            return node
        if isinstance(node, list):
            return [walk(x, key) for x in node]
        if isinstance(node, dict):
            for k in list(node):
                node[k] = walk(node[k], k)
            return node
        return node

    walk(script)
    return changed
