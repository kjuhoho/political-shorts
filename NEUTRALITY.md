# Neutrality policy

This channel is **strictly non-partisan**. Every automated and hand-authored
short must be balanced — it may never read as favouring the ruling party, the
opposition, the government, or any figure. The rules below are enforced in code
where possible.

## Hard rules (enforced)

| Rule | Where |
|---|---|
| No slurs / dehumanising terms (빨갱이, 토착왜구, 수구꼴통, …) | `safety.HARMFUL` → **BLOCK** |
| No unsourced absolutes ("100% 조작", "완전 거짓말") outside an attributed quote | `safety.ABSOLUTE` → **BLOCK** |
| No serious-crime word (구속·기소·뇌물·…) pinned on a named person in the hook without body backing | `safety` → **BLOCK** |
| A single-source claim with no date/number cue may not be stated as fact | `safety` "unsourced what" → **BLOCK** |
| Loaded/tabloid words (충격·발칵·사이다·굴욕·…) in the body | `safety.LOADED` → **WARN**, and templates avoid them entirely |
| Thumbnail/UI palette: **no red** (= 국민의힘/우파), **no strong blue** (= 민주당) — charcoal + off-white + news-caption yellow only | `video.py` `INK/PANEL/GOLD/LINE` |
| Prefer stories carried by **≥2 editorial leans** (progressive + conservative + wire) | `dedupe.build_clusters` `_rank()` sorts balance first |
| Every opinion in the script is **attributed** ("민주당은 …", "국민의힘은 …", "후보자 측은 …"); the fact-check card splits 사실 / 주장 / 확인 | `script_gen`, hand-authoring standard |
| Hook & title templates are neutral open-loop questions — no side is the hero or the villain | `hook.HOOKS`, `hook._TITLE_TMPL` |
| One-sided coverage (one party named, one lean only, no reaction card) | `safety` → **WARN**, surfaced in the description's "균형 관련 참고" |

## Editorial rules (hand-authoring)

- Lead with a **verbatim quote or a neutral question**, never a characterisation.
- Give **at least two attributed sides** in the reaction card. If only one side
  has spoken, say so plainly ("상대 측은 아직 입장을 내지 않았습니다").
- Label anything not yet established as **주장 / 의혹 / 전망**, and add the
  disclaimer "아직 사실로 확정된 것이 아닙니다".
- Pick the cluster with the widest source spread; cite wire + progressive +
  conservative outlets in the description.
- No adjectives that assign blame or credit. Report what was said and done.

## Sources

9 politics feeds span wire (연합·뉴시스·SBS), progressive (한겨레·경향·오마이),
conservative (조선·동아·국민). The apolitical fallback feeds
(`fallback_feeds` in `config/sources.yaml`) are wire-heavy and also
lean-balanced.
