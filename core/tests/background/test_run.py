"""Tests for the gate between a running task and the world: the model calls and the tool calls the
worker asks for, and the limits, the tiers and the audit they are held to."""

import asyncio
import json
import logging
from datetime import timezone

import pytest

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.ledger import Ledger
from raphael_core.agent.policy import Tier
from raphael_core.agent.tools import Tools
from raphael_core.background.run import RunLimits, TaskRun
from raphael_core.background.snapshot import take_snapshot
from raphael_core.background.tasks import TaskStore
from raphael_core.brain.openrouter import OpenRouterError
from raphael_core.brain.settings import SettingsStore
from raphael_core.brain.spend import SpendTracker
from raphael_core.protocol import INVALID_PARAMS, WORKER_STOPPED, RpcError

from .conftest import ARGUMENT, LOCAL_NOW, NOW_MS, RESULT, Clock, FakeModel, Meteo, call, turn

MESSAGES = [{"role": "system", "content": "S"}, {"role": "user", "content": "PRIVATE-PROMPT"}]


class Rig:
    def __init__(self, conn, plugins, **overrides):
        self.conn = conn
        self.plugins = plugins
        self.client = FakeModel()
        self.clock = Clock()
        self.wall = Clock(NOW_MS)
        self.meteo = Meteo()
        self.store = TaskStore(conn, self.wall)
        self.task_id = self.store.create(session_id="s", language="en", text="PRIVATE-TASK")
        self.spend = SpendTracker(conn, self.wall, timezone.utc)
        self.gate = ToolGate(plugins.hub, conn=conn, now_ms=self.wall)
        self.tools = Tools(self.client, http=self.meteo.http, now=lambda: LOCAL_NOW)
        self.changed = []
        self.settings = SettingsStore(conn)
        kwargs = {
            "task_id": self.task_id,
            "language": "en",
            "model": "fake/model",
            "limits": RunLimits(),
            "snapshot": take_snapshot(plugins.hub),
            "client": self.client,
            "gate": self.gate,
            "tools": self.tools,
            "ledger": Ledger(self.spend),
            "store": self.store,
            "spend": self.spend,
            "monthly_limit": lambda: self.settings.get("monthly_limit_usd"),
            "city": lambda: "Taipei",
            "changed": lambda: self.changed.append(True),
            "clock": self.clock,
            **overrides,
        }
        self.run = TaskRun(**kwargs)

    def resnap(self):
        self.run.snapshot = take_snapshot(self.plugins.hub)

    async def chat(self, messages=MESSAGES, **extra):
        return await self.run.chat({"task_id": self.task_id, "messages": messages, **extra})

    async def tool(self, name, arguments=None, call_id="c1"):
        return await self.run.tool(
            {"task_id": self.task_id, "name": name, "arguments": arguments or {}, "call_id": call_id}
        )

    def steps(self):
        return self.store.steps(self.task_id)

    def audit(self):
        return self.plugins.audit()


@pytest.fixture
async def rig(conn, plugins):
    rig = Rig(conn, plugins)
    yield rig
    await rig.meteo.http.aclose()


async def stop_of(awaitable):
    with pytest.raises(RpcError) as error:
        await awaitable
    assert error.value.code == WORKER_STOPPED
    return error.value.data["stop"], error.value.data["detail"]


# --- the model ------------------------------------------------------------------------------------


async def test_a_model_call_is_made_with_the_tools_of_the_snapshot_and_paid_into_the_spend(rig):
    rig.client.replies = [turn("Looking.", [call("weather", {"city": "Taipei"}, "c1")], cost=0.004)]
    reply = await rig.chat(tools=[{"type": "function", "function": {"name": "format_the_disk"}}])
    assert reply == {
        "content": "Looking.",
        "calls": [{"id": "c1", "name": "weather", "arguments": {"city": "Taipei"}, "raw": '{"city": "Taipei"}'}],
    }
    sent = rig.client.chat_calls[0]
    # The tools the worker names are not the tools the model is offered: the snapshot's are.
    assert sent["tools"] == rig.run.snapshot.definitions
    assert sent["messages"] == MESSAGES and sent["model"] == "fake/model"
    assert sent["reasoning_effort"] == "none" and sent["fallbacks"]
    rows = rig.conn.execute("SELECT purpose, model, cost_usd FROM spend").fetchall()
    assert [tuple(r) for r in rows] == [("agent", "fake/model", 0.004)]
    assert rig.run.ledger.total == 0.004
    assert rig.run.model_calls == 1


