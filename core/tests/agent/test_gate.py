"""Tests for the one gate every tool call goes through: the tier a tool has now, the check that a
tool at T2 or above has a yes, the call through the hub with the task that makes it, and the audit.

The hub here is a fake that notes how it is called; the audit of the real hub is tested with the
plugins (tests/integrations) and with the assistant (test_plugins.py).
"""

import pytest

from raphael_core.agent.commands import COMMANDS
from raphael_core.agent.gate import BUILTIN_SERVER, WEB_TOOLS, ToolGate
from raphael_core.agent.plugins import PluginResult
from raphael_core.agent.policy import (
    Action,
    Confirmations,
    NotApproved,
    Tier,
    UnknownTool,
)
from raphael_core.integrations.audit import AuditLog


class FakeHub:
    """A hub with the tools it is given the tiers of; it notes each call and each unrun record."""

    def __init__(self, **tiers):
        self.tiers = {f"mcp:s/{name}": Tier[tier] for name, tier in tiers.items()}
        self.calls = []
        self.pools = []
        self.unrun = []
        self.result = PluginResult(True, None, "ok")
        self.error = None

    def offered(self):
        return []

    def tier_of(self, key):
        return self.tiers.get(key)

    async def call(self, key, arguments, *, approver, task_id=None, pool=None):
        self.calls.append((key, dict(arguments), approver, task_id))
        self.pools.append(pool)
        if self.error is not None:
            raise self.error
        return self.result

    def record_unrun(self, key, *, outcome, task_id=None):
        self.unrun.append((key, outcome, task_id))


async def test_the_pool_goes_through_the_gate_to_the_hub_when_there_is_one():
    hub = FakeHub(look="T0")
    gate = ToolGate(hub)
    pool = object()
    await gate.call_plugin("mcp:s/look", {"topic": "tea"}, pool=pool)
    await gate.call_plugin("mcp:s/look", {"topic": "tea"})
    assert hub.pools == [pool, None]


def approval_for(action, tier=Tier.T2):
    confirmations = Confirmations()
    pending = confirmations.open("s", 1, action, tier, "en")
    return confirmations.resolve(pending.id, True).approval


def test_the_gate_knows_the_commands_and_the_lookups_of_raphael():
    gate = ToolGate()
    for name, command in COMMANDS.items():
        assert gate.tier(name) == command.tier
    for name in WEB_TOOLS:
        assert gate.tier(name) is Tier.T0
    with pytest.raises(UnknownTool):
        gate.tier("format_the_disk")
    assert gate.tier_now("format_the_disk") is None


def test_the_tier_of_a_plugins_tool_is_asked_of_the_hub_each_time():
    hub = FakeHub(look="T0")
    gate = ToolGate(hub)
    assert gate.tier_now("mcp:s/look") is Tier.T0
    hub.tiers["mcp:s/look"] = Tier.T3  # the user raised it
    assert gate.tier_now("mcp:s/look") is Tier.T3
    del hub.tiers["mcp:s/look"]  # ... and then blocked it
    assert gate.tier_now("mcp:s/look") is None
    with pytest.raises(UnknownTool):
        gate.tier("mcp:s/look")


def test_a_tool_at_t2_or_above_does_not_pass_without_a_yes_for_that_very_action():
    gate = ToolGate(FakeHub(note="T2", look="T0"))
    gate.check(Action("mcp:s/look", {}), None)
    gate.check(Action("lock_screen", {}), None)
    assert gate.needs_confirmation("mcp:s/note") and not gate.needs_confirmation("mcp:s/look")
    action = Action("mcp:s/note", {"text": "x"})
    with pytest.raises(NotApproved):
        gate.check(action, None)
    with pytest.raises(NotApproved):
        gate.check(action, approval_for(Action("mcp:s/note", {"text": "x"})))  # another action
    approval = approval_for(action)
    gate.check(action, approval)
    with pytest.raises(NotApproved):
        gate.check(action, approval)  # a yes lets its action run once
    with pytest.raises(UnknownTool):
        gate.check(Action("mcp:s/gone", {}), None)


