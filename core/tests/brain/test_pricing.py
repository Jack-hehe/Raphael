"""Tests for `pricing.get`: what the models cost, from OpenRouter's public model list.

No test touches the network: the client is a FakeModelsClient that answers with the recorded
response in fixtures/openrouter_models.json (a trimmed copy of GET /models?output_modalities=all
of 2026-10-18), or fails as told. The cache lives in an in-memory database.
"""

import asyncio
import copy
import json
import logging
from pathlib import Path

import pytest

from raphael_core.brain import pricing
from raphael_core.brain.openrouter import CLEANUP_MODEL_CHOICES, OpenRouterError
from raphael_core.brain.pricing import BUILTIN_PRICES, PRICING_TTL_MS, PricingService, parse_models
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.settings import STT_CLOUD_MODELS, SettingsStore
from raphael_core.router import Router

FIXTURE = Path(__file__).parent / "fixtures" / "openrouter_models.json"
RECORDED: list[dict] = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]
NOW_MS = 1_792_300_000_000
HOUR_MS = 3_600_000


class FakeModelsClient:
    """Stands in for OpenRouterClient: `models()` answers with `reply`, or raises `error`.

    Each call is kept in `calls` (the timeout it was given). A `delay` makes the call wait first.
    """

    def __init__(self):
        self.reply = copy.deepcopy(RECORDED)
        self.error = None
        self.delay = 0.0
        self.calls: list[float] = []

    async def models(self, timeout_s=5.0):
        self.calls.append(timeout_s)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.reply)


class Clock:
    def __init__(self, now_ms=NOW_MS):
        self.now_ms = now_ms

    def __call__(self):
        return self.now_ms


@pytest.fixture
def client():
    return FakeModelsClient()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def service(conn, client, clock):
    return PricingService(conn, client, clock)


def by_id(entries):
    return {entry["id"]: entry for entry in entries}


def network_down():
    return OpenRouterError(0, "network", "no route to host")


# --- parsing the recorded response --------------------------------------------------------------


def test_cleanup_models_are_priced_per_million_tokens():
    prices = parse_models(RECORDED)
    luna = prices["openai/gpt-6-luna"]
    assert (luna.input_per_mtok, luna.output_per_mtok, luna.per_minute) == (0.1, 0.5, None)
    haiku = prices["anthropic/claude-haiku-5.5"]
    assert (haiku.input_per_mtok, haiku.output_per_mtok) == (0.1, 0.5)
    flash = prices["google/gemini-3.1-flash-lite"]
    assert (flash.input_per_mtok, flash.output_per_mtok) == (0.25, 1.5)


def test_a_model_priced_per_second_of_audio_is_priced_per_minute():
    prices = parse_models(RECORDED)
    # The API says 0.00003055555555555 US dollars a second; a minute is 60 of them.
    scribe = prices["elevenlabs/scribe-v2"]
    assert scribe.per_minute == pytest.approx(0.0018333, rel=1e-4)
    assert (scribe.input_per_mtok, scribe.output_per_mtok) == (None, None)
    assert prices["openai/gpt-transcribe"].per_minute == pytest.approx(0.0045)
    assert prices["assemblyai/universal-3-6-pro"].per_minute == pytest.approx(0.005)
    assert prices["qwen/qwen3-asr-1.7b"].per_minute == pytest.approx(0.00045)
    assert prices["openai/whisper-large-v3-turbo"].per_minute == pytest.approx(0.0001998)


def test_a_model_priced_per_token_fills_the_token_prices_and_no_minute_price():
    gemini = parse_models(RECORDED)["google/gemini-3.5-transcribe"]
    assert (gemini.input_per_mtok, gemini.output_per_mtok, gemini.per_minute) == (2.0, 12.0, None)


def test_a_per_second_price_that_cannot_be_one_is_unknown_not_shown_as_six_dollars_a_minute():
    # The API lists microsoft/mai-transcribe-2 at "0.1" in the same field that holds a price per
    # second for the others: US$6 a minute. Microsoft's own prices are per hour; the unit is not
    # trustworthy, so the price is left out rather than guessed.
    mai = parse_models(RECORDED)["microsoft/mai-transcribe-2"]
    assert (mai.per_minute, mai.input_per_mtok, mai.output_per_mtok) == (None, None, None)


def test_only_the_models_raphael_offers_are_parsed():
    assert set(parse_models(RECORDED)) == set(CLEANUP_MODEL_CHOICES) | set(STT_CLOUD_MODELS)


