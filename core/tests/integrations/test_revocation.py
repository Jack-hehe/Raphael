"""A tool the owner switched off, or an app they uninstalled, is never offered and never called again, whatever
else the owner decided before: their 一律允許 (a narrow Always rule) waives a tool's questions and nothing more.
The gate and the offers are one answer (`tier_of`, `offered`), so a model that still remembers the tool, an old
session of a CLI, or a task that began before the change is refused at the same door as one that is asked now."""

import httpx
import pytest

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.plugins import tool_key
from raphael_core.agent.policy import Action
from raphael_core.agent.safety import SafetyGuard, SafetyJudge, SafetyRules
from raphael_core.brain.settings import SettingsStore
from raphael_core.connectors.service import COMPOSIO_SERVER, META_EXECUTE, ConnectorsService
from raphael_core.integrations.client import CallOutcome
from raphael_core.integrations.service import IntegrationsService
from raphael_core.router import Router

from test_connectors import Platform, no_wait, setup_service

from .test_inventory import toolkit
from .test_service import add


class NoJudge:
    """The judge of the owner's own words is never asked: Always is a narrow rule, which needs no judge."""


def owner_gate(hub, conn, *, ask_first=True):
    """The one gate with the owner's rules, and (as their 先詢問 switch makes it) every action asked about."""
    if ask_first:
        SettingsStore(conn).set("action_mode", "ask")
    return ToolGate(hub, conn=conn, safety=SafetyGuard(SafetyRules(conn), SafetyJudge(NoJudge())))


def offered(hub):
    return {tool.key for tool in hub.offered()}


async def test_a_tool_switched_off_or_an_uninstalled_server_is_not_offered_or_called_after_always(
    conn, tmp_path, vault, connector, servers, calls
):
    hub = IntegrationsService(conn, tmp_path, vault, connector=connector, call_timeout_s=3.0)
    rpc = Router()
    hub.register(rpc)
    notes = await add(rpc, servers, "Notes")
    gate = owner_gate(hub, conn)
    note, look = f"mcp:{notes}/note", f"mcp:{notes}/look"
    # Asked about first; then the owner says Always to that one tool.
    assert (await gate.call_plugin(note, {"text": "a"})).kind == "blocked" and calls == []
    assert gate.always_allow(note, "Notes", "note", "en")
    assert (await gate.call_plugin(note, {"text": "b"})).ok and calls == ["note"]
    assert {note, look} <= offered(hub)
    # The owner switches the tool off: it is no longer offered, has no tier, and Always waives nothing of it.
    await rpc.call("integrations.set_tool_enabled", {"id": notes, "tool": "note", "enabled": False})
    assert note not in offered(hub) and look in offered(hub)
    assert gate.tier_now(note) is None and not gate.waived(Action(note, {"text": "c"}))
    refused = await gate.call_plugin(note, {"text": "c"})
    assert (refused.ok, refused.kind) == (False, "blocked") and calls == ["note"]
    # Not through the hub either, with any approver, as a model with an old list of tools would call it.
    for approver in ("auto", "user"):
        assert (await hub.call(note, {"text": "d"}, approver=approver)).kind == "blocked"
    assert calls == ["note"]
    # The owner uninstalls the server: nothing of it is offered, and every tool of it is refused.
    await rpc.call("integrations.set_tool_enabled", {"id": notes, "tool": "note", "enabled": True})
    assert note in offered(hub)
    await rpc.call("integrations.disconnect", {"id": notes})
    assert offered(hub) == set() and gate.tier_now(look) is None
    for key in (note, look):
        assert (await gate.call_plugin(key, {"text": "e", "topic": "e"})).kind == "blocked"
    assert calls == ["note"]
    # The rule is still the owner's (it waives nothing for a tool that is not there; Settings lists it to be deleted).
    assert [rule.scope for rule in gate.safety.rules.rules() if rule.scope] == [f"tool:{note}"]


