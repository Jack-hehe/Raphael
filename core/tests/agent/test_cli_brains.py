"""Subscription CLI tests never discover or launch the machine's real assistants."""

import os
import sys
import asyncio
import json

import pytest

from raphael_core.agent.cli.discovery import CliBrains, cli_available, resolve_command


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


async def test_codex_child_is_isolated_and_borrows_only_nonrefreshable_login(tmp_path):
    from pathlib import Path
    from raphael_core.agent.cli.drivers import Driver

    owner = tmp_path / ".codex"
    owner.mkdir()
    credentials = owner / "auth.json"
    original = json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "private-key",
        "tokens": {"id_token": "synthetic-id", "access_token": "synthetic-access",
                   "refresh_token": "owner-refresh", "account_id": "account"}, "last_refresh": "2020-01-01T00:00:00Z"})
    credentials.write_text(original, encoding="utf-8")
    (owner / "config.toml").write_text('[mcp_servers.personal]\ncommand="private-plugin"', encoding="utf-8")
    (owner / "AGENTS.md").write_text("private-instructions", encoding="utf-8")
    record = tmp_path / "record.json"
    script = tmp_path / "isolated.py"
    script.write_text('''import json, os, sys
from pathlib import Path
def send(value): print(json.dumps(value), flush=True)
record = {"command": sys.argv[2:], "environment": dict(os.environ), "cwd": os.getcwd()}
home = Path(os.environ["CODEX_HOME"])
record["auth"] = json.loads((home / "auth.json").read_text(encoding="utf-8"))
record["home_files"] = sorted(p.name for p in home.iterdir())
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize": send({"id":msg["id"], "result":{}})
    elif msg.get("method") in ("thread/start", "thread/resume"):
        record["thread"] = msg
        send({"id":msg["id"], "result":{"thread":{"id":"isolated-thread"}}})
    elif msg.get("method") == "turn/start":
        Path(sys.argv[1]).write_text(json.dumps(record), encoding="utf-8")
        send({"method":"item/completed", "params":{"item":{"type":"agentMessage", "text":"Done"}}})
        send({"method":"turn/completed", "params":{"turn":{"status":"completed"}}})
        break
''', encoding="utf-8")
    environment = {**os.environ, "CODEX_HOME": str(owner), "CODEX_PROFILE": "personal",
        "CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "personal-app", "OPENAI_BASE_URL": "https://personal.invalid",
        "OPENAI_API_KEY": "private-key", "CODEX_API_KEY": "private-key", "CODEX_ACCESS_TOKEN": "private-token"}
    (tmp_path / "workspace").mkdir()
    driver = Driver("codex", [sys.executable, str(script), str(record)], environ=environment)
    events = [event async for event in driver.run(user="Create COO and PM", system="Raphael persona",
        endpoint="http://127.0.0.1:1/mcp", token="ephemeral", session="owner-thread", permission=None,
        cwd=str(tmp_path / "workspace"))]
    assert events[-1].kind == "final", events
    result = json.loads(record.read_text(encoding="utf-8"))
    child_env = result["environment"]
    home = Path(child_env["CODEX_HOME"])
    assert home != owner and home.is_relative_to(tmp_path / ".raphael")
    assert child_env["HOME"] == child_env["USERPROFILE"] == str(home / "profile")
    assert {key for key in child_env if key.startswith("CODEX_")} == {"CODEX_HOME"}
    assert not any(key.startswith("OPENAI_") for key in child_env)
    assert child_env["RAPHAEL_MCP_TOKEN"] == "ephemeral"
    assert result["cwd"] == str(home / "workspace")
    command = result["command"]
    assert command[:2] == ["app-server", "--strict-config"]
    overrides = dict(item.split("=", 1) for item in command[3::2])
    assert command[2::2] == ["-c"] * len(overrides)
    assert overrides["mcp_servers.raphael.url"] == '"http://127.0.0.1:1/mcp"'
    assert overrides["mcp_servers.raphael.bearer_token_env_var"] == '"RAPHAEL_MCP_TOKEN"'
    assert overrides["mcp_servers.raphael.omit_tools_from"] == '["deferred", "code_mode"]'
    assert overrides["mcp_servers.raphael.required"] == "true"
    assert overrides["mcp_servers.raphael.startup_readiness"] == '"catalog"'
    assert overrides["project_doc_max_bytes"] == "0"
    assert overrides["skills.include_instructions"] == "false"
    assert overrides["features.skip_host_skill_discovery"] == "true"
    for feature in ("apps", "plugins", "remote_plugin", "hooks", "memories", "shell_tool", "multi_agent",
                    "browser_use", "computer_use", "code_mode", "view_image", "image_generation", "skill_search"):
        assert overrides[f"features.{feature}"] == "false"
    assert overrides["memories.generate_memories"] == overrides["memories.use_memories"] == "false"
    assert overrides["web_search"] == '"disabled"'
    assert overrides["history.persistence"] == '"none"'
    assert overrides["tools.update_plan.enabled"] == overrides["tools.experimental_request_user_input.enabled"] == "false"
    assert set(overrides) == {
        "project_doc_max_bytes", "skills.include_instructions", "features.skip_host_skill_discovery",
        "features.apps", "features.plugins", "features.remote_plugin", "features.hooks", "features.memories",
        "features.shell_tool", "features.multi_agent", "features.browser_use", "features.computer_use",
        "features.code_mode", "features.code_mode_host", "features.view_image", "features.image_generation",
        "features.skill_search", "features.skill_mcp_dependency_install", "features.tool_suggest",
        "features.workspace_dependencies", "features.goals", "features.sleep_tool", "memories.generate_memories",
        "memories.use_memories", "include_apps_instructions", "tools.update_plan.enabled",
        "tools.experimental_request_user_input.enabled", "web_search", "history.persistence",
        "cli_auth_credentials_store",
        "mcp_servers.raphael.omit_tools_from", "mcp_servers.raphael.url",
        "mcp_servers.raphael.bearer_token_env_var",
        "mcp_servers.raphael.required", "mcp_servers.raphael.startup_readiness",
    }
    assert result["auth"]["tokens"] == {"id_token": "synthetic-id", "access_token": "synthetic-access",
                                        "refresh_token": "", "account_id": "account"}
    assert "OPENAI_API_KEY" not in result["auth"]
    assert result["auth"]["last_refresh"] != "2020-01-01T00:00:00Z"
    assert "config.toml" not in result["home_files"] and "AGENTS.md" not in result["home_files"]
    assert not (home / "auth.json").exists()
    assert credentials.read_text(encoding="utf-8") == original
    assert result["thread"]["method"] == "thread/start"
    assert result["thread"]["params"]["cwd"] == result["cwd"]
    assert result["thread"]["params"]["developerInstructions"] == "Raphael persona"
    assert "ephemeral" not in json.dumps(result["thread"])
    assert next(event.text for event in events if event.kind == "session") == "raphael-codex-v1:isolated-thread"
    assert "owner-refresh" not in json.dumps(result)
    assert "synthetic-access" not in json.dumps(command) + json.dumps(child_env)
    for key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        assert child_env[key] == str(home / "profile" / key.lower())
    updated = json.loads(original)
    updated["tokens"]["access_token"] = "next-access"
    credentials.write_text(json.dumps(updated), encoding="utf-8")
    resumed = Driver("codex", [sys.executable, str(script), str(record)], environ=environment)
    events = [event async for event in resumed.run(user="Continue", system="Raphael persona",
        endpoint="http://127.0.0.1:1/mcp", token="next-ephemeral", session="raphael-codex-v1:isolated-thread",
        permission=None, cwd=str(tmp_path / "workspace"))]
    assert events[-1].kind == "final"
    result = json.loads(record.read_text(encoding="utf-8"))
    assert result["environment"]["CODEX_HOME"] == str(home)
    assert result["auth"]["tokens"]["access_token"] == "next-access"
    assert result["thread"]["method"] == "thread/resume"
    assert result["thread"]["params"]["threadId"] == "isolated-thread"
    assert not (home / "auth.json").exists()
    assert json.loads(credentials.read_text(encoding="utf-8")) == updated


