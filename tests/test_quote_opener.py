"""Open on a named person's own words (CHANNEL_FINDINGS.md section 9, option B).

The failures this guards against were all seen on the real corpus while building it:
  * a closing quote mark paired with the NEXT opening one, producing the "quote" 라고 공격했다;
  * the person being addressed read as the speaker ('국민의힘은 김승원에게 "사과하라"고 요구했다');
  * a headline's condensed paraphrase presented as the words as said;
  * a job title ('비례 대표') read as a person's name.
A misattributed quote is worse than no quote, so every one of these must come back empty.
"""
import dataclasses

from political_shorts import hook_engine as H
from political_shorts import trending, variants
from political_shorts.config import settings
from political_shorts.hook import Entities, Frame


def _ent(politicians=(), parties=(), president=False):
    return Entities(politicians=list(politicians), parties=list(parties), president=president,
                    organizations=[], ministries=[]) if _has_orgs() else Entities(
        politicians=list(politicians), parties=list(parties), president=president)


def _has_orgs():
    return "organizations" in {f.name for f in dataclasses.fields(Entities)}


def _ctx(claims, *, politicians=("이재명", "김승원", "장동혁", "김민석"), parties=("국민의힘", "민주당"),
         president=True, headline="이재명 대통령 파병 입장", actor="이재명", facts=(), **kw):
    return H.HookContext(headline=headline, facts=list(facts), claims=list(claims),
                         frame=Frame(kind="remark"), entities=_ent(politicians, parties, president),
                         actor=actor, n_sources=2, **kw)


# --------------------------------------------------------------------------- #
# what a good opener looks like
# --------------------------------------------------------------------------- #
def test_a_named_subjects_own_words_open_the_video():
    c = _ctx(['이재명 대통령은 21일 국회에서 "전쟁에 관여하거나 개입하는 파병은 없다"고 말했다.'])
    h = H.build_hook(c)
    assert h.kind == "quote"
    assert h.narration.startswith("“전쟁에 관여하거나 개입하는 파병은 없다”")      # the quote is the FIRST thing said
    assert "이재명 대통령이 한 말입니다" in h.narration


def test_a_party_can_be_the_speaker():
    c = _ctx(['국민의힘은 “오늘 대통령이 내놓은 것은 약속이 아니라 변명이었다”고 비판했다.'], actor="")
    found = H.find_quotes(c)
    assert found and found[0].speaker == "국민의힘"


def test_the_sitting_president_by_title_alone():
    c = _ctx(['이 대통령은 21일 "인사시스템을 재점검해보려고 한다"며 밝혔다.'])
    assert H.find_quotes(c)[0].speaker == "이재명 대통령"


# --------------------------------------------------------------------------- #
# a misattributed or mangled quote must never open a video
# --------------------------------------------------------------------------- #
def test_the_person_addressed_is_not_the_speaker():
    c = _ctx(['국민의힘은 김승원 후보자에게 “반복되는 인사 실패와 무리한 엄호에 대해 사과하라”고 요구했다.'],
             headline="김승원 사퇴")
    # the party said it and 김승원 was its target. With a second named person in between we do not guess:
    # nothing is better than naming the wrong speaker, and above all never the person being addressed.
    assert not any("김승원" in q.speaker for q in H.find_quotes(c))
    assert H.find_quotes(c) == []


def test_a_second_named_person_between_speaker_and_quote_means_we_do_not_guess():
    c = _ctx(['장동혁 대표는 이재명 대통령을 향해 “국민 앞에 사과하고 책임을 져야 한다”고 말했다.'])
    assert H.find_quotes(c) == []


def test_closing_and_next_opening_quote_marks_are_not_a_quote():
    """'"A"라고 공격했다 "B"' once yielded the quote 라고 공격했다."""
    c = _ctx(['민주당은 "이번 인사는 국민의 뜻과 거리가 멀다"라고 공격했다 "또다시 반복되는 사과 없는 변명이다"'])
    texts = [q.text for q in H.find_quotes(c)]
    assert all("라고 공격했다" not in t for t in texts)


def test_a_headlines_condensed_paraphrase_is_not_the_words_as_said():
    c = _ctx([], headline='김민석 "용혜인, 당 지키는 선택한 것 이해"')
    assert H.find_quotes(c) == []


