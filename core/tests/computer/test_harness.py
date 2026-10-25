"""The interactive harness resumes after the existing desktop permission flow."""

import asyncio

from agent.test_narration import chat
from .test_operate import operating, shell, PRESS_SEVEN  # noqa: F401


async def test_cli_computer_tool_uses_auxiliary_api_model_and_keeps_approval_flow(operating, client, shell, conn, tmp_path):
    from members.test_cli_brains import fake_mcp
    from raphael_core.brain.settings import SettingsStore

    settings = SettingsStore(conn)
    settings.set("agent_model", "cli:claude")
    settings.set("assistant_model", "anthropic/claude-haiku-5.5")
    fake_mcp(operating, tmp_path, [("computer", {"request": "Press seven in Calculator"})])
    client.chats = list(PRESS_SEVEN)
    asked = await operating.call("agent.ask", {"session_id": "cli-computer", "source": "app", "text": "Press seven", "ui_language": "en"})
    assert asked["status"] == "confirm"
    done = await operating.confirm(asked["confirm"]["id"])
    assert done["text"] == "Finished."
    assert client.chat_calls and all(call["model"] == "anthropic/claude-haiku-5.5" for call in client.chat_calls)
    assert any(operation == "act" for operation, _ in shell.requests)


async def test_desktop_permission_resumes_the_same_harness_turn(operating, client, shell):
    client.chats = [chat('先用計算機確認結果。', ('computer', {'request': '在小算盤按七'})), *PRESS_SEVEN, chat('{"text":"已按下七。","detail":""}')]
    asked = await operating.call('agent.ask', {'session_id':'live', 'source':'app', 'text':'在小算盤按七', 'ui_language':'zh-TW'})
    assert asked['status'] == 'confirm'
    done = await operating.confirm(asked['confirm']['id'])
    assert done['status'] == 'done' and done['turn_id'] == asked['turn_id']
    assert done['text'] == '已按下七。'
    [turn] = await operating.turns(quick=False)
    assert [m['kind'] for m in turn['messages']] == ['progress', 'reply']
    assert any(op == 'act' for op, args in shell.requests)
    assert client.chat_calls[-1]['messages'][-1]['role'] == 'tool'


async def test_cancel_a_desktop_permission_finishes_the_existing_turn_once(operating, client, shell):
    client.chats = [chat("Checking the calculator.", ("computer", {"request": "Press seven"})), *PRESS_SEVEN]
    asked = await operating.call("agent.ask", {"session_id": "live", "source": "app", "text": "Press seven", "ui_language": "en"})
    assert asked["status"] == "confirm"
    await operating.call("agent.cancel", {"session_id": "live"})
    run = operating.service._computer.for_question(asked["confirm"]["id"])
    if run and run.task:
        await run.task
    await asyncio.sleep(0)
    [turn] = await operating.turns(quick=False)
    assert turn["status"] == "denied"
    assert len([m for m in turn["messages"] if m["kind"] == "reply"]) == 1
    assert not any(op == "act" for op, args in shell.requests)


async def test_a_denied_desktop_permission_is_data_for_the_harness_not_a_second_turn(operating, client, shell):
    client.chats = [chat("Checking the calculator.", ("computer", {"request": "Press seven"})), *PRESS_SEVEN[:3], chat('{"text":"I left it unchanged.","detail":""}')]
    asked = await operating.call("agent.ask", {"session_id": "live", "source": "app", "text": "Press seven", "ui_language": "en"})
    await operating.confirm(asked["confirm"]["id"], approved=False)
    [turn] = await operating.turns(quick=False)
    assert turn["text"] == "I left it unchanged."
    assert not any(op == "act" for op, args in shell.requests)