async def test_codex_missing_isolated_session_restarts_once_without_owner_history(tmp_path):
    from raphael_core.agent.cli.drivers import Driver

    script = tmp_path / "missing.py"
    script.write_text('''import json, sys
def send(value): print(json.dumps(value), flush=True)
resumed = False
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize": send({"id":msg["id"], "result":{}})
    elif method == "thread/resume":
        assert not resumed and msg["params"]["threadId"] == "missing"
        resumed = True
        send({"id":msg["id"], "error":{"code":-32600,"message":"Rollout not found"}})
    elif method == "thread/start":
        assert resumed and "threadId" not in msg["params"]
        send({"id":msg["id"], "result":{"thread":{"id":"new"}}})
    elif method == "turn/start":
        send({"method":"item/completed", "params":{"item":{"type":"agentMessage", "text":"Fresh"}}})
        send({"method":"turn/completed", "params":{"turn":{"status":"completed"}}})
        break
''', encoding="utf-8")
    events = [event async for event in Driver("codex", [sys.executable, str(script)]).run(
        user="Continue", system="Persona", endpoint="http://127.0.0.1:1/mcp", token="ephemeral",
        session="raphael-codex-v1:missing", cwd=str(tmp_path), permission=None)]
    assert events[-1].kind == "final" and events[-1].text == "Fresh"
    assert next(event.text for event in events if event.kind == "session") == "raphael-codex-v1:new"


