"""Tests that a background task keeps one connection to each server it calls, and that none is left
when the task is done, stopped, timed out, or the core closes."""

import asyncio

from raphael_core.background.run import RunLimits

from .conftest import ARGUMENT, call, report, turn, until


async def three_looks_then_report(rig):
    await rig.plugins.connect(look="T0")
    rig.client.replies = [
        turn("", [call("Notes__look", {"topic": ARGUMENT}, "c1")]),
        turn("", [call("Notes__look", {"topic": "two"}, "c2")]),
        turn("", [call("Notes__look", {"topic": "three"}, "c3")]),
        report("Looked three times."),
    ]


async def test_three_calls_of_one_task_open_one_connection_and_none_is_left_when_it_is_done(
    make_rig, plugins, calls
):
    rig = make_rig()
    await three_looks_then_report(rig)
    plugins.servers.opened = 0  # connecting for the tests' own setup is not the task's
    rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "done" and calls == ["look"] * 3
    assert plugins.servers.opened == 1
    await until(lambda: plugins.servers.active == 0)


async def test_each_task_has_its_own_connections(make_rig, plugins, calls):
    rig = make_rig()
    await three_looks_then_report(rig)
    plugins.servers.opened = 0
    rig.submit()
    await rig.wait_for_reports()
    rig.client.replies = [turn("", [call("Notes__look", {"topic": "x"}, "c4")]), report("Again.")]
    rig.submit()
    await rig.wait_for_reports(2)
    assert plugins.servers.opened == 2
    await until(lambda: plugins.servers.active == 0)


async def test_a_task_the_user_stops_leaves_no_connection(make_rig, plugins, calls):
    rig = make_rig()
    await rig.plugins.connect(look="T0", slow="T0")
    rig.client.replies = [
        turn("", [call("Notes__look", {"topic": "a"}, "c1")]),
        turn("", [call("Notes__slow", {}, "c2")]),
    ]
    rig.submit()
    await until(lambda: "slow" in calls)
    assert plugins.servers.active == 1
    await rig.rpc.call("worker.cancel", {})
    [done] = await rig.wait_for_reports()
    assert done["state"] == "cancelled"
    await until(lambda: plugins.servers.active == 0)


async def test_a_task_that_runs_out_of_time_leaves_no_connection(make_rig, plugins, calls):
    rig = make_rig(limits=RunLimits(seconds=0.5))
    await rig.plugins.connect(slow="T0")
    rig.client.replies = [turn("", [call("Notes__slow", {}, "c1")])]
    rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["state"] == "limit"
    await until(lambda: plugins.servers.active == 0)


async def test_a_call_that_times_out_closes_the_connection_and_the_next_call_opens_another(
    make_rig, plugins, calls
):
    rig = make_rig()
    await rig.plugins.connect(look="T0", slow="T0")
    rig.client.replies = [
        turn("", [call("Notes__slow", {}, "c1")]),  # the hub gives a call 3 s
        turn("", [call("Notes__look", {"topic": "again"}, "c2")]),
        report("Went on."),
    ]
    plugins.servers.opened = 0
    rig.submit()
    [done] = await rig.wait_for_reports(timeout=20)
    assert done["state"] == "done"
    assert plugins.servers.opened == 2
    await until(lambda: plugins.servers.active == 0)


async def test_closing_the_core_leaves_no_connection(make_rig, plugins, calls):
    rig = make_rig()
    await rig.plugins.connect(slow="T0")
    rig.client.replies = [turn("", [call("Notes__slow", {}, "c1")])]
    rig.submit()
    await until(lambda: "slow" in calls)
    assert plugins.servers.active == 1
    await asyncio.wait_for(rig.service.aclose(), 10)
    assert plugins.servers.active == 0


async def test_the_pool_goes_from_the_gate_to_the_hub_to_the_connector(make_rig, plugins, calls):
    from raphael_core.integrations.pool import McpPool

    rig = make_rig()
    await rig.plugins.connect(look="T0")
    key = next(t.key for t in plugins.hub.offered())
    pool = McpPool()
    plugins.servers.opened = 0
    for topic in ("a", "b"):
        result = await rig.gate.call_plugin(key, {"topic": topic}, pool=pool)
        assert result.ok
    assert plugins.servers.opened == 1 and pool.open_count == 1
    await pool.aclose()
    assert plugins.servers.active == 0
