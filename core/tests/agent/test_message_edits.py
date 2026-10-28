"""Owner message retraction and replacement through the conversation RPC boundary."""

import asyncio

import pytest

from raphael_core.background.tasks import TaskStore
from raphael_core.protocol import RpcError
from .conftest import FakeActions, FakeOpenRouter
from .test_service import Rig
from .test_narration import chat


@pytest.fixture
async def rig(conn):
    made = Rig(conn, FakeOpenRouter(), FakeActions())
    yield made
    await made.aclose()


async def test_retraction_cancels_a_running_request_and_removes_it_after_reload(rig):
    entered = asyncio.Event()

    async def waiting(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    rig.client.chat_tools = waiting
    request = asyncio.create_task(rig.call("agent.ask", {
        "session_id": "draft-1", "text": "Typo", "ui_language": "en", "source": "app",
    }))
    await entered.wait()
    [turn] = (await rig.call("agent.history.list", {}))["turns"]
    assert turn["status"] == "running"
    rig.history.add_message(turn["id"], "reply", "Partial answer")
    result = await rig.call("agent.history.retract", {"session_id": "draft-1", "member_id": 0})
    assert result["deleted"] == 1
    await request
    assert (await rig.call("agent.history.list", {}))["turns"] == []
    assert (await rig.call("agent.history.search", {"query": "Typo"}))["turns"] == []
    assert rig.history.messages([turn["id"]]) == {}


async def test_retraction_before_delivery_prevents_a_late_request(rig):
    await rig.call("agent.history.retract", {"session_id": "late", "member_id": 0})
    await rig.call("agent.ask", {"session_id": "late", "text": "Never sent", "ui_language": "en", "source": "app"})
    assert (await rig.call("agent.history.list", {}))["turns"] == []
    assert rig.client.chat_calls == []


async def test_an_edit_replaces_the_old_turn_only_when_the_new_request_is_sent(rig):
    rig.client.chats.append(chat('{"prefix":"答","text":"Original answer","detail":"","memory":[]}'))
    old = await rig.call("agent.ask", {"session_id": "old", "text": "Original", "ui_language": "en", "source": "app"})
    assert len((await rig.call("agent.history.list", {}))["turns"]) == 1
    rig.client.chats.append(chat('{"prefix":"答","text":"Corrected answer","detail":"","memory":[]}'))
    await rig.call("agent.ask", {
        "session_id": "new", "text": "Corrected", "ui_language": "en", "source": "app",
        "replace_turn_id": old["turn_id"],
    })
    [turn] = (await rig.call("agent.history.list", {}))["turns"]
    assert turn["question"] == "Corrected" and turn["text"] == "Corrected answer"
    assert (await rig.call("agent.history.search", {"query": "Original"}))["turns"] == []


async def test_retraction_cannot_target_a_different_thread_or_session(rig):
    turn = rig.history.add(session_id="kept", question="Keep", route="chat", prefix="", text="Answer", status="done")
    with pytest.raises(RpcError) as error:
        await rig.call("agent.history.retract", {"session_id": "other", "turn_id": turn, "member_id": 0})
    assert error.value.code == -32602
    assert len((await rig.call("agent.history.list", {}))["turns"]) == 1


async def test_retraction_detaches_old_worker_history_before_sqlite_reuses_an_id(rig, conn):
    old = rig.history.add(session_id="old", question="Old", route="delegate", prefix="", text="Done", status="done")
    tasks = TaskStore(conn)
    task = tasks.create(session_id="old", text="Old task", language="en")
    tasks.attach_turn(task, old)
    await rig.call("agent.history.retract", {"session_id": "old", "turn_id": old, "member_id": 0})
    new = rig.history.add(session_id="new", question="New", route="chat", prefix="", text="New answer", status="done")
    assert tasks.for_turns([new]) == {}
