"""Step 5 — turn a story cluster into a punchy, explainer-style short script.

Structure (v2):
    hook       curiosity-gap opener, names the real actors           (~4s)
    summary    "쉽게 말하면, ..." one plain sentence                  (~4s)
    what       1-2 middle-school beats of what actually happened      (~9s ea)
    reaction   what each side said, attributed                       (~9s)
    factcheck  ✅ 확인된 사실 / 💬 주장 / ⚠️ 해석 / 🔎 원문 확인       (~9s)
    outro      soft CTA + sources                                    (~4s)

Also carries `images` (keyless CC photos) that video.py lays under the cards.
The hook may be sensational in *tone*; every factual line still traces to the
sources, and the fact-check card keeps interpretation clearly labelled.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from .analyze import analyze
from .config import Settings, settings
from .db import cluster_articles, connect
from .hook import (
    detect_entities, detect_frame, josa, make_factcheck, make_hook, make_title,
    simplify, strip_wire_marks,
)
from .logging_setup import get_logger
from .textutil import clean_text, clip_sentence, strip_byline, truncate

log = get_logger("script_gen")

CAPTION_LIMIT = 46
BODY_LIMIT = 72
MAX_WHAT = 1

# Top-performing news shorts run tight — 20-38s. Aim ~30-36s: hook opens a
# loop, the summary card is dropped, 4-5 fast cards.
MAX_VIDEO_SECONDS = 38.0
# with an LLM writing connected explanation, allow a little more room so it can
# actually explain (still well under the 60s Shorts limit).
MAX_VIDEO_SECONDS_LLM = 56.0
_KR_CHARS_PER_SEC = 7.0          # edge-tts at ~+13% rate (TTS_RATE 198)
_CARD_PAD_SECONDS = 0.24         # brief breath between cards
# hard per-segment narration caps (chars). 0 = caption-only card, no voice.
_NARR_CAP = {"hook": 46, "summary": 40, "what": 58, "reaction": 62,
             "factcheck": 70, "sides": 104, "outro": 0}
_NARR_CAP_LLM = {"hook": 52, "summary": 56, "what": 76, "reaction": 76,
                 "factcheck": 66, "sides": 118, "outro": 78}
_SILENT_CARD_SECONDS = 1.5

_SENT_END = ("다", "요", "죠", "까", "네", "군", ".", "!", "?", "…")
# a chunk of text ending on a Korean predicate ending or sentence punctuation
_SENT_CHUNK = re.compile(r".+?(?:[다요죠까](?=[\s\"')\]]|$)|[.!?](?=\s|$))")
_TRAIL_JUNK = re.compile(
    r"[,·]?\s*[가-힣]{0,12}?(라며|하며|면서|는데|지만|따르면|밝히며|말하며|위해|대해|"
    r"관해|향해|에서|으로|에게|께|와|과|에|을|를|은|는|이|가|의|도|만|고|며|면)$"
)


def _spoken(text: str) -> str:
    """Return only the COMPLETE sentences of a narration line, joined and
    punctuated — or '' if there isn't one clean sentence (the caller then drops
    the card rather than voicing a fragment like '…참여한 곳이')."""
    t = clean_text(text).replace("…", " ").replace("...", " ").replace("..", " ")
    t = re.sub(r"\s+", " ", t).strip(" ,·.")
    if not t:
        return ""
    good = " ".join(m.group(0).strip() for m in _SENT_CHUNK.finditer(t)).strip()
    good = re.sub(r"\s+([.!?])", r"\1", good).strip(" ,·")
    if good and len(good) >= max(10, int(len(t) * 0.4)):
        return good if good[-1] in ".!?" else good + "."
    # no complete sentence — salvage only if a clause-drop leaves a real
    # predicate ending; otherwise return '' so the caller drops the card.
    t = _TRAIL_JUNK.sub("", t).strip(" ,·")
    if len(t) >= 8 and (t[-1] in ".!?" or t.endswith(
            ("다", "요", "죠", "까", "음", "됨", "함", "임", "것", "중"))):
        return t if t[-1] in ".!?" else t + "."
    return ""


# connective / particle tails that must not be the last thing on a caption card
_CAP_TAIL = re.compile(
    r"\s*[가-힣]{0,6}?(따르면|밝히며|말하며|라며|이라며|하며|면서|는데|지만|라고|"
    r"이라고|대해|위해|통해|관련|둘러싸고|며|면|고|은|는|이|가|을|를|에|의|와|과|도|만|께|"
    r"에서|으로|에게)$"
)


def _glyph_safe(s: str) -> str:
    """The bundled caption fonts have no ·/…/— glyph (they render as tofu)."""
    return (s.replace("·", ", ").replace("ㆍ", ", ").replace("…", " ")
             .replace("—", "-").replace("–", "-").replace("~", "-"))


def _tidy_caption(narration: str, limit: int) -> str:
    """A short on-screen caption from the (possibly long) spoken line that
    ALWAYS ends cleanly — on a sentence end or a noun, never on '…따르면'."""
    cap = _glyph_safe(clip_sentence(narration, limit, ell="")).rstrip(" ,·.")
    cap = re.sub(r"\s+", " ", cap)
    for _ in range(4):
        if not cap or cap[-1] in "다요죠까네군!?.":
            break
        t = _CAP_TAIL.sub("", cap).rstrip(" ,·")
        if t == cap or len(t) < max(6, limit * 0.35):
            break
        cap = t
    return cap or clip_sentence(narration, limit, ell="")


# split ONLY at a real sentence end — a formal ending (…습니다/…합니다/…죠/…나요)
# or explicit punctuation. NOT after bare "…다" (which also ends "찬성보다",
# "그에 따라" etc.), so "반대가 찬성보다 많았습니다" stays one sentence.
# narration is formal "~습니다" style, so a real sentence break is: an ending in
# "…니다" (+ optional period), OR explicit .!? punctuation. Bare "…요"/"…죠" is
# NOT a split point ("구독과 좋아요 눌러주세요" must not break at "좋아요").
_SENT_SPLIT = re.compile(
    r"(?<=니다)\.?\s+(?=[가-힣“\"'])"
    r"|(?<=[.!?])\s+(?=[가-힣“\"'])"
)
_SENT_END_OK = ("니다", "니다.", ".", "!", "?")


_CONNECTOR_LEAD = re.compile(r"^(그런데|그러나|하지만|반면|한편|또한|아울러|이에|이처럼|그리고|즉)\b")


def _sentences(text: str) -> list[str]:
    """Split spoken text into whole sentences; a piece that doesn't end cleanly
    is merged back, and a very short or bare-connector sentence rides WITH its
    neighbour so a 4-word line never gets its own 3-second card."""
    t = clean_text(text)
    parts = [p.strip(" ·,") for p in _SENT_SPLIT.split(t) if p.strip(" ·,")]
    out: list[str] = []
    for p in parts:
        clean = p if p.endswith(_SENT_END_OK) else (p + "." if p.endswith(("다", "요", "죠")) else p)
        rides = out and (len(p) <= 8 or not clean.endswith(_SENT_END_OK))
        if rides:
            out[-1] = out[-1].rstrip(".") + " " + p.rstrip(".") + "."
        else:
            out.append(clean if clean.endswith(_SENT_END_OK) else clean + ".")
    # second pass: fold a CONTENTLESS short line (no number, no name, just a
    # transition like "그런데 이걸 보는 눈은 이렇게 갈립니다") into the next
    # sentence so it doesn't get its own 3-second card. A short line that
    # carries a figure or a party name keeps its card.
    def _contentless(s: str) -> bool:
        if len(s) >= 24 or re.search(r"\d", s):
            return False
        if re.search(r"민주당|국민의힘|정의당|진보당|개혁신당|조국|한동훈|이재명|여당|야당", s):
            return False
        return bool(_CONNECTOR_LEAD.match(s)) or len(s) < 13

    merged: list[str] = []
    i = 0
    while i < len(out):
        cur = out[i]
        if _contentless(cur) and i + 1 < len(out) and len(cur) + len(out[i + 1]) <= 82:
            merged.append(cur.rstrip(".") + " " + out[i + 1])
            i += 2
        elif _contentless(cur) and merged and len(merged[-1]) + len(cur) <= 82:
            merged[-1] = merged[-1].rstrip(".") + " " + cur
            i += 1
        else:
            merged.append(cur)
            i += 1
    return merged or ([t + "." if t and t[-1] not in ".!?" else t] if t else [])


def _split_by_sentence(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Explode each spoken card into one card per sentence so the on-screen
    caption tracks the voice exactly. fact-check, caption-only and
    already-single-sentence cards pass straight through."""
    out: list[dict[str, Any]] = []
    for s in segments:
        nar = s.get("narration", "")
        if s.get("role") == "factcheck" or not nar:
            out.append(s)
            continue
        sents = _sentences(nar)
        if len(sents) <= 1:
            out.append(s)
            continue
        for i, sent in enumerate(sents):
            sub = dict(s)
            sub["narration"] = sent
            sub["caption"] = sent if len(sent) <= 82 else _tidy_caption(sent, 78)
            if i:                       # only the first piece keeps the kicker chip
                sub["kicker"] = ""
            sub["sub"] = i
            out.append(sub)
    return out


