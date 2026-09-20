"""Speaker attribution, the vetted hook, one-sided-source balancing, and the waste fixes."""
import dataclasses
import json

from political_shorts import llm as L
from political_shorts import quality_agent, research, script_llm
from political_shorts.config import settings


# ------------------------------------------------------------- sides: one speaker per sentence
def test_compose_sides_keeps_each_speakers_claim_and_reason_apart():
    """The published bug: the province's own reason (budget shortage) ended up in the critic's sentence."""
    story = {"sides": [
        {"who": "김민석 대표", "claim": "김민석 대표는 경기도의 영화제 축소를 계약 파기라고 비판했습니다.",
         "reason": "", "lean": "진보"},
        {"who": "경기도", "claim": "경기도는 재정 상황을 이유로 운영을 줄였다고 설명했습니다.",
         "reason": "", "lean": "보수"},
    ]}
    out = script_llm._compose_sides(story)
    critic, province = [s.strip() for s in out.split("경기도는")[0:2]]
    assert "재정" not in critic                                   # the province's reason is not in the critic's sentence
    assert "경기도는 재정 상황을 이유로" in out
    assert "진보 성향 매체 보도에 따르면, 김민석 대표는" in out       # both camps present -> each side labelled
    assert "보수 성향 매체 보도에 따르면, 경기도는" in out


def test_compose_sides_drops_a_sentence_that_never_names_its_speaker():
    story = {"sides": [{"who": "야당", "claim": "재정 부족 때문에 축소가 불가피했다고 밝혔습니다.",   # no "야당" in it
                        "reason": "야당은 예산 삭감이 문화 기반을 약화시킨다고 비판했습니다.", "lean": "기타"}]}
    out = script_llm._compose_sides(story)
    assert out == "야당은 예산 삭감이 문화 기반을 약화시킨다고 비판했습니다."


def test_compose_sides_needs_a_complete_sentence_and_handles_parenthesised_names():
    story = {"sides": [
        {"who": "청와대(강유정 수석대변인)", "claim": "청와대는 사퇴 결정을 존중한다고 밝혔습니다.", "reason": "", "lean": "기타"},
        {"who": "야당", "claim": "야당은 탄원서 접수 시점을", "reason": "", "lean": "기타"},     # cut mid-sentence
    ]}
    assert script_llm._compose_sides(story) == "청와대는 사퇴 결정을 존중한다고 밝혔습니다."
    assert script_llm._compose_sides(None) == "" and script_llm._compose_sides({"sides": []}) == ""


def test_single_camp_material_is_not_labelled():
    story = {"sides": [{"who": "정부", "claim": "정부는 파병 계획이 없다고 밝혔습니다.", "reason": "", "lean": "진보"}]}
    assert not script_llm._compose_sides(story).startswith("진보 성향")


# ------------------------------------------------------------------------------ hook
META = {"headline": "김승원 후보자 자진 사퇴", "topic": "김승원"}


def test_valid_hook_accepts_a_concrete_curious_question():
    assert script_llm._valid_hook("김승원 후보자는 왜 갑자기 사퇴를 택했을까요?", META) == "김승원 후보자는 왜 갑자기 사퇴를 택했을까요?"


def test_valid_hook_rejects_vague_offtopic_or_non_question_lines():
    assert script_llm._valid_hook("경기도를 두고 정치권이 정면으로 부딪히고 있습니다.", META) == ""   # vague + no question
    assert script_llm._valid_hook("정치권에서 무슨 일이 벌어지고 있을까요?", META) == ""              # vague phrasing
    assert script_llm._valid_hook("이번 사안은 왜 논란이 일고 있을까요 여러분?", META) == ""
    assert script_llm._valid_hook("법무부 장관 후보는 왜 물러났을까요 지금?", META) == ""            # names nothing from this story
    assert script_llm._valid_hook("김승원 사퇴?", META) == ""                                         # too short
    assert script_llm._valid_hook("김승원 후보자가 어제 자진 사퇴한 이유를 아십니까 여러분", META) == ""   # no question mark


