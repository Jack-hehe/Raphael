import json
import os
import sys
import asyncio
import base64

import pytest

from raphael_core.agent.cli.discovery import CliBrains
from .test_answering import client, setup, member, typed  # noqa: F401


@pytest.mark.parametrize("name", ["claude", "codex"])
async def test_cli_turn_delivers_only_current_images_natively(setup, tmp_path, client, name):
    rig, team = setup
    teacher = member(team, model=f"cli:{name}")
    image = base64.b64encode(b"\x89PNG\r\n\x1a\ncurrent-image").decode()
    fake_brain(rig, tmp_path, '''import base64, json, sys
from pathlib import Path
def send(value): print(json.dumps(value), flush=True)
if NAME == "claude":
    content = json.loads(sys.stdin.readline())["message"]["content"]
    if "--resume" in sys.argv:
        assert isinstance(content, str)
    else:
        assert isinstance(content, list)
        assert [block["type"] for block in content] == ["text", "image"]
        assert content[1]["source"]["data"] == IMAGE
        assert "read_attachment" in content[0]["text"]
    send({"type":"result","subtype":"success","session_id":"images-session","result":"I can see the image."})
else:
    resumed = False
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize": send({"id":msg["id"], "result":{}})
        elif msg.get("method") in ("thread/start", "thread/resume"):
            resumed = msg["method"] == "thread/resume"
            send({"id":msg["id"], "result":{"thread":{"id":"images-session"}}})
        elif msg.get("method") == "turn/start":
            content = msg["params"]["input"]
            if resumed:
                assert len(content) == 1
            else:
                assert len(content) == 2 and content[1]["type"] == "localImage"
                assert Path(content[1]["path"]).read_bytes() == base64.b64decode(IMAGE)
                assert "read_attachment" in content[0]["text"]
            send({"method":"item/completed", "params":{"item":{"type":"agentMessage", "id":"final", "phase":"final_answer", "text":"I can see the image."}}})
            send({"method":"turn/completed", "params":{"turn":{"status":"completed"}}})
            break
'''.replace("IMAGE", repr(image)).replace("NAME", repr(name)), name=name)
    result = await rig.call("agent.ask", {"session_id": "images", "text": "Explain the screenshot", "source": "app", "ui_language": "en",
        "member_id": teacher.id, "attachments": [
            {"name": "screenshot.png", "mime": "image/png", "data": image},
            {"name": "notes.txt", "mime": "text/plain", "data": base64.b64encode(b"notes").decode()},
        ]})
    assert result["text"] == "I can see the image."
    assert (await typed(rig, "Continue", teacher.id, language="en"))["text"] == "I can see the image."
    assert not client.chat_calls and not client.json_calls


@pytest.mark.parametrize("answer", ["", "The final answer."])
async def test_progress_only_turn_fails_and_terminal_answer_is_authoritative(setup, tmp_path, answer):
    rig, team = setup
    teacher = member(team, model="cli:claude")
    fake_brain(rig, tmp_path, '''import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"type":"assistant", "message":{"content":[{"type":"text", "text":"Still checking."}]}}),flush=True)
print(json.dumps({"type":"result", "subtype":"success", "result":ANSWER}),flush=True)
'''.replace("ANSWER", repr(answer)))
    result = await typed(rig, "Inspect this", teacher.id, language="en")
    [turn] = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
    replies = [message["text"] for message in turn["messages"] if message["kind"] == "reply"]
    if answer:
        assert result["text"] == answer and replies == [answer]
    else:
        assert turn["status"] == "error"
        assert result["text"] == "Claude Code stopped unexpectedly. Check its login and try again."
        assert [message["text"] for message in turn["messages"] if message["kind"] == "progress"] == ["Still checking."]
        assert "Still checking." not in replies


