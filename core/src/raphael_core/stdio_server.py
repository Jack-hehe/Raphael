"""JSON-RPC over stdio: one JSON object per line, requests in, responses and notifications out.

serve() speaks the protocol over any line source and sink. run_stdio() binds it to this
process's stdin and stdout, which is how the Tauri app talks to the core.

Never log a request line or its params here: `secrets.set_openrouter_key` carries the
OpenRouter key.
"""

import asyncio
import contextlib
import json
import logging
import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator

from .protocol import RpcError, error_response, notification, parse_message, peek_id, response
from .router import Router

logger = logging.getLogger(__name__)


def _encode(message: dict) -> str:
    # Compact, and ASCII-only (non-ASCII text is \u-escaped) so the output cannot depend on the
    # encoding stdout happens to have. allow_nan=False because serde_json rejects NaN.
    return json.dumps(message, separators=(",", ":"), allow_nan=False)


async def serve(router: Router, lines: AsyncIterator[str], write: Callable[[str], None]) -> None:
    """Answer the JSON-RPC requests in `lines` through `write` until `lines` ends.

    `lines` yields one message per item. `write` receives one compact JSON document per call, with
    no newline: the sink does the framing. Each request runs as its own task, so requests may
    overlap, and the ones still running when the input ends are answered before this returns.
    An incoming notification is run but never answered, not even with an error. An incoming
    response is ignored: the core makes no requests of its own.
    """

    def send(message: dict) -> None:
        try:
            write(_encode(message))
        except Exception:
            logger.exception("Could not write a message")

    def forward(method: str, params: dict) -> None:
        send(notification(method, params))

    async def handle(msg: dict) -> None:
        method = msg["method"]
        try:
            result = await router.call(method, msg.get("params"))
        except RpcError as err:
            if "id" in msg:
                send(error_response(msg["id"], err))
            else:
                logger.warning("Notification %s failed: %s", method, err)
            return
        if "id" in msg:
            send(response(msg["id"], result))

    router.set_notifier(forward)
    tasks: set[asyncio.Task[None]] = set()
    try:
        async for line in lines:
            if not line.strip():
                continue
            try:
                msg = parse_message(line)
            except RpcError as err:
                send(error_response(peek_id(line), err))
                continue
            if "method" not in msg:
                continue
            task = asyncio.create_task(handle(msg))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:  # still running only if we were cancelled or `lines` failed
            task.cancel()
        router.set_notifier(None)


def _decoded_lines(read: Callable[[int], bytes]) -> Iterator[str]:
    """The lines of a byte source, decoded as UTF-8 whatever the locale says.

    `read(n)` returns up to n bytes and b"" at the end. A line may arrive in several pieces, so
    it is split on b"\\n" before it is decoded, never in the middle of a character.
    """
    pending = b""
    while chunk := read(65536):
        *complete, pending = (pending + chunk).split(b"\n")
        for raw in complete:
            yield raw.decode("utf-8", errors="replace")
    if pending:
        yield pending.decode("utf-8", errors="replace")


def run_stdio(router: Router) -> None:
    """Serve JSON-RPC on stdin and stdout until stdin closes, then return.

    stdout carries protocol lines and nothing else; logging goes to stderr, where the app reads it.
    """
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_serve_stdio(router))


async def _serve_stdio(router: Router) -> None:
    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue[str | None] = asyncio.Queue()

    def post(item: str | None) -> None:
        with contextlib.suppress(RuntimeError):  # the loop is closed: the server stopped first
            loop.call_soon_threadsafe(inbox.put_nowait, item)

    def read_stdin() -> None:
        # A thread rather than asyncio's pipe support, which is unreliable for stdin on Windows.
        # Reads go to the buffer's raw layer: a BufferedReader holds its lock while it blocks, and
        # a daemon thread holding it makes the interpreter abort at shutdown ("could not acquire
        # lock ... possibly due to daemon threads") when run_stdio is left with stdin still open.
        stdin = sys.stdin.buffer
        try:
            for line in _decoded_lines(getattr(stdin, "raw", stdin).read):
                post(line)
        finally:
            post(None)  # end of input, or reading failed: either way, stop serving

    async def lines() -> AsyncIterator[str]:
        while (line := await inbox.get()) is not None:
            yield line

    write_lock = threading.Lock()  # notifications may come from any thread

    def write(line: str) -> None:
        # Bytes, so the line ends in "\n" on Windows too (text mode would write "\r\n").
        data = line.encode("utf-8") + b"\n"
        with write_lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    threading.Thread(target=read_stdin, name="stdin-reader", daemon=True).start()
    logger.info("Serving JSON-RPC on stdio")
    await serve(router, lines(), write)
    logger.info("Input closed, stopping")
