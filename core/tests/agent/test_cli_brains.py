"""Subscription CLI tests never discover or launch the machine's real assistants."""

import os
import sys
import asyncio
import json

import pytest

from raphael_core.agent.cli.discovery import CliBrains, resolve_command


async def test_discovery_uses_fake_process_and_auth_existence_only(tmp_path):
    script = tmp_path / "fake.py"
    script.write_text("print('fake 1.2.3')", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("not readable JSON", encoding="utf-8")
    brains = CliBrains(home=home, environ={}, commands={"codex": [sys.executable, str(script)]})
    result = await brains.list()
    codex = next(cli for cli in result["clis"] if cli["id"] == "codex")
    assert codex["installed"] and codex["logged_in"]
    assert codex["version"] == "fake 1.2.3"
    assert all(not cli["installed"] for cli in result["clis"] if cli["id"] != "codex")
    await brains.aclose()


def test_npm_shims_are_unwrapped_without_shell(tmp_path):
    entry = tmp_path / "node_modules" / "pkg" / "bin" / "cli.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.touch()
    shim = tmp_path / "codex.cmd"
    shim.write_text('@"%~dp0\\node.exe" "%~dp0\\node_modules\\pkg\\bin\\cli.js" %*', encoding="utf-8")
    assert resolve_command(shim, {}) == [str(node), str(entry)]


def test_unknown_shim_fails_closed(tmp_path):
    shim = tmp_path / "codex.cmd"
    shim.write_text("echo unsafe & something", encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_command(shim, {})


@pytest.mark.parametrize("name", ["claude", "codex", "gemini"])
async def test_driver_subscription_events_permissions_and_resume(tmp_path, name):
    from raphael_core.agent.cli.drivers import Driver

    script = tmp_path / "protocol.py"
    script.write_text('''import json, os, sys
name = sys.argv[1]
def send(value):
    print(json.dumps(value), flush=True)
assert all(key not in os.environ for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"))
if name == "claude":
    assert "--resume" in sys.argv and "previous" in sys.argv
    assert sys.argv[sys.argv.index("--model")+1] == "chosen-model"
    assert sys.argv[sys.argv.index("--effort")+1] == "high"
    json.loads(sys.stdin.readline())
    send({"type":"system", "subtype":"init", "session_id":"next"})
    send({"type":"assistant", "message":{"content":[{"type":"thinking","thinking":"PRIVATE-REASONING"}]}})
    send({"type":"stream_event", "event":{"type":"content_block_delta", "delta":{"type":"text_delta", "text":"Hello"}}})
    send({"type":"stream_event", "event":{"type":"content_block_start", "content_block":{"type":"tool_use","name":"mcp__raphael__routine_list"}}})
    send({"type":"user", "message":{"content":[{"type":"tool_result","is_error":False}]}})
    send({"type":"result", "subtype":"success", "result":"Hello", "usage":{"input_tokens":4,"output_tokens":2}})
elif name == "gemini":
    assert sys.argv[sys.argv.index("--conversation") + 1] == "previous"
    home = os.environ["USERPROFILE"]
    config = json.load(open(os.path.join(home, ".gemini", "config", "mcp_config.json"), encoding="utf-8"))
    assert config["mcpServers"]["raphael"]["headers"]["Authorization"] == "Bearer ephemeral"
    assert open(os.path.join(home, ".gemini", "GEMINI.md"), encoding="utf-8").read() == "persona"
    assert json.loads(sys.stdin.readline())["message"]["content"] == "question"
    send({"event":"init", "conversation_id":"next", "init":{}})
    send({"event":"step_update", "step_update":{"step_type":"tool", "state":"ACTIVE", "tool_name":"call_mcp_tool"}})
    send({"event":"step_update", "step_update":{"step_type":"tool", "state":"DONE", "tool_name":"call_mcp_tool"}})
    send({"event":"step_update", "step_update":{"step_type":"agent_response", "state":"ACTIVE", "text_delta":"Hello"}})
    send({"event":"result", "result":{"conversation_id":"next", "status":"SUCCESS", "response":"Hello", "usage":{"input_tokens":4}}})
else:
    for line in sys.stdin:
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc":"2.0", "id":msg["id"], "result":{}})
        elif method in ("thread/resume", "session/load"):
            assert msg["params"].get("threadId", msg["params"].get("sessionId")) == "previous"
            if name == "codex":
                assert msg["params"]["model"] == "chosen-model"
            send({"jsonrpc":"2.0", "id":msg["id"], "result":{"thread":{"id":"next"},"sessionId":"next"}})
        elif method in ("turn/start", "session/prompt"):
            if name == "codex":
                assert msg["params"]["effort"] == "high"
            send({"jsonrpc":"2.0", "id":"approval", "method":"item/commandExecution/requestApproval" if name=="codex" else "session/request_permission", "params":{"command":"echo hello", "options":[{"optionId":"yes","kind":"allow_once"},{"optionId":"no","kind":"reject_once"}]}})
            turn_id = msg["id"]
        elif msg.get("id") == "approval":
            assert "error" not in msg
            if name == "codex":
                assert msg["result"]["decision"] == "accept"
                send({"method":"item/reasoning/textDelta", "params":{"delta":"PRIVATE-REASONING"}})
                send({"method":"item/reasoning/summaryTextDelta", "params":{"delta":"Public summary"}})
                send({"method":"item/started", "params":{"item":{"type":"mcpToolCall","tool":"routine_list"}}})
                send({"method":"item/completed", "params":{"item":{"type":"mcpToolCall","tool":"routine_list","status":"completed"}}})
                send({"method":"item/agentMessage/delta", "params":{"delta":"Hello"}})
                send({"method":"turn/completed", "params":{"turn":{"status":"completed"}}})
            else:
                assert msg["result"]["outcome"]["optionId"] == "yes"
                send({"method":"session/update", "params":{"update":{"sessionUpdate":"agent_thought_chunk", "content":{"type":"text","text":"PRIVATE-REASONING"}}}})
                send({"method":"session/update", "params":{"update":{"sessionUpdate":"tool_call","toolCallId":"read-one","kind":"read","status":"in_progress"}}})
                send({"method":"session/update", "params":{"update":{"sessionUpdate":"tool_call_update","toolCallId":"read-one","status":"completed"}}})
                send({"method":"session/update", "params":{"update":{"sessionUpdate":"agent_message_chunk", "content":{"type":"text","text":"Hello"}}}})
                send({"id":turn_id,"result":{"stopReason":"end_turn"}})
''', encoding="utf-8")
    permissions = []

    async def permission(value):
        permissions.append(value)
        return "once"

    driver = Driver(name, [sys.executable, str(script), name], model="chosen-model", effort="high", environ={**os.environ, "OPENAI_API_KEY": "never-child"})
    events = [event async for event in driver.run(user="question", system="persona", endpoint="http://127.0.0.1:1/mcp", token="ephemeral", session="previous", permission=permission)]
    assert any(event.kind == "text_delta" and event.text == "Hello" for event in events)
    assert any(event.kind == "final" and event.text == "Hello" for event in events)
    assert any(event.kind == "session" for event in events)
    assert not any(event.kind == "error" for event in events)
    assert all("PRIVATE-REASONING" not in event.text for event in events)
    assert any(event.kind == "thinking" for event in events) == (name == "codex")
    assert {"tool_start", "tool_end"} <= {event.kind for event in events}
    # Antigravity's print mode denies every permission but Raphael's own tools: nothing to ask
    assert len(permissions) == (1 if name == "codex" else 0)


async def test_discovery_reports_changes_without_reading_credentials(tmp_path, monkeypatch):
    from pathlib import Path
    script = tmp_path / "version.py"
    script.write_text("print('fake 1.0')", encoding="utf-8")
    changed = []
    brains = CliBrains(home=tmp_path, environ={}, commands={"gemini": [sys.executable, str(script)]}, notify=lambda *args: changed.append(args))
    # Authentication discovery is independent of asynchronous model-catalog notifications.
    monkeypatch.setattr(brains, "_needs_models", lambda _name: False)
    assert (await brains.list())["clis"][2]["logged_in"] is False
    (tmp_path / ".gemini").mkdir()
    credentials = tmp_path / ".gemini" / "oauth_creds.json"
    credentials.touch()
    read_text = Path.read_text
    def forbid_read(path, *args, **kwargs):
        assert path.name not in ("auth.json", "oauth_creds.json", "google_accounts.json", ".credentials.json", "credentials.json")
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", forbid_read)
    assert (await brains.list())["clis"][2]["logged_in"] is True
    assert changed == [("brains.changed", {})]
    await brains.aclose()


def test_known_windows_directory_and_powershell_shim(tmp_path):
    npm = tmp_path / "npm"
    npm.mkdir()
    entry = npm / "entry.mjs"
    entry.touch()
    node = npm / "node.exe"
    node.touch()
    (npm / "claude.ps1").write_text('& "$basedir/node$exe" "$basedir/entry.mjs" $args', encoding="utf-8")
    brains = CliBrains(home=tmp_path, environ={"APPDATA": str(tmp_path), "PATH": ""})
    assert brains.command("claude") == [str(node), str(entry)]
    assert brains.command("codex") is None


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def test_cancel_kills_fake_cli_and_descendant_and_deletes_private_files(tmp_path):
    from raphael_core.agent.cli.drivers import Driver
    marker = tmp_path / "pids.json"
    script = tmp_path / "tree.py"
    script.write_text('''import json, os, subprocess, sys, time
from pathlib import Path
child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(300)"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid,sys.argv[sys.argv.index("--mcp-config")+1]]))
print(json.dumps({"type":"system","subtype":"init","session_id":"one"}),flush=True)
time.sleep(300)
''', encoding="utf-8")
    driver = Driver("claude", [sys.executable, str(script), str(marker)])
    async def permission(_):
        return "deny"
    async def consume():
        async for event in driver.run(user="hi", system="private persona", endpoint="http://127.0.0.1:1/mcp", token="private-token", permission=permission):
            pass
    task = asyncio.create_task(consume())
    try:
        async with asyncio.timeout(10):
            while not marker.is_file():
                await asyncio.sleep(.01)
        parent, descendant, config = json.loads(marker.read_text())
        assert alive(parent) and alive(descendant)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    from pathlib import Path
    assert not alive(parent) and not alive(descendant)
    assert not Path(config).parent.exists()


def test_login_launches_only_the_unwrapped_command_in_a_visible_console(tmp_path, monkeypatch):
    from raphael_core.agent.cli import discovery
    calls = []
    monkeypatch.setattr(discovery.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    brains = CliBrains(home=tmp_path, environ={"OPENAI_API_KEY": "secret", "PATH": "some-path"}, commands={"codex": ["fake-node", "fake-entry.js"]})
    if os.name != "nt":
        pytest.skip("Windows visible console contract")
    assert brains.login("codex") == {"started": True}
    assert calls[0][0][0] == ["fake-node", "fake-entry.js", "login"]
    assert calls[0][1]["creationflags"] & discovery.subprocess.CREATE_NEW_CONSOLE
    assert "OPENAI_API_KEY" not in calls[0][1]["env"]


async def test_large_prompts_use_stdin_and_private_files_not_command_arguments(tmp_path):
    from raphael_core.agent.cli.drivers import Driver
    from raphael_core.agent.cli.process import Child
    script = tmp_path / "large.py"
    script.write_text('''import json, sys
from pathlib import Path
assert sum(len(arg) for arg in sys.argv)<30000
assert len(Path(sys.argv[sys.argv.index("--append-system-prompt-file")+1]).read_text(encoding="utf-8"))==50000
assert len(json.loads(sys.stdin.readline())["message"]["content"])==50000
print(json.dumps({"type":"result","subtype":"success","result":"Done"}),flush=True)
''', encoding="utf-8")
    async def permission(_):
        return "deny"
    driver = Driver("claude", [sys.executable, str(script)])
    events = [event async for event in driver.run(user="u" * 50000, system="s" * 50000,
              endpoint="http://127.0.0.1:1/mcp", token="ephemeral", permission=permission)]
    assert events[-1].kind == "final" and events[-1].text == "Done"
    with pytest.raises(ValueError, match="command_too_long"):
        await Child([sys.executable, "x" * 30000]).start()


def test_antigravity_is_found_in_its_own_folder(tmp_path):
    agy = tmp_path / "agy" / "bin"
    agy.mkdir(parents=True)
    (agy / "agy.exe").touch()
    brains = CliBrains(home=tmp_path, environ={"LOCALAPPDATA": str(tmp_path), "PATH": ""})
    assert brains.command("gemini") == [str((agy / "agy.exe").resolve())]
