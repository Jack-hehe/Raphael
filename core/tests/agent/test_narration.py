"""Live turn sequences through the request, history and confirmation APIs."""

import asyncio
import json

import pytest

from raphael_core.brain.openrouter import ChatToolCall, ChatTurn
from .conftest import decision
from .test_service import make_rig  # noqa: F401
from .test_plugins import plugins, calls  # noqa: F401


def chat(text, *calls):
    return ChatTurn(text, [ChatToolCall(str(n), name, args, json.dumps(args)) for n, (name, args) in enumerate(calls)], "fake/model", 0.3, 10, 10, 1)


async def typed(rig, text="Check the time", member_id=0):
    return await rig.call("agent.ask", {"session_id": "live", "text": text, "ui_language": "en", "source": "app", "member_id": member_id})


async def test_progress_is_persisted_and_notified_before_each_tool_round(make_rig, client):
    notes = []
    rig = make_rig(notify=lambda method, data: notes.append((method, data)))
    client.decisions.append(decision("answer", time=0.95))

    async def second(**kwargs):
        [turn] = await rig.turns()
        assert turn["status"] == "running"
        assert [m["kind"] for m in turn["messages"]] == ["progress"]
        assert turn["messages"][0]["text"] == "《Notice. I will check the current time.》"
        assert kwargs["messages"][-1]["role"] == "tool"
        assert any(m.get("content") == "I will check the current time." for m in kwargs["messages"])
        return chat("I will confirm the date too.", ("local_time", {}))

    client.chats = [
        chat("I will check the current time.", ("local_time", {})), second,
        chat(json.dumps({"prefix": "Answer", "text": "It is Friday.", "detail": ""})),
    ]
    result = await typed(rig)
    assert result["text"] == "It is Friday."
    [turn] = await rig.turns()
    assert [m["kind"] for m in turn["messages"]] == ["progress", "progress", "reply"]
    assert turn["messages"][-1]["text"] == "《Answer. It is Friday.》"
    assert len({data["turn_id"] for method, data in notes if method == "agent.turn"}) == 1
    assert turn["cost_usd"] >= 0.9


async def test_a_permission_pauses_the_sequence_and_yes_continues_the_same_turn(make_rig, plugins, calls, client):
    await plugins.connect(note="T2", look="T1")
    rig = make_rig(plugins=plugins.hub)
    client.decisions.append(decision("answer"))
    client.chats = [chat("I will save your example.", ("Notes__look", {"topic": "example"}), ("Notes__note", {"text": "example"})),
                    chat("I will check the saved example.", ("local_time", {})),
                    chat(json.dumps({"prefix": "Answer", "text": "Saved.", "detail": ""}))]
    asked = await typed(rig, "Save an example")
    assert asked["status"] == "confirm"
    assert calls == ["look"]
    done = await rig.confirm(asked["confirm"]["id"])
    assert done["turn_id"] == asked["turn_id"] and done["text"] == "Saved."
    assert calls == ["look", "note"]
    [turn] = await rig.turns()
    assert turn["status"] == "done"
    assert [m["kind"] for m in turn["messages"]] == ["progress", "progress", "reply"]
    assert turn["cost_usd"] == pytest.approx(0.90004)
    assert [m["role"] for m in client.chat_calls[1]["messages"]][-2:] == ["tool", "tool"]


async def test_a_no_finishes_the_sequence_without_making_the_tool_call(make_rig, plugins, calls, client):
    await plugins.connect(note="T2")
    rig = make_rig(plugins=plugins.hub)
    client.decisions.append(decision("answer"))
    client.chats = [chat("I will save your example.", ("Notes__note", {"text": "example"}))]
    asked = await typed(rig, "Save an example")
    await rig.confirm(asked["confirm"]["id"], approved=False)
    [turn] = await rig.turns()
    assert calls == [] and turn["status"] == "denied"
    assert turn["messages"][-1]["kind"] == "reply"


async def test_cancel_ends_a_running_turn_and_clearing_cannot_resurrect_it(make_rig, client):
    rig = make_rig()
    waiting = asyncio.Event()

    async def slow(**kwargs):
        waiting.set()
        await asyncio.Event().wait()

    client.decisions.append(decision("answer"))
    client.chats = [chat("I will check the time.", ("local_time", {})), slow]
    task = asyncio.create_task(typed(rig))
    await asyncio.wait_for(waiting.wait(), 2)
    [turn] = await rig.turns()
    assert turn["status"] == "running"
    await rig.call("agent.cancel", {"session_id": "live"})
    await task
    assert (await rig.turns())[0]["status"] == "denied"
    await rig.call("agent.history.clear", {})
    assert await rig.turns() == []


async def test_a_resumed_turn_can_be_cancelled(make_rig, plugins, client):
    await plugins.connect(note="T2")
    rig = make_rig(plugins=plugins.hub)
    waiting = asyncio.Event()

    async def slow(**kwargs):
        waiting.set()
        await asyncio.Event().wait()

    client.decisions.append(decision("answer"))
    client.chats = [chat("I will save the example.", ("Notes__note", {"text": "example"})), slow]
    asked = await typed(rig)
    task = asyncio.create_task(rig.confirm(asked["confirm"]["id"]))
    await asyncio.wait_for(waiting.wait(), 2)
    await rig.call("agent.cancel", {"session_id": "live"})
    await task
    [turn] = await rig.turns()
    assert turn["status"] == "denied"
    assert turn["messages"][-1]["kind"] == "reply"
    assert turn["text"] and turn["cost_usd"] >= 0.3


async def test_a_confirmed_result_can_supply_the_address_for_the_next_round(make_rig, plugins, client, monkeypatch):
    from raphael_core.agent.tools import ToolResult

    await plugins.connect(look="T2")
    rig = make_rig(plugins=plugins.hub)
    opened = []
    address = "https://reference.example/guide"

    async def read(url):
        opened.append(url)
        return ToolResult("web_read", True, {"text": "The reference."})

    monkeypatch.setattr(rig.service._tools, "web_read", read)
    client.decisions.append(decision("answer"))
    client.chats = [chat("I will find the reference.", ("Notes__look", {"topic": address})),
                    chat("I will open that reference.", ("web_read", {"url": address})),
                    chat(json.dumps({"text": "Found.", "detail": ""}))]
    asked = await typed(rig, "Find and read the reference")
    await rig.confirm(asked["confirm"]["id"])
    assert opened == [address]


@pytest.mark.parametrize("wrap", ["```json\n{}\n```", "Here is the result: {}"])
async def test_final_json_wrapping_does_not_leak_into_the_reply(make_rig, client, wrap):
    rig = make_rig()
    client.decisions.append(decision("answer"))
    client.chats = [chat(wrap.format(json.dumps({"text": "Done.", "detail": ""})))]
    assert (await typed(rig))["text"] == "Done."
