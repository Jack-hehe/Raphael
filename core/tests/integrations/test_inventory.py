"""The count of the owner's apps: one definition for the roster every model is given, the connections page
and the sidebar. An app is connected when its tools can be called now; one that waits for a sign-in, one that
failed, one that is off and a second path to an app that is counted already are listed and never counted; an
inventory that cannot be read has no number."""

import httpx
import pytest

from raphael_core.agent.team_context import snapshot
from raphael_core.connectors.composio import KEY
from raphael_core.connectors.google import scopes_for
from raphael_core.connectors.service import COMPOSIO_SERVER, META, META_EXECUTE, ConnectorsService
from raphael_core.connectors.store import Connector
from raphael_core.integrations import inventory
from raphael_core.integrations.config import ServerConfig, tool_record
from raphael_core.integrations.service import IntegrationsService
from raphael_core.router import Router

from .test_service import add, tools_of, turn_off


@pytest.fixture
def hub(conn, tmp_path, vault, connector):
    return IntegrationsService(conn, tmp_path, vault, connector=connector, call_timeout_s=3.0)


@pytest.fixture
def rpc(hub):
    router = Router()
    hub.register(router)
    return router


@pytest.fixture
def managed(tmp_path, vault, hub, rpc):
    service = ConnectorsService(tmp_path, vault, hub, transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    service.register(rpc)
    return service


def toolkit(managed, hub, vault, slug, *, status="ACTIVE", enabled=True, tools=("GO_ONE", "GO_TWO"), disabled=(),
            accounts=None):
    """A Composio toolkit the owner connected, with the one server Composio's tools are reached through."""
    vault.set(KEY, "ak_test")
    if hub._config.get(COMPOSIO_SERVER) is None:
        hub._config.add(ServerConfig(
            COMPOSIO_SERVER, "Composio", "http", url="https://app.composio.dev/mcp", managed_by="composio",
            headers=["x-api-key"], header_refs={"x-api-key": KEY}, tools_checked_at_ms=1,
            tools=[tool_record(name, "meta", {"type": "object"}) for name in sorted(META)]))
    hub._config.get(COMPOSIO_SERVER).enabled = True
    entry = Connector(f"composio:{slug}", "composio", status=status, enabled=enabled, name=slug.title(),
                      accounts=accounts or [{"id": f"ca_{slug}", "status": status}],
                      enabled_tools=[name for name in tools if name not in disabled], disabled_tools=list(disabled))
    managed.store.connectors[entry.id] = entry
    return entry


async def private_google(managed, *, gmail="read", calendar="read", granted=None):
    """The owner's own Google, signed in, with the scopes it was granted (all it asked for, unless `granted` says)."""
    entry = Connector("google", "google", status="ACTIVE", gmail=gmail, calendar=calendar,
                      granted_scopes=scopes_for(gmail, calendar) if granted is None else granted)
    managed.store.connectors["google"] = entry
    await managed.sync_google()
    return entry


async def listed(rpc):
    result = await rpc.call("integrations.inventory")
    return {app["id"]: app["state"] for app in result["apps"]}, result


class Team:
    """What `snapshot` reads of the members' service."""

    def __init__(self, hub):
        self._connections = hub.connections

    @staticmethod
    def roster():
        return []

    @staticmethod
    def primary_id():
        return 0


# --- one connection by itself ---------------------------------------------------------------------------------


@pytest.mark.parametrize("enabled,status,error,tools,waiting,state", [
    (True, "ready", None, 3, False, "ready"),
    (False, "ready", None, 3, False, "disabled"),
    (False, "error", "auth", 3, False, "disabled"),  # the owner's switch is the first word
    (True, "ready", None, 0, False, "no_tools"),
    (True, "unchecked", None, 0, False, "unchecked"),
    (True, "unchecked", None, 0, True, "needs_sign_in"),  # a sign-in under way is a sign-in that is not done
    (True, "error", "auth", 3, False, "needs_sign_in"),
    (True, "error", "missing_secret", 3, False, "needs_secret"),
    (True, "error", "timeout", 3, False, "error"),
    (True, "error", None, 3, False, "error"),
])
def test_the_state_of_one_connection_by_itself(enabled, status, error, tools, waiting, state):
    assert inventory.app_state(enabled=enabled, status=status, error=error, tools=tools, waiting=waiting) == state


def test_nothing_is_counted_over_a_connection_that_could_not_be_read():
    assert inventory.summarize([{"state": "ready"}, {"state": "needs_sign_in"}]) == ("complete", 1)
    assert inventory.summarize([{"state": "ready"}, {"state": "unknown"}]) == ("partial", None)
    assert inventory.summarize([]) == ("complete", 0)


def test_a_list_without_states_is_read_as_the_roster_always_read_it():
    # A list of something other than the hub (a test's, an older one): its `status` and `error` speak.
    read = inventory.read_inventory(lambda: [
        {"id": "a", "name": "A", "enabled": True, "status": "ready", "tools": 2},
        {"id": "b", "name": "B", "enabled": True, "status": "error", "error": "auth", "token": "never-public"}])
    assert [(app["id"], app["state"], app["tools"], app["connected"]) for app in read.apps] == [
        ("a", "ready", 2, True), ("b", "needs_sign_in", 0, False)]
    assert "never-public" not in str(read.wire())


# --- the count ------------------------------------------------------------------------------------------------


async def test_a_connected_app_is_one_whose_tools_can_be_called_now(hub, rpc, servers):
    notes = await add(rpc, servers, "Notes")
    states, result = await listed(rpc)
    assert states == {notes: "ready"} and (result["state"], result["count"]) == ("complete", 1)
    [app] = result["apps"]
    assert (app["name"], app["enabled"], app["connected"]) == ("Notes", True, True) and app["tools"] > 1
    # Every tool switched off: it is there, on, well, and nothing of it can be called.
    names = [tool["name"] for tool in await tools_of(rpc, notes)]
    await turn_off(rpc, notes, *names)
    states, result = await listed(rpc)
    assert (states, result["count"]) == ({notes: "no_tools"}, 0)
    await rpc.call("integrations.set_tool_enabled", {"id": notes, "tool": "look", "enabled": True})
    states, result = await listed(rpc)
    assert (states, result["count"], result["apps"][0]["tools"]) == ({notes: "ready"}, 1, 1)
    # Off, it is not counted; gone, it is not even listed.
    await rpc.call("integrations.set_enabled", {"id": notes, "enabled": False})
    states, result = await listed(rpc)
    assert (states, result["count"]) == ({notes: "disabled"}, 0)
    await rpc.call("integrations.disconnect", {"id": notes})
    assert await listed(rpc) == ({}, {"state": "complete", "count": 0, "apps": []})


async def test_an_app_waiting_for_a_sign_in_a_failed_one_and_one_that_is_off_are_listed_and_not_counted(
    hub, rpc, managed, vault, servers
):
    notes = await add(rpc, servers, "Notes")
    toolkit(managed, hub, vault, "github")
    toolkit(managed, hub, vault, "slack", status="INITIALIZING")  # the browser is open and nobody signed in yet
    toolkit(managed, hub, vault, "linear", status="FAILED")
    toolkit(managed, hub, vault, "asana", status="EXPIRED")
    toolkit(managed, hub, vault, "trello", enabled=False)
    toolkit(managed, hub, vault, "jira", tools=("JIRA_ONE",), disabled=("JIRA_ONE",))
    states, result = await listed(rpc)
    assert states == {
        notes: "ready", "composio:github": "ready", "composio:slack": "needs_sign_in", "composio:linear": "error",
        "composio:asana": "needs_sign_in", "composio:trello": "disabled", "composio:jira": "no_tools"}
    assert (result["state"], result["count"]) == ("complete", 2)
    assert sorted(app["id"] for app in result["apps"] if app["connected"]) == sorted([notes, "composio:github"])


async def test_a_server_whose_call_failed_drops_out_of_the_count_and_the_windows_hear_of_it(hub, rpc, servers):
    notes = await add(rpc, servers, "Notes")
    heard = []
    rpc.set_notifier(lambda method, params: heard.append(method))
    server = servers.by_name.pop("Notes")
    assert not (await hub.call(f"mcp:{notes}/look", {"topic": "x"}, approver="auto")).ok
    states, result = await listed(rpc)
    assert (states, result["count"]) == ({notes: "error"}, 0)
    assert heard == ["integrations.changed"]  # without it a sidebar that is open keeps counting it
    servers.by_name["Notes"] = server
    assert (await hub.call(f"mcp:{notes}/look", {"topic": "x"}, approver="auto")).ok
    states, result = await listed(rpc)
    assert (states, result["count"]) == ({notes: "ready"}, 1)
    assert heard == ["integrations.changed"] * 2


async def test_the_same_app_reached_two_ways_is_one_app_whichever_was_connected_first(hub, rpc, managed, vault):
    # Composio's Gmail and Calendar first, the owner's own Google after them.
    toolkit(managed, hub, vault, "gmail")
    toolkit(managed, hub, vault, "googlecalendar")
    toolkit(managed, hub, vault, "notion")
    await private_google(managed)
    states, result = await listed(rpc)
    assert states == {"composio:gmail": "duplicate", "composio:googlecalendar": "duplicate", "composio:notion": "ready",
                      "google": "ready"}
    assert (result["state"], result["count"]) == ("complete", 2)
    # The duplicate works (the gate lets its tools through); it is only not a second app.
    assert [app["tools"] > 0 for app in result["apps"] if app["state"] == "duplicate"] == [True, True]
    # Turn the owner's Google off and the toolkits are the only way to the apps: they count.
    await managed.set_tools({"id": "google", "enabled": False})
    states, result = await listed(rpc)
    assert (states["google"], states["composio:gmail"], states["composio:googlecalendar"], result["count"]) == (
        "disabled", "ready", "ready", 3)


async def test_a_grant_without_a_product_leaves_the_other_way_to_it_counted(hub, rpc, managed, vault):
    await private_google(managed, granted=[scopes_for("read", "read")[1]])  # Calendar only
    toolkit(managed, hub, vault, "gmail")
    toolkit(managed, hub, vault, "googlecalendar")
    states, result = await listed(rpc)
    assert states == {"google": "ready", "composio:gmail": "ready", "composio:googlecalendar": "duplicate"}
    assert result["count"] == 2


async def test_the_owners_own_google_is_named_google_on_the_page_and_in_the_roster(hub, rpc, managed, vault):
    # The store keeps no name for it (it is not a toolkit), and an id is not what a person reads.
    await private_google(managed)
    toolkit(managed, hub, vault, "notion")
    _, result = await listed(rpc)
    names = {app["id"]: app["name"] for app in result["apps"]}
    assert names == {"google": "Google", "composio:notion": "Notion"}
    assert {app["id"]: app["name"] for app in snapshot(Team(hub), 0, "en").apps} == names


async def test_two_accounts_of_one_app_are_one_app(hub, rpc, managed, vault):
    toolkit(managed, hub, vault, "gmail", accounts=[{"id": "ca_work", "status": "ACTIVE"}, {"id": "ca_old", "status": "EXPIRED"}])
    states, result = await listed(rpc)
    assert states == {"composio:gmail": "ready"} and result["count"] == 1
    [connector] = (await rpc.call("connectors.status", {"refresh": False}))["connectors"]
    assert [account["status"] for account in connector["accounts"]] == ["ACTIVE", "EXPIRED"]


async def test_an_inventory_that_cannot_be_read_has_no_number_and_says_so(hub, rpc, managed, vault, servers, monkeypatch):
    await add(rpc, servers, "Notes")
    toolkit(managed, hub, vault, "github")

    def locked(name):
        raise OSError("the vault is locked")
    monkeypatch.setattr(vault, "get", locked)
    # One connection cannot be read: it is listed as unknown, and the others do not make a number of it.
    states, result = await listed(rpc)
    assert states["composio:github"] == "unknown" and (result["state"], result["count"]) == ("partial", None)
    assert [app["connected"] for app in result["apps"]] == [True, False]
    snap = snapshot(Team(hub), 0, "en")
    assert (snap.inventory_state, snap.connected) == ("partial", None)
    assert snap.wire()["connected"] is None
    # Nothing at all can be read.
    monkeypatch.setattr(hub, "connections", lambda: (_ for _ in ()).throw(RuntimeError("broken")))
    assert await rpc.call("integrations.inventory") == {"state": "unknown", "count": None, "apps": []}
    assert snapshot(Team(hub), 0, "en").connected is None


async def test_the_roster_a_model_is_given_counts_what_the_sidebar_and_the_page_count(hub, rpc, managed, vault, servers):
    notes = await add(rpc, servers, "Notes")
    toolkit(managed, hub, vault, "github")
    toolkit(managed, hub, vault, "slack", status="INITIALIZING")
    await private_google(managed)
    toolkit(managed, hub, vault, "gmail")  # a second way to Gmail
    states, result = await listed(rpc)
    snap = snapshot(Team(hub), 0, "en")
    assert (snap.inventory_state, snap.connected) == (result["state"], result["count"]) == ("complete", 3)
    assert snap.apps == result["apps"] and {app["id"]: app["state"] for app in snap.apps} == states
    assert snap.wire()["connected"] == 3
    # It moves with the apps: nothing is cached anywhere that could be stale.
    await rpc.call("integrations.disconnect", {"id": notes})
    later = snapshot(Team(hub), 0, "en")
    assert later.connected == (await rpc.call("integrations.inventory"))["count"] == 2
    assert later.revision != snap.revision
    managed.store.connectors["composio:github"].enabled = False  # what `connectors.set_tools` does first, before it tells Composio
    assert snapshot(Team(hub), 0, "en").connected == (await rpc.call("integrations.inventory"))["count"] == 1


async def test_a_connectors_ready_tools_are_exactly_the_ones_the_gate_lets_through(hub, rpc, managed, vault):
    entry = toolkit(managed, hub, vault, "github", tools=("GH_ONE", "GH_TWO", "GH_THREE"), disabled=("GH_TWO",))
    toolkit(managed, hub, vault, "slack", tools=("SL_ONE",))

    def gate_allows(slug):
        return managed.allow_call(COMPOSIO_SERVER, META_EXECUTE, {"tools": [{"tool_slug": slug, "arguments": {}}]}, "user")
    assert managed.ready_tools_of(entry) == ["GH_ONE", "GH_THREE"]
    assert [slug for slug in ("GH_ONE", "GH_TWO", "GH_THREE") if gate_allows(slug)] == managed.ready_tools_of(entry)
    # Uninstalled, the gate refuses every tool of it; switched off, nothing of it is ready either.
    managed.store.connectors.pop("composio:github")
    assert not any(gate_allows(slug) for slug in ("GH_ONE", "GH_THREE")) and gate_allows("SL_ONE")
    entry.enabled = False
    assert managed.ready_tools_of(entry) == []
    # And with the one server Composio's tools are reached through off, no toolkit has a tool to call.
    hub._config.get(COMPOSIO_SERVER).enabled = False
    assert managed.ready_tools_of(managed.store.connectors["composio:slack"]) == []
