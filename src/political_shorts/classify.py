"""Step 2 — decide whether an article is *domestic Korean politics*.

Offline heuristic: a weighted keyword lexicon with a few negative signals for
stories that are really foreign affairs / sports / markets wearing a political
hat. An optional LLM check can override borderline cases when a key is set.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings, settings
from .logging_setup import get_logger
from .textutil import clean_text

log = get_logger("classify")

# Strong domestic-politics signals (score 2 each).
STRONG = [
    "국회", "본회의", "상임위", "여야", "여당", "야당", "원내대표", "당대표",
    "대통령실", "청와대", "국무회의", "국무총리", "장관 후보자", "인사청문회",
    "개각", "대정부질문", "국정감사", "국정조사", "탄핵", "특검", "특별검사",
    "예산안", "본회의 통과", "법안 처리", "필리버스터", "선거법", "정개특위",
    "중앙선거관리위원회", "선관위", "공천", "경선", "당정협의", "시정연설",
    "거부권", "재의요구권", "국회의장", "교섭단체",
]

# Party / institution names (score 2 each).
PARTIES = [
    "더불어민주당", "국민의힘", "조국혁신당", "개혁신당", "진보당", "정의당",
    "민주당", "국힘", "기본소득당", "새로운미래",
]

# Medium signals (score 1 each).
MEDIUM = [
    "의원", "국정", "정책", "브리핑", "논평", "성명", "표결", "발의", "상정",
    "국회의원", "지방선거", "총선", "대선", "재보궐", "정당", "야권", "여권",
    "정부", "행정부", "입법", "국정운영", "지지율", "여론조사", "권한대행",
]

# Negative signals: if present and no STRONG/PARTY hit, push below threshold.
NEGATIVE = [
    "프로야구", "손흥민", "월드컵", "K리그", "증시", "코스피", "환율", "부동산 시세",
    "연예", "아이돌", "드라마", "박스오피스", "날씨", "미세먼지",
]

# Foreign-politics markers — allowed only if a domestic anchor is also present.
FOREIGN = [
    "백악관", "미 의회", "미국 대선", "트럼프", "바이든", "시진핑", "푸틴",
    "일본 총리", "기시다", "이시바", "유엔 안보리", "나토",
]
DOMESTIC_ANCHOR = ["한국", "우리 정부", "외교부", "대통령실", "국회", "한미", "한일", "한중"]

THRESHOLD = 3.0

# --- fallback: "generally newsworthy" non-politics (used only when there is no
#     fresh domestic-politics story). Deliberately narrow + apolitical: hard
#     news / science / policy-of-daily-life, NOT gossip, sports scores, markets.
GENERAL_STRONG = [
    "판결", "선고", "대법원", "헌법재판소", "구속영장", "압수수색", "기소",
    "리콜", "결함", "화재", "붕괴", "누출", "감염병", "질병관리청", "식약처",
    "개통", "완공", "발사", "우주", "인공지능", "반도체", "신기술", "특허",
    "기후", "폭염", "한파", "지진", "태풍", "전기요금", "건강보험", "국민연금",
    "최저임금", "물가", "전세사기", "층간소음", "학교폭력", "저출생", "고령화",
]
GENERAL_MED = ["발표", "공개", "도입", "시행", "확정", "추진", "조사 결과",
               "역대 최대", "역대 최고", "처음으로", "세계 최초", "국내 최초"]
GENERAL_NEG = [
    "연예", "아이돌", "가수", "배우", "드라마", "예능", "결혼", "열애", "이혼",
    "프로야구", "축구", "골프", "야구", "농구", "MVP", "우승", "박스오피스",
    "주가", "코스피", "코스닥", "환율", "비트코인", "부동산 시세", "분양",
]
GENERAL_THRESHOLD = 3.0


@dataclass
class ClassifyResult:
    is_politics: bool
    score: float
    hits: list[str]
    reason: str


def _count(text: str, terms: list[str]) -> list[str]:
    return [t for t in terms if t in text]


def classify_text(title: str, summary: str = "", cfg: Settings | None = None) -> ClassifyResult:
    cfg = cfg or settings
    text = f"{clean_text(title)} \n {clean_text(summary)}"

    strong_hits = _count(text, STRONG)
    party_hits = _count(text, PARTIES)
    medium_hits = _count(text, MEDIUM)
    extra_hits = _count(text, list(cfg.extra_keywords))
    neg_hits = _count(text, NEGATIVE)
    foreign_hits = _count(text, FOREIGN)
    anchor_hits = _count(text, DOMESTIC_ANCHOR)

    score = 2.0 * len(strong_hits) + 2.0 * len(party_hits)
    score += 1.0 * len(set(medium_hits))
    score += 1.0 * len(set(extra_hits))

    reason_bits: list[str] = []
    if neg_hits and not strong_hits and not party_hits:
        score -= 2.0
        reason_bits.append(f"negative={neg_hits}")
    if foreign_hits and not anchor_hits and not strong_hits:
        score -= 2.0
        reason_bits.append(f"foreign_only={foreign_hits}")

    is_pol = score >= THRESHOLD
    hits = sorted(set(strong_hits + party_hits + medium_hits + extra_hits))
    reason = (
        f"score={score:.1f} thr={THRESHOLD} strong={strong_hits} party={party_hits} "
        f"medium={len(set(medium_hits))} extra={extra_hits}"
        + ((" " + " ".join(reason_bits)) if reason_bits else "")
    )
    return ClassifyResult(is_pol, round(score, 2), hits, reason)


def general_interest(title: str, summary: str = "") -> bool:
    """Apolitical hard-news that most people would want a 30s explainer on.
    Narrow by design — this is only a fallback when there is no fresh politics
    story, and it must never drift into gossip / sports / markets."""
    text = f"{clean_text(title)} \n {clean_text(summary)}"
    if _count(text, GENERAL_NEG):
        return False
    score = 2.0 * len(_count(text, GENERAL_STRONG)) + 1.0 * len(set(_count(text, GENERAL_MED)))
    return score >= GENERAL_THRESHOLD


def classify_pending(cfg: Settings | None = None) -> tuple[int, int]:
    """Classify every article whose politics flag was never evaluated.

    Returns (evaluated, marked_politics)."""
    from .db import connect, set_article_politics

    cfg = cfg or settings
    evaluated = marked = general = 0
    with connect(cfg.db_path) as conn:
        rows = list(
            conn.execute(
                "SELECT id, title, summary FROM articles "
                "WHERE is_politics = 0 AND politics_score = 0.0"
            )
        )
        for row in rows:
            res = classify_text(row["title"], row["summary"], cfg)
            is_gen = (not res.is_politics) and general_interest(row["title"], row["summary"])
            # Store a tiny epsilon so score==0.0 still means "not yet evaluated".
            set_article_politics(conn, row["id"], res.is_politics,
                                 res.score or 0.0001, is_general=is_gen)
            evaluated += 1
            marked += int(res.is_politics)
            general += int(is_gen)
    log.info("classify done evaluated=%d politics=%d general=%d", evaluated, marked, general)
    return evaluated, marked


# --------------------------------------------------------------------------- #
# Optional LLM override (borderline scores only). Best-effort, never fatal.
# --------------------------------------------------------------------------- #
def llm_is_domestic_politics(title: str, summary: str, cfg: Settings) -> bool | None:
    if not cfg.llm_available:
        return None
    prompt = (
        "다음 뉴스가 '대한민국 국내 정치'에 해당하면 YES, 아니면 NO만 답하세요.\n"
        f"제목: {title}\n요약: {summary}\n답:"
    )
    try:
        from .llm import complete

        out = complete(prompt, cfg, max_tokens=4).strip().upper()
        if out.startswith("YES"):
            return True
        if out.startswith("NO"):
            return False
    except Exception as exc:  # pragma: no cover
        log.debug("llm classify skipped: %s", exc)
    return None
