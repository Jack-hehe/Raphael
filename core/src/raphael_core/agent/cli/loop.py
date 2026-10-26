"""Keep a CLI and its MCP requests alive across the existing inline approval cards."""

import asyncio
import json
import logging
import re
import traceback
import time
from contextlib import aclosing

from ...worker.loop import Call, Outcome, Stop, ToolOutcome, WAITING
from ...worker.guards import RepeatGuard, wrap_untrusted
from ..answers import MEMORY_ITEM, parse_reply
from ..conversations import Conversations
from ..mcp_server import RaphaelMcp
from ...brain.textnorm import public_line
from ..narration import PUBLIC_PROGRESS_RULES
from ..narration import _content
from ..policy import CONFIRM_TIMEOUT_S
from .discovery import NAMES, brain_ref, brain_effort
from .drivers import Driver
from .process import secure_directory

logger = logging.getLogger(__name__)

# The line a CLI brain puts between two chat messages of one reply.
MESSAGE_BREAK = "<<<next message>>>"
# A rate-limited turn that has not acted yet runs again after each of these waits.
RATE_LIMIT_WAITS_S = (20.0, 60.0)
# A room or background turn has no one sitting on it: it rides out a throttle of minutes (live, 429s lasted several).
ROOM_RATE_LIMIT_WAITS_S = (30.0, 60.0, 120.0, 240.0)

APPROVE = {"type": "function", "function": {"name": "approve", "description": "Ask the owner for permission. Never grants permission by itself.",
           "parameters": {"type": "object", "properties": {"tool_name": {"type": "string"}, "input": {"type": "object"}}, "additionalProperties": True}}}
# The fields an API brain's notes have: live, a CLI brain offered free strings wrote type "fact", which no space takes.
MEMORY_TOOLS = [{"type": "function", "function": {"name": name, "description": "Change only a memory the owner explicitly asked to remember or forget.",
    "parameters": {"type": "object", "properties": {key: value for key, value in MEMORY_ITEM["properties"].items() if key != "op"},
                   "required": required}}}
    for name, required in (("memory_write", ["name", "type", "description", "body", "space"]), ("memory_delete", ["name", "space"]))]


class Sessions:
    def __init__(self, conn):
        self.conn = conn
        self.locks = {}
        self.conversations = Conversations(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS agent_cli_sessions (member_id INTEGER NOT NULL, brain TEXT NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY(member_id, brain))")
        conn.execute("CREATE TRIGGER IF NOT EXISTS delete_member_cli_sessions AFTER DELETE ON members BEGIN DELETE FROM agent_cli_sessions WHERE member_id=OLD.id; END")
        # Existing native resume IDs belong only to the member's default conversation.
        with conn:
            conn.execute("INSERT OR IGNORE INTO agent_conversation_cli_sessions SELECT c.id, s.brain, s.session_id FROM agent_cli_sessions s JOIN conversations c ON c.member_id=s.member_id AND c.is_default=1")
            conn.execute("DELETE FROM agent_cli_sessions WHERE member_id IN (SELECT member_id FROM conversations WHERE is_default=1)")

    def get(self, member, brain, conversation_id=None):
        if conversation_id is None:
            conversation_id = self.conversations.main(member)
        row = self.conn.execute("SELECT s.session_id FROM agent_conversation_cli_sessions s JOIN conversations c ON c.id=s.conversation_id WHERE c.id=? AND c.member_id=? AND s.brain=?", (conversation_id, member, brain)).fetchone()
        return row[0] if row else None

    def put(self, member, brain, session, conversation_id=None):
        if not isinstance(session, str) or not 0 < len(session) <= 512:
            return
        if conversation_id is None:
            conversation_id = self.conversations.main(member)
        # A deleted conversation cannot be revived by a late session notification.
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO agent_conversation_cli_sessions SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM conversations WHERE id=? AND member_id=?)", (conversation_id, brain, session, conversation_id, member))

    def clear(self):
        with self.conn:
            self.conn.execute("DELETE FROM agent_conversation_cli_sessions")
            self.conn.execute("DELETE FROM agent_cli_sessions")

    def clear_member(self, member_id):
        with self.conn:
            self.conn.execute(
                "DELETE FROM agent_conversation_cli_sessions WHERE conversation_id IN "
                "(SELECT id FROM conversations WHERE member_id = ?)",
                (member_id,),
            )
            self.conn.execute("DELETE FROM agent_cli_sessions WHERE member_id = ?", (member_id,))


