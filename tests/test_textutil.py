from political_shorts.textutil import (
    canonical_url,
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