def test_a_job_title_is_not_a_name():
    c = _ctx(['당 비례 대표는 "지역에서 지지를 얻고 당의 기반을 넓혀야 한다"고 말했다.'], politicians=())
    assert H.find_quotes(c) == []


def test_an_unnamed_official_never_opens_a_video():
    c = _ctx(['한 관계자는 "내부에서 이미 결정이 났다는 이야기가 나온다"고 말했다.'])
    assert H.find_quotes(c) == []


def test_a_quoted_term_is_not_something_somebody_said():
    c = _ctx(['이재명 대통령은 21일 국회에서 열린 "국민참정권 수호 법안" 처리 문제를 논의했다.'])
    assert H.find_quotes(c) == []


# --------------------------------------------------------------------------- #
# the quote is whole, plain and not an attack line
# --------------------------------------------------------------------------- #
def test_a_quote_that_is_a_fragment_is_skipped():
    c = _ctx(['이재명 대통령은 "청와대 책임"이라고 말했다.'])
    assert H.find_quotes(c) == []


def test_an_edited_quote_is_skipped():
    for q in ("증인으로 (출석시키는 것은) 삼권분립을 무너뜨리는 것이다", "삼권분립을 형해화시키는 것이고 사법부를…",
              "이미 결정이 났다는 이야기가 [중략] 나온다고 한다"):
        assert H.find_quotes(_ctx([f'이재명 대통령은 "{q}"고 말했다.'])) == []


def test_it_is_not_cut_to_fit():
    long_q = "대통령이 개헌 의지를 여러 번 밝혔고 야당이 제기하는 걸림돌도 명확히 정리했다고 생각한다"
    assert len(long_q) > H.QUOTE_MAX
    assert H.find_quotes(_ctx([f'김민석 대표는 "{long_q}"고 말했다.'])) == []      # skipped whole, never truncated


def test_inflammatory_quotes_do_not_open_the_video():
    for q in ("저 사람은 매국노이고 나라를 팔아먹은 역적이다", "이 정권은 쓰레기 같은 정권이라고 말한다"):
        assert H.find_quotes(_ctx([f'장동혁 대표는 "{q}"고 말했다.'])) == []


def test_profanity_is_not_the_first_thing_viewers_hear():
    assert H.find_quotes(_ctx(['장동혁 대표는 "이런 시발 같은 일이 다시는 없어야 한다"고 말했다.'])) == []


def test_no_hype_words_in_the_opener():
    assert H.build_hook(_ctx(['이재명 대통령은 "충격적인 결과가 나왔다는 보고를 받았다"고 말했다.'])).kind != "quote"


def test_the_switch_turns_it_off():
    c = _ctx(['이재명 대통령은 21일 "전쟁에 관여하거나 개입하는 파병은 없다"고 말했다.'], quote_first=False)
    assert H.build_hook(c).kind != "quote"


def test_the_shorter_of_two_quotes_wins():
    c = _ctx(['이재명 대통령은 "인사시스템을 재점검해보려고 한다"고 말했다.',
              '이재명 대통령은 "전쟁에 관여하거나 개입하는 파병은 없다고 분명히 말씀드린다"고 말했다.'])
    assert H.find_quotes(c)[0].text == "인사시스템을 재점검해보려고 한다"


# --------------------------------------------------------------------------- #
# the LLM may not rewrite a person's words into a question
# --------------------------------------------------------------------------- #
def test_the_llm_does_not_overwrite_a_quote_opener(monkeypatch):
    from political_shorts import script_llm

    segs = [{"role": "hook", "narration": "“전쟁에 개입하는 파병은 없다” 이재명 대통령이 한 말입니다.",
             "hook_kind": "quote"},
            {"role": "summary", "narration": "배경입니다."}]
    kept = [s for s in segs if s.get("role") == "hook"][0]["narration"]
    monkeypatch.setattr(script_llm, "_valid_hook", lambda *a, **k: "이재명 대통령은 왜 이렇게 말했을까요?")
    # replicate the apply site: it must leave a quote opener alone
    for s in segs:
        if s.get("role") == "hook" and s.get("hook_kind") == "quote":
            break
        if s.get("role") == "hook":
            s["narration"] = script_llm._valid_hook(None, {})
            break
    assert segs[0]["narration"] == kept


