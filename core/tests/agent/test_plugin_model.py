"""Tests for the answer model and the plugins' tools: any model may word the answers, so a model that
cannot be sent tools must not fail a request, only leave the plugins out of it.

Whether a model takes tools is the catalog's to say (brain/catalog.py, the capability
`tool_calling`), read for each request. OpenRouter and the clock are fakes; the plugins are real
ones (see test_plugins.py).
"""

import logging

import pytest

from raphael_core.agent.plugins import TOOL_REFUSAL_TTL_S, ToolSupport
from raphael_core.brain.catalog import CatalogEntry, ModelPrice
from raphael_core.brain.openrouter import OpenRouterError

from .conftest import decision, tool_plan
from .test_plugins import PRIVATE, calls, plugins, route  # noqa: F401
from .test_service import make_rig, spoken  # noqa: F401

DEFAULT = "openai/gpt-6-luna"
JSON_ROLES = {"structured_output", "reasoning", "reasoning_off", "max_tokens"}


def llm(model_id: str, *, tools: bool = True, kind: str = "llm") -> CatalogEntry:
    """A model of the catalog that can word an answer, with or without tool calling."""
    capabilities = JSON_ROLES | ({"tool_calling"} if tools else set())
    return CatalogEntry(
        id=model_id,
        kind=kind,
        name=model_id.split("/")[-1],
        provider="Maker",
        price=ModelPrice(input_per_mtok=0.1, output_per_mtok=0.5),
        variable_price=False,
        context_length=None,
        created_at_ms=None,
        capabilities=frozenset(capabilities),
    )


class Catalog:
    """The catalog the assistant reads for each request, as `Brain.known_models` gives it: a list a
    test may change, or None for a list that was never read. `reads` counts the readings."""

    def __init__(self, *entries):
        self.entries = list(entries)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.entries


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def catalog():
    return Catalog(llm(DEFAULT))


@pytest.fixture
def rig(make_rig, plugins, catalog):  # noqa: F811
    return make_rig(plugins=plugins.hub, models=catalog)


# --- what the catalog says of a model ----------------------------------------------------------


def test_a_model_whose_entry_has_tool_calling_takes_tools():
    assert ToolSupport(Catalog(llm("a/b"))).supports("a/b") is True


def test_a_model_whose_entry_lacks_it_takes_none():
    assert ToolSupport(Catalog(llm("a/b", tools=False))).supports("a/b") is False


def test_a_model_the_catalog_does_not_list_is_not_known_either_way():
    support = ToolSupport(Catalog(llm("a/b"), llm("c/d", tools=False)))
    assert support.supports("e/f") is None
    # A speech model of the same id is not a language model: it says nothing.
    assert ToolSupport(Catalog(llm("a/b", kind="stt"))).supports("a/b") is None


@pytest.mark.parametrize("source", [None, Catalog()])
def test_without_a_list_the_builtin_table_speaks_for_raphaels_own_models_only(source):
    support = ToolSupport(source)  # a list that was never read, or no catalog at all
    for model_id in (DEFAULT, "anthropic/claude-haiku-5.5", "google/gemini-3.1-flash-lite"):
        assert support.supports(model_id) is True  # they were all tried live
    assert support.supports("some/other-model") is None


def test_the_catalog_is_read_for_each_question():
    source = Catalog(llm("a/b"))
    support = ToolSupport(source)
    assert support.supports("a/b") is True
    source.entries = [llm("a/b", tools=False)]  # the list was read again, and the model changed
    assert support.supports("a/b") is False
    assert source.reads == 2


def test_a_catalog_that_cannot_be_read_is_no_list_and_says_so_without_its_message(caplog):
    def broken():
        raise RuntimeError(f"cannot read {PRIVATE}")

    with caplog.at_level(logging.DEBUG):
        support = ToolSupport(broken)
        assert support.supports(DEFAULT) is True  # the builtin table
        assert support.supports("a/b") is None
    assert "RuntimeError" in caplog.text and PRIVATE not in caplog.text


def test_a_refusal_is_remembered_for_ten_minutes_and_then_the_catalog_speaks_again():
    clock = Clock()
    support = ToolSupport(Catalog(llm("a/b")), clock=clock)
    support.refused("a/b")
    assert support.supports("a/b") is False
    clock.now += TOOL_REFUSAL_TTL_S - 1
    assert support.supports("a/b") is False
    clock.now += 2
    assert support.supports("a/b") is True


def test_a_refusal_is_of_that_model_only():
    support = ToolSupport(Catalog(llm("a/b"), llm("c/d")))
    support.refused("a/b")
    assert support.supports("c/d") is True


# --- a request whose answer model cannot take tools --------------------------------------------


async def test_a_model_that_takes_no_tools_leaves_the_plugins_out_of_an_answer(
    rig, plugins, client, catalog, calls, caplog
):
    await plugins.connect(look="T0")
    catalog.entries = [llm(DEFAULT, tools=False)]
    route(client)
    client.replies.append(spoken("答", "好。"))
    with caplog.at_level(logging.DEBUG):
        result = await rig.ask(PRIVATE)
    assert (result["status"], result["prefix"], result["text"]) == ("done", "答", "好。")
    assert client.plan_calls == [] and calls == []
    # It is said in the log, with the model and nothing the user said.
    assert "takes no tools" in caplog.text and DEFAULT in caplog.text
    assert PRIVATE not in caplog.text


