from political_shorts.textutil import (
    canonical_url,
    clip_sentence,
    gloss_jargon,
    normalize_title,
    split_sentences,
    strip_byline,
    url_hash,
)


def test_strip_byline_leading_dateline_and_reporter():
    s = "(서울=연합뉴스) 이정현 조다운 기자 = 국민의힘은 31일 예산안 처리를 촉구했다."
    out = strip_byline(s)
    assert out.startswith("국민의힘은 31일")
    assert "기자" not in out and "연합뉴스" not in out


def test_strip_byline_trailing_footer():
    s = "여야가 예산안 처리에 합의했다. 홍길동 기자 사진=국회사진기자단 (끝)"
    out = strip_byline(s)
    assert out.endswith("합의했다.")


def test_strip_byline_keeps_plain_text():
    s = "국회는 3일 오후 본회의를 열어 예산안을 의결했다."
    assert strip_byline(s) == s


def test_canonical_url_drops_tracking():
    a = canonical_url("https://a.com/x?utm_source=rss&id=5")
    assert "utm_source" not in a and "id=5" in a
    assert url_hash("https://a.com/x/") == url_hash("https://a.com/x")


def test_split_sentences_korean():
    parts = split_sentences("국회가 열렸다. 여야가 충돌했다\n예산안은 가결됐다.")
    assert len(parts) == 3


def test_gloss_jargon_explains_regulatory_abbreviation():
    # a real user complaint: "토허구역 실거주" means nothing to someone who
    # doesn't follow real-estate policy news — it must never appear bare.
    out = gloss_jargon("이 아파트는 토허구역에 속해 실거주 의무가 있다.")
    assert "토허구역(" in out and "규제 지역" in out


def test_gloss_jargon_does_not_double_up():
    once = gloss_jargon("토허구역 지정 소식이 전해졌다.")
    twice = gloss_jargon(once)
    assert once == twice


def test_gloss_jargon_leaves_plain_text_untouched():
    s = "국회는 3일 본회의에서 예산안을 처리했다."
    assert gloss_jargon(s) == s


def test_gloss_jargon_explains_corruption_terms():
    # a real user complaint: "공천헌금과 같은 용어는 실제 나도... 무슨
    # 뜻인지 정확히 모름" — glossed with a plain, factually neutral phrase,
    # never re-labeled as a DIFFERENT charge ("뇌물" etc. — overclaiming
    # one legal charge as another is exactly the exaggeration this
    # project's neutrality rule bans).
    out = gloss_jargon("검찰은 강선우 의원의 공천헌금 의혹을 수사 중이다.")
    assert "공천헌금(" in out
    assert "뇌물" not in out

    out2 = gloss_jargon("이 사건은 배임수재 혐의로 기소됐다.")
    assert "배임수재(" in out2

    # the longer compound must win over the shorter word it contains —
    # "배임" alone must not also fire inside "배임수재"
    assert out2.count("배임") == 1


def _quote_balance(s: str) -> bool:
    return (s.count("“") == s.count("”") and s.count("‘") == s.count("’")
            and s.count('"') % 2 == 0 and s.count("'") % 2 == 0)


def test_clip_sentence_never_ends_mid_quote():
    # the real card that shipped broken: cut lands inside a nested quote with
    # no comma/connective anywhere to break on, and the outer “ never closes
    # within this fragment — must retreat before it, not trail off inside it.
    s = ("김 대표는 이날 민주당 공식 유튜브 채널 '민주당 TV'에 출연해 "
         "“'괜히 탄핵 이야기를 꺼낸 것이 경솔했다'고 할 수 있다.")
    out = clip_sentence(s, 64)
    assert _quote_balance(out)
    assert not out.rstrip().endswith(("…", "'", "‘", "“"))


def test_clip_sentence_extends_to_a_nearby_closing_quote():
    # the close IS within reach -> extend to it rather than retreat past
    # the whole quote (keeps more of the actual content than dropping it).
    s = "김 대표는 “예산안을 이번 주 안에 반드시 처리하겠다” 라고 분명히 밝혔습니다."
    out = clip_sentence(s, 24)
    assert _quote_balance(out)
    assert "예산안을 이번 주 안에 반드시 처리하겠다" in out


def test_clip_sentence_still_prefers_a_clean_sentence_end():
    s = "국회는 3일 예산안을 의결했다. 이어 다음 안건을 논의했다."
    assert clip_sentence(s, 20) == "국회는 3일 예산안을 의결했다."