DISCLAIMER = (
    "이 영상은 공개된 언론 보도를 쉽게 풀어 정리한 개인 제작물입니다. "
    "인용·수치는 원문 확인이 필요하고, 해석·전망은 제작자 견해가 아니라 보도 내용을 옮긴 것입니다."
)


def _seg_seconds(seg: dict[str, Any]) -> float:
    n = seg.get("narration", "")
    if not n:
        return _SILENT_CARD_SECONDS
    return len(n) / _KR_CHARS_PER_SEC + _CARD_PAD_SECONDS


def _fit_duration(segments: list[dict[str, Any]], budget: float = MAX_VIDEO_SECONDS,
                  caps: dict[str, int] | None = None) -> list[dict[str, Any]]:
    caps = caps or _NARR_CAP

    def total() -> float:
        return sum(_seg_seconds(s) for s in segments)

    # 1) hard per-role caps — trim to a clean boundary, never mid-word/mid-sentence
    for s in segments:
        cap = caps.get(s["role"])
        if cap == 0:
            s["narration"] = ""
        elif cap and len(s.get("narration", "")) > cap:
            s["narration"] = clip_sentence(s["narration"], cap)

    # 2) trim the deck for a short video: drop the standalone summary first,
    #    then any extra what/reaction beats. hook / one what / factcheck / outro
    #    are always kept.
    if total() > budget:
        segments[:] = [s for s in segments if s["role"] != "summary"]
    for role in ("reaction", "what"):
        while total() > budget and sum(1 for s in segments if s["role"] == role) > 1:
            for i in range(len(segments) - 1, -1, -1):
                if segments[i]["role"] == role:
                    segments.pop(i)
                    break

    # 3) still over? drop a whole trailing sentence from the longest card
    guard = 0
    while total() > budget and guard < 10:
        guard += 1
        longest = max((s for s in segments if s.get("narration")),
                      key=lambda s: len(s["narration"]), default=None)
        if not longest:
            break
        sents = re.split(r"(?<=[다요.!?])\s+", longest["narration"])
        if len(sents) > 1:
            longest["narration"] = " ".join(sents[:-1])
        else:
            # one long sentence — drop its last clause at a comma / connective,
            # never mid-phrase ("…참여한 곳이" -> keep up to "…21건 가운데")
            t = longest["narration"]
            parts = re.split(r"(?<=[,·])\s+|(?<=[가-힣])(?:는데|지만|면서|고서|며)\s+", t)
            longest["narration"] = (" ".join(parts[:-1]).rstrip(" ,·") if len(parts) > 1
                                    else clip_sentence(t, int(len(t) * 0.8)))

    # 4) final polish: every spoken line is a clean, complete sentence.
    #    If trimming left a card with no complete sentence, drop it outright
    #    rather than voice a fragment ("…한국리서치가 지난 ."). Only hook /
    #    factcheck / outro are truly load-bearing; summary/what/sides are
    #    droppable.
    _ESSENTIAL = {"hook", "factcheck", "outro"}
    kept: list[dict[str, Any]] = []
    for s in segments:
        if not s.get("narration"):
            kept.append(s)
            continue
        clean = _spoken(s["narration"])
        if clean:
            s["narration"] = clean
            kept.append(s)
        elif s["role"] in _ESSENTIAL:
            s["narration"] = _spoken(s["narration"] + " ") or clip_sentence(
                s["narration"], _NARR_CAP.get(s["role"], 60)).rstrip(" ,·.") + "."
            kept.append(s)
        else:
            log.info("dropped %s card — no clean sentence after trim", s["role"])
    segments[:] = kept
    return segments