@pytest.mark.parametrize("cancel", [False, True])
async def test_codex_auth_snapshot_is_removed_on_exit_or_cancellation(tmp_path, cancel):
    from pathlib import Path
    from raphael_core.agent.cli.drivers import Driver

    owner = tmp_path / ".codex"
    owner.mkdir()
    credentials = owner / "auth.json"
    original = json.dumps({"tokens": {"id_token": "synthetic-id", "access_token": "synthetic-access",
                                     "refresh_token": "owner-refresh", "account_id": "account"}})
    credentials.write_text(original, encoding="utf-8")
    marker = tmp_path / "auth-path.txt"
    script = tmp_path / "auth_cleanup.py"
    script.write_text('''import os, sys, time
from pathlib import Path
auth = Path(os.environ["CODEX_HOME"]) / "auth.json"
assert auth.is_file()
Path(sys.argv[1]).write_text(str(auth), encoding="utf-8")
if sys.argv[2] == "True": time.sleep(300)
''', encoding="utf-8")
    driver = Driver("codex", [sys.executable, str(script), str(marker), str(cancel)])
    async def consume():
        return [event async for event in driver.run(user="Hi", system="Persona", endpoint="http://127.0.0.1:1/mcp",
            token="ephemeral", cwd=str(tmp_path), permission=None)]
    task = asyncio.create_task(consume())
    try:
        async with asyncio.timeout(10):
            while not marker.is_file():
                await asyncio.sleep(.01)
        if cancel:
            assert Path(marker.read_text(encoding="utf-8")).is_file()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            events = await asyncio.wait_for(task, 10)
            assert events[-1].kind == "error"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not Path(marker.read_text(encoding="utf-8")).exists()
    assert credentials.read_text(encoding="utf-8") == original
    assert driver.child.process.returncode is not None


@pytest.mark.parametrize("content", ["not-json", "[]", '{"tokens":{"access_token":"secret"}}'])
async def test_codex_malformed_login_fails_without_launching_child_or_logging_content(tmp_path, caplog, content):
    from raphael_core.agent.cli.drivers import Driver

    owner = tmp_path / ".codex"
    owner.mkdir()
    auth = owner / "auth.json"
    auth.write_text(content, encoding="utf-8")
    driver = Driver("codex", ["must-not-start"])
    events = [event async for event in driver.run(user="Hi", system="Persona", endpoint="http://127.0.0.1:1/mcp",
        token="ephemeral", cwd=str(tmp_path), permission=None)]
    assert driver.child is None
    assert events[-1].kind == "error" and events[-1].text == "protocol_or_process_error"
    assert "secret" not in caplog.text
    assert auth.read_text(encoding="utf-8") == content


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
                send({"method":"item/completed", "params":{"item":{"id":"final", "type":"agentMessage", "text":"Hello"}}})
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
    session = "raphael-codex-v1:previous" if name == "codex" else "previous"
    events = [event async for event in driver.run(user="question", system="persona", endpoint="http://127.0.0.1:1/mcp", token="ephemeral", session=session, permission=permission)]
    assert any(event.kind == "text_delta" and event.text == "Hello" for event in events)
    assert any(event.kind == "final" and event.text == "Hello" for event in events)
    assert any(event.kind == "session" for event in events)
    assert not any(event.kind == "error" for event in events)
    assert all("PRIVATE-REASONING" not in event.text for event in events)
    assert any(event.kind == "thinking" for event in events) == (name == "codex")
    assert {"tool_start", "tool_end"} <= {event.kind for event in events}
    # Antigravity's print mode denies every permission but Raphael's own tools: nothing to ask
    assert len(permissions) == (1 if name == "codex" else 0)


