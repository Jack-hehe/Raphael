"""Tests for a task being shown the tools it needs out of many, and being able to find more."""

import json

from raphael_core.agent.ledger import Ledger
from raphael_core.agent.plugins import MAX_OFFERED, PluginTool
from raphael_core.agent.policy import Tier
from raphael_core.agent.toolpick import CHOOSE, PICK_MAX, ToolPicker
from raphael_core.background.snapshot import FIND, MCP, refine, take_snapshot
from raphael_core.brain.openrouter import ToolCall, ToolPlan
from raphael_core.brain.spend import SpendTracker
from raphael_core.worker.loop import Call, ToolOutcome, Turn, run_task

from .test_run import Rig

QUESTION = "search my notion for the notes I wrote today"


def notion_tools(count=65):
    """`count` tools of one service; search and update sort after the listing tools."""
    tools = [
        PluginTool(
            f"mcp:n/notion-a{n:03d}-list",
            "Notion",
            f"notion-a{n:03d}-list",
            "List some pages.",
            {"type": "object", "properties": {"cursor": {"type": "string"}}},
            Tier.T0,
        )
        for n in range(count - 2)
    ]
    for name, text in (
        ("notion-search", "Search the workspace for pages by words."),
        ("notion-update-page", "Change the properties or the content of a page."),
    ):
        tools.append(PluginTool(f"mcp:n/{name}", "Notion", name, text, {"type": "object"}, Tier.T0))
    return tools


class Hub:
    def __init__(self, tools):
        self.tools = tools

    def offered(self):
        return list(self.tools)


class Chooser:
    """A model that names the tools it is told to."""

    def __init__(self, *names):
        self.names = names
        self.calls = []

    async def plan_tools(self, **kwargs):
        self.calls.append(kwargs)
        return ToolPlan(
            calls=[ToolCall(CHOOSE, {"names": list(self.names)})],
            model="fake/model",
            cost_usd=0.0002,
            prompt_tokens=100,
            completion_tokens=10,
            latency_ms=1,
        )


def mcp_names(snapshot):
    return [ref.name for ref in snapshot.refs.values() if ref.kind == MCP]


def test_a_task_is_shown_the_tools_that_fit_its_words_and_can_find_the_rest():
    snapshot = take_snapshot(Hub(notion_tools()), QUESTION)
    shown = mcp_names(snapshot)
    assert "notion-search" in shown and len(shown) <= PICK_MAX + 4
    assert snapshot.refs["find_tools"].kind == FIND
    assert "find_tools" in [d["function"]["name"] for d in snapshot.definitions]


def test_a_few_tools_are_all_shown_and_there_is_nothing_to_find():
    snapshot = take_snapshot(Hub(notion_tools(MAX_OFFERED)), QUESTION)
    assert len(mcp_names(snapshot)) == MAX_OFFERED and "find_tools" not in snapshot.refs


async def test_the_model_chooses_the_tools_of_a_task_and_is_paid_for(conn):
    hub = Hub(notion_tools())
    snapshot = take_snapshot(hub, "zzz")  # words that help nothing
    chooser = Chooser("Notion__notion-a050-list")
    ledger = Ledger(SpendTracker(conn, now_ms=lambda: 1_000))
    await refine(snapshot, ToolPicker(chooser), "zzz", [], model="m", ledger=ledger)
    shown = mcp_names(snapshot)
    assert "notion-a050-list" in shown and "notion-search" in shown  # the search comes along
    assert len(shown) <= PICK_MAX + 4
    assert ledger.total == 0.0002 and "find_tools" in snapshot.refs
    names = [d["function"]["name"] for d in snapshot.definitions]
    assert len(names) == len(set(names)) and set(names) == set(snapshot.refs)


async def test_refine_asks_no_one_when_everything_fits(conn):
    snapshot = take_snapshot(Hub(notion_tools(5)), QUESTION)
    chooser = Chooser()
    ledger = Ledger(SpendTracker(conn, now_ms=lambda: 1_000))
    await refine(snapshot, ToolPicker(chooser), QUESTION, [], model="m", ledger=ledger)
    assert chooser.calls == [] and len(mcp_names(snapshot)) == 5


def test_find_shows_the_tools_that_fit_the_query_and_not_the_ones_it_has():
    snapshot = take_snapshot(Hub(notion_tools()), QUESTION)
    assert "notion-update-page" not in mcp_names(snapshot)
    added, lines = snapshot.find("update a page")
    assert [d["function"]["name"] for d in added] == ["Notion__notion-update-page"]
    assert "notion-update-page" in mcp_names(snapshot) and lines[0].startswith("Notion__notion-update")
    assert snapshot.find("update a page") == ([], [])  # nothing new the second time


async def test_the_find_tools_call_makes_the_tool_callable_and_counts_as_a_tool_call(
    conn, plugins
):
    snapshot = take_snapshot(Hub(notion_tools()), QUESTION)
    rig = Rig(conn, plugins, snapshot=snapshot)
    reply = await rig.tool("find_tools", {"query": "update page"})
    assert reply["ok"] and "Notion__notion-update-page" in reply["content"]
    assert [d["function"]["name"] for d in reply["tools"]] == ["Notion__notion-update-page"]
    assert "Notion__notion-update-page" in rig.run.snapshot.refs
    assert rig.run.tool_calls == 1
    bad = await rig.tool("find_tools", {"query": 5})
    assert not bad["ok"] and bad["kind"] == "bad_arguments"
    nothing = await rig.tool("find_tools", {"query": "qqqqq"})
    assert nothing["ok"] and "tools" not in nothing


# --- the worker's loop ---------------------------------------------------------------------

FIND_TOOLS = {"type": "function", "function": {"name": "find_tools", "parameters": {}}}
FOUND = {"type": "function", "function": {"name": "Notion__update", "parameters": {}}}


class Host:
    def __init__(self):
        self.turns = [
            Turn("", [Call("c0", "Notion__update", {}, "{}")]),
            Turn("", [Call("c1", "find_tools", {"query": "update"}, "{}")]),
            Turn("", [Call("c2", "Notion__update", {}, "{}")]),
            Turn(json.dumps({"prefix": "告", "text": "ok", "detail": ""})),
        ]
        self.seen = []
        self.made = []

    async def chat(self, messages, tools):
        self.seen.append([t["function"]["name"] for t in tools])
        return self.turns.pop(0)

    async def tool(self, call_id, name, arguments):
        self.made.append(name)
        if name == "find_tools":
            return ToolOutcome(True, content="Notion__update", tools=[FOUND])
        return ToolOutcome(True, content="done")


async def test_the_loop_allows_a_tool_the_host_made_available_and_offers_it_from_then_on():
    host = Host()
    outcome = await run_task(host, system="s", user="u", tools=[FIND_TOOLS])
    assert outcome.status == "done"
    assert host.made == ["find_tools", "Notion__update"]  # the first call, before it, was refused
    assert host.seen[0] == ["find_tools"] and host.seen[2] == ["find_tools", "Notion__update"]
