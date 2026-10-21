"""Tests for the gaps a review found in the gate between a running task and the world: calls that
come together, a conversation grown too long, replies that say nothing of their cost."""

import asyncio

import pytest

from raphael_core.background.run import MAX_MESSAGES, estimate_cost
from raphael_core.protocol import RpcError

from .test_run import rig, stop_of  # noqa: F401  (rig is the fixture)
from .conftest import turn


async def test_parallel_model_calls_wait_for_each_other_so_the_cap_holds(rig):
    gate = asyncio.Event()

    async def costly(**kwargs):
        await gate.wait()
        return turn("x", cost=0.10)

    rig.client.replies = [costly] * 15
    tasks = [asyncio.ensure_future(rig.chat()) for _ in range(15)]
    await asyncio.sleep(0.05)
    gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # Two calls fit under US$0.20; the rest were refused after the second landed.
    assert rig.run.ledger.total == pytest.approx(0.20)
    assert len(rig.client.chat_calls) == 2
    refused = [r for r in results if isinstance(r, RpcError)]
    assert len(refused) == 13 and {r.data["stop"] for r in refused} == {"limit_cost"}


async def test_parallel_searches_wait_for_each_other_too(rig):
    rig.client.search_cost = 0.15
    results = await asyncio.gather(
        *[rig.tool("web_search", {"query": f"q{n}"}) for n in range(6)], return_exceptions=True
    )
    assert rig.run.ledger.total == pytest.approx(0.30)
    assert sum(isinstance(r, RpcError) for r in results) == 4


async def test_a_call_waiting_for_its_turn_is_not_cut_off_by_cancelling(rig, calls):
    await rig.plugins.connect(slow="T0", look="T0")
    rig.resnap()
    first = asyncio.ensure_future(rig.tool("Notes__slow", call_id="a"))
    await asyncio.sleep(0.1)
    waiting = asyncio.ensure_future(rig.tool("Notes__look", {"topic": "x"}, call_id="b"))
    await asyncio.sleep(0.05)
    rig.run.cancel()
    assert await stop_of(first) == ("cancelled", None)
    assert await stop_of(waiting) == ("cancelled", None)
    assert calls == ["slow"]  # the one that waited never started
    assert rig.run.call_cut_off is True


async def test_cancelling_during_a_model_call_leaves_a_waiting_plugin_call_not_cut_off(rig, calls):
    await rig.plugins.connect(look="T0")
    rig.resnap()
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(30)

    rig.client.replies = [slow]
    chat = asyncio.ensure_future(rig.chat())
    await asyncio.wait_for(started.wait(), 2)
    tool = asyncio.ensure_future(rig.tool("Notes__look", {"topic": "x"}))
    await asyncio.sleep(0.05)
    rig.run.cancel()
    assert await stop_of(chat) == ("cancelled", None)
    assert await stop_of(tool) == ("cancelled", None)
    assert rig.run.call_cut_off is False and calls == []


@pytest.mark.parametrize(
    "messages", [[{"role": "user"}] * (MAX_MESSAGES + 1), [{"content": "x" * 700_000}]]
)
async def test_a_conversation_grown_too_long_ends_the_task_as_a_limit_not_a_failure(rig, messages):
    assert await stop_of(rig.chat(messages)) == ("limit_model", None)
    assert rig.client.chat_calls == []


async def test_a_reply_without_usage_is_charged_an_estimate_not_nothing(rig):
    rig.client.replies = [turn("x", cost=0.0, usage_known=False)]
    await rig.chat()
    assert rig.run.ledger.total >= 0.02  # the floor for a model whose price is not known
    rows = rig.conn.execute("SELECT cost_usd FROM spend").fetchall()
    assert [r[0] for r in rows] == [rig.run.ledger.total]


async def test_a_reply_that_says_it_cost_nothing_costs_nothing(rig):
    rig.client.replies = [turn("x", cost=0.0)]
    await rig.chat()
    assert rig.run.ledger.total == 0.0


def test_the_estimate_uses_the_price_of_a_known_model_and_the_size_of_the_request():
    small = estimate_cost("anthropic/claude-haiku-5.5", 1_000)
    big = estimate_cost("anthropic/claude-haiku-5.5", 400_000)
    assert 0 < small < big
    assert estimate_cost("some/unknown-model", 1_000) == 0.02


async def test_a_call_that_timed_out_or_was_cancelled_is_charged_its_estimate(rig, monkeypatch):
    import raphael_core.background.run as module

    monkeypatch.setattr(module, "CHAT_TIMEOUT_S", 0.05)

    async def slow(**kwargs):
        await asyncio.sleep(30)

    rig.client.replies = [slow]
    await stop_of(rig.chat())
    assert rig.run.ledger.total >= 0.02
    before = rig.run.ledger.total
    started = asyncio.Event()

    async def slow_again(**kwargs):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(module, "CHAT_TIMEOUT_S", 60.0)
    rig.client.replies = [slow_again]
    waiting = asyncio.ensure_future(rig.chat())
    await asyncio.wait_for(started.wait(), 2)
    rig.run.cancel()
    await stop_of(waiting)
    assert rig.run.ledger.total >= before + 0.02
