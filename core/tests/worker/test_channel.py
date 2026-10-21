"""Tests for the channel the core and its worker talk over: JSON-RPC in both directions."""

import asyncio
import json
import logging

import pytest

from raphael_core.protocol import INTERNAL_ERROR, INVALID_PARAMS, METHOD_NOT_FOUND, PARSE_ERROR, RpcError
from raphael_core.worker.channel import Peer, PeerClosed


async def lines(queue: asyncio.Queue):
    while (line := await queue.get()) is not None:
        yield line


class Wire:
    """Two peers joined by two queues, each serving what the other writes."""

    def __init__(self):
        self.to_b: asyncio.Queue = asyncio.Queue()
        self.to_a: asyncio.Queue = asyncio.Queue()
        self.written_by_a: list[str] = []
        self.a = Peer(self._a_writes)
        self.b = Peer(lambda line: self.to_a.put_nowait(line))
        self.tasks = [
            asyncio.ensure_future(self.a.serve(lines(self.to_a))),
            asyncio.ensure_future(self.b.serve(lines(self.to_b))),
        ]

    def _a_writes(self, line):
        self.written_by_a.append(line)
        self.to_b.put_nowait(line)

    async def end(self):
        self.to_a.put_nowait(None)
        self.to_b.put_nowait(None)
        await asyncio.gather(*self.tasks)


@pytest.fixture
async def wire():
    w = Wire()
    yield w
    await w.end()


async def test_a_request_is_answered_with_the_result_of_the_handler(wire):
    async def add(params):
        return {"sum": params["a"] + params["b"]}

    wire.b.register("add", add)
    assert await wire.a.request("add", {"a": 2, "b": 3}) == {"sum": 5}


async def test_a_handler_may_answer_with_an_error_that_arrives_with_its_code_and_data(wire):
    async def refuse(params):
        raise RpcError(1100, "No", {"stop": "cancelled"})

    wire.b.register("refuse", refuse)
    with pytest.raises(RpcError) as error:
        await wire.a.request("refuse", {})
    assert (error.value.code, error.value.message, error.value.data) == (1100, "No", {"stop": "cancelled"})


async def test_a_method_nobody_registered_is_not_found(wire):
    with pytest.raises(RpcError) as error:
        await wire.a.request("nothing", {})
    assert error.value.code == METHOD_NOT_FOUND


async def feed(peer: Peer, *incoming: str) -> None:
    """Have `peer` handle the lines, give the handlers a moment, and then see its input end (the
    handlers still running by then are cancelled, as when the other process is gone)."""
    queue: asyncio.Queue = asyncio.Queue()
    for line in incoming:
        queue.put_nowait(line)
    serving = asyncio.ensure_future(peer.serve(lines(queue)))
    await asyncio.sleep(0.05)
    queue.put_nowait(None)
    await asyncio.wait_for(serving, 2)


async def test_params_that_are_not_an_object_are_invalid():
    written: list[str] = []
    peer = Peer(written.append)

    async def echo(params):
        return params

    peer.register("echo", echo)
    await feed(peer, json.dumps({"jsonrpc": "2.0", "id": 99, "method": "echo", "params": [1]}))
    reply = json.loads(written[0])
    assert reply["id"] == 99 and reply["error"]["code"] == INVALID_PARAMS


async def test_a_handler_that_breaks_is_answered_without_its_message(wire, caplog):
    async def break_(params):
        raise ValueError("the secret of the task")

    wire.b.register("break", break_)
    with caplog.at_level(logging.DEBUG), pytest.raises(RpcError) as error:
        await wire.a.request("break", {})
    assert error.value.code == INTERNAL_ERROR
    assert "secret" not in error.value.message
    assert "secret" not in " ".join(r.getMessage() for r in caplog.records)
    assert "ValueError" in " ".join(r.getMessage() for r in caplog.records)  # the type only


