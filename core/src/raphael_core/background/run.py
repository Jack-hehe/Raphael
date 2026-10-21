"""One task as the core sees it while it runs: the answers to the worker's `host.chat` and `host.tool`.

A `TaskRun` is the gate between a task and the world. The worker asks it for a model reply or for a
tool call; it decides whether to give them, and gives them itself. In order:

- **Limits.** Before every call it checks that the task was not cancelled, that its time is not up,
  that it has model calls and tool calls left, that the task has not spent what one task may (and
  the month has not spent its limit). A call it refuses is answered with `WORKER_STOPPED` and the
  kind, and the task ends there with no more spend. The worker counts the limits too; this is the
  count that counts.
- **The model.** The key is read here, by the client, and nowhere else. The tools the model is offered
  are the snapshot's, whatever the worker sent; every reply is paid for into the spend (purpose
  "agent") and into the task's own total, with `data_collection: deny` as for every call.
- **The tools.** A name that is not in the snapshot is not a tool. A tool of a plugin goes through the
  gate (`agent.gate`), which asks the tier it has now, not the one it had when the task began, and
  writes the audit with the task's id; a tool of Raphael's own (the weather, a web search, the time)
  is checked by the gate too and audited the same way. What a tool returns goes back as it is: the
  worker marks it as untrusted data.

Each call, whatever came of it, leaves a step (`TaskStore.add_step`): the tool, its tier, how it
ended, what it cost; never an argument or a result. A task that was cancelled while a plugin's tool
was under way is told so (`call_cut_off`): the call may or may not have been made.

Nothing here logs an argument, a result or what the task is about.
"""

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, TypeVar

from ..agent.failure import failure_kind
from ..agent.gate import ToolGate
from ..agent.ledger import Ledger
from ..agent.plugins import MAX_ARGUMENT_CHARS, PluginResult
from ..agent.policy import Action, NotApproved, UnknownTool
from ..agent.tools import ToolResult, Tools
from ..brain.catalog import LLM, find
from ..brain.openrouter import CLEANUP_FALLBACKS, OpenRouterClient, OpenRouterError
from ..brain.pricing import builtin_catalog
from ..brain.spend import SpendTracker
from ..protocol import INVALID_PARAMS, WORKER_STOPPED, RpcError
from .snapshot import BUILTIN, MCP, Snapshot, ToolRef
from .tasks import TaskStore

logger = logging.getLogger(__name__)

T = TypeVar("T")

# One model reply may take this long, and may be this long.
CHAT_TIMEOUT_S = 60.0
CHAT_MAX_TOKENS = 2000
# The conversation the worker sends is bounded: a worker that sent more is not doing a task.
MAX_MESSAGES = 200
MAX_CHAT_CHARS = 600_000
# The most of a tool's result that goes to the worker, in characters (it cuts it further).
MAX_RESULT_CHARS = 12_000


# A model call whose cost is not known, and whose model has no price we know, is charged this.
UNKNOWN_COST_FLOOR_USD = 0.02
# A character is counted as a third of a token in the estimate, which is more than the usual
# quarter: the estimate is meant to be too high rather than too low.
_CHARS_PER_TOKEN = 3.0


def estimate_cost(model: str, request_chars: int) -> float:
    """What a model call is charged when nobody said what it cost (the reply had no usage, or it was
    given up on after a time-out or a cancel, when the provider may still bill it): the request's
    characters as input tokens plus the most the reply may be (`CHAT_MAX_TOKENS`), at the model's
    price; a model whose price is not known costs the floor, US$0.02."""
    entry = find(builtin_catalog(), LLM, model)
    price = None if entry is None else entry.price
    if price is None or price.input_per_mtok is None or price.output_per_mtok is None:
        return UNKNOWN_COST_FLOOR_USD
    tokens_in = request_chars / _CHARS_PER_TOKEN
    cost = (tokens_in * price.input_per_mtok + CHAT_MAX_TOKENS * price.output_per_mtok) / 1_000_000
    return cost


