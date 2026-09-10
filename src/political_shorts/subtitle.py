"""Subtitle Script — separate from the Narration Script.

The voice reads the full, connected sentences (script_gen / script_llm). The
screen does NOT echo them. `beat_caption()` returns ONE compressed 1-2 line key
message for a whole card: drop the connective lead-in, the "…라고 밝혔습니다"
reporting tail, hedges and parenthetical glosses; prefer the shortest COMPLETE
informative sentence; only hard-trim as a last resort, and never end on a
dangling particle. Figures / decisive words are kept in — the caption renderer
draws those bigger and red.

When an LLM is in the loop it writes a tighter `subtitle` per card
(script_llm); this module is the always-available fallback.
"""
from __future__ import annotations

import re

from .textutil import clean_text

_MAX = 26          # ~2 lines at the large caption size
_HOOK_MAX = 36     # the hook is one tight line — keep it whole

_LEAD = re.compile(
    r"^(?:그런데|그리고|그러나|하지만|반면|한편|또한|또|아울러|이어|이에|즉|특히|"
    r"결국|다만|사실|우선|먼저|이번에|최근|오늘|여기서 진짜|여기서|쉽게 말하면|"
    r"한마디로|정리하면|바로|앞서)\s*[,·]?\s*"
)
_REPORT_TAIL = re.compile(
    r"\s*(?:[가-힣]{0,7}(?:라고|이라고|라며|이라며|고|며))?\s*"
    r"(?:밝혔|전했|설명했|지적했|강조했|주장했|덧붙였|말했|반박했|촉구했|우려했|비판했|"
    r"해명했|반발했|경고했)(?:다|고|으며|습니다|다고|다며)\.?$"
)
_HEDGE_TAIL = re.compile(
    r"\s*(?:것으로\s*(?:보입니다|보인다|관측됩니다|전망됩니다)|"
    r"가능성이\s*(?:있|큽|높)(?:습니다|다)|전망입니다|관측입니다|분석입니다|"
    r"모양새입니다|셈입니다|상황입니다|분위기입니다|것입니다)\.?$"
)
_MEAN_TAIL = re.compile(r"\s*(?:이?라는\s*뜻입니다|는\s*뜻입니다|는\s*의미입니다|는\s*얘기입니다)\.?$")
_PAREN = re.compile(r"\s*[\(（][^)）]{1,40}[\)）]")
_ASK_TAIL = re.compile(r"\s*(?:무슨 일인지|왜 중요한지|왜 논란인지|하나씩)[^.?!]*[.?!]?$")
_TRAIL_PART = re.compile(
    r"[\s,·]*(?:이라고|이라며|다면서|면서|으며|고서|는데|지만|다고|라고|다며|"
    r"으로|로서|에게|에서|처럼|만큼|보다|까지|부터|"
    r"을|를|은|는|의|와|며)$"                 # (과/도/만/이/가 collide with noun syllables)
)
# a trailing token that is an INCOMPLETE verb form ("지적했", "물러나면", "물러나")
_VERB_FRAG = re.compile(r"[가-힣]*(?:하|했|되|겠|였|았|었|랐|났|려|러|나|미|기|시|리|"
                        r"면|며|서|자|고서|는데)$")

_FIG = re.compile(r"\d[\d,.]*\s?%p?|\d[\d,.]*\s?(?:퍼센트|명|석|표|건|년|개월|억|조|만명|배|주년|위|차|표차)")
_NAME = re.compile(
    r"이재명|한동훈|윤석열|조국|김민석|이준석|한덕수|추경호|박찬대|우원식|정청래|"
    r"김승원|장동혁|권성동|나경원|안철수|홍준표|오세훈|한덕수|"
    r"국민의힘|더불어민주당|민주당|조국혁신당|개혁신당|정의당|"
    r"대통령실|국회|정부|헌법재판소|헌재|대법원|검찰|공수처"
)
_DECISIVE = re.compile(
    r"사퇴|사임|경질|해임|물러났|물러난|발탁|지명|임명|통과|가결|부결|무산|철회|"
    r"체포|기소|구속|압수수색|해산|탄핵|거부권|급증|급감|충돌|정면|합의|무산|출석|소환"
)

_CLAUSE = re.compile(r"(?<=[,·])\s+|(?<=[가-힣])(?:는데|지만|면서|으며|고서)\s+")
_SENT = re.compile(r"(?<=[.!?])\s+|(?<=니다)\s+(?=[가-힣])|(?<=니다\.)\s+")


