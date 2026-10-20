"""Tests for `models.catalog` as a method of the brain, and for choosing a model with it.

A model is chosen with `settings.set` of `cleanup_model`, `agent_model` or `stt_cloud_model`, and
checked against the catalog OpenRouter gave last; a chosen model that OpenRouter no longer lists
goes back to its default when `models.catalog` finds out. No test touches the network: the client
answers with the recorded list of test_catalog.py, or fails as told.
"""

import logging

import pytest

from raphael_core.brain.catalog import ROLE_OF_SETTING, ROLES
from raphael_core.brain.openrouter import CLEANUP_MODEL_CHOICES
from raphael_core.brain.pricing import PRICING_TTL_MS, PricingService
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.settings import DEFAULTS, MODEL_SETTINGS, STT_CLOUD_MODELS, SettingsStore
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router

from .test_catalog import (
    LANGUAGE_MODELS,
    NOW_MS,
    RECORDED,
    Clock,
    FakeModelsClient,
    by_id,
    edited,
    network_down,
)


@pytest.fixture
def client():
    return FakeModelsClient()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def router(conn, client, clock):
    rpc = Router()
    Brain(conn, client, MemoryKeyStore(), now_ms=clock).register(rpc)
    return rpc


async def choose(router, key, model):
    return (await router.call("settings.set", {"key": key, "value": model}))["settings"][key]


async def refused(router, key, model) -> str:
    with pytest.raises(RpcError) as raised:
        await router.call("settings.set", {"key": key, "value": model})
    assert raised.value.code == INVALID_PARAMS
    return raised.value.message


# --- the roles ----------------------------------------------------------------------------------


def test_each_setting_of_a_model_is_a_role_with_its_default_among_its_own_models():
    assert [(role.name, role.setting, role.kind) for role in ROLES] == [
        ("cleanup", "cleanup_model", "llm"),
        ("answer", "agent_model", "llm"),
        ("stt", "stt_cloud_model", "stt"),
    ]
    assert set(ROLE_OF_SETTING) == set(MODEL_SETTINGS)
    for role in ROLES:
        assert role.default == DEFAULTS[role.setting]
        assert role.default == role.curated[0]


# --- the method ---------------------------------------------------------------------------------


async def test_the_catalog_and_the_prices_share_one_fetch(router, client):
    view = await router.call("models.catalog", {})
    assert (view["source"], view["fetched_at_ms"]) == ("live", NOW_MS)
    prices = await router.call("pricing.get", {})
    assert prices["source"] == "cached"
    assert by_id(prices["cleanup"])["openai/gpt-6-luna"]["input_per_mtok"] == 0.1
    assert (await router.call("models.catalog", {}))["source"] == "cached"
    assert client.calls == 1


async def test_the_prices_first_and_then_the_catalog_also_share_one_fetch(router, client):
    assert (await router.call("pricing.get", {}))["source"] == "live"
    view = await router.call("models.catalog", {})
    assert view["source"] == "cached"
    assert [m["id"] for m in view["llm"]] == LANGUAGE_MODELS
    assert client.calls == 1


async def test_without_the_list_the_answer_is_raphaels_own_models(router, client):
    client.error = network_down()
    view = await router.call("models.catalog", {})
    assert (view["source"], view["fetched_at_ms"], view["replaced"]) == ("builtin", None, [])
    assert [m["id"] for m in view["llm"]] == CLEANUP_MODEL_CHOICES
    assert [m["id"] for m in view["stt"]] == list(STT_CLOUD_MODELS)


async def test_models_catalog_needs_no_key(conn, client, clock):
    class Locked:
        def get(self):
            raise RuntimeError("vault locked")

        set = clear = get

    rpc = Router()
    Brain(conn, client, Locked(), now_ms=clock).register(rpc)
    assert (await rpc.call("models.catalog", {}))["source"] == "live"


# --- choosing a model ---------------------------------------------------------------------------


async def test_any_model_the_catalog_lists_for_the_role_can_be_chosen(router, client):
    await router.call("models.catalog", {})
    assert await choose(router, "cleanup_model", "tencent/hunyuan-a13b-instruct") == (
        "tencent/hunyuan-a13b-instruct"
    )
    free = "nvidia/nemotron-3-super-120b-a12b:free"
    assert await choose(router, "agent_model", free) == free
    assert await choose(router, "stt_cloud_model", " deepgram/nova-3 ") == "deepgram/nova-3"
    assert client.calls == 1  # choosing fetches nothing


