"""Unit tests for the template-path (no-LLM) narration seeds in script_gen —
these are the fallback that ships when the LLM rewrite is unavailable, so they
must not emit broken Korean."""
from political_shorts import script_gen as sg
from political_shorts.analyze import Tagged, Kind


def _c(text):
    return Tagged(text, Kind.CLAIM, 1.0, [])


def test_reaction_line_skips_unattributed_first_claim():
    # claims[0] has no party/person — the line must still find the attributable
    # claims that follow, not bail out.
    claims = [
        _c("대통령실은 일신상의 이유라고 밝혔다."),
        _c("국민의힘은 인사 검증 부실을 지적했다."),
        _c("더불어민주당은 정책 혼선을 우려했다."),
    ]
    line = sg._reaction_line(claims)
    assert "국민의힘" in line and "민주당" in line
    # the reporting verb is trimmed, not quoted verbatim
    assert "지적했다" not in line
    assert "  " not in line  # no doubled spaces / stray gaps


def test_reaction_line_never_gestures_at_an_unfound_second_side():
    # user: "여당 야당 입장은 있으면 추가하는 시스템으로" — when only ONE
    # side is actually attributable, the line must say only that, not imply
    # a counter-view that was never actually extracted from the source.
    claims = [_c("국민의힘은 인사 검증 부실을 지적했다.")]
    line = sg._reaction_line(claims)
    assert "국민의힘" in line
    assert "다른 목소리" not in line


def test_sides_line_no_interp_fallback_garbage():
    # a headline echoed into interps must NOT be spliced into "…는 전망" grammar
    interps = [Tagged("대통령실 정책실장 김승원 전격 사퇴…취임 두 달 만",
                      Kind.INTERPRETATION, 1.0, [])]
    assert sg._sides_line([], interps) == ""


def test_sides_line_builds_from_party_split():
    claims = [
        _c("국민의힘은 인사 검증 부실을 지적했다."),
        _c("더불어민주당은 정책 혼선을 우려했다."),
    ]
    line = sg._sides_line(claims, [])
    assert line.startswith("그런데 이 사안을 보는 눈은")
    assert "국민의힘" in line and "민주당" in line


def test_quote_ratio_flags_a_mostly_quoted_sentence():
    # the real "what" card that shipped broken: a nested-quote sentence with
    # almost no plain narrative around it.
    quoted = ("김 대표는 이날 출연해 “‘괜히 탄핵 이야기를 꺼낸 것이 경솔했다’고 할 수 "
              "있다”고 말했다.")
    plain = "국회는 3일 본회의를 열어 예산안을 의결했다."
    assert sg._quote_ratio(quoted) > sg._quote_ratio(plain)
    assert sg._quote_ratio(plain) == 0.0


def test_factcheck_row_backstop_marks_incomplete_rows_but_spares_the_info_row():
    # a real shipped case: two rows in the same table both ended without a
    # predicate ("...꺾으려는 정치적" / "...싸움이 계속될") and neither of the
    # two upstream fixes (factcheck._clip / script_llm._clean_row) could be
    # pinned down as the source — this backstop runs on the FINAL rows
    # regardless of which path built them.
    segments = [{"role": "factcheck", "rows": [
        {"tag": "사실", "tone": "ok", "text": "15일 국회에서 인사청문회가 열렸습니다."},
        {"tag": "주장", "tone": "claim",
         "text": "민주당 한병도 원내대표: 야당의 흑색선전은 동력을 꺾으려는 정치적"},
        {"tag": "전망", "tone": "warn", "text": "여야의 주도권 싸움이 계속될"},
        {"tag": "확인", "tone": "info", "text": "3개 매체 종합, 원문은 더보기란"},
    ]}]
    sg._mark_incomplete_factcheck_rows(segments)
    rows = {r["tag"]: r["text"] for r in segments[0]["rows"]}
    assert rows["사실"] == "15일 국회에서 인사청문회가 열렸습니다."       # untouched, already complete
    assert rows["주장"].endswith("..")
    assert rows["전망"].endswith("..")
    assert rows["확인"] == "3개 매체 종합, 원문은 더보기란"              # info row untouched