class CliLoop:
    def __init__(self, *, brains, sessions, member_id, model, ledger, language, plain,
                 system, user, tools, progress, thinking, event, execute, expire=None, spoken=False,
                 conversation_id=None, quota=None, attachments=(), wait_timeout_s=None, resume_session=True,
                 rate_limit_waits=None):
        self.brains, self.sessions, self.member_id, self.model = brains, sessions, member_id, model
        self.conversation_id = sessions.conversations.resolve(member_id, conversation_id) if sessions else conversation_id
        self.quota = quota
        self.resume_session = resume_session
        # The waits before running a rate-limited request again: None waits as for a turn someone is reading
        # (RATE_LIMIT_WAITS_S), () never waits.
        self.rate_limit_waits = rate_limit_waits
        self.ledger, self.language, self.plain = ledger, language, plain
        self.spoken = spoken
        self.system = system + "\n" + PUBLIC_PROGRESS_RULES + "\nUse only Raphael MCP tools for actions and lookups. "
        self.system += (
            'For the spoken reply only, return one JSON object with text, messages (usually one; several only '
            'when naturally separate, at most four), '
            'and say_ja (the Japanese rendering for the voice). No other fields or prose.'
            if spoken else (
                "Reply in plain text (Markdown is fine). It is one chat message unless it is naturally "
                "several (an answer, then a separate exercise or question): then put a line with only "
                f"{MESSAGE_BREAK} between them, at most four. Never split one thought or pad to fill. "
                "Never output a JSON reply envelope."
            )
        )
        self.user, self.tools = user, list(tools) + [APPROVE]
        self.images = [attachment for attachment in attachments if attachment.is_image]
        self.progress, self.thinking, self.event, self.execute = progress, thinking, event, execute
        self.seen, self.batch_calls = [], []
        self.response = None
        self.permission_always = False
        # Why the turn failed ("missing", "login", "busy", "error", "quota", "rate_limited"), else None.
        self.failure = None
        self.actual_model = None
        self.driver = None
        self.task = None
        self._queue = asyncio.Queue()
        self._resume = None
        self._serial = asyncio.Lock()
        self._handoff_pending = False
        self._buffer = ""
        self._thought = ""
        self._calls = 0
        self._acted = False
        self._usage = {}
        self._repeats = RepeatGuard()
        self.on_done = None
        self.expire = expire
        self.wait_timeout_s = CONFIRM_TIMEOUT_S if wait_timeout_s is None else wait_timeout_s

    @property
    def answered_by(self):
        """The model the turn records: the one the CLI's thread said answered, at the effort asked
        for, or the one asked for when the CLI names none."""
        if not self.actual_model:
            return self.model
        effort = brain_effort(self.model)
        return f"cli:{brain_ref(self.model)[0]}:{self.actual_model}" + (f"?effort={effort}" if effort else "")

    def _progress(self):
        if line := public_line(self._buffer, self.language):
            self.progress(line[:400])
        self._buffer = ""

    async def _tool(self, identifier, name, args):
        async with self._serial:
            if self._handoff_pending:
                return ToolOutcome(False, 'waiting_for_handoff')
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
            if result.ok and result.kind == 'handoff':
                self._handoff_pending = True
                self._queue.put_nowait(Outcome('stopped',kind='handoff'))
            return ToolOutcome(result.ok, result.kind, wrap_untrusted(name, result.instruction or result.content or result.kind or "", ok=result.ok))

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
                return await asyncio.wait_for(self._resume, self.wait_timeout_s + .1)
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
        getter = asyncio.ensure_future(self._queue.get())
        try:
            await asyncio.wait({getter, self.task}, return_when=asyncio.FIRST_COMPLETED)
            if not getter.done() and not self.task.cancelled():
                # The work ended without an outcome: it raised outside its own handling. Without this the
                # turn would wait forever.
                if self.task.exception() is not None:
                    logger.warning("CLI turn work failed before an outcome: %s", type(self.task.exception()).__name__)
                self._failure("error", NAMES[brain_ref(self.model)[0]])
            outcome = await getter
            if outcome.kind not in (WAITING, 'handoff'):
                if self.response is not None:
                    await self.aclose()
                else:
                    if not self.task.cancelled():
                        await asyncio.gather(self.task, return_exceptions=True)
            return outcome
        except asyncio.CancelledError:
            getter.cancel()
            await self.aclose()
            raise

    def cancel(self):
        if self.task and not self.task.cancelling():
            self.task.cancel()

    async def aclose(self):
        self.cancel()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)

    async def disconnect(self):
        """Stop the child and release the waiting turn with the usual connection guidance."""
        await self.aclose()
        self._failure("login", NAMES[brain_ref(self.model)[0]])

    def _failure(self, reason, name):
        self.failure = reason
        messages = {
            "missing": f"請先安裝 {name}，再到設定 › 模型與用量登入。",
            "login": f"請先到設定 › 模型與用量 › 訂閱帳號，為 {name} 按下「連線」。",
            "busy": "這個對話仍有 CLI 回合執行中，請先完成或取消它。",
            "error": f"{name} 回合中斷。請確認 CLI 登入狀態後再試一次。",
            "quota": f"{name} 的訂閱額度暫時用完了，請稍後再試，或換一個模型。",
            "rate_limited": f"{name} 目前請求太頻繁而被限流，請稍後再試。",
        }
        if str(self.language).startswith("ja"):
            messages = {
                "missing": f"先に {name} をインストールし、設定 › モデルと使用量でログインしてください。",
                "login": f"設定 › モデルと使用量 › サブスクリプションアカウントで {name} の「接続」を押してください。",
                "busy": "この会話ではまだ CLI ターンが実行中です。完了するかキャンセルしてください。",
                "error": f"{name} が予期せず停止しました。ログイン状態を確認してからもう一度お試しください。",
                "quota": f"{name} の利用上限に達しました。しばらく待つか、別のモデルを選んでください。",
                "rate_limited": f"{name} はリクエストが多すぎるため一時的に制限されています。しばらくしてからもう一度お試しください。",
            }
        elif self.language != "zh-TW":
            messages = {"missing": f"Install {name}, then sign in in Settings > Models & usage.",
                        "login": f"Connect {name} in Settings > Models & usage > Subscription accounts.",
                        "busy": "This thread has an active CLI turn. Finish or cancel it first.",
                        "error": f"{name} stopped unexpectedly. Check its login and try again.",
                        "quota": f"{name} has used up its subscription quota for now. Try again later or pick another model.",
                        "rate_limited": f"{name} is rate-limited right now (too many requests). Try again in a little while."}
        self.event(messages[reason])
        self._queue.put_nowait(Outcome("done", final=messages[reason]))

    def _rate_limit_wait(self, name, seconds):
        seconds = f"{seconds:g}"
        if str(self.language).startswith("ja"):
            return f"{name} はレート制限中です。{seconds} 秒後に再試行します。"
        if self.language != "zh-TW":
            return f"{name} is rate-limited; retrying in {seconds} s."
        return f"{name} 暫時限流，{seconds} 秒後重試。"

    async def _work(self):
        name, model = brain_ref(self.model)
        try:
            command = self.brains.command(name)
        except OSError:
            command = None
        if not command:
            self._failure("missing", NAMES[name])
            return
        if not self.brains.available(name, command=command):
            self._failure("login", NAMES[name])
            return
        lock = self.sessions.locks.setdefault((self.member_id, self.conversation_id), asyncio.Lock())
        if lock.locked():
            self._failure("busy", NAMES[name])
            return
        async with lock:
            began = time.perf_counter()
            try:
                # A stable, empty workspace keeps native CLI resume independent of turn temp files.
                workspace = self.brains.home / ".raphael" / "cli-workspaces" / str(self.member_id) / str(self.conversation_id) / name
                workspace.mkdir(parents=True, exist_ok=True)
                await secure_directory(workspace)
                async with asyncio.timeout(1800), RaphaelMcp(lambda: self.tools, self._tool) as server:
                    session = self.sessions.get(self.member_id, self.model, self.conversation_id) if self.resume_session else None
                    waits = list(RATE_LIMIT_WAITS_S if self.rate_limit_waits is None else self.rate_limit_waits)
                    failure = await self._attempt(name, command, model, server, session, workspace)
                    # Running the request again is safe only while nothing of it has happened yet.
                    while failure == "rate_limited" and waits and not (self._acted or self._calls):
                        wait = waits.pop(0)
                        self.progress(self._rate_limit_wait(NAMES[name], wait))
                        await asyncio.sleep(wait)
                        failure = await self._attempt(name, command, model, server, session, workspace)
                if failure:
                    self._failure(failure, NAMES[name])
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
                    value = next((self._usage[key] for key in keys if key in self._usage), 0)
                    return value if type(value) is int and 0 <= value < 10**10 else 0
                self.ledger.add(model=self.answered_by, cost_usd=0,
                    ms=int((time.perf_counter() - began) * 1000),
                    prompt_tokens=tokens("input_tokens", "inputTokens", "promptTokenCount"),
                    completion_tokens=tokens("output_tokens", "outputTokens", "candidatesTokenCount"))

    async def _attempt(self, name, command, model, server, session, workspace):
        """Run the CLI once for this turn: None once its answer is queued, else why it failed."""
        self._buffer = self._thought = ""
        self.driver = Driver(name, command, model=model, effort=brain_effort(self.model), environ=self.brains.environ, quota=self.quota, home=self.brains.home)
        async with aclosing(self.driver.run(user=self.user, system=self.system, endpoint=server.url,
                token=server.token, session=session, permission=self._native_permission,
                cwd=str(workspace), images=self.images if name in ("codex", "claude") else ())) as stream:
            async for event in stream:
                if event.kind == "session" and self.resume_session:
                    self.sessions.put(self.member_id, self.model, event.text, self.conversation_id)
                elif event.kind == "model":
                    self.actual_model = event.text
                elif event.kind == "text_delta":
                    self._buffer = (self._buffer + event.text)[-64000:]
                elif event.kind == "thinking":
                    self._thought = (self._thought + event.text)[-240:]
                elif event.kind in ("tool_start", "permission"):
                    self._acted = True
                    self._progress()
                    if line := public_line(self._thought, self.language):
                        self.thinking(line[:240])
                    self._thought = ""
                elif event.kind == "usage":
                    self._usage = event.data
                elif event.kind == "tool_end" and event.data.get("effect") and event.data.get("ok"):
                    self.event("CLI 回報檔案已變更" if self.language == "zh-TW" else "CLI reported a file change")
                elif event.kind == "error":
                    return event.text if event.text in ("quota", "rate_limited") else "error"
                elif event.kind == "final":
                    final = event.text.strip()
                    if final:
                        self._queue.put_nowait(Outcome("done", final=final))
                        return None
                    self._progress()
                    return "error"
        return "error"

    async def _native_permission(self, args):
        async with self._serial:
            return await self._permission(args)

    def reply(self, outcome):
        if self.spoken:
            objects, prose = _content(outcome.final)
            data = next((obj for obj in reversed(objects) if isinstance(obj, dict) and ("text" in obj or "messages" in obj)), None)
            if data is not None:
                return parse_reply(data, self.language, plain=self.plain, long=True)
        # paragraphs stay in one message; only the model's own breaks make several
        parts = [part.strip() for part in re.split(rf"^\s*{re.escape(MESSAGE_BREAK)}\s*$", outcome.final, flags=re.M) if part.strip()]
        if len(parts) > 4:
            parts = parts[:3] + ["\n\n".join(parts[3:])]
        text = "\n\n".join(parts) if parts else outcome.final
        return parse_reply({"text": text, "messages": parts or [outcome.final]}, self.language, plain=self.plain, long=True)
