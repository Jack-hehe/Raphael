"""Tests for the 「連接」 tab's side of the plugins: how tools are grouped, the store, and the login
that adds a service from it (against the fake service of `fake_oauth`)."""

import asyncio
import json

import pytest

from raphael_core.integrations import catalog
from raphael_core.integrations.client import McpConnector
from raphael_core.integrations.config import ConfigStore, tool_record
from raphael_core.integrations.service import IntegrationsService
from raphael_core.integrations.toolsets import tool_kind
from raphael_core.integrations.vault import MemoryVault
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router

from .fake_oauth import FakeService
from .test_service import SECRET, add


@pytest.fixture
def service(conn, tmp_path, vault, connector):
    return IntegrationsService(conn, tmp_path, vault, connector=connector, call_timeout_s=3.0)


@pytest.fixture
def rpc(service):
    router = Router()
    service.register(router)
    return router


def by_id(groups):
    return {group["id"]: group for group in groups}


# --- read or write ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hints", "name", "kind"),
    [
        ((True, None), "anything", "read"),  # the server says it only reads
        ((False, None), "get_page", "write"),  # ... says it does not
        ((True, True), "get_page", "write"),  # a destructive tool is a write whatever else it says
        ((None, True), "get_page", "write"),
        ((None, False), "get_page", "read"),  # no word on reading: the name is asked
        ((None, None), "get_page", "read"),
        ((None, None), "search", "read"),
        ((None, None), "listProjects", "read"),
        ((None, None), "list-issues", "read"),
        ((None, None), "notion.fetch", "write"),  # only the first word counts: "notion" is no verb
        ((None, None), "create_page", "write"),
        ((None, None), "delete_page", "write"),
        ((None, None), "frobnicate", "write"),  # unknown: counted with the ones to look at twice
        ((None, None), "Get_Thing", "read"),
    ],
)
def test_tools_are_grouped_by_their_hints_and_then_their_names(hints, name, kind):
    tool = tool_record(name, "", {}, hints[0], hints[1])
    assert tool_kind(tool) == kind


# --- the groups --------------------------------------------------------------------------------


async def test_the_groups_are_the_plugins_only(rpc, servers):
    assert (await rpc.call("toolsets.list"))["groups"] == []  # Raphael's own tools are not shown
    server_id = await add(rpc, servers)
    groups = (await rpc.call("toolsets.list"))["groups"]
    assert [g["id"] for g in groups] == [server_id]
    group = groups[0]
    assert (group["name"], group["status"], group["enabled"], group["auth"]) == ("Notes", "ready", True, "none")
    assert {tool["name"] for tool in group["tools"]} >= {"look", "note", "wipe"}
    assert all(tool["enabled"] for tool in group["tools"])  # on, with nothing to decide
    assert {tool["kind"] for tool in group["tools"]} == {"read", "write"}
    assert group["enabled_count"] == group["tool_count"]
    assert group["env"] == ["API_KEY"] and SECRET not in json.dumps(groups)


async def test_a_file_with_the_old_tiers_is_read_as_on_but_for_a_block(conn, tmp_path, vault, connector, servers):
    servers.add("Notes")
    tools = [tool_record(n, f"The {n}.", {"type": "object"}) for n in ("look", "note", "wipe", "fresh")]
    old = {
        "version": 1,
        "servers": [
            {
                "id": "s1",
                "name": "Notes",
                "transport": "stdio",
                "command": "npx",
                "tools": [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools],
                "tools_checked_at_ms": 1,
                "grants": {
                    "look": {"tier": "T0", "fingerprint": tools[0].fingerprint},
                    "note": {"tier": "T3", "fingerprint": tools[1].fingerprint},
                    "wipe": {"tier": "block", "fingerprint": tools[2].fingerprint},
                },
            }
        ],
    }
    (tmp_path / "integrations.json").write_text(json.dumps(old), encoding="utf-8")
    service = IntegrationsService(conn, tmp_path, vault, connector=connector)
    router = Router()
    service.register(router)
    [group] = (await router.call("toolsets.list"))["groups"]
    assert {t["name"]: t["enabled"] for t in group["tools"]} == {
        "look": True,
        "note": True,  # any tier counts as on
        "wipe": False,  # blocked on purpose
        "fresh": True,  # never decided: on, like every tool
    }
    assert service.tier_of("mcp:s1/look") is not None and service.tier_of("mcp:s1/wipe") is None


# --- the store ---------------------------------------------------------------------------------


async def test_the_store_lists_every_service_and_which_are_added(rpc):
    listed = (await rpc.call("integrations.catalog"))["services"]
    assert [s["id"] for s in listed] == [s.id for s in catalog.SERVICES]
    assert all(s["server_id"] is None for s in listed)
    notion = by_id(listed)["notion"]
    assert set(notion["examples"]) == {"zh-TW", "en", "ja"} and notion["url"].startswith("https://")


# --- a login from the store ---------------------------------------------------------------------


@pytest.fixture
async def fake():
    fake = FakeService()
    await fake.start()
    yield fake
    await fake.close()


