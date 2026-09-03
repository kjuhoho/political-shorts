"""Story-level de-dup: a story we've already published is skipped for a while."""
from political_shorts.db import connect, init_db, record_topic
from political_shorts.topics import recent_duplicate, signature_str, story_signature


def _sig(headline, entities, frame=""):
    return story_signature(headline, entities, frame)


def test_same_actor_followup_is_duplicate(tmp_path):
    db = tmp_path / "t.sqlite3"
    init_db(db)
    ent = {"president": True, "politicians": ["김용범"], "parties": ["국민의힘"],
           "institutions": ["청와대"]}
    sig = _sig("김용범 청와대 정책실장 사퇴...15개월만에 교체", ent, "personnel")
    with connect(db) as c:
        record_topic(c, signature_str(sig), "김용범 정책실장 사퇴", "personnel",
                     "VID123", "youtube", "김용범")

    # a later, differently-worded story about the same person
    later = _sig("청와대, 김용범 후임 인선 속도...윤창렬 거론",
                 {"president": True, "politicians": ["김용범"], "parties": [],
                  "institutions": ["청와대"]}, "personnel")
    with connect(db) as c:
        dup, why = recent_duplicate(c, later, actor="김용범")
    assert dup and "김용범" in why


def test_unrelated_story_not_duplicate(tmp_path):
    db = tmp_path / "t.sqlite3"
    init_db(db)
    sig = _sig("김용범 청와대 정책실장 사퇴", {"politicians": ["김용범"]}, "personnel")
    with connect(db) as c:
        record_topic(c, signature_str(sig), "김용범 사퇴", "personnel", "V", "youtube", "김용범")

    other = _sig("조국 '이 대통령 2심 유죄 가능성' 발언 파장",
                 {"president": True, "politicians": ["조국"], "parties": ["조국혁신당"]},
                 "remark")
    with connect(db) as c:
        dup, _ = recent_duplicate(c, other, actor="조국")
    assert not dup


def test_broad_actor_needs_keyword_overlap(tmp_path):
    db = tmp_path / "t.sqlite3"
    init_db(db)
    sig = _sig("이재명 대통령, 부동산 세제 개편 지시", {"president": True}, "remark")
    with connect(db) as c:
        record_topic(c, signature_str(sig), "이재명 세제", "remark", "V", "youtube", "이재명")

    # different 이재명 story — same broad actor must NOT auto-dedupe
    other = _sig("이재명 대통령, 국무회의서 개각 마무리 발언", {"president": True}, "remark")
    with connect(db) as c:
        dup, _ = recent_duplicate(c, other, actor="이재명")
    assert not dup


def test_dedup_disabled_when_days_zero(tmp_path):
    import dataclasses

    from political_shorts.config import settings
    db = tmp_path / "t.sqlite3"
    init_db(db)
    sig = _sig("김용범 사퇴", {"politicians": ["김용범"]}, "personnel")
    with connect(db) as c:
        record_topic(c, signature_str(sig), "김용범 사퇴", "personnel", "V", "youtube", "김용범")
    off = dataclasses.replace(settings, topic_dedup_days=0)
    with connect(db) as c:
        dup, _ = recent_duplicate(c, sig, off, actor="김용범")
    assert not dup
