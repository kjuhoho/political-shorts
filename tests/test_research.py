"""Research stage: extra news + web notes + YouTube, all best-effort, all cached,
and never allowed to break the pipeline."""
import dataclasses
import json

from political_shorts import research
from political_shorts.config import settings

REAL_BUILD_PACK = research.build_pack        # conftest stubs the module attribute per test
REAL_YOUTUBE = research.youtube


class _Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code = status
        self._body = body
        self.content = content

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_build_query_strips_noise_and_adds_actor():
    q = research.build_query("[속보] 이재명 대통령, '이란 파병' 검토…野 반발(종합)", "이재명")
    assert "[속보]" not in q and "(종합)" not in q and "'" not in q
    assert "이란 파병" in q
    assert research.build_query("국회 예산안 통과", "국회") == "국회 예산안 통과"
    assert research.build_query("예산안 통과", "국회").startswith("국회 ")


def test_gnews_parses_title_and_source(monkeypatch):
    rss = ("<?xml version='1.0'?><rss version='2.0'><channel>"
           "<item><title>이란 파병 논란 확산 - 연합뉴스</title><link>https://x/1</link></item>"
           "<item><title>野 &quot;파병 반대&quot; - 한겨레</title><link>https://x/2</link></item>"
           "</channel></rss>").encode("utf-8")
    monkeypatch.setattr(research.requests, "get", lambda *a, **k: _Resp(content=rss))
    out = research.gnews("이란 파병")
    assert out[0] == {"title": "이란 파병 논란 확산", "source": "연합뉴스", "link": "https://x/1"}
    assert out[1]["source"] == "한겨레"


def test_gnews_failure_is_empty_not_an_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(research.requests, "get", boom)
    assert research.gnews("이란 파병") == []


def test_web_notes_parses_json_and_falls_back_to_prose(monkeypatch):
    import political_shorts.llm as L
    payload = json.dumps({"background": "배경입니다", "pros": ["장점"], "cons": ["단점"]}, ensure_ascii=False)
    monkeypatch.setattr(L, "web_search", lambda *a, **k: "```json\n" + payload + "\n```")
    assert research.web_notes("헤드라인", "인물", settings)["cons"] == ["단점"]
    monkeypatch.setattr(L, "web_search", lambda *a, **k: "그냥 산문으로 답했습니다")
    assert research.web_notes("헤드라인", "인물", settings)["background"].startswith("그냥 산문")
    monkeypatch.setattr(L, "web_search", lambda *a, **k: "")
    assert research.web_notes("헤드라인", "인물", settings) == {}


def test_youtube_needs_key_and_keeps_no_authors(monkeypatch):
    assert REAL_YOUTUBE("이란 파병", dataclasses.replace(settings, youtube_api_key="")) == {}

    def fake_get(url, params=None, timeout=None, **k):
        if url.endswith("/search"):
            return _Resp(body={"items": [{"id": {"videoId": "v1"},
                                          "snippet": {"title": "파병 논란 정리", "channelTitle": "뉴스채널"}}]})
        return _Resp(body={"items": [{"snippet": {"topLevelComment": {"snippet": {
            "textDisplay": "이건 정말 신중하게 결정해야 할 문제입니다", "authorDisplayName": "홍길동"}}}}]})

    monkeypatch.setattr(research.requests, "get", fake_get)
    out = REAL_YOUTUBE("이란 파병", dataclasses.replace(settings, youtube_api_key="k"))
    assert out["videos"] == [{"title": "파병 논란 정리", "channel": "뉴스채널"}]
    assert out["comments"] == ["이건 정말 신중하게 결정해야 할 문제입니다"]
    assert "홍길동" not in json.dumps(out, ensure_ascii=False)