@pytest.fixture
def store(conn, tmp_path, fake, monkeypatch):
    """A service that signs in to the fake service whatever the store entry says."""
    vault = MemoryVault()
    monkeypatch.setattr(
        catalog,
        "_BY_ID",
        {**catalog._BY_ID, "notion": catalog.Service(**{**catalog._BY_ID["notion"].__dict__, "url": fake.url})},
    )
    service = IntegrationsService(
        conn,
        tmp_path,
        vault,
        connector=McpConnector(vault),
        open_browser=fake.browser,
        login_timeout_s=10,
    )
    router = Router()
    service.register(router)
    service.vault = vault
    service.router = router
    return service


async def test_a_service_of_the_store_is_added_with_every_tool_on(store, fake):
    done = await store.router.call("integrations.connect", {"catalog_id": "notion"})
    assert done["connected"] is True
    server = done["server"]
    assert (server["name"], server["auth"], server["catalog_id"], server["status"]) == ("Notion", "oauth", "notion", "ready")
    assert server["enabled_count"] == server["tool_count"] == 3 and server["enabled"] is True
    assert (server["write_count"], server["read_count"]) == (2, 1)
    assert {t["name"]: t["enabled"] for t in done["tools"]} == {"search": True, "create_page": True, "delete_page": True}
    # it is a plugin like the others, and its tools are offered to the assistant
    assert {t.name for t in store.offered()} == {"search", "create_page", "delete_page"}
    assert [s["server_id"] for s in (await store.router.call("integrations.catalog"))["services"] if s["id"] == "notion"] == [
        server["id"]
    ]


async def test_what_the_login_gave_is_in_the_vault_and_nowhere_else(store, fake, tmp_path, conn):
    await store.router.call("integrations.connect", {"catalog_id": "notion"})
    kept = "".join(store.vault.get(name) or "" for name in store.vault.names())
    assert "at-" in kept and "rt-" in kept  # they are in the vault
    everything = (tmp_path / "integrations.json").read_text(encoding="utf-8")
    everything += json.dumps([dict(r) for r in conn.execute("SELECT * FROM tool_audit")])
    everything += json.dumps(await store.router.call("toolsets.list")) + json.dumps(await store.router.call("integrations.list"))
    for secret in (*fake.access, *fake.refresh):
        assert secret not in everything
    assert "client-1" not in everything


async def test_a_call_to_a_tool_of_the_store_goes_through_the_gate(store, fake):
    done = await store.router.call("integrations.connect", {"catalog_id": "notion"})
    result = await store.call(f"mcp:{done['server']['id']}/search", {"q": "x"}, approver="auto")
    assert (result.ok, result.text) == (True, "did search")


async def test_a_service_cannot_be_added_twice_or_unknown(store):
    await store.router.call("integrations.connect", {"catalog_id": "notion"})
    for params in ({"catalog_id": "notion"}, {"catalog_id": "nope"}, {}):
        with pytest.raises(RpcError) as refused:
            await store.router.call("integrations.connect", params)
        assert refused.value.code == INVALID_PARAMS


async def test_a_login_the_user_refuses_adds_nothing(store, fake):
    fake.deny = True
    done = await store.router.call("integrations.connect", {"catalog_id": "notion"})
    assert done == {"connected": False, "error": "denied"}
    assert (await store.router.call("integrations.list"))["servers"] == []
    assert store.vault.names() == []


async def test_a_login_that_is_not_finished_in_time_adds_nothing(store, fake):
    async def nobody(url):
        return None

    store._open_browser = nobody
    store._login_timeout_s = 0.3
    done = await store.router.call("integrations.connect", {"catalog_id": "notion"})
    assert done == {"connected": False, "error": "timeout"}
    assert (await store.router.call("integrations.list"))["servers"] == [] and store.vault.names() == []


async def test_a_login_can_be_cancelled(store, fake):
    opened = asyncio.Event()

    async def wait_for_ever(url):
        opened.set()

    store._open_browser = wait_for_ever
    store._login_timeout_s = 30
    login = asyncio.create_task(store.router.call("integrations.connect", {"catalog_id": "notion"}))
    await asyncio.wait_for(opened.wait(), 10)
    assert await store.router.call("integrations.connect_cancel") == {"cancelled": True}
    assert await asyncio.wait_for(login, 10) == {"connected": False, "error": "cancelled"}
    assert (await store.router.call("integrations.list"))["servers"] == [] and store.vault.names() == []
    assert await store.router.call("integrations.connect_cancel") == {"cancelled": False}  # nothing left to cancel


async def test_only_one_login_runs_at_a_time(store, fake):
    opened = asyncio.Event()

    async def wait_for_ever(url):
        opened.set()

    store._open_browser = wait_for_ever
    store._login_timeout_s = 30
    login = asyncio.create_task(store.router.call("integrations.connect", {"catalog_id": "notion"}))
    await asyncio.wait_for(opened.wait(), 10)
    with pytest.raises(RpcError) as refused:
        await store.router.call("integrations.connect", {"catalog_id": "notion"})
    assert refused.value.code == INVALID_PARAMS
    await store.router.call("integrations.connect_cancel")
    await asyncio.wait_for(login, 10)


