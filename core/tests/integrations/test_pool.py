"""Tests for the pool of MCP connections: one connection for the calls of one job, closed when the
job ends, is cancelled, times out or the pool is closed. Against servers in the test, and a real
stdio server for the tests that count processes."""

import asyncio
import ctypes
import sys
from pathlib import Path

import pytest

from raphael_core.integrations.client import McpConnector, McpFailure
from raphael_core.integrations.config import ServerConfig
from raphael_core.integrations.pool import McpPool


FAKE = str(Path(__file__).with_name("fake_server.py"))


def server_config(name="Notes", ident="abc123") -> ServerConfig:
    return ServerConfig(id=ident, name=name, transport="stdio", command="unused")


async def until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting for a condition"
        await asyncio.sleep(0.01)


@pytest.fixture
def notes(servers):
    servers.add("Notes")
    return server_config()


@pytest.fixture
async def pool():
    made = []

    def make(**kwargs):
        made.append(McpPool(**kwargs))
        return made[-1]

    yield make
    for each in made:
        await each.aclose()


# --- one connection for many calls -----------------------------------------------------------------


async def test_three_calls_open_one_connection_and_read_the_tools_once(connector, servers, notes, pool):
    mine = pool()
    for topic in ("a", "b", "c"):
        outcome = await connector.call_tool(notes, "look", {"topic": topic}, pool=mine)
        assert outcome.text == f"found {topic}"
    assert len(servers.opened) == 1 and servers.active == 1
    assert mine.open_count == 1
    await mine.aclose()
    assert servers.active == 0 and mine.open_count == 0


async def test_without_a_pool_every_call_opens_its_own_connection(connector, servers, notes):
    for topic in ("a", "b"):
        await connector.call_tool(notes, "look", {"topic": topic})
    assert len(servers.opened) == 2 and servers.active == 0


async def test_calls_at_the_same_time_share_the_connection(connector, servers, notes, pool):
    mine = pool()
    outcomes = await asyncio.gather(
        *(connector.call_tool(notes, "look", {"topic": str(n)}, pool=mine) for n in range(4))
    )
    assert sorted(o.text for o in outcomes) == [f"found {n}" for n in range(4)]
    assert len(servers.opened) == 1


async def test_each_server_has_its_own_connection(connector, servers, pool):
    servers.add("Notes")
    servers.add("Other")
    mine = pool()
    await connector.call_tool(server_config("Notes", "n1"), "look", {"topic": "a"}, pool=mine)
    await connector.call_tool(server_config("Other", "o1"), "look", {"topic": "a"}, pool=mine)
    await connector.call_tool(server_config("Notes", "n1"), "look", {"topic": "b"}, pool=mine)
    assert len(servers.opened) == 2 and mine.open_count == 2


async def test_a_tool_the_server_does_not_have_is_gone_and_the_connection_stays(
    connector, servers, notes, pool
):
    mine = pool()
    with pytest.raises(McpFailure) as failure:
        await connector.call_tool(notes, "vanished", {}, pool=mine)
    assert failure.value.kind == "gone"
    await connector.call_tool(notes, "look", {"topic": "a"}, pool=mine)
    assert len(servers.opened) == 1


async def test_the_list_cached_when_the_connection_opened_is_never_given_back_as_fresh(
    connector, servers, notes, pool
):
    mine = pool()
    outcome = await connector.call_tool(notes, "look", {"topic": "a"}, expected="old", pool=mine)
    assert outcome.fresh is None  # the caller would save it over what a refresh found


async def test_a_tool_that_says_it_failed_is_an_outcome_and_the_connection_stays(
    connector, servers, notes, pool
):
    mine = pool()
    outcome = await connector.call_tool(notes, "broken", {}, pool=mine)
    assert outcome.is_error
    await connector.call_tool(notes, "look", {"topic": "a"}, pool=mine)
    assert len(servers.opened) == 1


# --- ending a call ---------------------------------------------------------------------------------


