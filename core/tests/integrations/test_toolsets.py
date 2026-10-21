"""Tests for the toolsets: the groups of the 「連接」 tab and the tier of a whole group."""

import json

import pytest

from raphael_core.agent.commands import COMMANDS
from raphael_core.integrations.service import IntegrationsService
from raphael_core.integrations.toolsets import BUILTIN_SETS, ToolSet, resolve
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router

from .test_service import SECRET, add, stdio


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


def tiers_of(group):
    return {tool["name"]: tool["tier"] for tool in group["tools"]}


# --- the structure ----------------------------------------------------------------------------


def test_resolve_flattens_includes_once_and_survives_a_cycle():
    sets = {
        "a": ToolSet("a", "A", "", tools=("x", "y"), includes=("b",)),
        "b": ToolSet("b", "B", "", tools=("y", "z"), includes=("a",)),
    }
    assert resolve("a", sets) == ["x", "y", "z"]
    assert resolve("nothing", sets) == []


async def test_the_built_in_groups_are_fixed_and_come_first(rpc):
    groups = (await rpc.call("toolsets.list"))["groups"]
    assert [g["id"] for g in groups] == ["builtin:web", "builtin:computer"]
    web, computer = groups
    assert tiers_of(web) == {"weather": "T0", "web_search": "T0", "local_time": "T0"}
    assert set(tiers_of(computer)) == set(COMMANDS)
    assert tiers_of(computer)["lock_screen"] == "T1"
    for group in groups:
        assert (group["kind"], group["enabled"], group["switchable"], group["tier_fixed"]) == (
            "builtin",
            True,
            False,
            True,
        )
    assert set(BUILTIN_SETS) == {"web", "computer"}


async def test_a_server_is_a_group_after_the_built_in_ones(rpc, servers):
    server_id = await add(rpc, servers)
    groups = (await rpc.call("toolsets.list"))["groups"]
    assert [g["id"] for g in groups][-1] == server_id
    group = by_id(groups)[server_id]
    assert (group["kind"], group["name"], group["switchable"], group["tier_fixed"]) == (
        "mcp",
        "Notes",
        True,
        False,
    )
    assert group["status"] == "ready"
    assert {tool["name"] for tool in group["tools"]} >= {"look", "note", "wipe"}
    # Nothing has a tier until the user gives one; the server's own claim is only a hint.
    assert all(tool["tier"] is None for tool in group["tools"])
    look = next(tool for tool in group["tools"] if tool["name"] == "look")
    assert look["suggested"] == "T0"
    assert group["env"] == ["API_KEY"]
    assert SECRET not in json.dumps(groups)


# --- a tier for the whole group ---------------------------------------------------------------


async def test_a_group_tier_sets_the_tools_there_are_and_not_the_ones_to_come(
    rpc, service, servers
):
    server_id = await add(rpc, servers)
    done = await rpc.call("integrations.set_group_tier", {"id": server_id, "tier": "T1"})
    assert set(tiers_of(done["group"]).values()) == {"T1"}
    assert done["group"]["allowed_count"] == done["group"]["tool_count"]

    # The server grows a tool later: it has no tier, so it is blocked.
    from raphael_core.integrations.config import tool_record

    server = service._config.get(server_id)
    server.tools.append(tool_record("fresh", "A new one.", {"type": "object"}))
    group = by_id((await rpc.call("toolsets.list"))["groups"])[server_id]
    assert tiers_of(group)["fresh"] is None
    assert {n for n in tiers_of(group) if tiers_of(group)[n] is not None} == {
        t.name for t in server.tools if t.name != "fresh"
    }
    assert service.tier_of(f"mcp:{server_id}/fresh") is None


async def test_a_group_tier_can_be_taken_back_and_is_checked(rpc, servers):
    server_id = await add(rpc, servers)
    await rpc.call("integrations.set_group_tier", {"id": server_id, "tier": "T2"})
    cleared = await rpc.call("integrations.set_group_tier", {"id": server_id, "tier": None})
    assert set(tiers_of(cleared["group"]).values()) == {None}
    assert all(tool["decided"] for tool in cleared["group"]["tools"])
    for params in ({"id": server_id, "tier": "T9"}, {"id": "nope", "tier": "T0"}, {"tier": "T0"}):
        with pytest.raises(RpcError) as failed:
            await rpc.call("integrations.set_group_tier", params)
        assert failed.value.code == INVALID_PARAMS


async def test_a_built_in_group_cannot_be_given_a_tier(rpc):
    with pytest.raises(RpcError) as failed:
        await rpc.call("integrations.set_group_tier", {"id": "builtin:web", "tier": "T3"})
    assert failed.value.code == INVALID_PARAMS


# --- the whole way: add, read the tools, give tiers, list -------------------------------------


async def test_add_refresh_tiers_and_list_end_to_end(rpc, servers, vault):
    servers.add("Notes")
    added = await rpc.call("integrations.add", stdio("Notes"))
    server_id = added["server"]["id"]
    listed = by_id((await rpc.call("toolsets.list"))["groups"])[server_id]
    assert (listed["status"], listed["tool_count"]) == ("unchecked", 0)

    refreshed = await rpc.call("integrations.refresh", {"id": server_id})
    assert refreshed["server"]["status"] == "ready"

    await rpc.call("integrations.set_group_tier", {"id": server_id, "tier": "T0"})
    await rpc.call("integrations.set_tier", {"id": server_id, "tool": "wipe", "tier": "T3"})
    await rpc.call("integrations.set_tier", {"id": server_id, "tool": "big", "tier": None})
    await rpc.call("integrations.set_enabled", {"id": server_id, "enabled": False})

    group = by_id((await rpc.call("toolsets.list"))["groups"])[server_id]
    tiers = tiers_of(group)
    assert (tiers["look"], tiers["wipe"], tiers["big"]) == ("T0", "T3", None)
    assert (group["status"], group["enabled"]) == ("disabled", False)
    assert SECRET not in json.dumps(await rpc.call("toolsets.list"))


