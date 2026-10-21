"""The background worker as the assistant sees it: tasks handed over, run one at a time, reported.

How a task travels. A request the router calls `delegate` (several steps, or a connected service)
is handed to `submit`, which writes the task down and queues it; the assistant answers 《了。》 at
once and the request is over. `_drain` takes the tasks one by one (one runs at a time, three may
wait; a fourth is refused): it takes a snapshot of the tools the task may use (`snapshot`), writes
the two prompts, and has the worker process carry it out (`host`), answering the worker's model
calls and tool calls itself (`run`). When the worker is done, the report is written to the task
and to the assistant's history, and told to the app:

- `worker.report {task_id, state, prefix, text}`: Raphael's report, a line to be said on the
  overlay when the user is not in the middle of something (the shell waits for that);
- `worker.changed {task_id, state, running, model_calls, tool_calls, cost_usd}`: the task moved on.
  `running` is whether a task is under way, which the tray's "stop the background task" follows.

A report is the model's own (`告。` and a line, with a longer version for the history) when the task
finished. When it did not (a limit, the user's stop, a worker that died, a model that failed) it is a
fixed line of the persona with the number of tool calls made, and nothing more is spent for it.

RPC methods, for the app: `worker.list {limit?}`, `worker.get {id}` (with the steps; never an
argument or a result) and `worker.cancel {task_id?}` (the task that runs, or the one named).

Everything runs on the event loop, which owns the database. Nothing here logs what a task is, or
what it said: only ids, states, counts and costs.
"""

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from collections.abc import Callable, Sequence
from datetime import tzinfo
from typing import Any

from ..agent.answers import Reply, parse_reply
from ..agent.failure import KINDS, AgentFailure
from ..agent.gate import ToolGate
from ..agent.history import AgentHistory, Turn
from ..agent.ledger import Ledger
from ..agent.persona import (
    PREFIXES,
    language_reminder,
    phrase,
    worker_system_prompt,
)
from ..agent.tools import Tools
from ..brain.openrouter import OpenRouterClient
from ..brain.settings import DEFAULTS, SettingsStore
from ..brain.spend import SpendTracker
from ..protocol import INVALID_PARAMS, NOT_FOUND, RpcError
from ..router import Router
from ..worker.guards import neutralize
from .host import WorkerCrashed, WorkerHost, WorkerStopped, WorkerUnavailable
from .run import RunLimits, TaskRun
from .snapshot import take_snapshot
from .tasks import QUEUED, RUNNING, Refused, Task, TaskStore

logger = logging.getLogger(__name__)

# One task runs; this many may wait behind it. A task beyond them is refused.
MAX_QUEUED = 3
# How many turns of the conversation a task is shown.
CONTEXT_TURNS = 3
# The core waits this much longer than the task's own time for the worker to say it is done.
TIME_NET_S = 20.0
# The assistant's history calls a task by the status of a turn.
_TURN_STATUS = {"done": "done", "cancelled": "denied"}

_FINAL_FENCE = "```"


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _invalid(message: str) -> RpcError:
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


def report_from(final: str, language: str) -> Reply | None:
    """The report the model wrote at the end: a JSON object with `prefix`, `text` and `detail`, as
    it was asked, possibly in a code fence or with words around it; or, when it wrote plain text, that
    text as a report. None when it said nothing usable."""
    text = final.strip()
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    data: Any = None
    if 0 <= start < end:
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            data = None
    if not isinstance(data, dict):
        if text.startswith(("{", "[", _FINAL_FENCE)):
            return None  # an attempt at JSON that is not: showing it would show braces
        data = {"prefix": PREFIXES[language].notice, "text": text, "detail": ""}
    try:
        return parse_reply(data, language)
    except AgentFailure:
        return None


