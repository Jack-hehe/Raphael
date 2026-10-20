"""Tests for OpenRouter's model list as Raphael reads and keeps it: what each model costs, one fetch
for `models.catalog`, a day-long cache, and the builtin table when the list cannot be had.

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
from raphael_core.brain.catalog import ModelPrice, parse_catalog
from raphael_core.brain.openrouter import CLEANUP_MODEL_CHOICES, OpenRouterError
from raphael_core.brain.pricing import BUILTIN_PRICES, CACHE_READING, PRICING_TTL_MS, PricingService
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.settings import STT_CLOUD_MODELS, SettingsStore
from raphael_core.router import Router

FIXTURE = Path(__file__).parent / "fixtures" / "openrouter_models.json"
RECORDED: list[dict] = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]
NOW_MS = 1_792_300_000_000
HOUR_MS = 3_600_000
OFFERED = {*CLEANUP_MODEL_CHOICES, *STT_CLOUD_MODELS}


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


def prices(models) -> dict[str, ModelPrice]:
    """The price of each model the list has that Raphael could use, by its id."""
    return {entry.id: entry.price for entry in parse_catalog(models)}


async def answer(service) -> tuple[dict[str, ModelPrice], int | None, str]:
    """The prices of the catalog that `service` answers with, the list's time and its source."""
    entries, fetched_at_ms, source = await service.catalog()
    return {entry.id: entry.price for entry in entries}, fetched_at_ms, source


def network_down():
    return OpenRouterError(0, "network", "no route to host")


# --- reading the prices -------------------------------------------------------------------------


def test_language_models_are_priced_per_million_tokens():
    listed = prices(RECORDED)
    luna = listed["openai/gpt-6-luna"]
    assert (luna.input_per_mtok, luna.output_per_mtok, luna.per_minute) == (0.1, 0.5, None)
    haiku = listed["anthropic/claude-haiku-5.5"]
    assert (haiku.input_per_mtok, haiku.output_per_mtok) == (0.1, 0.5)
    flash = listed["google/gemini-3.1-flash-lite"]
    assert (flash.input_per_mtok, flash.output_per_mtok) == (0.25, 1.5)


def test_a_model_priced_per_second_of_audio_is_priced_per_minute():
    listed = prices(RECORDED)
    # The API says 0.00003055555555555 US dollars a second; a minute is 60 of them.
    scribe = listed["elevenlabs/scribe-v2"]
    assert scribe.per_minute == pytest.approx(0.0018333, rel=1e-4)
    assert (scribe.input_per_mtok, scribe.output_per_mtok) == (None, None)
    assert listed["openai/gpt-transcribe"].per_minute == pytest.approx(0.0045)
    assert listed["assemblyai/universal-3-6-pro"].per_minute == pytest.approx(0.005)
    assert listed["qwen/qwen3-asr-1.7b"].per_minute == pytest.approx(0.00045)
    assert listed["openai/whisper-large-v3-turbo"].per_minute == pytest.approx(0.0001998)


def test_a_model_priced_per_token_fills_the_token_prices_and_no_minute_price():
    gemini = prices(RECORDED)["google/gemini-3.5-transcribe"]
    assert (gemini.input_per_mtok, gemini.output_per_mtok, gemini.per_minute) == (2.0, 12.0, None)


def test_a_per_second_price_that_cannot_be_one_is_read_per_hour_not_shown_as_six_dollars_a_minute():
    # The API lists microsoft/mai-transcribe-2 at "0.1" in the same field that holds a price per
    # second for the others: US$6 a minute. It is Microsoft's US$0.10 per hour of audio.
    mai = prices(RECORDED)["microsoft/mai-transcribe-2"]
    assert mai.per_minute == pytest.approx(0.1 / 60, rel=1e-5)
    assert (mai.input_per_mtok, mai.output_per_mtok) == (None, None)


@pytest.mark.parametrize(("listed", "per_minute"), [("0.36", 0.006), ("60", 1.0), ("61", None)])
def test_a_price_read_per_hour_is_still_unknown_when_it_is_too_dear_even_so(listed, per_minute):
    # MAI-Transcribe 1.5 is "0.36" (US$0.36 an hour). Over US$1 a minute even per hour is no price.
    model = {"id": "microsoft/mai-transcribe-2", "pricing": {"prompt": listed, "completion": "0"}}
    assert prices([model])["microsoft/mai-transcribe-2"].per_minute == per_minute


