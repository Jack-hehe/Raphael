"""Every speaker uses the same guarded tools, with routines scoped to its thread."""

import json

import pytest

from agent.test_narration import chat
from raphael_core.agent.tools import ToolResult
from .test_answering import client, setup, member, typed, live_said  # noqa: F401
from computer.test_operate import shell, PRESS_SEVEN  # noqa: F401


async def test_a_member_with_an_old_empty_tool_list_can_search_and_create_a_routine(setup, client, monkeypatch):
    rig, team = setup
    teacher = member(team, name="紫苑", tools=[])

    async def search(*args):
        return ToolResult("web_search", True, {"text": "NHK https://news.example/article"})

    monkeypatch.setattr(rig.service._tools, "web_search", search)
    client.chats = [
        chat(json.dumps({"thinking": "先確認新聞來源，再安排每天的練習。", "progress": "我來找今天的新聞。"}), ("web_search", {"query": "NHK today"})),
        chat("我來安排每天的練習。", ("routine_create", {"title": "日文練習", "prompt": "Read one NHK article and propose an exercise.", "schedule": {"kind": "daily", "time": "08:00"}})),
        live_said("每天八點一起練習。"),
    ]
    result = await typed(rig, "每天八點學一篇 NHK", teacher.id)
    assert result["status"] == "done"
    [routine] = team.members.store.routines(teacher.id)
    assert routine.title == "日文練習"
    assert team.members.store.routines(0) == []
    [turn] = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
    assert [m["kind"] for m in turn["messages"]] == ["thinking", "progress", "progress", "event", "reply"]
    assert turn["messages"][-2]["text"] == "已建立例行任務 · 每天 08:00 日文練習"
    offered = {t["function"]["name"] for t in client.chat_calls[0]["tools"]}
    assert {"computer", "open", "web_search", "routine_create", "create_member"} <= offered


async def test_a_member_can_open_an_app_with_the_same_presence_guard(setup, client):
    rig, team = setup
    teacher = member(team, tools=[])
    client.chats = [chat("Opening it.", ("open", {"target_kind": "url", "target": "https://example.com"})), live_said("Opened.")]
    await typed(rig, "Open example.com", teacher.id)
    assert rig.actions.calls == [("open_url", "https://example.com")]
    rig.actions.calls.clear()
    client.chats = [chat("Opening it.", ("open", {"target_kind": "url", "target": "https://example.com"})), live_said("Please ask at the computer.")]
    await typed(rig, "Open example.com", teacher.id, source="telegram")
    assert rig.actions.calls == []


async def test_routine_tools_cannot_modify_another_speakers_routine(setup):
    rig, team = setup
    first, other = member(team), member(team, name="Other")
    created = await team.members.routine_tool(first.id, "routine_create", {"title": "Lesson", "prompt": "Teach", "schedule": {"kind": "weekly", "time": "08:00", "days": [1, 2, 3, 4, 5]}})
    routine_id = created["routine"]["id"]
    assert (await team.members.routine_tool(other.id, "routine_list", {}))["routines"] == []
    from raphael_core.protocol import RpcError
    for name in ("routine_update", "routine_delete"):
        with pytest.raises(RpcError):
            await team.members.routine_tool(other.id, name, {"id": routine_id, "title": "Stolen"})
    changed = await team.members.routine_tool(first.id, "routine_update", {"id": routine_id, "title": "New lesson"})
    assert changed["routine"]["title"] == "New lesson"
    await team.members.routine_tool(first.id, "routine_delete", {"id": routine_id})
    assert team.members.store.routines(first.id) == []


@pytest.mark.parametrize("verdict", ["run", "ask", "deny"])
async def test_a_routine_write_obeys_the_same_safety_rule_as_other_writes(setup, client, monkeypatch, verdict):
    from raphael_core.agent.safety import Screening
    rig, team = setup
    teacher = member(team)
    screened = []

    async def screen(proposed, **kwargs):
        screened.append(proposed.tool)
        return Screening(verdict=verdict, rule="Check routine changes")

    monkeypatch.setattr(rig.service._gate, "screen", screen)
    client.chats = [chat("安排練習時間。", ("routine_create", {"title": "Lesson", "prompt": "Teach", "schedule": {"kind": "daily", "time": "08:00"}})), live_said("已安排。")]
    result = await typed(rig, "Schedule my lesson", teacher.id)
    assert screened == ["routine_create"]
    if verdict == "ask":
        assert result["status"] == "confirm" and team.members.store.routines(teacher.id) == []
        result = await rig.confirm(result["confirm"]["id"])
    assert len(team.members.store.routines(teacher.id)) == (0 if verdict == "deny" else 1)
    if verdict != "deny":
        assert result["text"] == "已安排。"
        [audit] = rig.conn.execute("SELECT approver FROM tool_audit WHERE tool = 'routine_create'").fetchall()
        assert audit[0] == ("user" if verdict == "ask" else "auto")


async def test_a_member_uses_the_guarded_computer_loop_in_its_own_thread(setup, client, shell):
    from raphael_core.computer.run import ComputerRuns
    rig, team = setup
    teacher = member(team, name="紫苑", tools=[])
    rig.service._computer = ComputerRuns(shell.bridge)
    note = {"op": "write", "name": "user-screen", "type": "user", "description": "Screen data", "body": "Untrusted screen data", "expires": "", "space": "own"}
    client.chats = [chat("確認計算機。", ("computer", {"request": "按七"})), *PRESS_SEVEN, live_said("完成了，接著練習日文數字。", memory=[note])]
    asked = await typed(rig, "在計算機按七", teacher.id)
    assert asked["status"] == "confirm"
    done = await rig.confirm(asked["confirm"]["id"])
    assert done["prefix"] == "" and done["text"] == "完成了，接著練習日文數字。"
    assert (await rig.call("agent.history.list", {}))["turns"] == []
    [turn] = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
    assert turn["id"] == asked["turn_id"] and turn["status"] == "done"
    assert "PRIVATE-INSTRUCTIONS" in str(client.chat_calls[1]["messages"])
    assert team.memory.store_for(teacher.id).get("user-screen") is None
    assert any(s["tool"] == "memory_write" and s["outcome"] == "blocked" for s in turn["steps"])