@pytest.mark.parametrize("language,prefix", [("zh-TW", "答。"), ("en", "Answer. "), ("ja", "解。")])
@pytest.mark.parametrize("wrapped", [False, True, "grouped"])
async def test_raphael_frames_a_multi_message_reply_only_once(setup, tmp_path, language, prefix, wrapped):
    rig, _ = setup
    rig.settings.set("agent_model", "cli:claude")
    parts = ["First.", "Second.", "Last."]
    answer = "\n<<<next message>>>\n".join(f"《{prefix}{part}》" if wrapped is True else part for part in parts)
    if wrapped == "grouped":
        answer = f"《{prefix}{answer}》"
    fake_brain(rig, tmp_path, '''import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"type":"result","subtype":"success","result":ANSWER}),flush=True)
'''.replace("ANSWER", repr(answer)))
    result = await typed(rig, "Explain", 0, language=language)
    [turn] = (await rig.call("agent.history.list", {}))["turns"]
    assert [message["text"] for message in turn["messages"] if message["kind"] == "reply"] == [
        f"《{prefix}First.", "Second.", "Last.》",
    ]
    assert result["text"] == "Last."


def fake_brain(rig, tmp_path, script, *, name="claude"):
    path = tmp_path / "fake.py"
    path.write_text(script, encoding="utf-8")
    home = tmp_path / "home"
    (home / f".{name}").mkdir(parents=True)
    (home / f".{name}" / ("auth.json" if name == "codex" else ".credentials.json")).write_text("{}", encoding="utf-8")
    rig.service._cli_brains = CliBrains(home=home, environ={**os.environ, "PYTHONUTF8": "1", "CODEX_HOME": str(home / ".codex")}, commands={name: [sys.executable, str(path)]}, settings=rig.service._settings)


async def test_a_resumed_cli_turn_carries_the_team_as_it_is_now(setup, tmp_path, client):
    # Live, a resumed Codex thread kept the instructions it began with: a renamed member and a removed
    # app stayed in its roster answers.
    rig, team = setup
    teacher = member(team, model="cli:codex")
    writer = member(team, name="Writer", role="Writes")
    inputs = tmp_path / "inputs.jsonl"
    fake_brain(rig, tmp_path, '''import json, sys
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize": send({"id":msg["id"], "result":{}})
    elif msg.get("method") in ("thread/start", "thread/resume"):
        send({"id":msg["id"], "result":{"thread":{"id":"roster-session"}}})
    elif msg.get("method") == "turn/start":
        with open(INPUTS, "a", encoding="utf-8") as out:
            out.write(json.dumps(msg["params"]["input"][0]["text"]) + "\\n")
        send({"method":"item/completed", "params":{"item":{"type":"agentMessage", "id":"final", "phase":"final_answer", "text":"Noted."}}})
        send({"method":"turn/completed", "params":{"turn":{"status":"completed"}}})
        break
'''.replace("INPUTS", repr(str(inputs))), name="codex")
    await typed(rig, "Who is on the team?", teacher.id, language="en")
    team.members.store.update_member(writer.id, name="Editor")
    await typed(rig, "And now?", teacher.id, language="en")
    first, second = [json.loads(line) for line in inputs.read_text(encoding="utf-8").splitlines()]
    assert "Editor" in second and "Editor" not in first


