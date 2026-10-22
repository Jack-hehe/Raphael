"""Tests for the background worker as a whole: a task handed over, carried out by a worker, reported.

The worker is a real `Worker` joined to the host by two queues (no process starts), except in the
tests that say so. The plugins are real ones; OpenRouter, Open-Meteo and the clock are fakes. No test
spends money or touches the network.
"""

import asyncio
import json
import logging

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.agent.persona import PREFIXES, phrase
from raphael_core.background.host import WorkerHost
from raphael_core.background.run import RunLimits
from raphael_core.background.service import MAX_QUEUED, report_from
from raphael_core.background.tasks import Refused
from raphael_core.brain.openrouter import OpenRouterError
from raphael_core.brain.settings import SettingsStore
from raphael_core.protocol import INVALID_PARAMS, NOT_FOUND, RpcError

from .conftest import ARGUMENT, RESULT, TASK_TEXT, call, report, turn, until


async def look_twice_then_report(rig):
    """The model asks for `look` once and reports; the plugin is connected at T0."""
    await rig.plugins.connect(look="T0")
    rig.client.replies = [
        turn("Looking.", [call("Notes__look", {"topic": ARGUMENT}, "c1")], cost=0.002),
        report("All looked up.", detail="Two things were found.", cost=0.003),
    ]


# --- a task from start to end ---------------------------------------------------------------------


async def test_a_task_is_carried_out_step_by_step_and_reported(make_rig, plugins, calls):
    rig = make_rig()
    await look_twice_then_report(rig)
    task_id = rig.submit()
    [done] = await rig.wait_for_reports()
    assert done == {"task_id": task_id, "state": "done", "prefix": "Notice", "text": "All looked up."}

    task = rig.task(task_id)
    assert (task.state, task.report_text, task.report_detail) == ("done", "All looked up.", "Two things were found.")
    assert (task.model_calls, task.tool_calls) == (2, 1)
    assert task.cost_usd == pytest.approx(0.005) and task.finished_at_ms and task.started_at_ms
    assert calls == ["look"]
    assert [(s.kind, s.tool, s.server, s.tier, s.outcome) for s in rig.steps(task_id)] == [
        ("model", None, None, None, "ok"),
        ("tool", "look", "Notes", "T1", "ok"),
        ("model", None, None, None, "ok"),
    ]


async def test_the_model_is_given_the_task_the_tools_of_the_snapshot_and_the_result_as_data(make_rig):
    rig = make_rig()
    await look_twice_then_report(rig)
    rig.submit()
    await rig.wait_for_reports()
    first, second = rig.client.chat_calls
    system, user = first["messages"]
    assert system["role"] == "system" and "background worker" in system["content"]
    assert "15 model calls" in system["content"] and "25 tool calls" in system["content"]
    assert user["role"] == "user" and f"<task>\n{TASK_TEXT}\n</task>" in user["content"]
    assert "2026-10-21 14:05" in user["content"] and user["content"].rstrip().endswith("English only.")
    assert [t["function"]["name"] for t in first["tools"]] == [
        "weather", "web_search", "local_time", "Notes__look",
    ]  # fmt: skip
    result = second["messages"][-1]
    assert result["role"] == "tool" and result["tool_call_id"] == "c1"
    assert result["content"].startswith('<tool_result untrusted="true" tool="Notes__look"')
    assert RESULT in result["content"]


async def test_every_call_is_audited_with_the_task_and_every_paid_call_is_spent(make_rig, plugins):
    rig = make_rig()
    await look_twice_then_report(rig)
    task_id = rig.submit()
    await rig.wait_for_reports()
    assert plugins.audit() == [("Notes", "look", None, "auto", "ok", task_id)]
    spent = rig.conn.execute("SELECT purpose, model, cost_usd FROM spend ORDER BY id").fetchall()
    assert [tuple(r) for r in spent] == [("agent", "fake/model", 0.002), ("agent", "fake/model", 0.003)]