@dataclass(frozen=True)
class RunLimits:
    """What one task may use (design §9): 15 model calls, 25 tool calls, 10 minutes, US$0.20."""

    model_calls: int = 15
    tool_calls: int = 25
    seconds: float = 600.0
    cost_usd: float = 0.20


def stopped(kind: str, detail: str | None = None) -> RpcError:
    """The answer to a call the task may not make: it ends the task. `kind` is one of
    `worker.loop.Stop`'s kinds."""
    return RpcError(WORKER_STOPPED, "The task may not go on", {"stop": kind, "detail": detail})


def _invalid(message: str) -> RpcError:
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


def _outcome_of(result: PluginResult) -> str:
    """How a call of a plugin's tool ended, as the audit and the steps say it."""
    if result.ok:
        return "ok"
    if result.kind == "timeout":
        return "timeout"
    if result.kind in ("blocked", "changed", "gone"):
        return "blocked"
    return "error"


def _refused() -> dict[str, Any]:
    return {"ok": False, "kind": "blocked", "content": "", "truncated": False}


class TaskRun:
    """A task that is running. Everything here runs on the event loop.

    `client` is the OpenRouter client, `gate` the one gate, `tools` Raphael's own lookups, `ledger`
    the task's paid calls (it writes the spend), `spend` the tracker the month's limit is read
    from, `monthly_limit` and `city` read the settings when asked. `changed` is told after each
    step so that whoever shows the task can update it.
    """

    def __init__(
        self,
        *,
        task_id: int,
        language: str,
        model: str,
        limits: RunLimits,
        snapshot: Snapshot,
        client: OpenRouterClient,
        gate: ToolGate,
        tools: Tools,
        ledger: Ledger,
        store: TaskStore,
        spend: SpendTracker,
        monthly_limit: Callable[[], float],
        city: Callable[[], str],
        changed: Callable[[], None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.task_id = task_id
        self.language = language
        self.model = model
        self.limits = limits
        self.snapshot = snapshot
        self.ledger = ledger
        self.model_calls = 0
        self.tool_calls = 0
        self.cancelled = False
        self.timed_out = False
        # A call of a plugin's tool was under way when the task was cancelled: it may have been made.
        self.call_cut_off = False
        self._client = client
        self._gate = gate
        self._tools = tools
        self._store = store
        self._spend = spend
        self._monthly_limit = monthly_limit
        self._city = city
        self._changed = changed
        self._clock = clock
        self.deadline = clock() + limits.seconds
        self._live: set[asyncio.Task[Any]] = set()
        self._in_plugin_call = False
        # One call at a time: the worker may send several at once, and the limits (the money above
        # all) are read before a reply lands. A call waits for the one before it and checks the
        # limits only when its turn comes.
        self._turn = asyncio.Lock()

    # --- cancelling -----------------------------------------------------------------------------

    def cancel(self) -> None:
        """The user stopped the task: nothing more is answered, and the call under way is given up."""
        self.cancelled = True
        if self._in_plugin_call:
            self.call_cut_off = True
        for task in list(self._live):
            task.cancel()

    async def _guarded(self, work: Awaitable[T]) -> T:
        """Await `work` as a task of its own, so that `cancel` can end it. A call ended that way
        is `stopped("cancelled")`; being cancelled from outside passes through."""
        task = asyncio.ensure_future(work)
        self._live.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if self.cancelled and task.cancelled():
                raise stopped("cancelled") from None
            raise
        finally:
            self._live.discard(task)
            if not task.done():
                task.cancel()

    def _check(self, *, model: bool = False, tool: bool = False, paid: bool = False) -> None:
        """Refuse a call the task may not make: `stopped(kind)`."""
        if self.cancelled:
            raise stopped("cancelled")
        if self._clock() >= self.deadline:
            raise stopped("limit_time")
        if model and self.model_calls >= self.limits.model_calls:
            raise stopped("limit_model")
        if tool and self.tool_calls >= self.limits.tool_calls:
            raise stopped("limit_tool")
        if paid:
            if self.ledger.total >= self.limits.cost_usd:
                raise stopped("limit_cost")
            try:
                if self._spend.is_over(self._monthly_limit()):
                    raise stopped("limit_month")
            except sqlite3.Error as exc:
                logger.warning("Could not read the month's spend: %s", type(exc).__name__)

    def _remaining_s(self) -> float:
        return max(self.deadline - self._clock(), 0.0)

    # --- the steps ------------------------------------------------------------------------------

    def _step(self, **fields: Any) -> None:
        try:
            self._store.add_step(self.task_id, **fields)
            self._store.progress(
                self.task_id,
                model_calls=self.model_calls,
                tool_calls=self.tool_calls,
                cost_usd=self.ledger.total,
            )
        except sqlite3.Error as exc:
            logger.warning("Could not write down a step: %s", type(exc).__name__)
        self._changed()

    # --- host.chat ------------------------------------------------------------------------------

    async def chat(self, params: dict) -> dict:
        """A reply of the model to the conversation the worker sends, with the tools of the
        snapshot."""
        messages = params.get("messages")
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            raise _invalid("messages must be a list of messages")
        if not messages:
            raise _invalid("messages is empty")
        request_chars = len(json.dumps(messages, ensure_ascii=False))
        async with self._turn:
            # A conversation grown past what is sent is a task that has used what it was given.
            if len(messages) > MAX_MESSAGES or request_chars > MAX_CHAT_CHARS:
                raise stopped("limit_model")
            return await self._chat(messages, request_chars)

    async def _chat(self, messages: list[dict], request_chars: int) -> dict:
        self._check(model=True, paid=True)
        self.model_calls += 1
        began = time.perf_counter()
        remaining = self._remaining_s()
        timeout = min(CHAT_TIMEOUT_S, remaining)
        try:
            turn = await self._guarded(
                asyncio.wait_for(
                    self._client.chat_tools(
                        model=self.model,
                        fallbacks=CLEANUP_FALLBACKS,
                        messages=messages,
                        # The snapshot's tools, whatever the worker sent.
                        tools=self.snapshot.definitions,
                        timeout_s=timeout,
                        max_tokens=CHAT_MAX_TOKENS,
                        reasoning_effort="none",
                    ),
                    timeout,
                )
            )
        except OpenRouterError as error:
            logger.warning(
                "The task's model failed: kind=%s status=%s", error.kind, error.status
            )
            # With every parameter required, "no endpoint found" is a model that takes no tools.
            detail = "tools" if error.status == HTTPStatus.NOT_FOUND else failure_kind(error)
            self._step(kind="model", outcome="error", ms=_ms(began))
            raise stopped("failed", detail) from None
        except TimeoutError:
            logger.warning("The task's model timed out")
            self._charge_estimate(request_chars)  # the provider may bill what was given up on
            self._step(kind="model", outcome="timeout", ms=_ms(began))
            # The wait that ended was the task's time when that was the shorter bound: not the
            # clock, which a timer that fires a tick early leaves just short of the deadline.
            if remaining <= CHAT_TIMEOUT_S or self._clock() >= self.deadline:
                raise stopped("limit_time") from None
            raise stopped("failed", "offline") from None
        except RpcError:
            self._charge_estimate(request_chars)  # cancelled while the provider was working
            raise
        except Exception as exc:
            logger.warning("The task's model failed unexpectedly: %s", type(exc).__name__)
            self._step(kind="model", outcome="error", ms=_ms(began))
            raise stopped("failed", "error") from None
        cost = turn.cost_usd
        if not turn.usage_known:  # no usage is not a free call
            cost = max(cost, estimate_cost(self.model, request_chars))
        self.ledger.add(
            model=turn.model,
            cost_usd=cost,
            prompt_tokens=turn.prompt_tokens,
            completion_tokens=turn.completion_tokens,
        )
        self._step(kind="model", outcome="ok", cost_usd=cost, ms=_ms(began))
        return {
            "content": turn.content,
            "calls": [
                {"id": c.id, "name": c.name, "arguments": c.arguments, "raw": c.raw_arguments}
                for c in turn.calls
            ],
        }

    def _charge_estimate(self, request_chars: int) -> None:
        self.ledger.add(model=self.model, cost_usd=estimate_cost(self.model, request_chars))

    # --- host.tool ------------------------------------------------------------------------------

    async def tool(self, params: dict) -> dict:
        """The result of a call of a tool of the snapshot."""
        name, arguments = params.get("name"), params.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise _invalid("name must be a string and arguments an object")
        async with self._turn:
            return await self._tool(name, arguments)

    async def _tool(self, name: str, arguments: dict) -> dict:
        ref = self.snapshot.refs.get(name)
        searching = ref is not None and ref.kind == BUILTIN and ref.name == "web_search"
        self._check(tool=True, paid=searching)
        self.tool_calls += 1
        began = time.perf_counter()
        if ref is None or len(json.dumps(arguments, ensure_ascii=False)) > MAX_ARGUMENT_CHARS:
            # Not a tool the task was given: nothing to call, and nothing to audit.
            self._step(kind="tool", outcome="blocked", ms=_ms(began))
            return _refused()
        if ref.kind == MCP:
            reply, outcome, tier = await self._plugin(ref, arguments)
        else:
            reply, outcome, tier = await self._builtin(ref, arguments)
        self._step(
            kind="tool",
            tool=ref.name,
            server=ref.server,
            tier=tier,
            outcome=outcome,
            ms=_ms(began),
        )
        return reply

    async def _plugin(self, ref: ToolRef, arguments: dict) -> tuple[dict, str, str | None]:
        now = self._gate.tier_now(ref.key)
        self._in_plugin_call = True
        try:
            result = await self._guarded(
                self._gate.call_plugin(ref.key, arguments, task_id=self.task_id)
            )
        finally:
            self._in_plugin_call = False
        text, cut = _cut(result.text)
        reply = {
            "ok": result.ok,
            "kind": result.kind,
            "content": text,
            "truncated": cut or result.truncated,
        }
        return reply, _outcome_of(result), None if now is None else now.name

    async def _builtin(self, ref: ToolRef, arguments: dict) -> tuple[dict, str, str | None]:
        gate = self._gate
        tier = gate.tier_now(ref.key)
        try:
            gate.check(Action(ref.key, arguments), None, worker=True)
        except (UnknownTool, NotApproved):
            gate.record_builtin(ref.name, outcome="blocked", task_id=self.task_id)
            return _refused(), "blocked", None if tier is None else tier.name
        result = await self._lookup(ref.name, arguments)
        outcome = "ok" if result.ok else ("timeout" if result.error == "timeout" else "error")
        gate.record_builtin(ref.name, outcome=outcome, task_id=self.task_id)
        if result.ok:
            text, cut = _cut(json.dumps(result.data, ensure_ascii=False))
            reply = {"ok": True, "kind": None, "content": text, "truncated": cut}
        else:
            reply = {"ok": False, "kind": result.error or "error", "content": "", "truncated": False}
        return reply, outcome, None if tier is None else tier.name

    async def _lookup(self, name: str, arguments: dict) -> ToolResult:
        """One of Raphael's own lookups. It never raises for a failure of the world."""
        try:
            if name == "weather":
                city = arguments.get("city")
                city = city.strip() if isinstance(city, str) and city.strip() else self._city()
                return await self._guarded(self._tools.weather(city, self.language))
            if name == "web_search":
                query = arguments.get("query")
                if not isinstance(query, str) or not query.strip():
                    return ToolResult(name, False, None, "bad_arguments")
                return await self._guarded(
                    self._tools.web_search(query.strip(), self.model, self.ledger)
                )
            return self._tools.local_time()
        except RpcError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("The %s lookup failed unexpectedly: %s", name, type(exc).__name__)
            return ToolResult(name, False, None, "error")


def _ms(began: float) -> int:
    return round((time.perf_counter() - began) * 1000)


def _cut(text: str) -> tuple[str, bool]:
    return (text, False) if len(text) <= MAX_RESULT_CHARS else (text[:MAX_RESULT_CHARS], True)
