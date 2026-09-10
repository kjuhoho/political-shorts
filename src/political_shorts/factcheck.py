"""FACT CHECK ENGINE.

Before a word of script is written, the cluster's sentences are turned into
structured, source-attributed units:

    FactUnit { text, kind, source_name, source_url, speaker, confidence, cues }

    FACT            an event/figure verifiable in the article, ideally dated
    QUOTE           an actual attributed statement ("…라고 말했다")
    INTERPRETATION  the writer's read — outlook, blame, "논란" — never spoken
                    as fact, only as a labelled 전망
    UNCERTAIN       a single-source claim with no date/number backing

Cross-source corroboration raises confidence. The script may only STATE units
that are FACT or QUOTE; INTERPRETATION goes in the 전망 row, labelled. If a
serious-allegation word rides only on a low-confidence single-source unit the
whole story is flagged `review_required` (POLITICAL_CONTENT_REVIEW_REQUIRED)
and must not auto-publish.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .analyze import Kind, analyze
from .textutil import clean_text

_ALLEGATION = (
    "구속", "기소", "뇌물", "횡령", "배임", "성범죄", "성폭행", "성추행", "마약",
    "간첩", "내란", "학살", "조작", "매수", "불법 자금", "비자금", "청부", "위증",
)
_SPEAKER_RE = re.compile(r"([가-힣]{2,4})\s*(?:대통령|총리|장관|의원|대표|수석|실장|위원장|청장|대변인)?"
                         r"\s*(?:은|는|이|가|측은|측이)?\s*[\"“']")
_QUOTE_RE = re.compile(r"[\"“”'‘’]([^\"“”'‘’]{5,120})[\"“”'‘’]")
_DIGIT = re.compile(r"\d")

# tolerant "same sentence" match for cross-source corroboration
_STOP = set("은는이가을를에의로과와도만한그이번오늘관련밝혔다했다고말했다다는".split())


@dataclass
class FactUnit:
    text: str
    kind: str                      # FACT | QUOTE | INTERPRETATION | UNCERTAIN
    source_name: str
    source_url: str
    speaker: str = ""
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)   # every source that carries it
    cues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "kind": self.kind, "speaker": self.speaker,
            "confidence": round(self.confidence, 2),
            "sources": self.sources or [self.source_name],
            "source_url": self.source_url, "cues": self.cues,
        }


@dataclass
class FactCheck:
    units: list[FactUnit]
    review_required: bool = False
    review_reason: str = ""

    @property
    def facts(self) -> list[FactUnit]:
        return [u for u in self.units if u.kind in ("FACT", "QUOTE") and u.confidence >= 0.55]

    @property
    def quotes(self) -> list[FactUnit]:
        return [u for u in self.units if u.kind == "QUOTE"]

    @property
    def interpretations(self) -> list[FactUnit]:
        return [u for u in self.units if u.kind == "INTERPRETATION"]

    @property
    def uncertain(self) -> list[FactUnit]:
        return [u for u in self.units if u.kind == "UNCERTAIN"]

    def rows(self, n_sources: int) -> list[dict[str, Any]]:
        """The on-screen 사실 / 주장 / 전망 table — each row source-attributed."""
        out: list[dict[str, Any]] = []
        f = next(iter(self.facts), None)
        if f:
            out.append({"tag": "사실", "tone": "ok", "text": _clip(f.text),
                        "source": ", ".join(f.sources or [f.source_name])})
        q = next((u for u in self.units if u.kind == "QUOTE" and u is not f), None)
        if q:
            who = q.speaker or _lead_actor(q.text) or (q.sources[0] if q.sources else q.source_name)
            body = _QUOTE_RE.search(q.text)
            said = _clip(body.group(1) if body else re.sub(
                r"^[가-힣]{2,10}(?:은|는|이|가|측은|측이)\s+", "", q.text), 40)
            out.append({"tag": "주장", "tone": "claim", "text": f"{who}: {said}",
                        "source": q.source_name})
        it = next(iter(self.interpretations), None)
        if it:
            out.append({"tag": "전망", "tone": "warn", "text": _clip(it.text),
                        "source": it.source_name})
        out.append({"tag": "확인", "tone": "info",
                    "text": f"{n_sources}개 매체 종합, 원문은 더보기란"})
        return out[:4]

    def to_dict(self) -> dict[str, Any]:
        return {
            "units": [u.to_dict() for u in self.units],
            "review_required": self.review_required,
            "review_reason": self.review_reason,
            "counts": {"fact": len(self.facts), "quote": len(self.quotes),
                       "interp": len(self.interpretations), "uncertain": len(self.uncertain)},
        }


def _clip(s: str, n: int = 46) -> str:
    s = re.sub(r"[·…—]", " ", clean_text(s)).strip(" ·,")
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0].rstrip(" ,·")


def _key(text: str) -> frozenset:
    toks = [t for t in re.findall(r"[가-힣]{2,}|\d[\d,.]*", clean_text(text)) if t not in _STOP]
    return frozenset(toks[:12])


def _speaker(text: str) -> str:
    m = _SPEAKER_RE.match(clean_text(text))
    return m.group(1) if m else ""


_PARTY = ("국민의힘", "더불어민주당", "민주당", "조국혁신당", "개혁신당", "정의당",
          "여당", "야당", "여권", "야권", "대통령실", "정부")


def _lead_actor(text: str) -> str:
    t = clean_text(text)
    for p in _PARTY:
        if t.startswith(p):
            return "민주당" if p == "더불어민주당" else p
    m = re.match(r"([가-힣]{2,4})\s*(?:대통령|총리|장관|의원|대표|수석|실장|위원장|청장|대변인)", t)
    return m.group(1) if m else ""


def extract(rows: list[Any]) -> FactCheck:
    """rows = the cluster's DB article rows (source_name / url / title / summary)."""
    raw: list[FactUnit] = []
    for r in rows:
        name = r["source_name"]
        url = r["url"]
        res = analyze(r["title"], r["summary"])
        for t in res.all:
            base = {Kind.FACT: "FACT", Kind.CLAIM: "QUOTE",
                    Kind.INTERPRETATION: "INTERPRETATION"}[t.kind]
            has_quote = bool(_QUOTE_RE.search(t.text))
            # a FACT sentence that carries a quoted span is really someone's 주장
            kind = "QUOTE" if (base == "FACT" and has_quote) else base
            raw.append(FactUnit(
                text=clean_text(t.text), kind=kind, source_name=name, source_url=url,
                speaker=_speaker(t.text), sources=[name], cues=list(t.cues),
            ))

    # merge near-identical units across sources
    merged: dict[frozenset, FactUnit] = {}
    for u in raw:
        k = _key(u.text)
        if not k:
            continue
        hit = merged.get(k)
        if hit:
            if u.source_name not in hit.sources:
                hit.sources.append(u.source_name)
            # prefer the longer phrasing, keep a real speaker
            if len(u.text) > len(hit.text):
                hit.text = u.text
            hit.speaker = hit.speaker or u.speaker
            hit.cues = sorted(set(hit.cues) | set(u.cues))
            # FACT/QUOTE beats INTERPRETATION when sources disagree on kind
            order = {"FACT": 3, "QUOTE": 3, "UNCERTAIN": 1, "INTERPRETATION": 0}
            if order[u.kind] > order[hit.kind]:
                hit.kind = u.kind
        else:
            merged[k] = u

    units = list(merged.values())
    for u in units:
        n_src = len(u.sources)
        conf = {"FACT": 0.62, "QUOTE": 0.66, "INTERPRETATION": 0.38, "UNCERTAIN": 0.30}[u.kind]
        conf += 0.12 * (n_src - 1)
        if "date" in u.cues or "num" in u.cues or _DIGIT.search(u.text):
            conf += 0.06
        u.confidence = round(min(conf, 0.98), 2)
        # a lone FACT with no date/number and no corroboration -> UNCERTAIN
        if u.kind == "FACT" and n_src == 1 and not (set(u.cues) & {"date", "num"}) \
                and not _DIGIT.search(u.text):
            u.kind, u.confidence = "UNCERTAIN", min(u.confidence, 0.45)

    units.sort(key=lambda u: (u.kind not in ("FACT", "QUOTE"), -u.confidence, -len(u.sources)))

    # review gate: a serious-allegation word that rides only on a low-confidence,
    # single-source unit — do not let the pipeline auto-publish this.
    review, reason = False, ""
    for u in units:
        hits = [w for w in _ALLEGATION if w in u.text]
        if hits and (u.confidence < 0.6 or len(u.sources) < 2) and u.kind != "QUOTE":
            review = True
            reason = f"단일·저신뢰 출처의 의혹성 표현: {', '.join(hits)} ({u.source_name})"
            break
    return FactCheck(units=units, review_required=review, review_reason=reason)