async def test_a_service_whose_login_ran_out_asks_for_a_new_one_and_keeps_its_settings(store, fake):
    done = await store.router.call("integrations.connect", {"catalog_id": "notion"})
    server_id = done["server"]["id"]
    await store.router.call("integrations.set_tool_enabled", {"id": server_id, "tool": "delete_page", "enabled": False})
    fake.expire_access_tokens()
    fake.refresh.clear()
    refreshed = await store.router.call("integrations.refresh", {"id": server_id})
    assert (refreshed["server"]["status"], refreshed["server"]["error"]) == ("error", "auth")
    assert fake.authorizations == 1  # the refresh did not open the browser

    again = await store.router.call("integrations.connect", {"id": server_id})
    assert again["connected"] is True and again["server"]["id"] == server_id
    assert (again["server"]["status"], again["server"]["error"]) == ("ready", None)
    tools = {t["name"]: t["enabled"] for t in again["tools"]}
    assert tools == {"search": True, "create_page": True, "delete_page": False}  # the user's choice stays
    assert len((await store.router.call("integrations.list"))["servers"]) == 1


async def test_a_new_login_that_fails_leaves_the_service_asking_for_a_login(store, fake):
    done = await store.router.call("integrations.connect", {"catalog_id": "notion"})
    server_id = done["server"]["id"]
    fake.deny = True
    failed = await store.router.call("integrations.connect", {"id": server_id})
    assert failed == {"connected": False, "error": "denied"}
    [server] = (await store.router.call("integrations.list"))["servers"]
    assert (server["status"], server["error"]) == ("error", "auth")


async def test_disconnecting_revokes_the_login_and_forgets_everything(store, fake):
    done = await store.router.call("integrations.connect", {"catalog_id": "notion"})
    server_id = done["server"]["id"]
    tokens = [*fake.access, *fake.refresh]
    gone = await store.router.call("integrations.disconnect", {"id": server_id})
    assert gone == {"removed": True, "revoked": True}
    assert set(fake.revoked) >= set(tokens)
    assert store.vault.names() == []
    assert (await store.router.call("integrations.list"))["servers"] == []
    assert ConfigStore(store._config._path.parent).servers() == []
    assert store.tier_of(f"mcp:{server_id}/search") is None


async def test_disconnecting_works_when_the_server_will_not_revoke(store, fake):
    done = await store.router.call("integrations.connect", {"catalog_id": "notion"})
    await fake.close()
    gone = await store.router.call("integrations.disconnect", {"id": done["server"]["id"]})
    assert gone == {"removed": True, "revoked": False}
    assert store.vault.names() == []


async def test_a_name_a_custom_plugin_has_does_not_stop_the_store(store, servers):
    await store.router.call(
        "integrations.add",
        {"name": "Notion", "transport": "http", "url": "https://example.com/mcp"},
    )
    done = await store.router.call("integrations.connect", {"catalog_id": "notion"})
    assert done["server"]["name"] == "Notion 2"


async def test_a_login_for_a_server_that_has_none_is_refused(store, servers):
    added = await store.router.call(
        "integrations.add", {"name": "Plain", "transport": "http", "url": "https://example.com/mcp"}
    )
    with pytest.raises(RpcError) as refused:
        await store.router.call("integrations.connect", {"id": added["server"]["id"]})
    assert refused.value.code == INVALID_PARAMS


# --- a service that needs no login ---------------------------------------------------------------


def public_entry(monkeypatch):
    entry = catalog.Service(
        **{**catalog._BY_ID["notion"].__dict__, "id": "openbook", "name": "Openbook", "auth": "none"}
    )
    monkeypatch.setattr(catalog, "_BY_ID", {**catalog._BY_ID, "openbook": entry})


async def test_a_public_service_is_added_at_once_with_no_browser(rpc, servers, monkeypatch, vault):
    public_entry(monkeypatch)
    servers.add("Openbook")
    done = await rpc.call("integrations.connect", {"catalog_id": "openbook"})
    assert done["connected"] is True
    server = done["server"]
    assert (server["name"], server["auth"], server["catalog_id"], server["status"]) == (
        "Openbook",
        "none",
        "openbook",
        "ready",
    )
    assert server["tool_count"] > 0 and server["enabled_count"] == server["tool_count"]
    assert vault.names() == []  # nothing to keep: there is no login
    with pytest.raises(RpcError) as refused:  # added already
        await rpc.call("integrations.connect", {"catalog_id": "openbook"})
    assert refused.value.code == INVALID_PARAMS


async def test_a_public_service_that_cannot_be_reached_adds_nothing(rpc, servers, monkeypatch):
    public_entry(monkeypatch)
    done = await rpc.call("integrations.connect", {"catalog_id": "openbook"})  # no server of that name
    assert done["connected"] is False
    assert (await rpc.call("integrations.list"))["servers"] == []
