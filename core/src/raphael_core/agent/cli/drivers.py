"""Claude stream-json, Codex app-server and Gemini ACP, normalized at one boundary.

Only public text and explicit reasoning summaries are forwarded. Raw diagnostics,
reasoning blocks, command output and provider credentials never become UI messages.
"""

import asyncio
import json
import os
import logging
from dataclasses import dataclass, field

from .process import Child, PrivateFiles

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    kind: str
    text: str = ""
    data: dict = field(default_factory=dict)


class Driver:
    def __init__(self, name, command, *, model=None, environ=None):
        self.name, self.command, self.model = name, list(command), model
        self.environ = environ
        self.child = None
        self.session = None
        self.turn = None

    async def interrupt(self):
        if self.child and self.child.process and self.child.process.returncode is None:
            try:
                if self.name == "codex" and self.session and self.turn:
                    await asyncio.wait_for(self.child.send({"jsonrpc": "2.0", "id": "interrupt", "method": "turn/interrupt",
                        "params": {"threadId": self.session, "turnId": self.turn}}), .25)
                elif self.name == "gemini" and self.session:
                    await asyncio.wait_for(self.child.send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.session}}), .25)
            except (OSError, TimeoutError, RuntimeError):
                pass
        if self.child:
            await self.child.kill()

    async def run(self, *, user, system, endpoint, token, session=None, permission, cwd=None):
        files = PrivateFiles()
        try:
            await files.secure()
            prompt_file = files.write("system.txt", system)
            mcp = {"mcpServers": {"raphael": {"type": "http", "url": endpoint,
                                              "headers": {"Authorization": f"Bearer {token}"}}}}
            config = files.write("mcp.json", json.dumps(mcp))
            command = list(self.command)
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
            elif self.name == "codex":
                # The thread's `config` is not applied to MCP servers: they are set when the server
                # starts, the token passed through the environment, never on the command line.
                command += ["app-server", "-c", f"mcp_servers.raphael.url={json.dumps(endpoint)}",
                            "-c", 'mcp_servers.raphael.bearer_token_env_var="RAPHAEL_MCP_TOKEN"']
            elif self.name == "gemini":
                command += ["--experimental-acp", "--approval-mode", "default", "--allowed-mcp-server-names", "raphael"]
                if self.model:
                    command += ["--model", self.model]
            else:
                raise ValueError("unknown_cli")
            environ = dict(os.environ if self.environ is None else self.environ)
            if self.name == "codex":
                environ["RAPHAEL_MCP_TOKEN"] = token
            self.child = Child(command, environ=environ, cwd=cwd or str(files.path))
            await self.child.start()
            if self.name == "claude":
                await self.child.send({"type": "user", "message": {"role": "user", "content": user}})
                self.child.process.stdin.close()
                async for event in self._claude():
                    yield event
            else:
                async for event in self._rpc(user, system, endpoint, token, session, permission):
                    yield event
        except (OSError, ValueError, TimeoutError, EOFError, asyncio.IncompleteReadError) as error:
            logger.warning("CLI transport failed: %s", type(error).__name__)
            yield Event("error", "protocol_or_process_error")
        finally:
            try:
                await self.interrupt()
            finally:
                files.close()

    async def _read(self):
        line = await self.child.process.stdout.readline()
        if not line:
            raise EOFError("cli_exited")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("invalid_packet")
        return value

    async def _claude(self):
        text = ""
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
                    text = (text + delta.get("text", ""))[-64000:]
                    yield Event("text_delta", delta.get("text", ""))
                elif event.get("type") == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        yield Event("tool_start", data={"name": block.get("name", "tool")})
            elif kind == "assistant":
                for block in packet.get("message", {}).get("content", []):
                    if block.get("type") == "text" and not partial:
                        text = (text + block.get("text", ""))[-64000:]
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
                    yield Event("error", "cli_failed")
                else:
                    yield Event("final", packet.get("result") or text)
                return

    async def _rpc(self, user, system, endpoint, token, session, permission):
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

        codex = self.name == "codex"
        await request("initialize", {"clientInfo": {"name": "raphael", "version": "0.2.0"}, "capabilities": {}} if codex else
                      {"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                       "clientInfo": {"name": "raphael", "version": "0.2.0"}}, "initialize")
        text = ""
        tool_kinds = {}
        while True:
            packet = await self._read()
            method, params = packet.get("method", ""), packet.get("params", {})
            if not method and packet.get("id") in pending:
                label = pending.pop(packet["id"])
                if "error" in packet:
                    yield Event("error", "cli_request_failed")
                    return
                result = packet.get("result") or {}
                if label == "initialize":
                    if codex:
                        await self.child.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                        config = {"mcp_servers": {"raphael": {"url": endpoint,
                                  "http_headers": {"Authorization": f"Bearer {token}"}}}}
                        arguments = {"developerInstructions": system, "approvalPolicy": "untrusted",
                                     "sandbox": "read-only", "config": config, "cwd": self.child.cwd}
                        if self.model:
                            arguments["model"] = self.model
                        if session:
                            arguments["threadId"] = session
                        await request("thread/resume" if session else "thread/start", arguments, "session")
                    else:
                        arguments = {"cwd": self.child.cwd, "mcpServers": [
                            {"name": "raphael", "type": "http", "url": endpoint,
                             "headers": [{"name": "Authorization", "value": f"Bearer {token}"}]}]}
                        if session:
                            arguments["sessionId"] = session
                        await request("session/load" if session else "session/new", arguments, "session")
                elif label == "session":
                    self.session = (result.get("thread") or {}).get("id") if codex else result.get("sessionId")
                    if not self.session:
                        raise ValueError("missing_session")
                    yield Event("session", self.session)
                    if codex:
                        await request("turn/start", {"threadId": self.session, "input": [{"type": "text", "text": user}]}, "turn")
                    else:
                        await request("session/prompt", {"sessionId": self.session, "prompt": [
                            {"type": "text", "text": system + "\n\n" + user}]}, "turn")
                elif label == "turn":
                    if codex:
                        self.turn = (result.get("turn") or {}).get("id")
                    else:
                        if result.get("stopReason") not in ("end_turn", "max_tokens"):
                            yield Event("error", "cli_turn_failed")
                        else:
                            yield Event("final", text)
                        return
            elif "id" in packet and method:
                if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                              "execCommandApproval", "applyPatchApproval", "session/request_permission"):
                    yield Event("permission", data={"method": method})
                    decision = await permission({"method": method, **params})
                    if codex:
                        await response(packet["id"], {"decision": {"once": "accept", "always": "acceptForSession"}.get(decision, "decline")})
                    else:
                        kinds = ("allow_always", "allow_once") if decision == "always" else ("allow_once",) if decision == "once" else ("reject_once", "reject_always")
                        option = next((item for kind in kinds for item in params.get("options", []) if item.get("kind") == kind), None)
                        await response(packet["id"], {"outcome": {"outcome": "selected", "optionId": option["optionId"]} if option else {"outcome": "cancelled"}})
                elif codex and method == "mcpServer/elicitation/request":
                    # Codex asks before it calls an MCP tool. Raphael's own server screens every call
                    # through the safety gate (and asks the owner when the rules say so), so a call to
                    # it is accepted here; any other server is declined.
                    await response(packet["id"], {"action": "accept", "content": {}} if params.get("serverName") == "raphael"
                                   else {"action": "decline"})
                else:
                    # No filesystem, terminal or arbitrary client-side RPC outside our gate.
                    await response(packet["id"], error={"code": -32601, "message": "Unsupported client capability"})
            elif codex:
                if method == "item/agentMessage/delta":
                    delta = params.get("delta", "")
                    text = (text + delta)[-64000:]
                    yield Event("text_delta", delta)
                elif method == "item/reasoning/summaryTextDelta":
                    yield Event("thinking", params.get("delta", ""))
                elif method in ("item/started", "item/completed"):
                    item = params.get("item", {})
                    if item.get("type") in ("commandExecution", "fileChange", "mcpToolCall", "webSearch"):
                        yield Event("tool_start" if method.endswith("started") else "tool_end", data={
                            "name": item.get("tool", item["type"]), "effect": item["type"] == "fileChange",
                            "ok": item.get("status") == "completed"})
                elif method == "thread/tokenUsage/updated":
                    yield Event("usage", data=(params.get("tokenUsage") or {}).get("last", {}))
                elif method == "turn/started":
                    self.turn = (params.get("turn") or {}).get("id")
                elif method == "turn/completed":
                    if (params.get("turn") or {}).get("status") == "completed":
                        yield Event("final", text)
                    else:
                        yield Event("error", "cli_turn_failed")
                    return
                elif method == "error":
                    yield Event("error", "cli_error")
                    return
            elif method == "session/update":
                update = params.get("update", {})
                kind = update.get("sessionUpdate")
                if kind == "agent_message_chunk" and update.get("content", {}).get("type") == "text":
                    delta = update["content"].get("text", "")
                    text = (text + delta)[-64000:]
                    yield Event("text_delta", delta)
                # ACP agent_thought_chunk is private reasoning, not a public summary.
                elif kind in ("tool_call", "tool_call_update"):
                    identifier = update.get("toolCallId")
                    if update.get("kind"):
                        tool_kinds[identifier] = update["kind"]
                    yield Event("tool_end" if update.get("status") in ("completed", "failed") else "tool_start", data={
                        "name": "fileChange" if tool_kinds.get(identifier) in ("edit", "delete", "move") else "tool",
                        "effect": tool_kinds.get(identifier) in ("edit", "delete", "move"),
                        "ok": update.get("status") == "completed"})
                elif kind == "usage_update":
                    yield Event("usage", data=update)
