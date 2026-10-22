"""The core's narrow way to ask the shell: a notification out, a request back."""

import asyncio

import pytest

from raphael_core.computer.bridge import BridgeError, ShellBridge
from raphael_core.protocol import RpcError
from raphael_core.router import Router


async def test_a_request_is_a_notification_answered_by_the_shells_reply():
    sent = []
    bridge = ShellBridge(lambda method, params: sent.append((method, params)))
    asking = asyncio.ensure_future(bridge.request("look", {"window": 7}, 5))
    await asyncio.sleep(0)
    (method, params), = sent
    assert method == "computer.request"
    assert params["op"] == "look" and params["args"] == {"window": 7}
    assert len(params["id"]) == 32
    assert await bridge._reply({"id": params["id"], "ok": True, "result": {"window": 7}}) == {}
    assert await asking == {"window": 7}
    assert bridge.waiting == 0


async def test_a_failure_of_the_shell_is_its_kind_and_an_unknown_kind_is_failed():
    sent = []
    bridge = ShellBridge(lambda method, params: sent.append(params))
    for error, kind in [("password", "password"), ("elevated", "elevated"), ("<b>x</b>", "failed"), (3, "failed")]:
        asking = asyncio.ensure_future(bridge.request("act", {}, 5))
        await asyncio.sleep(0)
        await bridge._reply({"id": sent[-1]["id"], "ok": False, "error": error})
        with pytest.raises(BridgeError) as failed:
            await asking
        assert failed.value.kind == kind


async def test_no_shell_or_no_answer_in_time_is_unavailable():
    with pytest.raises(BridgeError) as failed:
        await ShellBridge(None).request("apps", {}, 5)
    assert failed.value.kind == "unavailable"
    bridge = ShellBridge(lambda method, params: None)
    with pytest.raises(BridgeError) as failed:
        await bridge.request("apps", {}, 0.01)
    assert failed.value.kind == "unavailable"
    assert bridge.waiting == 0


async def test_a_reply_nobody_waits_for_changes_nothing_and_a_bad_one_is_refused():
    sent = []
    bridge = ShellBridge(lambda method, params: sent.append(params))
    assert await bridge._reply({"id": "0" * 32, "ok": True}) == {}
    asking = asyncio.ensure_future(bridge.request("apps", {}, 5))
    await asyncio.sleep(0)
    request_id = sent[-1]["id"]
    await bridge._reply({"id": request_id, "ok": True, "result": "not an object"})
    assert await asking == {}
    # answered twice: the second is late
    assert await bridge._reply({"id": request_id, "ok": False, "error": "gone"}) == {}
    for bad in ({"id": "x", "ok": True}, {"id": request_id, "ok": "yes"}, {"ok": True}):
        with pytest.raises(RpcError):
            await bridge._reply(bad)


async def test_the_state_of_a_run_is_a_notification_nobody_answers():
    sent = []
    bridge = ShellBridge(lambda method, params: sent.append((method, params)))
    bridge.state("step", "《告。操作中：點擊「七」》")
    bridge.state("nonsense", "x")
    assert sent == [("computer.state", {"phase": "step", "line": "《告。操作中：點擊「七」》", "run": None})]

    def broken(method, params):
        raise RuntimeError("the pipe is gone")

    ShellBridge(broken).state("end")  # never raises


async def test_a_request_and_a_state_say_the_run_they_are_for():
    sent = []
    bridge = ShellBridge(lambda method, params: sent.append((method, params)))
    run = "ab" * 16
    asking = asyncio.ensure_future(bridge.request("act", {"window": 7}, 5, run=run))
    await asyncio.sleep(0)
    bridge.state("end", None, run=run)
    (_, request), (_, state) = sent
    assert request["run"] == run and request["args"] == {"window": 7}
    assert state == {"phase": "end", "line": None, "run": run}
    await bridge._reply({"id": request["id"], "ok": False, "error": "stopped"})
    with pytest.raises(BridgeError) as failed:
        await asking
    assert failed.value.kind == "stopped"
    # the shell's re-check of the live focus is a kind of its own
    assert BridgeError("handoff").kind == "handoff"


async def test_the_shell_reaches_the_reply_and_the_interrupt_through_the_router():
    router = Router()
    bridge = ShellBridge(lambda method, params: None)
    bridge.register(router)
    assert {"computer.reply", "computer.interrupt"} <= set(router.methods())
    stopped = []
    bridge.interrupted = lambda: stopped.append(True)
    assert await router.call("computer.interrupt", {}) == {}
    assert stopped == [True]