async def test_cli_member_uses_persona_records_zero_cost_and_resumes(setup, tmp_path, client):
    rig, team = setup
    teacher = member(team, model="cli:claude:sonnet")
    fake_brain(rig, tmp_path, '''import json, sys
from pathlib import Path
assert "PRIVATE-INSTRUCTIONS" in Path(sys.argv[sys.argv.index("--append-system-prompt-file")+1]).read_text(encoding="utf-8")
json.loads(sys.stdin.readline())
if "--resume" in sys.argv:
    assert sys.argv[sys.argv.index("--resume")+1] == "thread-one"
print(json.dumps({"type":"result","subtype":"success","session_id":"thread-one","result":"Hello\\n<<<next message>>>\\nNext lesson.","usage":{"input_tokens":3,"output_tokens":5}}), flush=True)
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


async def test_named_cli_conversations_resume_only_their_own_native_session(setup, tmp_path):
    rig, team = setup
    teacher = member(team, model="cli:claude")
    record = tmp_path / "native-sessions.jsonl"
    fake_brain(rig, tmp_path, '''import json, sys
from pathlib import Path
json.loads(sys.stdin.readline())
workspace = Path.cwd()
native = "native-" + workspace.parent.name
resume = sys.argv[sys.argv.index("--resume")+1] if "--resume" in sys.argv else None
assert resume is None or resume == native
with Path(RECORD).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"workspace":str(workspace), "resume":resume, "native":native}) + "\\n")
print(json.dumps({"type":"result", "subtype":"success", "session_id":native, "result":"Finished."}), flush=True)
'''.replace("RECORD", repr(str(record))))
    first = (await rig.call("conversations.create", {"member_id": teacher.id}))["conversation"]["id"]
    second = (await rig.call("conversations.create", {"member_id": teacher.id}))["conversation"]["id"]
    for number, conversation in enumerate([first, second, first]):
        result = await rig.call("agent.ask", {"session_id": f"named-{number}", "text": "Teach me",
                                             "source": "app", "ui_language": "en", "member_id": teacher.id,
                                             "conversation_id": conversation})
        assert result["text"] == "Finished."
    assert (await typed(rig, "Hello", teacher.id))["text"] == "Finished."
    rows = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert [row["resume"] for row in rows] == [None, None, f"native-{first}", None]
    assert rows[0]["workspace"] == rows[2]["workspace"]
    assert len({row["workspace"] for row in rows}) == 3


async def test_a_cli_reply_is_one_message_unless_the_model_breaks_it(setup, tmp_path, client):
    # paragraphs are one message: only the model's own break makes several (owner, 2026-10-24)
    rig, team = setup
    teacher = member(team, model="cli:claude:sonnet")
    fake_brain(rig, tmp_path, """import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"type":"result","subtype":"success","session_id":"t","result":"One.\\n\\nTwo.\\n\\nThree.","usage":{}}), flush=True)
""")
    await typed(rig, "Hi", teacher.id)
    turn = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"][0]
    assert [m["text"] for m in turn["messages"] if m["kind"] == "reply"] == ["One.\n\nTwo.\n\nThree."]

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
        send({"method":"item/agentMessage/delta","params":{"delta":"我先確認這個動作。"}})
        send({"id":"permit","method":"item/commandExecution/requestApproval","params":{"command":"echo hello"}})
    elif packet.get("id")=="permit":
        decision=packet["result"]["decision"]
        send({"method":"item/agentMessage/delta","params":{"delta":decision}})
        send({"method":"item/completed","params":{"item":{"id":"final","type":"agentMessage","text":decision}}})
        send({"method":"turn/completed","params":{"turn":{"status":"completed"}}})
''', encoding="utf-8")
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    rig.service._cli_brains = CliBrains(home=home, environ={**os.environ, "PYTHONUTF8": "1", "CODEX_HOME": str(home / ".codex")}, commands={"codex": [sys.executable, str(path)]})
    pending = await typed(rig, "Say hello", teacher.id)
    assert pending["status"] == "confirm", pending
    result = await rig.call("agent.confirm", {"id": pending["confirm"]["id"], "approved": decision != "no", "always": decision == "always"})
    assert result["text"] == {"yes": "accept", "no": "decline", "always": "acceptForSession"}[decision]
    turn = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"][0]
    assert turn["id"] == pending["turn_id"] and turn["status"] == "done"
    assert any(step["tool"] == "cli_permission" for step in turn["steps"])
    assert any(msg["kind"] == "progress" and msg["text"] == "我先確認這個動作。" for msg in turn["messages"])
    assert not rig.service._cli_loops
    assert len(list((home / ".raphael" / "codex-brains").iterdir())) == 1


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


async def test_raphaels_quick_cli_answer_skips_auxiliary_api_routing(setup, tmp_path, client):
    rig, _ = setup
    rig.settings.set("agent_model", "cli:claude")
    rig.settings.set("assistant_model", "anthropic/claude-haiku-5.5")
    fake_brain(rig, tmp_path, '''import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"type":"result","subtype":"success","result":"Answer from subscription."}),flush=True)
''')
    result = await rig.ask("Explain it")
    assert result["text"] == "Answer from subscription."
    assert not client.decide_calls
    assert not client.chat_calls
    assert not client.json_calls