def test_writer_prompt_asks_for_a_hook_and_one_speaker_per_sentence():
    assert "hook" in script_llm._SYSTEM and "열린 질문" in script_llm._SYSTEM
    assert "한 문장에는 한 주체의 말만" in script_llm._SYSTEM
    assert "진보 성향 매체 보도에 따르면" in script_llm._SYSTEM
    assert "sides" in script_llm._ANALYSIS_SYSTEM and "claim" in script_llm._ANALYSIS_SYSTEM
    assert "주체를 절대 섞지 말 것" in script_llm._ANALYSIS_SYSTEM


def test_quality_agent_rubric_checks_attribution_cause_balance_and_hook():
    s = quality_agent._SYSTEM
    for needle in ("[귀속]", "[원인]", "[균형]", "[훅]", "비판받는 쪽의 이유"):
        assert needle in s


# ------------------------------------------------------------------ one-sided sources
def test_lean_of_reads_the_publisher_domain():
    assert research.lean_of("https://www.hani.co.kr/arti/politics/1.html") == "left"
    assert research.lean_of("https://news.chosun.com/x") == "right"
    assert research.lean_of("https://www.donga.com/news/x") == "right"
    assert research.lean_of("https://www.yna.co.kr/view/1") == ""
    assert research.lean_of("") == ""


def test_one_sided_material_triggers_a_search_of_the_other_camp(monkeypatch):
    queried = []

    def fake_gnews(q, limit=8):
        queried.append(q)
        if "site:" in q:
            dom = q.split("site:")[1]
            return [{"title": f"영화제 축소 논란 보도 {dom[:2]}", "source": dom, "link": f"https://{dom}/a"}]
        return [{"title": "진보 기사", "source": "한겨레", "link": "https://www.hani.co.kr/a"}]

    monkeypatch.setattr(research, "gnews", fake_gnews)
    monkeypatch.setattr(research, "bing_news", lambda q, **k: [])
    monkeypatch.setattr(research, "resolve_link", lambda link: link)
    monkeypatch.setattr(research, "article_text", lambda url, **k: "본문입니다. " * 40)
    seen = {}

    def fake_complete(prompt, cfg, max_tokens=400, system=""):
        seen["prompt"] = prompt
        return json.dumps({"background": "배경입니다 " * 5, "positions": [
            {"who": "경기도", "position": "재정 때문에 축소", "why": "예산", "lean": "보수", "source": "조선일보"}]},
            ensure_ascii=False)

    monkeypatch.setattr(L, "complete", fake_complete)
    plan = {"event": "영화제 축소", "question": "왜 축소했나?", "queries": ["DMZ 영화제 축소 이유"]}
    notes = research.web_notes("영화제 축소", "김민석", settings, plan=plan, seed_leans=["left"])
    assert any("site:chosun.com" in q for q in queried)                  # went looking on the conservative side
    assert not any("site:hani.co.kr" in q for q in queried)              # the progressive side was already covered
    assert "(보수 성향 매체)" in seen["prompt"] and "(진보 성향 매체)" in seen["prompt"]
    assert notes["positions"][0]["lean"] == "보수"


def test_balanced_material_does_not_trigger_extra_searches(monkeypatch):
    queried = []

    def fake_gnews(q, limit=8):
        queried.append(q)
        return [{"title": "진보", "source": "한겨레", "link": "https://www.hani.co.kr/a"},
                {"title": "보수", "source": "조선", "link": "https://www.chosun.com/a"}]

    monkeypatch.setattr(research, "gnews", fake_gnews)
    monkeypatch.setattr(research, "bing_news", lambda q, **k: [])
    monkeypatch.setattr(research, "resolve_link", lambda link: link)
    monkeypatch.setattr(research, "article_text", lambda url, **k: "본문입니다. " * 40)
    monkeypatch.setattr(L, "complete", lambda *a, **k: json.dumps({"background": "배경입니다 " * 5}, ensure_ascii=False))
    research.web_notes("주제", "", settings, plan={"queries": ["주제 이유"]})
    assert not any("site:" in q for q in queried)