async def test_each_model_call_leaves_a_step_and_tells_the_app(rig):
    rig.client.replies = [turn("x", cost=0.002)]
    await rig.chat()
    [step] = rig.steps()
    assert (step.kind, step.outcome, step.cost_usd, step.tool) == ("model", "ok", 0.002, None)
    assert rig.store.get(rig.task_id).model_calls == 1 and rig.store.get(rig.task_id).cost_usd == 0.002
    assert rig.changed == [True]


@pytest.mark.parametrize(
    "messages",
    [None, "text", [], ["not a message"]],
)
async def test_a_conversation_that_is_not_one_is_refused_before_anything_is_paid(rig, messages):
    with pytest.raises(RpcError) as error:
        await rig.chat(messages)
    assert error.value.code == INVALID_PARAMS
    assert rig.client.chat_calls == [] and rig.run.model_calls == 0


async def test_the_fifteenth_model_call_is_the_last(rig):
    rig.client.replies = [turn("x") for _ in range(16)]
    for _ in range(15):
        await rig.chat()
    assert await stop_of(rig.chat()) == ("limit_model", None)
    assert len(rig.client.chat_calls) == 15


async def test_a_task_that_spent_what_one_task_may_asks_the_model_no_more(rig):
    rig.client.replies = [turn("x", cost=0.15), turn("x", cost=0.15), turn("x")]
    await rig.chat()
    await rig.chat()  # 0.15 is under 0.20: allowed; this one takes it to 0.30
    assert await stop_of(rig.chat()) == ("limit_cost", None)
    assert len(rig.client.chat_calls) == 2


async def test_a_month_whose_limit_is_used_up_stops_the_task(rig):
    rig.settings.set("monthly_limit_usd", 1.0)
    rig.spend.record(purpose="cleanup", model="m", cost_usd=1.0, prompt_tokens=1, completion_tokens=1)
    assert await stop_of(rig.chat()) == ("limit_month", None)
    assert rig.client.chat_calls == []


async def test_a_task_out_of_time_asks_for_nothing(rig):
    rig.clock.now += 601
    assert await stop_of(rig.chat()) == ("limit_time", None)
    rig.clock.now = 1000
    rig.client.replies = [turn("x")]
    await rig.chat()
    rig.clock.now += 601
    assert await stop_of(rig.tool("local_time")) == ("limit_time", None)


async def test_a_cancelled_task_is_answered_nothing_more(rig):
    rig.run.cancel()
    assert await stop_of(rig.chat()) == ("cancelled", None)
    assert await stop_of(rig.tool("local_time")) == ("cancelled", None)
    assert rig.client.chat_calls == []


async def test_cancelling_gives_up_the_model_call_under_way(rig):
    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(30)

    rig.client.replies = [slow]
    waiting = asyncio.ensure_future(rig.chat())
    await asyncio.wait_for(started.wait(), 2)
    rig.run.cancel()
    assert await stop_of(waiting) == ("cancelled", None)


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (OpenRouterError(401, "auth", "x"), "auth"),
        (OpenRouterError(402, "credits", "x"), "credits"),
        (OpenRouterError(0, "network", "x"), "offline"),
        (OpenRouterError(429, "rate_limit", "x"), "offline"),
        (OpenRouterError(503, "server", "x"), "offline"),
        (OpenRouterError(400, "bad_request", "x"), "error"),
        (OpenRouterError(404, "bad_request", "x"), "tools"),
        (RuntimeError("boom"), "error"),
    ],
)
async def test_a_model_that_fails_ends_the_task_with_the_kind_of_failure(rig, error, detail):
    rig.client.replies = [error]
    assert await stop_of(rig.chat()) == ("failed", detail)
    assert rig.steps()[0].outcome == "error"


