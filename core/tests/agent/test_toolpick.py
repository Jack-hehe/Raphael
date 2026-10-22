"""Tests for choosing which of many tools a request is shown, and for cleaning a call's arguments.

Nothing here spends money: the model is the fake OpenRouter of the agent tests."""

import pytest

from raphael_core.agent.ledger import Ledger
from raphael_core.agent.plugins import (
    MAX_OFFERED,
    PluginResult,
    PluginTool,
    clean_arguments,
    make_offer,
)
from raphael_core.agent.policy import Tier
from raphael_core.agent.toolpick import CHOOSE, PICK_MAX, ToolPicker
from raphael_core.brain.openrouter import OpenRouterError
from raphael_core.brain.spend import SpendTracker

from .conftest import tool_plan
from .test_plugins import route, tools_offered
from .test_service import make_rig, spoken  # noqa: F401  (make_rig is a fixture)

QUESTION = "can you look at my notion and search what notes I made today"


def notion_tools(count=65, late="notion-search"):
    """`count` tools of one service; the one the request needs sorts after every other."""
    tools = [
        PluginTool(
            f"mcp:n/notion-a{n:03d}-list",
            "Notion",
            f"notion-a{n:03d}-list",
            "List some pages. Takes a cursor.",
            {"type": "object", "properties": {"cursor": {"type": "string"}}},
            Tier.T0,
        )
        for n in range(count - 1)
    ]
    tools.append(
        PluginTool(
            f"mcp:n/{late}",
            "Notion",
            late,
            "Search the workspace for pages by words. Returns the best matches.",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            Tier.T0,
        )
    )
    return tools


class Hub:
    """A plugin hub with a fixed list of tools; it makes no call."""

    def __init__(self, tools):
        self.tools = tools
        self.made = []

    def offered(self):
        return list(self.tools)

    def tier_of(self, key):
        return Tier.T0 if any(t.key == key for t in self.tools) else None

    async def call(self, key, arguments, *, approver, task_id=None, pool=None):
        self.made.append((key, dict(arguments)))
        return PluginResult(True, None, "found")

    def record_unrun(self, key, *, outcome, task_id=None):
        pass


def alias_of(offer, name):
    return next(a for a, t in offer.by_alias.items() if t.name == name)


def choosing(*names):
    return tool_plan((CHOOSE, {"names": list(names)}))


@pytest.fixture
def ledger(conn):
    return Ledger(SpendTracker(conn, now_ms=lambda: 1_000))


# --- the choice itself ---------------------------------------------------------------------


def test_the_offer_keeps_every_tool_so_that_nothing_is_lost_to_the_alphabet():
    offer = make_offer(notion_tools())
    assert len(offer.by_alias) == 65 > MAX_OFFERED
    assert "Notion__notion-search" in offer.by_alias


async def test_the_catalogue_step_picks_the_late_tool_and_shows_no_schemas(client, ledger):
    offer = make_offer(notion_tools())
    search = alias_of(offer, "notion-search")
    client.plans.append(choosing(search))
    chosen = await ToolPicker(client).pick(offer, QUESTION, [], model="m", ledger=ledger)
    assert chosen[0] == search
    sent = client.plan_calls[0]
    assert search in sent["user"] and "Search the workspace for pages by words." in sent["user"]
    assert "Returns the best matches" not in sent["user"]  # only the first sentence
    assert "properties" not in sent["user"]  # no schemas in the catalogue
    assert len(sent["user"]) < 12000
    assert ledger.total > 0  # the call is paid for


async def test_what_the_model_names_that_is_not_in_the_catalogue_is_ignored(client, ledger):
    offer = make_offer(notion_tools())
    listing = alias_of(offer, "notion-a003-list")
    client.plans.append(choosing("made_up", listing))
    chosen = await ToolPicker(client).pick(offer, QUESTION, [], model="m", ledger=ledger)
    assert "made_up" not in chosen and listing in chosen
    assert alias_of(offer, "notion-search") in chosen  # the service's search tool comes along


@pytest.mark.parametrize("failure", [OpenRouterError(500, "server", "boom"), TimeoutError()])
async def test_if_the_model_fails_the_tools_sharing_words_with_the_request_are_used(
    client, ledger, failure
):
    offer = make_offer(notion_tools())
    client.plans.append(failure)
    chosen = await ToolPicker(client).pick(offer, QUESTION, [], model="m", ledger=ledger)
    assert alias_of(offer, "notion-search") in chosen
    assert len(chosen) <= PICK_MAX + 4


async def test_a_catalogue_too_large_is_cut_by_words_before_the_model_sees_it(client, ledger):
    offer = make_offer(notion_tools(400))
    client.plans.append(choosing())
    chosen = await ToolPicker(client).pick(offer, QUESTION, [], model="m", ledger=ledger)
    assert client.plan_calls[0]["user"].count("\n") < 200
    assert alias_of(offer, "notion-search") in chosen


# --- the fast tier -------------------------------------------------------------------------


async def test_the_fast_tier_offers_the_search_tool_when_it_sorts_last_of_65(make_rig, client):
    hub = Hub(notion_tools())
    rig = make_rig(plugins=hub)
    search = alias_of(make_offer(hub.tools), "notion-search")
    route(client)
    client.plans.append(choosing(search))  # the catalogue step
    client.plans.append(tool_plan((search, {"query": "today"})))  # the real call
    client.replies.append(spoken("答", "好。"))
    await rig.ask(QUESTION)
    offered = tools_offered(client)
    assert search in offered and len(offered) <= PICK_MAX + 4 < MAX_OFFERED
    assert hub.made == [("mcp:n/notion-search", {"query": "today"})]


async def test_a_few_tools_need_no_extra_call(make_rig, client):
    hub = Hub(notion_tools(5))
    rig = make_rig(plugins=hub)
    route(client)
    client.plans.append(tool_plan())
    client.replies.append(spoken("答", "好。"))
    await rig.ask(QUESTION)
    assert len(client.plan_calls) == 1 and len(tools_offered(client)) == 5


# --- the arguments -------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "cursor": {"type": "string"},
        "n": {"type": "integer"},
    },
    "required": ["query"],
}


def test_an_empty_or_null_optional_argument_is_dropped_and_a_required_one_is_kept():
    assert clean_arguments(SCHEMA, {"query": "", "cursor": "", "n": None}) == {"query": ""}


def test_a_value_that_means_something_is_kept():
    args = {"query": "tea", "n": 0, "cursor": "abc"}
    assert clean_arguments(SCHEMA, args) == args


def test_an_unknown_key_is_dropped_only_when_the_schema_forbids_extras():
    args = {"query": "tea", "extra": 1}
    assert clean_arguments({**SCHEMA, "additionalProperties": False}, args) == {"query": "tea"}
    assert clean_arguments(SCHEMA, args) == args
    assert clean_arguments({**SCHEMA, "additionalProperties": True}, args) == args
    # a schema that lists nothing does not forbid what it did not describe
    assert clean_arguments({"type": "object", "additionalProperties": False}, args) == args