def test_a_model_the_list_does_not_have_is_left_out():
    without_scribe = [m for m in RECORDED if m["id"] != "elevenlabs/scribe-v2"]
    assert "elevenlabs/scribe-v2" not in parse_models(without_scribe)


@pytest.mark.parametrize(
    "pricing_field",
    [
        None,
        "free",
        [],
        {},
        {"prompt": None, "completion": None},
        {"prompt": "x", "completion": "y"},
    ],
)
def test_a_price_that_is_missing_or_no_number_is_unknown(pricing_field):
    models = copy.deepcopy(RECORDED)
    for model in models:
        if model["id"] in ("openai/gpt-6-luna", "elevenlabs/scribe-v2"):
            model["pricing"] = pricing_field
    prices = parse_models(models)
    luna, scribe = prices["openai/gpt-6-luna"], prices["elevenlabs/scribe-v2"]
    assert (luna.input_per_mtok, luna.output_per_mtok) == (None, None)
    assert (scribe.per_minute, scribe.input_per_mtok, scribe.output_per_mtok) == (None,) * 3


@pytest.mark.parametrize("bad", ["-0.1", "nan", "inf", -1, True, "1e999"])
def test_a_negative_or_unusable_price_is_unknown(bad):
    models = copy.deepcopy(RECORDED)
    next(m for m in models if m["id"] == "openai/gpt-6-luna")["pricing"] = {
        "prompt": bad,
        "completion": "0.0000005",
    }
    luna = parse_models(models)["openai/gpt-6-luna"]
    assert luna.input_per_mtok is None
    assert luna.output_per_mtok == 0.5


def test_a_free_model_costs_zero_not_unknown():
    models = copy.deepcopy(RECORDED)
    next(m for m in models if m["id"] == "elevenlabs/scribe-v2")["pricing"] = {
        "prompt": "0",
        "completion": "0",
    }
    assert parse_models(models)["elevenlabs/scribe-v2"].per_minute == 0.0


def test_entries_that_are_not_models_are_skipped():
    models = [None, 7, "openai/gpt-6-luna", {"id": 5}, {"no": "id"}, *RECORDED]
    assert set(parse_models(models)) == set(CLEANUP_MODEL_CHOICES) | set(STT_CLOUD_MODELS)


def test_the_builtin_table_covers_every_model_raphael_offers_and_equals_the_recorded_prices():
    assert set(BUILTIN_PRICES) == set(CLEANUP_MODEL_CHOICES) | set(STT_CLOUD_MODELS)
    recorded = parse_models(RECORDED)
    for model_id, builtin in BUILTIN_PRICES.items():
        assert (builtin.per_minute, builtin.input_per_mtok, builtin.output_per_mtok) == (
            recorded[model_id].per_minute,
            recorded[model_id].input_per_mtok,
            recorded[model_id].output_per_mtok,
        )


# --- the result ---------------------------------------------------------------------------------


async def test_a_live_answer_lists_the_models_in_the_order_of_the_choices(service, client):
    result = await service.get()
    assert result["source"] == "live"
    assert result["fetched_at_ms"] == NOW_MS
    assert [m["id"] for m in result["cleanup"]] == CLEANUP_MODEL_CHOICES
    assert [m["id"] for m in result["stt"]] == list(STT_CLOUD_MODELS)
    assert set(result) == {"cleanup", "stt", "fetched_at_ms", "source"}
    assert len(client.calls) == 1


async def test_entries_have_the_shape_the_app_reads(service):
    result = await service.get()
    for entry in result["cleanup"]:
        assert set(entry) == {"id", "name", "input_per_mtok", "output_per_mtok"}
    for entry in result["stt"]:
        assert set(entry) == {"id", "name", "per_minute", "input_per_mtok", "output_per_mtok"}
    json.dumps(result)  # the router sends it as JSON


async def test_names_are_short_display_names(service):
    result = await service.get()
    names = {m["id"]: m["name"] for m in [*result["cleanup"], *result["stt"]]}
    assert names["openai/gpt-6-luna"] == "GPT-6 Luna"
    assert names["anthropic/claude-haiku-5.5"] == "Claude Haiku 5.5"
    assert names["google/gemini-3.1-flash-lite"] == "Gemini 3.1 Flash Lite"
    assert names["elevenlabs/scribe-v2"] == "ElevenLabs Scribe v2"
    assert all(name for name in names.values())