def test_build_pack_caches_and_skips_when_disabled(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(research, "gnews", lambda q, **k: calls.append(q) or [{"title": "t", "source": "s", "link": ""}])
    monkeypatch.setattr(research, "web_notes", lambda *a, **k: {"background": "배경"})
    monkeypatch.setattr(research, "youtube", lambda *a, **k: {})
    cfg = dataclasses.replace(settings, data_dir=tmp_path, research_enabled=True)
    p1 = REAL_BUILD_PACK("이란 파병 논란", "이재명", cfg)
    p2 = REAL_BUILD_PACK("이란 파병 논란", "이재명", cfg)
    assert p1 == p2 and len(calls) == 1               # second call served from cache
    assert REAL_BUILD_PACK("이란 파병 논란", "이재명",
                           dataclasses.replace(cfg, research_enabled=False)) == {}


def test_build_pack_empty_when_nothing_found(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "gnews", lambda *a, **k: [])
    monkeypatch.setattr(research, "web_notes", lambda *a, **k: {})
    monkeypatch.setattr(research, "youtube", lambda *a, **k: {})
    cfg = dataclasses.replace(settings, data_dir=tmp_path, research_enabled=True)
    assert REAL_BUILD_PACK("아무도 모르는 주제", "", cfg) == {}


def test_pack_block_labels_reactions_as_unverified_and_is_bounded():
    pack = {"news": [{"title": "추가 보도", "source": "매체"}],
            "web": {"background": "배경", "pros": ["장점1"], "cons": ["단점1"],
                    "statements": [{"who": "대통령", "when": "3일", "text": "끝까지 한 말", "source": "청와대"}],
                    "positions": [{"who": "국민의힘", "position": "반대", "why": "이유", "source": "논평"}]},
            "youtube": {"comments": ["댓글입니다 " * 5], "videos": [{"title": "영상", "channel": "채널"}]}}
    block = research.pack_block(pack)
    assert "검증되지 않은 반응" in block and "끝까지 한 말" in block
    assert "장점" in block and "단점" in block and "국민의힘" in block
    assert len(block) <= research._BLOCK_CHARS + 2
    assert research.pack_block({}) == ""


def test_bing_news_extracts_direct_urls(monkeypatch):
    rss = ("<?xml version='1.0'?><rss version='2.0'><channel>"
           "<item><title>전쟁 파병 없다</title>"
           "<link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;"
           "url=https%3a%2f%2fnews.example.com%2fa%2f1&amp;c=1</link></item>"
           "<item><title>링크 없는 항목</title><link>http://www.bing.com/x</link></item>"
           "</channel></rss>").encode("utf-8")
    monkeypatch.setattr(research.requests, "get", lambda *a, **k: _Resp(content=rss))
    out = research.bing_news("이란 파병")
    assert len(out) == 1
    assert out[0]["link"] == "https://news.example.com/a/1" and out[0]["title"] == "전쟁 파병 없다"
    assert out[0]["source"] == "news.example.com"


def test_article_text_reads_paragraphs_and_skips_chrome(monkeypatch):
    body = ("<html><nav><p>메뉴 " + "가" * 40 + "</p></nav><article>"
            "<p>이재명 대통령은 3일 기자회견에서 전쟁에 파병하지 않겠다고 밝혔습니다. 청해부대 강화는 검토한다고 했습니다. "
            "정부는 호르무즈 해협 인근 우리 선박의 안전을 위해 필요한 조치를 하겠다고 설명했습니다. "
            "다만 구체적인 파견 규모와 시기, 국회 동의 절차에 대해서는 아직 정해진 바가 없다고 덧붙였습니다.</p>"
            "<p>짧다</p>"
            "<p>야당은 정부의 설명이 충분하지 않다며 국회 차원의 설명을 요구했고, 시민단체는 약속을 지켜보겠다고 했습니다.</p>"
            "</article><script>var x=1</script></html>")

    class R(_Resp):
        encoding = "utf-8"
        text = body

    monkeypatch.setattr(research.requests, "get", lambda *a, **k: R(status=200))
    txt = research.article_text("https://news.example.com/a/1")
    assert "파병하지 않겠다고 밝혔습니다" in txt and "야당은" in txt
    assert "메뉴" not in txt and "짧다" not in txt and "var x" not in txt
    monkeypatch.setattr(research.requests, "get", lambda *a, **k: R(status=404))
    assert research.article_text("https://news.example.com/a/1") == ""


def test_web_notes_extracts_from_article_bodies(monkeypatch):
    import political_shorts.llm as L
    items = [{"title": "기사1", "source": "매체A", "link": "https://a/1"},
             {"title": "기사2", "source": "매체B", "link": "https://b/2"}]
    monkeypatch.setattr(research, "gnews", lambda *a, **k: [])
    monkeypatch.setattr(research, "bing_news", lambda q, **k: items)
    monkeypatch.setattr(research, "article_text", lambda url, **k: "본문입니다. " * 40)
    seen = {}

    def fake_complete(prompt, cfg, max_tokens=400, system=""):
        seen["prompt"] = prompt
        return json.dumps({"background": "이 일은 지난달 회담에서 시작됐습니다. " * 2,
                           "statements": [{"who": "대통령", "text": "전쟁에 파병하지 않겠다", "source": "매체A"}],
                           "pros": ["외교 부담 감소"], "cons": ["동맹 압박"]}, ensure_ascii=False)

    monkeypatch.setattr(L, "complete", fake_complete)
    out = research.web_notes("이란 파병 논란", "이재명", settings)
    assert out["statements"][0]["text"] == "전쟁에 파병하지 않겠다"
    assert [s["url"] for s in out["sources"]] == ["https://a/1", "https://b/2"]
    assert "[기사1 — 매체A]" in seen["prompt"]


def test_web_notes_falls_back_to_web_search_when_no_body_can_be_read(monkeypatch):
    import political_shorts.llm as L
    monkeypatch.setattr(research, "gnews", lambda *a, **k: [])
    monkeypatch.setattr(research, "bing_news", lambda q, **k: [{"title": "t", "source": "s", "link": "https://a"}])
    monkeypatch.setattr(research, "article_text", lambda url, **k: "")
    monkeypatch.setattr(L, "web_search",
                        lambda *a, **k: json.dumps({"background": "웹 검색으로 찾은 배경 설명입니다. " * 2}, ensure_ascii=False))
    assert "웹 검색으로 찾은 배경" in research.web_notes("주제", "", settings)["background"]


def test_resolve_link_passes_direct_urls_through_and_decodes_google_links(monkeypatch):
    assert research.resolve_link("https://news.example.com/a/1") == "https://news.example.com/a/1"
    assert research.resolve_link("https://news.google.com/rss/search?q=x") == ""      # not an article link

    page = '<c-wiz data-n-a-sg="SIG" data-n-a-ts="1700000000"></c-wiz>'
    inner = json.dumps([None, "https://publisher.example.com/real-article"])
    reply = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner]])

    class Page(_Resp):
        text = page

    class Reply(_Resp):
        text = reply

    monkeypatch.setattr(research.requests, "get", lambda *a, **k: Page())
    monkeypatch.setattr(research.requests, "post", lambda *a, **k: Reply())
    assert research.resolve_link("https://news.google.com/rss/articles/CBMiabc?oc=5") \
        == "https://publisher.example.com/real-article"

    def boom(*a, **k):
        raise RuntimeError("blocked")
    monkeypatch.setattr(research.requests, "post", boom)
    assert research.resolve_link("https://news.google.com/rss/articles/CBMiabc?oc=5") == ""


