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
    _NOT_TARGET, detect_entities, detect_frame, josa, make_factcheck, make_hook,
    make_title, pick_actor, simplify, strip_wire_marks, to_polite,
)
from . import invariants
from .logging_setup import get_logger
from .subtitle import _complete as _sentence_complete
from .textutil import clean_text, clip_sentence, strip_byline, truncate

log = get_logger("script_gen")

CAPTION_LIMIT = 46
BODY_LIMIT = 72
MAX_WHAT = 1

# Top-performing news shorts run tight — 20-38s. Aim ~30-36s: hook opens a
# loop, the summary card is dropped, 4-5 fast cards.
# the template path now carries real background + significance + meaning
# (explain.py), not one-line clips, so it needs room like the LLM path — a
# viewer who doesn't follow politics needs the explanation more than a 30s cut.
MAX_VIDEO_SECONDS = 66.0
# the LLM writes real background + explanation now, so a card can run longer
# and the whole video too — a viewer who doesn't follow politics needs it.
MAX_VIDEO_SECONDS_LLM = 110.0
_KR_CHARS_PER_SEC = 7.0          # edge-tts at ~+13% rate (TTS_RATE 198)
_CARD_PAD_SECONDS = 0.24         # brief breath between cards
# hard per-segment narration caps (chars). 0 = caption-only card, no voice.
_NARR_CAP = {"hook": 42, "summary": 155, "what": 180, "reaction": 90,
             "factcheck": 170, "sides": 150, "outro": 150}
# sides raised 230->260 alongside script_llm._LLM_LIMIT["sides"] — user
# wanted the "sides" card able to carry a real 3-voice structure (both
# parties + an expert/third-party view when the source actually has one),
# not just two short attributed lines.
# summary/what/sides/outro raised again with the research stage: those cards now
# carry background + timeline, a full quote, and pros/cons, not just a re-telling.
_NARR_CAP_LLM = {"hook": 66, "summary": 210, "what": 250, "reaction": 120,
                 "factcheck": 170, "sides": 340, "outro": 170}
_SILENT_CARD_SECONDS = 1.5

_SENT_END = ("다", "요", "죠", "까", "네", "군", ".", "!", "?", "…")
# a chunk ending on a REAL sentence end: a formal "…니다" ending, a plain-style
# verb ending, or .!? — NOT a bare "…다" ("찬성보다", "그에 따라" would falsely
# match and drop the rest of the sentence).
_SENT_CHUNK = re.compile(
    r".+?(?:니다(?=[\s\"')\].!?]|$)"
    r"|(?:했다|한다|된다|이다|아니다|았다|었다|겠다|온다|난다|봤다|랬다)(?=[\s\"')\].!?]|$)"
    r"|[.!?](?=\s|$))"
)
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
        if good[-1] in ".!?":
            return good
        if _sentence_complete(good):
            return good + "."
        # long enough to pass the ratio check, but _SENT_CHUNK matched a
        # fragment with no real predicate ("...전략과" — 과 has no verb) — a
        # real shipped case where this blindly appended "." and shipped a
        # fragment that read as finished. Fall through to the salvage path
        # below instead of trusting length alone.
    # no complete sentence — salvage only if a clause-drop leaves a real
    # predicate ending; otherwise return '' so the caller drops the card.
    t = _TRAIL_JUNK.sub("", t).strip(" ,·")
    if len(t) >= 8 and (t[-1] in ".!?" or t.endswith(
            ("다", "요", "죠", "까", "음", "됨", "함", "임", "것", "중"))):
        return t if t[-1] in ".!?" else t + "."
    return ""


# generic 2-char word stems that say nothing about WHICH story two headlines share
_GENERIC_STEMS = {"국회", "정부", "대통", "여당", "야당", "민주", "국힘", "정치", "논란", "의혹", "발언", "입장",
                  "비판", "반박", "사퇴", "결정", "관련", "오늘", "이번", "지난", "청와", "후보", "대표", "의원"}


# 3-syllable name stems too common to identify an event
_GENERIC_NAMES = {"국민의", "더불어", "대통령", "청와대", "기자회", "대변인", "후보자", "국회의", "이재명", "이대통",
                  "의원들", "정부는", "여야는", "민주당", "관계자", "이번에", "오늘은"}
_SAME_EVENT_SH3 = 0.06      # measured on real data: unrelated pairs top out at ~0.05, same-story pairs 0.10-0.23
_SAME_EVENT_WINDOW_S = 36 * 3600


def _event_names(text: str) -> set[str]:
    """Proper-noun-like anchors: 3-syllable Korean stems ("경기도", "김승원") and acronyms ("DMZ"). Two-syllable
    words are ignored on purpose, so "경기도" (the province) never matches "경기 침체" (the economy)."""
    t = clean_text(text or "")
    return ({w[:3] for w in re.findall(r"[가-힣]{3,}", t)} | set(re.findall(r"[A-Z]{2,}", t))) \
        - _GENERIC_NAMES - {w[:3] for w in _GENERIC_STEMS}