async def test_prices_come_through_as_the_app_reads_them(service):
    result = await service.get()
    cleanup, stt = by_id(result["cleanup"]), by_id(result["stt"])
    assert cleanup["openai/gpt-6-luna"]["input_per_mtok"] == 0.1
    assert cleanup["openai/gpt-6-luna"]["output_per_mtok"] == 0.5
    assert stt["elevenlabs/scribe-v2"]["per_minute"] == pytest.approx(0.0018333, rel=1e-4)
    assert stt["elevenlabs/scribe-v2"]["input_per_mtok"] is None
    assert stt["google/gemini-3.5-transcribe"]["input_per_mtok"] == 2.0
    assert stt["google/gemini-3.5-transcribe"]["output_per_mtok"] == 12.0
    assert stt["microsoft/mai-transcribe-2"]["per_minute"] is None


async def test_a_model_the_live_list_lacks_is_unknown_in_the_answer(service, client):
    client.reply = [m for m in client.reply if m["id"] != "qwen/qwen3-asr-1.7b"]
    qwen = by_id((await service.get())["stt"])["qwen/qwen3-asr-1.7b"]
    assert (qwen["per_minute"], qwen["input_per_mtok"], qwen["output_per_mtok"]) == (None,) * 3
    assert qwen["name"] == "Qwen3 ASR 1.7B"


async def test_the_fetch_is_given_a_five_second_timeout(service, client):
    await service.get()
    assert client.calls == [5.0]


# --- the cache ----------------------------------------------------------------------------------


async def test_a_second_call_within_a_day_uses_the_cache(service, client, clock):
    first = await service.get()
    clock.now_ms += 23 * HOUR_MS
    second = await service.get()
    assert len(client.calls) == 1
    assert second["source"] == "cached"
    assert second["fetched_at_ms"] == NOW_MS
    assert (second["cleanup"], second["stt"]) == (first["cleanup"], first["stt"])


async def test_the_cache_expires_after_a_day(service, client, clock):
    await service.get()
    clock.now_ms += PRICING_TTL_MS - 1
    assert (await service.get())["source"] == "cached"
    clock.now_ms += 1
    again = await service.get()
    assert again["source"] == "live"
    assert again["fetched_at_ms"] == NOW_MS + PRICING_TTL_MS
    assert len(client.calls) == 2


async def test_a_fresh_answer_replaces_the_cached_prices(service, client, clock):
    await service.get()
    cheaper = copy.deepcopy(RECORDED)
    next(m for m in cheaper if m["id"] == "openai/gpt-6-luna")["pricing"]["prompt"] = "0.00000005"
    client.reply = cheaper
    clock.now_ms += PRICING_TTL_MS
    luna = by_id((await service.get())["cleanup"])["openai/gpt-6-luna"]
    assert luna["input_per_mtok"] == 0.05


async def test_the_cache_survives_a_restart(conn, client, clock):
    await PricingService(conn, client, clock).get()
    clock.now_ms += HOUR_MS
    restarted = PricingService(conn, client, clock)
    result = await restarted.get()
    assert len(client.calls) == 1
    assert (result["source"], result["fetched_at_ms"]) == ("cached", NOW_MS)


async def test_the_cache_is_not_a_setting(conn, service):
    await service.get()
    assert "cache.pricing" not in SettingsStore(conn).all()
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 1


async def test_a_clock_that_went_back_counts_the_cache_as_stale(service, client, clock):
    await service.get()
    clock.now_ms -= HOUR_MS
    assert (await service.get())["source"] == "live"
    assert len(client.calls) == 2


async def test_a_cache_that_is_garbled_is_ignored(conn, client, clock):
    for value in ["not json", "[]", '{"fetched_at_ms": "soon", "prices": {}}', '{"prices": 3}']:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('cache.pricing', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (value,),
            )
        client.calls.clear()
        result = await PricingService(conn, client, clock).get()
        assert result["source"] == "live", value
        assert len(client.calls) == 1


async def test_calls_at_the_same_time_share_one_fetch(service, client):
    client.delay = 0.05
    results = await asyncio.gather(service.get(), service.get(), service.get())
    assert len(client.calls) == 1
    assert [r["source"] for r in results].count("live") >= 1
    assert all(r["fetched_at_ms"] == NOW_MS for r in results)


# --- when OpenRouter cannot be reached ----------------------------------------------------------


