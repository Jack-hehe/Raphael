"""JSON-RPC 2.0 in both directions over lines, for the core and the worker to talk.

`stdio_server.serve` answers requests and ignores responses, because the app never waits for the
core to ask it something. The core and its worker do ask each other: the core sends `task.run` and
waits for the report, while the worker, meanwhile, sends `host.chat` and `host.tool` and waits for
the answers. A `Peer` is one end of that: it sends requests and notifications, answers those it is
sent, and matches the responses it gets to the requests it made.

It knows nothing of lines on a pipe: `serve` reads from any async iterator of lines, and the
`write` it is made with takes one compact JSON document and frames it. Never log a message or its
params here: they carry the task.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from raphael_core.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    RpcError,
    error_response,
    notification,
    parse_message,
    peek_id,
    request,
    response,
)

logger = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[Any]]


class PeerClosed(Exception):
    """The other end is gone (its output ended, or this end was closed), so no answer will come."""


def encode(message: dict) -> str:
    """One message as a compact, ASCII-only JSON document (no newline: the sink frames it)."""
    return json.dumps(message, separators=(",", ":"), allow_nan=False)


class Peer:
    """One end of a conversation in JSON-RPC. `write` sends one encoded message; it is called from
    the event loop only. Handlers are registered by method name; a request is answered with the
    handler's result, or with its `RpcError`, and a notification is run and never answered."""

    def __init__(self, write: Callable[[str], None]) -> None:
        self._write = write
        self._handlers: dict[str, Handler] = {}
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._running: set[asyncio.Task[None]] = set()
        self._next_id = 0
        self._closed = False

    def register(self, method: str, handler: Handler) -> None:
        if method in self._handlers:
            raise ValueError(f"method already registered: {method}")
        self._handlers[method] = handler

    @property
    def closed(self) -> bool:
        return self._closed

    def _send(self, message: dict) -> None:
        if self._closed:
            raise PeerClosed
        try:
            line = encode(message)
        except (TypeError, ValueError):
            if "id" in message and "method" not in message:
                # A reply that cannot be encoded must still be answered.
                line = encode(
                    error_response(message["id"], RpcError(INTERNAL_ERROR, "Reply is not JSON"))
                )
            else:
                raise
        try:
            self._write(line)
        except (OSError, ValueError) as exc:  # a pipe that was closed on us
            raise PeerClosed from exc

    async def request(self, method: str, params: dict | None = None) -> Any:
        """Call `method` on the other end and wait for the answer. RpcError if it answers with an
        error, PeerClosed if the conversation ends first. Cancelling the wait forgets the request."""
        if self._closed:
            raise PeerClosed
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            self._send(request(request_id, method, params))
            return await future
        finally:
            self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict) -> None:
        """Tell the other end something. Nothing comes back; a closed conversation is ignored."""
        with contextlib.suppress(PeerClosed):
            self._send(notification(method, params))

    async def serve(self, lines: AsyncIterator[str]) -> None:
        """Handle what comes in until `lines` ends, then close: everything still waited for fails
        with PeerClosed and the handlers still running are cancelled."""
        try:
            async for line in lines:
                if line.strip():
                    self._dispatch(line)
        finally:
            self.close()
            running = list(self._running)
            for task in running:
                task.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)

    def close(self) -> None:
        self._closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(PeerClosed())

    def _dispatch(self, line: str) -> None:
        try:
            msg = parse_message(line)
        except RpcError as err:
            with contextlib.suppress(PeerClosed):
                self._send(error_response(peek_id(line), err))
            return
        if "method" in msg:
            task = asyncio.ensure_future(self._handle(msg))
            self._running.add(task)
            task.add_done_callback(self._running.discard)
            return
        future = self._pending.get(msg.get("id"))  # type: ignore[arg-type]
        if future is None or future.done():
            return  # a late answer to a request nobody waits for any more
        if "error" in msg and isinstance(msg["error"], dict):
            error = msg["error"]
            code = error.get("code")
            message = error.get("message")
            future.set_exception(
                RpcError(
                    code if isinstance(code, int) else INTERNAL_ERROR,
                    message if isinstance(message, str) else "Error",
                    error.get("data"),
                )
            )
        else:
            future.set_result(msg.get("result"))

    async def _handle(self, msg: dict) -> None:
        method = msg["method"]
        handler = self._handlers.get(method)
        params = msg.get("params")
        try:
            if handler is None:
                raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise RpcError(INVALID_PARAMS, "Invalid params: expected a JSON object")
            result = await handler(params)
        except RpcError as err:
            self._reply(msg, error_response(msg.get("id"), err))
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the type only: its message could hold the task
            logger.warning("The handler of %s failed: %s", method, type(exc).__name__)
            self._reply(msg, error_response(msg.get("id"), RpcError(INTERNAL_ERROR, "Failed")))
            return
        self._reply(msg, response(msg.get("id"), result))

    def _reply(self, msg: dict, reply: dict) -> None:
        if "id" not in msg:
            return  # a notification is never answered
        with contextlib.suppress(PeerClosed):
            self._send(reply)