def _shingles(text: str, n: int = 3) -> set[str]:
    s = re.sub(r"[^가-힣A-Za-z0-9]", "", clean_text(text or ""))
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _related_reports(cluster_id: int, rows: list[Any], cfg: Settings, limit: int = 4) -> list[dict[str, Any]]:
    """Other clusters that cover the SAME event. A newsroom splits one event by actor ("김민석 비판"
    / "경기도 반박"), the clusterer keeps them apart, and each video then voices only one side. Two
    stories are the same event only if ALL hold: they share a proper noun, their title+summary text
    overlaps (3-syllable shingles, threshold measured on real data), and they were published within
    36 hours of each other. Each accepted pair is logged with its evidence."""
    mine_text = " ".join(f"{r['title']} {r['summary'] or ''}" for r in rows[:4])
    mine_names = _event_names(" ".join(str(r["title"]) for r in rows))
    mine_sh = _shingles(mine_text)
    mine_ts = max((int(r["published_ts"]) for r in rows if _has_ts(r)), default=0)
    if not mine_names or not mine_sh:
        return []
    with connect(cfg.db_path) as conn:
        others = conn.execute(
            "SELECT cluster_id, title, summary, source_name, source_lean, published_ts FROM articles "
            "WHERE cluster_id IS NOT NULL AND cluster_id != ? ORDER BY source_weight DESC", (cluster_id,)
        ).fetchall()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for r in others:
        if r["cluster_id"] in seen:
            continue
        shared = mine_names & _event_names(str(r["title"]))
        if not shared:
            continue
        sh = _shingles(f"{r['title']} {r['summary'] or ''}")
        score = len(mine_sh & sh) / max(1, len(mine_sh | sh))
        if score < _SAME_EVENT_SH3:
            continue
        if mine_ts and _has_ts(r) and abs(int(r["published_ts"]) - mine_ts) > _SAME_EVENT_WINDOW_S:
            continue
        seen.add(r["cluster_id"])
        log.info("related: cluster %d ~ cluster %d (shared=%s, sh3=%.2f) %s", cluster_id, r["cluster_id"],
                 ",".join(sorted(shared))[:30], score, str(r["title"])[:40])
        out.append({"cluster_id": r["cluster_id"], "title": clean_text(r["title"]),
                    "summary": truncate(clean_text(r["summary"] or ""), 380),
                    "source": r["source_name"] or "", "lean": r["source_lean"] or ""})
        if len(out) >= limit:
            break
    return out


def _has_ts(row: Any) -> bool:
    try:
        return row["published_ts"] is not None and int(row["published_ts"]) > 0
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def _ensure_comment_prompt(segments: list[dict[str, Any]], question: str) -> None:
    """Every video ends by asking the viewer for an opinion. The LLM is told to, but
    this makes it certain: an outro without a comment prompt gets the question appended
    (replacing a bare "구독과 좋아요" ask, which gives viewers nothing to do)."""
    for s in segments:
        if s.get("role") != "outro":
            continue
        n = s.get("narration", "") or ""
        if "댓글" in n:
            return
        n = re.sub(r"\s*구독과?\s*좋아요[^.!?]*[.!?]?", "", n).strip()
        if re.search(r"\?|보시나요|생각하시나요|어떠신가요", n):
            # the writer already asked its own (story-specific) question — a real published
            # video ended with TWO opinion questions back to back; just add the ask to comment
            s["narration"] = f"{n} 댓글로 의견을 남겨주세요.".strip()
        else:
            s["narration"] = f"{n} {question}".strip()
        return


def _research_rich(web: dict[str, Any]) -> bool:
    """Enough real material (quotes / positions / pros-cons) to write an analysis
    instead of a re-telling."""
    return sum(bool(web.get(k)) for k in ("why", "statements", "positions", "pros", "cons")) >= 2


def _with_research_cards(segments: list[dict[str, Any]], web: dict[str, Any]) -> list[dict[str, Any]]:
    """The LLM can only rewrite cards that exist. When the seed articles carried
    no quote or party positions the template has no "what"/"sides" card, so the
    researched material had nowhere to go. Add those two cards (with a safe,
    attributed draft taken from the research) so the writer can fill them."""
    out = [dict(s) for s in segments]
    have = {s["role"] for s in out}

    if "what" not in have:
        st = next((x for x in web.get("statements") or []
                   if isinstance(x, dict) and x.get("text") and x.get("who")), None)
        if st:
            who = str(st["who"]).strip()
            draft = f"{who}{josa(who, ('은', '는'))[len(who):]} \"{str(st['text']).strip()}\"라고 밝혔습니다."
            idx = max((i for i, x in enumerate(out) if x["role"] == "summary"), default=0) + 1
            out.insert(idx, {"role": "what", "kicker": "무슨 일이고 왜 중요하냐면",
                             "caption": clip_sentence(draft, CAPTION_LIMIT), "narration": draft,
                             "cues": []})
    if "sides" not in have:
        pos = next((x for x in web.get("positions") or []
                    if isinstance(x, dict) and x.get("who") and x.get("position")), None)
        if pos:
            who = str(pos["who"]).strip()
            draft = f"{who}{josa(who, ('은', '는'))[len(who):]} {str(pos['position']).strip().rstrip('.')}는 입장입니다."
            idx = next((i for i, x in enumerate(out) if x["role"] == "outro"), len(out))
            out.insert(idx, {"role": "sides", "kicker": "갈리는 입장",
                             "caption": clip_sentence(draft, CAPTION_LIMIT + 12), "narration": draft,
                             "attributed": True, "cues": []})
    return out


def _mark_incomplete_factcheck_rows(segments: list[dict[str, Any]]) -> None:
    """UNIVERSAL backstop for the factcheck table, mutating in place: whichever
    path actually built a row (factcheck.rows()'s template _clip(), or the
    LLM's facts_table via script_llm._clean_row — both independently fixed
    for this same failure mode, and it still shipped once more in production
    from a path neither of those traces could pin down), a row must never
    look finished when it isn't. Skips the "확인" row — a fixed UI label
    ("N개 매체 종합, 원문은 더보기란"), not a narrative claim."""
    for s in segments:
        if s.get("role") != "factcheck" or not s.get("rows"):
            continue
        for r in s["rows"]:
            if r.get("tone") == "info":
                continue
            t = (r.get("text") or "").rstrip()
            if t and not t.endswith("..") and not _sentence_complete(t):
                r["text"] = f"{t}.."


# connective / particle tails that must not be the last thing on a caption card
_CAP_TAIL = re.compile(
    r"\s*[가-힣]{0,6}?(따르면|밝히며|말하며|라며|이라며|하며|면서|는데|지만|라고|"
    r"이라고|대해|위해|통해|관련|둘러싸고|며|면|고|은|는|이|가|을|를|에|의|와|과|도|만|께|"
    r"에서|으로|에게)$"
)


