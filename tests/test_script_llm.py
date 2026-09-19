"""The LLM narration rewrite is optional, source-grounded, and safe-by-default:
it only ever swaps `narration` text, and only if it doesn't add a safety block."""
import dataclasses
import json

from political_shorts import llm, script_llm
from political_shorts.config import settings


def _segs():
    return [
        {"role": "hook", "kicker": "오늘의 이슈", "caption": "예산안 처리",
         "narration": "국회가 예산안을 처리했습니다."},
        {"role": "what", "kicker": "무슨 일이냐면", "caption": "합의 통과",
         "narration": "여야가 합의해 통과시켰습니다.", "cues": ["date", "num"]},
        {"role": "outro", "kicker": "", "caption": "댓글로",
         "narration": "자세한 내용은 더보기란에 있습니다."},
    ]


META = {
    "source_text": "국회, 예산안 본회의 통과 … 여야 합의 처리",
    "facts": ["국회는 3일 본회의에서 예산안을 의결했다"],
    "claims": ["여당은 민생을 위한 결정이라고 밝혔다"],
    "interps": ["향후 정국에 영향을 줄 전망이다"],
    "entities": {"president": "이재명", "politicians": ["김민석"], "parties": ["민주당"]},
    "topic": "국회",
}
BASE = {
    "headline": "국회, 예산안 본회의 통과", "frame": "vote", "n_sources": 3,
    "sources": [{"name": "연합뉴스", "url": "https://y", "lean": "wire"},
                {"name": "한겨레", "url": "https://h", "lean": "left"},
                {"name": "동아일보", "url": "https://d", "lean": "right"}],
    "entities": META["entities"],
}


def _cfg(**kw):
    return dataclasses.replace(settings, llm_provider="gemini", gemini_api_key="k", **kw)


def _rw(*a, **k):
    """rewrite_segments now returns (segments, title); tests want the segments."""
    segs, _title = script_llm.rewrite_segments(*a, **k)
    return segs


def test_no_provider_is_a_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "complete", lambda *a, **k: calls.append(1) or "{}")
    segs = _segs()
    out = _rw(segs, META, settings, BASE)   # provider ""
    assert out == segs and calls == []


