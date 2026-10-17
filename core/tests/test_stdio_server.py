import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

from raphael_core.protocol import BUSY, RpcError
from raphael_core.router import Router
from raphael_core.stdio_server import _decoded_lines, serve


async def test_serves_requests_and_stops_at_eof():
    r = Router()
    async def hello(p): return {"protocol": 1}
    r.register("core.hello", hello)
    out = []
    async def lines():
        yield '{"jsonrpc":"2.0","id":1,"method":"core.hello","params":{}}'
        yield '{"jsonrpc":"2.0","id":2,"method":"no.such","params":{}}'
    await serve(r, lines(), out.append)
    msgs = sorted((json.loads(x) for x in out), key=lambda m: m["id"])
    assert msgs[0]["result"] == {"protocol": 1}
    assert msgs[1]["error"]["code"] == -32601


async def test_notifications_are_written():
    r = Router()
    async def ping(p):
        r.notify("audio.level", {"level": 0.5})
        return "ok"
    r.register("t.ping", ping)
    out = []
    async def lines():
        yield '{"jsonrpc":"2.0","id":1,"method":"t.ping"}'
    await serve(r, lines(), out.append)
    assert {"jsonrpc": "2.0", "method": "audio.level", "params": {"level": 0.5}} in [json.loads(x) for x in out]


# --- beyond the brief's examples -------------------------------------------------------------


def feed(*lines):
    async def gen():
        for line in lines:
            yield line

    return gen()


def call(id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


async def run(router, *lines):
    """Serve the given input lines to the end and return the decoded output messages."""
    out = []
    await serve(router, feed(*lines), out.append)
    return [json.loads(x) for x in out]


def echo_router():
    r = Router()

    async def echo(p):
        return p

    r.register("t.echo", echo)
    return r


async def test_output_is_one_compact_json_object_per_write():
    out = []
    await serve(echo_router(), feed(call(1, "t.echo", {"a": [1, 2]})), out.append)
    assert out == ['{"jsonrpc":"2.0","id":1,"result":{"a":[1,2]}}']


async def test_requests_may_overlap():
    r = Router()
    gate = asyncio.Event()

    async def waits(p):
        await gate.wait()
        return "waited"

    async def opens(p):
        gate.set()
        return "opened"

    r.register("t.waits", waits)
    r.register("t.opens", opens)
    # A server that handled one request at a time would never get to t.opens.
    msgs = await asyncio.wait_for(run(r, call(1, "t.waits"), call(2, "t.opens")), timeout=5)
    assert {m["id"]: m["result"] for m in msgs} == {1: "waited", 2: "opened"}


async def test_waits_for_in_flight_requests_when_the_input_ends():
    r = Router()

    async def slow(p):
        await asyncio.sleep(0.05)
        return "done"

    r.register("t.slow", slow)
    msgs = await run(r, call(1, "t.slow"))
    assert [m["result"] for m in msgs] == ["done"]


async def test_incoming_notification_runs_the_handler_and_writes_nothing():
    r = Router()
    seen = []

    async def note(p):
        seen.append(p)

    r.register("t.note", note)
    msgs = await run(r, '{"jsonrpc":"2.0","method":"t.note","params":{"a":1}}')
    assert seen == [{"a": 1}]
    assert msgs == []


async def test_failing_incoming_notification_writes_nothing():
    r = Router()

    async def boom(p):
        raise ValueError("x")

    r.register("t.boom", boom)
    msgs = await run(
        r,
        '{"jsonrpc":"2.0","method":"t.boom"}',
        '{"jsonrpc":"2.0","method":"no.such"}',
        '{"jsonrpc":"2.0","method":"t.boom","params":[1]}',
    )
    assert msgs == []


async def test_rpc_error_from_a_handler_is_sent_with_its_code_and_data():
    r = Router()

    async def busy(p):
        raise RpcError(BUSY, "busy", {"session_id": "s1"})

    r.register("t.busy", busy)
    msgs = await run(r, call(1, "t.busy"))
    assert msgs == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": 1003, "message": "busy", "data": {"session_id": "s1"}},
        }
    ]


async def test_unexpected_handler_error_is_sent_as_internal_error():
    r = Router()

    async def boom(p):
        raise ValueError("x went wrong")

    r.register("t.boom", boom)
    msgs = await run(r, call(1, "t.boom"))
    assert msgs == [
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "x went wrong"}}
    ]


async def test_params_that_are_not_an_object_get_invalid_params():
    msgs = await run(echo_router(), '{"jsonrpc":"2.0","id":1,"method":"t.echo","params":[1,2]}')
    assert msgs[0]["id"] == 1
    assert msgs[0]["error"]["code"] == -32602


async def test_malformed_line_gets_a_parse_error_with_a_null_id():
    msgs = await run(Router(), "{nope")
    assert len(msgs) == 1
    assert msgs[0]["id"] is None
    assert msgs[0]["error"]["code"] == -32700