def test_spoken_never_appends_a_period_to_a_predicateless_fragment():
    # a real shipped card: _SENT_CHUNK matched a fragment long enough to pass
    # the 40%-length ratio check, but it had no predicate ("...전략과" — 과 is
    # "and", not a verb) — the old code blindly appended "." and shipped it
    # looking finished. Must now fall through and drop it instead.
    assert sg._spoken("더불어민주당의 원내 전략과") == ""
    # the untruncated original (real predicate) still passes straight through
    full = "더불어민주당의 원내 전략과 국민의힘의 대응 방식이 이번 논란의 핵심 배경입니다."
    assert sg._spoken(full) == full


def test_context_score_deprioritizes_quote_heavy_sentences():
    quoted = ("김 대표는 이날 출연해 “‘괜히 탄핵 이야기를 꺼낸 것이 경솔했다’고 할 수 "
              "있다”고 말했다.")
    plain = "국회는 처음으로 이례적인 절차를 밟아 예산안을 처리했다."   # hits _CONTEXT_HINTS
    assert sg._context_score(plain) > sg._context_score(quoted)


def _fit_segs():
    # summary (background) is deliberately the LONGEST card here — under the
    # old priority it was dropped first regardless of length; now the extra
    # "what" repeat and length-trimming absorb the pressure instead. Three
    # short COMPLETE sentences (not one long comma-joined one) so step 3's
    # sentence-level drop can genuinely shrink it a sentence at a time and
    # still leave something complete — a single long clause has no such
    # graceful middle ground (dropping a trailing comma-clause off a Korean
    # sentence almost always leaves an incomplete lead-in, not a valid
    # shorter sentence).
    return [
        {"role": "hook", "narration": "김승원이 왜 사퇴했을까요?"},
        {"role": "summary", "narration": "김승원은 대통령의 정책을 총괄하는 정책실장입니다. "
                                         "그는 법조계 출신입니다. 정부 출범 초기 핵심 참모로 "
                                         "임명됐습니다."},
        {"role": "what", "narration": "김승원 정책실장이 취임 두 달 만에 물러났습니다."},
        {"role": "what", "narration": "정부 출범 초기 실장급 인사가 물러난 것은 이례적입니다."},
        {"role": "factcheck", "narration": "확인된 사실은 이겁니다. 김승원은 3일 사퇴했습니다."},
        {"role": "outro", "narration": "구독과 좋아요 눌러주시면 큰 힘이 됩니다."},
    ]


def test_fit_duration_protects_summary_over_a_second_what_beat():
    out = sg._fit_duration(_fit_segs(), budget=22.0)
    roles = [s["role"] for s in out]
    assert roles.count("what") <= 1                 # the extra "what" repeat goes first
    summary = next((s for s in out if s["role"] == "summary"), None)
    assert summary is not None and summary.get("narration")   # background survives


def test_fit_duration_shrinks_summary_rather_than_deleting_it_when_possible():
    segs = _fit_segs()
    full_summary = next(s["narration"] for s in segs if s["role"] == "summary")
    out = sg._fit_duration(_fit_segs(), budget=17.0)
    summary = next((s for s in out if s["role"] == "summary"), None)
    assert summary is not None
    # shorter than the original (it absorbed some of the trim), not gone —
    # and still a genuinely complete sentence, not a fabricated-looking
    # fragment with a period stapled onto it.
    assert 0 < len(summary["narration"]) < len(full_summary)
    assert sg._sentence_complete(summary["narration"])


def test_fit_duration_spends_sides_and_what_before_ever_shrinking_summary():
    # the user's explicit priority: background matters more than a full
    # airing of every side's claims when something has to give. "sides" used
    # to be fully protected (untouchable) — now it shares the pressure with
    # "what" BEFORE summary is touched at all.
    segs = [
        {"role": "hook", "narration": "김승원이 왜 사퇴했을까요?"},
        {"role": "summary", "narration": "김승원은 대통령의 정책을 총괄하는 정책실장입니다."},
        {"role": "sides", "narration": "국민의힘은 인사 검증 부실을 지적했습니다. "
                                       "더불어민주당은 절차에 문제가 없었다고 반박했습니다."},
        {"role": "factcheck", "narration": "확인된 사실은 이겁니다. 김승원은 3일 사퇴했습니다."},
        {"role": "outro", "narration": "구독과 좋아요 눌러주시면 큰 힘이 됩니다."},
    ]
    full_summary = segs[1]["narration"]
    full_sides = segs[2]["narration"]
    out = sg._fit_duration([dict(s) for s in segs], budget=18.0)
    summary = next((s for s in out if s["role"] == "summary"), None)
    sides = next((s for s in out if s["role"] == "sides"), None)
    assert summary is not None and summary["narration"] == full_summary   # untouched
    assert sides is not None and len(sides["narration"]) < len(full_sides)  # absorbed the cut