def _sources_from_rows(rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for r in rows:
        if r["source_name"] in seen:
            continue
        seen.add(r["source_name"])
        out.append({"name": r["source_name"], "url": r["url"], "lean": r["source_lean"]})
    return out


def _headline(lead_title: str) -> str:
    t = strip_wire_marks(strip_byline(lead_title))
    for sep in (" - ", " | ", " :: "):
        if sep in t:
            t = t.split(sep)[0].strip()
    t = re.sub(r"(청와대|대통령|국무총리|국회)(정책실장|비서실장|안보실장|수석|대변인)", r"\1 \2", t)
    return truncate(t.rstrip(" .·"), 46)


_CONTEXT_HINTS = ("후임", "후임자", "공석", "배경", "이유", "때문", "처음", "첫",
                  "만에", "논란", "앞서", "이어", "당분간", "대행", "겸직", "이날")


def _tokens(t: str) -> set[str]:
    return {w for w in re.split(r"[^0-9A-Za-z가-힣]+", clean_text(t)) if len(w) > 1}


def _too_similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.72


def _context_score(t: str) -> int:
    s = sum(1 for h in _CONTEXT_HINTS if h in t)
    if re.search(r"\d", t):
        s += 1
    return s


_PARTY_WORDS = ("민주당", "국민의힘", "국힘", "조국혁신당", "개혁신당", "진보당", "정의당",
                "여당", "야당", "여권", "야권", "대통령실", "청와대", "정부")
_SPEAKER_RE = re.compile(r"([가-힣]{2,4})\s*(?:수석|대변인|의원|원내대표|대표|장관|위원장|실장)")


def _speaker(text: str) -> str:
    m = _SPEAKER_RE.search(text)
    return m.group(1) if m else ""


_LEFT = ("민주당", "더불어민주당", "조국혁신당", "진보당", "정의당", "야당", "야권")
_RIGHT = ("국민의힘", "국힘", "개혁신당", "여당", "여권")


def _side_key(text: str) -> str:
    for p in _PARTY_WORDS:
        if p in text:
            return p
    return _speaker(text)


def _lean_label(text: str) -> str:
    """A concrete attribution for a reaction, never a vague '한쪽':
    the party ('국민의힘'), the person ('조국'), or '' if neither."""
    for p in _RIGHT:
        if p in text:
            return "국민의힘" if p in ("국힘",) else p
    for p in _LEFT:
        if p in text:
            return "민주당" if p in ("더불어민주당",) else p
    return _speaker(text)


_QUOTE_RE = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{4,70})[\"'“”‘’]")
_ATTRIB_TAIL = re.compile(
    r"\s*(?:라고|이라고|라며|이라며|고|며)\s*"
    r"(?:말했다|밝혔다|주장했다|지적했다|반박했다|강조했다|설명했다|촉구했다|비판했다|덧붙였다|전했다)\.?$")
