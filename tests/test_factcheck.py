"""FACT CHECK ENGINE — structured, source-attributed units + a review gate."""
from political_shorts.factcheck import _clip, extract


def _row(name, url, title, summary):
    return {"source_name": name, "url": url, "title": title, "summary": summary}


def test_cross_source_fact_gets_higher_confidence():
    rows = [
        _row("연합뉴스", "http://y", "예산안 통과",
             "국회는 3일 본회의에서 내년도 예산안을 처리했다. 예산안은 찬성 210표로 가결됐다."),
        _row("동아일보", "http://d", "[속보] 예산안 국회 통과",
             "국회는 3일 본회의에서 내년도 예산안을 처리했다."),
    ]
    fc = extract(rows)
    shared = next(u for u in fc.units if "예산안을 처리" in u.text)
    assert len(shared.sources) == 2
    assert shared.confidence >= 0.75
    assert shared.kind in ("FACT", "QUOTE")


def test_quote_is_classified_as_such_with_a_speaker():
    rows = [_row("연합뉴스", "http://y", "예산안",
                 '국회는 예산안을 처리했다. 국민의힘은 "합의 처리"라고 밝혔다.')]
    fc = extract(rows)
    q = next(u for u in fc.units if u.kind == "QUOTE")
    assert "국민의힘" in (q.speaker or q.text)


def test_lone_low_confidence_allegation_triggers_review():
    rows = [_row("한겨레", "http://h", "A 의원 뇌물 의혹",
                 "B가 A 의원이 뇌물을 받았다는 의혹을 제기했다.")]
    fc = extract(rows)
    assert fc.review_required is True
    assert "뇌물" in fc.review_reason


def test_rows_carry_source_attribution():
    rows = [
        _row("연합뉴스", "http://y", "국회 예산안 통과",
             "국회는 3일 본회의에서 예산안을 처리했다."),
        _row("한겨레", "http://h", "예산안 의결",
             "국회는 3일 본회의에서 예산안을 처리했다."),
    ]
    r = extract(rows).rows(2)
    fact_row = next(x for x in r if x["tag"] == "사실")
    assert "연합뉴스" in fact_row["source"] and "한겨레" in fact_row["source"]


def test_clip_never_looks_complete_when_it_isnt():
    # a real shipped row: dense with numbers, no comma/clause break at all in
    # range -> the old flat 46-char space-cut with ell="" produced "...유엔
    # 제재 면제를" (dangling object particle, no verb, no visible sign it was
    # cut). Now it must either find a clause break or mark the cut visibly.
    s = ("정부는 평양 강동군병원에 100억원 규모 의료장비 166종 지원 추진 유엔 제재 "
         "면제 방안 검토 중이다")
    out = _clip(s)
    assert out != s                          # it is in fact shorter
    assert out.endswith("..") or out.rstrip(".").endswith(("다", "요", "고", "며"))
    assert not out.rstrip(".").endswith(("를", "을", "은", "는", "이", "가", "에"))


def test_clip_prefers_a_clause_break_and_marks_it():
    s = ("정부는 평양 강동군병원에 100억원 규모 의료장비 166종 지원을 추진하고 "
         "유엔 제재 면제를 받는 방안도 검토하고 있다.")
    out = _clip(s)
    assert out.endswith("추진하고..")            # cuts cleanly on the "고" connective
    assert "면제를" not in out                  # never reaches the dangling fragment


def test_clip_keeps_a_short_complete_sentence_untouched():
    s = "국회는 3일 본회의에서 예산안을 의결했다."
    assert _clip(s) == s


def test_clip_marks_a_short_fragment_even_under_budget():
    # a real shipped row: well under the 52-char budget (nothing for
    # clip_sentence to cut), but the underlying FactUnit text is itself a
    # fragment with no predicate ("...문화 교류를").
    s = "김혜경 여사와 우즈벡 영부인이 14일 국립고궁박물관을 방문해 문화 교류를"
    assert len(s) < 52
    out = _clip(s)
    assert out.endswith("..")
    assert out != s


def test_claim_row_budget_accounts_for_the_speaker_prefix():
    # "{who}: {said}" must fit the same on-screen budget as a plain _clip —
    # a long speaker label must not push the whole row past the render limit.
    # (a dated fact, corroborated by both sources, so it -- not the quote --
    # becomes `f`, leaving the quote free to land in its own 주장 row.)
    rows = [
        _row("경향신문", "http://a", "협력 추진",
             '정부는 3일 평양 강동군병원에 의료장비 지원을 추진한다고 밝혔다. '
             '국민의힘측은 "정부의 대북 보건의료 협력 추진은 굴종적인 대북 정책이므로 '
             '전면 재검토해야 한다"고 밝혔다.'),
        _row("국민일보", "http://b", "협력 추진",
             "정부는 3일 평양 강동군병원에 의료장비 지원을 추진한다고 밝혔다."),
    ]
    fc = extract(rows)
    r = fc.rows(2)
    claim_row = next((x for x in r if x["tag"] == "주장"), None)
    assert claim_row is not None
    assert len(claim_row["text"]) <= 56
