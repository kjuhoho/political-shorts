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
from .textutil import clean_text, clip_sentence, gloss_jargon

# a figure worth opening on — deliberately NOT bare 일/월/년 (those are dates)
_FIG_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s?(?:%p|%포인트|%|퍼센트|명|석|표차|표|억\s?원|억|조\s?원|조|"
    r"만\s?원|배|건|위|주년|주기|명선|여\s?명|만\s?명|천\s?명)"
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
    speaker: str = ""               # a quote opener: who said it ('이재명 대통령' / '국민의힘')


@dataclass
class HookContext:
    headline: str
    facts: list[str]
    claims: list[str]
    frame: Frame
    entities: Entities
    actor: str = ""
    n_sources: int = 1
    quote_first: bool = True        # config: HOOK_QUOTE_FIRST (the channel's opener experiment)

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


_STEPPED_DOWN = ("사퇴", "교체", "경질", "사임", "물러", "지명철회", "낙마", "하차")


def _question(ctx: HookContext) -> Hook | None:
    who = ctx.actor
    if not who or ctx.frame.kind not in ("personnel", "appoint", "scandal", "remark"):
        return None
    # a real shipped case: FRAMES["personnel"] also fires on plain "후보자"/
    # "지명"/"인선" mentions — a hearing-report dispute about a NOMINEE, with
    # nobody actually stepping down, still lands here. "왜 갑자기 자리에서
    # 내려왔을까요?" then flatly asserts a departure that never happened.
    # Require an actual departure word before asking it.
    if ctx.frame.kind == "personnel" and not any(w in ctx.blob for w in _STEPPED_DOWN):
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


_INSTITUTION = {"국회", "정부", "여야", "정치권", "청와대", "대통령실", "여당", "야당",
                "당정", "국무회의", "국가"}


def _generic(ctx: HookContext) -> Hook | None:
    """No strong pattern fired — lead on the top fact (NOT the headline) and
    hand off to the explanation."""
    f0 = _fact0(ctx, 40)
    if not f0:
        return None
    return Hook(caption=_clip(f0, 24),
                narration=f"{f0.rstrip('.')}. 무슨 의미인지, 왜 중요한지 하나씩 풀어보겠습니다.",
                kind="question")


def _fallback_question(ctx: HookContext) -> Hook:
    who = ctx.actor
    if not who or who in _INSTITUTION:
        who = next((n for n in ctx.entities.politicians if n in ctx.headline),
                   ctx.entities.parties[0] if ctx.entities.parties else "")
    if who:
        line = f"{josa(who, ('은', '는'))} 지금 왜 이렇게 주목받고 있을까요?"
    else:
        line = "오늘 정치권에서 가장 많이 오르내린 이야기, 쉽게 풀어보겠습니다."
    return Hook(caption=_clip(line, 24), narration=line, kind="question")


# --------------------------------------------------------------------------- #
# the quote opener: a named person's own words, verbatim, in the first seconds
# --------------------------------------------------------------------------- #
_PAIRS = {"“": "”", "‘": "’", '"': '"', "'": "'"}
_SAID = re.compile(r"^\s*(?:라고|이라고|라며|이라며|며|고)?\s*(?:\S{0,6}\s)?"
                   r"(?:말했|말하|말한|밝혔|밝히|강조했|강조하|주장했|주장하|지적했|지적하|비판했|비판하|"
                   r"반박했|반박하|덧붙였|언급했|호소했|촉구했|당부했|경고했|답했|설명했|전했|못박|일축했)")
_ROLES = ("대통령", "국무총리", "총리", "부총리", "원내대표", "대표", "최고위원", "의원", "장관", "차관",
          "위원장", "비서실장", "수석대변인", "대변인", "수석", "대법원장", "청장", "처장", "후보자", "시장", "지사")
_ROLE_RX = "|".join(sorted(_ROLES, key=len, reverse=True))
_PARTY_ROLE = r"(?:\s?(?:원내대표|대표|수석대변인|대변인|최고위원|지도부))?"
_SUBJ = r"\s?(?:은|는|이|가|도|측은|측이|께서는|께서)"
# a quote must end like an utterance, not a headline fragment ("청와대 책임")
_UTTERANCE_END = re.compile(r"(?:다|요|까|죠|니다|니까|세요|라|네|자|지|해|야)\s*[.?!]?$")
# openers that only mean something with the sentence before them
_NEEDS_CONTEXT = re.compile(r"^(?:그리고|그러나|하지만|그래서|따라서|또한|다만|이어|그는|그녀는|이는|이것은|그것은)")
# words that would make the very first seconds an attack line, however accurately quoted
_TOO_HOT = re.compile(r"매국|내란\s*수괴|쓰레기|범죄자|사기꾼|미친|척결|응징|처단|죽여|죽이|빨갱이|"
                      r"독재자|살인|암살|테러|폭도|국적|역적|반역")
QUOTE_MAX = 40
_FILLER_MAX = 24          # words between the speaker and the opening quote mark ('21일 국회에서')


@dataclass
class Quote:
    text: str
    speaker: str
    score: float


def _spans(s: str) -> list[tuple[int, int, str]]:
    """(start, end, inner text) of properly PAIRED quote marks. A bare regex also pairs a closing mark with the
    next opening one, which turned '"A"라고 공격했다 "B"' into the 'quote' 라고 공격했다."""
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(s):
        close = _PAIRS.get(s[i])
        if close:
            j = s.find(close, i + 1)
            if j > i:
                out.append((i, j + 1, s[i + 1:j]))
                i = j + 1
                continue
        i += 1
    return out


def _people(ctx: HookContext) -> list[str]:
    from .hook import candidate_people
    names = {*ctx.entities.politicians}
    return sorted(names, key=len, reverse=True)


def _speaker_before(prefix: str, ctx: HookContext, headline_form: bool = False) -> tuple[str, bool]:
    """-> (speaker, subject_form). Who said it, from the words just before the opening quote mark.

    Only a person or party the pipeline already recognises, and only as the SUBJECT of the sentence
    ('이재명 대통령은 21일 "…"라고 말했다') or as the byline-style lead of a headline ('이재명 "…"').
    The last name mentioned before the quote is not necessarily the speaker: it can be the person being
    addressed, and an unnamed 'one official' never opens a video."""
    pre = prefix.rstrip()
    known = _people(ctx)
    parties = sorted(ctx.entities.parties, key=len, reverse=True)

    def other_named(gap: str, own: str) -> bool:
        return any(n != own and n in gap for n in (*known, *parties))

    # 1) headline form: the quote is directly preceded by the name (and role): '이재명 "…"'.
    #    OFF for the opener: quote marks in a Korean headline routinely hold a CONDENSED paraphrase, not the
    #    words as said ('김민석 "용혜인, 당 지키는 선택한 것 이해"' was a summary of a longer remark), and the
    #    channel's rule is that remarks are quoted whole. Only a verb-confirmed body sentence qualifies.
    if headline_form:
        for name in known:
            m = re.search(rf"(?:^|[^가-힣]){re.escape(name)}(?:\s?({_ROLE_RX}))?\s*[,:]?$", pre)
            if m:
                return (f"{name} {m.group(1)}" if m.group(1) else name), False
        for p in parties:
            m = re.search(rf"(?:^|[^가-힣]){re.escape(p)}{_PARTY_ROLE}\s*[,:]?$", pre)
            if m:
                return clean_text(pre[m.start():].strip(" ,:")).lstrip("·, "), False

    # 2) subject form: 'NAME(ROLE)은/는/이/가 <≤24 chars, no other named person> "…"'
    for name in known:
        for m in re.finditer(rf"(?:^|[^가-힣]){re.escape(name)}(?:\s?({_ROLE_RX}))?{_SUBJ}\s", pre + " "):
            gap = (pre + " ")[m.end():]
            if len(gap) <= _FILLER_MAX and not other_named(gap, name):
                return (f"{name} {m.group(1)}" if m.group(1) else name), True
    for p in parties:
        for m in re.finditer(rf"(?:^|[^가-힣]){re.escape(p)}({_PARTY_ROLE}){_SUBJ}\s", pre + " "):
            gap = (pre + " ")[m.end():]
            if len(gap) <= _FILLER_MAX and not other_named(gap, p):
                return clean_text(f"{p}{m.group(1)}"), True
    # 3) '이 대통령은' / '대통령은' - the sitting president
    if ctx.entities.president:
        m = re.search(rf"(?:^|[^가-힣])(?:이\s?)?대통령{_SUBJ}\s", pre + " ")
        if m and len((pre + " ")[m.end():]) <= _FILLER_MAX and not other_named((pre + " ")[m.end():], "이재명"):
            return "이재명 대통령", True
    return "", False


def find_quotes(ctx: HookContext) -> list[Quote]:
    """Every whole, attributable, short quote in the cluster's own text, best first."""
    from .soften import SOFTEN

    out: list[Quote] = []
    seen: set[str] = set()
    for sent in [*ctx.claims, *ctx.facts]:
        s = clean_text(sent)
        for a, b, inner in _spans(s):
            q = inner.strip()
            if q in seen:
                continue
            seen.add(q)
            body = q.rstrip(" .")
            if not (8 <= len(body) <= QUOTE_MAX):
                continue
            if re.search(r"[()\[\]]|…|\.\.\.", q):
                continue                                  # edited for the print edition: not the words as said
            if re.search(r"[‘’'\"“”]", q):
                continue                                  # a quote inside the quote: the caption font (Jua) has no ‘ ’
            if not _UTTERANCE_END.search(q) or _NEEDS_CONTEXT.match(q):
                continue
            if _TOO_HOT.search(q) or any(k in q for k in SOFTEN):
                continue
            who, subject_form = _speaker_before(s[:a], ctx)
            if not who:
                continue
            # a subject-form attribution must be backed by a speech verb right after the quote; the headline
            # form ('이재명 "…"') is the byline convention and needs none
            if not _SAID.match(s[b:b + 24]):
                continue
            score = 0.0
            score += 2.0 if who.split()[0] in ctx.headline else 0.0
            score += 1.5 if ctx.actor and ctx.actor in who else 0.0
            score += 1.0 if q.endswith("?") else 0.0
            score -= abs(len(body) - 22) / 22.0           # short enough to take in at once, long enough to say something
            out.append(Quote(text=q, speaker=who, score=score))
    return sorted(out, key=lambda x: -x.score)


def _quote(ctx: HookContext) -> Hook | None:
    if not ctx.quote_first:
        return None
    found = find_quotes(ctx)
    if not found:
        return None
    best = found[0]
    q = best.text.strip(" ").rstrip(".")
    who = best.speaker
    return Hook(caption=f"“{q}” — {who}",
                narration=f"“{q}” {josa(who, ('이', '가'))} 한 말입니다.",
                kind="quote", speaker=who)


_ORDER = (_quote, _surprise, _twist, _question, _outcome_first, _conflict, _number, _generic)


def _gloss(h: Hook) -> Hook:
    """The hook is the ONE place in the pipeline with no LLM pass and no
    per-card glossing (script_llm's Stage-1 "terms" never reaches it — it
    explicitly never writes the hook) — a raw fact clip can ship a term like
    "토허구역" completely unexplained as the very first thing a viewer sees,
    hears, AND reads (scene.py sets the hook's on-screen caption to this same
    narration, verbatim). Applied AFTER the sentence is assembled, so a
    generator's own clip budget never cuts the gloss off mid-parenthesis."""
    h.narration = gloss_jargon(h.narration)
    h.caption = gloss_jargon(h.caption)
    return h


def build_hook(ctx: HookContext) -> Hook:
    for gen in _ORDER:
        try:
            h = gen(ctx)
        except Exception:
            h = None
        if h and h.narration and _grounded(h.narration, ctx):
            h.narration = re.sub(r"\s+", " ", h.narration).strip()
            h.caption = re.sub(r"\s+", " ", h.caption).strip() or h.narration[:24]
            return _gloss(h)
    return _gloss(_fallback_question(ctx))