async def test_the_lookups_of_raphael_are_calls_of_the_task_too(make_rig, plugins):
    rig = make_rig()
    rig.client.replies = [
        turn("", [call("weather", {"city": "Taipei"}, "w1"), call("local_time", {}, "t1")]),
        report("Fine weather."),
    ]
    task_id = rig.submit()
    await rig.wait_for_reports()
    assert plugins.audit() == [
        ("Raphael", "weather", "T0", "auto", "ok", task_id),
        ("Raphael", "local_time", "T0", "auto", "ok", task_id),
    ]


async def test_the_app_is_told_of_each_move_and_whether_a_task_is_under_way(make_rig):
    rig = make_rig()
    await look_twice_then_report(rig)
    task_id = rig.submit()
    await rig.wait_for_reports()
    await until(lambda: rig.changes()[-1]["running"] is False and rig.changes()[-1]["state"] == "done")
    states = [(c["state"], c["running"]) for c in rig.changes()]
    assert states[0] == ("queued", False)
    assert ("running", True) in states and states[-1] == ("done", False)
    assert {c["task_id"] for c in rig.changes()} == {task_id}
    last = rig.changes()[-1]
    assert (last["model_calls"], last["tool_calls"]) == (2, 1)
    # Nothing but numbers and states: the app is not sent what the task is or says in this message.
    assert TASK_TEXT not in json.dumps(rig.changes()) and "All looked up" not in json.dumps(rig.changes())


async def test_the_turn_of_the_history_that_handed_the_task_over_is_completed_with_the_report(
    make_rig, conn
):
    rig = make_rig()
    await look_twice_then_report(rig)
    history = AgentHistory(conn, rig.wall)
    turn_id = history.add(
        session_id="s1", question=TASK_TEXT, route="delegate", prefix="Understood",
        text="Handed to the background.", status="done", cost_usd=0.0001,
    )  # fmt: skip
    task_id = rig.submit()
    rig.service.attach_turn(task_id, turn_id)
    await rig.wait_for_reports()
    done = history.get(turn_id)
    assert (done.prefix, done.text, done.detail, done.status) == (
        "Notice", "All looked up.", "Two things were found.", "done",
    )  # fmt: skip
    assert done.cost_usd == pytest.approx(0.0001 + 0.005)
    assert done.question == TASK_TEXT


async def test_the_report_is_in_the_language_of_the_request(make_rig):
    rig = make_rig()
    await look_twice_then_report(rig)
    rig.client.replies[-1] = report("已整理完畢。", prefix="Notice")
    rig.submit(language="zh-TW")
    [done] = await rig.wait_for_reports()
    assert (done["prefix"], done["text"]) == ("告", "已整理完畢。")
    assert "Traditional Chinese" in rig.client.chat_calls[0]["messages"][0]["content"]


async def test_the_context_is_the_last_turns_of_the_conversation_without_the_task_itself(
    make_rig, conn
):
    rig = make_rig()
    history = AgentHistory(conn, rig.wall)
    for n in range(5):
        history.add(session_id="s", question=f"question {n}", route="chat", prefix="Answer",
                    text=f"answer {n}", status="done")  # fmt: skip
    own = history.add(session_id="s", question=TASK_TEXT, route="delegate", prefix="Understood",
                      text="Handed to the background.", status="done")  # fmt: skip
    rig.client.replies = [report()]
    task_id = rig.submit()
    rig.service.attach_turn(task_id, own)
    await rig.wait_for_reports()
    user = rig.client.chat_calls[0]["messages"][1]["content"]
    assert "question 2" in user and "question 4" in user and "question 1" not in user
    assert "Handed to the background." not in user


