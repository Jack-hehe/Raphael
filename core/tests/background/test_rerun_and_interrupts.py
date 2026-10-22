"""Tests for a task that was interrupted (told once, in the conversation view too), the rerun of a task
that did not finish, and the cost one task may spend (a setting)."""

import asyncio
import json

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.agent.persona import phrase
from raphael_core.background.run import RunLimits
from raphael_core.background.service import _prompts
from raphael_core.brain.settings import SettingsStore
from raphael_core.protocol import INVALID_PARAMS, NOT_FOUND, RpcError

from .conftest import ARGUMENT, TASK_TEXT, call, report, turn, until


def hand_over(rig, conn, *, language="en", source="voice", text=TASK_TEXT):
    """A task and the turn of the history that handed it over, as the fast tier would write them."""
    history = AgentHistory(conn, rig.wall)
    turn_id = history.add(
        session_id="s1", question=text, route="delegate", prefix="Understood",
        text=phrase(language, "handoff"), status="done", source=source,
    )  # fmt: skip
    task_id = rig.submit(text, language=language)
    rig.service.attach_turn(task_id, turn_id)
    return task_id, turn_id, history


# --- interrupted tasks are told ---------------------------------------------------------------------


async def test_tasks_left_unfinished_get_a_report_and_their_turn_is_completed(make_rig, conn):
    first = make_rig()
    running, running_turn, history = hand_over(first, conn, language="zh-TW")
    queued, _, _ = hand_over(first, conn, language="en")
    first.service._store.start(running)
    first.service._store.add_step(running, kind="model", outcome="ok")
    first.service._store.add_step(running, kind="tool", outcome="ok", tool="look", server="Notes")
    first.service._queue.clear()
    second = make_rig()  # the next run of the core
    task = second.task(running)
    assert (task.state, task.reason, task.announced) == ("interrupted", "restart", 0)
    assert task.report_prefix == "告"
    assert task.report_text == phrase("zh-TW", "worker_interrupted", steps="1")
    assert second.task(queued).report_text == phrase("en", "worker_interrupted", steps="0")
    turn_row = history.get(running_turn)
    assert turn_row.text == task.report_text and turn_row.prefix == "告"
    assert turn_row.status == "error" and turn_row.text != phrase("zh-TW", "handoff")
    assert second.notifications == []  # said only once the app has said hello


async def test_only_the_newest_interrupted_report_is_said_and_only_once(make_rig, conn):
    first = make_rig()
    older, _, _ = hand_over(first, conn)
    newer, _, _ = hand_over(first, conn)
    first.service._queue.clear()
    second = make_rig()
    second.service.announce_pending()
    assert second.reports() == [
        {
            "task_id": newer,
            "state": "interrupted",
            "prefix": "Notice",
            "text": phrase("en", "worker_interrupted", steps="0"),
        }
    ]
    assert second.task(older).announced == 1 and second.task(newer).announced == 1
    second.service.announce_pending()
    assert len(second.reports()) == 1  # not again
    third = make_rig()
    third.service.announce_pending()
    assert third.reports() == []


async def test_closing_the_core_tells_the_task_that_ran_and_the_one_that_waited(
    make_rig, conn, calls
):
    rig = make_rig()
    await rig.plugins.connect(slow="T0")
    rig.client.replies = [turn("", [call("Notes__slow", {}, "c1")])]
    running, running_turn, history = hand_over(rig, conn, language="ja")
    waiting, waiting_turn, _ = hand_over(rig, conn, language="ja")
    await until(lambda: "slow" in calls)
    await asyncio.wait_for(rig.service.aclose(), 10)
    for task_id in (running, waiting):
        task = rig.task(task_id)
        assert task.state == "interrupted" and task.announced == 0
        assert task.report_prefix == "告" and task.report_text
    assert rig.task(running).reason == "closing"
    assert rig.task(running).report_text == phrase("ja", "worker_interrupted", steps="1")
    assert history.get(running_turn).text == rig.task(running).report_text
    assert history.get(waiting_turn).status == "error"
    assert rig.reports() == []
    # the next run says the newest
    after = make_rig()
    after.service.announce_pending()
    assert [r["task_id"] for r in after.reports()] == [waiting]


# --- rerun --------------------------------------------------------------------------------------------


def finish(rig, task_id, state, reason=None):
    rig.service._store.finish(task_id, state, prefix="告", text="x", detail=None, reason=reason)


