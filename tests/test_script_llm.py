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


def test_no_provider_is_a_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "complete", lambda *a, **k: calls.append(1) or "{}")
    segs = _segs()
    out = script_llm.rewrite_segments(segs, META, settings, BASE)   # provider ""
    assert out == segs and calls == []


def test_valid_rewrite_is_applied(monkeypatch):
    payload = json.dumps({
        "hook": "국회가 내년 나라 살림 계획을 확정했습니다.",
        "what": "여야가 막판까지 맞섰지만 결국 합의해 예산안을 통과시켰습니다.",
        "outro": "쟁점과 원문은 더보기란에 정리해 뒀습니다.",
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = script_llm.rewrite_segments(_segs(), META, _cfg(), BASE)
    assert out[0]["narration"].startswith("국회가 내년")
    assert "합의해 예산안을 통과" in out[1]["narration"]
    assert out[2]["narration"].startswith("쟁점과 원문")


def test_narration_and_caption_object_form(monkeypatch):
    payload = json.dumps({
        "hook": {"narration": "국회가 내년 나라 살림 계획을 확정했습니다. 무엇이 달라질까요.",
                 "caption": "내년 예산안, 국회 통과"},
        "what": {"narration": "여야가 막판까지 맞섰지만 결국 합의해 통과시켰습니다.",
                 "caption": "여야 합의로 처리"},
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = script_llm.rewrite_segments(_segs(), META, _cfg(), BASE)
    assert out[0]["caption"] == "내년 예산안, 국회 통과" and out[0]["llm_caption"] is True
    assert out[1]["narration"].startswith("여야가 막판까지")


def test_json_fence_is_stripped(monkeypatch):
    payload = "```json\n" + json.dumps({"hook": "쉽게 풀어 설명하면 이렇습니다."}) + "\n```"
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = script_llm.rewrite_segments(_segs(), META, _cfg(), BASE)
    assert out[0]["narration"] == "쉽게 풀어 설명하면 이렇습니다."


def test_garbage_response_keeps_template(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "sorry, I cannot help")
    segs = _segs()
    assert script_llm.rewrite_segments(segs, META, _cfg(), BASE) == segs


def test_oversized_card_is_skipped_others_applied(monkeypatch):
    payload = json.dumps({
        "hook": "짧고 자연스러운 새 훅 문장입니다.",
        "what": "과도하게 긴 문장 " * 40,          # way over the cap -> skip this one
    })
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    out = script_llm.rewrite_segments(_segs(), META, _cfg(), BASE)
    assert out[0]["narration"] == "짧고 자연스러운 새 훅 문장입니다."
    assert out[1]["narration"] == "여야가 합의해 통과시켰습니다."   # unchanged


def test_rewrite_that_adds_a_safety_block_is_rejected(monkeypatch):
    # inject a slur from safety.HARMFUL -> the rewrite gains a block the
    # templated version didn't have, so it must be discarded
    payload = json.dumps({"hook": "상대를 빨갱이라고 부르며 표결이 시작됐습니다."})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: payload)
    segs = _segs()
    assert script_llm.rewrite_segments(segs, META, _cfg(), BASE) == segs


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