# --- the report the model writes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("final", "expected"),
    [
        ('{"prefix": "告", "text": "Done.", "detail": ""}', ("Notice", "Done.", None)),
        ('```json\n{"prefix": "Answer", "text": "Fenced.", "detail": "More."}\n```', ("Answer", "Fenced.", "More.")),
        ('Here it is: {"prefix": "否", "text": "Cannot.", "detail": ""} Bye.', ("Negative", "Cannot.", None)),
        ("A plain sentence.", ("Notice", "A plain sentence.", None)),
        ('{"text": "No prefix."}', ("Answer", "No prefix.", None)),
    ],
)
def test_the_report_of_the_model_is_read_in_the_shapes_it_comes_in(final, expected):
    reply = report_from(final, "en")
    assert (reply.prefix, reply.text, reply.detail) == expected


@pytest.mark.parametrize("final", ["", "   ", "{", '{"prefix": "告", "text": ""}', '```json\n{broken', "[]"])
def test_a_report_that_says_nothing_usable_is_none(final):
    assert report_from(final, "en") is None


def test_a_long_report_is_one_line_and_the_rest_goes_to_the_detail():
    reply = report_from(json.dumps({"prefix": "告", "text": "字" * 200, "detail": ""}), "zh-TW")
    assert len(reply.text) <= 81 and reply.detail == "字" * 200


async def test_a_report_with_no_words_is_the_fixed_line_that_the_task_is_complete(make_rig):
    rig = make_rig()
    rig.client.replies = [turn("   ")]
    rig.submit()
    [done] = await rig.wait_for_reports()
    assert (done["state"], done["text"]) == ("done", phrase("en", "worker_done"))


# --- the limits -----------------------------------------------------------------------------------


def endless(rig, count=40, **extra):
    """Model replies that ask for another lookup each time, for a task that would never end."""
    rig.client.replies = [
        turn("", [call("local_time", {"n": n}, f"c{n}")], cost=extra.get("cost", 0.001))
        for n in range(count)
    ]


async def test_at_fifteen_model_calls_the_task_stops_and_says_so_with_a_fixed_line(make_rig):
    rig = make_rig()
    endless(rig)
    task_id = rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "limit"
    assert done["text"] == phrase("en", "worker_limit_model", steps="15")
    assert done["prefix"] == "Notice"
    assert len(rig.client.chat_calls) == 15  # not one more, so not one more paid
    assert rig.task(task_id).reason == "limit_model"
    assert rig.conn.execute("SELECT count(*) FROM spend").fetchone()[0] == 15


async def test_at_twenty_five_tool_calls_the_task_stops_before_the_twenty_sixth(make_rig):
    rig = make_rig(limits=RunLimits(model_calls=40, tool_calls=25))
    rig.client.replies = [
        turn("", [call("local_time", {"n": 2 * n}, f"a{n}"), call("local_time", {"n": 2 * n + 1}, f"b{n}")])
        for n in range(20)
    ]
    task_id = rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "limit" and done["text"] == phrase("en", "worker_limit_tool", steps="25")
    assert rig.task(task_id).tool_calls == 25


async def test_the_money_one_task_may_spend_is_a_limit(make_rig):
    rig = make_rig()
    endless(rig, cost=0.09)
    task_id = rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "limit" and done["text"] == phrase("en", "worker_limit_cost", steps="3")
    assert len(rig.client.chat_calls) == 3  # 0.09, 0.18, 0.27: stopped before a fourth
    assert rig.task(task_id).cost_usd == pytest.approx(0.27)


async def test_the_month_limit_stops_a_task_in_the_middle(make_rig, conn):
    rig = make_rig()
    SettingsStore(conn).set("monthly_limit_usd", 0.5)
    rig.client.replies = [
        turn("", [call("local_time", {"n": 0}, "c0")], cost=0.05),
        lambda **kwargs: (
            rig.service._spend.record(purpose="cleanup", model="m", cost_usd=1.0, prompt_tokens=1, completion_tokens=1)
            or turn("", [call("local_time", {"n": 1}, "c1")], cost=0.01)
        ),
        turn("", [call("local_time", {"n": 2}, "c2")]),
    ]
    rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "limit" and done["text"] == phrase("en", "worker_limit_month", steps="2")
    assert len(rig.client.chat_calls) == 2