@pytest.mark.parametrize("name", ["claude", "codex", "gemini"])
@pytest.mark.parametrize("state", ["failed", "exhausted_with_answer", "recovered"])
async def test_driver_quota_notifications_and_terminal_events(tmp_path, monkeypatch, name, state):
    from raphael_core.agent.cli.drivers import Driver
    from .test_quota import NOW

    monkeypatch.setattr("raphael_core.agent.cli.drivers.wall_clock_ms", lambda: NOW)
    script = tmp_path / "quota_protocol.py"
    script.write_text('''import json, sys
name, state, reset = sys.argv[1:4]
def send(packet): print(json.dumps(packet), flush=True)
def finish():
    if state != "recovered":
        limits = {"primary": {"usedPercent": 100, "resetsAt": int(reset)}}
        if name == "codex": send({"method":"account/rateLimits/updated", "params":{"rateLimits":limits}})
        else: send({"type":"rate_limit_event", "event":"rate_limit_event", "rateLimits":limits})
    if name == "codex":
        if state != "failed":
            send({"method":"item/agentMessage/delta", "params":{"delta":"Public answer."}})
            send({"method":"item/completed", "params":{"item":{"id":"final", "type":"agentMessage", "text":"Public answer."}}})
        send({"method":"turn/completed", "params":{"turn":{"status":"failed" if state == "failed" else "completed"}}})
    elif name == "claude":
        send({"type":"result", "subtype":"error_during_execution" if state == "failed" else "success",
              "is_error":state == "failed", "result":"PRIVATE-DIAGNOSTIC" if state == "failed" else "Public answer."})
    else:
        send({"event":"result", "result":{"status":"ERROR" if state == "failed" else "SUCCESS",
              "error":"PRIVATE-DIAGNOSTIC" if state == "failed" else "", "response":"Public answer."}})
if name == "codex":
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        if method == "initialize": send({"id":request["id"], "result":{}})
        elif method == "thread/start": send({"id":request["id"], "result":{"thread":{"id":"one"}}})
        elif method == "turn/start":
            finish()
            break
else:
    json.loads(sys.stdin.readline())
    finish()
''', encoding="utf-8")
    recorded = []
    driver = Driver(name, [sys.executable, str(script), name, state, str(NOW // 1000 + 3600)],
                    quota=lambda *value: recorded.append(value))
    events = [event async for event in driver.run(user="hello", system="persona", endpoint="http://127.0.0.1:1/mcp",
                                                 token="ephemeral", permission=None)]
    expected = {"exhausted": False} if state == "recovered" else {"exhausted": True, "reset_at_ms": NOW + 3600000}
    assert recorded == [(name, expected)]
    assert events[-1].kind == ("error" if state == "failed" else "final")
    assert events[-1].text == ("quota" if state == "failed" else "Public answer.")
    assert all("PRIVATE-DIAGNOSTIC" not in event.text for event in events)


@pytest.mark.parametrize("shape,expected", [
    ("notified", ("error", "rate_limited")), ("snake_case", ("error", "rate_limited")),
    ("retried", ("final", "Public answer.")), ("exhausted", ("error", "quota")), ("other", ("error", "cli_turn_failed")),
])
async def test_codex_too_many_requests_is_a_rate_limit_unless_the_plan_is_exhausted(tmp_path, monkeypatch, shape, expected):
    from raphael_core.agent.cli.drivers import Driver
    from .test_quota import NOW

    monkeypatch.setattr("raphael_core.agent.cli.drivers.wall_clock_ms", lambda: NOW)
    script = tmp_path / "rate_limited.py"
    script.write_text('''import json, sys
shape, reset = sys.argv[1:3]
def send(packet): print(json.dumps(packet), flush=True)
camel = {"message":"exceeded retry limit, last status: 429 Too Many Requests",
         "codexErrorInfo":{"responseTooManyFailedAttempts":{"httpStatusCode":429}}}
snake = {"message":"exceeded retry limit, last status: 429","codex_error_info":{"response_too_many_failed_attempts":{"http_status_code":429}}}
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize": send({"id":request["id"], "result":{}})
    elif method == "thread/start": send({"id":request["id"], "result":{"thread":{"id":"one"}}})
    elif method == "turn/start":
        if shape == "exhausted":
            send({"method":"account/rateLimits/updated", "params":{"rateLimits":{"primary":{"usedPercent":100, "resetsAt":int(reset)}}}})
        if shape in ("notified", "exhausted", "retried"):
            send({"method":"error", "params":{"error":camel, "willRetry":shape == "retried", "threadId":"one", "turnId":"t"}})
        if shape == "retried":
            send({"method":"item/completed", "params":{"item":{"id":"final", "type":"agentMessage", "text":"Public answer."}}})
            send({"method":"turn/completed", "params":{"turn":{"id":"t", "status":"completed"}}})
        else:
            error = snake if shape == "snake_case" else {"message":"PRIVATE-DIAGNOSTIC"} if shape == "other" else camel
            send({"method":"turn/completed", "params":{"turn":{"id":"t", "status":"failed", "error":error}}})
        break
''', encoding="utf-8")
    environ = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CODEX_", "OPENAI_"))}
    environ["USERPROFILE"] = environ["HOME"] = str(tmp_path)
    driver = Driver("codex", [sys.executable, str(script), shape, str(NOW // 1000 + 3600)], environ=environ, home=tmp_path)
    events = [event async for event in driver.run(user="hello", system="persona", endpoint="http://127.0.0.1:1/mcp",
                                                 token="ephemeral", permission=None, cwd=str(tmp_path / "workspace"))]
    assert (events[-1].kind, events[-1].text) == expected
    assert all("PRIVATE-DIAGNOSTIC" not in event.text and "429" not in event.text for event in events)


@pytest.mark.parametrize("shape,expected", [("limited", "rate_limited"), ("exhausted", "quota"), ("other", "cli_failed")])
async def test_claude_api_429_is_a_rate_limit_unless_the_plan_window_is_rejected(tmp_path, monkeypatch, shape, expected):
    from raphael_core.agent.cli.drivers import Driver
    from .test_quota import NOW

    monkeypatch.setattr("raphael_core.agent.cli.drivers.wall_clock_ms", lambda: NOW)
    script = tmp_path / "claude_429.py"
    script.write_text('''import json, sys
shape, reset = sys.argv[1:3]
def send(packet): print(json.dumps(packet), flush=True)
json.loads(sys.stdin.readline())
if shape == "exhausted":
    send({"type":"rate_limit_event", "rate_limit_info":{"status":"rejected", "rateLimitType":"five_hour", "resetsAt":int(reset)}})
send({"type":"result", "subtype":"success", "is_error":True, "terminal_reason":"api_error",
      "api_error_status":500 if shape == "other" else 429, "result":"PRIVATE-DIAGNOSTIC"})
''', encoding="utf-8")
    driver = Driver("claude", [sys.executable, str(script), shape, str(NOW // 1000 + 3600)])
    events = [event async for event in driver.run(user="hello", system="persona", endpoint="http://127.0.0.1:1/mcp",
                                                 token="ephemeral", permission=None, cwd=str(tmp_path))]
    assert (events[-1].kind, events[-1].text) == ("error", expected)
    assert all("PRIVATE-DIAGNOSTIC" not in event.text for event in events)


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
        outcome, = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(outcome, asyncio.CancelledError), repr(outcome)
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


async def test_disconnect_purges_raphael_credentials_hides_models_and_reconnects_without_login_window(tmp_path, conn, monkeypatch):
    from raphael_core.agent.cli import discovery
    from raphael_core.brain.settings import SettingsStore

    script = tmp_path / "version.py"
    script.write_text("print('fake 1.0.0')", encoding="utf-8")
    home = tmp_path / "home"
    claude_cred = home / ".claude" / ".credentials.json"
    codex_cred = home / ".codex" / "auth.json"
    gemini_cred = home / ".gemini" / "google_accounts.json"
    for path, content in ((claude_cred, "user-claude"), (codex_cred, "user-codex"), (gemini_cred, "user-gemini")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    copied_auth = home / ".raphael" / "codex-brains" / "session-1" / "auth.json"
    copied_cfg = copied_auth.parent / "config.toml"
    copied_auth.parent.mkdir(parents=True, exist_ok=True)
    copied_auth.write_text("raphael-copy", encoding="utf-8")
    copied_cfg.write_text("cli_auth_credentials_store = 'file'\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    copied_mcp = home / ".raphael" / "cli-workspaces" / "1" / "0" / "gemini" / "home" / ".gemini" / "config" / "mcp_config.json"
    copied_mcp.parent.mkdir(parents=True, exist_ok=True)
    copied_mcp.write_text("{}", encoding="utf-8")

    events = []
    settings = SettingsStore(conn)
    cmd = [sys.executable, str(script)]
    brains = CliBrains(
        home=home,
        environ={},
        commands={"claude": cmd, "codex": cmd, "gemini": cmd},
        notify=lambda method, payload: events.append((method, payload)),
        settings=settings,
    )
    monkeypatch.setattr(brains, "_needs_models", lambda _name: False)
    brains._models["codex"] = [{"id": "cli:codex:gpt-5", "name": "GPT-5", "is_default": True}]
    brains._models["gemini"] = [{"id": "cli:gemini:pro", "name": "Gemini Pro", "is_default": True}]

    try:
        before = {c["id"]: c for c in (await brains.list())["clis"]}
        assert all(c["available"] and not c["disconnected"] and c["models"] for c in before.values())

        for name in ("codex", "claude", "gemini"):
            assert brains.disconnect(name) == {"disconnected": True}
        assert settings.get("disconnected_clis") == ["codex", "claude", "gemini"]
        restarted = CliBrains(home=home, environ={}, commands={"codex": cmd}, settings=SettingsStore(conn))
        assert restarted.disconnected("codex") and not restarted.available("codex")
        assert not copied_auth.exists() and copied_cfg.exists()
        assert not copied_mcp.exists()
        assert claude_cred.read_text(encoding="utf-8") == "user-claude"
        assert codex_cred.read_text(encoding="utf-8") == "user-codex"
        assert gemini_cred.read_text(encoding="utf-8") == "user-gemini"

        after = {c["id"]: c for c in (await brains.list())["clis"]}
        assert all(c["logged_in"] is True and c["disconnected"] is True and c["available"] is False and c["models"] == [] for c in after.values())
        assert ("brains.changed", {}) in events

        popen_calls = []
        monkeypatch.setattr(discovery.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

        events.clear()
        assert brains.login("claude") == {"started": False, "reconnected": True}
        assert brains.login("codex") == {"started": False, "reconnected": True}
        assert popen_calls == []
        assert settings.get("disconnected_clis") == ["gemini"]
        assert events == [("brains.changed", {}), ("brains.changed", {})]

        reconnected = {c["id"]: c for c in (await brains.list())["clis"]}
        assert reconnected["claude"]["available"] is True and reconnected["claude"]["models"]
        assert reconnected["codex"]["available"] is True

        gemini_cred.unlink()
        if os.name == "nt":
            assert brains.login("gemini") == {"started": True}
            assert len(popen_calls) == 1
            assert settings.get("disconnected_clis") == []
    finally:
        await brains.aclose()


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("logged_in", [False, True, None])
@pytest.mark.parametrize("disconnected", [False, True])
def test_cli_availability_requires_install_login_and_connection(installed, logged_in, disconnected):
    assert cli_available(installed, logged_in, disconnected) is (installed and logged_in is True and not disconnected)


def test_failed_login_does_not_reconnect_or_launch_an_uninstalled_cli(tmp_path, monkeypatch):
    from raphael_core.agent.cli import discovery
    brains = CliBrains(home=tmp_path, environ={}, commands={"codex": ["fake.exe"]})
    brains.disconnect("codex")
    def fail(*args, **kwargs):
        raise OSError("login window unavailable")
    monkeypatch.setattr(discovery.subprocess, "Popen", fail)
    with pytest.raises((OSError, ValueError)):
        brains.login("codex")
    assert brains.disconnected("codex")
    brains.commands.clear()
    with pytest.raises(ValueError, match="cli_missing"):
        brains.login("codex")
    assert brains.disconnected("codex")