def test_every_model_of_the_recorded_list_is_read_raphaels_own_among_them():
    listed = prices(RECORDED)
    assert set(listed) == {model["id"] for model in RECORDED}
    assert OFFERED <= set(listed)


def test_a_model_the_list_does_not_have_is_left_out():
    without_scribe = [m for m in RECORDED if m["id"] != "elevenlabs/scribe-v2"]
    assert "elevenlabs/scribe-v2" not in prices(without_scribe)


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
    listed = prices(models)
    luna, scribe = listed["openai/gpt-6-luna"], listed["elevenlabs/scribe-v2"]
    assert (luna.input_per_mtok, luna.output_per_mtok) == (None, None)
    assert (scribe.per_minute, scribe.input_per_mtok, scribe.output_per_mtok) == (None,) * 3


@pytest.mark.parametrize("bad", ["-0.1", "nan", "inf", -1, True, "1e999"])
def test_a_negative_or_unusable_price_is_unknown(bad):
    models = copy.deepcopy(RECORDED)
    next(m for m in models if m["id"] == "openai/gpt-6-luna")["pricing"] = {
        "prompt": bad,
        "completion": "0.0000005",
    }
    luna = prices(models)["openai/gpt-6-luna"]
    assert luna.input_per_mtok is None
    assert luna.output_per_mtok == 0.5


def test_a_free_model_costs_zero_not_unknown():
    models = copy.deepcopy(RECORDED)
    next(m for m in models if m["id"] == "elevenlabs/scribe-v2")["pricing"] = {
        "prompt": "0",
        "completion": "0",
    }
    assert prices(models)["elevenlabs/scribe-v2"].per_minute == 0.0


def test_entries_that_are_not_models_are_skipped():
    models = [None, 7, "openai/gpt-6-luna", {"id": 5}, {"no": "id"}, *RECORDED]
    assert set(prices(models)) == set(prices(RECORDED))


def test_the_builtin_table_covers_every_model_raphael_offers_and_equals_the_recorded_prices():
    assert set(BUILTIN_PRICES) == OFFERED
    recorded = prices(RECORDED)
    for model_id, builtin in BUILTIN_PRICES.items():
        assert builtin == recorded[model_id], model_id


def test_the_builtin_table_names_each_model_as_the_recorded_list_does():
    assert set(pricing.BUILTIN_LISTING) == set(BUILTIN_PRICES)
    recorded = {entry.id: entry for entry in parse_catalog(RECORDED)}
    for entry in pricing.builtin_catalog():
        assert (entry.provider, entry.name, entry.kind, entry.price) == (
            recorded[entry.id].provider,
            recorded[entry.id].name,
            recorded[entry.id].kind,
            recorded[entry.id].price,
        )
        # Raphael's own models were tried live: every role of their kind can use them, and the
        # language models take tools.
        assert entry.capabilities == recorded[entry.id].capabilities, entry.id
        assert ("tool_calling" in entry.capabilities) == (entry.kind == "llm")


# --- the answer ---------------------------------------------------------------------------------


async def test_a_live_answer_is_every_model_of_the_list_in_its_order(service, client):
    entries, fetched_at_ms, source = await service.catalog()
    assert (source, fetched_at_ms) == ("live", NOW_MS)
    assert [entry.id for entry in entries] == [model["id"] for model in RECORDED]
    assert len(client.calls) == 1


async def test_prices_come_through_as_they_are_read(service):
    listed, _, _ = await answer(service)
    luna = listed["openai/gpt-6-luna"]
    assert (luna.input_per_mtok, luna.output_per_mtok) == (0.1, 0.5)
    assert listed["elevenlabs/scribe-v2"].per_minute == pytest.approx(0.0018333, rel=1e-4)
    assert listed["elevenlabs/scribe-v2"].input_per_mtok is None
    assert listed["google/gemini-3.5-transcribe"].input_per_mtok == 2.0
    assert listed["microsoft/mai-transcribe-2"].per_minute == pytest.approx(0.0016667, rel=1e-4)


