"""Tests for the worker process's side of the conversation with the core, in one process: a `Worker`
on one end of a wire and a peer that plays the core on the other."""

import asyncio
import json
import os

import pytest

from raphael_core.protocol import BUSY, INVALID_PARAMS, WORKER_STOPPED, RpcError
from raphael_core.worker.runner import Worker

from .test_channel import Wire

TOOLS = [
    {
        "type": "function",
        "function": {"name": "Notes__look", "description": "Look.", "parameters": {"type": "object"}},
    }
]


@pytest.fixture
async def core():
    """The core's end of a wire whose other end is a Worker. Its host.chat answers from `chats`,
    its host.tool from `tools`; the requests it gets are kept."""
    wire = Wire()
    Worker(wire.b)
    wire.chats = []
    wire.tools = []
    wire.chat_requests = []
    wire.tool_requests = []

    async def chat(params):
        wire.chat_requests.append(params)
        answer = wire.chats.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            answer = await answer()
        return answer

    async def tool(params):
        wire.tool_requests.append(params)
        answer = wire.tools.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            answer = await answer()
        return answer

    wire.a.register("host.chat", chat)
    wire.a.register("host.tool", tool)
    yield wire
    await wire.end()


def spec(**overrides):
    return {"task_id": 7, "system": "SYSTEM", "user": "USER", "tools": TOOLS, **overrides}


def final(text="Done."):
    return {"content": json.dumps({"prefix": "告", "text": text, "detail": ""}), "calls": []}


def calling(name="Notes__look", arguments=None, id="c1"):
    arguments = {"topic": "tea"} if arguments is None else arguments
    return {
        "content": "",
        "calls": [{"id": id, "name": name, "arguments": arguments, "raw": json.dumps(arguments)}],
    }


async def test_the_worker_says_hello_with_the_protocol_version_and_its_process_id(core):
    hello = await core.a.request("worker.hello", {})
    assert hello == {"protocol": 1, "pid": os.getpid()}


async def test_a_task_is_carried_out_by_asking_the_core_for_every_model_reply_and_tool_result(core):
    core.chats = [calling(), final("Tea is green.")]
    core.tools = [{"ok": True, "kind": None, "content": "green", "truncated": False}]
    result = await core.a.request("task.run", spec())
    assert result["status"] == "done" and result["kind"] is None
    assert json.loads(result["final"])["text"] == "Tea is green."
    assert (result["model_calls"], result["tool_calls"]) == (2, 1)
    # Every request carries the task, the first model call the two messages and the tools.
    assert [r["task_id"] for r in core.chat_requests + core.tool_requests] == [7, 7, 7]
    first = core.chat_requests[0]
    assert [m["role"] for m in first["messages"]] == ["system", "user"]
    assert first["tools"] == TOOLS
    assert core.tool_requests[0] == {
        "task_id": 7,
        "call_id": "c1",
        "name": "Notes__look",
        "arguments": {"topic": "tea"},
    }
    sent_back = core.chat_requests[1]["messages"][-1]
    assert sent_back["role"] == "tool" and "green" in sent_back["content"]


async def test_a_refusal_of_the_core_ends_the_task_with_what_it_said(core):
    core.chats = [RpcError(WORKER_STOPPED, "no", {"stop": "limit_cost"})]
    result = await core.a.request("task.run", spec())
    assert (result["status"], result["kind"]) == ("stopped", "limit_cost")
    core.chats = [RpcError(WORKER_STOPPED, "no", {"stop": "failed", "detail": "offline"})]
    result = await core.a.request("task.run", spec())
    assert (result["kind"], result["detail"]) == ("failed", "offline")


async def test_a_refusal_of_a_tool_call_ends_the_task_too(core):
    core.chats = [calling()]
    core.tools = [RpcError(WORKER_STOPPED, "no", {"stop": "cancelled"})]
    result = await core.a.request("task.run", spec())
    assert result["status"] == "cancelled"


async def test_an_error_of_the_core_that_is_not_a_refusal_fails_the_run_instead_of_hanging(core):
    core.chats = [RpcError(-32603, "the core broke")]
    with pytest.raises(RpcError):
        await asyncio.wait_for(core.a.request("task.run", spec()), 2)
    # ... and the worker takes the next task.
    core.chats = [final()]
    assert (await core.a.request("task.run", spec()))["status"] == "done"


@pytest.mark.parametrize(
    "bad",
    [
        {"task_id": "7"},
        {"task_id": True},
        {"system": 1},
        {"user": None},
        {"tools": "none"},
        {"tools": [{"type": "function"}]},
        {"tools": [1]},
    ],
)
async def test_a_task_that_is_not_well_formed_is_refused(core, bad):
    with pytest.raises(RpcError) as error:
        await core.a.request("task.run", spec(**bad))
    assert error.value.code == INVALID_PARAMS


async def test_one_task_at_a_time(core):
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        started.set()
        await release.wait()
        return final()

    core.chats = [hold]
    running = asyncio.ensure_future(core.a.request("task.run", spec()))
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(RpcError) as error:
        await core.a.request("task.run", spec(task_id=8))
    assert error.value.code == BUSY
    release.set()
    assert (await running)["status"] == "done"


async def test_cancel_stops_the_task_that_runs_and_only_that_one(core):
    started = asyncio.Event()

    async def hold():
        started.set()
        await asyncio.sleep(30)

    core.chats = [hold]
    running = asyncio.ensure_future(core.a.request("task.run", spec()))
    await asyncio.wait_for(started.wait(), 2)
    core.a.notify("task.cancel", {"task_id": 999})  # another task: nothing happens
    await asyncio.sleep(0.05)
    assert not running.done()
    core.a.notify("task.cancel", {"task_id": 7})
    result = await asyncio.wait_for(running, 2)
    assert result["status"] == "cancelled"


async def test_a_cancel_with_no_task_running_does_nothing_and_does_not_stop_the_next(core):
    core.a.notify("task.cancel", {"task_id": 7})
    await asyncio.sleep(0.02)
    core.chats = [final()]
    assert (await core.a.request("task.run", spec()))["status"] == "done"


async def test_the_limits_of_the_core_are_the_limits_of_the_loop(core):
    core.chats = [calling(arguments={"topic": str(n)}, id=f"c{n}") for n in range(5)]
    core.tools = [{"ok": True, "content": "r"}] * 5
    result = await core.a.request("task.run", spec(limits={"model_calls": 2, "tool_calls": 9}))
    assert (result["status"], result["kind"]) == ("stopped", "limit_model")


@pytest.mark.parametrize("limits", ["fast", None, {"model_calls": -1, "seconds": "x"}, {"tool_calls": True}])
async def test_limits_that_make_no_sense_are_the_defaults(core, limits):
    core.chats = [final()]
    result = await core.a.request("task.run", spec(limits=limits))
    assert result["status"] == "done"
