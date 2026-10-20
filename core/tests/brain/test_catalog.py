"""Tests for the catalog of OpenRouter's models: what Raphael reads of the list, what each role can
use, and how the list is kept (test_model_choice.py has `models.catalog` and choosing a model).

No test touches the network: the client is a FakeModelsClient that answers with the recorded
response in fixtures/openrouter_catalog.json (a trimmed copy of GET /models?output_modalities=all of
2026-10-20 with a model of each sort the catalog tells apart: language models with and without
tool calling, one that always reasons, one without the reasoning parameter, one without
structured output, a router priced at -1, a free model, an alias, a name without its maker;
speech-to-text priced per second, per hour (Microsoft's MAI) and per token; and a batch variant, an
image, an embedding, a speech, a decision model and the auto router, which are left out), or fails
as told. The cache lives in an in-memory database.
"""

import copy
import json
from pathlib import Path

import pytest

from raphael_core.brain import catalog, pricing
from raphael_core.brain.catalog import (
    ROLE_OF_SETTING,
    ROLES,
    CatalogEntry,
    catalog_view,
    choice_problem,
    entry_from_json,
    entry_to_json,
    parse_catalog,
)
from raphael_core.brain.openrouter import CLEANUP_MODEL_CHOICES, OpenRouterError
from raphael_core.brain.pricing import PRICING_TTL_MS, PricingService
from raphael_core.brain.settings import STT_CLOUD_MODELS

FIXTURE = Path(__file__).parent / "fixtures" / "openrouter_catalog.json"
RECORDED: list[dict] = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]
NOW_MS = 1_792_400_000_000
HOUR_MS = 3_600_000

LANGUAGE_MODELS = [
    "openai/gpt-6-luna",
    "anthropic/claude-haiku-5.5",
    "google/gemini-3.1-flash-lite",
    "tencent/hunyuan-a13b-instruct",
    "google/gemini-3.8-flash",
    "openai/gpt-4.1-mini",
    "moondream/moondream3-preview",
    "typesafe/jev-router",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "mistralai/mistral-large-4-0",
    "mistralai/mistral-large-2407",
    "~openai/gpt-luna-latest",
]
SPEECH_MODELS = [
    "elevenlabs/scribe-v2",
    "openai/gpt-transcribe",
    "microsoft/mai-transcribe-2",
    "assemblyai/universal-3-6-pro",
    "qwen/qwen3-asr-1.7b",
    "google/gemini-3.5-transcribe",
    "openai/whisper-large-v3-turbo",
    "microsoft/mai-transcribe-1.5",
    "openai/gpt-4o-mini-transcribe",
    "deepgram/nova-3",
]
LEFT_OUT = [
    "anthropic/claude-haiku-5.5:batch",  # answers within a day: never in time
    "google/gemini-3.1-flash-image",  # writes images too
    "openai/text-embedding-3-small",
    "elevenlabs/eleven-v4",  # text to speech
    "typesafe/jev-1.14",  # decisions: the router, which is no choice
    "openrouter/auto",  # writes images too
]


class FakeModelsClient:
    """Stands in for OpenRouterClient: `models()` answers with `reply`, or raises `error`. Each
    call is counted in `calls`."""

    def __init__(self):
        self.reply = copy.deepcopy(RECORDED)
        self.error = None
        self.calls = 0

    async def models(self, timeout_s=5.0):
        self.calls += 1
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


def entries_by_id(models=None) -> dict[str, CatalogEntry]:
    return {entry.id: entry for entry in parse_catalog(RECORDED if models is None else models)}


def by_id(views):
    return {view["id"]: view for view in views}


def edited(model_id, **changes):
    """The recorded list with `changes` made to the model `model_id`."""
    models = copy.deepcopy(RECORDED)
    next(m for m in models if m["id"] == model_id).update(changes)
    return models


def network_down():
    return OpenRouterError(0, "network", "no route to host")


# --- the kinds ----------------------------------------------------------------------------------


def test_language_and_speech_models_are_kept_in_the_order_of_the_list():
    entries = parse_catalog(RECORDED)
    assert [e.id for e in entries if e.kind == "llm"] == LANGUAGE_MODELS
    assert [e.id for e in entries if e.kind == "stt"] == SPEECH_MODELS