async def test_a_model_the_live_list_lacks_is_not_in_the_answer(service, client):
    client.reply = [m for m in client.reply if m["id"] != "qwen/qwen3-asr-1.7b"]
    listed, _, _ = await answer(service)
    assert "qwen/qwen3-asr-1.7b" not in listed


async def test_the_fetch_is_given_a_five_second_timeout(service, client):
    await service.catalog()
    assert client.calls == [5.0]


# --- the cache ----------------------------------------------------------------------------------


async def test_a_second_call_within_a_day_uses_the_cache(service, client, clock):
    first = await answer(service)
    clock.now_ms += 23 * HOUR_MS
    second = await answer(service)
    assert len(client.calls) == 1
    assert second[1:] == (NOW_MS, "cached")
    assert second[0] == first[0]


async def test_the_cache_expires_after_a_day(service, client, clock):
    await service.catalog()
    clock.now_ms += PRICING_TTL_MS - 1
    assert (await service.catalog())[2] == "cached"
    clock.now_ms += 1
    _, fetched_at_ms, source = await service.catalog()
    assert (source, fetched_at_ms) == ("live", NOW_MS + PRICING_TTL_MS)
    assert len(client.calls) == 2


async def test_a_fresh_answer_replaces_the_cached_prices(service, client, clock):
    await service.catalog()
    cheaper = copy.deepcopy(RECORDED)
    next(m for m in cheaper if m["id"] == "openai/gpt-6-luna")["pricing"]["prompt"] = "0.00000005"
    client.reply = cheaper
    clock.now_ms += PRICING_TTL_MS
    listed, _, _ = await answer(service)
    assert listed["openai/gpt-6-luna"].input_per_mtok == 0.05


async def test_the_cache_survives_a_restart(conn, client, clock):
    await PricingService(conn, client, clock).catalog()
    clock.now_ms += HOUR_MS
    _, fetched_at_ms, source = await PricingService(conn, client, clock).catalog()
    assert len(client.calls) == 1
    assert (source, fetched_at_ms) == ("cached", NOW_MS)


async def test_the_cache_is_not_a_setting(conn, service):
    await service.catalog()
    assert "cache.pricing" not in SettingsStore(conn).all()
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 1


async def test_a_clock_that_went_back_counts_the_cache_as_stale(service, client, clock):
    await service.catalog()
    clock.now_ms -= HOUR_MS
    assert (await service.catalog())[2] == "live"
    assert len(client.calls) == 2


async def test_a_cache_read_the_oldest_way_is_fetched_again(conn, client, clock):
    # Cached the hour before by a core that left MAI-Transcribe 2 unknown, with no `reading`.
    old = json.dumps(
        {
            "fetched_at_ms": NOW_MS - HOUR_MS,
            "prices": {"microsoft/mai-transcribe-2": {"per_minute": None}},
        }
    )
    with conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('cache.pricing', ?)", (old,))
    listed, _, source = await answer(PricingService(conn, client, clock))
    assert source == "live"
    assert listed["microsoft/mai-transcribe-2"].per_minute is not None


async def test_a_cache_that_is_garbled_is_ignored(conn, client, clock):
    reading = CACHE_READING
    for value in [
        "not json",
        "[]",
        f'{{"reading": {reading}, "fetched_at_ms": "soon", "models": []}}',
        f'{{"reading": {reading}, "fetched_at_ms": 1, "models": 3}}',
        f'{{"reading": {reading}, "fetched_at_ms": 1, "models": [{{"id": 5}}]}}',
    ]:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('cache.pricing', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (value,),
            )
        client.calls.clear()
        _, _, source = await PricingService(conn, client, clock).catalog()
        assert source == "live", value
        assert len(client.calls) == 1


async def test_calls_at_the_same_time_share_one_fetch(service, client):
    client.delay = 0.05
    results = await asyncio.gather(service.catalog(), service.catalog(), service.catalog())
    assert len(client.calls) == 1
    assert [source for _, _, source in results].count("live") >= 1
    assert all(fetched_at_ms == NOW_MS for _, fetched_at_ms, _ in results)


# --- when OpenRouter cannot be reached ----------------------------------------------------------