# ---- cause-focused research (plan -> search -> read -> answer "why") -------------
def test_plan_research_targets_the_event_not_the_reaction(monkeypatch):
    import political_shorts.llm as L
    seen = {}

    def fake_complete(prompt, cfg, max_tokens=400, system=""):
        seen["prompt"] = prompt
        return json.dumps({"event": "김승원 후보자가 법무부 장관 후보직에서 사퇴",
                           "question": "김승원 후보자는 왜 사퇴했나?",
                           "queries": ["김승원 후보자 사퇴 이유", "김승원 후보자 의혹 청문회 논란", ""]},
                          ensure_ascii=False)

    monkeypatch.setattr(L, "complete", fake_complete)
    plan = research.plan_research("청와대, 김승원 사퇴에 결정 존중", "김승원", "김승원 후보자가 사퇴했다", settings)
    assert plan["question"] == "김승원 후보자는 왜 사퇴했나?"
    assert plan["queries"] == ["김승원 후보자 사퇴 이유", "김승원 후보자 의혹 청문회 논란"]
    assert "청와대, 김승원 사퇴에 결정 존중" in seen["prompt"]


def test_plan_research_falls_back_to_the_headline_query(monkeypatch):
    import political_shorts.llm as L

    def boom(*a, **k):
        raise RuntimeError("no llm")

    monkeypatch.setattr(L, "complete", boom)
    plan = research.plan_research("이재명 대통령 이란 파병", "이재명", "", settings)
    assert plan["queries"] == [research.build_query("이재명 대통령 이란 파병", "이재명")]
    monkeypatch.setattr(L, "complete", lambda *a, **k: "not json")
    assert research.plan_research("헤드라인", "", "", settings)["queries"]


