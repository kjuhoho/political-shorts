"""FACT CHECK ENGINE — structured, source-attributed units + a review gate."""
from political_shorts.factcheck import extract


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