async def test_ten_minutes_is_a_limit_and_a_task_that_takes_longer_is_stopped(make_rig):
    rig = make_rig(limits=RunLimits(seconds=0.4))

    async def slow(**kwargs):
        await asyncio.sleep(30)

    rig.client.replies = [slow]
    rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "limit" and done["text"] == phrase("en", "worker_limit_time", steps="0")


async def test_a_worker_that_does_not_answer_in_time_is_ended_by_the_core_alone(make_rig, monkeypatch):
    import raphael_core.background.service as module

    monkeypatch.setattr(module, "TIME_NET_S", 0.3)

    class Stuck(WorkerHost):
        """A worker that never says how the task ended."""

        killed = False

        async def run(self, run, spec):
            await asyncio.sleep(30)

        def kill(self):
            self.killed = True

    host = Stuck()
    rig = make_rig(limits=RunLimits(seconds=0.2), host=host)
    rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "limit" and done["text"] == phrase("en", "worker_limit_time", steps="0")
    assert host.killed is True


# --- when the model fails -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "line"),
    [
        (OpenRouterError(401, "auth", "x"), "auth"),
        (OpenRouterError(402, "credits", "x"), "credits"),
        (OpenRouterError(0, "network", "x"), "offline"),
        (OpenRouterError(400, "bad_request", "x"), "error"),
        (OpenRouterError(404, "bad_request", "x"), "worker_model"),
        (RuntimeError("boom"), "error"),
    ],
)
async def test_a_model_that_fails_ends_the_task_with_the_reason_in_a_fixed_line(make_rig, error, line):
    rig = make_rig()
    rig.client.replies = [error]
    task_id = rig.submit()
    [done] = await rig.wait_for_reports()
    assert (done["state"], done["prefix"], done["text"]) == ("failed", "Negative", phrase("en", line))
    assert rig.task(task_id).state == "failed"


# --- stopping a task ------------------------------------------------------------------------------


async def test_a_task_the_user_stops_ends_at_once_with_what_was_done(make_rig, calls):
    rig = make_rig()
    await rig.plugins.connect(look="T0", slow="T0")
    rig.client.replies = [
        turn("", [call("Notes__look", {"topic": "a"}, "c1")]),
        turn("", [call("Notes__slow", {}, "c2")]),
        report("never"),
    ]
    task_id = rig.submit()
    await until(lambda: "slow" in calls)
    assert await rig.rpc.call("worker.cancel", {}) == {"cancelled": True}
    [done] = await rig.wait_for_reports()
    # The call that was under way may have been made: the report says it is not known.
    assert done["state"] == "cancelled"
    assert done["text"] == phrase("en", "worker_cancelled_unsure", steps="2")
    assert rig.task(task_id).state == "cancelled" and len(rig.client.chat_calls) == 2
    assert [s.outcome for s in rig.steps(task_id) if s.kind == "tool"] == ["ok"]


async def test_a_task_stopped_in_the_middle_of_a_model_call_has_nothing_cut_off(make_rig):
    rig = make_rig()
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(30)

    rig.client.replies = [slow]
    task_id = rig.submit()
    await asyncio.wait_for(started.wait(), 5)
    await rig.rpc.call("worker.cancel", {"task_id": task_id})
    [done] = await rig.wait_for_reports()
    assert done["text"] == phrase("en", "worker_cancelled", steps="0")


async def test_the_worker_comes_back_for_the_next_task_after_one_was_stopped(make_rig):
    rig = make_rig()
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(30)

    rig.client.replies = [slow, report("Second task.")]
    first = rig.submit()
    second = rig.submit()
    await asyncio.wait_for(started.wait(), 5)
    await rig.rpc.call("worker.cancel", {})
    reports = await rig.wait_for_reports(2)
    assert [r["state"] for r in reports] == ["cancelled", "done"]
    assert reports[1]["text"] == "Second task." and (first, second) == (reports[0]["task_id"], reports[1]["task_id"])


