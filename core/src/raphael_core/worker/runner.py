"""The worker process: `raphael-core.exe --worker` (`python -m raphael_core --worker`).

It speaks JSON-RPC with the core that started it, on stdin and stdout, and ends when stdin closes:
a core that is gone leaves it nothing to do. The core calls:

- `worker.hello {}`: answered with the protocol version and the process id (the start-up handshake);
- `task.run {task_id, system, user, tools, limits}`: carry the task out (`loop.run_task`) and answer
  with how it ended. One task at a time;
- `task.cancel {task_id}` (a notification): stop the task at once.

and, while a task runs, the worker calls the core's `host.chat {task_id, messages, tools}` for a
reply of the model and `host.tool {task_id, call_id, name, arguments}` for the result of a tool. A
refusal of either comes as an error whose data says `stop`, and the loop ends with it.

stdout is claimed for the protocol before anything else is done (`stdio_server.claim_stdout`). This
module imports the loop and the channel and nothing of the core's other side: no keys, no database,
no plugins (see the package docstring). Nothing here logs a task, a prompt, an argument or a result.
"""

import asyncio
import contextlib
import logging
import os
import sys
import threading
from collections.abc import AsyncIterator, Sequence
from typing import Any

from raphael_core.protocol import BUSY, INVALID_PARAMS, PROTOCOL_VERSION, WORKER_STOPPED, RpcError
from raphael_core.stdio_server import _decoded_lines, claim_stdout

from .channel import Peer
from .loop import Call, Limits, Outcome, Stop, ToolOutcome, Turn, run_task

logger = logging.getLogger(__name__)


def _invalid(message: str) -> RpcError:
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


class CoreHost:
    """The core, as the loop's `Host`: every call is a request to it."""

    def __init__(self, peer: Peer, task_id: int) -> None:
        self._peer = peer
        self._task_id = task_id

    async def _ask(self, method: str, params: dict) -> Any:
        try:
            return await self._peer.request(method, {"task_id": self._task_id, **params})
        except RpcError as error:
            data = error.data if isinstance(error.data, dict) else {}
            if error.code == WORKER_STOPPED and isinstance(data.get("stop"), str):
                detail = data.get("detail")
                raise Stop(data["stop"], detail if isinstance(detail, str) else None) from None
            raise

    async def chat(self, messages: Sequence[dict], tools: Sequence[dict]) -> Turn:
        reply = await self._ask("host.chat", {"messages": list(messages), "tools": list(tools)})
        calls = [
            Call(
                id=str(call.get("id", "")),
                name=str(call.get("name", "")),
                arguments=call.get("arguments"),
                raw=str(call.get("raw", "")),
            )
            for call in reply.get("calls", [])
            if isinstance(call, dict)
        ]
        content = reply.get("content")
        return Turn(content if isinstance(content, str) else "", calls)

    async def tool(self, call_id: str, name: str, arguments: dict) -> ToolOutcome:
        reply = await self._ask(
            "host.tool", {"call_id": call_id, "name": name, "arguments": arguments}
        )
        content = reply.get("content")
        kind = reply.get("kind")
        return ToolOutcome(
            ok=reply.get("ok") is True,
            kind=kind if isinstance(kind, str) else None,
            content=content if isinstance(content, str) else "",
            truncated=reply.get("truncated") is True,
            tools=[t for t in (reply.get("tools") or []) if _is_tool(t)],
        )


def _limits(params: Any) -> Limits:
    if not isinstance(params, dict):
        return Limits()
    defaults = Limits()

    def number(name: str, default: float) -> float:
        value = params.get(name)
        ok = isinstance(value, int | float) and not isinstance(value, bool) and value > 0
        return float(value) if ok else default

    return Limits(
        model_calls=int(number("model_calls", defaults.model_calls)),
        tool_calls=int(number("tool_calls", defaults.tool_calls)),
        seconds=number("seconds", defaults.seconds),
    )


def _result(outcome: Outcome) -> dict:
    return {
        "status": outcome.status,
        "final": outcome.final,
        "kind": outcome.kind,
        "detail": outcome.detail,
        "model_calls": outcome.model_calls,
        "tool_calls": outcome.tool_calls,
    }


class Worker:
    """What the process does with its peer: the methods the core calls."""

    def __init__(self, peer: Peer) -> None:
        self._peer = peer
        self._task_id: int | None = None
        self._cancelled = asyncio.Event()
        peer.register("worker.hello", self._hello)
        peer.register("task.run", self._run)
        peer.register("task.cancel", self._cancel)

    async def _hello(self, params: dict) -> dict:
        return {"protocol": PROTOCOL_VERSION, "pid": os.getpid()}

    async def _run(self, params: dict) -> dict:
        task_id, system, user = params.get("task_id"), params.get("system"), params.get("user")
        tools = params.get("tools")
        if type(task_id) is not int:
            raise _invalid("task_id must be a whole number")
        if not isinstance(system, str) or not isinstance(user, str):
            raise _invalid("system and user must be strings")
        if not isinstance(tools, list) or not all(_is_tool(tool) for tool in tools):
            raise _invalid("tools must be a list of tools in the chat format")
        if self._task_id is not None:
            raise RpcError(BUSY, "A task is running")
        self._task_id = task_id
        self._cancelled = asyncio.Event()
        try:
            outcome = await run_task(
                CoreHost(self._peer, task_id),
                system=system,
                user=user,
                tools=tools,
                limits=_limits(params.get("limits")),
                cancelled=self._cancelled,
            )
        finally:
            self._task_id = None
        logger.info(
            "Task ended: status=%s kind=%s model_calls=%d tool_calls=%d",
            outcome.status,
            outcome.kind,
            outcome.model_calls,
            outcome.tool_calls,
        )
        return _result(outcome)

    async def _cancel(self, params: dict) -> dict:
        if self._task_id is not None and params.get("task_id") in (None, self._task_id):
            self._cancelled.set()
        return {}


def _is_tool(tool: Any) -> bool:
    function = tool.get("function") if isinstance(tool, dict) else None
    return isinstance(function, dict) and isinstance(function.get("name"), str)


async def _lines_from_stdin() -> AsyncIterator[str]:
    """The lines of stdin. A thread reads them (asyncio's pipe support for stdin is unreliable on
    Windows), as in `stdio_server`."""
    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue[str | None] = asyncio.Queue()

    def post(item: str | None) -> None:
        try:
            loop.call_soon_threadsafe(inbox.put_nowait, item)
        except RuntimeError:  # the loop is closed: we are stopping
            pass

    def read() -> None:
        stdin = sys.stdin.buffer
        try:
            for line in _decoded_lines(getattr(stdin, "raw", stdin).read):
                post(line)
        finally:
            post(None)

    threading.Thread(target=read, name="stdin-reader", daemon=True).start()
    while (line := await inbox.get()) is not None:
        yield line


async def serve(output: Any) -> None:
    write_lock = threading.Lock()

    def write(line: str) -> None:
        data = line.encode("utf-8") + b"\n"
        with write_lock:
            output.write(data)
            output.flush()

    peer = Peer(write)
    Worker(peer)
    logger.info("The worker is serving on stdio")
    await peer.serve(_lines_from_stdin())
    logger.info("Input closed, stopping")


def main() -> None:
    """The entry point of `--worker`. Returns when the core closes our stdin."""
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    output = claim_stdout()
    try:
        asyncio.run(serve(output))
    finally:
        with contextlib.suppress(OSError):
            output.close()
