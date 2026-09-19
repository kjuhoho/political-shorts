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