async def test_each_request_is_decided_by_the_catalog_as_it_is_then(
    rig, plugins, client, catalog, calls
):
    await plugins.connect(look="T0")
    for takes_tools in (False, True, False, True):
        catalog.entries = [llm(DEFAULT, tools=takes_tools)]
        route(client)
        if takes_tools:
            client.plans.append(tool_plan(("Notes__look", {"topic": "tea"})))
        client.replies.append(spoken("答", "好。"))
        await rig.ask("你好嗎")
    assert len(client.plan_calls) == 2 and calls == ["look", "look"]


async def test_a_model_the_catalog_does_not_list_is_tried(rig, plugins, client, catalog, calls):
    await plugins.connect(look="T0")
    catalog.entries = [llm("some/other-model", tools=False)]  # the model that is set is not there
    route(client)
    client.plans.append(tool_plan(("Notes__look", {"topic": "tea"})))
    client.replies.append(spoken("答", "查到茶了。"))
    result = await rig.ask("幫我查茶")
    assert result["text"] == "查到茶了。" and calls == ["look"]


async def test_before_any_list_was_read_the_default_model_is_tried(
    rig, plugins, client, catalog, calls
):
    await plugins.connect(look="T0")
    catalog.entries = None
    route(client)
    client.plans.append(tool_plan(("Notes__look", {"topic": "tea"})))
    client.replies.append(spoken("答", "查到茶了。"))
    assert (await rig.ask("幫我查茶"))["text"] == "查到茶了。" and calls == ["look"]


async def test_the_picking_call_goes_to_the_model_that_is_set_whichever_it_is(
    rig, plugins, client, catalog, calls
):
    await plugins.connect(look="T0")
    for model in ("anthropic/claude-haiku-5.5", "meta-llama/llama-9-70b:nitro"):
        catalog.entries = [llm(model)]
        rig.settings.set("agent_model", model)
        route(client)
        client.plans.append(tool_plan())
        client.replies.append(spoken("答", "好。"))
        await rig.ask("你好嗎")
        assert client.plan_calls[-1]["model"] == model
        assert client.json_calls[-1]["model"] == model  # the same one words the answer


async def test_changing_the_model_changes_what_is_known_about_it(
    rig, plugins, client, catalog, calls
):
    await plugins.connect(look="T0")
    catalog.entries = [llm(DEFAULT), llm("no/tools", tools=False)]
    rig.settings.set("agent_model", "no/tools")
    route(client)
    client.replies.append(spoken("答", "好。"))
    await rig.ask("你好嗎")
    assert client.plan_calls == []
    rig.settings.set("agent_model", DEFAULT)  # back to one that takes tools
    route(client)
    client.plans.append(tool_plan(("Notes__look", {"topic": "tea"})))
    client.replies.append(spoken("答", "查到茶了。"))
    await rig.ask("幫我查茶")
    assert calls == ["look"]


async def test_the_catalog_is_read_only_for_a_request_that_may_use_a_plugin(
    rig, plugins, client, catalog
):
    # Small talk, a discussion and a command never ask whether a model takes tools...
    await plugins.connect(look="T0")
    for kind in ("chat", "discuss"):
        client.decisions.append(decision(kind))
        client.replies.append(spoken("答", "好。"))
        await rig.ask("你好嗎")
    client.decisions.append(decision("command", "volume_up"))
    await rig.ask("大聲一點")
    assert catalog.reads == 0
    # ...and neither does an answer when no plugin tool has a tier: nothing would be offered.
    [server] = (await plugins.rpc.call("toolsets.list"))["groups"]
    await plugins.set_on(server["id"], "look", False)
    route(client)
    client.replies.append(spoken("答", "好。"))
    await rig.ask("你好嗎")
    assert catalog.reads == 0 and client.plan_calls == []


# --- a model that refuses tools although the catalog said it takes them ------------------------


async def test_a_refusal_of_the_tools_leaves_the_plugins_out_and_is_remembered(
    rig, plugins, client, catalog, calls, caplog
):
    await plugins.connect(look="T0")
    client.plans.append(OpenRouterError(404, "bad_request", "No endpoints found that support tool use"))
    route(client)
    client.replies.append(spoken("答", "好。"))
    with caplog.at_level(logging.DEBUG):
        result = await rig.ask(PRIVATE)
    assert (result["status"], result["text"]) == ("done", "好。")
    assert len(client.plan_calls) == 1 and calls == []
    assert "refused tools" in caplog.text and DEFAULT in caplog.text
    assert PRIVATE not in caplog.text
    # The next request does not try again (ten minutes).
    route(client)
    client.replies.append(spoken("答", "好。"))
    await rig.ask("你好嗎")
    assert len(client.plan_calls) == 1
    # After the ten minutes the catalog speaks again, and the model is planned with.
    rig.clock.now += TOOL_REFUSAL_TTL_S + 1
    route(client)
    client.plans.append(tool_plan(("Notes__look", {"topic": "tea"})))
    client.replies.append(spoken("答", "查到茶了。"))
    await rig.ask("幫我查茶")
    assert len(client.plan_calls) == 2 and calls == ["look"]


async def test_a_request_the_provider_found_wrong_is_not_a_model_without_tools(
    rig, plugins, client, calls
):
    # A 400 can be a tool whose schema a provider does not accept: the model is not to blame, and
    # the next request is planned as before.
    await plugins.connect(look="T0")
    client.plans.append(OpenRouterError(400, "bad_request", "Invalid schema for function"))
    route(client)
    client.replies.append(spoken("答", "好。"))
    assert (await rig.ask("你好嗎"))["text"] == "好。"
    route(client)
    client.plans.append(tool_plan(("Notes__look", {"topic": "tea"})))
    client.replies.append(spoken("答", "查到茶了。"))
    assert (await rig.ask("幫我查茶"))["text"] == "查到茶了。"
    assert calls == ["look"] and len(client.plan_calls) == 2