class Recorder:
    """The boundary after the gate and the hub: it records what reaches a Composio server."""

    def __init__(self):
        self.calls = []

    async def call_tool(self, server, name, arguments, *, expected=None, **kwargs):
        self.calls.append([call["tool_slug"] for call in arguments["tools"]])
        return CallOutcome("ok", False)

    def invalidate(self, *args):
        pass


async def test_a_toolkits_tool_switched_off_or_uninstalled_is_refused_even_with_always_on_the_tool_that_runs_it(
    conn, tmp_path, vault, connector
):
    hub = IntegrationsService(conn, tmp_path, vault, connector=connector)
    managed = ConnectorsService(tmp_path, vault, hub, transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    toolkit(managed, hub, vault, "gmail", tools=("GM_ONE", "GM_TWO"))
    toolkit(managed, hub, vault, "slack", tools=("SL_ONE",))
    hub._connector = recorder = Recorder()
    gate = owner_gate(hub, conn)
    run = tool_key(COMPOSIO_SERVER, META_EXECUTE)
    assert gate.always_allow(run, "Composio", META_EXECUTE, "en")

    async def execute(slug):
        return await gate.call_plugin(run, {"tools": [{"tool_slug": slug, "arguments": {}}]})
    assert (await execute("GM_ONE")).ok and (await execute("SL_ONE")).ok
    # One tool of Gmail switched off (what connectors.set_tools does first): only that one is refused.
    gmail = managed.store.connectors["composio:gmail"]
    gmail.disabled_tools, gmail.enabled_tools = ["GM_ONE"], ["GM_TWO"]
    assert (await execute("GM_ONE")).kind == "blocked" and (await execute("GM_TWO")).ok
    assert recorder.calls == [["GM_ONE"], ["SL_ONE"], ["GM_TWO"]]
    # Gmail uninstalled: all of it is refused, and the other toolkit still works through the same tool.
    managed.store.connectors.pop("composio:gmail")
    assert [(await execute(slug)).kind for slug in ("GM_ONE", "GM_TWO")] == ["blocked", "blocked"]
    assert (await execute("SL_ONE")).ok and run in offered(hub)
    # The last toolkit uninstalled: Composio's tools are not offered at all, and Always cannot call one.
    managed.store.connectors.pop("composio:slack")
    assert offered(hub) == set() and gate.tier_now(run) is None and not gate.waived(Action(run, {}))
    assert (await execute("SL_ONE")).kind == "blocked"
    assert recorder.calls == [["GM_ONE"], ["SL_ONE"], ["GM_TWO"], ["SL_ONE"]]


async def test_a_connected_toolkit_through_the_real_connector_calls_the_provider_only_while_it_is_on(conn, tmp_path):
    fake = Platform()

    async def browser(url):
        pass
    router, managed, hub, vault = setup_service(tmp_path, conn, fake, open_browser=browser, sleep=no_wait)
    await router.call("connectors.set_key", {"key": "ak_private"})
    await router.call("connectors.connect", {"toolkit": "gmail"})
    gate = owner_gate(hub, conn)
    run = tool_key(COMPOSIO_SERVER, META_EXECUTE)
    assert gate.always_allow(run, "Composio", META_EXECUTE, "en")
    args = {"tools": [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {}}]}
    assert (await gate.call_plugin(run, args)).ok
    await router.call("connectors.set_tools", {"id": "composio:gmail", "disabled_tools": ["GMAIL_FETCH_EMAILS"]})
    reached = len(fake.requests)
    assert (await gate.call_plugin(run, args)).kind == "blocked"
    assert len(fake.requests) == reached  # nothing was sent to Composio
    await router.call("connectors.set_tools", {"id": "composio:gmail", "disabled_tools": []})
    assert (await gate.call_plugin(run, args)).ok
    await router.call("connectors.disconnect", {"id": "composio:gmail"})
    reached = len(fake.requests)
    assert offered(hub) == set() and gate.tier_now(run) is None
    assert (await gate.call_plugin(run, args)).kind == "blocked"
    assert len(fake.requests) == reached
    inventory = await router.call("integrations.inventory")
    assert (inventory["state"], inventory["count"], inventory["apps"]) == ("complete", 0, [])