def test_pack_block_shows_which_camp_each_position_came_from():
    pack = {"web": {"positions": [{"who": "경기도", "position": "축소 불가피", "why": "재정", "lean": "보수", "source": "조선"},
                                   {"who": "김민석 대표", "position": "계약 파기", "why": "", "lean": "진보", "source": "한겨레"}]}}
    block = research.pack_block(pack)
    assert "경기도 [보수 성향 매체]" in block and "김민석 대표 [진보 성향 매체]" in block


# ----------------------------------------------------------------------- waste fixes
def test_stage_one_analysis_is_computed_once_per_story_not_once_per_attempt(monkeypatch):
    calls = []
    payload = json.dumps({"what_happened": "사퇴했다", "confirmed_fact": "사실입니다."}, ensure_ascii=False)
    monkeypatch.setattr(L, "complete", lambda *a, **k: calls.append(1) or payload)
    monkeypatch.setattr(script_llm, "_ANALYSIS_CACHE", {})
    cfg = dataclasses.replace(settings, llm_provider="groq")
    meta = {"source_text": "기사 본문", "facts": ["사실1"], "claims": [], "interps": [], "entities": {}}
    a = script_llm.analyze_story(meta, cfg)
    b = script_llm.analyze_story(meta, cfg)
    c = script_llm.analyze_story(dict(meta, facts=["다른 사실"]), cfg)      # a different story is NOT served from cache
    assert a == b and a["what_happened"] == "사퇴했다"
    assert len(calls) == 2


def test_retry_after_understands_gemini_style_wording():
    assert abs(L._retry_after("Please retry in 26.5s.") - 26.5) < 1e-6


def test_gemini_model_that_404s_is_not_asked_again(monkeypatch):
    posted = []

    class _R:
        def __init__(self, status):
            self.status_code = status

        def json(self):
            if self.status_code == 404:
                return {"error": {"status": "NOT_FOUND", "message": "model not found"}}
            return {"candidates": [{"content": {"parts": [{"text": '{"ok":1}'}]}}]}

    def fake_post(url, params=None, json=None, timeout=None):
        posted.append(url.split("/models/")[1].split(":")[0])
        return _R(404 if "flash-latest" in url and "lite" not in url else 200)

    monkeypatch.setattr(L, "requests", type("m", (), {"post": staticmethod(fake_post)}))
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    monkeypatch.setattr(L, "_COOLDOWN", {})
    cfg = dataclasses.replace(settings, llm_provider="gemini", gemini_api_key="k", llm_model="")
    assert L._gemini("hi", cfg, 100, "") == '{"ok":1}'
    first = list(posted)
    posted.clear()
    assert L._gemini("hi", cfg, 100, "") == '{"ok":1}'
    assert "gemini-flash-latest" in first and "gemini-flash-latest" not in posted


# ---------------------------------------------- other side: relevance, title-only, honest "not found"
def _gap_env(monkeypatch, site_title, article_body):
    def fake_gnews(q, limit=8):
        if "site:" in q:
            dom = q.split("site:")[1]
            return [{"title": site_title, "source": dom, "link": f"https://{dom}/a"}]
        return [{"title": "진보 기사", "source": "한겨레", "link": "https://www.hani.co.kr/a"}]

    monkeypatch.setattr(research, "gnews", fake_gnews)
    monkeypatch.setattr(research, "bing_news", lambda q, **k: [])
    monkeypatch.setattr(research, "resolve_link", lambda link: link)
    monkeypatch.setattr(research, "article_text", lambda url, **k: article_body if "hani.co.kr" not in url else "본문입니다. " * 40)
    seen = {}

    def fake_complete(prompt, cfg, max_tokens=400, system=""):
        seen["prompt"] = prompt
        return json.dumps({"background": "배경입니다 " * 5}, ensure_ascii=False)

    monkeypatch.setattr(L, "complete", fake_complete)
    return seen