async def test_a_listed_model_the_role_cannot_use_is_refused(router):
    await router.call("models.catalog", {})
    message = await refused(router, "cleanup_model", "google/gemini-3.8-flash")
    assert "reasoning_required" in message
    assert "no_structured_output" in await refused(
        router, "agent_model", "moondream/moondream3-preview"
    )
    assert "batch_only" in await refused(router, "agent_model", "anthropic/claude-haiku-5.5:batch")
    settings = (await router.call("settings.get", {}))["settings"]
    assert (settings["cleanup_model"], settings["agent_model"]) == (
        DEFAULTS["cleanup_model"],
        DEFAULTS["agent_model"],
    )


async def test_a_model_of_the_other_kind_or_off_the_list_is_refused(router):
    await router.call("models.catalog", {})
    for key, model in [
        ("stt_cloud_model", "openai/gpt-6-luna"),  # no transcription model
        ("cleanup_model", "elevenlabs/scribe-v2"),
        ("cleanup_model", "google/gemini-3.1-flash-image"),  # left out of the catalog
        ("agent_model", "nobody/no-such-model"),
    ]:
        assert "lists" in await refused(router, key, model)
    assert (await router.call("settings.get", {}))["settings"]["stt_cloud_model"] == (
        DEFAULTS["stt_cloud_model"]
    )


async def test_blank_or_spaced_model_ids_are_refused_before_the_catalog_is_asked(router):
    for value in ["", "  ", "openai/gpt 6 luna"]:
        await refused(router, "cleanup_model", value)
    with pytest.raises(RpcError) as raised:
        await router.call("settings.set", {"key": "stt_cloud_model", "value": 3})
    assert raised.value.code == INVALID_PARAMS


async def test_before_the_list_is_read_only_raphaels_own_models_can_be_chosen(router, client):
    # No call to models.catalog yet: nothing is known, and settings.set fetches nothing.
    assert await choose(router, "cleanup_model", "anthropic/claude-haiku-5.5") == (
        "anthropic/claude-haiku-5.5"
    )
    assert await choose(router, "stt_cloud_model", "qwen/qwen3-asr-1.7b") == "qwen/qwen3-asr-1.7b"
    message = await refused(router, "cleanup_model", "tencent/hunyuan-a13b-instruct")
    assert "Raphael's own" in message
    assert client.calls == 0


async def test_when_the_list_cannot_be_had_the_curated_models_can_be_chosen(router, client):
    client.error = network_down()
    assert (await router.call("models.catalog", {}))["source"] == "builtin"
    assert await choose(router, "agent_model", "google/gemini-3.1-flash-lite") == (
        "google/gemini-3.1-flash-lite"
    )
    await refused(router, "agent_model", "deepgram/nova-3")


async def test_without_the_list_the_current_model_is_kept_and_can_be_set_again(conn, router):
    # A model an older Raphael took for an answer, before models were checked against the list.
    SettingsStore(conn).set("agent_model", "some/older-choice")
    assert await choose(router, "agent_model", "some/older-choice") == "some/older-choice"
    assert (await router.call("settings.get", {}))["settings"]["agent_model"] == "some/older-choice"


async def test_the_default_can_always_be_chosen(router, client):
    client.reply = [m for m in RECORDED if m["id"] != "openai/gpt-6-luna"]
    await router.call("models.catalog", {})
    await choose(router, "cleanup_model", "tencent/hunyuan-a13b-instruct")
    # The list lacks the default, but the setting reads as it anyway when nobody chose.
    assert await choose(router, "cleanup_model", "openai/gpt-6-luna") == "openai/gpt-6-luna"


async def test_a_stale_catalog_kept_from_before_a_restart_is_what_a_choice_is_checked_against(
    conn, client, clock
):
    await PricingService(conn, client, clock).catalog()
    clock.now_ms += 3 * PRICING_TTL_MS
    rpc = Router()
    Brain(conn, client, MemoryKeyStore(), now_ms=clock).register(rpc)
    assert await choose(rpc, "stt_cloud_model", "deepgram/nova-3") == "deepgram/nova-3"
    await refused(rpc, "cleanup_model", "google/gemini-3.8-flash")
    assert client.calls == 1