async def test_invalid_request_echoes_the_id_when_it_can_be_read():
    msgs = await run(
        Router(),
        '{"id": 7, "method": "x"}',  # no "jsonrpc"
        '{"jsonrpc":"2.0","id":"abc","method":5}',  # method is not a string
        "[1, 2]",  # a batch: unsupported, and there is no id to read
        '{"jsonrpc":"2.0","id":[1],"method":"x"}',  # an id that is not allowed is not echoed
    )
    assert [(m["id"], m["error"]["code"]) for m in msgs] == [
        (7, -32600),
        ("abc", -32600),
        (None, -32600),
        (None, -32600),
    ]


async def test_server_keeps_answering_after_a_bad_line():
    msgs = await run(echo_router(), "{nope", call(1, "t.echo", {"a": 1}))
    assert [m["id"] for m in msgs] == [None, 1]
    assert msgs[1]["result"] == {"a": 1}


async def test_blank_lines_are_ignored():
    msgs = await run(echo_router(), "", "   ", call(1, "t.echo"), "\n", "\r\n")
    assert [m["id"] for m in msgs] == [1]


async def test_lines_may_end_in_a_newline_or_crlf():
    msgs = await run(echo_router(), call(1, "t.echo") + "\n", call(2, "t.echo") + "\r\n")
    assert sorted(m["id"] for m in msgs) == [1, 2]


async def test_incoming_responses_are_ignored():
    msgs = await run(
        echo_router(),
        '{"jsonrpc":"2.0","id":1,"result":{}}',
        '{"jsonrpc":"2.0","id":2,"error":{"code":1,"message":"x"}}',
    )
    assert msgs == []


async def test_notifications_from_another_thread_are_written():
    # The microphone callback runs on a PortAudio thread, not on the event loop's thread.
    r = Router()

    async def ping(p):
        await asyncio.to_thread(r.notify, "audio.level", {"level": 0.25})
        return "ok"

    r.register("t.ping", ping)
    msgs = await run(r, call(1, "t.ping"))
    assert {"jsonrpc": "2.0", "method": "audio.level", "params": {"level": 0.25}} in msgs


async def test_the_notifier_is_detached_when_serve_returns():
    r = echo_router()
    out = []
    await serve(r, feed(), out.append)
    r.notify("audio.level", {"level": 0.5})
    assert out == []


async def test_a_failing_write_does_not_stop_the_server():
    attempts = []

    def flaky(line):
        attempts.append(line)
        if len(attempts) == 1:
            raise BrokenPipeError

    await serve(echo_router(), feed(call(1, "t.echo"), call(2, "t.echo")), flaky)
    assert len(attempts) == 2  # both responses were attempted


async def test_cancelling_serve_cancels_the_requests_in_flight():
    r = Router()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang(p):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    r.register("t.hang", hang)

    async def endless():
        yield call(1, "t.hang")
        await asyncio.Event().wait()  # stdin stays open

    server = asyncio.create_task(serve(r, endless(), lambda line: None))
    await asyncio.wait_for(started.wait(), timeout=5)
    server.cancel()
    with pytest.raises(asyncio.CancelledError):
        await server
    await asyncio.wait_for(cancelled.wait(), timeout=5)
    r.notify("audio.level", {"level": 0.5})  # and the notifier is gone: this must not raise


# --- the line splitter behind run_stdio's stdin thread ---------------------------------------


def scripted(*chunks):
    """A read(n) that returns the given chunks one by one, then b"" (the end of the input)."""
    remaining = iter(chunks)
    return lambda n: next(remaining, b"")


_CHAR = "拉".encode()  # three bytes in UTF-8


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        ([b"a\nb\n"], ["a", "b"]),
        ([b"a\nb"], ["a", "b"]),  # the last line has no newline
        ([b"a", b"b\nc\n"], ["ab", "c"]),  # one line in two reads
        ([b"a\n", b"\n", b"b\n"], ["a", "", "b"]),  # an empty line is still a line
        ([_CHAR[:2], _CHAR[2:] + b"\n"], ["拉"]),  # a character cut between two reads
        ([b"\xff\n"], ["�"]),  # invalid UTF-8 is replaced, not raised
        ([], []),
    ],
    ids=["lines", "unterminated-last", "split-line", "empty-line", "split-character", "bad-utf8", "empty"],
)
def test_decoded_lines(chunks, expected):
    assert list(_decoded_lines(scripted(*chunks))) == expected


# --- run_stdio, in a real child process (the way the Tauri app runs the core) ----------------

_CHILD = """
import logging
from raphael_core.router import Router
from raphael_core.stdio_server import run_stdio

router = Router()

async def echo(params):
    logging.getLogger("child").warning("echo called")
    return params

async def boom(params):
    raise RuntimeError("kaboom")

async def quit_now(params):
    raise SystemExit(3)

router.register("t.echo", echo)
router.register("t.boom", boom)
router.register("t.quit", quit_now)
run_stdio(router)
"""


