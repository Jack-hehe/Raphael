"""Tests for the tasks and steps in the database."""

import pytest

from raphael_core.background.tasks import FINAL_STATES, TaskStore


@pytest.fixture
def store(conn):
    ticks = iter(range(1000, 100000, 10))
    return TaskStore(conn, now_ms=lambda: next(ticks))


def make(store, text="do it", language="en"):
    return store.create(session_id="s", language=language, text=text)


def test_a_new_task_is_queued_and_holds_what_was_handed_over(store):
    task_id = make(store, "look up tea")
    task = store.get(task_id)
    assert (task.state, task.text, task.language, task.session_id) == ("queued", "look up tea", "en", "s")
    assert (task.model_calls, task.tool_calls, task.cost_usd) == (0, 0, 0.0)
    assert task.turn_id is None and task.report_text is None and task.finished_at_ms is None


def test_a_task_goes_from_queued_to_running_to_its_end_with_its_report(store):
    task_id = make(store)
    store.attach_turn(task_id, 41)
    store.start(task_id)
    running = store.get(task_id)
    assert running.state == "running" and running.started_at_ms is not None
    store.progress(task_id, model_calls=2, tool_calls=1, cost_usd=0.003)
    store.finish(task_id, "done", prefix="告", text="Done.", detail="More.", reason=None)
    task = store.get(task_id)
    assert (task.state, task.turn_id, task.report_prefix, task.report_text, task.report_detail) == (
        "done", 41, "告", "Done.", "More.",
    )  # fmt: skip
    assert (task.model_calls, task.tool_calls, task.cost_usd) == (2, 1, 0.003)
    assert task.finished_at_ms > task.started_at_ms


def test_a_task_cannot_end_in_a_state_that_is_not_an_end(store):
    task_id = make(store)
    for state in ("queued", "running", "weird"):
        with pytest.raises(ValueError):
            store.finish(task_id, state, prefix="告", text="x", detail=None, reason=None)
    for state in FINAL_STATES:
        store.finish(task_id, state, prefix="告", text="x", detail=None, reason=None)


def test_the_steps_are_numbered_in_order_for_each_task(store):
    first, second = make(store), make(store)
    store.add_step(first, kind="model", outcome="ok", cost_usd=0.001, ms=300)
    store.add_step(first, kind="tool", outcome="ok", tool="look", server="Notes", tier="T0", ms=12)
    store.add_step(second, kind="model", outcome="error")
    store.add_step(first, kind="tool", outcome="blocked")
    assert [(s.n, s.kind, s.tool, s.outcome) for s in store.steps(first)] == [
        (1, "model", None, "ok"),
        (2, "tool", "look", "ok"),
        (3, "tool", None, "blocked"),
    ]
    assert [s.n for s in store.steps(second)] == [1]
    tool = store.steps(first)[1]
    assert (tool.server, tool.tier, tool.ms) == ("Notes", "T0", 12)


def test_a_step_has_no_column_for_an_argument_or_a_result(conn):
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(worker_steps)")]
    assert columns == [
        "id", "task_id", "n", "at_ms", "kind", "tool", "server", "tier", "outcome", "cost_usd", "ms",
        "reason",
    ]  # fmt: skip


def test_the_newest_task_comes_first_and_the_list_is_limited(store):
    ids = [make(store, f"task {n}") for n in range(5)]
    assert [t.id for t in store.list(3)] == ids[::-1][:3]
    assert len(store.list(1000)) == 5
    assert len(store.list(0)) == 1  # a limit is clamped, not obeyed to the letter


def test_tasks_left_unfinished_by_the_last_process_are_interrupted_and_the_rest_are_not(store):
    queued, running, done = make(store), make(store), make(store)
    store.start(running)
    store.start(done)
    store.finish(done, "done", prefix="告", text="x", detail=None, reason=None)
    assert store.interrupt_unfinished() == 2
    assert store.get(queued).state == store.get(running).state == "interrupted"
    assert store.get(queued).reason == "restart" and store.get(queued).finished_at_ms is not None
    assert store.get(done).state == "done"
    assert store.interrupt_unfinished() == 0


def test_old_finished_tasks_are_pruned_with_their_steps_and_one_that_runs_is_not(store):
    old, young, running = make(store, "old"), make(store, "young"), make(store, "running")
    for task_id in (old, young):
        store.add_step(task_id, kind="model", outcome="ok")
        store.finish(task_id, "done", prefix="告", text="x", detail=None, reason=None)
    store.add_step(running, kind="model", outcome="ok")
    store.start(running)
    cutoff = store.get(young).created_at_ms
    assert store.prune(cutoff) == 1
    assert store.get(old) is None and store.steps(old) == []
    assert store.get(young) is not None and store.steps(young)
    assert store.prune(10**12) == 1  # everything finished; `running` stays
    assert store.get(running) is not None and store.steps(running)


def test_clearing_deletes_what_is_finished_and_leaves_what_runs(store):
    done, running = make(store), make(store)
    store.add_step(done, kind="model", outcome="ok")
    store.finish(done, "limit", prefix="告", text="x", detail=None, reason="limit_model")
    store.start(running)
    assert store.clear() == 1
    assert store.get(done) is None and store.steps(done) == []
    assert store.get(running) is not None


def test_a_step_that_failed_keeps_its_reason(store):
    task_id = make(store)
    store.add_step(task_id, kind="tool", outcome="error", tool="look", reason="auth")
    store.add_step(task_id, kind="tool", outcome="ok", tool="look")
    assert [s.reason for s in store.steps(task_id)] == ["auth", None]


def test_the_task_of_each_turn_is_found_by_the_turn(store):
    first, second, alone = make(store), make(store), make(store)
    store.attach_turn(first, 11)
    store.attach_turn(second, 12)
    found = store.for_turns([11, 12, 13])
    assert {turn: task.id for turn, task in found.items()} == {11: first, 12: second}
    assert store.for_turns([]) == {}
    assert alone not in {task.id for task in found.values()}