async def test_a_call_at_t0_is_made_as_an_automatic_one_and_carries_the_task():
    hub = FakeHub(look="T0")
    gate = ToolGate(hub)
    result = await gate.call_plugin("mcp:s/look", {"topic": "tea"}, task_id=7)
    assert result.ok and hub.calls == [("mcp:s/look", {"topic": "tea"}, "auto", 7)]
    await gate.call_plugin("mcp:s/look", {})
    assert hub.calls[1][3] is None  # the fast tier has no task


async def test_a_call_at_t2_without_a_yes_is_made_as_a_refused_one_so_that_it_is_audited():
    hub = FakeHub(note="T2")
    gate = ToolGate(hub)
    await gate.call_plugin("mcp:s/note", {"text": "x"}, task_id=3)
    assert hub.calls == [("mcp:s/note", {"text": "x"}, "none", 3)]


async def test_a_call_with_a_yes_is_made_as_the_users_and_a_yes_is_spent_by_the_call():
    hub = FakeHub(note="T2")
    gate = ToolGate(hub)
    action = Action("mcp:s/note", {"text": "x"})
    approval = approval_for(action)
    await gate.call_plugin("mcp:s/note", {"text": "x"}, approval=approval, action=action)
    assert hub.calls[-1][2] == "user" and approval.spent
    await gate.call_plugin("mcp:s/note", {"text": "x"}, approval=approval, action=action)
    assert hub.calls[-1][2] == "none"  # the same yes cannot make a second call


async def test_a_tool_with_no_tier_now_is_called_as_a_refused_one():
    hub = FakeHub()
    gate = ToolGate(hub)
    await gate.call_plugin("mcp:s/look", {})
    assert hub.calls[0][2] == "none"


async def test_without_a_hub_a_call_is_blocked():
    result = await ToolGate().call_plugin("mcp:s/look", {})
    assert (result.ok, result.kind) == (False, "blocked")


async def test_a_hub_that_breaks_is_an_error_result_and_not_a_raise():
    hub = FakeHub(look="T0")
    hub.error = RuntimeError("down")
    result = await ToolGate(hub).call_plugin("mcp:s/look", {})
    assert (result.ok, result.kind) == (False, "error")


async def test_a_cancellation_passes_through_the_gate():
    import asyncio

    hub = FakeHub(look="T0")
    hub.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await ToolGate(hub).call_plugin("mcp:s/look", {})


def test_a_call_that_was_asked_about_and_not_made_is_written_down_with_its_task():
    hub = FakeHub(note="T2")
    ToolGate(hub).record_unrun("mcp:s/note", outcome="denied", task_id=4)
    assert hub.unrun == [("mcp:s/note", "denied", 4)]
    ToolGate().record_unrun("mcp:s/note", outcome="denied")  # no hub: nothing to write


def test_a_lookup_of_raphael_is_audited_as_raphaels_own_with_the_task(conn):
    gate = ToolGate(conn=conn, now_ms=lambda: 5)
    gate.record_builtin("weather", outcome="ok", task_id=9)
    gate.record_builtin("web_search", outcome="timeout", task_id=9)
    gate.record_builtin("local_time", outcome="blocked", task_id=None)
    rows = [
        (e.server, e.tool, e.tier, e.approver, e.outcome, e.task_id)
        for e in reversed(AuditLog(conn).recent())
    ]
    assert rows == [
        (BUILTIN_SERVER, "weather", "T0", "auto", "ok", 9),
        (BUILTIN_SERVER, "web_search", "T0", "auto", "timeout", 9),
        (BUILTIN_SERVER, "local_time", "T0", "none", "blocked", None),
    ]


def test_without_a_database_the_lookups_are_not_audited_and_nothing_breaks():
    ToolGate().record_builtin("weather", outcome="ok", task_id=1)


def test_every_command_for_this_computer_is_for_the_foreground_only():
    gate = ToolGate()
    for name in COMMANDS:
        with pytest.raises(NotApproved):
            gate.check(Action(name, {}), None, worker=True)
    for name in WEB_TOOLS:
        gate.check(Action(name, {}), None, worker=True)


def test_opening_is_refused_to_the_worker_whatever_the_tier():
    gate = ToolGate()
    assert gate.tier("open") is Tier.T1
    gate.check(Action("open", {"target_kind": "url", "target": "https://example.com/"}), None)
    with pytest.raises(NotApproved):
        gate.check(Action("open", {}), None, worker=True)
