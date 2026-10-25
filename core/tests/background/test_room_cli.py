"""Room CLI turns use the same background task, tool gate and approval lifecycle."""
import asyncio
import pytest

from raphael_core.agent.cli.loop import CliLoop
from raphael_core.agent.history import AgentHistory
from raphael_core.background.run import RunLimits
from raphael_core.worker.loop import Outcome
from .conftest import Clock, until


def submit_room(rig):
    history = AgentHistory(rig.conn)
    turn = history.add(session_id="room:1:1:0", question="Plan", route="room", prefix="",
                       text="", status="running", source="room")
    rig.service.use_cli(object())
    task = rig.service.submit(session_id="room:1:1:0", text="Plan", language="en", model="cli:codex:gpt-5.6-terra")
    rig.service.attach_turn(task, turn)
    return task, turn


async def test_cli_room_turn_uses_shared_tools_and_keeps_private_sessions_separate(make_rig, monkeypatch):
    rig = make_rig()
    async def work(loop):
        assert loop.sessions.get(0, loop.model) is None
        assert loop.conversation_id.startswith("room-task-")
        assert not (await loop._tool("permission", "cli_permission", {"tool_name":"Bash"})).ok
        result = await loop._tool("time", "local_time", {})
        assert result.ok
        loop._queue.put_nowait(Outcome("done", final="The plan is ready."))
    monkeypatch.setattr(CliLoop, "_work", work)
    task, turn = submit_room(rig)
    await until(lambda: rig.task(task).state == "done")
    assert AgentHistory(rig.conn).get(turn).text == "The plan is ready."
    assert rig.task(task).tool_calls == 1
    assert rig.client.chat_calls == [] and rig.reports() == []


@pytest.mark.parametrize("approved", [True, False])
async def test_cli_room_approval_resumes_same_turn_without_bypassing_gate(make_rig, monkeypatch, calls, approved):
    rig = make_rig()
    await rig.plugins.connect(look="T2")
    results = []
    async def work(loop):
        results.append(await loop._tool("lookup", "Notes__look", {"topic":"plan"}))
        loop._queue.put_nowait(Outcome("done", final="Finished after the owner's decision."))
    monkeypatch.setattr(CliLoop, "_work", work)
    task, turn = submit_room(rig)
    await until(lambda: rig.task(task).state == "waiting")
    assert calls == []
    [question] = rig.service.waiting_questions()
    assert rig.service.answer(task, question["id"], approved) == ("approved" if approved else "denied")
    await until(lambda: rig.task(task).state == "done")
    assert calls == (["look"] if approved else [])
    assert results[0].ok is approved
    assert AgentHistory(rig.conn).get(turn).status == "done"
    await until(lambda: not rig.service._cli_loops)
    assert not rig.service.waiting_questions()


async def test_cli_room_timeout_and_cancel_are_worker_terminal_states(make_rig, monkeypatch):
    rig = make_rig(limits=RunLimits(seconds=.05))
    async def work(loop):
        await asyncio.Event().wait()
    monkeypatch.setattr(CliLoop, "_work", work)
    task, _ = submit_room(rig)
    await until(lambda: rig.task(task).state == "limit")
    assert not rig.service._cli_loops
    task, _ = submit_room(rig)
    await until(lambda: rig.task(task).state == "running")
    await rig.service._cancel({"task_id":task})
    await until(lambda: rig.task(task).state == "cancelled")
    assert not rig.service._cli_loops


async def test_cli_room_expired_approval_cancels_the_waiting_loop(make_rig, monkeypatch):
    from raphael_core.background.service import QUESTION_WAIT_S
    clock = Clock()
    rig = make_rig(clock=clock)
    await rig.plugins.connect(look="T2")
    async def work(loop):
        await loop._tool("lookup", "Notes__look", {"topic":"plan"})
    monkeypatch.setattr(CliLoop, "_work", work)
    task, _ = submit_room(rig)
    await until(lambda: rig.task(task).state == "waiting")
    loop = rig.service._cli_loops[task]
    clock.now += QUESTION_WAIT_S + 1
    rig.service.sweep_questions()
    await until(lambda: loop.task.done())
    assert rig.task(task).state == "cancelled"
    assert not rig.service._cli_loops and not rig.service.waiting_questions()