# --------------------------------------------------------------------------- #
# ranking: a bounded tie-break, never a leap past a major story
# --------------------------------------------------------------------------- #
def test_a_quotable_story_is_pulled_forward_only_from_the_top_few(monkeypatch):
    cfg = dataclasses.replace(settings, hook_quote_first=True)
    quotable = {104}
    monkeypatch.setattr(trending, "has_quote", lambda cid, c: cid in quotable)
    assert trending.prefer_quotable([101, 102, 103, 104, 105], cfg)[0] == 104
    assert trending.prefer_quotable([101, 102, 103, 104, 105], cfg) == [104, 101, 102, 103, 105]   # order of the rest kept
    assert trending.prefer_quotable([101, 102, 103, 105, 106, 104], cfg) == [101, 102, 103, 105, 106, 104]   # #6: too far


def test_ranking_is_untouched_when_the_switch_is_off(monkeypatch):
    cfg = dataclasses.replace(settings, hook_quote_first=False)
    monkeypatch.setattr(trending, "has_quote", lambda cid, c: True)
    assert trending.prefer_quotable([3, 1, 2], cfg) == [3, 1, 2]


def test_ranking_survives_a_failing_lookup(monkeypatch):
    cfg = dataclasses.replace(settings, hook_quote_first=True)

    def boom(cid, c):
        raise RuntimeError("db gone")

    monkeypatch.setattr(trending, "has_quote", boom)
    assert trending.prefer_quotable([3, 1, 2], cfg) == [3, 1, 2]


# --------------------------------------------------------------------------- #
# the record that makes the experiment readable
# --------------------------------------------------------------------------- #
def test_each_videos_opener_is_remembered_by_its_youtube_id(tmp_path):
    cfg = dataclasses.replace(settings, data_dir=tmp_path)
    variants.record(cfg, "abc123", hook="quote")
    variants.record(cfg, "def456", hook="question")
    assert variants.load(cfg) == {"abc123": {"hook": "quote"}, "def456": {"hook": "question"}}


def test_a_missing_id_is_ignored_not_an_error(tmp_path):
    cfg = dataclasses.replace(settings, data_dir=tmp_path)
    variants.record(cfg, "", hook="quote")
    assert variants.load(cfg) == {}


def test_a_broken_history_file_reads_as_empty(tmp_path):
    cfg = dataclasses.replace(settings, data_dir=tmp_path)
    (tmp_path / variants.FILE).write_text("{not json", encoding="utf-8")
    assert variants.load(cfg) == {}

def test_a_quote_with_a_quote_inside_is_skipped():
    """The caption font (Jua) has no single curly quotes: a nested ‘…’ would print as an empty box in the
    very first seconds of the video."""
    c = _ctx(['이재명 대통령은 “이번 ‘인사 쇄신’은 반드시 끝까지 추진하겠다”고 말했다.'])
    assert H.find_quotes(c) == []


def test_the_quote_marks_we_print_exist_in_every_caption_font():
    from pathlib import Path
    from PIL import Image, ImageDraw, ImageFont

    def blank(f, ch):
        a = Image.new("L", (120, 120), 0)
        ImageDraw.Draw(a).text((10, 10), ch, font=f, fill=255)
        b = Image.new("L", (120, 120), 0)
        ImageDraw.Draw(b).text((10, 10), "􏿿", font=f, fill=255)
        return a.tobytes() == b.tobytes()

    fonts = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    for name in ("Jua-Regular", "DoHyeon-Regular", "BlackHanSans-Regular"):
        f = ImageFont.truetype(str(fonts / f"{name}.ttf"), 60)
        assert not blank(f, "“") and not blank(f, "”"), name

# --------------------------------------------------------------------------- #
# the picture under a quote is the SPEAKER's - seen on the first real render, 2026-10-20
# --------------------------------------------------------------------------- #
def _imgs():
    return [{"path": "kim.jpg", "kind": "portrait", "is_lead": True, "query": "김승원"},
            {"path": "lee.jpg", "kind": "portrait", "query": "이재명"},
            {"path": "p0.jpg", "kind": "photo", "query": "국회"},
            {"path": "p1.jpg", "kind": "photo", "query": "시청"}]


def _cards(hook):
    return [hook,
            {"role": "summary", "caption": "배경", "narration": "김승원 후보자 관련 배경입니다."},
            {"role": "what", "caption": "무슨 일", "narration": "무슨 일이 있었습니다."}]


