"""Step 6 — the publish gate. A punchy *tone* is allowed; fabrication, slurs,
and unbacked serious allegations about named people are not.

`review_script` returns a SafetyReport. `passed=False` -> the pipeline must not
build or publish; warnings are advisory and shown on the dashboard / in the
video description.

BLOCK  harmful language        slurs, dehumanising terms, calls to violence
BLOCK  unbacked allegation     hook/caption pins a serious crime word on a named
                               person with no supporting fact/quote in the body
BLOCK  absolute framing        "100% 조작" etc. outside an attributed line
BLOCK  unsourced 'what'        single-source claim, no date/number cue
BLOCK  no sources at all
WARN   sensational body        loaded words in summary/what/factcheck (hook is OK)
WARN   one-sided balance       one party named / single lean / no reaction line
WARN   speculation as lead     the 'what' card is only a rephrased headline
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Settings, settings
from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("safety")

HARMFUL = [
    "빨갱이", "수구꼴통", "토착왜구", "친일파 새끼", "국개", "쓰레기 정당",
    "죽여", "때려죽", "몰살", "처단하자", "테러하", "화형", "목매달", "쳐죽",
    "정신병자", "틀딱", "급식충", "맘충", "홍어", "전라디언", "착짱죽짱",
]
ABSOLUTE = [
    "무조건", "100% 거짓", "100% 조작", "완전 조작", "전부 거짓말", "명백한 사기",
    "빼도 박도 못하", "반박 불가", "확정적으로", "빼박",
]
# serious allegation words — must not be pinned on a named person without backing
ALLEGATION = [
    "구속", "기소", "뇌물", "횡령", "배임", "성범죄", "성폭행", "성추행", "마약",
    "간첩", "내란", "학살", "조작", "매수", "불법 자금", "비자금", "청부",
]
LOADED = [
    "충격", "발칵", "경악", "초강수", "멘붕", "폭탄 발언", "속시원", "사이다",
    "굴욕", "참패 확정", "정치 생명 끝", "묵사발", "완패", "박살",
]
PARTY_TOKENS = [
    "민주당", "국민의힘", "국힘", "조국혁신당", "개혁신당", "진보당", "정의당",
    "여당", "야당", "여권", "야권",
]
NAMES = [
    "이재명", "한동훈", "조국", "우원식", "추경호", "박찬대", "나경원", "안철수",
    "이준석", "김두관", "홍준표", "오세훈", "김문수", "원희룡", "장동혁", "김민석",
    "정청래", "김용범", "조희대", "윤석열", "김건희",
]

BODY_ROLES = {"summary", "what", "factcheck"}


@dataclass
class SafetyReport:
    passed: bool = True
    blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "blocks": self.blocks,
                "warnings": self.warnings, "stats": self.stats}


def _seg_text(seg: dict[str, Any]) -> str:
    parts = [seg.get("caption", ""), seg.get("narration", "")]
    for row in seg.get("rows", []) or []:
        parts.append(row.get("text", ""))
    return clean_text(" ".join(parts))


def _all_text(script: dict[str, Any]) -> str:
    parts = [script.get("headline", "")]
    for seg in script.get("segments", []):
        parts.append(_seg_text(seg))
    return clean_text(" \n ".join(parts))


def _hits(text: str, terms: list[str]) -> list[str]:
    return sorted({t for t in terms if t in text})


def review_script(script: dict[str, Any], cfg: Settings | None = None) -> SafetyReport:
    cfg = cfg or settings
    rep = SafetyReport()
    segments = script.get("segments", [])
    text = _all_text(script)
    body_text = clean_text(" ".join(_seg_text(s) for s in segments if s.get("role") in BODY_ROLES))
    hook_text = clean_text(" ".join(_seg_text(s) for s in segments if s.get("role") in {"hook", "outro"}))
    sources = script.get("sources", [])
    leans = {s.get("lean", "center") for s in sources}
    parties = _hits(text, PARTY_TOKENS)
    n_sources = int(script.get("n_sources", len(sources)))

    # BLOCK: harmful language --------------------------------------------
    harmful = _hits(text, HARMFUL)
    if harmful:
        rep.blocks.append(f"유해/비하 표현: {', '.join(harmful)}")

    # BLOCK: absolute framing outside an attributed line ---------------
    absolute = _hits(text, ABSOLUTE)
    if absolute:
        ok = any(
            seg.get("role") in {"reaction", "factcheck"}
            and any(a in _seg_text(seg) for a in absolute)
            for seg in segments
        )
        if not ok:
            rep.blocks.append(f"단정적 표현(출처 없음): {', '.join(absolute)}")

    # BLOCK: unbacked serious allegation on a named person -------------
    hook_names = _hits(hook_text, NAMES)
    hook_alleg = _hits(hook_text, ALLEGATION)
    if hook_names and hook_alleg:
        backed = any(w in body_text for w in hook_alleg)
        if not backed:
            rep.blocks.append(
                f"훅/캡션이 인물({', '.join(hook_names)})에 '{', '.join(hook_alleg)}'를 "
                "본문 근거 없이 붙임"
            )

    # BLOCK: no sources ---------------------------------------------
    if not sources:
        rep.blocks.append("출처가 하나도 없음")

    # BLOCK: unsourced 'what' segment ------------------------------
    for i, seg in enumerate(segments):
        if seg.get("role") != "what":
            continue
        cues = set(seg.get("cues", []))
        hard_cue = bool(cues & {"date", "num"}) or any(c not in {"headline-only"} for c in cues)
        if n_sources < cfg.min_sources_for_fact and not hard_cue:
            rep.blocks.append(f"'무슨 일' 카드 #{i}가 단일 출처이고 날짜·수치 단서 없음")

    # WARN: sensational language in the *body* (hook is allowed) -------
    body_loaded = _hits(body_text, LOADED)
    if body_loaded:
        rep.warnings.append(f"본문(요약/사실/팩트체크)에 자극적 표현: {', '.join(body_loaded)}")

    # WARN: balance ----------------------------------------------
    if parties:
        if len(parties) == 1:
            rep.warnings.append(f"한쪽 정당만 언급됨({parties[0]}) — 상대측 반응 보강 권장")
        real_leans = leans - {"wire", "center"}
        if len(real_leans) == 1:
            rep.warnings.append(f"편집 성향 치우침({next(iter(real_leans))}) — 다른 성향 매체 보강 권장")
        if not any(s.get("role") == "reaction" for s in segments):
            rep.warnings.append("양쪽 반응 카드가 없음 — 쟁점 사안이면 균형 보완 권장")

    # WARN: headline-only body ---------------------------------
    first_what = next((s for s in segments if s.get("role") == "what"), None)
    if first_what and "headline-only" in set(first_what.get("cues", [])):
        rep.warnings.append("'무슨 일' 카드가 제목 재서술 수준 — 구체 사실 확인 권장")

    # WARN: no fact-check card -------------------------------
    if not any(s.get("role") == "factcheck" for s in segments):
        rep.warnings.append("팩트체크 카드가 없음")

    rep.stats = {
        "n_sources": n_sources, "leans": sorted(leans), "parties": parties,
        "n_segments": len(segments), "frame": script.get("frame"),
        "harmful_hits": harmful, "absolute_hits": absolute,
        "hook_allegation_hits": hook_alleg, "body_loaded_hits": body_loaded,
        "n_images": len(script.get("images", [])),
    }
    rep.passed = not rep.blocks

    log.info("safety cluster=%s passed=%s blocks=%d warnings=%d",
             script.get("cluster_id"), rep.passed, len(rep.blocks), len(rep.warnings))
    return rep