def _score(s: str) -> float:
    return (3.0 * bool(_FIG.search(s)) + 2.0 * bool(_NAME.search(s))
            + 2.0 * bool(_DECISIVE.search(s)) - 0.03 * len(s))


def _complete(s: str) -> bool:
    s = s.rstrip(" .?!\"'”’)")
    return s.endswith(("다", "요", "까", "죠", "네")) or s.endswith(("?", "!"))


def _tighten(s: str) -> str:
    s = _PAREN.sub("", s)
    s = _LEAD.sub("", s).strip()
    for rx in (_REPORT_TAIL, _HEDGE_TAIL, _MEAN_TAIL, _ASK_TAIL):
        s = rx.sub("", s).strip(" ,·")
    return re.sub(r"\s+", " ", s).strip(" ,·.")


def _no_dangle(s: str) -> str:
    prev = None
    while s and s != prev:
        prev = s
        s = _TRAIL_PART.sub("", s).rstrip(" ,·")
    # if it still ends mid-verb ("…물러나면", "…지적했"), drop that last token once
    if s and " " in s and _VERB_FRAG.search(s.rsplit(" ", 1)[1]):
        head = s.rsplit(" ", 1)[0].rstrip(" ,·")
        if len(head) >= 8:
            s = _no_dangle(head)
    return s


def _hard_clip(s: str, n: int) -> str:
    if len(s) <= n:
        return _no_dangle(s)
    cut = s.rfind(" ", 0, n + 1)
    s = s[:cut] if cut >= n * 0.55 else s[:n]
    return _no_dangle(s.rstrip(" ,·"))


def beat_caption(narration: str, role: str = "", fallback: str = "") -> str:
    """One compressed on-screen line (<= ~2 lines) for a whole card/beat."""
    t = clean_text(narration or "")
    if not t:
        return _hard_clip(clean_text(fallback), _MAX)

    sents = [s.strip() for s in _SENT.split(t) if s.strip()]
    cap_max = _HOOK_MAX if role == "hook" else _MAX

    # 1) the hook, or an already-tight card: light trim, no clause surgery
    if role == "hook" or len(t) <= 32:
        c = _tighten(sents[0] if sents else t)
        return _hard_clip(c, cap_max) or _hard_clip(clean_text(fallback), cap_max)

    tight = [_tighten(s) for s in sents]

    # 2) the shortest COMPLETE, informative sentence that already fits
    good = [c for c in tight
            if 12 <= len(c) <= cap_max and _complete(c)
            and (_FIG.search(c) or _NAME.search(c) or _DECISIVE.search(c))]
    if good:
        return _with_subject(min(good, key=len), tight)

    # 3) any complete sentence that fits
    ok = [c for c in tight if 10 <= len(c) <= cap_max and _complete(c)]
    if ok:
        return _with_subject(max(ok, key=_score), tight)

    # 4) hard-clip the best-scored sentence to a clean word / noun boundary
    best = _tighten(max(sents, key=_score))
    clipped = (_hard_clip(best, cap_max) or _hard_clip(t, cap_max)
               or _hard_clip(clean_text(fallback), cap_max))
    return _with_subject(clipped, tight) if clipped else clipped


def _with_subject(cap: str, tight_sents: list[str]) -> str:
    """Prepend the story's subject ONLY when the picked line genuinely lacks one
    ('3일 사퇴했습니다' -> '김승원 3일 사퇴했습니다'). A line that already carries a
    subject marker (…은/는/이/가) or a name is left alone."""
    if _NAME.search(cap) or re.search(r"[가-힣]{2,}(?:은|는|이|가)(?:\s|$)", cap + " "):
        return cap
    for s in tight_sents:
        m = _NAME.search(s)
        if m and len(cap) + len(m.group(0)) + 1 <= _MAX + 3:
            return f"{m.group(0)} {cap}"
    return cap


def emphasis_terms(text: str) -> list[str]:
    out: list[str] = []
    for rx in (_FIG, _DECISIVE):
        for m in rx.finditer(text or ""):
            tok = m.group(0).strip()
            if tok and tok not in out:
                out.append(tok)
    return out[:4]


def valid_llm_subtitle(s: str) -> str:
    """Accept an LLM-written subtitle only if it's a sane short line. The model
    wrote it deliberately — only light cleanup, no aggressive trimming."""
    s = clean_text(s or "").strip().strip('"\'“”').rstrip(" .·,")
    if not (3 <= len(s) <= 30) or s.count(" ") > 7:
        return ""
    # bounce a line that still ends on an obvious connective / dangling particle
    if re.search(r"(?:은|는|을|를|이|가|에|와|의|고|며|면서|라고|다고)$", s):
        return ""
    return s
