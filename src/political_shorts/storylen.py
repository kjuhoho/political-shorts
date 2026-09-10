"""Length target by story type.

A one-fact 속보 and a multi-party 정책 공방 should not be the same length. This
classifies the cluster from what `analyze` actually found and returns a target
(and a hard ceiling) in seconds. `script_gen` uses the target as the trimming
budget and the ceiling to scale subtitle read-time down if it still overruns.

  단순 속보 / 한 가지 사건   25-40s   (target 34)
  일반 정치 뉴스            40-60s   (target 50)
  논란·공방·정책 설명        50-75s   (target 63)
  정말 복잡한 사건           70-90s   (target 82)
"""
from __future__ import annotations

from dataclasses import dataclass

_BANDS = {
    "brief":    (25.0, 40.0, 34.0),
    "standard": (40.0, 60.0, 50.0),
    "debate":   (50.0, 75.0, 63.0),
    "complex":  (70.0, 90.0, 82.0),
}
_LABEL = {
    "brief": "단순 속보", "standard": "일반 정치 뉴스",
    "debate": "논란·공방·정책", "complex": "복잡한 사건",
}


@dataclass
class LengthPlan:
    cls: str
    label: str
    target_s: float
    min_s: float
    max_s: float


def classify_length(analysis, frame, entities, n_sources: int, leans) -> LengthPlan:
    n_fact = len(getattr(analysis, "facts", []) or [])
    n_claim = len(getattr(analysis, "claims", []) or [])
    n_interp = len(getattr(analysis, "interpretations", []) or [])
    n_party = len(getattr(entities, "parties", []) or [])
    real_leans = len({l for l in (leans or []) if l not in ("wire", "center")})
    kind = getattr(frame, "kind", "generic")

    # signal for a genuine multi-sided dispute
    disputed = (kind in ("clash", "scandal")
                or n_party >= 2
                or (kind == "vote" and n_claim >= 2)
                or (n_claim >= 2 and real_leans >= 2))

    if (n_fact >= 4 and n_claim >= 2 and n_sources >= 3
            and (n_interp >= 6 or n_fact + n_claim >= 8)):
        cls = "complex"
    elif disputed and n_sources >= 2:
        cls = "debate"
    elif not disputed and n_claim == 0 and n_fact <= 2 and n_interp <= 4:
        cls = "brief"                       # 한 가지 사건, 이견 없음
    elif n_sources <= 2 and n_claim <= 1 and n_fact <= 3 and not disputed:
        cls = "brief"
    else:
        cls = "standard"

    lo, hi, tgt = _BANDS[cls]
    return LengthPlan(cls=cls, label=_LABEL[cls], target_s=tgt, min_s=lo, max_s=hi)