async def test_a_cancelled_call_closes_the_connection_and_the_next_call_opens_another(
    connector, servers, notes, pool, calls
):
    mine = pool()
    await connector.call_tool(notes, "look", {"topic": "a"}, pool=mine)
    running = asyncio.ensure_future(connector.call_tool(notes, "slow", {}, timeout_s=30, pool=mine))
    await until(lambda: "slow" in calls)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert servers.active == 0 and mine.open_count == 0  # the whole connection is closed
    outcome = await connector.call_tool(notes, "look", {"topic": "b"}, pool=mine)
    assert outcome.text == "found b" and len(servers.opened) == 2


async def test_a_call_that_times_out_closes_the_connection_and_the_next_call_opens_another(
    connector, servers, notes, pool
):
    mine = pool()
    with pytest.raises(McpFailure) as failure:
        await connector.call_tool(notes, "slow", {}, timeout_s=0.3, pool=mine)
    assert failure.value.kind == "timeout"
    assert servers.active == 0 and mine.open_count == 0
    await connector.call_tool(notes, "look", {"topic": "b"}, pool=mine)
    assert len(servers.opened) == 2


async def test_closing_the_pool_ends_a_call_that_is_under_way(connector, servers, notes, pool, calls):
    mine = pool()
    running = asyncio.ensure_future(connector.call_tool(notes, "slow", {}, timeout_s=30, pool=mine))
    await until(lambda: "slow" in calls)
    await mine.aclose()
    with pytest.raises(McpFailure):
        await running
    assert servers.active == 0 and mine.open_count == 0


async def test_closing_the_pool_gives_the_call_under_way_a_failure_at_once(
    connector, servers, notes, pool, calls
):
    mine = pool()
    running = asyncio.ensure_future(connector.call_tool(notes, "slow", {}, timeout_s=60, pool=mine))
    await until(lambda: "slow" in calls)
    began = asyncio.get_running_loop().time()
    await mine.aclose()
    with pytest.raises(McpFailure) as failure:
        await running
    assert failure.value.kind == "gone"
    assert asyncio.get_running_loop().time() - began < 5


async def test_a_pool_closed_in_the_same_turn_as_the_first_call_fails_it_at_once(
    connector, servers, notes, pool
):
    mine = pool()
    began = asyncio.get_running_loop().time()
    call = asyncio.ensure_future(connector.call_tool(notes, "look", {"topic": "a"}, timeout_s=30, pool=mine))
    await asyncio.sleep(0)  # the call has made its connection, whose owner has not run yet
    closing = asyncio.ensure_future(mine.aclose())
    with pytest.raises(McpFailure):
        await call
    await closing
    assert asyncio.get_running_loop().time() - began < 5 and servers.active == 0


async def test_calls_at_the_same_time_are_made_at_the_same_time(connector, servers, notes, pool):
    mine = pool()
    began = asyncio.get_running_loop().time()
    outcomes = await asyncio.gather(
        *(connector.call_tool(notes, "nap", {"seconds": 1.0}, timeout_s=10, pool=mine) for _ in range(3))
    )
    assert [o.text for o in outcomes] == ["rested"] * 3
    assert asyncio.get_running_loop().time() - began < 2.5  # not three seconds one after the other
    assert len(servers.opened) == 1


async def test_a_call_that_waited_for_no_one_does_not_time_out_for_the_sake_of_another(
    connector, servers, notes, pool
):
    mine = pool()
    outcomes = await asyncio.gather(
        *(connector.call_tool(notes, "nap", {"seconds": 0.7}, timeout_s=1.2, pool=mine) for _ in range(3))
    )
    assert all(o.text == "rested" for o in outcomes)


async def test_cancelling_one_call_closes_the_connection_and_fails_the_others_under_way(
    connector, servers, notes, pool, calls
):
    mine = pool()
    slow = asyncio.ensure_future(connector.call_tool(notes, "slow", {}, timeout_s=30, pool=mine))
    other = asyncio.ensure_future(connector.call_tool(notes, "nap", {"seconds": 5}, timeout_s=30, pool=mine))
    await until(lambda: "slow" in calls and "nap" in calls)
    slow.cancel()
    with pytest.raises(asyncio.CancelledError):
        await slow
    with pytest.raises(McpFailure) as failure:
        await other
    assert failure.value.kind == "gone"
    assert servers.active == 0


