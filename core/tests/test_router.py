import asyncio
import logging

import pytest

from raphael_core.protocol import (
    BUSY,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    RpcError,
)
from raphael_core.router import Router


async def test_call_round_trips_through_json():
    r = Router()
    async def echo(p): return {"got": p}
    r.register("t.echo", echo)
    assert await r.call("t.echo", {"a": (1, 2)}) == {"got": {"a": [1, 2]}}   # tuple came back as list


async def test_unknown_method():
    with pytest.raises(RpcError) as e:
        await Router().call("nope.x", {})
    assert e.value.code == METHOD_NOT_FOUND


async def test_handler_exception_becomes_internal_error():
    r = Router()
    async def boom(p): raise ValueError("x")
    r.register("t.boom", boom)
    with pytest.raises(RpcError) as e:
        await r.call("t.boom", {})
    assert e.value.code == INTERNAL_ERROR


def test_register_twice_fails():
    r = Router()
    async def h(p): return None
    r.register("a.b", h)
    with pytest.raises(ValueError):
        r.register("a.b", h)


# --- beyond the brief's examples -------------------------------------------------------------


def _router_with(name, handler):
    router = Router()
    router.register(name, handler)
    return router


async def test_missing_params_reach_the_handler_as_an_empty_dict():
    async def echo(p):
        return p

    r = _router_with("t.echo", echo)
    assert await r.call("t.echo") == {}
    assert await r.call("t.echo", None) == {}


async def test_no_object_is_shared_across_the_boundary():
    shared = {"items": [1, 2]}

    async def mutate(p):
        p["items"].append(3)
        return shared

    r = _router_with("t.mutate", mutate)
    params = {"items": [1, 2]}
    result = await r.call("t.mutate", params)
    assert params == {"items": [1, 2]}  # the handler changed its own copy
    assert result == shared and result is not shared


async def test_rpc_error_from_a_handler_passes_through_unchanged():
    err = RpcError(BUSY, "busy", {"session_id": "s1"})

    async def busy(p):
        raise err

    r = _router_with("t.busy", busy)
    with pytest.raises(RpcError) as e:
        await r.call("t.busy", {})
    assert e.value is err


async def test_internal_error_keeps_the_message_and_logs_the_traceback(caplog):
    async def boom(p):
        raise ValueError("x went wrong")

    r = _router_with("t.boom", boom)
    with caplog.at_level(logging.ERROR, logger="raphael_core.router"), pytest.raises(RpcError) as e:
        await r.call("t.boom", {})
    assert e.value.message == "x went wrong"
    assert isinstance(e.value.__cause__, ValueError)
    assert any(rec.exc_info and rec.exc_info[0] is ValueError for rec in caplog.records)


async def test_internal_error_message_falls_back_to_the_exception_name():
    async def boom(p):
        raise KeyError()  # str(KeyError()) is ""

    r = _router_with("t.boom", boom)
    with pytest.raises(RpcError) as e:
        await r.call("t.boom", {})
    assert e.value.message == "KeyError"


@pytest.mark.parametrize(
    "params",
    [{"a": object()}, {"a": {1, 2}}, {"a": b"raw"}, {"a": float("nan")}, {"a": float("inf")}],
    ids=["object", "set", "bytes", "nan", "inf"],
)
async def test_params_that_are_not_json_are_invalid_params(params):
    called = []

    async def h(p):
        called.append(p)

    r = _router_with("t.h", h)
    with pytest.raises(RpcError) as e:
        await r.call("t.h", params)
    assert e.value.code == INVALID_PARAMS
    assert called == []  # the handler never ran


@pytest.mark.parametrize("params", [[1, 2], "text", 5], ids=["list", "string", "number"])
async def test_params_must_be_a_json_object(params):
    async def h(p):
        return p

    r = _router_with("t.h", h)
    with pytest.raises(RpcError) as e:
        await r.call("t.h", params)
    assert e.value.code == INVALID_PARAMS


@pytest.mark.parametrize(
    "value",
    [object(), {1, 2}, float("nan")],
    ids=["object", "set", "nan"],
)
async def test_a_result_that_is_not_json_is_an_internal_error(value):
    async def h(p):
        return value

    r = _router_with("t.h", h)
    with pytest.raises(RpcError) as e:
        await r.call("t.h", {})
    assert e.value.code == INTERNAL_ERROR


async def test_cancellation_is_not_turned_into_an_rpc_error():
    started = asyncio.Event()

    async def hang(p):
        started.set()
        await asyncio.Event().wait()

    r = _router_with("t.hang", hang)
    task = asyncio.create_task(r.call("t.hang", {}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_methods_lists_the_registered_names():
    async def h(p):
        return None

    r = Router()
    assert r.methods() == []
    r.register("b.two", h)
    r.register("a.one", h)
    assert r.methods() == ["a.one", "b.two"]


def test_notify_without_a_notifier_does_nothing():
    Router().notify("audio.level", {"level": 0.5})  # must not raise


def test_notify_hands_json_normalised_params_to_the_notifier():
    r = Router()
    seen = []
    r.set_notifier(lambda method, params: seen.append((method, params)))
    r.notify("audio.level", {"level": 0.5, "pair": (1, 2)})
    assert seen == [("audio.level", {"level": 0.5, "pair": [1, 2]})]


def test_set_notifier_none_detaches_the_notifier():
    r = Router()
    seen = []
    r.set_notifier(lambda method, params: seen.append(method))
    r.set_notifier(None)
    r.notify("audio.level", {"level": 0.5})
    assert seen == []


def test_a_failing_notifier_never_breaks_the_caller(caplog):
    def broken(method, params):
        raise BrokenPipeError

    r = Router()
    r.set_notifier(broken)
    with caplog.at_level(logging.ERROR, logger="raphael_core.router"):
        r.notify("audio.level", {"level": 0.5})  # must not raise
    assert any(rec.exc_info and rec.exc_info[0] is BrokenPipeError for rec in caplog.records)


def test_notification_params_that_are_not_json_are_dropped_not_raised(caplog):
    seen = []
    r = Router()
    r.set_notifier(lambda method, params: seen.append(method))
    with caplog.at_level(logging.ERROR, logger="raphael_core.router"):
        r.notify("audio.level", {"level": object()})  # must not raise
    assert seen == []
    assert any(rec.levelno == logging.ERROR for rec in caplog.records)