@pytest.fixture
def child():
    env = {
        k: v
        for k, v in os.environ.items()
        # No UTF-8 mode, no unbuffered stdio: the child must not lean on either to behave.
        if k not in {"PYTHONUTF8", "PYTHONIOENCODING", "PYTHONUNBUFFERED"}
    }
    env["PYTHONUTF8"] = "0"
    with subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    ) as proc:
        try:
            yield proc
        finally:
            if proc.poll() is None:
                proc.kill()


def read_line(proc, timeout=30):
    """One stdout line from the child; fails instead of hanging when none arrives."""
    box = queue.Queue()
    threading.Thread(target=lambda: box.put(proc.stdout.readline()), daemon=True).start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        proc.kill()
        pytest.fail(f"the stdio server wrote nothing within {timeout}s")


def test_run_stdio_answers_before_stdin_closes_and_exits_at_eof(child):
    child.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"t.echo","params":{"n":1}}\n')
    child.stdin.flush()
    assert json.loads(read_line(child)) == {"jsonrpc": "2.0", "id": 1, "result": {"n": 1}}
    child.stdin.close()
    assert child.wait(timeout=30) == 0


def test_run_stdio_exits_when_stdin_closes_without_any_input(child):
    child.stdin.close()
    assert child.wait(timeout=30) == 0


def test_run_stdio_reads_and_writes_utf8_and_plain_newlines(child):
    text = "你好，拉斐爾。こんにちは 🙂"
    request = {"jsonrpc": "2.0", "id": 1, "method": "t.echo", "params": {"text": text}}
    # A Windows text-mode writer (the Task 13 test, for one) ends its lines with CRLF.
    child.stdin.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\r\n")
    child.stdin.flush()
    line = read_line(child)
    assert line.endswith(b"\n") and not line.endswith(b"\r\n")
    assert json.loads(line)["result"] == {"text": text}
    child.stdin.close()
    assert child.wait(timeout=30) == 0


def test_run_stdio_keeps_stdout_for_json_and_sends_logs_to_stderr(child):
    child.stdin.write(
        b'{"jsonrpc":"2.0","id":1,"method":"t.echo","params":{}}\n'
        b'{"jsonrpc":"2.0","id":2,"method":"t.boom","params":{}}\n'
    )
    out, err = child.communicate(timeout=30)  # closes stdin, then collects everything
    messages = {m["id"]: m for m in map(json.loads, out.splitlines())}  # every line is JSON
    assert messages[1]["result"] == {}
    assert messages[2]["error"] == {"code": -32603, "message": "kaboom"}
    assert b"echo called" in err  # logging goes to stderr
    assert b"Traceback" in err and b"kaboom" in err  # a handler crash is logged with its traceback
    assert child.returncode == 0


def test_run_stdio_reassembles_a_line_that_arrives_in_pieces(child):
    # A first round trip, so the reader is already waiting in read() when the first piece comes.
    child.stdin.write(b'{"jsonrpc":"2.0","id":0,"method":"t.echo","params":{}}\n')
    child.stdin.flush()
    read_line(child)

    text = "拉斐爾"
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "t.echo", "params": {"text": text}}, ensure_ascii=False
    ).encode("utf-8") + b"\n"
    cut = payload.index(text.encode("utf-8")) + 1  # in the middle of the first character
    child.stdin.write(payload[:cut])
    child.stdin.flush()
    time.sleep(0.3)  # the reader takes the first piece on its own
    child.stdin.write(payload[cut:])
    child.stdin.flush()
    assert json.loads(read_line(child))["result"] == {"text": text}
    child.stdin.close()
    assert child.wait(timeout=30) == 0


def test_run_stdio_answers_a_last_line_that_has_no_newline(child):
    child.stdin.write(b'{"jsonrpc":"2.0","id":9,"method":"t.echo","params":{"k":1}}')
    out, _ = child.communicate(timeout=30)
    assert json.loads(out) == {"jsonrpc": "2.0", "id": 9, "result": {"k": 1}}
    assert child.returncode == 0


def test_run_stdio_leaves_cleanly_when_it_stops_before_stdin_closes(child):
    # Ctrl-C or a crash ends the server while the reader thread is still waiting on stdin.
    # That must not turn into "Fatal Python error ... daemon threads" and a crash exit code.
    child.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"t.quit"}\n')
    child.stdin.flush()  # and stdin stays open
    assert child.wait(timeout=30) == 3  # the SystemExit(3) of the handler
    assert b"Fatal Python error" not in child.stderr.read()


def test_run_stdio_survives_a_line_that_is_not_valid_utf8(child):
    child.stdin.write(b'\xff\xfe not json\n{"jsonrpc":"2.0","id":5,"method":"t.echo","params":{}}\n')
    out, _ = child.communicate(timeout=30)
    first, second = map(json.loads, out.splitlines())
    assert first["id"] is None and first["error"]["code"] == -32700
    assert second == {"jsonrpc": "2.0", "id": 5, "result": {}}
    assert child.returncode == 0