def test_fit_duration_restores_a_floor_summary_instead_of_dropping_it():
    # a real user complaint: on a thin story, summary used to be dropped
    # ENTIRELY once step 3's own trimming left it too mangled to voice,
    # leaving hook -> raw fact dump -> outro with zero background. At a
    # budget where the old code produced None, it must now come back as a
    # short, genuinely complete clause restored from the untouched original.
    out = sg._fit_duration(_fit_segs(), budget=12.0)
    summary = next((s for s in out if s["role"] == "summary"), None)
    assert summary is not None and summary.get("narration")
    assert sg._sentence_complete(summary["narration"])


def test_fit_duration_never_ships_a_fabricated_complete_looking_summary():
    # a real shipped case: under real pressure, the "last resort" rescue for
    # essential roles used to blindly append "." to whatever clip_sentence
    # returned, regardless of whether it actually ended on a predicate
    # ("법무부 장관 후보자 김승원은 판사 출신이자 국회." — no verb after
    # "국회"). At a budget too tight to keep even one complete summary
    # sentence, it must now be dropped honestly, not faked.
    out = sg._fit_duration(_fit_segs(), budget=16.0)
    summary = next((s for s in out if s["role"] == "summary"), None)
    # either genuinely absent, or present AND a real complete sentence —
    # never present with fabricated-looking punctuation on a fragment
    assert summary is None or sg._sentence_complete(summary["narration"])


def test_research_adds_what_and_sides_cards_when_the_template_has_none():
    from political_shorts import script_gen as G
    segs = [{"role": "hook", "narration": "훅"}, {"role": "summary", "narration": "배경입니다."},
            {"role": "factcheck", "narration": "사실입니다."}, {"role": "outro", "narration": "끝."}]
    web = {"statements": [{"who": "이재명 대통령", "text": "전쟁에 관여하는 파병은 없다", "source": "매체"}],
           "positions": [{"who": "국민의힘", "position": "한미동맹 차원에서 파병을 주장한다", "why": "동맹 의무"}],
           "pros": ["원유 수송로 안정"], "cons": ["동맹 압박"]}
    assert G._research_rich(web)
    out = G._with_research_cards(segs, web)
    roles = [s["role"] for s in out]
    assert roles == ["hook", "summary", "what", "factcheck", "sides", "outro"]
    what = next(s for s in out if s["role"] == "what")["narration"]
    assert what.startswith("이재명 대통령은") and "전쟁에 관여하는 파병은 없다" in what
    assert next(s for s in out if s["role"] == "sides")["narration"].startswith("국민의힘은")
    assert segs == [{"role": "hook", "narration": "훅"}, {"role": "summary", "narration": "배경입니다."},
                    {"role": "factcheck", "narration": "사실입니다."}, {"role": "outro", "narration": "끝."}]


def test_research_cards_never_duplicate_existing_ones_and_thin_research_is_not_rich():
    from political_shorts import script_gen as G
    segs = [{"role": "summary", "narration": "a."}, {"role": "what", "narration": "b."},
            {"role": "sides", "narration": "c."}, {"role": "outro", "narration": "d."}]
    web = {"statements": [{"who": "대통령", "text": "말", "source": "s"}],
           "positions": [{"who": "야당", "position": "반대한다"}]}
    assert [s["role"] for s in G._with_research_cards(segs, web)] == ["summary", "what", "sides", "outro"]
    assert not G._research_rich({"background": "배경만 있음"})
    assert not G._research_rich({"pros": ["장점"]})


def test_fit_duration_never_deletes_a_one_sentence_what_or_sides_card():
    from political_shorts import script_gen as G
    segs = [
        {"role": "hook", "narration": "김승원 후보는 왜 사퇴했을까요?"},
        {"role": "summary", "narration": "법무부 장관은 검찰 사무를 지휘하는 자리입니다. 후보자가 지명됐다가 사퇴했습니다."},
        {"role": "what", "narration": "김 후보자는 19일 기자회견에서 국민 눈높이에 미치지 못했다며 후보직에서 물러난다고 밝혔습니다."},
        {"role": "factcheck", "narration": "청와대는 탄원서 접수 여부를 확인하기 어렵다고 밝혔습니다."},
        {"role": "sides", "narration": "국민의힘은 인사 검증 실패라며 사과를 요구했습니다."},
        {"role": "outro", "narration": "후속 인선이 다시 시작됩니다. 구독과 좋아요 부탁드립니다."},
    ]
    out = G._fit_duration([dict(s) for s in segs], budget=8.0, caps=G._NARR_CAP_LLM)   # absurdly tight
    roles = [s["role"] for s in out]
    assert "what" in roles and "sides" in roles


