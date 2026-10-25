"""Claude stream-json, Codex app-server and Antigravity stream-json, normalized at one boundary.

Only public text and explicit reasoning summaries are forwarded. Raw diagnostics,
reasoning blocks, command output and provider credentials never become UI messages.
"""

import asyncio
import base64
import hashlib
import json
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .process import Child, PrivateFiles, secure_directory
from ..quota import parse_quota
from ...clock import wall_clock_ms

logger = logging.getLogger(__name__)

CODEX_SESSION_PREFIX = "raphael-codex-v1:"
# Verified against the installed Codex 0.159.3 config types and strict-config parser.
CODEX_ISOLATION = {
    "project_doc_max_bytes": 0,
    "skills.include_instructions": False,
    "features.skip_host_skill_discovery": True,
    **{f"features.{name}": False for name in (
        "apps", "plugins", "remote_plugin", "hooks", "memories", "shell_tool", "multi_agent",
        "browser_use", "computer_use", "code_mode", "code_mode_host", "view_image", "image_generation",
        "skill_search", "skill_mcp_dependency_install", "tool_suggest", "workspace_dependencies", "goals", "sleep_tool",
    )},
    "memories.generate_memories": False,
    "memories.use_memories": False,
    "include_apps_instructions": False,
    "tools.update_plan.enabled": False,
    "tools.experimental_request_user_input.enabled": False,
    "web_search": "disabled",
    "history.persistence": "none",
    "cli_auth_credentials_store": "file",
    # Deferral feature flags are removed in this release; use the supported exposure surfaces.
    "mcp_servers.raphael.omit_tools_from": ["deferred", "code_mode"],
    "mcp_servers.raphael.required": True,
    "mcp_servers.raphael.startup_readiness": "catalog",
}


@dataclass(frozen=True)
class Event:
    kind: str
    text: str = ""
    data: dict = field(default_factory=dict)