# "5·18" is one name (the Gwangju uprising), not the numbers "5" and "18": with the middle dot swapped for
# ", " it was voiced "5, 18" and quality.py flagged a number the article never had. Say the name.
_KNOWN_DATES = {"5·18": "오일팔", "4·19": "사일구", "6·25": "육이오", "3·1": "삼일", "8·15": "팔일오",
                "5·16": "오일육", "12·12": "십이십이", "4·3": "사삼", "6·10": "육일공", "5·18": "오일팔"}
_KNOWN_DATES_RX = re.compile(r"(?<![\d.])(\d{1,2})[·ㆍ](\d{1,2})(?![\d])")


def _glyph_safe(s: str) -> str:
    """The bundled caption fonts have no ·/…/—/→ glyph (they render as tofu
    — a real shipped case: "20석→15석" rendered as "20석□15석" on screen)."""
    s = _KNOWN_DATES_RX.sub(lambda m: _KNOWN_DATES.get(f"{m.group(1)}·{m.group(2)}", m.group(0)), s)
    return (s.replace("·", ", ").replace("ㆍ", ", ").replace("…", " ")
             .replace("—", "-").replace("–", "-").replace("~", "-")
             .replace("→", "->").replace("←", "<-"))


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
        if len(s) >= 26 or re.search(r"\d", s):
            return False
        if re.search(r"민주당|국민의힘|정의당|진보당|개혁신당|조국|한동훈|이재명|여당|야당", s):
            return False
        # a bare transition: starts with a connector, OR is just "…엇갈렸습니다 /
        # …갈립니다 / …다릅니다" with no subject worth its own card
        if bool(_CONNECTOR_LEAD.match(s)) or len(s) < 13:
            return True
        return bool(re.search(r"(엇갈|갈립|갈렸|나뉘|나뉜|다릅니다|다른데|팽팽)", s))

    merged: list[str] = []
    i = 0
    while i < len(out):
        cur = out[i]
        if _contentless(cur) and i + 1 < len(out):
            merged.append(cur.rstrip(".") + " " + out[i + 1])   # fold forward always
            i += 2
        elif _contentless(cur) and merged:
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
            # caption is the card's ONE compressed key message (set upstream) —
            # every sentence-piece keeps it; only the voice changes per piece
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
    # the UNTOUCHED background text, captured before any trimming below can
    # mangle it — step 4's floor falls back to THIS, not to whatever step 3
    # left behind, so a floor clip is always taken from a clean sentence.
    _summary_seed = next((s.get("narration", "") for s in segments if s["role"] == "summary"), "")

    def total() -> float:
        return sum(_seg_seconds(s) for s in segments)

    # 1) hard per-role caps — trim to a clean boundary, never mid-word/mid-sentence
    for s in segments:
        cap = caps.get(s["role"])
        if cap == 0:
            s["narration"] = ""
        elif cap and len(s.get("narration", "")) > cap:
            s["narration"] = clip_sentence(s["narration"], cap)

    # 2) trim the deck for a short video: drop any EXTRA what/reaction beats
    #    beyond the first one. hook / one what / factcheck / outro are always
    #    kept. The standalone summary (background) is NOT touched here —
    #    a viewer who doesn't follow politics needs the background more than
    #    a second "what happened" elaboration, so it's no longer the first
    #    thing this function sacrifices (see step 4).
    for role in ("reaction", "what"):
        while total() > budget and sum(1 for s in segments if s["role"] == role) > 1:
            for i in range(len(segments) - 1, -1, -1):
                if segments[i]["role"] == role:
                    segments.pop(i)
                    break

    # 3) still over? drop a trailing sentence from the longest card, one round
    #    at a time — never from the hook or factcheck (the verified-fact
    #    payload). TWO passes, so background pays LAST, not first or evenly:
    #    3a) "sides"/"what" absorb the pressure first — "sides" used to be
    #        fully protected as "the payoff", which meant a claim-heavy story
    #        spent its whole budget there and squeezed summary out instead.
    #        The user's own priority: background explanation matters more
    #        than a full airing of every side's claims when something has to
    #        give.
    #    3b) only if that alone wasn't enough does summary itself start
    #        shrinking — shorter, not gone (step 4 is the true last resort).
    def _shrink_longest(pool: list[dict[str, Any]]) -> bool:
        longest = max(pool, key=lambda s: len(s["narration"]), default=None)
        if not longest:
            return False
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
        return True

    guard = 0
    while total() > budget and guard < 12:
        guard += 1
        # a card's LAST sentence is never shrunk away: trimming used to delete a
        # one-sentence what/sides card outright ("dropped what card — no clean
        # sentence after trim"), i.e. the analysis was the first thing sacrificed.
        pool = [s for s in segments if s.get("narration") and s["role"] in ("what", "sides")
                and len(re.split(r"(?<=[다요.!?])\s+", s["narration"])) > 1]
        if not _shrink_longest(pool):
            break
    guard = 0
    while total() > budget and guard < 12:
        guard += 1
        pool = [s for s in segments if s.get("narration") and s["role"] == "summary"]
        if not _shrink_longest(pool):
            break

    # 4) previously a TRUE last resort that dropped the standalone summary/
    #    background card entirely once step 3 left it too mangled to voice. A
    #    real user complaint: on a thin "brief" story this left hook -> raw
    #    fact dump -> outro with NO background at all for a viewer who
    #    doesn't follow politics — "이게 무슨 소리야". Summary now has a hard
    #    floor instead: whenever what step 3 left behind isn't a genuinely
    #    complete clause (broken fragment, or trimmed away to nothing), it's
    #    restored from the UNTOUCHED original, clipped to one short complete
    #    clause — never just removed. Runs regardless of whether we're still
    #    over budget: a few seconds over on a thin story beats zero context.
    if _summary_seed:
        for s in segments:
            if s["role"] != "summary":
                continue
            cur = s.get("narration", "")
            if cur and _sentence_complete(cur):
                continue                        # step 3 already left something usable
            floor = clip_sentence(_summary_seed, 44)
            if _sentence_complete(floor):
                s["narration"] = floor

    # 4) final polish: every spoken line is a clean, complete sentence.
    #    If trimming left a card with no complete sentence, drop it outright
    #    rather than voice a fragment ("…한국리서치가 지난 ."). Only hook /
    #    factcheck / outro are truly load-bearing; summary/what/sides are
    #    droppable.
    _ESSENTIAL = {"hook", "summary", "factcheck", "outro"}
    kept: list[dict[str, Any]] = []
    for s in segments:
        if not s.get("narration"):
            kept.append(s)
            continue
        clean = _spoken(s["narration"])
        if not clean and s["role"] in _ESSENTIAL:
            # keep the last complete sentence, or a clean clip, rather than lose
            # a load-bearing card
            whole = re.split(r"(?<=니다[.!?])\s+|(?<=[.!?])\s+", s["narration"])
            clean = next((w for w in whole if w.strip().endswith(("니다", "니다.", ".", "!", "?"))), "")
            if not clean:
                # last resort: a straight clip. A real shipped case: this
                # blindly appended "." to whatever clip_sentence returned —
                # "법무부 장관 후보자 김승원은 판사 출신이자 국회." (no verb
                # after "국회"). Only accept it if it genuinely ends on a
                # predicate; otherwise fall through and drop the card below
                # rather than fabricate a period onto a fragment.
                clipped = clip_sentence(s["narration"], _NARR_CAP.get(s["role"], 60)).rstrip(" ,·.")
                clean = f"{clipped}." if clipped and _sentence_complete(clipped) else ""
        if clean:
            s["narration"] = clean
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