async def test_a_model_that_takes_too_long_is_a_failure_to_reach_it(rig, monkeypatch):
    import raphael_core.background.run as module

    monkeypatch.setattr(module, "CHAT_TIMEOUT_S", 0.05)

    async def slow(**kwargs):
        await asyncio.sleep(30)

    rig.client.replies = [slow]
    assert await stop_of(rig.chat()) == ("failed", "offline")
    assert rig.steps()[0].outcome == "timeout"


# --- the tools of the plugins ---------------------------------------------------------------------


async def test_a_tool_of_a_plugin_is_called_through_the_gate_and_audited_with_the_task(rig, calls):
    await rig.plugins.connect(look="T0")
    rig.resnap()
    reply = await rig.tool("Notes__look", {"topic": ARGUMENT})
    assert reply == {"ok": True, "kind": None, "content": RESULT, "truncated": False}
    assert calls == ["look"]
    assert rig.audit() == [("Notes", "look", None, "auto", "ok", rig.task_id)]
    [step] = rig.steps()
    assert (step.kind, step.tool, step.server, step.tier, step.outcome) == ("tool", "look", "Notes", "T1", "ok")
    assert rig.run.tool_calls == 1


async def test_neither_the_steps_nor_the_audit_nor_the_database_hold_an_argument_or_a_result(rig):
    await rig.plugins.connect(look="T0")
    rig.resnap()
    rig.client.replies = [turn("x")]
    await rig.chat([{"role": "user", "content": "PRIVATE-PROMPT"}])
    await rig.tool("Notes__look", {"topic": ARGUMENT})
    rig.run.ledger.add(model="m", cost_usd=0.0)
    everything = json.dumps(
        {
            table: [dict(r) for r in rig.conn.execute(f"SELECT * FROM {table}")]
            for table in ("worker_steps", "tool_audit", "spend")
        }
    )
    for private in (ARGUMENT, "found", "PRIVATE-PROMPT"):
        assert private not in everything


async def test_a_tool_that_would_have_to_ask_after_the_snapshot_is_refused_and_audited(rig, calls):
    server = await rig.plugins.connect(look="T0")
    rig.resnap()  # the task started with `look` at T0
    rig.plugins.hub.strict["look"] = Tier.T2  # ... and it needs a yes now
    reply = await rig.tool("Notes__look", {"topic": "x"})
    assert (reply["ok"], reply["kind"]) == (False, "blocked")
    assert calls == []
    assert rig.audit() == [("Notes", "look", None, "none", "blocked", rig.task_id)]
    assert rig.steps()[0].outcome == "blocked" and rig.steps()[0].tier == "T2"


async def test_a_tool_the_user_blocked_after_the_snapshot_is_refused(rig, calls):
    server = await rig.plugins.connect(look="T0")
    rig.resnap()
    await rig.plugins.set_on(server, "look", False)
    reply = await rig.tool("Notes__look", {"topic": "x"})
    assert (reply["ok"], reply["kind"]) == (False, "blocked") and calls == []
    assert rig.audit() == [("Notes", "look", None, "none", "blocked", rig.task_id)]


async def test_a_plugin_turned_off_after_the_snapshot_is_refused(rig, calls):
    server = await rig.plugins.connect(look="T0")
    rig.resnap()
    await rig.plugins.rpc.call("integrations.set_enabled", {"id": server, "enabled": False})
    reply = await rig.tool("Notes__look", {"topic": "x"})
    assert reply["kind"] == "blocked" and calls == []
    assert rig.audit()[0][4:] == ("blocked", rig.task_id)


async def test_a_tool_the_server_describes_differently_after_the_snapshot_is_still_on(rig, calls):
    server = await rig.plugins.connect(look="T0")
    rig.resnap()
    from integrations import fake_server

    rig.plugins.servers.by_name["Notes"] = fake_server.build(describe="Now it does something else entirely.")
    await rig.plugins.rpc.call("integrations.refresh", {"id": server})  # the user reads the tools again
    reply = await rig.tool("Notes__look", {"topic": "x"})
    assert reply["ok"] is True and calls == ["look"]  # a tool is on until the user turns it off


