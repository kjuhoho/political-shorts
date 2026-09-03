from political_shorts.analyze import Kind, analyze, tag_sentence


def test_fact_sentence_detected():
    t = tag_sentence("국회는 3일 오전 본회의를 열고 예산안을 의결했다.")
    assert t.kind is Kind.FACT


def test_claim_sentence_detected():
    t = tag_sentence('야당 원내대표는 "명백한 위헌"이라고 말했다.')
    assert t.kind is Kind.CLAIM


def test_interpretation_sentence_detected():
    t = tag_sentence("이번 결정으로 여권의 정치적 부담이 커질 것으로 전망된다.")
    assert t.kind is Kind.INTERPRETATION


def test_analyze_buckets_and_dedupes():
    res = analyze(
        "국회는 3일 본회의를 열었다. 국회는 3일 본회의를 열었다.",
        '여당은 "합의 처리"라고 밝혔다. 후폭풍이 불가피해 보인다.',
    )
    assert len(res.facts) == 1
    assert len(res.claims) >= 1
    assert len(res.interpretations) >= 1