async def test_a_task_that_waits_can_be_taken_out_of_the_queue(make_rig):
    rig = make_rig()
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(30)

    rig.client.replies = [slow]
    rig.submit()
    waiting = rig.submit()
    await asyncio.wait_for(started.wait(), 5)
    assert await rig.rpc.call("worker.cancel", {"task_id": waiting}) == {"cancelled": True}
    [gone] = await rig.wait_for_reports()
    assert (gone["task_id"], gone["state"]) == (waiting, "cancelled")
    assert rig.task(waiting).started_at_ms is None  # it never started
    await rig.rpc.call("worker.cancel", {})
    await rig.wait_for_reports(2)
    assert len(rig.client.chat_calls) == 1  # only the first task ever asked the model


async def test_stopping_with_no_task_does_nothing(make_rig):
    rig = make_rig()
    assert await rig.rpc.call("worker.cancel", {}) == {"cancelled": False}
    assert await rig.rpc.call("worker.cancel", {"task_id": 99}) == {"cancelled": False}
    with pytest.raises(RpcError) as error:
        await rig.rpc.call("worker.cancel", {"task_id": "1"})
    assert error.value.code == INVALID_PARAMS


# --- the queue ------------------------------------------------------------------------------------


async def test_one_task_runs_at_a_time_three_wait_and_a_fifth_is_refused(make_rig):
    rig = make_rig()
    release = asyncio.Event()
    running = []
    most = []

    async def hold(**kwargs):
        running.append(1)
        most.append(len(running))
        await release.wait()
        running.pop()
        return report("ok")

    rig.client.replies = [hold, hold, hold, hold]
    ids = [rig.submit() for _ in range(1 + MAX_QUEUED)]
    with pytest.raises(Refused) as refused:
        rig.submit()
    assert refused.value.kind == "worker_busy"
    await until(lambda: len(rig.client.chat_calls) == 1)
    assert [rig.task(i).state for i in ids] == ["running", "queued", "queued", "queued"]
    release.set()
    reports = await rig.wait_for_reports(4)
    assert [r["task_id"] for r in reports] == ids  # in the order they came
    assert max(most) == 1  # never two at once
    rig.submit()  # the queue has room again
    rig.client.replies.append(report("again"))
    await rig.wait_for_reports(5)


# --- a worker that dies ---------------------------------------------------------------------------


def dying(rig):
    """A model reply during which the worker dies."""

    async def die(**kwargs):
        rig.links.last.kill()
        await asyncio.sleep(30)

    return die


async def test_a_worker_that_dies_interrupts_the_task_and_nothing_starts_it_again(make_rig):
    rig = make_rig()
    rig.client.replies = [turn("", [call("local_time", {}, "c0")]), dying(rig), report("never asked for")]
    task_id = rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "interrupted" and done["prefix"] == "Notice"
    assert done["text"] == phrase("en", "worker_interrupted", steps="1")
    await asyncio.sleep(0.3)
    assert len(rig.links.made) == 1 and len(rig.client.chat_calls) == 2
    assert rig.task(task_id).state == "interrupted" and rig.task(task_id).tool_calls == 1
    assert len(rig.reports()) == 1  # reported once


async def test_after_a_crash_the_next_task_gets_a_new_worker(make_rig):
    rig = make_rig()
    rig.client.replies = [dying(rig), report("Fine now.")]
    rig.submit()
    await rig.wait_for_reports()
    rig.submit()
    reports = await rig.wait_for_reports(2)
    assert reports[1]["state"] == "done" and len(rig.links.made) == 2