# a sentence that's mostly a long embedded quotation reads badly chopped into
# a 1.5-3.5s scene (nested quote marks, no natural clause break to split on) —
# prefer a plainer narrative FACT sentence for the "what" card when one exists.
_QUOTE_SPAN = re.compile(r"[“\"][^”\"]{10,}[”\"]|[‘'][^’']{10,}[’']")


def _quote_ratio(t: str) -> float:
    spans = _QUOTE_SPAN.findall(t)
    return sum(len(s) for s in spans) / max(len(t), 1)


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
    if _quote_ratio(t) >= 0.5:
        s -= 2
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
# a bare "…을/를 지적했다 / …라고 우려했다" tail with NO opening quote — trim it
# so the sides line quotes the substance, not the reporting verb.
_VERB_TAIL = re.compile(
    r"\s*(?:[을를이가]\s*)?(?:지적|비판|우려|반박|강조|촉구|주장|반발|해명|경고|규탄|"
    r"성토|일축|해석|평가|전망|관측|시사|부인|해석)(?:하고\s*나섰다|했다|한다|했습니다|하며)\.?$")
_LEAD_NAME = re.compile(r"^[가-힣]{2,4}(?:\s?의원|\s?대표|\s?장관|\s?수석|\s?측)?\s*(?:은|는|이|가)\s+")


def _one_quote(text: str, limit: int = 44) -> tuple[str, bool]:
    """(core of what someone said, was_it_a_real_quoted_span).
    Prefers the quoted span; else the sentence minus its '…라고 밝혔다' /
    '…을 지적했다' reporting tail and its leading '누구는 '."""
    t = clean_text(text)
    m = _QUOTE_RE.search(t)
    if m:
        return clip_sentence(m.group(1), limit).strip().rstrip(" .,…\"'"), True
    core = _LEAD_NAME.sub("", _ATTRIB_TAIL.sub("", t))
    for _ in range(2):
        c2 = _VERB_TAIL.sub("", core).rstrip(" .,…\"'")
        if c2 == core or len(c2) < 6:
            break
        core = c2
    return clip_sentence(core, limit).strip().rstrip(" .,…\"'"), False


def _reaction_line(claims: list) -> str:
    """A 'who said what' line — ALWAYS name who: the party, the person, or
    (when neither is identifiable) frame it as online / public reaction.
    Never a faceless '한쪽 / 다른 쪽'."""
    if not claims:
        return ""
    # side A = the first claim we can actually attribute (a party or a named
    # person), NOT just claims[0] — that is often an unattributed "대통령실은…".
    scored = []
    for c in claims:
        lab = _lean_label(c.text)
        core, real = _one_quote(c.text)
        if 5 <= len(core) <= 48:
            scored.append((c, lab, core, real))
    if not scored:
        return ""
    a = next((x for x in scored if x[1]), scored[0])
    _, la, c0, q0 = a
    b = next((x for x in scored if x is not a and x[1] and x[1] != la), None)
    lb, c1 = (b[1], b[2]) if b else ("", "")
    if la and lb:
        return f'{josa(la, ("은", "는"))} "{c0}", {josa(lb, ("은", "는"))} "{c1}" 쪽입니다.'
    if la:
        # user: "여당 야당 입장은 있으면 추가하는 시스템으로" — only one side
        # is actually attributable here, so say only that one. Used to add
        # ", 다른 목소리도 있습니다" to gesture at an unnamed second side
        # that was never actually extracted from the source — a vague
        # implied counter-view, not a real one.
        obj = josa(c0, ("을", "를"))[len(c0):]          # just the 을/를 particle for c0
        return f'{josa(la, ("은", "는"))} "{c0}"{obj} 문제 삼고 있습니다.'
    if not q0:                       # no party AND not a real quote — skip it
        return ""
    return f'온라인에서는 "{c0}"라는 반응이 나옵니다.'


def _sides_line(claims: list, interps: list) -> str:
    """Seed for the closing 'where the sides differ' card. The LLM then adds a
    one-line 'why' to each side. '' when there's no real dispute to lay out —
    the card is non-essential and a skipped card beats an awkward one.
    (`interps` was a fallback source but it echoed headlines into broken
    grammar; the LLM rewrite handles the no-reaction case instead.)"""
    core = _reaction_line(claims)     # "국민의힘은 '…', 민주당은 '…' 입장입니다."
    if core:
        return "그런데 이 사안을 보는 눈은 이렇게 갈립니다. " + core
    return ""


