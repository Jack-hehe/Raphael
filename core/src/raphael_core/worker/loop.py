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
  once, and the call under way is given up;
- a call the owner must say yes to first stops the loop too (`Stop("waiting")`): the core keeps the
  question, and the loop gives back the conversation as it stands (the reply that asked for the
  call, and the results of the calls of that reply made before it), so that the task can go on
  later from there. It is started again with that conversation (`messages`), and with the result
  of the call the owner answered, and of those after it that were not made, added by the core.

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

    model_calls: int = 80
    tool_calls: int = 200
    seconds: float = 1800.0


# The kind of `Stop` of a call that waits for the owner's yes: the task pauses there.
WAITING = "waiting"


class Stop(Exception):
    """The host will not go on, or the loop may not. `kind` says why: "cancelled" (the user
    stopped the task), "limit_model", "limit_tool", "limit_time" (a
    limit was reached), "failed" (the model failed; `detail` is the kind of failure) or "waiting"
    (a call waits for the owner's yes: the task pauses)."""

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
    tools: list[dict] = field(default_factory=list)  # tools the call made available from now on


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
    # A task that pauses for the owner's yes ("stopped", kind "waiting"): the conversation as it
    # stands, and the id of the call that waits.
    messages: list[dict] | None = None
    waiting_call: str | None = None


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
    messages: Sequence[dict] | None = None,
) -> Outcome:
    """Carry out a task. `system` and `user` are the first two messages of the conversation (the
    core wrote them), `tools` the tools the model is offered in the chat format; a task that goes
    on after a pause starts from the whole conversation instead (`messages`, which the core gives
    back as the loop left it, with the results of the calls that waited). The loop ends when the
    model reports, when a limit is reached, when the task is cancelled or when the host stops it;
    it raises nothing for any of those. `limits` are what is left to the task."""
    cancelled = cancelled if cancelled is not None else asyncio.Event()
    tools = list(tools)
    offered = {tool["function"]["name"] for tool in tools}
    conversation: list[dict[str, Any]] = (
        [dict(message) for message in messages]
        if messages
        else [{"role": "system", "content": system}, {"role": "user", "content": user}]
    )
    messages = conversation
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
                    try:
                        outcome = await _until(
                            host.tool(call.id, call.name, call.arguments), cancelled, deadline, clock
                        )
                    except Stop as stop:
                        if stop.kind != WAITING:
                            raise
                        # The task pauses for the owner: the core keeps the conversation.
                        return Outcome(
                            "stopped", "", WAITING, None, model_calls, tool_calls,
                            messages=list(messages), waiting_call=call.id,
                        )  # fmt: skip
                    content = result_text(call.name, outcome)
                    for found in outcome.tools:
                        name = found["function"]["name"]
                        if name not in offered:
                            offered.add(name)
                            tools.append(found)
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


def result_text(name: str, outcome: ToolOutcome) -> str:
    """What the model is shown of a call that was made: its result, or why it failed, wrapped as
    untrusted data. The core uses it too, for the call a paused task waited on."""
    if outcome.ok:
        return wrap_untrusted(name, outcome.content, ok=True)
    reason = outcome.kind or "error"
    text = f"The call failed ({reason})."
    if outcome.content:  # a tool that says why it failed: its words are data too
        text += f"\n{outcome.content}"
    return wrap_untrusted(name, text, ok=False)
