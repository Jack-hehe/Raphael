"""Room CLI turns use the same background task, tool gate and approval lifecycle."""
import asyncio
import pytest

from raphael_core.agent.cli.drivers import Event
from raphael_core.agent.cli.loop import CliLoop
from raphael_core.agent.history import AgentHistory
from raphael_core.background.run import RunLimits
from raphael_core.worker.loop import Outcome
from .conftest import Clock, until


def submit_room(rig):
    history = AgentHistory(rig.conn)
    with rig.conn:
        room = rig.conn.execute("INSERT INTO rooms(name,created) VALUES ('Room',1)").lastrowid
    thread = rig.conn.execute("SELECT id FROM room_threads WHERE room_id=?",(room,)).fetchone()[0]
    turn = history.add(session_id="room:1:1:0", question="Plan", route="room", prefix="",
                       text="", status="running", source="room", scope="room", room_id=room, thread_id=thread)
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


class Brains:
    def __init__(self, home):
        self.home, self.environ = home, {}

    def command(self, name):
        return ["never-spawn-real-CLI"]

    def available(self, name, *, command=None):
        return True


def scripted_cli(monkeypatch, *runs, waits=(0.0, 0.0)):
    """Each CLI run plays the next list of events (the last one repeats); returns the runs made."""
    played = []
    class Driver:
        def __init__(self, *args, **kwargs):
            pass
        async def run(self, **kwargs):
            played.append(kwargs)
            for event in runs[min(len(played), len(runs)) - 1]:
                yield event
        async def interrupt(self):
            pass
    monkeypatch.setattr("raphael_core.agent.cli.loop.Driver", Driver)
    monkeypatch.setattr("raphael_core.agent.cli.loop.RATE_LIMIT_WAITS_S", waits)
    return played


def progress(rig, task):
    return [params["text"] for method, params in rig.notifications if method == "worker.progress" and params["task_id"] == task]


async def test_cli_room_turn_waits_out_one_rate_limit_and_answers(make_rig, monkeypatch, tmp_path):
    rig = make_rig()
    played = scripted_cli(monkeypatch, [Event("error", "rate_limited")], [Event("final", "The plan is ready.")])
    task, turn = submit_room(rig)
    rig.service.use_cli(Brains(tmp_path))
    await until(lambda: rig.task(task).state in ("done", "failed"))
    assert rig.task(task).state == "done" and AgentHistory(rig.conn).get(turn).text == "The plan is ready."
    assert len(played) == 2 and progress(rig, task) == ["Codex CLI is rate-limited; retrying in 0 s."]


@pytest.mark.parametrize("first", [[], [Event("tool_start", data={"name": "local_time"})]])
async def test_cli_room_turn_reports_a_rate_limit_it_cannot_wait_out(make_rig, monkeypatch, tmp_path, first):
    rig = make_rig()
    played = scripted_cli(monkeypatch, [*first, Event("error", "rate_limited")])
    task, turn = submit_room(rig)
    rig.service.use_cli(Brains(tmp_path))
    await until(lambda: rig.task(task).state in ("done", "failed"))
    failed = rig.task(task)
    assert (failed.state, failed.reason) == ("failed", "cli_rate_limited")
    assert failed.report_text == "Codex CLI is rate-limited right now (too many requests). Try again in a little while."
    assert AgentHistory(rig.conn).get(turn).status == "error"
    assert len(played) == (1 if first else 3)


async def test_stopping_a_room_turn_while_it_waits_out_a_rate_limit_cancels_it(make_rig, monkeypatch, tmp_path):
    rig = make_rig()
    played = scripted_cli(monkeypatch, [Event("error", "rate_limited")], waits=(30.0, 60.0))
    task, _ = submit_room(rig)
    rig.service.use_cli(Brains(tmp_path))
    await until(lambda: progress(rig, task))
    await rig.service._cancel({"task_id": task})
    await until(lambda: rig.task(task).state == "cancelled")
    assert len(played) == 1 and not rig.service._cli_loops


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