async def test_three_crashes_switch_the_worker_off_and_say_why(make_rig):
    rig = make_rig()
    rig.client.replies = [dying(rig), dying(rig), dying(rig), report("not reached")]
    for _ in range(3):
        rig.submit()
    reports = await rig.wait_for_reports(3)
    assert [r["state"] for r in reports] == ["interrupted"] * 3
    assert rig.host.disabled is True
    with pytest.raises(Refused) as refused:
        rig.submit()
    assert refused.value.kind == "worker_disabled"
    assert len(rig.links.made) == 3


async def test_tasks_still_queued_when_the_worker_is_switched_off_are_told_so(make_rig):
    rig = make_rig()
    rig.client.replies = [dying(rig), dying(rig), dying(rig)]
    ids = [rig.submit() for _ in range(4)]
    reports = await rig.wait_for_reports(4)
    assert [r["state"] for r in reports] == ["interrupted", "interrupted", "interrupted", "failed"]
    assert reports[3]["text"] == phrase("en", "worker_disabled") and reports[3]["prefix"] == "Negative"
    assert [r["task_id"] for r in reports] == ids


async def test_a_worker_that_cannot_start_is_a_failed_task_with_a_plain_line(make_rig):
    async def nothing():
        raise OSError("no such program")

    rig = make_rig(host=WorkerHost(connect=nothing))
    rig.submit()
    [done] = await rig.wait_for_reports()
    assert (done["state"], done["text"]) == ("failed", phrase("en", "error"))


# --- the old tasks, the methods of the app --------------------------------------------------------


async def test_tasks_left_unfinished_by_the_last_process_are_interrupted_when_the_service_starts(
    make_rig, conn
):
    first = make_rig()
    running, queued, done = first.submit(), first.submit(), first.submit()
    first.service._store.start(running)
    first.service._store.finish(done, "done", prefix="告", text="x", detail=None, reason=None)
    first.service._queue.clear()  # the process "ends" with two tasks open
    first.client.replies = []
    second = make_rig()
    assert second.task(running).state == "interrupted" and second.task(queued).state == "interrupted"
    assert second.task(done).state == "done"
    await asyncio.sleep(0.1)
    assert second.client.chat_calls == [] and second.notifications == []  # nothing was started again


async def test_the_app_lists_the_tasks_and_reads_one_with_its_steps_and_never_its_arguments(make_rig):
    rig = make_rig()
    await look_twice_then_report(rig)
    task_id = rig.submit()
    await rig.wait_for_reports()
    listed = (await rig.rpc.call("worker.list", {}))["tasks"]
    assert [t["id"] for t in listed] == [task_id] and listed[0]["state"] == "done"
    assert listed[0]["report"] == {"prefix": "Notice", "text": "All looked up.", "detail": "Two things were found."}
    got = (await rig.rpc.call("worker.get", {"id": task_id}))["task"]
    assert [(s["n"], s["kind"], s["tool"], s["tier"], s["outcome"]) for s in got["steps"]] == [
        (1, "model", None, None, "ok"),
        (2, "tool", "look", "T1", "ok"),
        (3, "model", None, None, "ok"),
    ]
    assert ARGUMENT not in json.dumps(got) and RESULT not in json.dumps(got)
    with pytest.raises(RpcError) as error:
        await rig.rpc.call("worker.get", {"id": 999})
    assert error.value.code == NOT_FOUND
    for method, params in (("worker.get", {"id": "1"}), ("worker.list", {"limit": "3"})):
        with pytest.raises(RpcError) as error:
            await rig.rpc.call(method, params)
        assert error.value.code == INVALID_PARAMS


async def test_old_tasks_are_pruned_and_cleared_but_one_that_runs_is_kept(make_rig):
    rig = make_rig()
    rig.client.replies = [report("one"), report("two")]
    first = rig.submit()
    await rig.wait_for_reports()
    rig.wall.now += 60_000
    second = rig.submit()
    await rig.wait_for_reports(2)
    rig.service.prune(rig.task(second).created_at_ms)
    assert rig.task(first) is None and rig.task(second) is not None
    rig.service.clear()
    assert rig.task(second) is None


