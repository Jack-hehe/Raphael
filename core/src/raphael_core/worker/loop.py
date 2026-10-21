"""The loop: plan, call tools, look at the results, go on, until the model reports.

`run_task` is the whole of a background task from the worker's side. It asks the host (`Host`: the
core, through `runner`) for a reply of the model; if the reply asks for calls, it makes them one
after another (v1 has no parallel calls), puts each result into the conversation as data, and asks
again; a reply that asks for none is the report, and the loop ends with it.

Everything that could go wrong on the way is dealt with here or by the host, never by chance:

- a call to a name that was not offered, with arguments that are not a JSON object or that are far
  too long, is not made: the model is told so as the result of that call, and goes on;
- the same call with the same arguments for the third time is not made either (`guards`);
- a result is wrapped as untrusted data and cut down, and the results of earlier steps are dropped
  once the model has gone on (`guards`);
- the host refuses a model call or a tool call when a limit is reached, the user stopped the task,
  or the model failed: it raises `Stop`, and the loop ends there with the kind. The loop counts the
  limits too, so a host that forgot would not make a task endless;
- cancelling is looked at before every step and while a host call is under way: the task stops at
  once, and the call under way is given up.

The loop makes no call by itself and holds no key. Nothing here logs a prompt, an argument or a
result.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .guards import (
    MAX_ARGUMENT_CHARS,
    RepeatGuard,
    canonical,
    elide_old,
    wrap_untrusted,
)

# What the model is told for a call that was not made.
UNKNOWN_TOOL = "There is no such tool. Use only the tools you are offered."
BAD_ARGUMENTS = "The arguments are not a JSON object. Call the tool again with a JSON object."
TOO_LONG = "The arguments are far too long. Call the tool again with less."
REPEATED = (
    "This exact call has been made twice already, with the same arguments. Do not make it again: "
    "use what you have, or try something else, or report."
)


@dataclass(frozen=True)
class Limits:
    """What one task may use. The core enforces these; the loop counts them too."""

    model_calls: int = 15
    tool_calls: int = 25
    seconds: float = 600.0


class Stop(Exception):
    """The host will not go on, or the loop may not. `kind` says why: "cancelled" (the user
    stopped the task), "limit_model", "limit_tool", "limit_time", "limit_cost", "limit_month" (a
    limit was reached), or "failed" (the model failed; `detail` is the kind of failure)."""

    def __init__(self, kind: str, detail: str | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.detail = detail


@dataclass
class Call:
    """A call the model asked for. `arguments` is the JSON it wrote, parsed, or None when what it
    wrote is not JSON; `raw` is the text as it came."""

    id: str
    name: str
    arguments: Any
    raw: str = ""


@dataclass
class Turn:
    """A reply of the model: its words, and the calls it asks for."""

    content: str
    calls: list[Call] = field(default_factory=list)


@dataclass
class ToolOutcome:
    """What came of a call: `ok`; `kind` says why not ("blocked", "timeout", "tool_error",
    "network", ...); `content` is what the tool returned, which nobody has vetted."""

    ok: bool
    kind: str | None = None
    content: str = ""
    truncated: bool = False


class Host(Protocol):
    """The core, as the loop sees it. Both calls may raise `Stop`."""

    async def chat(self, messages: Sequence[dict], tools: Sequence[dict]) -> Turn: ...

    async def tool(self, call_id: str, name: str, arguments: dict) -> ToolOutcome: ...


@dataclass
class Outcome:
    """How a task ended. `status` is "done" (the model reported: `final` is its last reply),
    "stopped" (`kind` says why; see `Stop`) or "cancelled"."""

    status: str
    final: str = ""
    kind: str | None = None
    detail: str | None = None
    model_calls: int = 0
    tool_calls: int = 0


async def _until(
    awaitable: Awaitable[Any], cancelled: asyncio.Event, deadline: float, clock: Callable[[], float]
) -> Any:
    """The result of `awaitable`, unless the task is cancelled or its time runs out first: then it
    is given up and `Stop` is raised."""
    work = asyncio.ensure_future(awaitable)
    watch = asyncio.ensure_future(cancelled.wait())
    try:
        done, _ = await asyncio.wait(
            {work, watch}, timeout=max(deadline - clock(), 0.0), return_when=asyncio.FIRST_COMPLETED
        )
        if work in done:
            return work.result()
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        raise Stop("cancelled" if cancelled.is_set() else "limit_time")
    finally:
        watch.cancel()
        if not work.done():
            work.cancel()
        elif not work.cancelled():
            work.exception()  # looked at, so that a failure nobody is left to see is not reported


def _assistant_message(turn: Turn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.content or None}
    if turn.calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                # What the model wrote, unless it is not JSON: a provider may refuse that coming
                # back to it, and the call was not made anyway.
                "function": {
                    "name": call.name,
                    "arguments": call.raw if call.arguments is not None and call.raw else "{}",
                },
            }
            for call in turn.calls
        ]
    return message


async def run_task(
    host: Host,
    *,
    system: str,
    user: str,
    tools: Sequence[dict],
    limits: Limits = Limits(),
    cancelled: asyncio.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Carry out a task. `system` and `user` are the first two messages of the conversation (the
    core wrote them), `tools` the tools the model is offered in the chat format. The loop ends when
    the model reports, when a limit is reached, when the task is cancelled or when the host stops
    it; it raises nothing for any of those."""
    cancelled = cancelled if cancelled is not None else asyncio.Event()
    offered = {tool["function"]["name"] for tool in tools}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    repeats = RepeatGuard()
    deadline = clock() + limits.seconds
    model_calls = tool_calls = 0

    def check_before(kind: str, used: int, allowed: int) -> None:
        if cancelled.is_set():
            raise Stop("cancelled")
        if clock() >= deadline:
            raise Stop("limit_time")
        if used >= allowed:
            raise Stop(kind)

    try:
        while True:
            check_before("limit_model", model_calls, limits.model_calls)
            turn = await _until(host.chat(messages, tools), cancelled, deadline, clock)
            model_calls += 1
            messages.append(_assistant_message(turn))
            if not turn.calls:
                return Outcome("done", turn.content, None, None, model_calls, tool_calls)
            for call in turn.calls:
                refusal = _refusal(call, offered, repeats)
                if refusal is None:
                    check_before("limit_tool", tool_calls, limits.tool_calls)
                    tool_calls += 1
                    outcome = await _until(
                        host.tool(call.id, call.name, call.arguments), cancelled, deadline, clock
                    )
                    content = _result_text(call.name, outcome)
                else:
                    content = wrap_untrusted(call.name, refusal, ok=False)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            elide_old(messages)
    except Stop as stop:
        status = "cancelled" if stop.kind == "cancelled" else "stopped"
        return Outcome(status, "", stop.kind, stop.detail, model_calls, tool_calls)


def _refusal(call: Call, offered: set[str], repeats: RepeatGuard) -> str | None:
    """Why a call is not made, in the words the model is told, or None when it may be."""
    if call.name not in offered:
        return UNKNOWN_TOOL
    if not isinstance(call.arguments, dict):
        return BAD_ARGUMENTS
    if len(canonical(call.arguments)) > MAX_ARGUMENT_CHARS:
        return TOO_LONG
    if not repeats.allows(call.name, call.arguments):
        return REPEATED
    return None


def _result_text(name: str, outcome: ToolOutcome) -> str:
    if outcome.ok:
        return wrap_untrusted(name, outcome.content, ok=True)
    reason = outcome.kind or "error"
    text = f"The call failed ({reason})."
    if outcome.content:  # a tool that says why it failed: its words are data too
        text += f"\n{outcome.content}"
    return wrap_untrusted(name, text, ok=False)
