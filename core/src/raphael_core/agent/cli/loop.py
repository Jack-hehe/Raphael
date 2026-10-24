"""Keep a CLI and its MCP requests alive across the existing inline approval cards."""

import asyncio
import json
import logging
import re
import traceback
from contextlib import aclosing

from ...worker.loop import Call, Outcome, Stop, ToolOutcome, WAITING
from ...worker.guards import RepeatGuard, wrap_untrusted
from ..answers import parse_reply
from ..mcp_server import RaphaelMcp
from ...brain.textnorm import public_line
from ..narration import PUBLIC_PROGRESS_RULES
from ..narration import _content
from ..policy import CONFIRM_TIMEOUT_S
from .discovery import NAMES, brain_ref, brain_effort
from .drivers import Driver
from .process import secure_directory

logger = logging.getLogger(__name__)

APPROVE = {"type": "function", "function": {"name": "approve", "description": "Ask the owner for permission. Never grants permission by itself.",
           "parameters": {"type": "object", "properties": {"tool_name": {"type": "string"}, "input": {"type": "object"}}, "additionalProperties": True}}}
MEMORY_TOOLS = [{"type": "function", "function": {"name": name, "description": "Change only a memory the owner explicitly asked to remember or forget.",
    "parameters": {"type": "object", "properties": {key: {"type": "string"} for key in ("name", "type", "description", "body", "expires", "space")}, "required": ["name"]}}}
    for name in ("memory_write", "memory_delete")]


class Sessions:
    def __init__(self, conn):
        self.conn = conn
        self.locks = {}
        conn.execute("CREATE TABLE IF NOT EXISTS agent_cli_sessions (member_id INTEGER NOT NULL, brain TEXT NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY(member_id, brain))")
        conn.execute("CREATE TRIGGER IF NOT EXISTS delete_member_cli_sessions AFTER DELETE ON members BEGIN DELETE FROM agent_cli_sessions WHERE member_id=OLD.id; END")
        conn.commit()

    def get(self, member, brain):
        row = self.conn.execute("SELECT session_id FROM agent_cli_sessions WHERE member_id=? AND brain=?", (member, brain)).fetchone()
        return row[0] if row else None

    def put(self, member, brain, session):
        if not isinstance(session, str) or not 0 < len(session) <= 512:
            return
        self.conn.execute("INSERT OR REPLACE INTO agent_cli_sessions VALUES(?,?,?)", (member, brain, session))
        self.conn.commit()

    def clear(self):
        self.conn.execute("DELETE FROM agent_cli_sessions")
        self.conn.commit()

    def clear_member(self, member_id):
        self.conn.execute("DELETE FROM agent_cli_sessions WHERE member_id = ?", (member_id,))
        self.conn.commit()