async def test_the_tray_is_told_a_task_runs_while_another_one_waits(make_rig):
    rig = make_rig()
    release = asyncio.Event()

    async def hold(**kwargs):
        await release.wait()
        return report("ok")

    rig.client.replies = [hold, report("two")]
    first = rig.submit()
    await until(lambda: len(rig.client.chat_calls) == 1)
    second = rig.submit()
    queued = rig.changes()[-1]
    assert (queued["task_id"], queued["state"]) == (second, "queued")
    assert queued["running"] is True  # the first is still under way
    assert (queued["model_calls"], queued["tool_calls"]) == (0, 0)  # the counters are the running task's
    assert rig.task(first).state == "running"
    release.set()
    await rig.wait_for_reports(2)
    await until(lambda: rig.changes()[-1]["state"] == "done")
    assert rig.changes()[-1]["running"] is False


async def test_taking_a_waiting_task_out_of_the_queue_does_not_tell_the_tray_none_runs(make_rig):
    rig = make_rig()
    release = asyncio.Event()

    async def hold(**kwargs):
        await release.wait()
        return report("ok")

    rig.client.replies = [hold]
    rig.submit()
    waiting = rig.submit()
    await until(lambda: len(rig.client.chat_calls) == 1)
    await rig.rpc.call("worker.cancel", {"task_id": waiting})
    gone = [c for c in rig.changes() if c["task_id"] == waiting][-1]
    assert (gone["state"], gone["running"]) == ("cancelled", True)
    release.set()
    await rig.wait_for_reports(2)


async def test_clearing_the_history_cancels_the_tasks_that_wait_and_the_one_that_runs(make_rig, conn):
    rig = make_rig()
    history = AgentHistory(conn, rig.wall)
    started = asyncio.Event()

    async def hold(**kwargs):
        started.set()
        await asyncio.sleep(30)

    rig.client.replies = [hold]
    running = rig.submit()
    turn_id = history.add(
        session_id="s1", question=TASK_TEXT, route="delegate", prefix="Understood",
        text="Handed to the background.", status="done",
    )  # fmt: skip
    rig.service.attach_turn(running, turn_id)
    waiting = [rig.submit(), rig.submit()]
    await asyncio.wait_for(started.wait(), 5)

    history.clear()
    rig.service.clear()  # what agent.history.clear does
    assert all(rig.task(task_id) is None for task_id in waiting)  # gone at once
    assert rig.task(running).text == ""  # the running one keeps nothing of what was said
    await until(lambda: rig.task(running) is None)  # and is gone when it has stopped
    await until(lambda: rig.changes()[-1]["running"] is False)
    assert rig.steps(running) == []
    assert rig.reports() == []  # nothing is said about a task the user cleared
    assert history.get(turn_id) is None  # and nothing is saved to the history
    assert len(rig.client.chat_calls) == 1  # the ones that waited never started
    rig.client.replies = [report("later")]
    rig.submit()
    [later] = await rig.wait_for_reports()
    assert later["text"] == "later"  # the service goes on


async def test_the_service_shares_the_one_gate_it_was_given(make_rig):
    rig = make_rig()
    assert rig.service._gate is rig.gate and rig.gate.plugins is rig.plugins.hub


# --- the real worker process ----------------------------------------------------------------------


async def test_a_task_carried_out_by_a_real_worker_process(make_rig, plugins, calls):
    rig = make_rig(host=WorkerHost(grace_s=1.0))
    await plugins.connect(look="T0")
    rig.client.replies = [
        turn("", [call("Notes__look", {"topic": "alpha"}, "c1")]),
        turn("", [call("Notes__look", {"topic": "beta"}, "c2")]),
        report("Both looked up."),
    ]
    task_id = rig.submit()
    [done] = await rig.wait_for_reports(timeout=30)
    assert (done["state"], done["text"]) == ("done", "Both looked up.")
    assert calls == ["look", "look"]
    assert [row[5] for row in plugins.audit()] == [task_id, task_id]
    assert rig.host.pid is not None and rig.host.pid != __import__("os").getpid()


