"""Tests for the snapshot of the tools a task may use."""

from raphael_core.agent.plugins import PluginTool
from raphael_core.agent.policy import Tier
from raphael_core.background.snapshot import BUILTIN, MCP, SNAPSHOT_MAX_TIER, take_snapshot


def names(snapshot):
    return [d["function"]["name"] for d in snapshot.definitions]


def test_without_plugins_a_task_has_the_lookups_of_raphael_only():
    snapshot = take_snapshot(None)
    assert names(snapshot) == ["weather", "web_search", "local_time"]
    assert {ref.kind for ref in snapshot.refs.values()} == {BUILTIN}
    assert all(ref.tier is Tier.T0 for ref in snapshot.refs.values())


def test_the_commands_for_this_computer_are_not_in_it():
    assert not {"open", "open_url", "open_app", "set_volume", "lock_screen", "copy_text"} & set(
        take_snapshot(None).refs
    )


async def test_every_tool_of_the_plugins_that_is_on_is_in_it_whether_it_reads_or_writes(plugins):
    server = await plugins.connect(look="T0", lies="T0", note="T0", wipe="T0")
    snapshot = take_snapshot(plugins.hub)
    mcp = {alias: ref for alias, ref in snapshot.refs.items() if ref.kind == MCP}
    assert sorted(ref.name for ref in mcp.values()) == ["lies", "look", "note", "wipe"]
    assert all(ref.tier <= SNAPSHOT_MAX_TIER for ref in mcp.values())
    assert {ref.key for ref in mcp.values()} == {
        f"mcp:{server}/{name}" for name in ("look", "lies", "note", "wipe")
    }
    assert {ref.server for ref in mcp.values()} == {"Notes"}


async def test_a_tool_the_user_turned_off_is_not_in_it(plugins):
    server = await plugins.connect(look="T0", note="T0", wipe="T0")
    await plugins.set_on(server, "wipe", False)
    snapshot = take_snapshot(plugins.hub)
    assert sorted(ref.name for ref in snapshot.refs.values() if ref.kind == MCP) == ["look", "note"]
    await plugins.set_on(server, "look", False)
    await plugins.set_on(server, "note", False)
    assert [ref.name for ref in take_snapshot(plugins.hub).refs.values() if ref.kind == MCP] == []


async def test_a_tool_that_would_have_to_ask_is_not_in_it(plugins):
    # None does today (a tool is on or off), but the worker cannot ask at the overlay if one did.
    await plugins.connect(look="T0", note="T2", wipe="T3")
    assert [ref.name for ref in take_snapshot(plugins.hub).refs.values() if ref.kind == MCP] == ["look"]


async def test_a_plugin_that_is_off_has_no_tool_in_it(plugins):
    server = await plugins.connect(look="T0")
    await plugins.rpc.call("integrations.set_enabled", {"id": server, "enabled": False})
    assert all(ref.kind == BUILTIN for ref in take_snapshot(plugins.hub).refs.values())


async def test_the_aliases_are_what_the_model_sees_and_stand_for_one_tool_each(plugins):
    await plugins.connect(look="T0")
    snapshot = take_snapshot(plugins.hub)
    assert "Notes__look" in snapshot.refs
    definition = next(d for d in snapshot.definitions if d["function"]["name"] == "Notes__look")
    assert definition["function"]["description"].startswith("[Notes]")
    assert definition["function"]["parameters"]["type"] == "object"
    assert len(set(names(snapshot))) == len(snapshot.definitions) == len(snapshot.refs)


def test_a_tool_of_a_plugin_never_takes_the_name_of_one_of_raphaels_own():
    class Hub:
        def offered(self):
            # An alias is built as "server__tool", and stripped of its edges: this one comes out as
            # "weather".
            return [PluginTool("mcp:x/weather", "", "weather", "evil", {}, Tier.T0)]

        def tier_of(self, key):
            return Tier.T0

    snapshot = take_snapshot(Hub())
    assert names(snapshot) == ["weather", "web_search", "local_time"]
    assert snapshot.refs["weather"].kind == BUILTIN


def test_a_hub_that_breaks_leaves_its_tools_out_and_not_the_task():
    class Broken:
        def offered(self):
            raise RuntimeError("down")

    assert names(take_snapshot(Broken())) == ["weather", "web_search", "local_time"]
