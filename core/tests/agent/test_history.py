"""Tests for the voice assistant's history: the turns, newest first, and what a turn becomes."""

import pytest

from raphael_core.agent.history import AgentHistory, Turn
from raphael_core.agent.steps import Step


class Clock:
    def __init__(self, now_ms=1_000):
        self.now_ms = now_ms

    def __call__(self):
        return self.now_ms


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def history(conn, clock):
    return AgentHistory(conn, clock)


def add(history, clock, question="q", *, advance=1, **overrides):
    clock.now_ms += advance
    fields = {
        "session_id": "s1",
        "question": question,
        "route": "chat",
        "prefix": "答",
        "text": "好。",
        "status": "done",
    }
    return history.add(**{**fields, **overrides})


def test_a_turn_is_stored_as_given_and_stamped_with_the_time(history, clock):
    turn_id = add(
        history, clock, "台北天氣如何", route="answer", detail="長一點的答案", cost_usd=0.0004
    )
    assert history.get(turn_id) == Turn(
        id=turn_id,
        created_at_ms=1_001,
        session_id="s1",
        question="台北天氣如何",
        route="answer",
        prefix="答",
        text="好。",
        detail="長一點的答案",
        status="done",
        cost_usd=0.0004,
    )


def test_a_turn_needs_no_detail_and_costs_nothing_by_default(history, clock):
    turn = history.get(add(history, clock))
    assert turn.detail is None and turn.cost_usd == 0.0


def test_an_unknown_turn_is_none(history):
    assert history.get(99) is None


def test_messages_are_immediately_readable_in_order_and_cleared_with_their_thread(history, clock):
    first = add(history, clock, status="running", member_id=1)
    other = add(history, clock, member_id=2)
    history.add_message(first, "progress", "I will check the examples.")
    clock.now_ms += 10
    history.add_message(first, "event", "Example saved")
    history.add_message(first, "reply", "Try the next sentence.")
    history.add_message(other, "reply", "Another conversation")
    messages = history.messages([first, other])
    assert [(m["seq"], m["kind"], m["text"]) for m in messages[first]] == [
        (1, "progress", "I will check the examples."),
        (2, "event", "Example saved"),
        (3, "reply", "Try the next sentence."),
    ]
    assert messages[first][1]["at_ms"] == clock.now_ms
    history.resolve(first, prefix="", text="Try the next sentence.", status="done")
    assert history.recent(1, member_id=1)[0].messages == [
        "I will check the examples.", "Example saved", "Try the next sentence."
    ]
    assert history.search("examples", member_id=1)[0].id == first
    history.clear_member(1)
    assert history.messages([first, other]) == {other: messages[other]}
    history.clear()
    assert history.messages([other]) == {}


def test_list_is_newest_first(history, clock):
    ids = [add(history, clock, f"q{i}") for i in range(3)]
    assert [t.id for t in history.list()] == ids[::-1]
    assert [t.question for t in history.list()] == ["q2", "q1", "q0"]


def test_turns_made_in_the_same_millisecond_keep_their_order(history, clock):
    ids = [add(history, clock, f"q{i}", advance=0) for i in range(3)]
    assert [t.id for t in history.list()] == ids[::-1]


def test_list_pages_by_the_id_of_the_last_turn_seen(history, clock):
    ids = [add(history, clock, f"q{i}") for i in range(5)]
    first = history.list(limit=2)
    assert [t.id for t in first] == [ids[4], ids[3]]
    second = history.list(limit=2, before_id=first[-1].id)
    assert [t.id for t in second] == [ids[2], ids[1]]
    last = history.list(limit=2, before_id=second[-1].id)
    assert [t.id for t in last] == [ids[0]]
    assert history.list(limit=2, before_id=ids[0]) == []


def test_resolve_sets_the_outcome_of_a_turn_and_adds_its_cost(history, clock):
    turn_id = add(history, clock, route="command", prefix="問", text="複製嗎？", status="confirm",
                  cost_usd=0.001)  # fmt: skip
    assert history.resolve(turn_id, prefix="了", text="已複製。", status="done", cost_usd=0.002)
    turn = history.get(turn_id)
    assert (turn.prefix, turn.text, turn.status, turn.detail) == ("了", "已複製。", "done", None)
    assert turn.cost_usd == pytest.approx(0.003)
    assert turn.question == "q" and turn.created_at_ms == 1_001  # the rest stays


def test_resolving_a_turn_that_is_gone_says_so(history):
    assert history.resolve(7, prefix="了", text="x", status="done") is False