async def test_a_notification_is_run_and_never_answered(wire):
    seen = []

    async def note(params):
        seen.append(params)

    async def boom(params):
        raise RpcError(1, "ignored")

    wire.b.register("note", note)
    wire.b.register("boom", boom)
    wire.a.notify("note", {"n": 1})
    wire.a.notify("boom", {})
    wire.a.notify("unknown", {})
    await asyncio.sleep(0.05)
    assert seen == [{"n": 1}]
    assert wire.to_a.empty()  # nothing came back, not even an error


async def test_each_side_can_ask_the_other_at_the_same_time(wire):
    async def ping(params):
        # While a's request waits, b asks a something in turn.
        inner = await wire.b.request("pong", {"n": params["n"]})
        return {"inner": inner["n"] + 1}

    async def pong(params):
        return {"n": params["n"] * 10}

    wire.b.register("ping", ping)
    wire.a.register("pong", pong)
    assert await wire.a.request("ping", {"n": 4}) == {"inner": 41}


async def test_requests_overlap_and_are_matched_to_their_answers(wire):
    async def slow(params):
        await asyncio.sleep(params["wait"])
        return {"name": params["name"]}

    wire.b.register("slow", slow)
    first = asyncio.ensure_future(wire.a.request("slow", {"wait": 0.1, "name": "first"}))
    second = asyncio.ensure_future(wire.a.request("slow", {"wait": 0.0, "name": "second"}))
    assert (await second)["name"] == "second"
    assert (await first)["name"] == "first"


async def test_a_message_of_a_megabyte_goes_through(wire):
    async def echo(params):
        return params

    wire.b.register("echo", echo)
    big = {"text": "字" * 1_000_000}
    assert await wire.a.request("echo", big) == big
    assert all(line.isascii() for line in wire.written_by_a)  # the sink never depends on a codec


async def test_the_end_of_the_conversation_fails_the_requests_that_wait():
    wire = Wire()

    async def never(params):
        await asyncio.sleep(30)

    wire.b.register("never", never)
    waiting = asyncio.ensure_future(wire.a.request("never", {}))
    await asyncio.sleep(0.02)
    wire.to_a.put_nowait(None)  # b's output ends: a hears nothing more
    with pytest.raises(PeerClosed):
        await asyncio.wait_for(waiting, 2)
    with pytest.raises(PeerClosed):
        await wire.a.request("never", {})
    assert wire.a.closed
    wire.a.notify("never", {})  # a closed conversation is ignored
    await wire.end()


async def test_the_handlers_still_running_when_the_input_ends_are_cancelled():
    wire = Wire()
    started = asyncio.Event()
    cancelled = []

    async def long(params):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    wire.b.register("long", long)
    asyncio.ensure_future(wire.a.request("long", {}))
    await asyncio.wait_for(started.wait(), 2)
    await wire.end()
    assert cancelled == [True]


async def test_a_line_that_is_not_json_is_answered_with_a_parse_error_and_the_conversation_goes_on():
    written: list[str] = []
    peer = Peer(written.append)

    async def echo(params):
        return params

    peer.register("echo", echo)
    good = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "echo", "params": {"still": "works"}})
    await feed(peer, "this is not json", good)
    replies = [json.loads(line) for line in written]
    assert replies[0]["error"]["code"] == PARSE_ERROR and replies[0]["id"] is None
    assert replies[1] == {"jsonrpc": "2.0", "id": 5, "result": {"still": "works"}}


async def test_an_answer_to_a_request_nobody_waits_for_is_ignored(wire):
    wire.to_a.put_nowait(json.dumps({"jsonrpc": "2.0", "id": 12345, "result": {}}))
    await asyncio.sleep(0.02)
    assert not wire.a.closed


async def test_a_handler_registered_twice_is_a_mistake(wire):
    async def one(params):
        return {}

    wire.a.register("one", one)
    with pytest.raises(ValueError):
        wire.a.register("one", one)


async def test_a_reply_that_is_not_json_is_still_answered(wire):
    async def odd(params):
        return {"value": {1, 2}}  # a set is not JSON

    wire.b.register("odd", odd)
    with pytest.raises(RpcError) as error:
        await wire.a.request("odd", {})
    assert error.value.code == INTERNAL_ERROR