_LEAD_NAME = re.compile(r"^[가-힣]{2,4}(?:\s?의원|\s?대표|\s?장관|\s?수석|\s?측)?\s*(?:은|는|이|가)\s+")


def _one_quote(text: str, limit: int = 44) -> tuple[str, bool]:
    """(core of what someone said, was_it_a_real_quoted_span).
    Prefers the quoted span; else the sentence minus its '…라고 밝혔다' tail."""
    t = clean_text(text)
    m = _QUOTE_RE.search(t)
    if m:
        return clip_sentence(m.group(1), limit).rstrip(" .,…\"'"), True
    core = _LEAD_NAME.sub("", _ATTRIB_TAIL.sub("", t))
    return clip_sentence(core, limit).rstrip(" .,…\"'"), False


def _reaction_line(claims: list) -> str:
    """A 'who said what' line — ALWAYS name who: the party, the person, or
    (when neither is identifiable) frame it as online / public reaction.
    Never a faceless '한쪽 / 다른 쪽'."""
    if not claims:
        return ""
    a = claims[0]
    la = _lean_label(a.text)
    c0, q0 = _one_quote(a.text)
    if len(c0) < 5 or len(c0) > 48:
        return ""
    b = next((c for c in claims[1:] if _lean_label(c.text) != la), None)
    c1, q1 = _one_quote(b.text) if b else ("", False)
    lb = _lean_label(b.text) if b else ""
    ok1 = 5 <= len(c1) <= 48
    if la and lb and ok1:
        return f'{josa(la, ("은", "는"))} "{c0}", {josa(lb, ("은", "는"))} "{c1}" 입장입니다.'
    if la and ok1:
        return f'{josa(la, ("은", "는"))} "{c0}"라고 밝혔고, 반론도 나옵니다.'
    if la:
        return f'{josa(la, ("은", "는"))} "{c0}"라는 입장입니다.'
    if not q0:                       # no party AND not a real quote — skip it
        return ""
    if ok1:
        return f'온라인에서는 "{c0}"라는 반응과 반대 목소리가 함께 나옵니다.'
    return f'온라인에서는 "{c0}"라는 반응이 나옵니다.'