async def test_without_a_cache_a_network_failure_gives_the_builtin_prices(service, client):
    client.error = network_down()
    entries, fetched_at_ms, source = await service.catalog()
    assert (source, fetched_at_ms) == ("builtin", None)
    assert [e.id for e in entries if e.kind == "llm"] == CLEANUP_MODEL_CHOICES
    assert [e.id for e in entries if e.kind == "stt"] == list(STT_CLOUD_MODELS)
    listed = {entry.id: entry.price for entry in entries}
    luna = listed["openai/gpt-6-luna"]
    assert (luna.input_per_mtok, luna.output_per_mtok) == (0.1, 0.5)
    assert listed["elevenlabs/scribe-v2"].per_minute == pytest.approx(0.0018333, rel=1e-4)


async def test_a_failure_with_an_expired_cache_gives_the_cached_prices(service, client, clock):
    await service.catalog()
    cheaper = copy.deepcopy(RECORDED)
    next(m for m in cheaper if m["id"] == "openai/gpt-6-luna")["pricing"]["prompt"] = "0.00000005"
    client.reply = cheaper
    clock.now_ms += 3 * PRICING_TTL_MS
    client.error = network_down()
    listed, fetched_at_ms, source = await answer(service)
    assert (source, fetched_at_ms) == ("cached", NOW_MS)
    assert listed["openai/gpt-6-luna"].input_per_mtok == 0.1


async def test_a_failure_with_an_expired_cache_from_before_a_restart(conn, client, clock):
    await PricingService(conn, client, clock).catalog()
    clock.now_ms += 5 * PRICING_TTL_MS
    client.error = network_down()
    _, fetched_at_ms, source = await PricingService(conn, client, clock).catalog()
    assert (source, fetched_at_ms) == ("cached", NOW_MS)


async def test_a_failure_is_tried_again_at_the_next_call(service, client):
    client.error = network_down()
    assert (await service.catalog())[2] == "builtin"
    client.error = None
    assert (await service.catalog())[2] == "live"
    assert len(client.calls) == 2


@pytest.mark.parametrize("kind", ["timeout", "server", "bad_response", "rate_limit", "auth"])
async def test_every_kind_of_failure_falls_back(service, client, kind):
    client.error = OpenRouterError(500, kind, "nope")
    assert (await service.catalog())[2] == "builtin"


async def test_a_fetch_that_hangs_gives_up(conn, client, clock):
    client.delay = 5
    service = PricingService(conn, client, clock, fetch_timeout_s=0.05)
    _, _, source = await asyncio.wait_for(service.catalog(), 2)
    assert source == "builtin"


async def test_a_client_that_breaks_gives_the_fallback_and_logs_only_the_type(
    service, client, caplog
):
    secret = "sk-or-v1-TOPSECRET"
    client.error = RuntimeError(f"the key is {secret}")
    with caplog.at_level(logging.DEBUG):
        _, _, source = await service.catalog()
    assert source == "builtin"
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


async def test_a_client_without_a_model_list_gives_the_fallback(conn, clock):
    class NoModels:
        pass

    _, _, source = await PricingService(conn, NoModels(), clock).catalog()
    assert source == "builtin"


@pytest.mark.parametrize("reply", [[], [{"id": "some/other-model", "pricing": {}}], "oops", None])
async def test_a_list_without_any_model_raphael_could_use_is_no_answer_and_is_not_cached(
    service, client, conn, reply
):
    client.reply = reply
    assert (await service.catalog())[2] == "builtin"
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 0


async def test_a_cache_that_cannot_be_written_does_not_fail_the_answer(conn, client, clock):
    service = PricingService(conn, client, clock)
    conn.execute("DROP TABLE settings")
    assert (await service.catalog())[2] == "live"
    # The list is still kept in memory for the day.
    assert (await service.catalog())[2] == "cached"
    assert len(client.calls) == 1


# --- as RPC methods -----------------------------------------------------------------------------


async def test_the_brain_tells_the_prices_by_models_catalog_alone(conn, client, clock):
    router = Router()
    Brain(conn, client, MemoryKeyStore(), now_ms=clock).register(router)
    assert "models.catalog" in router.methods()
    assert "pricing.get" not in router.methods()
    assert (await router.call("models.catalog", {}))["source"] == "live"
    assert (await router.call("models.catalog", {}))["source"] == "cached"
    assert len(client.calls) == 1