def test_batch_variants_images_embeddings_speech_and_decisions_are_left_out():
    listed = entries_by_id()
    for model_id in LEFT_OUT:
        assert model_id in {m["id"] for m in RECORDED}
        assert model_id not in listed


def test_entries_that_are_no_models_and_ids_a_setting_could_not_hold_are_skipped():
    luna = next(m for m in RECORDED if m["id"] == "openai/gpt-6-luna")
    odd = [
        None,
        7,
        "openai/gpt-6-luna",
        {"id": 5},
        {"no": "id"},
        {**luna, "id": ""},
        {**luna, "id": "two words/model"},
        {**luna, "id": "m" * 101},
    ]
    assert [e.id for e in parse_catalog([*odd, luna, luna])] == ["openai/gpt-6-luna"]


def test_a_model_without_modalities_is_of_no_kind_unless_it_is_one_of_raphaels():
    models = [
        {"id": "openai/gpt-6-luna", "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}},
        {"id": "elevenlabs/scribe-v2", "architecture": {"output_modalities": "transcription"}},
        {"id": "some/model", "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}},
    ]
    kinds = {e.id: e.kind for e in parse_catalog(models)}
    assert kinds == {"openai/gpt-6-luna": "llm", "elevenlabs/scribe-v2": "stt"}


# --- names --------------------------------------------------------------------------------------


def test_the_maker_and_the_name_come_from_the_lists_name():
    luna = entries_by_id()["openai/gpt-6-luna"]
    assert (luna.provider, luna.name) == ("OpenAI", "GPT-6 Luna")
    mai = entries_by_id()["microsoft/mai-transcribe-2"]
    assert (mai.provider, mai.name) == ("Microsoft AI", "MAI-Transcribe 2")


def test_a_name_without_its_maker_takes_the_one_of_the_authors_other_models():
    old = entries_by_id()["mistralai/mistral-large-2407"]
    assert (old.provider, old.name) == ("Mistral", "Mistral Large 2407")


def test_an_alias_is_the_maker_of_the_model_it_stands_for():
    assert entries_by_id()["~openai/gpt-luna-latest"].provider == "OpenAI"


def test_an_author_whose_models_have_no_maker_goes_by_its_slug():
    model = {**next(m for m in RECORDED if m["id"] == "mistralai/mistral-large-2407")}
    model.update(id="unbiased/pareto-26.10", name="Pareto 26.10")
    entry = parse_catalog([model])[0]
    assert (entry.provider, entry.name) == ("Unbiased", "Pareto 26.10")
    nameless = parse_catalog([{**model, "name": None}])[0]
    assert nameless.name == "unbiased/pareto-26.10"


# --- prices -------------------------------------------------------------------------------------


def test_a_language_model_is_priced_per_million_tokens():
    luna = entries_by_id()["openai/gpt-6-luna"]
    assert (luna.price.input_per_mtok, luna.price.output_per_mtok) == (0.1, 0.5)
    assert luna.price.per_minute is None
    assert (luna.free, luna.variable_price) == (False, False)


def test_speech_to_text_priced_per_second_is_shown_per_minute():
    listed = entries_by_id()
    # 0.0000611111111111 US dollars a second; a minute is 60 of them.
    assert listed["elevenlabs/scribe-v2"].price.per_minute == pytest.approx(0.00366667)
    assert listed["deepgram/nova-3"].price.per_minute == pytest.approx(0.0043)
    assert listed["deepgram/nova-3"].price.input_per_mtok is None


def test_microsofts_mai_models_are_read_per_hour():
    listed = entries_by_id()
    # "0.1" and "0.36": US$0.10 and US$0.36 an hour, not US$6 and US$21.60 a minute.
    assert listed["microsoft/mai-transcribe-2"].price.per_minute == pytest.approx(0.1 / 60, 1e-5)
    assert listed["microsoft/mai-transcribe-1.5"].price.per_minute == pytest.approx(0.006)


def test_speech_to_text_priced_per_token_has_token_prices_and_no_minute_price():
    listed = entries_by_id()
    gemini = listed["google/gemini-3.5-transcribe"].price
    assert (gemini.input_per_mtok, gemini.output_per_mtok, gemini.per_minute) == (2.0, 12.0, None)
    mini = listed["openai/gpt-4o-mini-transcribe"].price
    assert (mini.input_per_mtok, mini.output_per_mtok) == (1.25, 5.0)


def test_a_router_priced_at_minus_one_has_a_variable_price_and_no_price():
    router = entries_by_id()["typesafe/jev-router"]
    assert router.variable_price is True
    assert router.price == catalog.ModelPrice()
    assert router.free is False


def test_a_model_that_costs_nothing_is_free():
    free = entries_by_id()["nvidia/nemotron-3-super-120b-a12b:free"]
    assert free.free is True
    assert (free.price.input_per_mtok, free.price.output_per_mtok) == (0.0, 0.0)
    nothing = {"prompt": "0", "completion": "0"}
    scribe = entries_by_id(edited("elevenlabs/scribe-v2", pricing=nothing))
    assert scribe["elevenlabs/scribe-v2"].free is True


def test_a_price_that_is_missing_is_unknown_and_not_free():
    luna = entries_by_id(edited("openai/gpt-6-luna", pricing={"prompt": "0"}))["openai/gpt-6-luna"]
    assert (luna.price.input_per_mtok, luna.price.output_per_mtok) == (0.0, None)
    assert (luna.free, luna.variable_price) == (False, False)


# --- what the roles can use ---------------------------------------------------------------------


def usable(model_id, role, models=None) -> str | None:
    """The reason `role` cannot use `model_id`, or None when it can."""
    return catalog.refusal(entries_by_id(models)[model_id], ROLE_OF_SETTING[role])


@pytest.mark.parametrize("role", ["cleanup_model", "agent_model"])
def test_raphaels_own_language_models_serve_both_roles(role):
    for model_id in CLEANUP_MODEL_CHOICES:
        assert usable(model_id, role) is None


@pytest.mark.parametrize("role", ["cleanup_model", "agent_model"])
def test_tool_calling_is_not_needed(role):
    hunyuan = next(m for m in RECORDED if m["id"] == "tencent/hunyuan-a13b-instruct")
    assert "tools" not in hunyuan["supported_parameters"]
    assert usable("tencent/hunyuan-a13b-instruct", role) is None


@pytest.mark.parametrize(
    ("model_id", "reason"),
    [
        ("google/gemini-3.8-flash", "reasoning_required"),
        ("openai/gpt-4.1-mini", "no_reasoning_setting"),
        ("moondream/moondream3-preview", "no_structured_output"),
        ("mistralai/mistral-large-2407", "no_reasoning_setting"),
    ],
)
def test_a_model_without_what_the_json_calls_need_says_why(model_id, reason):
    assert usable(model_id, "cleanup_model") == reason
    assert usable(model_id, "agent_model") == reason


def test_a_model_without_a_token_limit_cannot_answer_in_json():
    luna = next(m for m in RECORDED if m["id"] == "openai/gpt-6-luna")
    parameters = [p for p in luna["supported_parameters"] if p != "max_tokens"]
    models = edited("openai/gpt-6-luna", supported_parameters=parameters)
    assert usable("openai/gpt-6-luna", "cleanup_model", models) == "no_max_tokens"


def test_efforts_without_none_mean_that_the_model_always_reasons():
    reasoning = {"mandatory": False, "supported_efforts": ["high", "low"]}
    models = edited("openai/gpt-6-luna", reasoning=reasoning)
    assert usable("openai/gpt-6-luna", "agent_model", models) == "reasoning_required"


def test_a_router_and_a_free_model_and_an_alias_can_answer():
    for model_id in [
        "typesafe/jev-router",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "~openai/gpt-luna-latest",
    ]:
        assert usable(model_id, "cleanup_model") is None


def test_every_speech_model_can_transcribe_and_two_get_the_dictionary():
    listed = entries_by_id()
    for model_id in SPEECH_MODELS:
        assert usable(model_id, "stt_cloud_model") is None
    hinted = {m for m in SPEECH_MODELS if "dictionary_hints" in listed[m].capabilities}
    assert hinted == {"openai/gpt-transcribe", "openai/whisper-large-v3-turbo"}


def test_a_model_of_the_other_kind_is_never_usable():
    listed = entries_by_id()
    stt = ROLE_OF_SETTING["stt_cloud_model"]
    assert catalog.refusal(listed["openai/gpt-6-luna"], stt) == "wrong_kind"
    cleanup = ROLE_OF_SETTING["cleanup_model"]
    assert catalog.refusal(listed["elevenlabs/scribe-v2"], cleanup) == "wrong_kind"


# --- the answer ---------------------------------------------------------------------------------


def test_the_answer_has_the_models_by_kind_and_where_they_came_from():
    view = catalog_view(parse_catalog(RECORDED), NOW_MS, "live")
    assert set(view) == {"llm", "stt", "fetched_at_ms", "source", "replaced"}
    assert [m["id"] for m in view["llm"]] == LANGUAGE_MODELS
    assert [m["id"] for m in view["stt"]] == SPEECH_MODELS
    assert (view["fetched_at_ms"], view["source"], view["replaced"]) == (NOW_MS, "live", [])
    assert json.loads(json.dumps(view)) == view  # the router sends it as JSON


def test_an_entry_has_what_the_picker_shows():
    view = catalog_view(parse_catalog(RECORDED), NOW_MS, "live")
    luna = by_id(view["llm"])["openai/gpt-6-luna"]
    assert luna == {
        "id": "openai/gpt-6-luna",
        "name": "GPT-6 Luna",
        "provider": "OpenAI",
        "input_per_mtok": 0.1,
        "output_per_mtok": 0.5,
        "per_minute": None,
        "free": False,
        "variable_price": False,
        "context_length": 1_050_000,
        "created_at_ms": 1_790_100_786_000,
        "capabilities": {
            "structured_output": True,
            "reasoning": True,
            "reasoning_off": True,
            "max_tokens": True,
        },
        "recommended": True,
        "roles": {
            "cleanup": {"usable": True, "reason": None},
            "answer": {"usable": True, "reason": None},
        },
    }
    scribe = by_id(view["stt"])["elevenlabs/scribe-v2"]
    assert scribe["context_length"] is None
    assert scribe["capabilities"] == {"dictionary_hints": False}
    assert scribe["roles"] == {"stt": {"usable": True, "reason": None}}
    flash = by_id(view["llm"])["google/gemini-3.8-flash"]
    assert flash["roles"]["cleanup"] == {"usable": False, "reason": "reasoning_required"}


def test_raphaels_own_models_are_recommended_and_no_other():
    view = catalog_view(parse_catalog(RECORDED), NOW_MS, "live")
    recommended = {m["id"] for m in [*view["llm"], *view["stt"]] if m["recommended"]}
    assert recommended == {*CLEANUP_MODEL_CHOICES, *STT_CLOUD_MODELS}


def test_an_entry_is_kept_and_read_back_as_it_was():
    for entry in parse_catalog(RECORDED):
        assert entry_from_json(json.loads(json.dumps(entry_to_json(entry)))) == entry


@pytest.mark.parametrize(
    "garbled",
    [
        None,
        [],
        {"id": "a/b", "kind": "image", "name": "B", "provider": "A"},
        {"id": "a/b", "kind": "llm", "name": "", "provider": "A", "variable_price": False},
        {"id": "a b", "kind": "llm", "name": "B", "provider": "A", "variable_price": False},
        {"id": "a/b", "kind": "llm", "name": "B", "provider": "A", "variable_price": 0},
        {"id": "a/b", "kind": "llm", "name": "B", "provider": "A", "variable_price": False},
    ],
)
def test_a_kept_entry_that_is_garbled_is_no_entry(garbled):
    assert entry_from_json(garbled) is None


# --- the cache ----------------------------------------------------------------------------------


async def test_the_catalog_survives_a_restart(conn, client, clock):
    await PricingService(conn, client, clock).catalog()
    clock.now_ms += HOUR_MS
    entries, fetched_at_ms, source = await PricingService(conn, client, clock).catalog()
    assert (source, fetched_at_ms, client.calls) == ("cached", NOW_MS, 1)
    assert [e.id for e in entries] == [e.id for e in parse_catalog(RECORDED)]


BATCH_ENTRY = {
    "id": "anthropic/claude-haiku-5.5:batch",
    "kind": "llm",
    "name": "Claude Haiku 5.5 (batch)",
    "provider": "Anthropic",
    "per_minute": None,
    "input_per_mtok": 0.05,
    "output_per_mtok": 0.25,
    "variable_price": False,
    "context_length": 1_000_000,
    "created_at_ms": 1_791_397_883_000,
    "capabilities": ["max_tokens", "reasoning", "reasoning_off", "structured_output"],
}


@pytest.mark.parametrize(
    "old",
    [
        # Reading 2 kept the prices of Raphael's own models, and nothing else.
        {"reading": 2, "prices": {"openai/gpt-6-luna": {"input_per_mtok": 0.1}}},
        # Reading 3 kept every model, the batch variants among them.
        {"reading": 3, "models": [BATCH_ENTRY]},
    ],
)
async def test_a_cache_of_an_older_reading_is_fetched_again(conn, client, clock, old):
    assert old["reading"] < pricing.CACHE_READING
    kept = json.dumps({**old, "fetched_at_ms": NOW_MS - HOUR_MS})
    with conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('cache.pricing', ?)", (kept,))
    entries, _, source = await PricingService(conn, client, clock).catalog()
    assert (source, client.calls) == ("live", 1)
    assert not any(entry.id.endswith(":batch") for entry in entries)


async def test_without_the_list_the_catalog_is_raphaels_own_models(conn, client, clock):
    client.error = network_down()
    entries, fetched_at_ms, source = await PricingService(conn, client, clock).catalog()
    assert (source, fetched_at_ms) == ("builtin", None)
    view = catalog_view(entries, fetched_at_ms, source)
    assert [m["id"] for m in view["llm"]] == CLEANUP_MODEL_CHOICES
    assert [m["id"] for m in view["stt"]] == list(STT_CLOUD_MODELS)
    for model in [*view["llm"], *view["stt"]]:
        assert model["recommended"] is True
        assert all(role["usable"] for role in model["roles"].values())
    luna = by_id(view["llm"])["openai/gpt-6-luna"]
    assert (luna["provider"], luna["name"], luna["input_per_mtok"]) == ("OpenAI", "GPT-6 Luna", 0.1)
    assert by_id(view["stt"])["openai/gpt-transcribe"]["capabilities"]["dictionary_hints"] is True


async def test_a_stale_catalog_is_the_answer_when_the_list_cannot_be_had(conn, client, clock):
    service = PricingService(conn, client, clock)
    await service.catalog()
    clock.now_ms += 2 * PRICING_TTL_MS
    client.error = network_down()
    entries, fetched_at_ms, source = await service.catalog()
    assert (source, fetched_at_ms) == ("cached", NOW_MS)
    assert [e.id for e in entries if e.kind == "llm"] == LANGUAGE_MODELS
    assert client.calls == 2


async def test_what_is_known_is_read_without_a_fetch(conn, client, clock):
    assert PricingService(conn, client, clock).known() is None
    await PricingService(conn, client, clock).catalog()
    clock.now_ms += 3 * PRICING_TTL_MS
    known = PricingService(conn, client, clock).known()
    assert [e.id for e in known] == [e.id for e in parse_catalog(RECORDED)]
    assert client.calls == 1


# --- choosing -----------------------------------------------------------------------------------


def test_a_choice_is_checked_against_the_catalog_or_without_one_against_raphaels_own_models():
    cleanup, stt = ROLE_OF_SETTING["cleanup_model"], ROLE_OF_SETTING["stt_cloud_model"]
    entries = parse_catalog(RECORDED)
    assert choice_problem(cleanup, "tencent/hunyuan-a13b-instruct", None, "x") == "not_curated"
    assert choice_problem(cleanup, "anthropic/claude-haiku-5.5", None, "x") is None
    assert choice_problem(cleanup, "tencent/hunyuan-a13b-instruct", entries, "x") is None
    assert choice_problem(cleanup, "google/gemini-3.8-flash", entries, "x") == "reasoning_required"
    assert choice_problem(cleanup, "nobody/model", entries, "x") == "not_listed"
    assert choice_problem(stt, "openai/gpt-6-luna", entries, "x") == "not_listed"
    assert choice_problem(stt, "deepgram/nova-3", entries, "x") is None


def test_the_model_chosen_now_and_the_default_may_always_be_chosen():
    cleanup, stt = ROLE_OF_SETTING["cleanup_model"], ROLE_OF_SETTING["stt_cloud_model"]
    entries = parse_catalog(RECORDED)
    assert choice_problem(cleanup, "nobody/model", entries, "nobody/model") is None
    assert choice_problem(cleanup, "nobody/model", None, "nobody/model") is None
    assert choice_problem(cleanup, "openai/gpt-6-luna", [], "x") is None
    assert choice_problem(stt, "elevenlabs/scribe-v2", None, "x") is None