async def test_a_tool_that_needs_a_yes_is_not_called_even_when_it_is_in_the_snapshot(rig, calls):
    server = await rig.plugins.connect(look="T0", note="T2")
    rig.resnap()
    # The snapshot never held `note` (phase 2 has no yes to ask for) ...
    assert "Notes__note" not in rig.run.snapshot.refs
    reply = await rig.tool("Notes__note", {"text": "x"})
    assert (reply["ok"], reply["kind"]) == (False, "blocked") and calls == []
    # ... and one that was put there by hand is stopped by the gate itself.
    from raphael_core.background.snapshot import MCP, ToolRef
    from raphael_core.agent.policy import Tier

    rig.run.snapshot.refs["Notes__note"] = ToolRef(MCP, f"mcp:{server}/note", "Notes", "note", Tier.T0)
    reply = await rig.tool("Notes__note", {"text": "x"})
    assert reply["kind"] == "blocked" and calls == []
    assert rig.audit() == [("Notes", "note", None, "none", "blocked", rig.task_id)]


async def test_a_name_that_is_not_a_tool_of_the_task_is_not_called_and_not_audited(rig, calls):
    await rig.plugins.connect(look="T0", wipe="T3")
    rig.resnap()
    for name in ("Notes__wipe", "open_url", "nothing"):
        reply = await rig.tool(name)
        assert (reply["ok"], reply["kind"]) == (False, "blocked")
    assert calls == [] and rig.audit() == []
    assert [s.outcome for s in rig.steps()] == ["blocked"] * 3 and {s.tool for s in rig.steps()} == {None}
    assert rig.run.tool_calls == 3  # even a call to nothing counts


async def test_arguments_that_are_far_too_long_are_refused_before_the_tool(rig, calls):
    await rig.plugins.connect(look="T0")
    rig.resnap()
    reply = await rig.tool("Notes__look", {"topic": "x" * 20000})
    assert reply["kind"] == "blocked" and calls == []


@pytest.mark.parametrize("params", [{"name": 1, "arguments": {}}, {"name": "x", "arguments": "no"}, {}])
async def test_a_tool_call_that_is_not_one_is_invalid(rig, params):
    with pytest.raises(RpcError) as error:
        await rig.run.tool({"task_id": rig.task_id, **params})
    assert error.value.code == INVALID_PARAMS


async def test_the_twenty_sixth_tool_call_is_not_made(rig):
    for _ in range(25):
        await rig.tool("local_time")
    assert await stop_of(rig.tool("local_time")) == ("limit_tool", None)
    assert rig.run.tool_calls == 25


async def test_what_a_tool_returns_is_cut_to_a_size_the_worker_can_take(rig):
    server = await rig.plugins.connect(big="T0")
    rig.resnap()
    reply = await rig.tool("Notes__big")
    assert reply["ok"] and reply["truncated"] is True and len(reply["content"]) <= 12_000


async def test_a_tool_that_fails_is_told_as_a_failure_with_its_kind(rig):
    await rig.plugins.connect(broken="T0")
    rig.resnap()
    reply = await rig.tool("Notes__broken")
    assert (reply["ok"], reply["kind"]) == (False, "tool_error")
    assert "secret detail" not in json.dumps(rig.steps()[0].__dict__)
    assert rig.audit()[0][4] == "error"


async def test_a_call_cut_off_by_cancelling_may_have_been_made(rig, calls):
    await rig.plugins.connect(slow="T0")
    rig.resnap()
    waiting = asyncio.ensure_future(rig.tool("Notes__slow"))
    await asyncio.sleep(0.1)
    assert calls == ["slow"]
    rig.run.cancel()
    assert await stop_of(waiting) == ("cancelled", None)
    assert rig.run.call_cut_off is True
    assert rig.audit()[0][4] == "error"  # the hub wrote it down: it was cut off


async def test_cancelling_between_calls_leaves_nothing_cut_off(rig):
    rig.run.cancel()
    assert rig.run.call_cut_off is False