# --- what is logged -------------------------------------------------------------------------------


async def test_nothing_of_a_task_is_logged_whichever_way_it_ends(make_rig, caplog):
    rig = make_rig()
    await rig.plugins.connect(look="T0", broken="T0", slow="T0")
    secret_text = "PRIVATE-TASK-TEXT window title: Secret Plans - Notepad"
    scripts = [
        # done
        [turn("PRIVATE-THOUGHT", [call("Notes__look", {"topic": ARGUMENT}, "c1"), call("Notes__broken", {}, "c2")]),
         report("PRIVATE-REPORT", detail="PRIVATE-DETAIL")],
        # a model that fails
        [OpenRouterError(500, "server", "PRIVATE-ERROR-MESSAGE")],
        # a limit
        [turn("", [call("local_time", {"n": n}, f"c{n}")]) for n in range(16)],
        # a crash
        [dying(rig)],
    ]
    with caplog.at_level(logging.DEBUG):
        for replies in scripts:
            rig.client.replies = list(replies)
            before = len(rig.reports())
            rig.submit(secret_text)
            await rig.wait_for_reports(before + 1)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert logged  # it does log: ids, states, counts
    for private in ("PRIVATE", ARGUMENT, "Secret Plans", "Notepad", "found", "secret detail", "background worker"):
        assert private not in logged


async def test_the_prefixes_of_the_reports_are_the_persona_prefixes(make_rig):
    rig = make_rig()
    rig.client.replies = [report("ok")]
    rig.submit(language="ja")
    [done] = await rig.wait_for_reports()
    assert done["prefix"] == PREFIXES["ja"].notice == "告"


async def test_closing_the_service_interrupts_the_task_that_runs_and_the_ones_that_wait(
    make_rig, calls
):
    rig = make_rig()
    await rig.plugins.connect(slow="T0")
    rig.client.replies = [turn("", [call("Notes__slow", {}, "c1")])]
    running, waiting = rig.submit(), rig.submit()
    await until(lambda: "slow" in calls)
    await asyncio.wait_for(rig.service.aclose(), 10)
    assert rig.task(running).state == "interrupted" and rig.task(waiting).state == "interrupted"
    assert rig.task(running).reason == "closing" and rig.task(waiting).reason == "restart"
    assert rig.reports() == []  # nobody is left to report to
    assert rig.host.running is False and rig.service.running is False
    await rig.service.aclose()  # safe twice


async def test_stopping_a_session_stops_only_its_own_tasks(make_rig):
    rig = make_rig()
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(30)

    rig.client.replies = [slow]
    mine = rig.submit(session="other")
    queued = rig.submit(session="telegram:42")
    await asyncio.wait_for(started.wait(), 5)
    assert await rig.service.cancel_session("telegram:42") is True  # the queued one only
    [gone] = await rig.wait_for_reports()
    assert (gone["task_id"], gone["state"]) == (queued, "cancelled")
    assert rig.service.running and rig.task(mine).state == "running"
    assert await rig.service.cancel_session("telegram:42") is False
    assert await rig.service.cancel_session("other") is True
    await rig.wait_for_reports(2)


async def test_stopping_a_session_stops_the_task_that_runs_when_it_is_the_sessions(make_rig):
    rig = make_rig()
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(30)

    rig.client.replies = [slow]
    task_id = rig.submit(session="telegram:42")
    await asyncio.wait_for(started.wait(), 5)
    assert await rig.service.cancel_session("telegram:42") is True
    [done] = await rig.wait_for_reports()
    assert (done["task_id"], done["state"]) == (task_id, "cancelled")