def _sides_line(claims: list, interps: list) -> str:
    """Seed for the closing 'where the sides differ' card. The LLM then adds a
    one-line 'why' to each side. '' when there's no real dispute to lay out."""
    core = _reaction_line(claims)     # "국민의힘은 '…', 민주당은 '…' 입장입니다."
    if core:
        return "그런데 이 사안을 보는 눈은 이렇게 갈립니다. " + core
    if interps:
        it = clip_sentence(clean_text(interps[0].text), 70).rstrip(" .…")
        if len(it) >= 10:
            return f"확정된 건 아니지만, 정치권에서는 {it}는 전망이 나옵니다."
    return ""


def build_script(cluster_id: int, cfg: Settings | None = None) -> dict[str, Any]:
    cfg = cfg or settings
    with connect(cfg.db_path) as conn:
        rows = cluster_articles(conn, cluster_id)
    if not rows:
        raise ValueError(f"cluster {cluster_id} has no articles")

    lead = rows[0]
    titles = " \n ".join(r["title"] for r in rows)
    summaries = " \n ".join(r["summary"] for r in rows)
    analysis = analyze(titles, summaries)
    entities = detect_entities(titles, summaries)
    frame = detect_frame(titles, summaries)

    n_sources = len({r["source_name"] for r in rows})
    leans = sorted({r["source_lean"] for r in rows})
    headline = _headline(lead["title"])
    multi = n_sources >= cfg.min_sources_for_fact

    segments: list[dict[str, Any]] = []

    # 1) hook ---------------------------------------------------------------
    hcap, hnar = make_hook(headline, entities, frame, cfg.headline_style)
    segments.append({"role": "hook", "kicker": "오늘의 이슈", "caption": hcap, "narration": hnar})

    # 2) one-line summary -------------------------------------------------
    summary_fact = analysis.facts[0] if analysis.facts else None
    if summary_fact:
        s = simplify(summary_fact.text, add_lead=True)
    else:
        s = f"쉽게 말하면, {n_sources}개 언론이 이 사안을 나란히 보도했습니다: {headline}."
    segments.append({"role": "summary", "kicker": "한 줄 요약",
                     "caption": clip_sentence(s, CAPTION_LIMIT), "narration": s})

    # 3) what happened — a fact that is NOT just the headline restated
    sfx = summary_fact.text if summary_fact else ""
    hl_tokens = _tokens(headline) | _tokens(sfx)

    # photo-caption sentences ("…귀엣말하고 있다", "…악수하는 모습",
    # "[연합뉴스] …") leak in as "facts" — they describe a picture, not news.
    _CAPTION_RE = re.compile(
        r"(하고|되고|나누고|들으며|웃으며|서서|앉아)\s*있다\.?$|"
        r"모습(이다|\.)?$|장면(이다|\.)?$|^\[[^\]]{1,20}\]|기념\s*(촬영|사진)")

    def _adds_new(text: str) -> bool:
        tt = _tokens(text)
        if not tt:
            return False
        if _CAPTION_RE.search(clean_text(text)):
            return False
        overlap = len(tt & hl_tokens) / len(tt)
        return overlap < 0.6 and len(tt - hl_tokens) >= 3

    body = [f for f in analysis.facts[1:] if _adds_new(f.text)]
    body.sort(key=lambda f: (_context_score(f.text), f.score), reverse=True)
    if body:
        f = body[0]
        # if the outlet joined two clauses with an ellipsis, keep the longer one,
        # then trim to a clean sentence boundary
        raw = max(re.split(r"…|\.\.\.", clean_text(f.text)), key=len)
        clause = simplify(raw, limit=64)
        segments.append({"role": "what", "kicker": "무슨 일이냐면",
                         "caption": clip_sentence(clause, CAPTION_LIMIT),
                         "narration": clause,
                         "source": lead["source_name"], "multi_source": multi,
                         "cues": f.cues})
    # (no genuinely new fact -> skip the 'what' card)

    # 4) fact-check — the ONE confirmed fact (+ the on-screen 사실/주장/전망 table)
    if cfg.factcheck_segment:
        fc_rows = make_factcheck(analysis, n_sources)
        fact_row = next((r for r in fc_rows if r["tone"] == "ok"), None)
        fact_t = clip_sentence(fact_row["text"], 44).rstrip(" .…") if fact_row else ""
        narr = f"확인된 사실은 이겁니다. {fact_t}." if fact_t else \
               f"{n_sources}개 매체가 이 사안을 나란히 보도했습니다."
        segments.append({"role": "factcheck", "kicker": "확인된 사실",
                         "caption": "팩트체크", "rows": fc_rows, "narration": narr})

    # 5) sides — "그런데 해석은 갈립니다": each side's position, attributed by
    #    party/진영, with room for the LLM to add a one-line 'why'. Sits right
    #    before the outro so the back half still has something to watch.
    sides = _sides_line(analysis.claims, analysis.interpretations)
    if sides:
        segments.append({"role": "sides", "kicker": "갈리는 입장",
                         "caption": clip_sentence(sides, CAPTION_LIMIT + 12),
                         "narration": sides, "attributed": True,
                         "cues": analysis.claims[0].cues if analysis.claims else []})

    # 6) outro — one-line wrap + a subscribe/like call to action
    lean_note = ("여러 매체 보도를 종합했습니다."
                 if len(set(leans) - {"wire"}) >= 2 or len(leans) >= 3
                 else "아직 보도가 많지 않아 추가 확인이 필요합니다.")
    segments.append({"role": "outro", "kicker": "",
                     "caption": "구독과 좋아요가 큰 힘이 됩니다",
                     "narration": f"{lean_note} 이런 정치 이슈, 30초로 정리해 드립니다. "
                                  "구독과 좋아요 눌러주시면 큰 힘이 됩니다."})

    # 6b) optional — let a (free) LLM rewrite the narration into a natural,
    #     lay-friendly explanation that flows card to card. Falls back silently
    #     to the templated lines on any problem; every fact still traces to the
    #     source and the result must pass safety.review_script.
    llm_title: list[str] = []
    llm_on = bool((getattr(cfg, "llm_provider", "") or "").strip())
    if llm_on:
        try:
            from .hook import pick_actor as _pick_actor
            from .script_llm import rewrite_segments

            _LEAN_KO = {"left": "진보 성향", "right": "보수 성향", "wire": "통신·방송", "center": "중도"}
            meta = {
                "source_text": f"{titles}\n{summaries}",
                "facts": [f.text for f in analysis.facts],
                "claims": [c.text for c in analysis.claims],
                "interps": [i.text for i in analysis.interpretations],
                "entities": {"president": entities.president,
                             "politicians": entities.politicians,
                             "parties": entities.parties},
                "leans": [_LEAN_KO.get(x, x) for x in leans],
                "topic": _pick_actor(headline, entities, frame),
            }
            segments, llm_title = rewrite_segments(
                segments, meta, cfg,
                base_script={
                    "headline": headline, "frame": frame.kind,
                    "n_sources": n_sources, "sources": _sources_from_rows(rows),
                    "entities": {"president": entities.president,
                                 "politicians": entities.politicians,
                                 "parties": entities.parties,
                                 "institutions": entities.institutions},
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("llm rewrite errored, using template: %s", exc)

    segments = _fit_duration(
        segments,
        budget=MAX_VIDEO_SECONDS_LLM if llm_on else MAX_VIDEO_SECONDS,
        caps=_NARR_CAP_LLM if llm_on else _NARR_CAP,
    )

    # --- caption == what's being said, so the viewer reads ALONG with the
    #     voice. Cards are short enough now that the whole line fits the plate;
    #     only fall back to a clean clip if a line runs long. ---
    n = 0
    for s in segments:
        nar = _glyph_safe(s.get("narration", ""))
        if s["role"] == "factcheck":
            s["caption"] = clip_sentence(s.get("caption", "팩트체크"), 46, ell="..")
        elif s["role"] in ("hook", "outro") and (not llm_on) and not s.get("narration"):
            s["caption"] = _glyph_safe(clip_sentence(s.get("caption", ""), 46, ell=".."))
        elif nar:
            # full sentence when it fits the plate (~82 chars / 4-5 lines),
            # else a clean clip that still ends on a complete clause
            s["caption"] = nar if len(nar) <= 82 else _tidy_caption(nar, 78)
        s["narration"] = nar
        if s["role"] not in ("outro",):
            n += 1
            s["num"] = n

    # --- one sentence per card, so the caption and the voice stay in lockstep.
    #     (a 2-sentence 'what' card used to show only sentence 1 while the voice
    #     read sentence 2 with no caption.) fact-check / caption-only cards and
    #     already-single-sentence cards pass through unchanged. ---
    segments = _split_by_sentence(segments)

    est_seconds = round(sum(_seg_seconds(s) for s in segments), 1)
    title = [_glyph_safe(t)[:14] for t in (llm_title or make_title(headline, entities, frame))]
    from .hook import pick_actor as _pa
    topic = _pa(headline, entities, frame)
    # the on-screen chip shouldn't say "이재명" for a poll/policy story that only
    # mentions him in passing — use a short headline phrase instead.
    _prez = entities.president or ""
    if topic and topic == _prez and _prez not in clean_text(headline):
        m = re.search(r"[‘'\"“]([^’'\"”]{2,16})[’'\"”]", headline) \
            or re.match(r"\s*([가-힣]{2,6}(?:\s[가-힣]{2,6})?)", clean_text(_headline(headline)))
        if m and m.group(1).strip():
            topic = m.group(1).strip()

    # 7) images (keyless CC) + optional b-roll video -------------
    images: list[dict[str, Any]] = []
    try:
        from .images import collect_images

        images = [a.__dict__ for a in collect_images(entities, frame, headline, cfg)]
    except Exception as exc:  # pragma: no cover - network dependent
        log.warning("image collection failed: %s", exc)
    if getattr(cfg, "broll_enabled", False):
        try:
            from .footage import collect_footage

            images += [a.__dict__ for a in collect_footage(entities, frame, headline, cfg)]
        except Exception as exc:  # pragma: no cover - network dependent
            log.warning("b-roll collection failed: %s", exc)

    script: dict[str, Any] = {
        "cluster_id": cluster_id,
        "headline": headline,
        "title": title,
        "topic": topic,
        "n_sources": n_sources,
        "leans": leans,
        "frame": frame.kind,
        "entities": {"president": entities.president, "politicians": entities.politicians,
                     "parties": entities.parties, "institutions": entities.institutions},
        "segments": segments,
        "images": images,
        "est_seconds": est_seconds,
        "sources": _sources_from_rows(rows),
        "counts": {"facts": len(analysis.facts), "claims": len(analysis.claims),
                   "interpretations": len(analysis.interpretations)},
        "disclaimer": DISCLAIMER,
        "style": cfg.headline_style,
    }

    if cfg.llm_available:
        try:
            script = _llm_polish(script, cfg)
        except Exception as exc:  # pragma: no cover
            log.warning("llm polish skipped: %s", exc)

    log.info(
        "script cluster=%d segs=%d ~%.0fs frame=%s imgs=%d facts=%d claims=%d interp=%d src=%d",
        cluster_id, len(segments), est_seconds, frame.kind, len(images),
        len(analysis.facts), len(analysis.claims), len(analysis.interpretations), n_sources,
    )
    return script


def _llm_polish(script: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    """Optional: smooth hook/outro wording only. Facts and roles stay frozen."""
    from .llm import complete

    for seg in script["segments"]:
        if seg["role"] in {"hook", "outro"}:
            prompt = (
                "다음 나레이션을 뜻은 그대로, 더 자연스럽고 흡입력 있는 한국어 한두 문장으로 "
                "다듬어 주세요. 새로운 사실·주장·인물 추가 금지, 과장·허위 금지.\n"
                f"문장: {seg['narration']}\n결과:"
            )
            out = clean_text(complete(prompt, cfg, max_tokens=120))
            if out:
                seg["narration"] = truncate(out, 200)
    script["llm_polished"] = True
    return script


def script_word_estimate(script: dict[str, Any]) -> int:
    return sum(len(s.get("narration", "")) for s in script["segments"])