RATE_LIMITED_CODEX = '''import json, sys
from pathlib import Path
launches = Path(LAUNCHES)
launches.write_text((launches.read_text() if launches.exists() else "") + "x")
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize": send({"id":msg["id"], "result":{}})
    elif msg.get("method") in ("thread/start", "thread/resume"): send({"id":msg["id"], "result":{"thread":{"id":"limited"}}})
    elif msg.get("method") == "turn/start":
        if len(launches.read_text()) >= ANSWER_ON:
            send({"method":"item/completed", "params":{"item":{"type":"agentMessage", "id":"final", "text":"Planned."}}})
            send({"method":"turn/completed", "params":{"turn":{"status":"completed"}}})
        else:
            send({"method":"error", "params":{"willRetry":False, "threadId":"limited", "turnId":"t", "error":{
                "message":"exceeded retry limit, last status: 429 Too Many Requests",
                "codexErrorInfo":{"responseTooManyFailedAttempts":{"httpStatusCode":429}}}}})
        break
'''


async def test_a_rate_limited_cli_turn_is_run_again_with_one_public_line(setup, tmp_path, monkeypatch):
    rig, team = setup
    teacher = member(team, model="cli:codex")
    monkeypatch.setattr("raphael_core.agent.cli.loop.RATE_LIMIT_WAITS_S", (0.0, 0.0))
    launches = tmp_path / "launches.txt"
    fake_brain(rig, tmp_path, RATE_LIMITED_CODEX.replace("LAUNCHES", repr(str(launches))).replace("ANSWER_ON", "2"), name="codex")
    assert (await typed(rig, "Plan it", teacher.id))["text"] == "Planned."
    [turn] = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
    assert [m["text"] for m in turn["messages"] if m["kind"] == "progress"] == ["Codex CLI 暫時限流，0 秒後重試。"]
    assert launches.read_text() == "xx"


async def test_a_quick_cli_question_fails_at_once_when_rate_limited(setup, tmp_path, monkeypatch):
    rig, _ = setup
    rig.settings.set("agent_model", "cli:codex")
    monkeypatch.setattr("raphael_core.agent.cli.loop.RATE_LIMIT_WAITS_S", (0.0, 0.0))
    launches = tmp_path / "launches.txt"
    fake_brain(rig, tmp_path, RATE_LIMITED_CODEX.replace("LAUNCHES", repr(str(launches))).replace("ANSWER_ON", "99"), name="codex")
    result = await rig.ask("Explain it")
    assert result["text"] == "Codex CLI 目前請求太頻繁而被限流，請稍後再試。"
    assert launches.read_text() == "x"


async def test_not_logged_in_never_starts_the_fake_cli(setup, tmp_path):
    rig, team = setup
    teacher = member(team, model="cli:gemini")
    rig.service._cli_brains = CliBrains(home=tmp_path, environ={}, commands={"gemini": ["this-must-not-run"]})
    result = await typed(rig, "Hello", teacher.id)
    assert result["text"] == "請先到設定 › 模型與用量 › 訂閱帳號，為 Antigravity 按下「連線」。"


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


async def test_cli_web_search_uses_auxiliary_api_model(setup, tmp_path, client):
    from agent.test_service import found

    rig, team = setup
    teacher = member(team, model="cli:claude")
    rig.settings.set("assistant_model", "anthropic/claude-haiku-5.5")
    client.texts.append(found("Search results."))
    fake_mcp(rig, tmp_path, [("web_search", {"query": "Weather news"})])
    assert (await typed(rig, "Search weather news", teacher.id))["text"] == "Finished."
    assert client.text_calls[0]["model"] == "anthropic/claude-haiku-5.5"


@pytest.mark.parametrize("approved", [True, False, "always"])
async def test_claude_permission_prompt_tool_round_trip(setup, tmp_path, approved):
    rig, team = setup
    teacher = member(team, model="cli:claude")
    fake_mcp(rig, tmp_path, [("approve", {"tool_name": "Write", "input": {"path": "lesson.txt"}})], allowed=approved)
    pending = await typed(rig, "Make a lesson", teacher.id)
    assert pending["status"] == "confirm"
    assert (await rig.call("agent.confirm", {"id": pending["confirm"]["id"], "approved": bool(approved), "always": approved == "always"}))["text"] == "Finished."