def build_script(cluster_id: int, cfg: Settings | None = None, *,
                 skip_llm_if: Any = None, allow_thin: bool = False) -> dict[str, Any]:
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

    # FACT CHECK ENGINE — structured, source-attributed units (FACT / QUOTE /
    # INTERPRETATION / UNCERTAIN) with cross-source confidence.
    from .factcheck import extract as _fc_extract
    fc = _fc_extract(rows)

    n_sources = len({r["source_name"] for r in rows})
    leans = sorted({r["source_lean"] for r in rows})
    headline = _headline(lead["title"])
    multi = n_sources >= cfg.min_sources_for_fact

    # target length by story type (속보 25-40s … 복잡한 사건 70-90s)
    from .storylen import classify_length
    lp = classify_length(analysis, frame, entities, n_sources, leans)

    segments: list[dict[str, Any]] = []

    _actor = pick_actor(headline, entities, frame)

    # 1) hook — a dedicated engine builds the first ~2s (surprise / twist /
    #    question / outcome-first / conflict / number), never the raw headline.
    from .hook_engine import HookContext, build_hook
    hk = build_hook(HookContext(
        headline=headline,
        facts=[f.text for f in analysis.facts],
        claims=[c.text for c in analysis.claims],
        frame=frame, entities=entities, actor=_actor, n_sources=n_sources,
    ))
    hcap, hnar = hk.caption, hk.narration
    if not hnar or len(hnar) < 8:                       # engine gave up -> template
        hcap, hnar = make_hook(headline, entities, frame, cfg.headline_style)
    segments.append({"role": "hook", "kicker": "오늘의 이슈",
                     "caption": hcap, "narration": hnar, "hook_kind": hk.kind})

    # 2) summary card — BACKGROUND: who/what is involved + a term gloss, then
    #    the core fact. A viewer who doesn't follow politics starts here.
    from . import explain
    summary_fact = analysis.facts[0] if analysis.facts else None
    bg = explain.background(_actor, headline, entities, frame)
    core = simplify(summary_fact.text, add_lead=False) if summary_fact else ""
    if bg and core and _actor and core.startswith(_actor):
        # background already introduced the person — drop a repeated
        # "{name} {role}이/가" subject; Korean lets the subject be understood
        stripped = re.sub(
            rf"^{re.escape(_actor)}(?:\s+[가-힣]+){{0,2}}?\s*(?:은|는|이|가)\s+", "", core)
        if 6 <= len(stripped) < len(core):
            core = stripped
    if bg and core:
        s = f"{bg} {core}"
    elif bg:
        s = f"{bg} 오늘 이와 관련한 소식이 나왔습니다."
    elif core:
        s = f"쉽게 말하면, {core}"
    else:
        s = f"{n_sources}개 매체가 이 사안을 나란히 보도했습니다."
    segments.append({"role": "summary", "kicker": "배경부터",
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
        # Just the fact — explain.significance(frame) used to be appended
        # here too, which duplicated the outro's own payoff line verbatim
        # whenever the LLM narration rewrite was off/unavailable (raw
        # template shipped as-is): a real CI-built video closed by
        # literally repeating its own "what happened" card word for word.
        # "why this matters" now lives in exactly one place — the outro.
        narr = clause.rstrip('.') + '.'
        segments.append({"role": "what", "kicker": "무슨 일이고 왜 중요하냐면",
                         "caption": clip_sentence(clause, CAPTION_LIMIT),
                         "narration": narr,
                         "source": lead["source_name"], "multi_source": multi,
                         "cues": f.cues})
    # else: no second fact worth a distinct card — dropped rather than
    # filled with explain.significance(frame) as a standalone "card", which
    # is the outro's own line verbatim (see above). "what" isn't in
    # _ESSENTIAL below, so skipping it here is already an anticipated,
    # safe shape — a video with nothing more to say about "what happened"
    # goes straight from summary to factcheck instead of padding itself
    # with the same sentence the outro is about to say anyway.

    # 4) fact-check — the ONE confirmed fact (+ the on-screen 사실/주장/전망 table),
    #    now source-attributed and confidence-ranked by the FACT CHECK ENGINE.
    if cfg.factcheck_segment:
        fc_rows = fc.rows(n_sources) or make_factcheck(analysis, n_sources)
        fact_row = next((r for r in fc_rows if r["tone"] == "ok"), None)
        fact_t = to_polite(clip_sentence(fact_row["text"], 46).rstrip(" .…")) if fact_row else ""
        if fact_t:
            meaning = explain.meaning(frame)
            narr = f"{fact_t.rstrip('.')}." + (f" {meaning}" if meaning else "")
        else:
            narr = f"{n_sources}개 매체가 이 사안을 나란히 보도했습니다."
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

    # 6) outro — the story's real payoff, THEN the CTA. Used to lead with
    #    "여러 매체 보도를 종합했습니다" — production-process commentary, not
    #    a story payoff. That trust signal already lives on the factcheck
    #    "확인" row; the outro now leads with why this actually matters
    #    (explain.significance), so the video closes on the story, not on
    #    how it was made. A thin-sourcing caveat still rides along when it's
    #    genuinely warranted (real transparency, not filler).
    payoff = explain.significance(frame).rstrip(" .")
    caveat = ("" if len(set(leans) - {"wire"}) >= 2 or len(leans) >= 3
              else " 아직 보도가 많지 않아 추가 확인이 필요합니다.")
    _engage = explain.engage_question(frame, pick_actor(headline, entities, frame))
    segments.append({"role": "outro", "kicker": "",
                     "caption": "여러분의 생각은? 댓글로 남겨주세요",
                     "narration": f"{payoff}.{caveat} {_engage}"})

    # 6b) optional — let a (free) LLM rewrite the narration into a natural,
    #     lay-friendly explanation that flows card to card. Falls back silently
    #     to the templated lines on any problem; every fact still traces to the
    #     source and the result must pass safety.review_script.
    #
    #     AI QUALITY AGENT: a second, semantic gate layered on top of
    #     quality.py's mechanical checks (sync/length/layout). This session
    #     found repeatedly that a script can pass every mechanical check —
    #     complete sentences, in-budget length — while still asking about the
    #     wrong person, missing all background, or closing on a banned vague
    #     cliché; quality.py has no "does this story make sense" check. Below
    #     the score bar, its own critique is fed back into ONE more rewrite
    #     and re-graded, up to 4 total attempts, before giving up honestly.
    #     `script["quality_agent"]` records the outcome either way — pipeline
    #     skips the cluster if it never cleared the bar.
    from . import quality_agent

    llm_title: list[str] = []
    # pipeline._process_story skips a cluster this thin outright once
    # build_script returns (n_sources<=1 and facts+claims+interp<=3) — no
    # point burning up to 4 quality-agent LLM calls polishing a script that
    # will be discarded anyway regardless of how good the rewrite is.
    _material = len(analysis.facts) + len(analysis.claims) + len(analysis.interpretations)
    # allow_thin: a story picked ON PURPOSE (--focus) is researched even when its seed articles
    # are few — the research stage exists to supply the missing material
    material_thin = n_sources <= 1 and _material <= 3 and not allow_thin
    # A story the pipeline is about to throw away (already covered / topic saturated) must not
    # cost 15-30 LLM calls first: `skip_llm_if(preview)` lets the caller veto the LLM/research
    # stages now, using only what is known before any LLM call. The cheap template script that
    # comes back is discarded by the same checks the caller runs afterwards.
    _skip_llm = False
    if skip_llm_if is not None and not material_thin:
        try:
            _skip_llm = bool(skip_llm_if({
                "headline": headline, "topic": _actor, "frame": frame.kind,
                "entities": {"president": entities.president, "politicians": entities.politicians,
                             "parties": entities.parties, "institutions": entities.institutions}}))
        except Exception as exc:  # pragma: no cover - defensive
            log.info("early skip check failed (%s)", str(exc)[:80])
        if _skip_llm:
            log.info("no LLM spent on %r — it is already covered / saturated", headline[:40])
    llm_on = (bool((getattr(cfg, "llm_provider", "") or "").strip())
              and not material_thin and not _skip_llm)
    template_segments = [dict(s) for s in segments]
    agent_report = None
    agent_attempts = 0
    research_web: dict[str, Any] = {}
    chosen_viol: list[dict[str, str]] = []
    absorbed: list[dict[str, Any]] = []
    research_text = ""
    if llm_on:
        from .hook import pick_actor as _pick_actor
        from .script_llm import rewrite_segments

        _LEAN_KO = {"left": "진보 성향", "right": "보수 성향", "wire": "통신·방송", "center": "중도"}
        meta = {
            "headline": headline,
            "related": _related_reports(cluster_id, rows, cfg),
            "source_text": f"{titles}\n{summaries}",
            # classified + cross-source-verified view (FACT CHECK ENGINE)
            "facts": [u.text for u in fc.facts] or [f.text for f in analysis.facts],
            "claims": [u.text for u in fc.quotes] or [c.text for c in analysis.claims],
            "interps": [u.text for u in fc.interpretations] or [i.text for i in analysis.interpretations],
            "entities": {"president": entities.president,
                         "politicians": entities.politicians,
                         "parties": entities.parties},
            "leans": [_LEAN_KO.get(x, x) for x in leans],
            "topic": _pick_actor(headline, entities, frame),
        }
        # RESEARCH: the seed articles are only the skeleton — gather extra news,
        # web/official statements and YouTube signals so the writer explains the
        # story instead of re-telling those articles. Best-effort and cached.
        absorbed = list(meta.get("related") or [])
        meta["research"] = ""
        research_web: dict[str, Any] = {}
        if getattr(cfg, "research_enabled", True):
            from . import research
            try:
                _pack = research.build_pack(
                    headline, meta["topic"], cfg,
                    context="\n".join(meta["facts"][:8] + meta["claims"][:4]),
                    seed_leans=leans)
                meta["research"] = research.pack_block(_pack)
                research_web = (_pack or {}).get("web") or {}
                meta["missing_leans"] = list(research_web.get("missing_leans") or [])
            except Exception as exc:  # pragma: no cover - defensive
                log.info("research skipped (%s)", str(exc)[:80])
        research_text = str(meta.get("research", "") or "")
        rich = _research_rich(research_web)
        if rich:
            template_segments = _with_research_cards(template_segments, research_web)
        base_script_meta = {
            "headline": headline, "frame": frame.kind,
            "n_sources": n_sources, "sources": _sources_from_rows(rows),
            "entities": {"president": entities.president,
                         "politicians": entities.politicians,
                         "parties": entities.parties,
                         "institutions": entities.institutions},
        }
        # feedback ACCUMULATES across attempts rather than being replaced —
        # a real observed failure mode: passing only the latest critique let
        # a rewrite silently re-break something an earlier attempt had
        # already fixed (scores oscillating 45->85->85->45 instead of
        # climbing). Every rewrite now sees every problem ever flagged.
        # Attempts don't always improve monotonically either, so the BEST-
        # scoring attempt is what ships — even one that never reaches
        # PASS_SCORE still gets the highest-quality version actually tried,
        # never just whatever the last, possibly-worse, attempt produced.
        feedback_history: list[str] = []
        best_score = -1
        best_segments, best_title, best_report = segments, llm_title, None
        best_viol: list[dict[str, str]] = []
        stale = 0
        # an analysis with real background, a full quote, positions and pros/cons
        # needs more room than a one-fact brief — lift the target to ~120s (budget ~96s)
        _budget = max((max(lp.target_s, 120.0) if rich else lp.target_s) * 0.80, 26.0)
        # 3 attempts, not 4: in a real run the 4th never beat the best of the first three (70>75>81>80, 78>85>85>78, 70>85>85>70) — it only cost LLM calls
        for agent_attempts in range(1, 4):
            try:
                cand, cand_title = rewrite_segments(
                    [dict(s) for s in template_segments], meta, cfg,
                    base_script=base_script_meta, feedback="\n".join(feedback_history),
                )
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("llm rewrite errored, using template: %s", exc)
                cand, cand_title = [dict(s) for s in template_segments], []

            cand = [dict(s) for s in cand]
            _mark_incomplete_factcheck_rows(cand)
            cand = _fit_duration(cand, budget=_budget, caps=_NARR_CAP_LLM)
            _ensure_comment_prompt(cand, explain.engage_question(frame, pick_actor(headline, entities, frame)))

            agent_report = quality_agent.review({"headline": headline, "segments": cand}, cfg)
            # mechanical post-conditions (invariants.py) — checked in code, not by another LLM
            viol = invariants.check(cand, research_web,
                                    research_expected=bool(getattr(cfg, "research_enabled", True)))
            cand_score = agent_report.score if agent_report.available else -1
            eff_score = cand_score - 10 * len(viol)              # a violated invariant outranks a nicer style
            if eff_score > best_score:
                best_score, best_segments, best_title, best_report = (
                    eff_score, cand, cand_title, agent_report)
                best_viol = viol
                stale = 0
            else:
                stale += 1

            if (not agent_report.available or agent_report.score >= quality_agent.PASS_SCORE) and (
                    not viol or not agent_report.available):
                segments, llm_title = cand, cand_title
                chosen_viol = viol
                break
            if viol:
                feedback_history.append(f"[{agent_attempts}차 구조 검사 — 반드시 고칠 것]\n"
                                        + "\n".join(f"- {v['message']}" for v in viol))
                log.info("invariants violated (%s)", ", ".join(v["code"] for v in viol))
            issue_line = agent_report.feedback_text()
            if issue_line:
                feedback_history.append(f"[{agent_attempts}차 시도 문제점]\n{issue_line}")
            log.info("quality agent attempt %d scored %s (best so far %d) — regenerating: %s",
                     agent_attempts, agent_report.score, best_score, issue_line[:300] or "(no issues text)")
            if agent_attempts >= 2 and stale >= 1:
                # this rewrite did not beat the best so far: more rounds oscillate, they don't climb
                log.info("quality agent plateaued at %d — stopping rewrites early", best_score)
                segments, llm_title, agent_report = best_segments, best_title, best_report
                chosen_viol = best_viol
                break
        else:
            # exhausted every attempt without ever reaching PASS_SCORE — use
            # the best-scoring one tried, not necessarily the last.
            segments, llm_title, agent_report = best_segments, best_title, best_report
            chosen_viol = best_viol
    else:
        _mark_incomplete_factcheck_rows(segments)
        _budget = max(lp.target_s * 0.80, 22.0)
        segments = _fit_duration(segments, budget=_budget, caps=_NARR_CAP)
        _ensure_comment_prompt(segments, explain.engage_question(frame, pick_actor(headline, entities, frame)))

    # --- FULL-SCRIPT SUBTITLE: the on-screen caption IS the narration (the words
    #     the voice is saying), verbatim — never compressed. It is split into
    #     clean readable 2-3 line chunks per scene by scene.plan(). A separate
    #     short `topic_label` (compressed) is kept for the persistent top bar. ---
    from . import subtitle as _sub
    n = 0
    for s in segments:
        nar = _glyph_safe(s.get("narration", ""))
        if s["role"] == "factcheck":
            s["caption"] = clip_sentence(s.get("caption", "팩트체크"), 46, ell="..")
        elif s["role"] == "outro" and not nar:
            s["caption"] = _glyph_safe(clip_sentence(s.get("caption", "구독과 좋아요"), 46, ell=".."))
        elif nar:
            s["caption"] = nar                          # subtitle == the spoken words
        s["narration"] = nar
        # short label for the top bar (LLM writes a tighter one when available)
        if nar and s["role"] not in ("factcheck", "outro"):
            s["topic_label"] = _glyph_safe(
                s.get("_llm_sub")
                or _sub.topic_label(nar, s["role"],
                                    fallback=s.get("kicker") or _actor or "오늘의 이슈"))
        if s["role"] not in ("outro",):
            n += 1
            s["num"] = n

    # --- one sentence per card (voice/subtitle lockstep) ---
    segments = _split_by_sentence(segments)

    # --- Scene Duration Controller: one clean readable subtitle chunk per scene,
    #     each held long enough to read; per-scene camera move + transition. ---
    from .scene import plan as _plan_scenes
    segments = _plan_scenes(segments)

    # keep the finished video within the story-type ceiling: estimate the total
    # (spoken time OR subtitle read-time, whichever is longer, per scene) and, if
    # it overruns lp.max_s, scale read-time down (never below 0.72).
    def _scene_est(s: dict[str, Any]) -> float:
        sc = s.get("scene", {})
        return max(_seg_seconds(s), float(sc.get("min_read_s", 0.0))) + 0.14
    _raw_total = sum(_scene_est(s) for s in segments) + float(getattr(cfg, "thumb_hold_seconds", 1.3))
    read_scale = 1.0
    if _raw_total > lp.max_s and _raw_total > 1:
        read_scale = max(0.72, lp.target_s / _raw_total)
        for s in segments:
            sc = s.get("scene")
            if sc and sc.get("min_read_s"):
                sc["min_read_s"] = round(sc["min_read_s"] * read_scale, 2)

    est_seconds = round(sum(_seg_seconds(s) for s in segments), 1)

    # the bundled fonts have no CJK-Han glyph — 李/尹/文 render as tofu on the
    # chip / title. Map the common ones back to Hangul, then drop anything else
    # that isn't Hangul/digit/space.
    _HANJA = {"李": "이", "尹": "윤", "文": "문", "朴": "박", "安": "안", "韓": "한",
              "洪": "홍", "秋": "추", "與": "여", "野": "야", "北": "북", "美": "미",
              "中": "중", "日": "일", "檢": "검"}

    def _chip_safe(s: str) -> str:
        s = "".join(_HANJA.get(c, c) for c in s)
        s = re.sub(r"[^가-힣0-9%·\s]", " ", s)
        return re.sub(r"\s+", " ", s).strip(" ·")

    def _title_safe(s: str) -> str:
        # like _chip_safe but keeps the punctuation the user's own titles use
        # ("유출 경위 조사할까?", "'전격' 사퇴")
        s = "".join(_HANJA.get(c, c) for c in s)
        s = re.sub(r"[^가-힣0-9%\s?!'\"·]", " ", s).replace('"', "'")
        return re.sub(r"\s+", " ", s).strip(" ·")

    def _title_line(s: str, limit: int = 16) -> str:
        s = _title_safe(_glyph_safe(s)).strip()
        if len(s) <= limit:
            return s
        cut = s.rfind(" ", 0, limit + 1)         # never mid-word
        return (s[:cut] if cut >= limit - 6 else s[:limit]).rstrip(" ·'\"")

    title = [_title_line(t) for t in (llm_title or make_title(headline, entities, frame))]
    from .hook import pick_actor as _pa
    topic = _chip_safe(_pa(headline, entities, frame))
    # famous names on the chip are GOOD (user wants 한동훈/이재명 up front) — only
    # swap for a headline phrase when pick_actor returned junk (a bare noun like
    # "논란"/"없다", or an institution that isn't really the subject).
    if not topic or topic in _NOT_TARGET or topic in {"여야", "여당", "야당", "정부", "국회",
                                                      "경찰청", "검찰청", "청와대", "대통령실"}:
        m = re.search(r"[‘'\"“]([^’'\"”]{2,16}?)(?=[,'’\"”])", headline) \
            or re.match(r"\s*([가-힣]{2,6}(?:\s?[가-힣]{2,6}){0,2})", clean_text(_headline(headline)))
        topic = _chip_safe(m.group(1)) if (m and m.group(1).strip()) else ""
    topic = topic or "오늘의 이슈"

    # 7) images (keyless CC) + optional b-roll video -------------
    # body text (not just the headline) so topic detection (북한/유엔/미국/…)
    # catches a subject only named in the article body, not the headline.
    _body_text = f"{titles}\n{summaries}"
    images: list[dict[str, Any]] = []
    try:
        from .images import collect_images

        images = [a.__dict__ for a in
                  collect_images(entities, frame, headline, cfg, body_text=_body_text)]
    except Exception as exc:  # pragma: no cover - network dependent
        log.warning("image collection failed: %s", exc)
    if getattr(cfg, "broll_enabled", False):
        try:
            from .footage import collect_footage

            images += [a.__dict__ for a in
                      collect_footage(entities, frame, headline, cfg, body_text=_body_text)]
        except Exception as exc:  # pragma: no cover - network dependent
            log.warning("b-roll collection failed: %s", exc)

    # A portrait comes from people named anywhere in the cluster BODY, but the script is written about
    # the headline's actor — a published video showed one politician's face over a story that never
    # mentioned him. Keep a face only when that person is named in what the video actually says.
    _spoken_all = " ".join([headline] + [s.get("narration", "") for s in segments])
    _dropped = [im.get("query") for im in images
                if im.get("kind") == "portrait" and not (im.get("query") and im["query"] in _spoken_all)]
    if _dropped:
        images = [im for im in images
                  if im.get("kind") != "portrait" or (im.get("query") and im["query"] in _spoken_all)]
        log.info("dropped portrait(s) of people the script never names: %s", ", ".join(map(str, _dropped)))

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
        # the research articles are source material too: quality.py's "number not in the article" check
        # flagged "166종 1,431개" (real, from a researched article) as invented and cut 10+ points
        "source_text": clean_text(f"{titles} {summaries} {research_text}")[:8000],
        "length_class": lp.cls,
        "length_label": lp.label,
        "target_seconds": lp.target_s,
        "length_band": [lp.min_s, lp.max_s],
        "sources": _sources_from_rows(rows),
        "counts": {"facts": len(analysis.facts), "claims": len(analysis.claims),
                   "interpretations": len(analysis.interpretations)},
        "factcheck": fc.to_dict(),
        "disclaimer": DISCLAIMER,
        "style": cfg.headline_style,
        "engage_question": explain.engage_question(frame, pick_actor(headline, entities, frame)),
        "invariant_violations": chosen_viol,
        # other clusters this video already covers — recorded as "covered" when it is published
        "absorbed": [{"title": a["title"], "cluster_id": a.get("cluster_id")} for a in absorbed],
        # the articles the research actually read — cited in the description next to the seed sources
        "research_sources": [{"title": s.get("title", ""), "url": s.get("url", "")}
                             for s in (research_web.get("sources") or []) if s.get("url")][:8],
        "quality_agent": {
            "available": bool(agent_report and agent_report.available),
            "score": agent_report.score if agent_report else 0,
            "attempts": agent_attempts,
            "passed": bool(agent_report.passed) if agent_report else True,
            "issues": agent_report.issues if agent_report else [],
        },
    }

    if cfg.llm_available:
        try:
            script = _llm_polish(script, cfg)
        except Exception as exc:  # pragma: no cover
            log.warning("llm polish skipped: %s", exc)

    log.info(
        "script cluster=%d segs=%d ~%.0fs [%s tgt=%.0fs rs=%.2f] frame=%s imgs=%d "
        "facts=%d claims=%d interp=%d src=%d qagent=%s/%d(%d attempt%s)",
        cluster_id, len(segments), est_seconds, lp.cls, lp.target_s, read_scale,
        frame.kind, len(images), len(analysis.facts), len(analysis.claims),
        len(analysis.interpretations), n_sources,
        agent_report.score if agent_report else "n/a", quality_agent.PASS_SCORE if llm_on else 0,
        agent_attempts, "" if agent_attempts == 1 else "s",
    )
    # harsh wording is rewritten, not refused — see soften.py
    from .soften import soften_script
    softened = soften_script(script)
    if softened:
        log.info("softened %d harsh expression(s): %s", len(softened), ", ".join(sorted(set(softened))))
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