def test_valid_rewrite_is_applied(monkeypatch):
    payload = json.dumps({
        "hook": "국회가 내년 나라 살림 계획을 확정했습니다.",   # ignored — Hook Engine owns it
        "what": "여야가 막판까지 맞섰지만 결국 합의해 예산안을 통과시켰습니다.",
        "outro": "쟁점과 원문은 더보기란에 정리해 뒀습니다.",
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = _rw(_segs(), META, _cfg(), BASE)
    assert out[0]["narration"] == "국회가 예산안을 처리했습니다."   # hook untouched
    assert "합의해 예산안을 통과" in out[1]["narration"]
    assert out[2]["narration"].startswith("쟁점과 원문")


def test_feedback_is_folded_into_the_prompt_sent_to_the_model(monkeypatch):
    # the quality agent's critique on a prior attempt must actually reach the
    # model on a regenerate pass, not just be accepted and ignored.
    captured = {}

    def fake_complete(payload, cfg, **k):
        captured["payload"] = payload
        return json.dumps({"what": "여야가 결국 합의해 예산안을 통과시켰습니다."})

    monkeypatch.setattr(llm, "complete", fake_complete)
    _rw(_segs(), META, _cfg(), BASE,
        feedback="- (hook) 훅이 본문과 무관한 인물을 언급합니다.")
    assert "훅이 본문과 무관한 인물" in captured["payload"]
    assert "편집장 피드백" in captured["payload"]


def test_no_feedback_leaves_the_prompt_unchanged(monkeypatch):
    captured = {}

    def fake_complete(payload, cfg, **k):
        captured["payload"] = payload
        return json.dumps({"what": "여야가 결국 합의해 예산안을 통과시켰습니다."})

    monkeypatch.setattr(llm, "complete", fake_complete)
    _rw(_segs(), META, _cfg(), BASE)
    assert "편집장 피드백" not in captured["payload"]


def test_tolerates_nested_and_cards_shapes(monkeypatch):
    # a small model may echo {"cards":[{role,narration}]} or nest {narration:...}
    payload = json.dumps({"cards": [
        {"role": "hook", "narration": "국회가 내년 나라 살림 계획을 확정했습니다."},
        {"role": "what", "narration": "여야가 막판까지 맞섰지만 결국 합의해 통과시켰습니다."},
    ]})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = _rw(_segs(), META, _cfg(), BASE)
    assert out[0]["narration"] == "국회가 예산안을 처리했습니다."   # hook untouched
    assert "합의해 통과" in out[1]["narration"]

    payload2 = json.dumps({"what": {"narration": "여야가 밤샘 협상 끝에 예산안을 통과시켰습니다."}})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload2)
    out2 = _rw(_segs(), META, _cfg(), BASE)
    assert out2[1]["narration"] == "여야가 밤샘 협상 끝에 예산안을 통과시켰습니다."


def test_json_fence_is_stripped(monkeypatch):
    payload = "```json\n" + json.dumps({"what": "쉽게 풀어 설명하면 이렇습니다."}) + "\n```"
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = _rw(_segs(), META, _cfg(), BASE)
    assert out[1]["narration"] == "쉽게 풀어 설명하면 이렇습니다."


def test_garbage_response_keeps_template(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "sorry, I cannot help")
    segs = _segs()
    assert _rw(segs, META, _cfg(), BASE) == segs


def test_oversized_card_is_skipped_others_applied(monkeypatch):
    payload = json.dumps({
        "outro": "쟁점과 원문은 더보기란에 정리해 뒀습니다.",
        "what": "과도하게 긴 문장 " * 40,          # way over the cap -> skip this one
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = _rw(_segs(), META, _cfg(), BASE)
    assert out[2]["narration"].startswith("쟁점과 원문")
    assert out[1]["narration"] == "여야가 합의해 통과시켰습니다."   # unchanged


def test_sides_llm_limit_matches_script_gens_downstream_fit_cap():
    # script_llm tells the model its budget (_LLM_LIMIT) and clips its own
    # output to match; script_gen._fit_duration then clips AGAIN against a
    # separate constant (_NARR_CAP_LLM) on the way to the final video. The
    # two must be raised together — otherwise a longer "sides" card the
    # model was explicitly asked for gets silently cut back down by the
    # second, forgotten cap.
    from political_shorts.script_gen import _NARR_CAP_LLM
    assert script_llm._LLM_LIMIT["sides"] == _NARR_CAP_LLM["sides"]


def test_sides_card_has_room_for_a_third_expert_voice(monkeypatch):
    # user: "민주당 측에서는 --, 야당인 국민의 힘에서는 --, 그리고 -- 전문가가
    # 말하길 --" — a real 3-voice sides card (both parties + an expert) runs
    # past the old 220-char cap; it must not get silently discarded as
    # "oversized" now that the budget was raised to make room for it.
    three_voice = (
        "이런 상황 속에서 민주당은 서민 가계에 돌아갈 부담이 지나치게 크다는 "
        "이유를 들어 이번 조치에 강하게 반대한다고 밝혔습니다. "
        "국민의힘은 지금 손대지 않으면 나라 살림 자체가 무너질 수 있다며, "
        "재정 건전성을 지키기 위해 꼭 필요한 조치라고 반박했습니다. "
        "그런데 서울대 정치외교학과 김모 교수는 이 사안을 두고, "
        "여야 모두 겉으로는 원칙을 내세우지만 실제로는 다가올 총선을 "
        "의식한 정치적 계산이 깔려 있다고 짚었습니다."
    )
    assert 220 < len(three_voice) <= 260          # past the OLD cap, within the NEW one
    segs = _segs() + [{"role": "sides", "kicker": "갈리는 입장", "caption": "입장차",
                       "narration": "국민의힘과 민주당의 입장이 갈립니다.", "attributed": True}]
    payload = json.dumps({"sides": three_voice})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = _rw(segs, META, _cfg(), BASE)
    sides = next(s for s in out if s["role"] == "sides")
    assert sides["narration"] == three_voice
    assert "교수" in sides["narration"]              # the third voice actually survived


def test_llm_subtitle_is_attached_separately_from_narration(monkeypatch):
    payload = json.dumps({
        "what": "여야가 막판까지 맞섰지만 결국 합의해 예산안을 통과시켰습니다.",
        "subtitles": {"what": "여야 합의, 예산안 통과"},
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = _rw(_segs(), META, _cfg(), BASE)
    what = next(s for s in out if s["role"] == "what")
    assert what["_llm_sub"] == "여야 합의, 예산안 통과"
    assert what["_llm_sub"] != what["narration"]      # screen != voice


def test_rewrite_that_adds_a_safety_block_is_rejected(monkeypatch):
    # inject a slur from safety.HARMFUL -> the rewrite gains a block the
    # templated version didn't have, so it must be discarded
    payload = json.dumps({"what": "상대를 빨갱이라고 부르며 표결이 시작됐습니다."})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    segs = _segs()
    assert _rw(segs, META, _cfg(), BASE) == segs


def test_llm_title_used_when_it_matches_content(monkeypatch):
    payload = json.dumps({
        "title": ["예산안 국회 통과", "뭐가 바뀌나?"],
        "what": "여야가 막판까지 맞섰지만 결국 합의해 예산안을 통과시켰습니다.",
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    _segsr, title = script_llm.rewrite_segments(_segs(), META, _cfg(), BASE)
    assert title == ["예산안 국회 통과", "뭐가 바뀌나?"]


def test_llm_title_dropped_when_off_topic(monkeypatch):
    payload = json.dumps({
        "title": ["삼성전자 실적 발표", "충격?"],       # nothing to do with the story
        "what": "여야가 막판까지 맞섰지만 결국 합의해 예산안을 통과시켰습니다.",
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    _segsr, title = script_llm.rewrite_segments(_segs(), META, _cfg(), BASE)
    assert title == []


def test_llm_facts_table_replaces_factcheck_rows(monkeypatch):
    payload = json.dumps({
        "what": "여야가 막판까지 맞섰지만 결국 합의해 예산안을 통과시켰습니다.",
        "facts_table": {"사실": "예산안이 3일 본회의를 통과했습니다.",
                        "주장": "여당측: 민생을 위한 결정이라는 입장입니다.",
                        "전망": "야당 반발로 후속 갈등이 예상됩니다."},
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    segs = _segs() + [{"role": "factcheck", "kicker": "확인된 사실", "caption": "팩트체크",
                       "narration": "확인된 사실은 이겁니다.",
                       "rows": [{"tag": "사실", "tone": "ok", "text": "old"},
                                {"tag": "확인", "tone": "info", "text": "2개 매체 종합"}]}]
    out, _t = script_llm.rewrite_segments(segs, META, _cfg(), BASE)
    fc = next(s for s in out if s["role"] == "factcheck")
    tags = [r["tag"] for r in fc["rows"]]
    assert tags == ["사실", "주장", "전망", "확인"]
    assert fc["rows"][0]["text"].startswith("예산안이 3일")
    assert fc["rows"][-1]["text"] == "2개 매체 종합"     # template's 확인 row kept


def test_narration_ending_on_a_bare_noun_falls_back_to_template(monkeypatch):
    # a real shipped card: within the ~40자 guideline, valid JSON, but the
    # model stopped on a bare noun with no predicate ("...총괄하는 대한민국.")
    # instead of finishing the sentence ("...정부 부처입니다."). Must not ship.
    payload = json.dumps({
        "what": "통일부는 남북관계와 통일 외교 정책을 총괄하는 대한민국.",
        "outro": "쟁점과 원문은 더보기란에 정리해 뒀습니다.",
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    segs = _segs()
    out = _rw(segs, META, _cfg(), BASE)
    assert out[1]["narration"] == "여야가 합의해 통과시켰습니다."   # template kept, not the fragment
    assert out[2]["narration"].startswith("쟁점과 원문")            # the other card still applied


def test_vague_watch_and_see_outro_falls_back_to_template(monkeypatch):
    # a real shipped outro: complete sentence, on-topic, but closes on nothing
    # but the exact cliché the prompt already bans — "...지켜봐야 합니다." A
    # prompt instruction alone didn't stop it from shipping; this must be
    # rejected in code too, the same way an incomplete sentence is.
    payload = json.dumps({
        "outro": "오랫동안 비어 있던 법적 공백 속에서 여성 건강권과 직결된 약물 기준이 "
                 "어떻게 안착할지 지켜봐야 합니다.",
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    segs = _segs()
    out = _rw(segs, META, _cfg(), BASE)
    assert out[2]["narration"] == "자세한 내용은 더보기란에 있습니다."   # template kept


def test_vague_outro_synonyms_found_in_real_ci_output_are_also_banned():
    # a real CI run showed the LLM reaching for synonyms not in the
    # original banned list — "눈여겨봐야 합니다", "살펴볼 필요가
    # 있습니다", "추가 확인이 필요합니다", "살펴봐야 합니다" — none of
    # which contain the literal "지켜봐야"/"주목" the original regex
    # matched, but all of which are the same "관망형" non-answer.
    for bad in (
        "어떤 결과를 가져올지 지켜봐야 합니다.",
        "귀추가 주목되는 부분입니다. 앞으로 상황을 눈여겨봐야 합니다.",
        "관련 논의를 계속 살펴볼 필요가 있습니다.",
        "정확한 경위는 추가 확인이 필요합니다.",
        "여야 셈법이 어떻게 흘러갈지 살펴봐야 합니다.",
    ):
        assert script_llm._is_vague_outro(bad), f"should be flagged: {bad!r}"


def test_facts_table_row_marked_incomplete_even_under_the_clip_budget(monkeypatch):
    # a real shipped row: SHORT enough (under 52 chars) that clip_sentence
    # never had anything to cut, but the model's own sentence stops on a bare
    # verb stem with no predicate ("...협력이 더욱 깊어질"). Must be marked,
    # not shipped as if it were a finished thought.
    short_incomplete = "이번 정상 배우자의 만남을 계기로 한국과 우즈베키스탄의 문화 외교적 협력이 더욱 깊어질"
    assert len(short_incomplete) < 52
    payload = json.dumps({
        "what": "여야가 막판까지 맞섰지만 결국 합의해 예산안을 통과시켰습니다.",
        "facts_table": {"사실": "예산안이 3일 본회의를 통과했습니다.",
                        "주장": "여당측: 민생을 위한 결정입니다.",
                        "전망": short_incomplete},
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    segs = _segs() + [{"role": "factcheck", "kicker": "확인된 사실", "caption": "팩트체크",
                       "narration": "확인된 사실은 이겁니다.",
                       "rows": [{"tag": "사실", "tone": "ok", "text": "old"},
                                {"tag": "확인", "tone": "info", "text": "2개 매체 종합"}]}]
    out, _t = script_llm.rewrite_segments(segs, META, _cfg(), BASE)
    fc = next(s for s in out if s["role"] == "factcheck")
    outlook_row = next(r for r in fc["rows"] if r["tag"] == "전망")
    assert outlook_row["text"].endswith("..")
    assert outlook_row["text"] != short_incomplete    # visibly marked, not passed through


def test_llm_facts_table_row_never_looks_complete_when_its_not(monkeypatch):
    # a real shipped row: the LLM's own "사실" sentence ran long and dense —
    # no comma/sentence-end near the old flat 52-char cut, so the previous
    # _clean_row (a separate, un-fixed truncator from textutil.clip_sentence)
    # silently dropped "..승인받았습니다" and left "...유엔 제재 면제를" on
    # screen with no verb and no sign it was cut.
    long_fact = ("정부는 평양 강동군병원에 100억원 규모의 의료장비 지원을 추진하고 "
                 "유엔 제재 면제를 승인받았습니다")
    payload = json.dumps({
        "what": "여야가 막판까지 맞섰지만 결국 합의해 예산안을 통과시켰습니다.",
        "facts_table": {"사실": long_fact, "주장": "여당측: 민생을 위한 결정입니다.",
                        "전망": "야당 반발로 후속 갈등이 예상됩니다."},
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    segs = _segs() + [{"role": "factcheck", "kicker": "확인된 사실", "caption": "팩트체크",
                       "narration": "확인된 사실은 이겁니다.",
                       "rows": [{"tag": "사실", "tone": "ok", "text": "old"},
                                {"tag": "확인", "tone": "info", "text": "2개 매체 종합"}]}]
    out, _t = script_llm.rewrite_segments(segs, META, _cfg(), BASE)
    fc = next(s for s in out if s["role"] == "factcheck")
    fact_row = next(r for r in fc["rows"] if r["tag"] == "사실")
    assert fact_row["text"] != long_fact                 # it was in fact shortened
    assert fact_row["text"].endswith("..")                # marked, not silently cut
    assert "면제를" not in fact_row["text"]                 # never reaches the dangling tail


UNDERSTANDING = {
    "who": [{"name": "김민석", "role": "더불어민주당 대표"}],
    "what_happened": "김민석 대표가 탄핵 전조 발언을 한 데 대해 조국 원장이 비판했고, 김 대표가 재반박했다.",
    "why_now": "전날 방송에서 나온 발언이 논란이 됐다.",
    "why_it_matters": "여권 내 노선 갈등이 드러났다는 점에서 주목된다.",
    "terms": {"탄핵 전조": "대통령을 국회가 파면하려는 조짐"},
    "sides": [{"who": "김민석", "position": "걱정을 위장한 흔들기다", "why": "법적 하자가 없다는 이유"}],
    "confirmed_fact": "김민석 대표는 15일 방송에서 관련 발언을 했다.",
}


def _two_stage_mock(monkeypatch, stage2_payload: str):
    """`complete()` returns the stage-1 UNDERSTANDING when called with the
    analysis system prompt, and `stage2_payload` for every other call —
    mirrors how the two real calls are told apart in rewrite_segments."""
    calls: list[dict] = []

    def fake(prompt, cfg, max_tokens=400, system=""):
        calls.append({"prompt": prompt, "system": system})
        if system == script_llm._ANALYSIS_SYSTEM:
            return json.dumps(UNDERSTANDING)
        return stage2_payload

    monkeypatch.setattr(llm, "complete", fake)
    return calls


def test_analyze_story_returns_none_without_a_provider():
    assert script_llm.analyze_story(META, settings) is None   # provider ""


def test_analyze_story_parses_a_valid_understanding(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps(UNDERSTANDING))
    out = script_llm.analyze_story(META, _cfg())
    assert out["what_happened"] == UNDERSTANDING["what_happened"]
    assert out["sides"][0]["who"] == "김민석"


def test_analyze_story_gives_up_on_unusable_json(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "not json at all")
    assert script_llm.analyze_story(META, _cfg()) is None


def test_analyze_story_gives_up_on_empty_understanding(monkeypatch):
    # parses fine but carries nothing usable -> treated as a miss
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps({"terms": {}}))
    assert script_llm.analyze_story(META, _cfg()) is None


def test_understanding_block_formats_the_key_fields():
    block = script_llm._understanding_block(UNDERSTANDING)
    assert "김민석: 더불어민주당 대표" in block
    assert UNDERSTANDING["what_happened"] in block
    assert "탄핵 전조: 대통령을 국회가 파면하려는 조짐" in block
    assert block.startswith("[분석 1단계")


def test_understanding_block_empty_when_no_understanding():
    assert script_llm._understanding_block(None) == ""


def test_payload_drops_template_draft_once_understood():
    spoken = _segs()[1:2]                    # the "what" card, has a narration draft
    with_ub = script_llm._payload(META, spoken, understanding=UNDERSTANDING)
    without_ub = script_llm._payload(META, spoken, understanding=None)
    assert "여야가 합의해 통과시켰습니다" not in with_ub   # draft text left out
    assert "여야가 합의해 통과시켰습니다" in without_ub     # ...but present without stage 1
    assert "[분석 1단계" in with_ub


def test_rewrite_runs_analysis_stage_then_writes_from_it(monkeypatch):
    stage2 = json.dumps({"what": "김민석 대표가 조국 원장의 비판에 재반박했습니다."})
    calls = _two_stage_mock(monkeypatch, stage2)
    out = _rw(_segs(), META, _cfg(), BASE)
    assert "재반박" in out[1]["narration"]
    # the analysis call happened, and its output reached the writing call
    assert any(c["system"] == script_llm._ANALYSIS_SYSTEM for c in calls)
    write_calls = [c for c in calls if c["system"] != script_llm._ANALYSIS_SYSTEM]
    assert write_calls and "탄핵 전조" in write_calls[0]["prompt"]


def test_rewrite_still_works_when_analysis_stage_fails(monkeypatch):
    # complete() only ever returns a stage-2-shaped payload -> analyze_story
    # can't parse it as an understanding and gives up; the rewrite must still
    # go through on the raw facts/claims, exactly as before stage 1 existed.
    payload = json.dumps({"what": "여야가 막판까지 맞섰지만 결국 합의해 통과시켰습니다."})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = _rw(_segs(), META, _cfg(), BASE)
    assert "합의해 통과" in out[1]["narration"]


def test_gemini_request_shape(monkeypatch):
    seen = {}

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"ok":1}'}]}}]}

    def fake_post(url, params=None, json=None, timeout=None):
        seen["url"], seen["params"], seen["body"] = url, params, json
        return _R()

    import political_shorts.llm as L
    monkeypatch.setattr(L, "requests", type("m", (), {"post": staticmethod(fake_post)}))
    txt = L._gemini("hi", _cfg(llm_model="gemini-2.0-flash"), 300, "sys")
    assert txt == '{"ok":1}'
    assert "gemini-2.0-flash:generateContent" in seen["url"]
    assert seen["params"]["key"] == "k"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_groq_rate_limit_moves_to_next_model(monkeypatch):
    posted = []

    class _R:
        def __init__(self, status):
            self.status_code = status

        def json(self):
            if self.status_code == 429:
                return {"error": {"message": "Rate limit reached"}}
            return {"choices": [{"message": {"content": '{"ok":1}'}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        posted.append(json["model"])
        return _R(429 if json["model"] == "openai/gpt-oss-120b" else 200)

    import political_shorts.llm as L
    monkeypatch.setattr(L, "requests", type("m", (), {"post": staticmethod(fake_post)}))
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    cfg = dataclasses.replace(settings, llm_provider="groq", llm_model="")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert L._groq("hi", cfg, 300, "sys") == '{"ok":1}'
    assert posted[-1] == "openai/gpt-oss-20b"


def test_complete_falls_back_to_next_provider(monkeypatch):
    import political_shorts.llm as L
    calls = []

    def fake_call(provider, prompt, cfg, max_tokens, system):
        calls.append((provider, cfg.llm_model))
        if provider == "groq":
            raise RuntimeError("groq call failed (429)")
        return '{"ok":1}'

    monkeypatch.setattr(L, "_call", fake_call)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cfg = dataclasses.replace(settings, llm_provider="groq", llm_model="some-groq-model",
                              gemini_api_key="g", openai_api_key="", anthropic_api_key="")
    assert L.complete("hi", cfg, 300, "sys") == '{"ok":1}'
    assert calls == [("groq", "some-groq-model"), ("gemini", "")]   # model name not leaked


def test_complete_raises_when_every_provider_fails(monkeypatch):
    import political_shorts.llm as L
    import pytest

    monkeypatch.setattr(L, "_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setenv("GROQ_API_KEY", "k")
    cfg = dataclasses.replace(settings, llm_provider="groq", gemini_api_key="g",
                              openai_api_key="", anthropic_api_key="")
    with pytest.raises(RuntimeError):
        L.complete("hi", cfg, 300, "sys")