# --- a chosen model that OpenRouter no longer lists ---------------------------------------------


async def test_a_model_gone_from_the_list_goes_back_to_the_default_and_is_reported_once(
    router, client, clock
):
    await router.call("models.catalog", {})
    await choose(router, "cleanup_model", "tencent/hunyuan-a13b-instruct")
    await choose(router, "stt_cloud_model", "deepgram/nova-3")
    gone = {"tencent/hunyuan-a13b-instruct", "deepgram/nova-3"}
    client.reply = [m for m in RECORDED if m["id"] not in gone]
    clock.now_ms += PRICING_TTL_MS
    view = await router.call("models.catalog", {})
    assert view["source"] == "live"
    assert view["replaced"] == [
        {
            "key": "cleanup_model",
            "role": "cleanup",
            "model": "tencent/hunyuan-a13b-instruct",
            "default": "openai/gpt-6-luna",
        },
        {
            "key": "stt_cloud_model",
            "role": "stt",
            "model": "deepgram/nova-3",
            "default": "elevenlabs/scribe-v2",
        },
    ]
    settings = (await router.call("settings.get", {}))["settings"]
    assert (settings["cleanup_model"], settings["stt_cloud_model"]) == (
        "openai/gpt-6-luna",
        "elevenlabs/scribe-v2",
    )
    assert (await router.call("models.catalog", {}))["replaced"] == []


async def test_a_model_kept_from_an_older_raphael_that_is_not_listed_is_replaced(conn, router):
    SettingsStore(conn).set("agent_model", "some/older-choice")
    view = await router.call("models.catalog", {})
    assert [(r["key"], r["model"]) for r in view["replaced"]] == [
        ("agent_model", "some/older-choice")
    ]
    assert (await router.call("settings.get", {}))["settings"]["agent_model"] == "openai/gpt-6-luna"


async def test_a_gone_model_stays_when_the_default_is_gone_too(conn, router, client):
    SettingsStore(conn).set("cleanup_model", "nobody/model")
    client.reply = [m for m in RECORDED if m["id"] != "openai/gpt-6-luna"]
    assert (await router.call("models.catalog", {}))["replaced"] == []
    assert (await router.call("settings.get", {}))["settings"]["cleanup_model"] == "nobody/model"


async def test_nothing_is_replaced_without_the_list(conn, router, client):
    SettingsStore(conn).set("agent_model", "some/older-choice")
    client.error = network_down()
    assert (await router.call("models.catalog", {}))["replaced"] == []
    assert (await router.call("settings.get", {}))["settings"]["agent_model"] == "some/older-choice"


async def test_a_listed_model_that_a_role_can_no_longer_use_is_left_to_the_user(
    router, client, clock
):
    await router.call("models.catalog", {})
    await choose(router, "agent_model", "tencent/hunyuan-a13b-instruct")
    client.reply = edited("tencent/hunyuan-a13b-instruct", reasoning={"mandatory": True})
    clock.now_ms += PRICING_TTL_MS
    view = await router.call("models.catalog", {})
    assert view["replaced"] == []
    hunyuan = by_id(view["llm"])["tencent/hunyuan-a13b-instruct"]
    assert hunyuan["roles"]["answer"] == {"usable": False, "reason": "reasoning_required"}
    settings = (await router.call("settings.get", {}))["settings"]
    assert settings["agent_model"] == "tencent/hunyuan-a13b-instruct"


async def test_the_lists_models_and_prices_are_not_logged(router, client, clock, caplog):
    await router.call("models.catalog", {})
    await choose(router, "cleanup_model", "tencent/hunyuan-a13b-instruct")
    client.reply = [m for m in RECORDED if m["id"] != "tencent/hunyuan-a13b-instruct"]
    clock.now_ms += PRICING_TTL_MS
    with caplog.at_level(logging.DEBUG):
        await router.call("models.catalog", {})
        await router.call("pricing.get", {})
    assert "cleanup_model" in caplog.text  # the setting that went back is named...
    for model in RECORDED:
        assert model["id"] not in caplog.text  # ...but no model
    assert "0.1" not in caplog.text and "$" not in caplog.text
