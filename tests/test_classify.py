from political_shorts.classify import classify_text


def test_domestic_politics_positive():
    r = classify_text(
        "여야, 국회 본회의서 예산안 처리 놓고 충돌",
        "더불어민주당과 국민의힘은 3일 본회의에서 내년도 예산안 처리를 두고 맞섰다.",
    )
    assert r.is_politics is True
    assert r.score >= 3.0
    assert any(k in r.hits for k in ("국회", "본회의", "예산안"))


def test_party_only_headline_is_politics():
    r = classify_text("국민의힘 원내대표, 대통령실과 당정협의", "")
    assert r.is_politics is True


def test_sports_is_not_politics():
    r = classify_text("프로야구 개막전 매진…손흥민도 시구", "K리그 일정도 발표됐다.")
    assert r.is_politics is False


def test_foreign_only_downweighted():
    r = classify_text("미국 대선 앞두고 트럼프 지지율 상승", "백악관은 논평을 내지 않았다.")
    assert r.is_politics is False


def test_foreign_with_domestic_anchor_counts():
    r = classify_text(
        "한미 정상, 국회 연설 조율…대통령실 브리핑",
        "외교부와 대통령실은 한미 현안을 협의했다고 밝혔다.",
    )
    assert r.is_politics is True