@pytest.mark.parametrize("model, offered", [("cli:claude", True), ("cli:codex", False), ("cli:gemini", False)])
def test_only_claude_code_is_offered_the_permission_prompt_tool(model, offered):
    from raphael_core.agent.cli.loop import CliLoop
    loop = CliLoop(brains=None, sessions=None, member_id=0, model=model, ledger=None, language="zh-TW", plain=False,
                   system="persona", user="hello", tools=[], progress=lambda _: None, thinking=lambda _: None,
                   event=lambda _: None, execute=None)
    assert ("approve" in [tool["function"]["name"] for tool in loop.tools]) is offered


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


@pytest.mark.parametrize(
    "language,expected",
    [
        ("zh-TW", "請先到設定 › 模型與用量 › 訂閱帳號，為 Codex CLI 按下「連線」。"),
        ("en", "Connect Codex CLI in Settings > Models & usage > Subscription accounts."),
        ("ja", "設定 › モデルと使用量 › サブスクリプションアカウントで Codex CLI の「接続」を押してください。"),
    ],
)
async def test_disconnected_cli_refuses_turns_for_member_and_raphael_in_window_language(setup, tmp_path, language, expected):
    rig, team = setup
    teacher = member(team, model="cli:codex")
    fake_brain(rig, tmp_path, "raise SystemExit('disconnected CLI must not start')", name="codex")
    assert await rig.call("brains.cli_disconnect", {"id": "codex"}) == {"disconnected": True}
    codex = next(c for c in (await rig.call("brains.cli", {}))["clis"] if c["id"] == "codex")
    assert codex["disconnected"] is True and codex["available"] is False and codex["models"] == []

    member_reply = await typed(rig, "Hello", teacher.id, language=language)
    assert member_reply["text"] == expected
    member_turn = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"][0]
    assert member_turn["status"] == "error"

    rig.settings.set("agent_model", "cli:codex")
    raphael_reply = await typed(rig, "Hello", 0, language=language)
    assert expected in raphael_reply["text"]
    raphael_turn = (await rig.call("agent.history.list", {"member_id": 0}))["turns"][0]
    assert raphael_turn["status"] == "error"

    assert await rig.call("brains.cli_login", {"id": "codex"}) == {"started": False, "reconnected": True}
    codex_after = next(c for c in (await rig.call("brains.cli", {}))["clis"] if c["id"] == "codex")
    assert codex_after["disconnected"] is False and codex_after["available"] is True


async def test_disconnect_stops_an_active_member_turn_and_releases_its_reply(setup, tmp_path):
    rig, team = setup
    teacher = member(team, model="cli:claude")
    marker = tmp_path / "running.json"
    fake_brain(rig, tmp_path, '''import json, os, sys, time
from pathlib import Path
json.loads(sys.stdin.readline())
Path(MARKER).write_text(json.dumps({"pid": os.getpid(), "config": sys.argv[sys.argv.index("--mcp-config")+1]}))
time.sleep(30)
'''.replace("MARKER", repr(str(marker))))
    task = asyncio.create_task(typed(rig, "Wait for me", teacher.id, language="en"))
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(.01)
        assert await asyncio.wait_for(rig.call("brains.cli_disconnect", {"id": "claude"}), 5) == {"disconnected": True}
        result = await asyncio.wait_for(task, 5)
        assert "Connect Claude Code in Settings" in result["text"]
        from pathlib import Path
        assert not Path(json.loads(marker.read_text())["config"]).exists()
        assert (rig.service._cli_brains.home / ".claude" / ".credentials.json").read_text() == "{}"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_an_unexpected_error_before_the_cli_starts_still_ends_the_turn(setup, tmp_path):
    # The turn waited forever when the work raised before its own error handling: the owner would have
    # seen a reply that never finished.
    rig, team = setup
    teacher = member(team, model="cli:claude")

    class Broken(CliBrains):
        def available(self, name, **kwargs):
            raise RuntimeError("PRIVATE-AVAILABILITY-DETAIL")

    rig.service._cli_brains = Broken(home=tmp_path, environ={}, commands={"claude": ["this-must-not-run"]})
    result = await asyncio.wait_for(typed(rig, "Hello", teacher.id), 10)
    assert "登入狀態" in result["text"]
    assert "PRIVATE-AVAILABILITY-DETAIL" not in result["text"]