class CliLoop:
    def __init__(self, *, brains, sessions, member_id, model, ledger, language, plain,
                 system, user, tools, progress, thinking, event, execute, expire=None, spoken=False):
        self.brains, self.sessions, self.member_id, self.model = brains, sessions, member_id, model
        self.ledger, self.language, self.plain = ledger, language, plain
        self.spoken = spoken
        self.system = system + "\n" + PUBLIC_PROGRESS_RULES + "\nUse only Raphael MCP tools for actions and lookups. "
        self.system += (
            'For the spoken reply only, return one JSON object with text, messages (at most four), '
            'and say_ja (the Japanese rendering for the voice). No other fields or prose.'
            if spoken else "Reply in plain text, at most four paragraphs. Never output a JSON reply envelope."
        )
        self.user, self.tools = user, list(tools) + [APPROVE]
        self.progress, self.thinking, self.event, self.execute = progress, thinking, event, execute
        self.seen, self.batch_calls = [], []
        self.response = None
        self.permission_always = False
        self.failed = False
        self.driver = None
        self.task = None
        self._queue = asyncio.Queue()
        self._resume = None
        self._serial = asyncio.Lock()
        self._buffer = ""
        self._thought = ""
        self._calls = 0
        self._repeats = RepeatGuard()
        self.on_done = None
        self.expire = expire

    def _progress(self):
        if line := public_line(self._buffer.strip()[:400], self.language):
            self.progress(line)
        self._buffer = ""

    async def _tool(self, identifier, name, args):
        async with self._serial:
            self._calls += 1
            if self._calls > 200:
                return ToolOutcome(False, "tool_limit")
            if not self._repeats.allows(name, args):
                return ToolOutcome(False, "repeated")
            self._progress()
            if name == "approve":
                decision = await self._permission(args)
                if decision == "deny":
                    answer = {"behavior": "deny", "message": "The owner did not approve this action."}
                else:
                    answer = {"behavior": "allow", "updatedInput": args.get("input", args.get("tool_input", {}))}
                    if decision == "always" and isinstance(args.get("tool_name"), str):
                        answer["updatedPermissions"] = [{"type": "addRules", "rules": [{"toolName": args["tool_name"]}],
                                                        "behavior": "allow", "destination": "session"}]
                return ToolOutcome(True, content=json.dumps(answer))
            result = await self._execute(identifier, name, args)
            return ToolOutcome(result.ok, result.kind, wrap_untrusted(name, result.content or result.kind or "", ok=result.ok))

    async def _execute(self, identifier, name, args):
        self.batch_calls = [Call(identifier, name, args)]
        try:
            return await self.execute(identifier, name, args)
        except Stop as stop:
            if stop.kind != WAITING:
                self._queue.put_nowait(Outcome("stopped", kind=stop.kind))
                return ToolOutcome(False, "stopped")
            self._resume = asyncio.get_running_loop().create_future()
            self._queue.put_nowait(Outcome("stopped", kind=WAITING))
            try:
                return await asyncio.wait_for(self._resume, CONFIRM_TIMEOUT_S + .1)
            except TimeoutError:
                if self.expire:
                    self.expire()
                self.cancel()
                return ToolOutcome(False, "expired")

    async def _permission(self, args):
        self._progress()
        outcome = await self._execute("permission", "cli_permission", args)
        return ("always" if self.permission_always else "once") if outcome.ok else "deny"

    def continue_with(self, outcome):
        self.response = None
        if self._resume and not self._resume.done():
            self._resume.set_result(outcome)

    async def run(self):
        if self.task is None:
            self.task = asyncio.create_task(self._work())
            if self.on_done:
                self.task.add_done_callback(lambda _: self.on_done(self))
        try:
            outcome = await self._queue.get()
            if outcome.kind != WAITING:
                if self.response is not None:
                    await self.aclose()
                else:
                    await self.task
            return outcome
        except asyncio.CancelledError:
            await self.aclose()
            raise

    def cancel(self):
        if self.task and not self.task.cancelling():
            self.task.cancel()

    async def aclose(self):
        self.cancel()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)

    def _failure(self, reason, name):
        self.failed = True
        messages = {
            "missing": f"請先安裝 {name}，再到設定 › 模型與用量登入。",
            "login": f"請先在設定 › 模型與用量登入 {name}。",
            "busy": "這個對話仍有 CLI 回合執行中，請先完成或取消它。",
            "error": f"{name} 回合中斷。請確認 CLI 登入狀態後再試一次。",
            "quota": f"{name} 的訂閱額度暫時用完了，請稍後再試，或換一個模型。",
        }
        if self.language != "zh-TW":
            messages = {"missing": f"Install {name}, then sign in in Settings > Models & usage.",
                        "login": f"Sign in to {name} in Settings > Models & usage.",
                        "busy": "This thread has an active CLI turn. Finish or cancel it first.",
                        "error": f"{name} stopped unexpectedly. Check its login and try again.",
                        "quota": f"{name} has used up its subscription quota for now. Try again later or pick another model."}
        self.event(messages[reason])
        self._queue.put_nowait(Outcome("done", final=messages[reason]))

    async def _work(self):
        name, model = brain_ref(self.model)
        try:
            command = self.brains.command(name)
        except OSError:
            command = None
        if not command:
            self._failure("missing", NAMES[name])
            return
        if self.brains.logged_in(name) is False:
            self._failure("login", NAMES[name])
            return
        lock = self.sessions.locks.setdefault(self.member_id, asyncio.Lock())
        if lock.locked():
            self._failure("busy", NAMES[name])
            return
        async with lock:
            usage = {}
            try:
                # A stable, empty workspace keeps native CLI resume independent of turn temp files.
                workspace = self.brains.home / ".raphael" / "cli-workspaces" / str(self.member_id) / name
                workspace.mkdir(parents=True, exist_ok=True)
                await secure_directory(workspace)
                async with asyncio.timeout(1800), RaphaelMcp(lambda: self.tools, self._tool) as server:
                    self.driver = Driver(name, command, model=model, effort=brain_effort(self.model), environ=self.brains.environ)
                    async with aclosing(self.driver.run(user=self.user, system=self.system, endpoint=server.url,
                            token=server.token, session=self.sessions.get(self.member_id, self.model), permission=self._native_permission,
                            cwd=str(workspace))) as stream:
                        async for event in stream:
                            if event.kind == "session":
                                self.sessions.put(self.member_id, self.model, event.text)
                            elif event.kind == "text_delta":
                                self._buffer = (self._buffer + event.text)[-64000:]
                            elif event.kind == "thinking":
                                self._thought = (self._thought + event.text)[-240:]
                            elif event.kind in ("tool_start", "permission"):
                                self._progress()
                                if line := public_line(self._thought.strip()[:240], self.language):
                                    self.thinking(line)
                                self._thought = ""
                            elif event.kind == "usage":
                                usage = event.data
                            elif event.kind == "tool_end" and event.data.get("effect") and event.data.get("ok"):
                                self.event("CLI 回報檔案已變更" if self.language == "zh-TW" else "CLI reported a file change")
                            elif event.kind == "error":
                                self._failure("quota" if event.text == "quota" else "error", NAMES[name])
                                return
                            elif event.kind == "final":
                                final = self._buffer.strip() or event.text.strip()
                                if not final:
                                    self._failure("error", NAMES[name])
                                else:
                                    self._queue.put_nowait(Outcome("done", final=final))
                                return
                self._failure("error", NAMES[name])
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Protocol errors never publish provider diagnostics or private packet contents.
                frame = traceback.extract_tb(error.__traceback__)[-1]
                logger.warning("CLI turn failed: %s at %s:%d", type(error).__name__, frame.name, frame.lineno)
                self._failure("error", NAMES[name])
            finally:
                if self.driver:
                    await self.driver.interrupt()
                def tokens(*keys):
                    value = next((usage[key] for key in keys if key in usage), 0)
                    return value if type(value) is int and 0 <= value < 10**10 else 0
                self.ledger.add(model=self.model, cost_usd=0,
                    prompt_tokens=tokens("input_tokens", "inputTokens", "promptTokenCount"),
                    completion_tokens=tokens("output_tokens", "outputTokens", "candidatesTokenCount"))

    async def _native_permission(self, args):
        async with self._serial:
            return await self._permission(args)

    def reply(self, outcome):
        if self.spoken:
            objects, prose = _content(outcome.final)
            data = next((obj for obj in reversed(objects) if isinstance(obj, dict) and ("text" in obj or "messages" in obj)), None)
            if data is not None:
                return parse_reply(data, self.language, plain=self.plain, long=True)
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", outcome.final) if part.strip()]
        if len(paragraphs) > 4:
            paragraphs = paragraphs[:3] + ["\n\n".join(paragraphs[3:])]
        return parse_reply({"text": outcome.final, "messages": paragraphs}, self.language, plain=self.plain, long=True)