PLAN = {"event": "영화제 축소", "question": "왜 축소했나?", "queries": ["DMZ 영화제 축소 이유"]}


def test_other_side_results_that_are_off_topic_are_ignored_and_reported_as_missing(monkeypatch):
    seen = _gap_env(monkeypatch, "붕어빵 지역축제 눈먼 돈 4편", "본문입니다. " * 40)
    notes = research.web_notes("영화제 축소", "김민석", settings, plan=PLAN, seed_leans=["left"])
    assert "붕어빵" not in seen["prompt"]                      # an unrelated site: hit never reaches the writer
    assert notes["missing_leans"] == ["right"]                  # ... and the missing camp is reported honestly


def test_other_side_headline_is_kept_as_title_only_when_the_body_cannot_be_read(monkeypatch):
    seen = _gap_env(monkeypatch, "경기도 영화제 축소 불가피 재정 어려움", "")      # outlet blocks scraping
    notes = research.web_notes("영화제 축소", "김민석", settings, plan=PLAN, seed_leans=["left"])
    assert "보수 성향 매체, 제목만" in seen["prompt"] and "경기도 영화제 축소 불가피" in seen["prompt"]
    assert "추측하지 말 것" in seen["prompt"]
    assert notes["missing_leans"] == []                          # that camp's stance is known from its headline


def test_pack_block_tells_the_writer_which_camp_had_no_coverage():
    block = research.pack_block({"web": {"background": "배경입니다 " * 4, "missing_leans": ["right"]}})
    assert "성향 균형 안내" in block and "보수 성향" in block and "지어내지 말 것" in block
    assert "성향 균형 안내" not in research.pack_block({"web": {"background": "배경입니다 " * 4, "missing_leans": []}})


def test_sides_card_states_honestly_that_a_camp_had_no_coverage(monkeypatch):
    story = {"what_happened": "축소했다", "confirmed_fact": "사실입니다.",
             "sides": [{"who": "김민석 대표", "claim": "김민석 대표는 축소를 계약 파기라고 비판했습니다.", "reason": "", "lean": "진보"}]}
    monkeypatch.setattr(script_llm, "analyze_story", lambda meta, cfg: story)
    monkeypatch.setattr(L, "complete", lambda *a, **k: json.dumps({
        "hook": "경기도는 왜 영화제 예산을 줄였을까요?", "summary": "경기도가 영화제 운영을 줄였습니다.",
        "sides": "자유롭게 쓴 문장입니다.", "outro": "재정 배분을 둘러싼 공방이 이어지고 있습니다."}, ensure_ascii=False))
    segs = [{"role": "hook", "narration": "경기도를 두고 정치권이 정면으로 부딪히고 있습니다."},
            {"role": "summary", "narration": "배경입니다 배경입니다."},
            {"role": "sides", "narration": "입장이 갈립니다."},
            {"role": "outro", "narration": "마무리입니다 마무리입니다."}]
    meta = {"headline": "경기도 영화제 축소", "topic": "경기도", "source_text": "기사", "facts": [], "claims": [],
            "interps": [], "entities": {}, "missing_leans": ["right"]}
    cfg = dataclasses.replace(settings, llm_provider="groq")
    out, _title = script_llm.rewrite_segments(segs, meta, cfg, {})
    sides = next(s for s in out if s["role"] == "sides")["narration"]
    hook = next(s for s in out if s["role"] == "hook")["narration"]
    assert sides.startswith("김민석 대표는 축소를 계약 파기라고 비판했습니다.")     # composed from stage 1, not free text
    assert "자유롭게 쓴" not in sides
    assert sides.endswith("보수 성향 매체 보도에서는 이 사안에 대한 별도 입장을 확인하지 못했습니다.")
    assert hook == "경기도는 왜 영화제 예산을 줄였을까요?"                        # the vetted, concrete hook replaced the vague one