# --- "never decided" is not "blocked on purpose" ----------------------------------------------


def decided_of(group):
    return {tool["name"]: tool["decided"] for tool in group["tools"]}


async def test_a_new_tool_is_undecided_until_the_user_gives_it_a_tier_or_blocks_it(rpc, servers):
    server_id = await add(rpc, servers)
    group = by_id((await rpc.call("toolsets.list"))["groups"])[server_id]
    assert set(decided_of(group).values()) == {False}
    assert group["pending_count"] == group["tool_count"] > 0

    await rpc.call("integrations.set_tier", {"id": server_id, "tool": "look", "tier": "T0"})
    await rpc.call("integrations.set_tier", {"id": server_id, "tool": "wipe", "tier": None})
    group = by_id((await rpc.call("toolsets.list"))["groups"])[server_id]
    assert (decided_of(group)["look"], decided_of(group)["wipe"]) == (True, True)
    assert tiers_of(group)["wipe"] is None  # blocked on purpose: no tier, never offered
    assert group["pending_count"] == group["tool_count"] - 2
    assert group["allowed_count"] == 1
    assert not any(tool["changed"] for tool in group["tools"])


async def test_a_block_on_purpose_is_never_offered_or_run(rpc, service, servers):
    server_id = await add(rpc, servers)
    await rpc.call("integrations.set_group_tier", {"id": server_id, "tier": "T1"})
    await rpc.call("integrations.set_tier", {"id": server_id, "tool": "wipe", "tier": None})
    assert service.tier_of(f"mcp:{server_id}/wipe") is None
    assert service.tier_of(f"mcp:{server_id}/look") is not None


async def test_a_group_tier_can_be_limited_to_named_tools(rpc, servers):
    server_id = await add(rpc, servers)
    done = await rpc.call(
        "integrations.set_group_tier", {"id": server_id, "tier": "T2", "tools": ["note", "ghost"]}
    )
    assert tiers_of(done["group"])["note"] == "T2"
    assert [n for n, t in tiers_of(done["group"]).items() if t is not None] == ["note"]
    assert decided_of(done["group"])["look"] is False
    for tools in ("note", [1], {"note": 1}):
        with pytest.raises(RpcError) as failed:
            await rpc.call(
                "integrations.set_group_tier", {"id": server_id, "tier": "T0", "tools": tools}
            )
        assert failed.value.code == INVALID_PARAMS


async def test_a_group_block_decides_the_tools_it_names(rpc, servers):
    server_id = await add(rpc, servers)
    done = await rpc.call(
        "integrations.set_group_tier", {"id": server_id, "tier": None, "tools": ["wipe"]}
    )
    assert decided_of(done["group"])["wipe"] is True
    assert tiers_of(done["group"])["wipe"] is None
    assert decided_of(done["group"])["look"] is False


async def test_a_tool_that_changed_is_not_decided_and_is_flagged(rpc, service, servers):
    from raphael_core.integrations.config import tool_record

    server_id = await add(rpc, servers)
    await rpc.call("integrations.set_tier", {"id": server_id, "tool": "look", "tier": "T0"})
    server = service._config.get(server_id)
    old = server.tool("look")
    server.tools[server.tools.index(old)] = tool_record(
        "look", "Now it does more.", old.input_schema
    )
    group = by_id((await rpc.call("toolsets.list"))["groups"])[server_id]
    look = next(tool for tool in group["tools"] if tool["name"] == "look")
    assert (look["tier"], look["decided"], look["changed"]) == (None, False, True)


async def test_the_built_in_tools_are_always_decided(rpc):
    groups = (await rpc.call("toolsets.list"))["groups"]
    assert all(tool["decided"] for group in groups for tool in group["tools"])


async def test_a_block_survives_a_restart_and_an_old_file_still_loads(rpc, servers, tmp_path):
    from raphael_core.integrations.config import ConfigStore

    server_id = await add(rpc, servers)
    await rpc.call("integrations.set_tier", {"id": server_id, "tool": "wipe", "tier": None})
    await rpc.call("integrations.set_tier", {"id": server_id, "tool": "look", "tier": "T0"})

    reloaded = ConfigStore(tmp_path).get(server_id)
    wipe, look = reloaded.tool("wipe"), reloaded.tool("look")
    assert reloaded.decided(wipe) and reloaded.granted_tier(wipe) is None
    assert reloaded.granted_tier(look) == "T0"

    # A file from before: its grants hold tiers only, and the tools it does not name are undecided.
    path = tmp_path / "integrations.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    grants = data["servers"][0]["grants"]
    data["servers"][0]["grants"] = {k: v for k, v in grants.items() if v["tier"] != "block"}
    path.write_text(json.dumps(data), encoding="utf-8")
    old = ConfigStore(tmp_path).get(server_id)
    assert old.granted_tier(old.tool("look")) == "T0"
    assert not old.decided(old.tool("wipe"))