def test_watch_and_see_outro_variants_are_vague():
    from political_shorts import script_llm as S
    assert S._is_vague_outro("후속 인선 작업이 어떻게 풀릴지 지켜볼 필요가 있습니다")
    assert S._is_vague_outro("논란의 향방을 눈여겨볼 필요가 있습니다.")
    assert not S._is_vague_outro("후보 지명이 철회되면서 후속 인선이 처음부터 다시 시작됩니다.")


def test_soften_replaces_slurs_and_profanity_but_leaves_allegations_and_violence():
    from political_shorts.soften import soften_script, soften_text
    out, hits = soften_text("그는 상대를 빨갱이라고 부르며 병신이라고 말했다")
    assert "빨갱이" not in out and "병신" not in out and set(hits) == {"빨갱이", "병신"}
    assert soften_text("탄원서에 성추행 의혹이 담겼다")[0] == "탄원서에 성추행 의혹이 담겼다"   # allegation kept
    assert soften_text("처단하자는 구호")[0] == "처단하자는 구호"                             # violence is NOT softened
    assert soften_text("")[0] == ""

    script = {"headline": "토착왜구 발언 논란", "segments": [
        {"role": "hook", "narration": "그가 국개라고 했다", "caption": "국개 발언",
         "source": "https://example.com/빨갱이", "rows": [{"tag": "주장", "text": "쓰레기 정당이라 했다"}]}],
        "sources": [{"name": "빨갱이 뉴스", "url": "https://x/빨갱이"}]}
    words = soften_script(script)
    assert set(words) == {"토착왜구", "국개", "쓰레기 정당"} or "국개" in words
    seg = script["segments"][0]
    assert "국개" not in seg["narration"] and "쓰레기 정당" not in seg["rows"][0]["text"]
    assert seg["source"] == "https://example.com/빨갱이"            # non-prose fields are untouched
    assert script["sources"][0]["url"] == "https://x/빨갱이"


def test_every_outro_asks_for_a_comment_and_drops_the_bare_subscribe_ask():
    from political_shorts import script_gen as G
    q = "이번 인사 결정, 여러분은 어떻게 보시나요? 댓글로 의견을 남겨주세요."
    segs = [{"role": "outro", "narration": "후속 인선이 다시 시작됩니다. 구독과 좋아요 눌러주시면 큰 힘이 됩니다."}]
    G._ensure_comment_prompt(segs, q)
    assert segs[0]["narration"] == "후속 인선이 다시 시작됩니다. " + q
    assert "구독" not in segs[0]["narration"]
    keep = [{"role": "outro", "narration": "김 후보의 사퇴로 인선이 원점입니다. 여러분은 어떻게 보시나요? 댓글로 남겨주세요."}]
    G._ensure_comment_prompt(keep, q)
    assert keep[0]["narration"].count("댓글") == 1            # an outro that already asks is left alone


def test_engage_question_is_neutral_open_and_names_a_person_only_for_personnel():
    from political_shorts import explain
    from political_shorts.hook import Frame
    personnel = explain.engage_question(Frame(kind="personnel"), "김승원")
    assert personnel.startswith("김승원은 이번 결정") and "댓글" in personnel
    assert explain.engage_question(Frame(kind="personnel"), "청와대") == explain.ENGAGE["personnel"]
    for kind, text in explain.ENGAGE.items():
        assert "댓글" in text and "?" in text, kind
    assert explain.engage_question(Frame(kind="nonexistent")) == explain.ENGAGE["generic"]


def test_comment_prompt_does_not_stack_a_second_question_on_the_writers_own():
    from political_shorts import script_gen as G
    q = "이 갈등, 어느 쪽 주장이 더 설득력 있다고 보시나요? 댓글로 남겨주세요."
    segs = [{"role": "outro", "narration": "시각이 엇갈리고 있습니다. 이번 공방, 여러분은 어떻게 보시나요?"}]
    G._ensure_comment_prompt(segs, q)
    assert segs[0]["narration"] == "시각이 엇갈리고 있습니다. 이번 공방, 여러분은 어떻게 보시나요? 댓글로 의견을 남겨주세요."
    assert segs[0]["narration"].count("?") == 1