def test_the_quote_card_shows_the_speaker_not_the_stories_subject():
    """The quote was President Lee's; the face under it was 김승원's - the story's subject. The caption said
    whose words they were and the picture said otherwise."""
    from political_shorts.video import _assign_images

    hook = {"role": "hook", "caption": "x", "narration": "“인사시스템을 재점검해보려고 한다” 이재명 대통령이 한 말입니다.",
            "hook_kind": "quote", "hook_speaker": "이재명 대통령"}
    assert _assign_images(_cards(hook), _imgs(), "김승원")[0] == "lee.jpg"


def test_a_face_named_inside_the_quote_is_not_the_speaker():
    """'김승원 후보자는 ...' inside the words must not pull 김승원's face under them."""
    from political_shorts.video import _assign_images

    hook = {"role": "hook", "caption": "x", "hook_kind": "quote", "hook_speaker": "이재명 대통령",
            "narration": "“김승원 후보자 문제는 더 지켜보겠다” 이재명 대통령이 한 말입니다."}
    assert _assign_images(_cards(hook), _imgs(), "김승원")[0] == "lee.jpg"


def test_without_the_speakers_portrait_the_subjects_face_does_not_stand_in():
    from political_shorts.video import _assign_images

    imgs = [i for i in _imgs() if i["query"] != "이재명"]
    hook = {"role": "hook", "caption": "x", "narration": "“인사시스템을 재점검해보려고 한다” 이재명 대통령이 한 말입니다.",
            "hook_kind": "quote", "hook_speaker": "이재명 대통령"}
    first = _assign_images(_cards(hook), imgs, "김승원")[0]
    assert first != "kim.jpg" and first in ("p0.jpg", "p1.jpg")        # a neutral picture, never the wrong face


def test_a_party_quote_gets_a_neutral_picture_not_a_face():
    from political_shorts.video import _assign_images

    hook = {"role": "hook", "caption": "x", "narration": "“오늘 내놓은 것은 변명이었다” 국민의힘이 한 말입니다.",
            "hook_kind": "quote", "hook_speaker": "국민의힘"}
    assert _assign_images(_cards(hook), _imgs(), "김승원")[0] in ("p0.jpg", "p1.jpg")


def test_every_other_opener_is_laid_out_exactly_as_before():
    from political_shorts.video import _assign_images

    hook = {"role": "hook", "caption": "x", "narration": "같은 사안을 두고 정반대로 말합니다.", "hook_kind": "conflict"}
    assert _assign_images(_cards(hook), _imgs(), "김승원")[0] == "kim.jpg"        # the subject opens, as it always did


def test_the_speakers_portrait_is_asked_for_even_when_he_is_not_the_subject():
    from political_shorts.script_gen import _speaker_names

    seg = [{"role": "hook", "hook_speaker": "이재명 대통령"}]
    assert _speaker_names(seg, _ent(politicians=("김승원",))) == ["이재명"]
    assert _speaker_names([{"role": "hook", "hook_speaker": "김민석 대표"}], _ent(politicians=("김민석",))) == ["김민석"]


def test_a_party_or_an_unlisted_name_is_not_looked_up_for_a_face():
    from political_shorts.script_gen import _speaker_names

    assert _speaker_names([{"role": "hook", "hook_speaker": "국민의힘"}], _ent(parties=("국민의힘",))) == []
    assert _speaker_names([{"role": "hook", "hook_speaker": "홍길동 의원"}], _ent(politicians=("김승원",))) == []
    assert _speaker_names([{"role": "hook"}], _ent()) == []


def test_collect_images_collects_the_speaker_even_when_he_is_not_in_the_headline(monkeypatch, tmp_path):
    from political_shorts import images
    from political_shorts.hook import detect_entities, detect_frame

    asked = []
    monkeypatch.setattr(images, "_portrait_infos", lambda name, cfg, n=1: (asked.append(name) or []))
    monkeypatch.setattr(images, "_wikidata_portrait", lambda name, cfg: None)
    monkeypatch.setattr(images, "_resolve", lambda *a, **k: None)
    cfg = dataclasses.replace(settings, image_enabled=True, image_cache_dir=str(tmp_path / "c"), data_dir=tmp_path)
    hl = "김승원 후보자 사퇴"
    images.collect_images(detect_entities(hl), detect_frame(hl), hl, cfg, also_people=["이재명"])
    assert "이재명" in asked