async def test_a_login_or_a_removal_closes_the_connections_kept_for_the_server(
    connector, servers, notes, pool
):
    mine, theirs = pool(), pool(idle_s=120)
    for each in (mine, theirs):
        await connector.call_tool(notes, "look", {"topic": "a"}, pool=each)
    assert servers.active == 2
    connector.invalidate(notes.id)
    await until(lambda: servers.active == 0)
    assert mine.open_count == 0 and theirs.open_count == 0
    await connector.call_tool(notes, "look", {"topic": "b"}, pool=mine)  # the next call opens a new one
    assert len(servers.opened) == 3


async def test_a_closed_pool_makes_no_new_connection(connector, servers, notes, pool):
    mine = pool()
    await mine.aclose()
    with pytest.raises(McpFailure) as failure:
        await connector.call_tool(notes, "look", {"topic": "a"}, pool=mine)
    assert failure.value.kind == "gone" and servers.opened == []
    await mine.aclose()  # safe twice


async def test_a_connection_that_cannot_be_opened_is_a_failure_and_the_next_call_tries_again(
    vault, notes, pool
):
    attempts = []

    def refuses(server, secrets):
        attempts.append(1)
        raise FileNotFoundError("no such program")

    mine = pool()
    connector = McpConnector(vault, refuses)
    for _ in range(2):
        with pytest.raises(McpFailure) as failure:
            await connector.call_tool(notes, "look", {"topic": "a"}, pool=mine)
        assert failure.value.kind == "not_found"
    assert len(attempts) == 2 and mine.open_count == 0


async def test_a_connection_left_alone_is_closed_after_the_idle_time(connector, servers, notes, pool):
    mine = pool(idle_s=0.3)
    await connector.call_tool(notes, "look", {"topic": "a"}, pool=mine)
    assert servers.active == 1
    await until(lambda: servers.active == 0, timeout=5)
    assert mine.open_count == 0
    await connector.call_tool(notes, "look", {"topic": "b"}, pool=mine)
    assert len(servers.opened) == 2


async def test_a_call_keeps_the_connection_from_going_idle(connector, servers, notes, pool):
    mine = pool(idle_s=0.4)
    for _ in range(4):
        await connector.call_tool(notes, "look", {"topic": "a"}, pool=mine)
        await asyncio.sleep(0.2)
    assert len(servers.opened) == 1


# --- a real server process --------------------------------------------------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="counts processes with Win32")
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x102


def running(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def real(vault) -> tuple[McpConnector, ServerConfig]:
    config = ServerConfig(
        id="abc123", name="Real", transport="stdio", command=sys.executable, args=[FAKE]
    )
    return McpConnector(vault), config


@windows_only
async def test_three_calls_start_one_process_and_closing_the_pool_ends_it(vault, pool):
    connector, config = real(vault)
    mine = pool()
    pids = {
        (await connector.call_tool(config, "pid", {}, timeout_s=30, pool=mine)).text for _ in range(3)
    }
    assert len(pids) == 1
    pid = int(pids.pop())
    assert running(pid)
    await mine.aclose()
    await until(lambda: not running(pid), timeout=10)


@windows_only
async def test_a_cancelled_call_ends_the_process_and_the_next_call_starts_another(vault, pool, calls):
    connector, config = real(vault)
    mine = pool()
    first = int((await connector.call_tool(config, "pid", {}, timeout_s=30, pool=mine)).text)
    slow = asyncio.ensure_future(connector.call_tool(config, "slow", {}, timeout_s=60, pool=mine))
    await asyncio.sleep(1.0)  # the call is on its way to the server
    slow.cancel()
    with pytest.raises(asyncio.CancelledError):
        await slow
    await until(lambda: not running(first), timeout=10)
    second = int((await connector.call_tool(config, "pid", {}, timeout_s=30, pool=mine)).text)
    assert second != first and running(second)
    await mine.aclose()
    await until(lambda: not running(second), timeout=10)


@windows_only
async def test_a_call_that_times_out_ends_the_process(vault, pool):
    connector, config = real(vault)
    mine = pool()
    pid = int((await connector.call_tool(config, "pid", {}, timeout_s=30, pool=mine)).text)
    with pytest.raises(McpFailure) as failure:
        await connector.call_tool(config, "slow", {}, timeout_s=1.0, pool=mine)
    assert failure.value.kind == "timeout"
    await until(lambda: not running(pid), timeout=10)
