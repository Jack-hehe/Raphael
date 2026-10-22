"""Tests for the search over past turns: SQLite FTS5 over the terms made in Python, Chinese words of
two characters among them, and the LIKE search where SQLite has no FTS5."""

import sqlite3

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.brain import db

from .conftest import add_turn


def test_the_bundled_sqlite_has_fts5():
    # The search is built on it; the LIKE search is only for a build that left it out.
    assert sqlite3.sqlite_version_info >= (3, 9, 0)
    conn = sqlite3.connect(":memory:")
    assert "ENABLE_FTS5" in {row[0] for row in conn.execute("PRAGMA compile_options")}
    conn.close()


@pytest.fixture(params=["fts5", "like"])
def history(request, conn, clock):
    """The history, with the search as this SQLite has it, and as one without FTS5 would."""
    if request.param == "like":
        conn.execute("DROP TABLE agent_turns_fts")
        conn.execute(
            "CREATE TABLE agent_turns_fts (rowid INTEGER PRIMARY KEY, pairs TEXT NOT NULL,"
            " chars TEXT NOT NULL)"
        )
    return AgentHistory(conn, clock.ms)


def questions(turns):
    return [turn.question for turn in turns]


def test_a_chinese_word_of_two_characters_is_found_inside_a_longer_run(history):
    add_turn(history, "明天的會議幾點開始", "早上十點。")
    add_turn(history, "今天天氣如何", "多雲。")
    add_turn(history, "議題是什麼", "預算。")
    assert questions(history.search("會議")) == ["明天的會議幾點開始"]
    assert questions(history.search("天氣")) == ["今天天氣如何"]


def test_a_word_is_found_in_the_answer_too(history):
    add_turn(history, "Amy 說了什麼", "她說下週三交設計稿。")
    assert questions(history.search("設計稿")) == ["Amy 說了什麼"]


def test_english_words_are_found_whatever_their_case(history):
    add_turn(history, "Which model is OpenRouter using", "GPT-6 Luna.")
    assert questions(history.search("openrouter")) == ["Which model is OpenRouter using"]
    assert questions(history.search("LUNA")) == ["Which model is OpenRouter using"]


def test_one_character_is_found_on_its_own(history):
    add_turn(history, "我的貓叫什麼", "小橘。")
    add_turn(history, "狗呢", "沒有記錄。")
    assert questions(history.search("貓")) == ["我的貓叫什麼"]


def test_a_turn_with_any_of_the_words_is_found(history):
    add_turn(history, "會議在哪裡", "三樓。")
    add_turn(history, "Amy 幾點到", "九點。")
    add_turn(history, "晚餐吃什麼", "拉麵。")
    assert set(questions(history.search("會議 Amy"))) == {"會議在哪裡", "Amy 幾點到"}


def test_a_sentence_with_no_spaces_falls_back_to_its_pairs(history):
    add_turn(history, "下週的出差行程", "週二出發。")
    assert questions(history.search("上次說的出差")) == ["下週的出差行程"]


def test_only_answered_turns_are_found_and_the_limit_holds(history):
    for n in range(12):
        add_turn(history, f"會議 {n}")
    add_turn(history, "會議 waiting", status="confirm")
    found = history.search("會議", limit=50)
    assert len(found) == 10  # SEARCH_MAX
    assert "會議 waiting" not in questions(found)
    assert len(history.search("會議", limit=3)) == 3
    assert history.search("") == [] and history.search("找不到的字串") == []


def test_since_keeps_the_newer_turns(history, clock):
    add_turn(history, "舊的會議")
    clock.advance(days=10)
    cutoff = clock.ms()
    add_turn(history, "新的會議")
    assert questions(history.search("會議", since_ms=cutoff)) == ["新的會議"]


def test_the_turns_left_out_are_left_out_before_the_limit(history):
    old = add_turn(history, "舊的會議")
    recent = [add_turn(history, f"會議 {n}") for n in range(12)]
    assert questions(history.search("會議", exclude=recent)) == ["舊的會議"]
    assert {t.id for t in history.search("會議", limit=2, exclude=recent[:11])} == {recent[11], old}
    # The pairs a long run of Chinese falls back to leave them out too.
    assert questions(history.search("上次說的會議", exclude=recent)) == ["舊的會議"]


def test_a_resolved_turn_is_found_by_its_final_words_and_a_cleared_one_is_not(history):
    turn = add_turn(history, "要寄信嗎", "是否寄出？", status="confirm")
    assert history.search("寄出") == []  # not answered yet
    history.resolve(turn, prefix="了", text="已寄給 Amy。", status="done")
    assert questions(history.search("Amy")) == ["要寄信嗎"]
    assert history.search("寄出") == []  # the words it holds now
    history.clear()
    assert history.search("Amy") == []


def test_quotes_and_operators_in_a_query_are_only_words(history):
    add_turn(history, "AND OR NOT 是什麼", "布林運算。")
    assert questions(history.search('"AND" OR NOT*')) == ["AND OR NOT 是什麼"]
    assert history.search('"') == []


def test_a_database_of_an_older_version_has_its_turns_put_into_the_search(tmp_path):
    path = tmp_path / "v12.db"
    raw = sqlite3.connect(path)
    for version in range(1, 13):
        step = db._MIGRATIONS[version]
        if callable(step):
            step(raw)
        else:
            raw.executescript(step)
    raw.execute(
        "INSERT INTO agent_turns (created_at_ms, session_id, question, route, prefix, text,"
        " status) VALUES (1, 's', '上次的會議記錄', 'chat', '答', '在 Notion。', 'done')"
    )
    raw.commit()
    raw.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    assert [t.question for t in AgentHistory(conn).search("會議")] == ["上次的會議記錄"]
    assert [t.question for t in AgentHistory(conn).search("notion")] == ["上次的會議記錄"]
    conn.close()
