"""Length target by story type — 속보는 짧게, 공방·복잡한 사건은 길게."""
from political_shorts.analyze import analyze
from political_shorts.hook import detect_entities, detect_frame
from political_shorts.storylen import classify_length


def _lp(title, body, n_sources=3, leans=("wire", "left", "right")):
    a = analyze(title, body)
    return classify_length(a, detect_frame(title, body),
                           detect_entities(title, body), n_sources, list(leans))


def test_single_event_is_brief():
    lp = _lp("국무총리 김민석 임명동의안 국회 통과",
             "김민석 국무총리 임명동의안이 3일 국회 본회의를 통과했다.")
    assert lp.cls == "brief"
    assert 25 <= lp.target_s <= 40


def test_yeoya_clash_is_debate():
    lp = _lp("예산안 본회의 통과 여야 정면충돌",
             "국회는 3일 예산안을 처리했다. 국민의힘은 합의 처리라고 밝혔다. "
             "더불어민주당은 독소조항이라고 반발했다. 찬반 표결 끝에 가결됐다.")
    assert lp.cls == "debate"
    assert 50 <= lp.target_s <= 75


def test_target_within_band():
    lp = _lp("아무 뉴스", "무슨 일이 있었다.")
    assert lp.min_s <= lp.target_s <= lp.max_s