class Driver:
    def __init__(self, name, command, *, model=None, effort=None, environ=None, quota=None, home=None):
        self.name, self.command, self.model = name, list(command), model
        self.effort = effort
        self.environ = environ
        self.home = home
        self.child = None
        self.session = None
        self.turn = None
        self.quota = quota
        self.exhausted = False

    def _event(self, kind, text):
        # Only normalized quota metadata crosses this boundary, never provider diagnostics.
        if kind == "error" and self.exhausted:
            text = "quota"
        elif kind == "final" and self.quota and not self.exhausted:
            self.quota(self.name, {"exhausted": False})
        return Event(kind, text)

    async def interrupt(self):
        if self.child and self.child.process and self.child.process.returncode is None:
            try:
                if self.name == "codex" and self.session and self.turn:
                    await asyncio.wait_for(self.child.send({"jsonrpc": "2.0", "id": "interrupt", "method": "turn/interrupt",
                        "params": {"threadId": self.session, "turnId": self.turn}}), .25)
            except (OSError, TimeoutError, RuntimeError):
                pass
        if self.child:
            await self.child.kill()

    async def run(self, *, user, system, endpoint, token, session=None, permission, cwd=None, images=()):
        files = PrivateFiles()
        codex_auth = None
        try:
            await files.secure()
            prompt_file = files.write("system.txt", system)
            mcp = {"mcpServers": {"raphael": {"type": "http", "url": endpoint,
                                              "headers": {"Authorization": f"Bearer {token}"}}}}
            config = files.write("mcp.json", json.dumps(mcp))
            command = list(self.command)
            environ = dict(os.environ if self.environ is None else self.environ)
            child_cwd = cwd or str(files.path)
            if self.name == "claude":
                command += ["-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose",
                            "--include-partial-messages", "--append-system-prompt-file", prompt_file,
                            "--mcp-config", config, "--strict-mcp-config", "--tools", "",
                            "--allowedTools", "mcp__raphael__*",
                            "--permission-prompt-tool", "mcp__raphael__approve"]
                if session:
                    command += ["--resume", session]
                if self.model:
                    command += ["--model", self.model]
                if self.effort:
                    command += ["--effort", self.effort]
            elif self.name == "codex":
                # The thread's `config` is not applied to MCP servers: they are set when the server
                # starts, the token passed through the environment, never on the command line.
                home, environ, auth = await codex_home(child_cwd, environ, owner=self.home)
                codex_auth = home / "auth.json"
                if auth is not None:
                    descriptor = os.open(codex_auth, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        json.dump(auth, stream)
                child_cwd = str(home / "workspace")
                # Only sessions issued by the isolated driver may cross this boundary.
                session = session.removeprefix(CODEX_SESSION_PREFIX) if session and session.startswith(CODEX_SESSION_PREFIX) else None
                overrides = {**CODEX_ISOLATION, "mcp_servers.raphael.url": endpoint,
                             "mcp_servers.raphael.bearer_token_env_var": "RAPHAEL_MCP_TOKEN"}
                command += ["app-server", "--strict-config"]
                for key, value in overrides.items():
                    command += ["-c", f"{key}={json.dumps(value)}"]
            elif self.name == "gemini":
                # Google's Antigravity CLI (`agy`; Gemini CLI was retired). It reads MCP servers and
                # rules only from its home folder, so it runs with a private home inside the
                # member's workspace: Raphael's server and the persona are there, the owner's own
                # agy settings are never touched, and the login (in the OS store) still applies.
                home = antigravity_home(cwd or str(files.path), endpoint, token, system)
                command += ["--input-format", "stream-json", "--output-format", "stream-json", "-p="]
                if self.model:
                    command += ["--model", self.model]
                if session:
                    command += ["--conversation", session]
            else:
                raise ValueError("unknown_cli")
            if self.name == "codex":
                environ["RAPHAEL_MCP_TOKEN"] = token
            elif self.name == "gemini":
                environ["USERPROFILE"] = environ["HOME"] = str(home)
            self.child = Child(command, environ=environ, cwd=child_cwd)
            await self.child.start()
            if self.name == "claude":
                content = ([{"type": "text", "text": user}] + [
                    {"type": "image", "source": {"type": "base64", "media_type": image.mime,
                     "data": base64.b64encode(image.data).decode("ascii")}} for image in images
                ]) if images else user
                await self.child.send({"type": "user", "message": {"role": "user", "content": content}})
                self.child.process.stdin.close()
                async for event in self._claude():
                    yield event
            elif self.name == "gemini":
                await self.child.send({"event": "user", "message": {"role": "user", "content": user}})
                self.child.process.stdin.close()
                async for event in self._antigravity():
                    yield event
            else:
                image_paths = []
                for number, image in enumerate(images):
                    # Copy only this turn's validated bytes; owner-supplied names are never paths.
                    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}[image.mime]
                    path = files.path / f"image-{number}{suffix}"
                    path.write_bytes(image.data)
                    image_paths.append(str(path.resolve()))
                async for event in self._rpc(user, system, endpoint, token, session, permission, image_paths):
                    yield Event("session", CODEX_SESSION_PREFIX + event.text) if event.kind == "session" else event
        except (OSError, ValueError, TimeoutError, EOFError, asyncio.IncompleteReadError) as error:
            logger.warning("CLI transport failed: %s", type(error).__name__)
            yield self._event("error", "protocol_or_process_error")
        finally:
            try:
                await self.interrupt()
            finally:
                if codex_auth is not None:
                    codex_auth.unlink(missing_ok=True)
                files.close()
                if self.name == "gemini":
                    forget_antigravity_server(cwd or str(files.path))

    async def _read(self):
        line = await self.child.process.stdout.readline()
        if not line:
            raise EOFError("cli_exited")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("invalid_packet")
        self._observe_quota(value)
        return value

    def _observe_quota(self, packet):
        method = packet.get("method", "")
        kind = packet.get("type", packet.get("event", ""))
        failed = ("error" in packet or method in ("error", "account/rateLimits/updated")
                  or kind in ("error", "rate_limit_event")
                  or method == "turn/completed" and (packet.get("params", {}).get("turn") or {}).get("status") != "completed")
        if kind == "result":
            if self.name == "gemini":
                failed = failed or (packet.get("result") or {}).get("status") != "SUCCESS"
            else:
                failed = failed or packet.get("is_error") or packet.get("subtype") != "success"
        if failed:
            data = parse_quota(packet, wall_clock_ms())
            if data is not None:
                self.exhausted = data["exhausted"]
                if self.quota:
                    self.quota(self.name, data)

    async def _claude(self):
        partial = False
        while True:
            packet = await self._read()
            kind = packet.get("type")
            if kind == "system" and packet.get("session_id"):
                self.session = packet["session_id"]
                yield Event("session", self.session)
            elif kind == "stream_event":
                event = packet.get("event", {})
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    partial = True
                    yield Event("text_delta", delta.get("text", ""))
                elif event.get("type") == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        yield Event("tool_start", data={"name": block.get("name", "tool")})
            elif kind == "assistant":
                for block in packet.get("message", {}).get("content", []):
                    if block.get("type") == "text" and not partial:
                        yield Event("text_delta", block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        yield Event("tool_start", data={"name": block.get("name", "tool")})
                partial = False
            elif kind == "user":
                for block in packet.get("message", {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        yield Event("tool_end", data={"ok": not block.get("is_error", False)})
            elif kind == "result":
                if packet.get("session_id"):
                    self.session = packet["session_id"]
                    yield Event("session", self.session)
                yield Event("usage", data=packet.get("usage", {}))
                if packet.get("is_error") or packet.get("subtype") != "success":
                    yield self._event("error", "cli_failed")
                else:
                    yield self._event("final", packet.get("result") or "")
                return

    async def _antigravity(self):
        while True:
            packet = await self._read()
            kind = packet.get("event")
            if kind == "init" and packet.get("conversation_id"):
                self.session = packet["conversation_id"]
                yield Event("session", self.session)
            elif kind == "step_update":
                step = packet.get("step_update") or {}
                if step.get("step_type") == "agent_response" and step.get("text_delta"):
                    yield Event("text_delta", step["text_delta"])
                elif step.get("step_type") == "tool":
                    name = step.get("tool_name") or "tool"
                    if step.get("state") == "ACTIVE":
                        yield Event("tool_start", data={"name": name})
                    elif step.get("state") in ("DONE", "ERROR"):
                        yield Event("tool_end", data={"name": name, "ok": step.get("state") == "DONE"})
            elif kind == "result":
                result = packet.get("result") or {}
                if result.get("conversation_id"):
                    self.session = result["conversation_id"]
                    yield Event("session", self.session)
                yield Event("usage", data=result.get("usage") or {})
                if result.get("status") != "SUCCESS":
                    error = str(result.get("error") or "").lower()
                    yield self._event("error", "quota" if "quota" in error or "rate limit" in error else "cli_failed")
                else:
                    yield self._event("final", result.get("response") or "")
                return

    async def _rpc(self, user, system, endpoint, token, session, permission, image_paths=()):
        """Codex app-server: JSON-RPC over stdio."""
        pending = {}
        sequence = 0

        async def request(method, params, label):
            nonlocal sequence
            sequence += 1
            pending[sequence] = label
            await self.child.send({"jsonrpc": "2.0", "id": sequence, "method": method, "params": params})

        async def response(identifier, result=None, error=None):
            await self.child.send({"jsonrpc": "2.0", "id": identifier,
                                   **({"error": error} if error else {"result": result})})

        await request("initialize", {"clientInfo": {"name": "raphael", "version": "0.2.0"}, "capabilities": {}}, "initialize")
        final_text = ""
        while True:
            packet = await self._read()
            method, params = packet.get("method", ""), packet.get("params", {})
            if not method and packet.get("id") in pending:
                label = pending.pop(packet["id"])
                if "error" in packet:
                    if label == "session" and session:
                        # A removed private home must not send us looking in the owner's sessions.
                        session = None
                        arguments.pop("threadId", None)
                        await request("thread/start", arguments, "session")
                        continue
                    yield self._event("error", "cli_request_failed")
                    return
                result = packet.get("result") or {}
                if label == "initialize":
                    await self.child.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                    arguments = {"developerInstructions": system, "approvalPolicy": "untrusted",
                                 "sandbox": "read-only", "cwd": self.child.cwd}
                    if self.model:
                        arguments["model"] = self.model
                    if session:
                        arguments["threadId"] = session
                    await request("thread/resume" if session else "thread/start", arguments, "session")
                elif label == "session":
                    self.session = (result.get("thread") or {}).get("id")
                    if not self.session:
                        raise ValueError("missing_session")
                    yield Event("session", self.session)
                    inputs = [{"type": "text", "text": user}] + [{"type": "localImage", "path": path} for path in image_paths]
                    await request("turn/start", {"threadId": self.session, "input": inputs,
                                  **({"effort": self.effort} if self.effort else {})}, "turn")
                elif label == "turn":
                    self.turn = (result.get("turn") or {}).get("id")
            elif "id" in packet and method:
                if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                              "execCommandApproval", "applyPatchApproval"):
                    final_text = ""
                    yield Event("permission", data={"method": method})
                    decision = await permission({"method": method, **params})
                    await response(packet["id"], {"decision": {"once": "accept", "always": "acceptForSession"}.get(decision, "decline")})
                elif method == "mcpServer/elicitation/request":
                    # Codex asks before it calls an MCP tool. Raphael's own server screens every call
                    # through the safety gate (and asks the owner when the rules say so), so a call to
                    # it is accepted here; any other server is declined.
                    await response(packet["id"], {"action": "accept", "content": {}} if params.get("serverName") == "raphael"
                                   else {"action": "decline"})
                else:
                    # No filesystem, terminal or arbitrary client-side RPC outside our gate.
                    await response(packet["id"], error={"code": -32601, "message": "Unsupported client capability"})
            elif method == "item/agentMessage/delta":
                yield Event("text_delta", params.get("delta", ""))
            elif method == "item/reasoning/summaryTextDelta":
                yield Event("thinking", params.get("delta", ""))
            elif method in ("item/started", "item/completed"):
                item = params.get("item", {})
                if item.get("type") == "agentMessage":
                    if method == "item/started":
                        final_text = ""
                    else:
                        # Phase-less legacy messages are answers only after the most recent tool.
                        final_text = "" if item.get("phase") == "commentary" else item.get("text", "")
                if item.get("type") in ("commandExecution", "fileChange", "mcpToolCall", "webSearch"):
                    if method == "item/started":
                        final_text = ""
                    yield Event("tool_start" if method.endswith("started") else "tool_end", data={
                        "name": item.get("tool", item["type"]), "effect": item["type"] == "fileChange",
                        "ok": item.get("status") == "completed"})
            elif method == "thread/tokenUsage/updated":
                yield Event("usage", data=(params.get("tokenUsage") or {}).get("last", {}))
            elif method == "turn/started":
                self.turn = (params.get("turn") or {}).get("id")
            elif method == "turn/completed":
                if (params.get("turn") or {}).get("status") == "completed":
                    yield self._event("final", final_text)
                else:
                    yield self._event("error", "cli_turn_failed")
                return
            elif method == "error":
                yield self._event("error", "cli_error")
                return

async def codex_home(workspace, environ, *, owner=None):
    """Private persistent state outside the workspace; borrow login without refresh authority.

    Never copy the owner's config, instructions, skills, history or refresh token. An expired
    access token requires the owner's Codex to refresh its login before the next Raphael turn.
    """
    owner = Path(owner or environ.get("USERPROFILE") or environ.get("HOME") or Path.home())
    source = Path(environ.get("CODEX_HOME") or owner / ".codex") / "auth.json"
    identity = hashlib.sha256(os.path.normcase(str(Path(workspace).resolve())).encode()).hexdigest()[:24]
    home = owner / ".raphael" / "codex-brains" / identity
    home.mkdir(parents=True, exist_ok=True)
    await secure_directory(home)
    (home / "auth.json").unlink(missing_ok=True)
    for name in ("profile", "workspace"):
        (home / name).mkdir(exist_ok=True)
    auth = None
    try:
        stored = json.loads(source.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        stored = {}
    if not isinstance(stored, dict):
        raise ValueError("invalid_codex_auth")
    tokens = stored.get("tokens")
    if tokens:
        if not isinstance(tokens, dict) or any(not isinstance(tokens.get(key), str) or not tokens[key]
                for key in ("access_token", "id_token")):
            raise ValueError("invalid_codex_auth")
        auth = {"auth_mode": "chatgpt", "tokens": {key: tokens.get(key) for key in
                ("access_token", "id_token", "account_id")}, "last_refresh": datetime.now(timezone.utc).isoformat()}
        auth["tokens"]["refresh_token"] = ""
    environ = {key: value for key, value in environ.items() if not key.upper().startswith(("CODEX_", "OPENAI_"))}
    environ["CODEX_HOME"] = str(home)
    environ["HOME"] = environ["USERPROFILE"] = str(home / "profile")
    for key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        environ[key] = str(home / "profile" / key.lower())
    return home, environ, auth


def antigravity_home(workspace, endpoint, token, system):
    """The private home Antigravity runs with, inside the member's private workspace: Raphael's MCP
    server (its tools allowed and nothing else, since print mode denies every other permission),
    and the persona as the home's global rules."""
    home = Path(workspace) / "home"
    gemini = home / ".gemini"
    (gemini / "config").mkdir(parents=True, exist_ok=True)
    (gemini / "antigravity-cli").mkdir(parents=True, exist_ok=True)
    server = {"serverUrl": endpoint, "headers": {"Authorization": f"Bearer {token}"}, "disabled": False}
    (gemini / "config" / "mcp_config.json").write_text(
        json.dumps({"mcpServers": {"raphael": server}}), encoding="utf-8")
    (gemini / "antigravity-cli" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["mcp(raphael/*)"]}}), encoding="utf-8")
    (gemini / "GEMINI.md").write_text(system, encoding="utf-8")
    return home


def forget_antigravity_server(workspace):
    """The run's bearer token does not stay on disk after the run."""
    try:
        path = Path(workspace) / "home" / ".gemini" / "config" / "mcp_config.json"
        if path.exists():
            path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    except OSError:
        pass