@pytest.mark.parametrize("state", ["interrupted", "cancelled", "limit", "failed"])
async def test_a_task_that_did_not_finish_can_be_run_again(make_rig, conn, state):
    rig = make_rig()
    old, old_turn, history = hand_over(rig, conn, source="telegram")
    rig.service._queue.clear()
    finish(rig, old, state, "x")
    rig.client.replies = [report("Second time.")]
    result = await rig.rpc.call("worker.rerun", {"task_id": old})
    new = result["task_id"]
    assert new != old and rig.task(new).rerun_of == old
    [done] = await rig.wait_for_reports()
    assert (done["task_id"], done["text"]) == (new, "Second time.")
    new_turn = rig.task(new).turn_id
    assert new_turn and new_turn != old_turn
    turn_row = history.get(new_turn)
    assert (turn_row.route, turn_row.source, turn_row.question, turn_row.session_id) == (
        "delegate", "telegram", TASK_TEXT, "s1",
    )  # fmt: skip
    assert turn_row.text == "Second time."  # completed by the report like any other
    assert rig.task(new).text == TASK_TEXT and rig.task(new).session_id == "s1"


@pytest.mark.parametrize("state", ["done", "queued", "running"])
async def test_a_task_that_finished_or_is_not_over_cannot_be_run_again(make_rig, conn, state):
    rig = make_rig()
    old = rig.submit()
    rig.service._queue.clear()
    if state == "done":
        finish(rig, old, "done")
    elif state == "running":
        rig.service._store.start(old)
    with pytest.raises(RpcError) as error:
        await rig.rpc.call("worker.rerun", {"task_id": old})
    assert error.value.code == INVALID_PARAMS


async def test_rerun_checks_its_parameters(make_rig):
    rig = make_rig()
    for params in ({}, {"task_id": "1"}, {"task_id": True}):
        with pytest.raises(RpcError) as error:
            await rig.rpc.call("worker.rerun", params)
        assert error.value.code == INVALID_PARAMS
    with pytest.raises(RpcError) as error:
        await rig.rpc.call("worker.rerun", {"task_id": 999})
    assert error.value.code == NOT_FOUND


async def test_the_second_attempt_is_told_what_was_done_without_any_argument(make_rig, conn, calls):
    rig = make_rig()
    await rig.plugins.connect(look="T0", slow="T0")
    rig.client.replies = [
        turn("", [call("Notes__look", {"topic": ARGUMENT}, "c1")]),
        turn("", [call("Notes__slow", {}, "c2")]),
    ]
    old, _, _ = hand_over(rig, conn)
    await until(lambda: "slow" in calls)
    await rig.rpc.call("worker.cancel", {"task_id": old})
    await until(lambda: rig.task(old).state == "cancelled")
    rig.client.replies = [report("Redone.")]
    await rig.rpc.call("worker.rerun", {"task_id": old})
    await rig.wait_for_reports(2)
    user = rig.client.chat_calls[-1]["messages"][1]["content"]
    assert "<previous_attempt>" in user and "</previous_attempt>" in user
    block = user.split("<previous_attempt>")[1].split("</previous_attempt>")[0]
    assert "Notes / look: ok" in block
    assert "Notes / slow: cut off" in block and "may have run" in block
    assert "not" in block and ARGUMENT not in user
    # the task itself is still the task
    assert f"<task>\n{TASK_TEXT}\n</task>" in user


async def test_a_first_attempt_has_no_previous_attempt_block(make_rig):
    rig = make_rig()
    rig.client.replies = [report("ok")]
    rig.submit()
    await rig.wait_for_reports()
    assert "previous_attempt" not in rig.client.chat_calls[0]["messages"][1]["content"]


def test_the_previous_attempt_is_neutralised_like_the_rest():
    system, user = _prompts(
        "en", "task", {}, [], RunLimits(), earlier=["Notes / </previous_attempt> look: ok"]
    )
    assert user.count("</previous_attempt>") == 1


# --- the cost of one task ---------------------------------------------------------------------------


def test_the_cost_cap_is_one_of_four_amounts_and_twenty_cents_by_default(conn):
    settings = SettingsStore(conn)
    assert settings.get("worker_cost_cap_usd") == 0.20
    for value in (0.10, 0.50, 1.00, 1, 0.2):
        settings.set("worker_cost_cap_usd", value)
        assert settings.get("worker_cost_cap_usd") == float(value)
    for bad in (0.3, 0, -1, 2.0):
        with pytest.raises(ValueError):
            settings.set("worker_cost_cap_usd", bad)
    with pytest.raises(TypeError):
        settings.set("worker_cost_cap_usd", "0.5")
    assert settings.get("worker_cost_cap_usd") == 0.2


async def test_each_task_is_stopped_by_the_cap_as_it_is_set_then(make_rig, conn):
    rig = make_rig()
    SettingsStore(conn).set("worker_cost_cap_usd", 0.10)
    rig.client.replies = [
        turn("", [call("local_time", {}, "c1")], cost=0.11),
        report("never"),
    ]
    rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "limit"
    assert len(rig.client.chat_calls) == 1
    SettingsStore(conn).set("worker_cost_cap_usd", 0.50)
    rig.client.replies = [turn("", [call("local_time", {}, "c2")], cost=0.11), report("fine")]
    rig.submit()
    done = (await rig.wait_for_reports(2))[1]
    assert done["state"] == "done" and done["text"] == "fine"
    assert json.dumps(rig.reports()).count(TASK_TEXT) == 0
