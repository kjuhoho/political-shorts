"""Hook Engine — builds the first ~2 seconds of every video.

NOT a restatement of the article headline. It generates a line that makes the
viewer want to hear the next sentence, using one of six patterns, in this
order of preference:

  1. 의외성    (surprise)      — an unexpected qualifier in the facts
  2. 반전      (twist)         — a plain fact that people then dispute
  3. 핵심 질문  (core question) — the unexplained "why / what" at the centre
  4. 결과 선공개 (outcome first) — state the result, then promise the story
  5. 갈등      (conflict)      — two named sides saying opposite things
  6. 숫자/비교  (number)        — open on the salient figure

Grounding rules (enforced in `_grounded`, not just hoped for):
  * no hype / verdict words (충격·발칵·경악·사실상 확정 …);
  * every number in the hook must appear in the cluster's own text;
  * every party / politician name in the hook must appear there too;
  * neutral — never a partisan characterisation, never a new fact.

`build_hook()` always returns a `Hook`. The LLM path may still overwrite the
hook narration afterwards; this is the always-available floor and fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .hook import Entities, Frame, josa, to_polite
from .textutil import clean_text, clip_sentence

# a figure worth opening on — deliberately NOT 일/월/년 (those are dates)
_FIG_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s?(?:%|퍼센트|명|석|표차|표|억\s?원|억|조\s?원|조|만\s?원|"
    r"배|건|위|議席)"
)
_SPECIAL_FIG = ("과반", "절반", "만장일치", "전원 찬성", "전원 반대", "재적 과반")

# "this doesn't happen often" cues (surprise). Stored without a trailing "만";
# the template appends " 만에".
_SURPRISE_SOON = ("취임 두 달", "취임 한 달", "취임 석 달", "취임 이틀", "취임 사흘",
                  "취임 나흘", "취임 일주일", "하루 만", "이틀 만", "사흘 만", "나흘 만",
                  "일주일 만", "한 달 만", "두 달 만", "석 달 만", "임명 하루")
_SURPRISE_RARE = ("역대 최초", "역대 최대", "역대 최다", "역대 최저", "사상 처음",
                  "사상 첫", "헌정 사상", "헌정사상", "만장일치", "전원 찬성",
                  "처음으로", "첫 사례")
_SURPRISE_WORD = ("이례적", "전격", "돌연", "기습")

# words a hook must never use
_HYPE = re.compile(
    r"충격|발칵|경악|초유의 사태|멘붕|폭탄|분노\s*폭발|사실상\s*확정|결국 이렇게|소름|경천동지"
)
_OUTCOME_FRAMES = {"vote", "personnel", "appoint"}
_COMPLETE_END = re.compile(r"(?:다|요|까|죠|음|함|됨|것|겁니다|습니다|입니다|나요)$")


@dataclass
class Hook:
    caption: str
    narration: str
    kind: str = "question"
    lead_number: str = ""


@dataclass
class HookContext:
    headline: str
    facts: list[str]
    claims: list[str]
    frame: Frame
    entities: Entities
    actor: str = ""
    n_sources: int = 1

    @property
    def blob(self) -> str:
        return clean_text(" ".join([self.headline, *self.facts, *self.claims]))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _grounded(text: str, ctx: HookContext) -> bool:
    if _HYPE.search(text):
        return False
    src = ctx.blob
    if any(n not in src for n in re.findall(r"\d[\d,.]*", text)):
        return False
    for name in (*ctx.entities.parties, *ctx.entities.politicians):
        if name in text and name not in src:
            return False
    return True


def _complete(s: str) -> bool:
    """The clipped fact ends on a real predicate, not a dangling particle."""
    s = s.strip().rstrip(" .")
    return bool(s) and bool(_COMPLETE_END.search(s))


def _fig(ctx: HookContext) -> str:
    for s in [ctx.headline, *ctx.facts, *ctx.claims]:
        m = _FIG_RE.search(clean_text(s))
        if m:
            return m.group(0).replace(" ", "")
    return next((sp for sp in _SPECIAL_FIG if sp in ctx.blob), "")


# frame -> (past-adnominal, declarative) forms of the actor's action
_ACT: dict[str, tuple[str, str]] = {
    "personnel": ("물러난", "물러났습니다"),
    "appoint": ("발탁된", "발탁됐습니다"),
    "vote": ("처리된", "처리됐습니다"),
    "clash": ("맞붙은", "정면으로 부딪혔습니다"),
    "scandal": ("도마에 오른", "도마 위에 올랐습니다"),
    "remark": ("입을 연", "입을 열었습니다"),
    "poll": ("달라진", "달라졌습니다"),
}


def _action(ctx: HookContext) -> str:
    return _ACT.get(ctx.frame.kind, ("", ""))[1]


def _clip(s: str, n: int = 34) -> str:
    return clip_sentence(clean_text(s), n).rstrip(" .,·…\"'").strip()


def _fact0(ctx: HookContext, n: int = 36) -> str:
    for f in ctx.facts:
        c = to_polite(_clip(f, n))
        if len(c) >= 10 and _complete(c):
            return c
    return ""


# --------------------------------------------------------------------------- #
# the six generators — each returns a ONE-sentence Hook or None
# --------------------------------------------------------------------------- #
def _surprise(ctx: HookContext) -> Hook | None:
    if ctx.frame.kind == "poll":                 # a poll isn't a "rare event"
        return None
    blob = ctx.blob
    soon = next((c for c in _SURPRISE_SOON if c in blob), "")
    rare = next((c for c in _SURPRISE_RARE if c in blob), "")
    word = next((c for c in _SURPRISE_WORD if c in blob), "")
    who = ctx.actor
    adnom, decl = _ACT.get(ctx.frame.kind, ("", ""))
    if soon and who and adnom:
        soon = soon[:-1].rstrip() if soon.endswith("만") else soon
        return Hook(caption=_clip(f"{who}, {soon} 만에 {adnom}", 24),
                    narration=f"{who}, {soon} 만에 {adnom} 건 정치권에서도 흔치 않은 일입니다.",
                    kind="surprise")
    if rare:
        return Hook(caption=_clip(f"'{rare}'인 일이 벌어졌습니다", 22),
                    narration=f"'{rare}'라고 할 만한 일이 벌어졌습니다.", kind="surprise")
    if word and who and decl:
        return Hook(caption=_clip(f"{who}, '{word}' {adnom}", 22),
                    narration=f"{josa(who, ('이', '가'))} '{word}'이라 불릴 만큼 갑작스럽게 {decl}.",
                    kind="surprise")
    return None


def _twist(ctx: HookContext) -> Hook | None:
    # a plain fact that is then disputed. (vote -> outcome_first instead; a
    # passed bill isn't a "twist".)
    if not (ctx.facts and ctx.claims):
        return None
    if ctx.frame.kind not in ("scandal", "clash", "personnel"):
        return None
    f0 = _fact0(ctx, 32)
    if not f0:
        return None
    return Hook(caption="그런데, 평가는 엇갈립니다",
                narration=f"{f0.rstrip('.')}. 그런데 이걸 두고 평가는 엇갈립니다.",
                kind="twist")


def _question(ctx: HookContext) -> Hook | None:
    who = ctx.actor
    if not who or ctx.frame.kind not in ("personnel", "appoint", "scandal", "remark"):
        return None
    q = {
        "personnel": f"{josa(who, ('은', '는'))} 왜 갑자기 자리에서 내려왔을까요?",
        "appoint": f"왜 하필 {josa(who, ('이', '가'))} 그 자리에 발탁됐을까요?",
        "scandal": f"{josa(who, ('을', '를'))} 둘러싼 의혹, 어디까지 사실일까요?",
        "remark": f"{who}의 이 한마디가 왜 이렇게 시끄러울까요?",
    }[ctx.frame.kind]
    return Hook(caption=_clip(q, 24), narration=q, kind="question")


def _outcome_first(ctx: HookContext) -> Hook | None:
    if ctx.frame.kind not in _OUTCOME_FRAMES:
        return None
    out = _fact0(ctx, 34)
    if not out:
        return None
    return Hook(caption=_clip(f"결론부터: {out}", 24),
                narration=f"결론부터 말하면, {out.rstrip('.')}.", kind="outcome_first")


def _conflict(ctx: HookContext) -> Hook | None:
    p = ctx.entities.parties
    if len(p) >= 2:
        return Hook(caption="여야, 정반대로 말합니다",
                    narration=f"같은 사안을 두고 {josa(p[0], ('과', '와'))} "
                              f"{josa(p[1], ('은', '는'))} 정반대로 말합니다.",
                    kind="conflict")
    if ctx.frame.kind == "clash" and ctx.actor:
        return Hook(caption=_clip(f"{ctx.actor} 두고 정면충돌", 20),
                    narration=f"{josa(ctx.actor, ('을', '를'))} 두고 정치권이 "
                              f"정면으로 부딪히고 있습니다.", kind="conflict")
    return None


def _number(ctx: HookContext) -> Hook | None:
    fig = _fig(ctx)
    if not fig:
        return None
    return Hook(caption=_clip(f"{fig}, 이 숫자부터", 20),
                narration=f"{fig}, 이 숫자 하나에서 오늘 이야기가 시작됩니다.",
                kind="number", lead_number=fig)


def _fallback_question(ctx: HookContext) -> Hook:
    who = ctx.actor or (ctx.entities.parties[0] if ctx.entities.parties else "정치권")
    line = f"{josa(who, ('은', '는'))} 지금 왜 이렇게 주목받고 있을까요?"
    return Hook(caption=_clip(line, 24), narration=line, kind="question")


_ORDER = (_surprise, _twist, _question, _outcome_first, _conflict, _number)


def build_hook(ctx: HookContext) -> Hook:
    for gen in _ORDER:
        try:
            h = gen(ctx)
        except Exception:
            h = None
        if h and h.narration and _grounded(h.narration, ctx):
            h.narration = re.sub(r"\s+", " ", h.narration).strip()
            h.caption = re.sub(r"\s+", " ", h.caption).strip() or h.narration[:24]
            return h
    return _fallback_question(ctx)