def test_gather_merges_queries_and_drops_repeated_titles(monkeypatch):
    calls = []

    def fake_gnews(q, limit=8):
        calls.append(q)
        return [{"title": "모든 검색에 나오는 공통 기사", "source": "s", "link": "https://a/" + q},
                {"title": f"{q} 전용 기사", "source": "s", "link": "https://b/" + q}]

    monkeypatch.setattr(research, "gnews", fake_gnews)
    monkeypatch.setattr(research, "bing_news", lambda q, **k: [])
    out = research._gather(["사퇴 이유", "사퇴 배경"])
    assert calls == ["사퇴 이유", "사퇴 배경"]
    assert [i["title"] for i in out] == ["모든 검색에 나오는 공통 기사", "사퇴 이유 전용 기사", "사퇴 배경 전용 기사"]
    assert len({i["title"] for i in out}) == len(out)


def test_extraction_asks_for_the_cause_and_keeps_reactions_out_of_it(monkeypatch):
    import political_shorts.llm as L
    monkeypatch.setattr(research, "gnews", lambda q, **k: [{"title": "기사", "source": "매체", "link": "https://a/1"}])
    monkeypatch.setattr(research, "bing_news", lambda q, **k: [])
    monkeypatch.setattr(research, "article_text", lambda url, **k: "본문입니다. " * 40)
    seen = {}

    def fake_complete(prompt, cfg, max_tokens=400, system=""):
        seen["system"], seen["prompt"] = system, prompt
        return json.dumps({"why": [{"reason": "청문회에서 자녀 관련 의혹이 불거졌다",
                                    "evidence": "9월 15일 법사위 청문회", "source": "매체"}],
                           "reactions": [{"where": "청와대", "summary": "결정을 존중한다"}]},
                          ensure_ascii=False)

    monkeypatch.setattr(L, "complete", fake_complete)
    plan = {"event": "김승원 사퇴", "question": "김승원 후보자는 왜 사퇴했나?", "queries": ["김승원 사퇴 이유"]}
    notes = research.web_notes("청와대, 김승원 사퇴에 결정 존중", "김승원", settings, plan=plan)
    assert notes["why"][0]["reason"].startswith("청문회에서")
    assert "[핵심 질문] 김승원 후보자는 왜 사퇴했나?" in seen["prompt"]
    assert "반응·논평·입장 표명" in seen["system"] and "원인이 아니므로" in seen["system"]


def test_pack_block_leads_with_the_question_and_causes():
    pack = {"plan": {"event": "김승원 사퇴", "question": "김승원 후보자는 왜 사퇴했나?"},
            "web": {"why": [{"reason": "청문회 의혹", "evidence": "9월 15일", "source": "매체"}],
                    "reactions": [{"where": "청와대", "summary": "결정을 존중한다"}]}}
    block = research.pack_block(pack)
    assert block.index("핵심 질문") < block.index("원인·발단") < block.index("여론·온라인 반응")
    assert "청문회 의혹" in block and "반응·논평은 원인이 아님" in block


def test_recent_duplicate_catches_a_reaction_story_about_the_same_person(monkeypatch):
    import time

    from political_shorts import topics

    prev_sig = topics.story_signature("김승원 후보자 자진 사퇴", {"politicians": ["김승원"]}, "personnel")

    class Row(dict):
        pass

    row = Row(signature=topics.signature_str(prev_sig), published_ts=int(time.time()) - 3600,
              headline="김승원 후보자 자진 사퇴", actor="김승원")
    monkeypatch.setattr(topics, "recent_topics", lambda conn, since: [row])
    cur = topics.story_signature("청와대, 김승원 사퇴에 결정 존중", {"politicians": ["김승원"]}, "personnel")
    assert topics._overlap(cur, prev_sig) < 0.6                     # wording moved on ...
    dup, why = topics.recent_duplicate(None, cur, settings, actor="청와대", people={"김승원"})
    assert dup and "김승원" in why                                    # ... same person still caught
    dup, _ = topics.recent_duplicate(None, cur, settings, actor="청와대", people=set())
    assert not dup                                                   # (old behaviour: missed)
    dup, _ = topics.recent_duplicate(None, cur, settings, actor="청와대", people={"이재명"})
    assert not dup                                                   # broad actors never trigger it
