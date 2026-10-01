"""Incident 2026-09-28/29: the DMZ mine blast went out as a video two evenings running.

The person-level checks missed it — the lead actor differed (장동혁 reacting, then 이재명 reacting) and the
full-signature overlap sat under the threshold — so the event itself is now compared.
"""
import sqlite3
import time

import pytest

from political_shorts import topics
from political_shorts.config import settings
from political_shorts.db import init_db, record_topic


@pytest.fixture()
def conn(tmp_path):
    init_db(tmp_path / "t.sqlite3")
    c = sqlite3.connect(tmp_path / "t.sqlite3")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


DAY28 = ("거론 국민의힘 국회 눈뜨고 막말 못볼 민주당 장동혁 지경 지뢰사고에 탄핵 한병도", "장동혁")
DAY29 = set("국민의힘 말아야 부추기지 음모론 이재명 조치 지뢰사고 진상 투명하게 필요".split())


def _publish(conn, signature, actor, hours_ago=18.0):
    rid = record_topic(conn, signature=signature, headline="지뢰사고 공방", actor=actor, frame="clash")
    conn.execute("UPDATE topic_history SET published_ts = ? WHERE id = ?",
                 (int(time.time() - hours_ago * 3600), rid))
    conn.commit()


def test_the_same_event_the_next_day_is_a_duplicate(conn):
    _publish(conn, *DAY28)
    is_dup, why = topics.recent_duplicate(conn, DAY29, settings, actor="이재명", people={"이재명"})
    assert is_dup and "같은 사건" in why and "지뢰사고" in why


def test_one_short_word_in_common_is_not_an_event(conn):
    """'탄핵' alone turns up constantly; it takes a compound name or two words to pin one event down."""
    _publish(conn, "국민의힘 민주당 장동혁 탄핵 추진", "장동혁")
    other = set("국회 대법원장 이재명 탄핵 언급".split())
    assert topics._event_score(topics._shared_event(
        set("국민의힘 민주당 장동혁 탄핵 추진".split()), other)) < topics._EVENT_MIN_SCORE
    assert topics.recent_duplicate(conn, other, settings, actor="이재명", people=set())[0] is False


def test_a_different_story_on_the_same_day_is_not(conn):
    _publish(conn, *DAY28)
    other = set("국감 국민의힘 국회 김용범 대통령실 배민 이견 재경위 증인 채택".split())
    assert topics.recent_duplicate(conn, other, settings, actor="김용범", people={"김용범"})[0] is False


def test_party_names_and_procedure_words_alone_never_make_a_duplicate(conn):
    _publish(conn, "국민의힘 국회 민주당 본회의 예산안 처리 충돌", "")
    other = set("국민의힘 국회 민주당 본회의 인사청문회 후보자 논란".split())
    assert topics.recent_duplicate(conn, other, settings, actor="", people=set())[0] is False
    assert topics._shared_event(set("국민의힘 국회 민주당 본회의 예산안 처리 충돌".split()), other) == set()


def test_the_window_closes_after_three_days(conn):
    _publish(conn, *DAY28, hours_ago=24 * 4)
    assert topics.recent_duplicate(conn, DAY29, settings, actor="이재명", people=set())[0] is False
