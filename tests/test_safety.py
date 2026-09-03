from political_shorts.config import load_settings
from political_shorts.safety import review_script

CFG = load_settings()


def _script(**over):
    base = {
        "cluster_id": 1,
        "headline": "국회 본회의, 예산안 개정안 의결",
        "n_sources": 3,
        "leans": ["wire", "left", "right"],
        "frame": "vote",
        "entities": {"president": False, "politicians": [], "parties": ["민주당", "국민의힘"],
                     "institutions": ["국회"]},
        "images": [],
        "segments": [
            {"role": "hook", "kicker": "오늘의 이슈", "caption": "예산안 결국 통과됐습니다. 왜 시끄러울까요?",
             "narration": "예산안이 결국 통과됐습니다. 웃는 쪽과 반발하는 쪽이 갈렸습니다. 핵심만 짚어드릴게요."},
            {"role": "summary", "kicker": "한 줄 요약", "caption": "쉽게 말하면, 국회가 내년 예산안을 통과시켰습니다.",
             "narration": "쉽게 말하면, 국회가 3일 내년도 예산안을 통과시켰습니다."},
            {"role": "what", "kicker": "무슨 일이냐면", "caption": "3일 오전 본회의에서 표결이 이뤄졌습니다.",
             "narration": "국회는 3일 오전 본회의를 열어 예산안을 표결에 부쳤습니다.",
             "source": "연합뉴스", "multi_source": True, "cues": ["date", "num"]},
            {"role": "reaction", "kicker": "양쪽 반응", "caption": "야당은 '독소조항'이라며 반발했습니다.",
             "narration": "한쪽에선 합의 처리라고 말합니다. 반면 다른 쪽은 독소조항이라며 맞섭니다.",
             "attributed": True, "cues": ["주장했다"]},
            {"role": "factcheck", "kicker": "팩트체크", "caption": "팩트체크",
             "rows": [{"mark": "✅", "label": "확인된 사실", "text": "3일 본회의에서 예산안이 표결됐다"},
                      {"mark": "⚠️", "label": "아직은 해석", "text": "여권 부담이 커질 것이라는 전망"}],
             "narration": "마지막으로 어디까지가 사실인지 짚어볼게요. ✅ 3일 표결은 사실. ⚠️ 정치적 여파는 전망."},
            {"role": "outro", "kicker": "", "caption": "여러분 생각은 어떤가요? 댓글로 알려주세요",
             "narration": "여러 성향의 매체 보도를 종합했습니다. 원문 링크는 더보기란에 있습니다."},
        ],
        "sources": [
            {"name": "연합뉴스", "url": "https://e/a", "lean": "wire"},
            {"name": "한겨레", "url": "https://e/b", "lean": "left"},
            {"name": "동아일보", "url": "https://e/c", "lean": "right"},
        ],
        "disclaimer": "고지문.",
        "style": "punchy",
    }
    base.update(over)
    return base


def test_balanced_script_passes():
    rep = review_script(_script(), CFG)
    assert rep.passed is True, rep.blocks


def test_harmful_language_blocks():
    s = _script()
    s["segments"][0]["narration"] = "저 빨갱이들을 처단하자"
    rep = review_script(s, CFG)
    assert rep.passed is False
    assert any("유해" in b for b in rep.blocks)


def test_absolute_framing_without_attribution_blocks():
    s = _script()
    s["segments"][2]["narration"] = "이 법안은 100% 조작이다"
    s["segments"][2]["cues"] = []
    rep = review_script(s, CFG)
    assert rep.passed is False


def test_unbacked_allegation_on_named_person_blocks():
    s = _script()
    s["segments"][0]["narration"] = "이재명, 결국 구속되나? 오늘 큰일이 벌어졌습니다."
    s["segments"][0]["caption"] = "이재명 구속 초읽기?"
    # body has no supporting mention of 구속
    rep = review_script(s, CFG)
    assert rep.passed is False
    assert any("근거 없이" in b for b in rep.blocks)


def test_allegation_backed_by_body_passes():
    s = _script()
    s["segments"][0]["caption"] = "이재명 수사 어디까지?"
    s["segments"][0]["narration"] = "이재명 대통령을 둘러싼 수사, 어디까지 왔을까요?"
    s["segments"][2]["narration"] = "검찰은 3일 관련 수사에 착수했다고 밝혔습니다."
    rep = review_script(s, CFG)
    assert rep.passed is True, rep.blocks


def test_single_source_what_without_cue_blocks():
    s = _script(n_sources=1)
    s["segments"][2]["cues"] = ["headline-only"]
    s["segments"][2]["multi_source"] = False
    rep = review_script(s, CFG)
    assert rep.passed is False
    assert any("단일 출처" in b for b in rep.blocks)


def test_sensational_hook_is_allowed():
    s = _script()
    s["segments"][0]["narration"] = "정치권이 발칵 뒤집혔습니다. 충격적인 하루였습니다."
    rep = review_script(s, CFG)
    assert rep.passed is True  # loaded words in the HOOK do not block


def test_sensational_body_warns():
    s = _script()
    s["segments"][1]["narration"] = "쉽게 말하면 야당이 완패했고 참패 확정입니다."
    rep = review_script(s, CFG)
    assert rep.passed is True
    assert any("자극적" in w for w in rep.warnings)


def test_no_sources_blocks():
    rep = review_script(_script(sources=[]), CFG)
    assert rep.passed is False