async def test_without_a_cache_a_network_failure_gives_the_builtin_prices(service, client):
    client.error = network_down()
    result = await service.get()
    assert result["source"] == "builtin"
    assert result["fetched_at_ms"] is None
    assert [m["id"] for m in result["cleanup"]] == CLEANUP_MODEL_CHOICES
    assert [m["id"] for m in result["stt"]] == list(STT_CLOUD_MODELS)
    luna = by_id(result["cleanup"])["openai/gpt-6-luna"]
    assert (luna["input_per_mtok"], luna["output_per_mtok"]) == (0.1, 0.5)
    assert by_id(result["stt"])["elevenlabs/scribe-v2"]["per_minute"] == pytest.approx(
        0.0018333, rel=1e-4
    )


async def test_a_failure_with_an_expired_cache_gives_the_cached_prices(service, client, clock):
    await service.get()
    cheaper = copy.deepcopy(RECORDED)
    next(m for m in cheaper if m["id"] == "openai/gpt-6-luna")["pricing"]["prompt"] = "0.00000005"
    client.reply = cheaper
    clock.now_ms += 3 * PRICING_TTL_MS
    client.error = network_down()
    result = await service.get()
    assert (result["source"], result["fetched_at_ms"]) == ("cached", NOW_MS)
    assert by_id(result["cleanup"])["openai/gpt-6-luna"]["input_per_mtok"] == 0.1


async def test_a_failure_with_an_expired_cache_from_before_a_restart(conn, client, clock):
    await PricingService(conn, client, clock).get()
    clock.now_ms += 5 * PRICING_TTL_MS
    client.error = network_down()
    result = await PricingService(conn, client, clock).get()
    assert (result["source"], result["fetched_at_ms"]) == ("cached", NOW_MS)


async def test_a_failure_is_tried_again_at_the_next_call(service, client):
    client.error = network_down()
    assert (await service.get())["source"] == "builtin"
    client.error = None
    assert (await service.get())["source"] == "live"
    assert len(client.calls) == 2


@pytest.mark.parametrize("kind", ["timeout", "server", "bad_response", "rate_limit", "auth"])
async def test_every_kind_of_failure_falls_back(service, client, kind):
    client.error = OpenRouterError(500, kind, "nope")
    assert (await service.get())["source"] == "builtin"


async def test_a_fetch_that_hangs_gives_up(conn, client, clock):
    client.delay = 5
    service = PricingService(conn, client, clock, fetch_timeout_s=0.05)
    result = await asyncio.wait_for(service.get(), 2)
    assert result["source"] == "builtin"


async def test_a_client_that_breaks_gives_the_fallback_and_logs_only_the_type(
    service, client, caplog
):
    secret = "sk-or-v1-TOPSECRET"
    client.error = RuntimeError(f"the key is {secret}")
    with caplog.at_level(logging.DEBUG):
        result = await service.get()
    assert result["source"] == "builtin"
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


async def test_a_client_without_a_model_list_gives_the_fallback(conn, clock):
    class NoModels:
        pass

    result = await PricingService(conn, NoModels(), clock).get()
    assert result["source"] == "builtin"


@pytest.mark.parametrize("reply", [[], [{"id": "some/other-model", "pricing": {}}], "oops", None])
async def test_a_list_without_any_of_the_models_is_no_answer_and_is_not_cached(
    service, client, conn, reply
):
    client.reply = reply
    assert (await service.get())["source"] == "builtin"
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 0


async def test_a_cache_that_cannot_be_written_does_not_fail_the_answer(conn, client, clock):
    service = PricingService(conn, client, clock)
    conn.execute("DROP TABLE settings")
    result = await service.get()
    assert result["source"] == "live"
    # The prices are still kept in memory for the day.
    assert (await service.get())["source"] == "cached"
    assert len(client.calls) == 1


# --- as an RPC method ---------------------------------------------------------------------------


async def test_pricing_get_is_a_method_of_the_brain(conn, client, clock):
    router = Router()
    Brain(conn, client, MemoryKeyStore(), now_ms=clock).register(router)
    assert "pricing.get" in router.methods()
    result = await router.call("pricing.get", {})
    assert result["source"] == "live"
    assert [m["id"] for m in result["cleanup"]] == CLEANUP_MODEL_CHOICES
    again = await router.call("pricing.get", {})
    assert again["source"] == "cached"


async def test_pricing_get_needs_no_key_and_asks_nothing_of_the_key_store(conn, client, clock):
    class Locked:
        def get(self):
            raise RuntimeError("vault locked")

        set = clear = get

    router = Router()
    Brain(conn, client, Locked(), now_ms=clock).register(router)
    assert (await router.call("pricing.get", {}))["source"] == "live"


def test_the_module_keeps_its_prices_and_names_for_the_models_it_lists():
    assert set(pricing.MODEL_NAMES) == set(BUILTIN_PRICES)
