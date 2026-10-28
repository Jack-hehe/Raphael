"""A pool of MCP connections: the calls of one job go through one connection each server, opened
at the first call and closed when the job is over.

Without a pool every call starts the server (npx, say, for a few seconds), lists its tools and
stops it. A background task that calls three tools of one server would do that three times; with a
pool it does it once.

- **One owner task for each connection.** The SDK's streams and task groups (anyio) must be entered
  and left in the same asyncio task, and the tasks that make the calls are not the same from one
  call to the next (`TaskRun` runs each in a task of its own so that it can be cancelled). So a
  connection is owned by a task of its own that opens the client, reads the tool list once, serves
  the calls that arrive in its inbox, and closes the client when it ends.
- **The owner is stopped through an anyio cancel scope, never with `Task.cancel()`.** The SDK stops
  a stdio server (closes its stdin, waits for it, kills it, closes its output) in a scope shielded
  from anyio's cancellation, and drains the server's output in another until that output is
  closed. A plain `Task.cancel()` is not held off by those shields: one that lands while the SDK
  is stopping the server breaks the stop off before the server's stdin is closed, the server
  stays, and the drain, the owner and everyone who waits for it (`aclose`, the background tasks
  queued behind) wait for ever. A connection is closed twice in the ordinary course of things (a
  cancelled call closes it, and the pool is closed right after), so the second close would come
  while the first one's stop is under way. The scope's cancel waits for the shield, and asking
  for it again is harmless.
- **Calls are made at the same time.** MCP takes several requests over one session, so each call
  is a task of its own inside the owner's scope and a slow call does not make the ones after it
  wait (the time-out of a call is its own).
- **A call that is cancelled or times out closes the whole connection.** Nobody can say what state a
  server is in after a call was given up on, and a tool may still be running in it. The next call
  opens another connection, and the other calls under way on this one fail with `gone`. (The server
  is not told to cancel the call: closing it is the cancel.)
- **A connection is closed when the secrets it was made with change** (`evict`, which the
  connector calls on a login again or a removal).
- **A connection is closed** when its pool is (`aclose`: the task is over, cancelled, timed out or
  the core is closing), when it has been idle for `idle_s` (the pool of the fast tier keeps one
  for two minutes, so that a second question about the same service is quick), and when a call on
  it failed in a way that may have broken it.
- **The tools** are read when the connection is opened and kept; a tool that is not in the list is
  `gone`. A server that says its tools changed is not listened to: the next job opens a new
  connection and reads them again.

A failure is a `McpFailure` or what the SDK raised, to be turned into a kind by the connector
(`client._failure`). Nothing here logs a command, an address, an argument or a result.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

import anyio  # the SDK's own (`mcp` depends on it): its cancel scopes are what its shields hold off

from .client import McpFailure
from .config import ToolRecord

# What the owner of a connection is told in its inbox besides a call.
_IDLE = object()
_STOP = object()

OpenClient = Callable[[], AbstractAsyncContextManager[Any]]
ListTools = Callable[[Any], Awaitable[list[ToolRecord]]]


@dataclass(eq=False)
class _Call:
    tool: str
    arguments: dict[str, Any]
    future: "asyncio.Future[Any]"


class _Connection:
    """One open client of one server, and the task that owns it."""

    def __init__(
        self,
        open_client: OpenClient,
        list_tools: ListTools,
        idle_s: float | None,
        on_end: Callable[["_Connection"], None],
    ) -> None:
        loop = asyncio.get_running_loop()
        self._open = open_client
        self._list = list_tools
        self._idle_s = idle_s
        self._on_end = on_end
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()
        self.ready: asyncio.Future[None] = loop.create_future()
        # The failure to open is given to whoever waits for the connection; nobody may be left.
        self.ready.add_done_callback(lambda f: f.cancelled() or f.exception())
        self.tools: list[ToolRecord] = []
        self.closed = False
        self._calls: set[_Call] = set()  # the calls being made
        self.served = 0  # the calls given to this connection
        # How `close` stops the owner (see the module): the owner's cancel scope, once it runs, and
        # whether it was asked to stop before it did.
        self._scope: anyio.CancelScope | None = None
        self._stop_asked = False
        self.task = asyncio.ensure_future(self._own())
        # A task cancelled before it first ran (the core closing) never reaches its `finally`.
        self.task.add_done_callback(lambda _task: self._settle())

    async def _own(self) -> None:
        try:
            # A cancel of this scope (`close`) ends the owner wherever it is, except inside the
            # SDK's shielded stop of the server, which it lets finish; the scope swallows it.
            with anyio.CancelScope() as self._scope:
                if self._stop_asked:
                    return  # closed before it began: no server is started
                await self._serve()
        except asyncio.CancelledError:
            raise
        except BaseException as error:  # the SDK's task groups wrap what went wrong
            if not self.ready.done():
                self.ready.set_exception(error)
        finally:
            self._settle()

    async def _serve(self) -> None:
        """Open the client, read its tools, and make the calls of the inbox until told to stop or
        idle long enough; the client is closed on the way out."""
        loop = asyncio.get_running_loop()
        async with self._open() as client:
            self.tools = await self._list(client)
            self.ready.set_result(None)
            making: set[asyncio.Task[None]] = set()
            try:
                while True:
                    timer = None
                    if self._idle_s is not None:
                        timer = loop.call_later(self._idle_s, self.inbox.put_nowait, _IDLE)
                    try:
                        item = await self.inbox.get()
                    finally:
                        if timer is not None:
                            timer.cancel()
                    if item is _STOP:
                        break
                    if item is _IDLE:
                        if making or not self.inbox.empty():
                            continue  # a call is under way or came just now: not idle
                        self.closed = True  # from here on a call opens another connection
                        break
                    if item.future.done():
                        continue  # the caller gave up before it was its turn
                    self._calls.add(item)
                    made = asyncio.ensure_future(self._make(client, item))
                    making.add(made)
                    made.add_done_callback(making.discard)
            finally:
                # The calls under way end before the client does, in the task that owns it. (A
                # cancelled scope cancels every wait in it again and again: this one is shielded,
                # and the calls, cancelled once, end at once.)
                for made in list(making):
                    made.cancel()
                if making:
                    with anyio.CancelScope(shield=True):
                        await asyncio.wait(making)

    async def _make(self, client: Any, item: _Call) -> None:
        """One call, in a task of its own."""
        try:
            result = await client.call_tool(item.tool, item.arguments)
        except asyncio.CancelledError:
            if not item.future.done():
                item.future.set_exception(McpFailure("cancelled"))  # it was sent
            raise
        except Exception as error:
            # It may have broken the connection: it is closed after this.
            self.closed = True
            if not item.future.done():
                item.future.set_exception(error)
            self.inbox.put_nowait(_STOP)
        else:
            if not item.future.done():
                item.future.set_result(result)
        finally:
            self._calls.discard(item)

    def _settle(self) -> None:
        """The connection is over: whoever waits for it, or for a call on it, is told. Safe to
        run more than once."""
        self.closed = True
        self._on_end(self)
        if not self.ready.done():
            self.ready.set_exception(McpFailure("gone"))
        # A call that was being made may have been made; one that was waiting was not sent.
        for item in list(self._calls):
            if not item.future.done():
                item.future.set_exception(McpFailure("cancelled"))
        while not self.inbox.empty():
            item = self.inbox.get_nowait()
            if item is not _IDLE and item is not _STOP and not item.future.done():
                item.future.set_exception(McpFailure("gone"))

    def close(self) -> None:
        """Ask the owner to end; it closes the client in its own task. Safe to call again while
        it does (see the module: never `Task.cancel()`)."""
        self.closed = True
        self._stop_asked = True
        if self._scope is not None:
            self._scope.cancel()


class McpPool:
    """The connections of one job (or of the fast tier). Use it on the event loop. `idle_s` is how
    long a connection with nothing to do is kept (None: until the pool is closed)."""

    def __init__(self, *, idle_s: float | None = None) -> None:
        self._idle_s = idle_s
        self._connections: dict[str, _Connection] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    @property
    def open_count(self) -> int:
        """How many connections are open or being opened."""
        return sum(1 for c in self._connections.values() if not c.closed)

    def evict(self, key: str) -> None:
        """Close the connection kept for the server `key`, if there is one (its calls under way
        fail). The next call opens another."""
        connection = self._connections.get(key)
        if connection is not None:
            connection.close()

    def _ended(self, connection: _Connection) -> None:
        for key, held in list(self._connections.items()):
            if held is connection:
                del self._connections[key]

    def _connection(self, key: str, open_client: OpenClient, list_tools: ListTools) -> _Connection:
        held = self._connections.get(key)
        if held is None or held.closed:
            held = _Connection(open_client, list_tools, self._idle_s, self._ended)
            self._connections[key] = held
            self._tasks.add(held.task)
            held.task.add_done_callback(self._tasks.discard)
        return held

    async def call(
        self,
        key: str,
        open_client: OpenClient,
        list_tools: ListTools,
        tool: str,
        arguments: dict[str, Any],
    ) -> tuple[Any, ToolRecord, list[ToolRecord] | None]:
        """Call `tool` of the server `key` through its connection (opened if there is none): the
        client's result, the tool as the server described it when the connection was opened, and,
        for the first call of the connection only, the whole list read when it opened (it was read
        for this call, so it is not older than anything saved; the list of a connection that has
        been open for other calls may be).
        `McpFailure("gone")` for a tool the server does not have. Whatever ends the call early (a
        cancel, a time-out) closes the connection."""
        if self._closed:
            raise McpFailure("gone")
        connection = self._connection(key, open_client, list_tools)
        try:
            await asyncio.shield(connection.ready)
            current = next((t for t in connection.tools if t.name == tool), None)
            if current is None:
                raise McpFailure("gone")
            first = connection.served == 0
            connection.served += 1
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            connection.inbox.put_nowait(_Call(tool, arguments, future))
            return await future, current, list(connection.tools) if first else None
        except asyncio.CancelledError:
            connection.close()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait({connection.task})
            raise

    async def aclose(self) -> None:
        """Close every connection and wait until their servers are stopped. Safe twice."""
        self._closed = True
        for connection in list(self._connections.values()):
            connection.close()
        tasks = list(self._tasks)
        if tasks:
            await asyncio.wait(tasks)
