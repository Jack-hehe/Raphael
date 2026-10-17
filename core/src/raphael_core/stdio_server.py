"""JSON-RPC over stdio: one JSON object per line, requests in, responses and notifications out.

serve() speaks the protocol over any line source and sink. run_stdio() binds it to this
process's stdin and stdout, which is how the Tauri app talks to the core. claim_stdout() keeps
the protocol's stdout to itself, away from native code that writes to fd 1.

Never log a request line or its params here: `secrets.set_openrouter_key` carries the
OpenRouter key.
"""

import asyncio
import contextlib
import json
import logging
import os
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import BinaryIO

from .protocol import (
    INTERNAL_ERROR,
    RpcError,
    error_response,
    notification,
    parse_message,
    peek_id,
    response,
)
from .router import Router

logger = logging.getLogger(__name__)

_STD_OUTPUT_HANDLE = 0xFFFFFFF5  # (DWORD)-11, for SetStdHandle


def _encode(message: dict) -> str:
    # Compact, and ASCII-only (non-ASCII text is \u-escaped) so the output cannot depend on the
    # encoding stdout happens to have. allow_nan=False because serde_json rejects NaN.
    return json.dumps(message, separators=(",", ":"), allow_nan=False)


def _encode_message(message: dict) -> str:
    """`_encode`, except that a reply that cannot be encoded becomes an error reply.

    Router.call checks results, but a handler's RpcError reaches serve untouched, so its `data` may
    hold anything (a set, a Path, a numpy scalar). The request must still be answered.
    """
    try:
        return _encode(message)
    except Exception:
        if "id" not in message:  # a notification: nothing waits for it
            raise
        logger.exception("The reply to request %r cannot be encoded", message["id"])
        # The id was validated on the way in, so this always encodes.
        return _encode(
            error_response(message["id"], RpcError(INTERNAL_ERROR, "Response could not be encoded"))
        )


async def serve(router: Router, lines: AsyncIterator[str], write: Callable[[str], None]) -> None:
    """Answer the JSON-RPC requests in `lines` through `write` until `lines` ends.

    `lines` yields one message per item. `write` receives one compact JSON document per call, with
    no newline: the sink does the framing. It may be called from any thread (a `Router.notify` from
    the audio thread, say), so it must be thread-safe. Each request runs as its own task, so
    requests may overlap, and the ones still running when the input ends are answered before this
    returns. An incoming notification is run but never answered, not even with an error. An
    incoming response is ignored: the core makes no requests of its own.
    """

    def send(message: dict) -> None:
        try:
            write(_encode_message(message))
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


def claim_stdout() -> BinaryIO:
    """Take this process's stdout for the protocol alone, and return it as a binary stream.

    The stdout the process was given is kept on a new descriptor, which is returned, and fd 1 (and,
    on Windows, the standard output handle) is pointed at stderr. So a native library that prints
    (ONNX Runtime, PortAudio, OpenCC) or a stray print() lands in the log, not in the middle of a
    JSON-RPC line. Call it before such libraries are imported, and pass what it returns to
    run_stdio as `output`: from here on, sys.stdout writes to stderr.
    """
    with contextlib.suppress(Exception):
        sys.stdout.flush()
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    if sys.platform == "win32":
        # Code that asks Windows for its stdout gets the handle, not fd 1. The C runtime points the
        # handle at stderr too for a console program; it is said again here so it never depends on
        # that.
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        kernel32.SetStdHandle.restype = ctypes.c_int
        if not kernel32.SetStdHandle(_STD_OUTPUT_HANDLE, msvcrt.get_osfhandle(2)):
            logger.warning("Could not point the standard output handle at stderr")
    return os.fdopen(protocol_fd, "wb")


def run_stdio(
    router: Router,
    on_close: Callable[[], Awaitable[None]] | None = None,
    *,
    output: BinaryIO | None = None,
    stop: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Serve JSON-RPC on stdin and stdout until stdin closes, then return.

    stdout carries protocol lines and nothing else; logging goes to stderr, where the app reads it.
    `output` is the binary stream the protocol is written to: what claim_stdout returned, or by
    default sys.stdout.

    `stop`, if given, is awaited on the loop beside the input. When it returns, the server stops as
    if stdin had closed: the requests in flight are answered, then `on_close` runs. That is how
    `core.shutdown` ends the process.

    `on_close` runs on the event loop once the input has ended and every request is answered, for
    what has to be closed on the loop (an async client, say). A failure in it is logged, not raised:
    the server is stopping anyway.
    """
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_serve_stdio(router, on_close, output=output, stop=stop))


async def _serve_stdio(
    router: Router,
    on_close: Callable[[], Awaitable[None]] | None = None,
    *,
    output: BinaryIO | None = None,
    stop: Callable[[], Awaitable[None]] | None = None,
) -> None:
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
    sink = sys.stdout.buffer if output is None else output

    def write(line: str) -> None:
        # Bytes, so the line ends in "\n" on Windows too (text mode would write "\r\n").
        data = line.encode("utf-8") + b"\n"
        with write_lock:
            sink.write(data)
            sink.flush()

    async def stop_when_asked(stop: Callable[[], Awaitable[None]]) -> None:
        await stop()
        logger.info("Asked to stop")
        post(None)  # as if stdin had closed

    threading.Thread(target=read_stdin, name="stdin-reader", daemon=True).start()
    stopper = None if stop is None else asyncio.create_task(stop_when_asked(stop))
    logger.info("Serving JSON-RPC on stdio")
    try:
        await serve(router, lines(), write)
    finally:
        if stopper is not None:
            stopper.cancel()
            await asyncio.gather(stopper, return_exceptions=True)
    logger.info("Input closed, stopping")
    if on_close is not None:
        try:
            await on_close()
        except Exception:
            logger.exception("Shutdown did not finish cleanly")
