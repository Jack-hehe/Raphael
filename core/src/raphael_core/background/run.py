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
- **The owner's safety rules.** A plugin's tool that its server does not say only reads is judged
  by the rules before it is called (`ToolGate.screen`, paid into the task's ledger). A rule that
  forbids it refuses the call ("forbidden": the model is told which rule, and the call is not
  made). A rule that asks, or a tier of T2 or more, makes the call wait for the owner: the run
  keeps it (`waiting`) and stops the task with "waiting", and the background service turns that
  into a question in the app (`service`). Only a run that may wait (`can_wait`, the service's) does
  so; any other refuses such a call as before. When the owner has answered, the service has the
  run make the call itself with the yes (`answer_call`), and the task goes on from where it was.
- **Addresses.** `web_read` reads a page only at an address the task has seen (`SeenUrls`): in the
  task's request, or in what one of its tools returned before (search results, a page read, a
  plugin's answer), exactly or as the same page (the same origin and path). Any other is refused
  with the kind "url_not_seen", and nothing is fetched: a page cannot make the task send what it
  knows to an address of the page's making. The addresses are kept in the run, in memory.
- **Files.** `read_attachment` reads one of the files handed over with the task, by its number,
  from memory: never a path.

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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Protocol, TypeVar

from ..agent.failure import failure_kind
from ..agent.gate import ToolGate
from ..agent.ledger import Ledger
from ..agent.plugins import MAX_ARGUMENT_CHARS, PluginResult
from ..agent.steps import reason_of
from ..agent.policy import Action, Approval, NotApproved, Tier, UnknownTool
from ..agent.safety import DENY, PASS, Proposed, Screening, needs_yes, summarize
from ..agent.tools import ToolResult, Tools
from ..brain.catalog import LLM, find
from ..brain.openrouter import CLEANUP_FALLBACKS, OpenRouterClient, OpenRouterError
from ..brain.pricing import builtin_catalog
from ..brain.spend import SpendTracker
from ..integrations.pool import McpPool
from ..protocol import WORKER_STOPPED, RpcError, invalid_params
from ..services.attachments import Attachment
from ..services.urls import SeenUrls
from ..worker.loop import WAITING, ToolOutcome, result_text
from .snapshot import BUILTIN, FIND, MCP, Snapshot, ToolRef
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
# A page read, or a file's text, is longer: the budget of a tool's text, and its footer.
MAX_TEXT_RESULT_CHARS = 16_000
# The tools whose call is paid: the search (by a model, or a service), and the reading of a file.
_PAID_TOOLS = ("web_search", "read_attachment")


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
    """What one task may use: 15 model calls, 25 tool calls, 10 minutes, US$0.20."""

    model_calls: int = 15
    tool_calls: int = 25
    seconds: float = 600.0
    cost_usd: float = 0.20


def stopped(kind: str, detail: str | None = None) -> RpcError:
    """The answer to a call the task may not make: it ends the task. `kind` is one of
    `worker.loop.Stop`'s kinds."""
    return RpcError(WORKER_STOPPED, "The task may not go on", {"stop": kind, "detail": detail})


def _outcome_of(result: PluginResult) -> str:
    """How a call of a plugin's tool ended, as the audit and the steps say it."""
    if result.ok:
        return "ok"
    if result.kind == "timeout":
        return "timeout"
    if result.kind == "cancelled":  # sent, and the connection ended: it may have been made
        return "cancelled"
    if result.kind in ("blocked", "changed", "gone"):
        return "blocked"
    return "error"


def _refused() -> dict[str, Any]:
    return {"ok": False, "kind": "blocked", "content": "", "truncated": False}


# What the model is told of a call that a rule of the owner's forbids, and of one the owner said no
# to (the rule is in the owner's own words).
FORBIDDEN_BY_RULE = (
    "The owner's safety rule 「{rule}」 forbids this call, so it was not made. Do not try it another "
    "way: do the rest of the task without it, and say in the report what was not done."
)
OWNER_SAID_NO = (
    "The owner said no to this call, so it was not made. Do not make it or anything like it: do "
    "the rest of the task without it, and say in the report what was not done."
)
NOT_MADE_WHILE_WAITING = (
    "This call was not made: the task paused for the owner's answer to an earlier call of the same "
    "reply. Make it again if it is still needed."
)


@dataclass
class Waiting:
    """A call of a plugin's tool that waits for the owner's yes: the alias the worker called it by,
    the tool, what it is given (in memory only), the id of the call, the tier it was asked at, and
    what the safety rules said of it."""

    alias: str
    ref: ToolRef
    arguments: dict
    call_id: str
    tier: Tier
    screening: Screening


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
        memory: "MemoryReader | None" = None,
        request: str = "",
        attachments: Sequence[Attachment] = (),
        can_wait: bool = False,
    ) -> None:
        self._memory = memory
        # Whether a call that needs the owner's yes pauses the task (the background service's runs)
        # or is refused (any other).
        self.can_wait = can_wait
        # The call the task paused on, until the service takes it.
        self.waiting: Waiting | None = None
        # The files of the task, by number from 1, and the addresses it has seen (see the module).
        self.attachments = list(attachments)
        self.seen_urls = SeenUrls([request])
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
        # The connections of the task's plugin calls: one for each server, kept for the task and
        # closed by whoever ends it (`pool.aclose`).
        self.pool = McpPool()
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

    def remaining_s(self) -> float:
        """The task's own time that is left now."""
        return self._remaining_s()

    def reopen(self, remaining_s: float) -> None:
        """Make the run ready to go on after a pause: the time it waited was not the task's, so it
        has `remaining_s` from now; it has a new pool of connections (the old one was closed when it
        paused), and no call waits."""
        self.deadline = self._clock() + max(remaining_s, 0.0)
        self.timed_out = False
        self.waiting = None
        self.pool = McpPool()

    # --- the steps ------------------------------------------------------------------------------

    def _step(self, **fields: Any) -> int | None:
        step_id = None
        try:
            step_id = self._store.add_step(self.task_id, **fields)
            self._store.progress(
                self.task_id,
                model_calls=self.model_calls,
                tool_calls=self.tool_calls,
                cost_usd=self.ledger.total,
            )
        except sqlite3.Error as exc:
            logger.warning("Could not write down a step: %s", type(exc).__name__)
        self._changed()
        return step_id

    def _end_step(self, step_id: int | None, **fields: Any) -> None:
        """End the step of a call that was written when it was sent; or write it now if that
        could not be done."""
        if step_id is None:
            self._step(kind="tool", **fields)
            return
        try:
            self._store.update_step(
                step_id,
                outcome=fields["outcome"],
                tier=fields.get("tier"),
                ms=fields.get("ms", 0),
                reason=fields.get("reason"),
            )
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
            raise invalid_params("messages must be a list of messages")
        if not messages:
            raise invalid_params("messages is empty")
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
            self._step(kind="model", outcome="error", ms=_ms(began), reason=reason_of("error", detail))
            raise stopped("failed", detail) from None
        except TimeoutError:
            logger.warning("The task's model timed out")
            self._charge_estimate(request_chars)  # the provider may bill what was given up on
            self._step(kind="model", outcome="timeout", ms=_ms(began), reason="timeout")
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
            self._step(kind="model", outcome="error", ms=_ms(began), reason="error")
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
            raise invalid_params("name must be a string and arguments an object")
        call_id = params.get("call_id")
        async with self._turn:
            return await self._tool(name, arguments, call_id if isinstance(call_id, str) else "")

    async def _tool(self, name: str, arguments: dict, call_id: str = "") -> dict:
        ref = self.snapshot.refs.get(name)
        paying = ref is not None and ref.kind == BUILTIN and ref.name in _PAID_TOOLS
        self._check(tool=True, paid=paying)
        self.tool_calls += 1
        began = time.perf_counter()
        if ref is None or len(json.dumps(arguments, ensure_ascii=False)) > MAX_ARGUMENT_CHARS:
            # Not a tool the task was given: nothing to call, and nothing to audit.
            self._step(kind="tool", outcome="blocked", ms=_ms(began), reason="blocked")
            return _refused()
        sent: int | None = None
        detail: str | None = None
        if ref.kind == MCP:
            reply, outcome, tier, sent = await self._plugin(ref, arguments, name, call_id)
        elif ref.kind == FIND:
            reply, outcome, tier = self._find(arguments)
        else:
            reply, outcome, tier, detail = await self._builtin(ref, arguments)
        if reply.get("ok") and isinstance(reply.get("content"), str):
            self.seen_urls.add_from(reply["content"])  # what it returned may be read next
        fields = {
            "tool": ref.name,
            "server": ref.server,
            "tier": tier,
            "outcome": outcome,
            "ms": _ms(began),
            # The kind the reply carries, never its content: a service's own words stay out.
            "reason": reason_of(outcome, reply.get("kind")),
        }
        if detail is not None:
            fields["detail"] = detail
        if ref.kind == MCP:
            self._end_step(sent, **fields)  # the step written when the call was sent
        else:
            self._step(kind="tool", **fields)
        return reply

    async def _screen(self, ref: ToolRef, arguments: dict) -> Screening:
        """What the owner's safety rules say of a call: PASS for a tool its server says only reads,
        which is never judged. The judge is paid, so the limits on money are read first."""
        if self._gate.safety is None or self._gate.reads_only(ref.server, ref.name):
            return PASS
        self._check(paid=True)
        description = ""
        pool = self.snapshot.pool
        if pool is not None:
            tool = next((t for t in pool.by_alias.values() if t.key == ref.key), None)
            description = "" if tool is None else tool.description
        proposed = Proposed(f"{ref.server} / {ref.name}", description, summarize(arguments))
        return await self._guarded(
            self._gate.screen(proposed, language=self.language, ledger=self.ledger)
        )

    async def _plugin(
        self, ref: ToolRef, arguments: dict, alias: str = "", call_id: str = ""
    ) -> tuple[dict, str, str | None, int | None]:
        now = self._gate.tier_now(ref.key)
        if now is not None:
            screening = await self._screen(ref, arguments)
            if screening.verdict == DENY:
                reply = {
                    "ok": False,
                    "kind": "forbidden",
                    "content": FORBIDDEN_BY_RULE.format(rule=screening.rule or ""),
                    "truncated": False,
                }
                self._gate.record_unrun(ref.key, outcome="blocked", task_id=self.task_id)
                return reply, "blocked", now.name, None
            if needs_yes(now >= Tier.T2, screening):
                if self.can_wait:
                    # The task pauses here; the service asks the owner (see the module).
                    self.waiting = Waiting(alias or ref.name, ref, dict(arguments), call_id, now, screening)
                    raise stopped(WAITING)
                if screening.verdict != PASS.verdict:
                    # A rule asks, and nobody can be asked: the call is not made.
                    self._gate.record_unrun(ref.key, outcome="blocked", task_id=self.task_id)
                    return _refused(), "blocked", now.name, None
        self._in_plugin_call = True
        began = time.perf_counter()
        # Written before the call is sent: if the core dies in the middle of it, the step is
        # still there, and a later attempt is told the call may have been made.
        sent = self._step(
            kind="tool",
            tool=ref.name,
            server=ref.server,
            tier=None if now is None else now.name,
            outcome="in_progress",
            ms=0,
        )
        try:
            result = await self._guarded(
                self._gate.call_plugin(ref.key, arguments, task_id=self.task_id, pool=self.pool)
            )
        except (RpcError, asyncio.CancelledError):
            # The call did not come back: it may or may not have been made. A later attempt is
            # told so.
            self._end_step(
                sent,
                tool=ref.name,
                server=ref.server,
                tier=None if now is None else now.name,
                outcome="cancelled",
                ms=_ms(began),
                reason="cancelled",
            )
            raise
        finally:
            self._in_plugin_call = False
        text, cut = _cut(result.text)
        reply = {
            "ok": result.ok,
            "kind": result.kind,
            "content": text,
            "truncated": cut or result.truncated,
        }
        return reply, _outcome_of(result), None if now is None else now.name, sent

    async def answer_call(self, waiting: Waiting, approval: Approval | None, action: Action | None) -> str:
        """The call the task paused on, now that the owner answered: made with the yes (`approval`,
        for `action`, the very action the question was asked about), or not made after a no
        (`action` None). Either way it is a step of the task, and the audit says what came of it.
        Returns what the model is shown of it, as the worker shows a result."""
        ref = waiting.ref
        if action is None:
            self._gate.record_unrun(ref.key, outcome="denied", task_id=self.task_id)
            self._step(
                kind="tool", tool=ref.name, server=ref.server, tier=waiting.tier.name,
                outcome="blocked", ms=0, reason="denied",
            )  # fmt: skip
            return result_text(waiting.alias, ToolOutcome(False, "denied", OWNER_SAID_NO))
        now = self._gate.tier_now(ref.key)
        # The yes is for the tier the question was asked at: a tool that has none now, or a higher
        # one, is not covered by it.
        if now is None or now > waiting.tier:
            approval = None
        began = time.perf_counter()
        sent = self._step(
            kind="tool", tool=ref.name, server=ref.server,
            tier=None if now is None else now.name, outcome="in_progress", ms=0,
        )  # fmt: skip
        self._in_plugin_call = True
        try:
            result = await self._guarded(
                self._gate.call_plugin(
                    ref.key, waiting.arguments, approval=approval, action=action,
                    task_id=self.task_id, pool=self.pool,
                )
            )  # fmt: skip
        except (RpcError, asyncio.CancelledError):
            self._end_step(
                sent, tool=ref.name, server=ref.server, tier=None if now is None else now.name,
                outcome="cancelled", ms=_ms(began), reason="cancelled",
            )  # fmt: skip
            raise
        finally:
            self._in_plugin_call = False
        text, cut = _cut(result.text)
        outcome = _outcome_of(result)
        self._end_step(
            sent, tool=ref.name, server=ref.server, tier=None if now is None else now.name,
            outcome=outcome, ms=_ms(began), reason=reason_of(outcome, result.kind),
        )  # fmt: skip
        if result.ok:
            self.seen_urls.add_from(text)
        shown = ToolOutcome(result.ok, result.kind, text, cut or result.truncated)
        return result_text(waiting.alias, shown)

    def _find(self, arguments: dict) -> tuple[dict, str, str | None]:
        """Show the task more of the snapshot's tools, by what it says it wants to do. It makes no
        call to a service and no model call: only the snapshot's own list is read."""
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"ok": False, "kind": "bad_arguments", "content": "", "truncated": False}, "error", "T0"
        added, lines = self.snapshot.find(query)
        text = "\n".join(lines) if lines else "No other tool fits that."
        reply: dict = {"ok": True, "kind": None, "content": text, "truncated": False}
        if added:
            reply["tools"] = added
        return reply, "ok", "T0"

    async def _builtin(
        self, ref: ToolRef, arguments: dict
    ) -> tuple[dict, str, str | None, str | None]:
        """(the reply, how it ended, the tier, the service it used) of one of Raphael's own."""
        gate = self._gate
        tier = gate.tier_now(ref.key)
        try:
            gate.check(Action(ref.key, arguments), None, worker=True)
        except (UnknownTool, NotApproved):
            gate.record_builtin(ref.name, outcome="blocked", task_id=self.task_id)
            return _refused(), "blocked", None if tier is None else tier.name, None
        result = await self._lookup(ref.name, arguments)
        if result.error == "url_not_seen":
            outcome = "blocked"
        else:
            outcome = "ok" if result.ok else ("timeout" if result.error == "timeout" else "error")
        gate.record_builtin(ref.name, outcome=outcome, task_id=self.task_id)
        if result.ok:
            limit = MAX_TEXT_RESULT_CHARS if ref.name in ("web_read", "read_attachment") else None
            text, cut = _cut(json.dumps(result.data, ensure_ascii=False), limit)
            reply = {"ok": True, "kind": None, "content": text, "truncated": cut}
        else:
            reply = {"ok": False, "kind": result.error or "error", "content": "", "truncated": False}
        return reply, outcome, None if tier is None else tier.name, result.detail

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
            if name == "web_read":
                return await self._read_page(arguments)
            if name == "read_attachment":
                return await self._read_attachment(arguments)
            if name in ("memory_read", "history_search"):
                return self._read_memory(name, arguments)
            return self._tools.local_time()
        except RpcError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("The %s lookup failed unexpectedly: %s", name, type(exc).__name__)
            return ToolResult(name, False, None, "error")


    async def _read_page(self, arguments: dict) -> ToolResult:
        """A page at an address the task has seen; "url_not_seen" for any other, unfetched."""
        url = arguments.get("url")
        if not isinstance(url, str) or not url.strip():
            return ToolResult("web_read", False, None, "bad_arguments")
        url = url.strip()
        if not self.seen_urls.seen(url):
            logger.info("A task asked to read an address it has not seen: refused")
            return ToolResult("web_read", False, None, "url_not_seen")
        result = await self._guarded(self._tools.web_read(url))
        if result.ok:
            self.seen_urls.add(url)
        return result

    async def _read_attachment(self, arguments: dict) -> ToolResult:
        """One of the task's own files, by its number from 1: never anything else."""
        index = arguments.get("index")
        if type(index) is not int or not 1 <= index <= len(self.attachments):
            return ToolResult("read_attachment", False, None, "bad_arguments")
        question = arguments.get("question")
        question = question.strip() if isinstance(question, str) and question.strip() else ""
        attachment = self.attachments[index - 1]
        return await self._guarded(
            self._tools.read_attachment(
                attachment, question or "What does this file hold?", self.ledger, number=index
            )
        )

    def _read_memory(self, name: str, arguments: dict) -> ToolResult:
        """A note read or the past turns searched; never a write."""
        if self._memory is None:
            return ToolResult(name, False, None, "blocked")
        if name == "memory_read":
            names = arguments.get("names")
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                return ToolResult(name, False, None, "bad_arguments")
            return self._memory.read(names)
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(name, False, None, "bad_arguments")
        return self._memory.search(query.strip())


class MemoryReader(Protocol):
    """What a task may do with the memory (`memory.service.MemoryService`): read and search."""

    def read(self, names: Sequence[str]) -> ToolResult: ...

    def search(self, query: str, *, limit: int = 5, exclude: Sequence[int] = ()) -> ToolResult: ...


def _ms(began: float) -> int:
    return round((time.perf_counter() - began) * 1000)


def _cut(text: str, limit: int | None = None) -> tuple[str, bool]:
    most = MAX_RESULT_CHARS if limit is None else limit
    return (text, False) if len(text) <= most else (text[:most], True)
