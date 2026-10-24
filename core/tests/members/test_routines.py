"""Tests for the scheduler of the routines: what runs when, at most once, and what a run leaves in
its thread."""

import asyncio

from raphael_core.agent.history import AgentHistory
from raphael_core.members.service import CATCH_UP_MS, TICK_S

HOUR = 3_600_000


async def make(rig, member_id=None, schedule=None, **fields):
    if member_id is None:
        member_id = (await rig.call("members.create", {"name": "A", "model": "own/model"}))["member"]["id"]
    params = {
        "member_id": member_id, "title": "晨報", "prompt": "PRIVATE-PROMPT 看今天的進度",
        "schedule": schedule or {"kind": "daily", "time": "09:00"}, **fields,
    }  # fmt: skip
    return (await rig.call("routines.create", params))["routine"]


async def test_a_due_routine_runs_once_as_a_task_of_its_member_and_is_moved_on_first(rig, conn):
    routine = await make(rig)
    member_id = routine["member_id"]
    assert rig.service.tick() == []  # 08:59: not yet
    rig.wall.now += 60_000
    assert rig.service.tick() == [routine["id"]]
    assert rig.service.tick() == []  # already moved to tomorrow: it runs at most once
    (task,) = rig.background.submitted
    assert task == {
        "session_id": f"routine:{routine['id']}", "text": "PRIVATE-PROMPT 看今天的進度",
        # a model kept on the member is not used: the team answers with Raphael's
        "language": "zh-TW", "model": "openai/gpt-6-luna", "member_id": member_id, "routine_id": routine["id"],
    }  # fmt: skip
    moved = rig.service.store.routine(routine["id"])
    assert moved.last_run_ms == rig.wall.now
    assert moved.next_run_ms == rig.wall.now + 24 * HOUR
    # The run is a turn of the member's thread, with the routine's title, completed by the task.
    (turn,) = AgentHistory(conn).list(member_id=member_id)
    assert (turn.route, turn.source, turn.question, turn.status) == ("routine", "routine", "晨報", "running")
    (attached,) = rig.background.attached
    assert attached[1] == turn.id
    assert ("agent.turn", {"turn_id": turn.id, "member_id": member_id}) in rig.notes
    listed = {m["id"]: m for m in (await rig.call("members.list"))["members"]}
    assert listed[member_id]["unread"] and listed[member_id]["preview"] == "晨報"


async def test_raphael_has_routines_too_run_on_the_answer_model(rig, conn):
    routine = await make(rig, member_id=0, schedule={"kind": "every", "hours": 2})
    rig.wall.now += 2 * HOUR
    assert rig.service.tick() == [routine["id"]]
    (task,) = rig.background.submitted
    assert task["member_id"] == 0 and task["model"]  # the answer model, from the settings


async def test_a_routine_missed_while_the_core_was_off_runs_once_within_twelve_hours(rig):
    hourly = await make(rig, schedule={"kind": "every", "hours": 1})
    rig.wall.now += 5 * HOUR  # missed five times
    assert rig.service.tick() == [hourly["id"]]  # once, not five times
    assert rig.service.store.routine(hourly["id"]).next_run_ms == rig.wall.now + HOUR
    assert len(rig.background.submitted) == 1


async def test_a_routine_missed_longer_ago_is_not_run_only_moved_on(rig):
    daily = await make(rig)
    rig.wall.now += 60_000 + CATCH_UP_MS + 60_000  # due at nine, now past nine in the evening
    assert rig.service.tick() == []
    assert rig.background.submitted == []
    moved = rig.service.store.routine(daily["id"])
    assert moved.last_run_ms is None and moved.next_run_ms > rig.wall.now


async def test_a_routine_that_is_off_never_runs(rig):
    await make(rig, enabled=False)
    rig.wall.now += 2 * HOUR
    assert rig.service.tick() == []


async def test_a_run_the_worker_cannot_take_ends_its_turn_with_the_reason(rig, conn):
    routine = await make(rig)
    rig.background.refuse = "worker_busy"
    rig.wall.now += 60_000
    assert rig.service.tick() == [routine["id"]]
    (turn,) = AgentHistory(conn).list(member_id=routine["member_id"])
    assert (turn.status, turn.text) == ("error", "背景任務已滿。請稍後再試。")
    # It was moved on all the same: it does not run again until it is due again.
    assert rig.service.tick() == []


async def test_the_scheduler_ticks_while_the_core_runs_and_stops_with_it(rig, monkeypatch):
    routine = await make(rig)
    rig.wall.now += 60_000
    rig.service._tick_s = 0.01
    rig.service.start()
    rig.service.start()  # once is enough
    for _ in range(100):
        if rig.background.submitted:
            break
        await asyncio.sleep(0.01)
    assert [t["routine_id"] for t in rig.background.submitted] == [routine["id"]]
    await rig.service.aclose()
    assert TICK_S == 30.0