def test_recent_is_the_last_answered_turns_oldest_first(history, clock):
    for i in range(6):
        add(history, clock, f"q{i}")
    add(history, clock, "pending", status="confirm")
    add(history, clock, "refused", status="denied")
    add(history, clock, "failed", status="error")
    assert [t.question for t in history.recent(4)] == ["q2", "q3", "q4", "q5"]
    assert [t.question for t in history.recent(100)] == [f"q{i}" for i in range(6)]
    assert history.recent(0) == []


def test_clear_deletes_every_turn_and_counts_them(history, clock):
    for i in range(3):
        add(history, clock, f"q{i}")
    assert history.clear() == 3
    assert history.list() == []
    assert history.clear() == 0


def test_the_turns_are_never_pruned_by_age(history):
    # One continuous conversation: the turns stay until the user clears them.
    assert not hasattr(history, "prune")


def test_after_gives_the_turns_after_one_oldest_first_whatever_their_status(history, clock):
    ids = [add(history, clock, f"q{i}", status="confirm" if i == 2 else "done") for i in range(5)]
    assert [t.question for t in history.after(ids[1])] == ["q2", "q3", "q4"]
    assert [t.question for t in history.after(0, limit=2)] == ["q3", "q4"]
    assert history.after(ids[-1]) == [] and history.after(0, limit=0) == []
    assert history.last_id() == ids[-1]


def test_a_confirmation_that_died_with_the_process_is_expired(history, clock):
    waiting = add(history, clock, "a", status="confirm", prefix="問")
    done = add(history, clock, "b")
    assert history.expire_pending() == 1
    assert history.get(waiting).status == "expired"
    assert history.get(done).status == "done"
    assert history.expire_pending() == 0


def test_every_write_is_committed(tmp_path):
    from raphael_core.brain import db

    path = tmp_path / "raphael.db"
    writer, reader = db.connect(path), db.connect(path)
    clock = Clock()
    mine, theirs = AgentHistory(writer, clock), AgentHistory(reader, clock)
    turn_id = add(mine, clock, "q")
    assert [t.id for t in theirs.list()] == [turn_id]
    mine.resolve(turn_id, prefix="否", text="x", status="error")
    assert theirs.get(turn_id).status == "error"
    mine.clear()
    assert theirs.list() == []
    writer.close()
    reader.close()


# --- where a turn came from, and its steps ---


def test_a_turn_is_spoken_unless_said_otherwise_and_keeps_its_model(history, clock):
    spoken = history.get(add(history, clock))
    assert (spoken.source, spoken.model) == ("voice", None)
    typed = history.get(add(history, clock, source="telegram", model="google/gemini-3-flash"))
    assert (typed.source, typed.model) == ("telegram", "google/gemini-3-flash")


def test_a_turn_is_stored_with_its_steps_in_order(history, clock):
    weather = Step("tool", "weather", "Raphael", "ok", 420, at_ms=900)
    failed = Step("tool", "create-event", "Google Calendar", "error", 1_200, "auth", at_ms=950)
    turn_id = add(history, clock, steps=[weather, failed])
    other = add(history, clock)
    assert history.steps([turn_id, other]) == {turn_id: [weather, failed]}
    assert history.steps([]) == {}


def test_steps_added_later_come_after_the_ones_a_turn_has(history, clock):
    turn_id = add(history, clock, steps=[Step("tool", "weather", "Raphael", "ok", 1, at_ms=5)])
    clock.now_ms = 7_000
    history.add_steps(turn_id, [Step("command", "copy_text", None, "ok", 3)])
    steps = history.steps([turn_id])[turn_id]
    assert [s.tool for s in steps] == ["weather", "copy_text"]
    assert steps[1].at_ms == 7_000  # stamped now when it was not given a time


def test_a_negative_duration_is_kept_as_none_at_all(history, clock):
    turn_id = add(history, clock, steps=[Step("tool", "weather", "Raphael", "ok", -4, at_ms=1)])
    assert history.steps([turn_id])[turn_id][0].ms == 0


def test_resolve_keeps_the_model_unless_a_new_one_answered(history, clock):
    turn_id = add(history, clock, status="confirm", model="a/first")
    history.resolve(turn_id, prefix="了", text="", status="done")
    assert history.get(turn_id).model == "a/first"
    history.resolve(turn_id, prefix="答", text="好", status="done", model="b/second")
    assert history.get(turn_id).model == "b/second"


def test_clear_takes_the_steps_and_the_search_with_the_turns(history, clock, conn):
    old = add(history, clock, advance=10, steps=[Step("tool", "weather", "Raphael", "ok", 1)])
    new = add(history, clock, advance=10, steps=[Step("tool", "web_search", "Raphael", "ok", 1)])
    assert set(history.steps([old, new])) == {old, new}
    history.clear()
    assert conn.execute("SELECT count(*) FROM agent_steps").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM agent_turns_fts").fetchone()[0] == 0