# --- Raphael's own lookups ------------------------------------------------------------------------


async def test_the_weather_is_a_lookup_that_the_gate_lets_through_and_audits(rig):
    reply = await rig.tool("weather", {"city": "Taipei"})
    assert reply["ok"] and json.loads(reply["content"])["city"] == "Taipei"
    assert rig.audit() == [("Raphael", "weather", "T0", "auto", "ok", rig.task_id)]
    assert rig.steps()[0].tool == "weather" and rig.steps()[0].server == "Raphael"


async def test_the_weather_without_a_city_is_the_users_own(rig):
    await rig.tool("weather", {})
    assert any("Taipei" in str(r.url) for r in rig.meteo.requests)


async def test_a_search_is_paid_for_by_the_task(rig):
    rig.client.search_cost = 0.012
    reply = await rig.tool("web_search", {"query": "tea"})
    assert reply["ok"] and "Searched." in reply["content"]
    assert rig.run.ledger.total == 0.012
    assert rig.conn.execute("SELECT purpose, cost_usd FROM spend").fetchone()[:] == ("agent", 0.012)
    assert rig.client.text_calls[0]["model"] == "fake/model"


async def test_a_search_is_refused_once_the_task_has_spent_what_it_may(rig):
    rig.client.search_cost = 0.25
    await rig.tool("web_search", {"query": "tea"})
    assert await stop_of(rig.tool("web_search", {"query": "more tea"})) == ("limit_cost", None)
    # ... but a lookup that costs nothing is still made.
    assert (await rig.tool("local_time"))["ok"]


@pytest.mark.parametrize("arguments", [{}, {"query": ""}, {"query": 5}])
async def test_a_search_without_a_query_fails_without_asking_the_model(rig, arguments):
    reply = await rig.tool("web_search", arguments)
    assert (reply["ok"], reply["kind"]) == (False, "bad_arguments")
    assert rig.client.text_calls == []


async def test_the_time_is_the_computers(rig):
    reply = await rig.tool("local_time")
    assert reply["ok"] and json.loads(reply["content"])["date"] == "2026-10-21"


async def test_a_lookup_that_cannot_reach_its_service_fails_with_its_kind(rig):
    await rig.meteo.http.aclose()
    reply = await rig.tool("weather", {"city": "Taipei"})
    assert reply["ok"] is False and reply["kind"] in ("network", "error")
    rows = rig.conn.execute("SELECT outcome FROM tool_audit").fetchall()
    assert rows[0][0] == "error"


# --- what is logged -------------------------------------------------------------------------------


async def test_nothing_of_the_task_is_logged(rig, caplog):
    await rig.plugins.connect(look="T0", broken="T0")
    rig.resnap()
    rig.client.replies = [OpenRouterError(500, "server", "PRIVATE-DETAIL"), turn("PRIVATE-WORDS")]
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RpcError):
            await rig.chat([{"role": "user", "content": "PRIVATE-PROMPT"}])
        await rig.chat([{"role": "user", "content": "PRIVATE-PROMPT"}])
        await rig.tool("Notes__look", {"topic": ARGUMENT})
        await rig.tool("Notes__broken")
        await rig.tool("web_search", {"query": "PRIVATE-QUERY"})
        await rig.tool("weather", {"city": "PRIVATE-CITY"})
    logged = " ".join(record.getMessage() for record in caplog.records)
    for private in ("PRIVATE", ARGUMENT, "secret detail", "found"):
        assert private not in logged


async def test_a_model_call_that_runs_out_of_the_tasks_time_is_the_time_limit_whatever_the_clock_says(
    conn, plugins
):
    # asyncio fires a timer up to a clock tick early (about 16 ms on Windows), so the monotonic
    # clock may still read a hair before the deadline when the wait ends. A frozen clock is that.
    rig = Rig(conn, plugins, limits=RunLimits(seconds=0.05))

    async def hangs(**kwargs):
        await asyncio.sleep(30)

    rig.client.replies = [hangs]
    assert await stop_of(rig.chat()) == ("limit_time", None)
    await rig.meteo.http.aclose()
