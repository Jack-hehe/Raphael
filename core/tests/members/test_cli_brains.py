import json
import os
import sys
import asyncio

import pytest

from raphael_core.agent.cli.discovery import CliBrains
from .test_answering import client, setup, member, typed  # noqa: F401


def fake_brain(rig, tmp_path, script):
    path = tmp_path / "fake.py"
    path.write_text(script, encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").touch()
    rig.service._cli_brains = CliBrains(home=home, environ={**os.environ, "PYTHONUTF8": "1"}, commands={"claude": [sys.executable, str(path)]})


async def test_cli_member_uses_persona_records_zero_cost_and_resumes(setup, tmp_path, client):
    rig, team = setup
    teacher = member(team, model="cli:claude:sonnet")
    fake_brain(rig, tmp_path, '''import json, sys
from pathlib import Path
assert "PRIVATE-INSTRUCTIONS" in Path(sys.argv[sys.argv.index("--append-system-prompt-file")+1]).read_text(encoding="utf-8")
json.loads(sys.stdin.readline())
if "--resume" in sys.argv:
    assert sys.argv[sys.argv.index("--resume")+1] == "thread-one"
print(json.dumps({"type":"result","subtype":"success","session_id":"thread-one","result":"Hello\\n\\nNext lesson.","usage":{"input_tokens":3,"output_tokens":5}}), flush=True)
''')
    for _ in range(2):
        result = await typed(rig, "Teach me", teacher.id)
        assert result["text"] == "Next lesson."
    turns = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
    assert all(turn["model"] == "cli:claude:sonnet" for turn in turns)
    assert [message["text"] for message in turns[0]["messages"] if message["kind"] == "reply"] == ["Hello", "Next lesson."]
    rows = rig.conn.execute("SELECT cost_usd, purpose, model FROM spend WHERE model LIKE 'cli:%'").fetchall()
    assert [tuple(row) for row in rows] == [(0, "agent", "cli:claude:sonnet")] * 2
    assert client.calls == 0 and not client.chat_calls


async def test_missing_cli_is_a_clear_reply_and_event_without_api_key(setup, tmp_path):
    rig, team = setup
    teacher = member(team, model="cli:codex")
    rig.keys.set("")
    rig.service._cli_brains = CliBrains(home=tmp_path, environ={}, commands={})
    result = await typed(rig, "Teach me", teacher.id)
    assert "Codex CLI" in result["text"] and "安裝" in result["text"]
    turn = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"][0]
    assert turn["status"] == "error"
    assert [message["kind"] for message in turn["messages"]] == ["event", "reply"]


@pytest.mark.parametrize("decision", ["yes", "no", "always"])
async def test_cli_native_permission_reuses_inline_cards(setup, tmp_path, decision):
    rig, team = setup
    teacher = member(team, model="cli:codex")
    path = tmp_path / "fake_codex.py"
    path.write_text('''import json, sys
def send(packet): print(json.dumps(packet), flush=True)
turn = None
for line in sys.stdin:
    packet=json.loads(line)
    method=packet.get("method")
    if method=="initialize": send({"id":packet["id"],"result":{}})
    elif method=="thread/start":
        assert "PRIVATE-INSTRUCTIONS" in packet["params"]["developerInstructions"]
        assert packet["params"]["sandbox"]=="read-only"
        send({"id":packet["id"],"result":{"thread":{"id":"one"}}})
    elif method=="turn/start":
        send({"id":packet["id"],"result":{"turn":{"id":"turn"}}})
        send({"method":"item/agentMessage/delta","params":{"delta":"Checking the action."}})
        send({"id":"permit","method":"item/commandExecution/requestApproval","params":{"command":"echo hello"}})
    elif packet.get("id")=="permit":
        decision=packet["result"]["decision"]
        send({"method":"item/agentMessage/delta","params":{"delta":decision}})
        send({"method":"turn/completed","params":{"turn":{"status":"completed"}}})
''', encoding="utf-8")
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").touch()
    rig.service._cli_brains = CliBrains(home=home, environ={**os.environ, "PYTHONUTF8": "1"}, commands={"codex": [sys.executable, str(path)]})
    pending = await typed(rig, "Say hello", teacher.id)
    assert pending["status"] == "confirm", pending
    result = await rig.call("agent.confirm", {"id": pending["confirm"]["id"], "approved": decision != "no", "always": decision == "always"})
    assert result["text"] == {"yes": "accept", "no": "decline", "always": "acceptForSession"}[decision]
    turn = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"][0]
    assert turn["id"] == pending["turn_id"] and turn["status"] == "done"
    assert any(step["tool"] == "cli_permission" for step in turn["steps"])
    assert any(msg["kind"] == "progress" and msg["text"] == "Checking the action." for msg in turn["messages"])
    assert not rig.service._cli_loops


async def test_mcp_routine_call_keeps_member_scope_and_safety_approval(setup, tmp_path, monkeypatch):
    from raphael_core.agent.safety import Screening
    rig, team = setup
    teacher = member(team, model="cli:claude")
    fake_brain(rig, tmp_path, '''import json, sys, urllib.request
from pathlib import Path
config=json.loads(Path(sys.argv[sys.argv.index("--mcp-config")+1]).read_text())["mcpServers"]["raphael"]
json.loads(sys.stdin.readline())
print(json.dumps({"type":"assistant","message":{"content":[{"type":"text","text":"Preparing the lesson."}]}}),flush=True)
packet={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"routine_create","arguments":{"title":"Lesson","prompt":"Teach Japanese","schedule":{"kind":"daily","time":"08:00"}}}}
request=urllib.request.Request(config["url"],data=json.dumps(packet).encode(),headers={**config["headers"],"Content-Type":"application/json"})
response=json.load(urllib.request.urlopen(request,timeout=15))
assert not response["result"]["isError"]
print(json.dumps({"type":"result","subtype":"success","result":"Lesson ready."}),flush=True)
''')
    async def screen(*args, **kwargs):
        return Screening(verdict="ask", rule="Ask before routine changes")
    monkeypatch.setattr(rig.service._gate, "screen", screen)
    pending = await typed(rig, "Make a lesson", teacher.id)
    assert pending["status"] == "confirm" and team.members.store.routines(teacher.id) == [], pending
    result = await rig.confirm(pending["confirm"]["id"])
    assert result["text"] == "Lesson ready."
    assert len(team.members.store.routines(teacher.id)) == 1 and not team.members.store.routines(0)
    turn = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"][0]
    assert any(msg["kind"] == "event" and "Lesson" in msg["text"] for msg in turn["messages"])


async def test_rpc_detection_is_registered_without_exposing_process_details(setup, tmp_path):
    rig, _ = setup
    fake_brain(rig, tmp_path, "print('fake-cli 1.0')")
    result = await rig.call("brains.cli", {})
    assert len(result["clis"]) == 3 and result["clis"][0]["installed"]
    assert str(tmp_path) not in json.dumps(result)


async def test_raphaels_voice_keeps_fast_routing_and_uses_cli_for_the_answer(setup, tmp_path, client):
    from agent.conftest import decision
    rig, _ = setup
    rig.settings.set("agent_model", "cli:claude")
    fake_brain(rig, tmp_path, '''import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"type":"result","subtype":"success","result":"Answer from subscription."}),flush=True)
''')
    client.decisions.append(decision("chat"))
    result = await rig.ask("Explain it")
    assert result["text"] == "Answer from subscription."
    assert len(client.decide_calls) == 1 and client.decide_calls[0]["model"] != "cli:claude"
    assert not client.chat_calls and not client.json_calls


async def test_not_logged_in_never_starts_the_fake_cli(setup, tmp_path):
    rig, team = setup
    teacher = member(team, model="cli:gemini")
    rig.service._cli_brains = CliBrains(home=tmp_path, environ={}, commands={"gemini": ["this-must-not-run"]})
    result = await typed(rig, "Hello", teacher.id)
    assert result["text"] == "請先在設定 › 模型與用量登入 Gemini CLI。"


async def test_crash_does_not_publish_diagnostics_or_private_reasoning(setup, tmp_path, caplog):
    rig, team = setup
    teacher = member(team, model="cli:claude")
    fake_brain(rig, tmp_path, '''import json, sys
json.loads(sys.stdin.readline())
print("PRIVATE-DIAGNOSTIC-SECRET",file=sys.stderr)
print(json.dumps({"type":"assistant","message":{"content":[{"type":"thinking","thinking":"PRIVATE-REASONING"}]}}),flush=True)
sys.exit(1)
''')
    result = await typed(rig, "Hello", teacher.id)
    assert "登入狀態" in result["text"]
    history = await rig.call("agent.history.list", {"member_id": teacher.id})
    assert "PRIVATE-DIAGNOSTIC-SECRET" not in json.dumps(history) + caplog.text
    assert "PRIVATE-REASONING" not in json.dumps(history) + caplog.text


async def test_clearing_history_discards_cli_resume_ids(setup, tmp_path):
    rig, _ = setup
    sessions = rig.service._cli_sessions
    sessions.put(0, "cli:claude", "one")
    await rig.call("agent.history.clear", {})
    assert sessions.get(0, "cli:claude") is None


def fake_mcp(rig, tmp_path, calls, *, allowed=True):
    fake_brain(rig, tmp_path, '''import json, os, sys, urllib.request
from pathlib import Path
config_path=Path(sys.argv[sys.argv.index("--mcp-config")+1])
config=json.loads(config_path.read_text())["mcpServers"]["raphael"]
json.loads(sys.stdin.readline())
for number, (name, arguments) in enumerate(CALLS,1):
    packet={"jsonrpc":"2.0","id":number,"method":"tools/call","params":{"name":name,"arguments":arguments}}
    request=urllib.request.Request(config["url"],data=json.dumps(packet).encode(),headers={**config["headers"],"Content-Type":"application/json"})
    response=json.load(urllib.request.urlopen(request,timeout=15))
    if name=="approve":
        content=json.loads(response["result"]["content"][0]["text"])
        assert content["behavior"]==("allow" if ALLOWED else "deny")
        if ALLOWED=="always":
            assert content["updatedPermissions"][0]["destination"]=="session"
            assert content["updatedPermissions"][0]["rules"]==[{"toolName":arguments["tool_name"]}]
    else:
        assert response["result"]["isError"] is (not ALLOWED)
print(json.dumps({"type":"result","subtype":"success","result":"Finished."}),flush=True)
'''.replace("CALLS", repr(calls)).replace("ALLOWED", repr(allowed)))


@pytest.mark.parametrize("approved", [True, False, "always"])
async def test_claude_permission_prompt_tool_round_trip(setup, tmp_path, approved):
    rig, team = setup
    teacher = member(team, model="cli:claude")
    fake_mcp(rig, tmp_path, [("approve", {"tool_name": "Write", "input": {"path": "lesson.txt"}})], allowed=approved)
    pending = await typed(rig, "Make a lesson", teacher.id)
    assert pending["status"] == "confirm"
    assert (await rig.call("agent.confirm", {"id": pending["confirm"]["id"], "approved": bool(approved), "always": approved == "always"}))["text"] == "Finished."


@pytest.mark.parametrize("remember", [True, False])
async def test_mcp_memory_write_requires_owner_request_and_is_scoped(setup, tmp_path, remember):
    rig, team = setup
    teacher = member(team, model="cli:claude")
    args = {"name": "preference-examples", "type": "preference", "description": "Learning style", "body": "The owner prefers examples.", "space": "own"}
    fake_mcp(rig, tmp_path, [("memory_write", args)], allowed=remember)
    result = await typed(rig, "Remember that I prefer examples" if remember else "Give an example", teacher.id)
    assert result["text"] == "Finished."
    assert bool(team.memory.store_for(teacher.id).get("preference-examples")) is remember
    assert team.memory.store_for(0).get("preference-examples") is None
    turn = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"][0]
    assert any(step["tool"] == "memory_write" for step in turn["steps"])


@pytest.mark.parametrize("stop", ["cancel", "expired", "deadline"])
async def test_paused_cli_is_reaped_and_mcp_closes(setup, tmp_path, monkeypatch, stop):
    from pathlib import Path
    import httpx
    from agent.test_cli_brains import alive
    from raphael_core.agent.cli import loop as cli_loop
    if stop == "deadline":
        monkeypatch.setattr(cli_loop, "CONFIRM_TIMEOUT_S", .05)
    rig, team = setup
    teacher = member(team, model="cli:claude")
    fake_mcp(rig, tmp_path, [("approve", {"tool_name": "Write", "input": {"path": "lesson.txt"}})])
    pending = await typed(rig, "Make a lesson", teacher.id)
    assert pending["status"] == "confirm"
    [active] = rig.service._cli_loops
    pid = active.driver.child.process.pid
    config = Path(active.driver.child.command[active.driver.child.command.index("--mcp-config") + 1])
    url = json.loads(config.read_text())["mcpServers"]["raphael"]["url"]
    if stop == "cancel":
        await rig.call("agent.cancel", {"session_id": "s1"})
    else:
        rig.clock.now += 31
        if stop == "expired":
            await rig.confirm(pending["confirm"]["id"])
        else:
            async with asyncio.timeout(5):
                while rig.service._cli_loops:
                    await asyncio.sleep(.01)
    assert not alive(pid) and not config.parent.exists()
    assert not rig.service._cli_loops
    async with httpx.AsyncClient() as http:
        with pytest.raises(httpx.ConnectError):
            await http.post(url, json={})


async def test_spoken_cli_reply_retains_japanese_without_showing_json(setup, tmp_path):
    from raphael_core.agent.cli.loop import CliLoop
    from raphael_core.worker.loop import Outcome
    # Exercise the same reply boundary used by Raphael's optional voice renderer.
    loop = CliLoop(brains=None, sessions=None, member_id=0, model="cli:claude", ledger=None,
                   language="zh-TW", plain=False, system="persona", user="hello", tools=[],
                   progress=lambda _: None, thinking=lambda _: None, event=lambda _: None,
                   execute=None, spoken=True)
    reply = loop.reply(Outcome("done", final=json.dumps({"text": "明天練習。", "messages": ["明天練習。"], "say_ja": "明日練習します。"})))
    assert reply.text == "明天練習。" and reply.say_ja == "明日練習します。"


async def test_mcp_memory_write_safety_approval_is_audited_as_user(setup, tmp_path, monkeypatch):
    from raphael_core.agent.safety import Screening
    rig, team = setup
    teacher = member(team, model="cli:claude")
    args = {"name": "preference-examples", "type": "preference", "description": "Learning style", "body": "The owner prefers examples.", "space": "own"}
    fake_mcp(rig, tmp_path, [("memory_write", args)])
    async def screen(*args, **kwargs):
        return Screening(verdict="ask", rule="Ask before memory changes")
    monkeypatch.setattr(rig.service._gate, "screen", screen)
    pending = await typed(rig, "Remember that I prefer examples", teacher.id)
    assert pending["status"] == "confirm"
    assert team.memory.store_for(teacher.id).get("preference-examples") is None
    assert (await rig.confirm(pending["confirm"]["id"]))["text"] == "Finished."
    [audit] = rig.conn.execute("SELECT approver FROM tool_audit WHERE tool='memory_write'").fetchall()
    assert audit[0] == "user"