def _prompts(
    language: str,
    text: str,
    moment: dict[str, str],
    context: Sequence[Turn],
    limits: RunLimits,
) -> tuple[str, str]:
    """The system prompt and the first message of the user for a task."""
    system = worker_system_prompt(
        language, model_calls=limits.model_calls, tool_calls=limits.tool_calls
    )
    parts = [
        f"Current date and time: {moment.get('date', '')} {moment.get('time', '')}, "
        f"{moment.get('weekday', '')} ({moment.get('utc_offset', '')})"
    ]
    if context:
        lines = [
            neutralize(f"User: {turn.question}\nRaphael: {turn.text or turn.prefix}")
            for turn in context
        ]
        parts.append("Conversation so far (oldest first):\n" + "\n".join(lines))
    parts.append(f"<task>\n{neutralize(text)}\n</task>")
    parts.append(language_reminder(language))
    return system, "\n\n".join(parts)


class BackgroundService:
    """The background worker's tasks. `conn` is the open database (used on the event loop only),
    `client` the OpenRouter client, `gate` the one gate shared with the fast tier, `tools` Raphael's
    own lookups (this service closes them), and `notify` sends a notification to the app. `host`
    is the worker process (tests give one with a worker of their own); `limits` what one task may
    use; `now_ms` is the wall clock, `tz` the time zone of the spend's months and `clock` a
    monotonic clock in seconds."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: OpenRouterClient,
        gate: ToolGate,
        tools: Tools,
        *,
        notify: Callable[[str, dict], None],
        host: WorkerHost | None = None,
        limits: RunLimits = RunLimits(),
        now_ms: Callable[[], int] = _wall_clock_ms,
        tz: tzinfo | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._gate = gate
        self._tools = tools
        self._notify = notify
        self._host = host if host is not None else WorkerHost()
        self._limits = limits
        self._clock = clock
        self._store = TaskStore(conn, now_ms)
        self._history = AgentHistory(conn, now_ms)
        self._settings = SettingsStore(conn)
        self._spend = SpendTracker(conn, now_ms, tz)
        self._queue: deque[int] = deque()
        # task id -> the model it runs on (the assistant's model, as it was when it was handed over)
        self._models: dict[int, str] = {}
        self._pump: asyncio.Task[None] | None = None
        self._run: TaskRun | None = None
        self._executing = False  # a task has been taken off the queue and is being carried out
        # Tasks the user cleared while they ran: they end without a report and leave no row.
        self._discarded: set[int] = set()
        # A task that was under way when the last process ended is over: it is never started again.
        try:
            interrupted = self._store.interrupt_unfinished()
            if interrupted:
                logger.info("%d background tasks were left unfinished by the last run", interrupted)
        except sqlite3.Error as exc:
            logger.warning("Could not close the old background tasks: %s", type(exc).__name__)

    def register(self, router: Router) -> None:
        for method, handler in {
            "worker.list": self._list,
            "worker.get": self._get,
            "worker.cancel": self._cancel,
        }.items():
            router.register(method, handler)

    # --- taking a task --------------------------------------------------------------------------

    def submit(self, *, session_id: str, text: str, language: str, model: str) -> int:
        """Write a task down and queue it; its id. `Refused` when the worker is switched off or the
        queue is full. The task starts as soon as the one before it is over. (`model` is accepted
        but not used yet: a task uses the assistant's model.)"""
        if self._host.disabled:
            raise Refused("worker_disabled")
        if len(self._queue) >= MAX_QUEUED + (0 if self._executing else 1):
            raise Refused("worker_busy")
        task_id = self._store.create(session_id=session_id, language=language, text=text)
        self._models[task_id] = model
        self._queue.append(task_id)
        self._ensure_pump()
        self._changed(task_id, QUEUED)
        return task_id

    def attach_turn(self, task_id: int, turn_id: int) -> None:
        """Tie the task to the turn of the history that handed it over, which its report completes."""
        try:
            self._store.attach_turn(task_id, turn_id)
        except sqlite3.Error as exc:
            logger.warning("Could not tie a task to its turn: %s", type(exc).__name__)

    def prune(self, older_than_ms: int) -> None:
        try:
            self._store.prune(older_than_ms)
        except sqlite3.Error as exc:
            logger.warning("Could not prune the background tasks: %s", type(exc).__name__)

    def clear(self) -> None:
        """Clear the tasks with the history: the finished ones and the ones that wait are deleted,
        and the one that runs is stopped, emptied of what the user said at once and deleted when it
        has stopped, without a report to the overlay or the history."""
        try:
            for task_id in list(self._queue):
                self._queue.remove(task_id)
                self._models.pop(task_id, None)
                self._store.delete(task_id)
            self._store.clear()
            run = self._run
            if run is not None:
                self._discarded.add(run.task_id)
                self._store.scrub(run.task_id)
                self._host.cancel(run)
        except sqlite3.Error as exc:
            logger.warning("Could not clear the background tasks: %s", type(exc).__name__)

    @property
    def running(self) -> bool:
        return self._run is not None

    # --- running them ---------------------------------------------------------------------------

    def _ensure_pump(self) -> None:
        if self._pump is None or self._pump.done():
            self._pump = asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        while self._queue:
            task = self._store.get(self._queue.popleft())
            if task is None or task.state != QUEUED:
                continue  # cancelled while it waited, or cleared
            self._executing = True
            try:
                await self._execute(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a bug here must not stop the tasks after this one
                logger.warning("A background task failed unexpectedly: %s", type(exc).__name__)
                self._conclude(
                    task, None, "failed", PREFIXES[task.language].negative,
                    phrase(task.language, "error"), None, "error",
                )  # fmt: skip
            finally:
                self._executing = False

    async def _execute(self, task: Task) -> None:
        language = task.language
        if task.id in self._discarded:
            return
        if self._host.disabled:
            self._conclude(
                task, None, "failed", PREFIXES[language].negative,
                phrase(language, "worker_disabled"), None, "disabled",
            )  # fmt: skip
            return
        self._store.start(task.id)
        ledger = Ledger(self._spend)
        run = TaskRun(
            task_id=task.id,
            language=language,
            model=self._models.pop(task.id, DEFAULTS["agent_model"]),
            limits=self._limits,
            snapshot=take_snapshot(self._gate.plugins),
            client=self._client,
            gate=self._gate,
            tools=self._tools,
            ledger=ledger,
            store=self._store,
            spend=self._spend,
            monthly_limit=lambda: self._settings.get("monthly_limit_usd"),
            city=lambda: self._text_setting("agent_city"),
            changed=lambda: self._changed(task.id, RUNNING),
            clock=self._clock,
        )
        system, user = _prompts(
            language,
            task.text,
            self._tools.local_time().data or {},
            self._context(task),
            self._limits,
        )
        self._run = run
        self._changed(task.id, RUNNING)
        spec = {
            "task_id": task.id,
            "system": system,
            "user": user,
            "tools": run.snapshot.definitions,
            "limits": {
                "model_calls": self._limits.model_calls,
                "tool_calls": self._limits.tool_calls,
                "seconds": self._limits.seconds,
            },
        }
        try:
            try:
                reply = await asyncio.wait_for(
                    self._host.run(run, spec), self._limits.seconds + TIME_NET_S
                )
            except TimeoutError:
                run.timed_out = True
                run.cancel()
                self._host.kill()
                reply = {"status": "stopped", "kind": "limit_time"}
            self._conclude_run(task, run, reply)
        except WorkerStopped:
            if run.cancelled:
                self._conclude_run(task, run, {"status": "cancelled"})
            elif run.timed_out:
                self._conclude_run(task, run, {"status": "stopped", "kind": "limit_time"})
            else:
                self._conclude_run(task, run, {"status": "interrupted"})
        except WorkerUnavailable:
            self._conclude(
                task, run, "failed", PREFIXES[language].negative,
                phrase(language, "error"), None, "worker",
            )  # fmt: skip
        except WorkerCrashed:
            self._conclude_run(task, run, {"status": "interrupted"})
        except asyncio.CancelledError:
            # The core is closing: the task is over, and nobody is left to report to. The call
            # that was under way is given up.
            run.cancel()
            self._store.finish(
                task.id, "interrupted", prefix="", text="", detail=None, reason="closing"
            )
            raise
        finally:
            self._run = None

    def _context(self, task: Task) -> list[Turn]:
        try:
            turns = self._history.recent(CONTEXT_TURNS + 1)
        except sqlite3.Error:
            return []
        return [turn for turn in turns if turn.id != task.turn_id][-CONTEXT_TURNS:]

    def _text_setting(self, key: str) -> str:
        value = self._settings.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else str(DEFAULTS[key])

    # --- the end of a task ----------------------------------------------------------------------

    def _conclude_run(self, task: Task, run: TaskRun, reply: dict) -> None:
        """Turn how the worker said the task ended into the task's state and report."""
        language = task.language
        status, kind = reply.get("status"), reply.get("kind")
        steps = str(run.tool_calls)
        notice, negative = PREFIXES[language].notice, PREFIXES[language].negative
        if status == "done":
            report = report_from(str(reply.get("final") or ""), language)
            if report is None:
                self._conclude(
                    task, run, "done", notice, phrase(language, "worker_done"), None, None
                )
            else:
                self._conclude(task, run, "done", report.prefix, report.text, report.detail, None)
        elif status == "cancelled":
            key = "worker_cancelled_unsure" if run.call_cut_off else "worker_cancelled"
            self._conclude(
                task, run, "cancelled", notice, phrase(language, key, steps=steps), None, "cancelled"
            )
        elif status == "stopped" and isinstance(kind, str) and kind.startswith("limit_"):
            text = phrase(language, f"worker_{kind}", steps=steps)
            self._conclude(task, run, "limit", notice, text, None, kind)
        elif status == "stopped" and kind == "failed":
            detail = reply.get("detail")
            if detail == "tools":
                text = phrase(language, "worker_model")
            else:
                text = phrase(language, detail if detail in KINDS else "error")
            self._conclude(task, run, "failed", negative, text, None, "failed")
        else:  # interrupted, or something the worker should not have said
            text = phrase(language, "worker_interrupted", steps=steps)
            self._conclude(task, run, "interrupted", notice, text, None, "interrupted")

    def _conclude(
        self,
        task: Task,
        run: TaskRun | None,
        state: str,
        prefix: str,
        text: str,
        detail: str | None,
        reason: str | None,
    ) -> None:
        """Write the end of a task: its row, the turn of the history that handed it over, and the
        report to the app. `prefix` is a prefix of the task's language (《告》, 《否》 ...). A task the
        user cleared meanwhile is deleted instead, and nothing is said or saved about it."""
        if task.id in self._discarded:
            self._discarded.discard(task.id)
            try:
                self._store.delete(task.id)
            except sqlite3.Error as exc:
                logger.warning("Could not delete a cleared task: %s", type(exc).__name__)
            self._changed(task.id, state, ending=True)
            return
        cost = 0.0 if run is None else run.ledger.total
        try:
            if run is not None:
                self._store.progress(
                    task.id, model_calls=run.model_calls, tool_calls=run.tool_calls, cost_usd=cost
                )
            self._store.finish(task.id, state, prefix=prefix, text=text, detail=detail, reason=reason)
        except sqlite3.Error as exc:
            logger.warning("Could not write down the end of a task: %s", type(exc).__name__)
        if task.turn_id:
            try:
                self._history.resolve(
                    task.turn_id,
                    prefix=prefix,
                    text=text,
                    detail=detail,
                    status=_TURN_STATUS.get(state, "error"),
                    cost_usd=cost,
                )
            except sqlite3.Error as exc:
                logger.warning("Could not complete the turn of a task: %s", type(exc).__name__)
        logger.info(
            "Background task %d ended: state=%s reason=%s model_calls=%s tool_calls=%s cost=%.5f",
            task.id,
            state,
            reason,
            None if run is None else run.model_calls,
            None if run is None else run.tool_calls,
            cost,
        )
        self._notify(
            "worker.report", {"task_id": task.id, "state": state, "prefix": prefix, "text": text}
        )
        self._changed(task.id, state, ending=True)

    def _changed(self, task_id: int, state: str, *, ending: bool = False) -> None:
        """Tell the app a task moved on. `running` is whether any task is under way once this change
        has been made (the tray offers to stop it while one is, whichever task changed); the counters
        are those of the task that changed, if it is the one that runs, else zero."""
        run = self._run
        owner = run if run is not None and run.task_id == task_id else None
        self._notify(
            "worker.changed",
            {
                "task_id": task_id,
                "state": state,
                "running": run is not None and not (ending and run.task_id == task_id),
                "model_calls": 0 if owner is None else owner.model_calls,
                "tool_calls": 0 if owner is None else owner.tool_calls,
                "cost_usd": 0.0 if owner is None else owner.ledger.total,
            },
        )

    # --- the methods of the app -----------------------------------------------------------------

    @staticmethod
    def _view(task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "created_at_ms": task.created_at_ms,
            "started_at_ms": task.started_at_ms,
            "finished_at_ms": task.finished_at_ms,
            "state": task.state,
            "text": task.text,
            "report": None
            if task.report_text is None
            else {
                "prefix": task.report_prefix,
                "text": task.report_text,
                "detail": task.report_detail,
            },
            "reason": task.reason,
            "model_calls": task.model_calls,
            "tool_calls": task.tool_calls,
            "cost_usd": task.cost_usd,
        }

    async def _list(self, params: dict) -> dict:
        limit = params.get("limit", 20)
        if type(limit) is not int:
            raise _invalid("limit must be a whole number")
        return {"tasks": [self._view(task) for task in self._store.list(limit)]}

    async def _get(self, params: dict) -> dict:
        task_id = params.get("id")
        if type(task_id) is not int:
            raise _invalid("id must be a whole number")
        task = self._store.get(task_id)
        if task is None:
            raise RpcError(NOT_FOUND, "There is no such task")
        steps = [
            {
                "n": step.n,
                "at_ms": step.at_ms,
                "kind": step.kind,
                "tool": step.tool,
                "server": step.server,
                "tier": step.tier,
                "outcome": step.outcome,
                "cost_usd": step.cost_usd,
                "ms": step.ms,
            }
            for step in self._store.steps(task_id)
        ]
        return {"task": {**self._view(task), "steps": steps}}

    async def _cancel(self, params: dict) -> dict:
        """Stop the task that runs (no `task_id`, or its id) or take a queued one out of the queue.
        Esc does not do this: the cancel key of the assistant leaves the background alone."""
        task_id = params.get("task_id")
        if task_id is not None and type(task_id) is not int:
            raise _invalid("task_id must be a whole number or null")
        run = self._run
        if run is not None and task_id in (None, run.task_id):
            self._host.cancel(run)
            return {"cancelled": True}
        if task_id is not None and task_id in self._queue:
            self._queue.remove(task_id)
            task = self._store.get(task_id)
            if task is not None:
                text = phrase(task.language, "worker_cancelled", steps="0")
                self._conclude(
                    task, None, "cancelled", PREFIXES[task.language].notice, text, None, "cancelled"
                )
            return {"cancelled": True}
        return {"cancelled": False}

    # --- closing --------------------------------------------------------------------------------

    async def aclose(self) -> None:
        """End the task that runs (it is interrupted), the worker process and the lookups' client."""
        pump, self._pump = self._pump, None
        if pump is not None and not pump.done():
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        for task_id in list(self._queue):
            self._queue.remove(task_id)
        try:
            self._store.interrupt_unfinished()
        except sqlite3.Error as exc:
            logger.warning("Could not close the background tasks: %s", type(exc).__name__)
        await self._host.aclose()
        await self._tools.aclose()
