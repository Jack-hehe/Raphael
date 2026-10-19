"""Tests for the OpenRouter client.

Every request goes to an httpx.MockTransport, so no test touches the network.
"""

import asyncio
import json
import logging
import time

import httpx
import keyring.errors
import pytest

from raphael_core.brain import openrouter
from raphael_core.brain.openrouter import (
    CLEANUP_FALLBACKS,
    CLEANUP_MODEL_CHOICES,
    DEFAULT_CLEANUP_MODEL,
    Completion,
    KeyInfo,
    OpenRouterClient,
    OpenRouterError,
)
from raphael_core.brain.settings import DEFAULTS

KEY = "sk-or-v1-test"
SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


@pytest.fixture
async def client_with():
    """`client_with(handler, key=KEY)` is a client whose HTTP goes to `handler`.

    `key` is the key, or a function that returns it. The httpx clients close when the test ends.
    """
    opened: list[httpx.AsyncClient] = []

    def make(handler, key=KEY):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        opened.append(http)
        return OpenRouterClient(key if callable(key) else (lambda: key), http=http)

    yield make
    for http in opened:
        await http.aclose()


def ok_reply(content: dict, cost=0.0002):
    return httpx.Response(
        200,
        json={
            "model": "google/gemini-3.1-flash-lite",
            "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "cost": cost},
        },
    )


def chat_reply(content):
    """A 200 reply whose first choice holds `content`, whatever that is."""
    choice = {"message": {"content": content}, "finish_reason": "stop"}
    return httpx.Response(200, json={"choices": [choice]})


def key_reply():
    """A 200 reply to GET /key."""
    return httpx.Response(200, json={"data": {"label": "Raphael"}})


def answering(reply, seen):
    """A handler that records each request in `seen` and answers with `reply()`."""

    def handler(request):
        seen.append(request)
        return reply()

    return handler


async def complete(client, **overrides):
    """`client.complete_json` with typical arguments; override any of them by keyword."""
    args = {
        "model": "openai/gpt-6-luna",
        "fallbacks": CLEANUP_FALLBACKS,
        "system": "s",
        "user": "u",
        "schema": SCHEMA,
        "schema_name": "cleaned_dictation",
        "timeout_s": 3,
        "max_tokens": 256,
    }
    return await client.complete_json(**{**args, **overrides})


def key_info_of(client):
    return client.key_info()


def openrouter_warnings(caplog):
    """The warnings the client has logged, as text."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == openrouter.logger.name and r.levelno == logging.WARNING
    ]


# Both calls need a key and fail in the same ways, so some tests run against each.
EACH_CALL = pytest.mark.parametrize(
    "call", [complete, key_info_of], ids=["complete_json", "key_info"]
)


# --- the request ------------------------------------------------------------------------------


async def test_complete_json_sends_schema_and_fallbacks(client_with):
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        seen["auth"] = req.headers["authorization"]
        return ok_reply({"text": "好。"})

    c = await client_with(handler).complete_json(
        model="openai/gpt-6-luna",
        fallbacks=CLEANUP_FALLBACKS,
        system="s",
        user="u",
        schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        schema_name="cleaned_dictation",
        timeout_s=3,
        max_tokens=256,
    )
    assert c.data == {"text": "好。"}
    assert c.cost_usd == 0.0002
    assert c.prompt_tokens == 120
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["strict"] is True
    assert seen["models"] == CLEANUP_FALLBACKS
    assert seen["provider"]["require_parameters"] is True
    assert seen["reasoning"] == {"effort": "none"}
    assert "temperature" not in seen
    assert seen["auth"] == "Bearer sk-or-v1-test"


async def test_the_request_body_is_exactly_the_documented_one(client_with):
    # require_parameters drops every model that lacks one of the request's parameters, so an
    # extra field (temperature, top_p) could leave no model to answer. Nothing else may be sent.
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return ok_reply({"text": "x"})

    await complete(
        client_with(handler),
        fallbacks=["fallback/one", "fallback/two"],
        system="只整理文字。",
        user="<transcript>\n嗯 明天開會\n</transcript>",
        max_tokens=256,
    )
    assert seen == {
        "model": "openai/gpt-6-luna",
        "models": ["fallback/one", "fallback/two"],
        "messages": [
            {"role": "system", "content": "只整理文字。"},
            {"role": "user", "content": "<transcript>\n嗯 明天開會\n</transcript>"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "cleaned_dictation", "strict": True, "schema": SCHEMA},
        },
        "provider": {"require_parameters": True, "sort": "latency", "data_collection": "deny"},
        "reasoning": {"effort": "none"},
        "max_tokens": 256,
    }


async def test_the_cleanup_request_denies_data_collection_beside_its_routing_options(client_with):
    # The dictated text goes only to providers that do not keep it, as the audio of a
    # transcription does; the routing options the request already had stay.
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return ok_reply({"text": "x"})

    await complete(client_with(handler))
    assert seen["provider"] == {
        "require_parameters": True,
        "sort": "latency",
        "data_collection": "deny",
    }
    seen.clear()
    await complete(client_with(handler), fallbacks=[])
    assert seen["provider"]["data_collection"] == "deny" and "models" not in seen


async def test_a_completion_is_posted_to_chat_completions_with_the_app_headers(client_with):
    seen = []
    await complete(client_with(answering(lambda: ok_reply({"text": "x"}), seen)))
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-or-v1-test"
    assert request.headers["http-referer"] == "http://localhost/raphael"
    assert request.headers["x-openrouter-title"] == "Raphael"


async def test_key_info_gets_the_key_endpoint_with_the_app_headers(client_with):
    seen = []
    await client_with(answering(key_reply, seen)).key_info()
    request = seen[0]
    assert request.method == "GET"
    assert str(request.url) == "https://openrouter.ai/api/v1/key"
    assert request.headers["authorization"] == "Bearer sk-or-v1-test"
    assert request.headers["http-referer"] == "http://localhost/raphael"
    assert request.headers["x-openrouter-title"] == "Raphael"


async def test_the_timeout_applies_to_the_request(client_with):
    seen = []
    await complete(client_with(answering(lambda: ok_reply({"text": "x"}), seen)), timeout_s=2.5)
    assert seen[0].extensions["timeout"] == {"connect": 2.5, "read": 2.5, "write": 2.5, "pool": 2.5}


@pytest.mark.parametrize(("kwargs", "timeout"), [({}, 5.0), ({"timeout_s": 1.5}, 1.5)])
async def test_key_info_timeout_defaults_to_five_seconds(client_with, kwargs, timeout):
    seen = []
    await client_with(answering(key_reply, seen)).key_info(**kwargs)
    assert seen[0].extensions["timeout"] == {
        "connect": timeout,
        "read": timeout,
        "write": timeout,
        "pool": timeout,
    }


async def test_without_fallbacks_the_request_has_no_models_field(client_with):
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return ok_reply({"text": "x"})

    await complete(client_with(handler), fallbacks=[])
    assert "models" not in seen


@pytest.mark.parametrize(
    ("model", "fallbacks", "sent"),
    [
        ("b", ["a", "b", "c"], ["a", "c"]),
        ("a", ["b", "c"], ["b", "c"]),
        ("a", ["a"], None),
    ],
    ids=["dropped from the middle", "not among them", "the only one"],
)
async def test_the_chosen_model_is_not_tried_again_as_a_fallback(
    client_with, model, fallbacks, sent
):
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return ok_reply({"text": "x"})

    await complete(client_with(handler), model=model, fallbacks=fallbacks)
    assert seen.get("models") == sent


# --- the key ----------------------------------------------------------------------------------


@EACH_CALL
@pytest.mark.parametrize("key", [None, "", "   "])
async def test_without_a_key_nothing_is_sent(client_with, call, key):
    seen = []
    client = client_with(answering(lambda: ok_reply({"text": "x"}), seen), key=key)
    with pytest.raises(OpenRouterError) as e:
        await call(client)
    assert (e.value.status, e.value.kind, e.value.message) == (0, "auth", "No OpenRouter key")
    assert seen == []


@EACH_CALL
@pytest.mark.parametrize(
    "key",
    [
        "sk-or-v1-" + chr(0x200B) + "abc",  # a zero-width space, as copied from a web page
        "sk-or-v1-abc\ndef",
        "sk-or-v1 abc",
        "sk-or-v1-密鑰",
    ],
    ids=["zero-width space", "line break", "space", "non-ASCII"],
)
async def test_a_key_that_cannot_go_in_a_header_is_an_auth_error_and_nothing_is_sent(
    client_with, call, key
):
    seen = []
    client = client_with(answering(lambda: ok_reply({"text": "x"}), seen), key=key)
    with pytest.raises(OpenRouterError) as e:
        await call(client)
    assert (e.value.status, e.value.kind) == (0, "auth")
    assert seen == []


async def test_whitespace_around_a_key_is_ignored(client_with):
    seen = []
    client = client_with(answering(lambda: ok_reply({"text": "x"}), seen), key="  sk-or-v1-test\n")
    await complete(client)
    assert seen[0].headers["authorization"] == "Bearer sk-or-v1-test"


async def test_the_key_is_read_again_for_every_request(client_with):
    keys = ["sk-or-v1-first", "sk-or-v1-second"]
    seen = []
    client = client_with(answering(lambda: ok_reply({"text": "x"}), seen), key=lambda: keys.pop(0))
    await complete(client)
    await complete(client)
    assert [r.headers["authorization"] for r in seen] == [
        "Bearer sk-or-v1-first",
        "Bearer sk-or-v1-second",
    ]


FAILING_PROVIDERS = {
    "RuntimeError": lambda: RuntimeError("vault locked"),
    "OSError": lambda: OSError(5, "Access is denied"),
    "KeyringLocked": lambda: keyring.errors.KeyringLocked("the vault is locked"),
    "NoKeyringError": lambda: keyring.errors.NoKeyringError("no keyring backend"),
}


@EACH_CALL
@pytest.mark.parametrize(
    "make_error", list(FAILING_PROVIDERS.values()), ids=list(FAILING_PROVIDERS)
)
async def test_a_key_provider_that_fails_is_an_auth_error_and_nothing_is_sent(
    client_with, call, make_error
):
    # The cleanup service falls back to the raw text on an OpenRouterError, and only on that.
    error = make_error()

    def failing():
        raise error

    seen = []
    client = client_with(answering(lambda: ok_reply({"text": "x"}), seen), key=failing)
    with pytest.raises(OpenRouterError) as e:
        await call(client)
    assert (e.value.status, e.value.kind) == (0, "auth")
    assert e.value.message == "Could not read the OpenRouter key"
    assert e.value.__cause__ is error
    assert seen == []


async def test_key_info_with_a_key_does_not_ask_the_key_provider(client_with):
    def failing():
        raise RuntimeError("vault locked")

    seen = []
    client = client_with(answering(key_reply, seen), key=failing)
    info = await client.key_info(key="sk-or-v1-candidate")
    assert info.label == "Raphael"
    assert seen[0].headers["authorization"] == "Bearer sk-or-v1-candidate"


async def test_a_key_provider_failure_is_logged_by_exception_type_only(client_with, caplog):
    caplog.set_level(logging.DEBUG)

    def failing():
        raise RuntimeError("vault locked, token sk-or-v1-LEAK")

    with pytest.raises(OpenRouterError):
        await complete(client_with(lambda r: ok_reply({"text": "x"}), key=failing))

    logged = [r.getMessage() for r in caplog.records if r.name == openrouter.logger.name]
    assert any("RuntimeError" in line and "auth" in line for line in logged)
    assert "vault locked" not in caplog.text
    assert "sk-or-v1-LEAK" not in caplog.text


async def test_key_info_checks_the_key_it_is_given_not_the_stored_one(client_with):
    seen = []
    client = client_with(answering(key_reply, seen), key="sk-or-v1-stored")
    await client.key_info(key="sk-or-v1-candidate")
    await client.key_info()
    assert [r.headers["authorization"] for r in seen] == [
        "Bearer sk-or-v1-candidate",
        "Bearer sk-or-v1-stored",
    ]


async def test_a_blank_candidate_key_does_not_fall_back_to_the_stored_one(client_with):
    # Otherwise "is this blank key valid?" would be answered by checking the stored key: yes.
    seen = []
    client = client_with(answering(key_reply, seen), key="sk-or-v1-stored")
    with pytest.raises(OpenRouterError) as e:
        await client.key_info(key="")
    assert e.value.kind == "auth"
    assert seen == []


# --- the reply --------------------------------------------------------------------------------


async def test_a_completion_reports_the_model_that_answered_with_its_tokens_and_cost(client_with):
    reply = {
        "model": "openai/gpt-6-luna",
        "choices": [{"message": {"content": '{"text": "ok"}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 9, "cost": 0.00031},
    }
    c = await complete(client_with(lambda r: httpx.Response(200, json=reply)))
    assert c.data == {"text": "ok"}
    assert c.model == "openai/gpt-6-luna"
    assert (c.prompt_tokens, c.completion_tokens) == (80, 9)
    assert c.cost_usd == 0.00031


async def test_the_line_breaks_a_model_writes_as_json_escapes_are_real_line_breaks(client_with):
    # A list reaches the app as "\n" inside the JSON string the model writes.
    content = r'{"text": "有兩件事：\n\n1. 甲。\n2. 乙。"}'
    c = await complete(client_with(lambda r: chat_reply(content)))
    assert c.data == {"text": "有兩件事：\n\n1. 甲。\n2. 乙。"}


def test_completion_fields_are_in_the_documented_order():
    c = Completion({"text": "x"}, "m", 0.5, 11, 22, 33)
    assert c.data == {"text": "x"}
    assert c.model == "m"
    assert c.cost_usd == 0.5
    assert c.prompt_tokens == 11
    assert c.completion_tokens == 22
    assert c.latency_ms == 33


@pytest.mark.parametrize(
    "usage",
    [
        pytest.param(None, id="usage is null"),
        pytest.param({}, id="usage is empty"),
        pytest.param({"prompt_tokens": None, "completion_tokens": None, "cost": None}, id="nulls"),
    ],
)
async def test_missing_usage_counts_as_zero(client_with, caplog, usage):
    reply = {"choices": [{"message": {"content": '{"text": "ok"}'}, "finish_reason": "stop"}]}
    reply["usage"] = usage
    c = await complete(client_with(lambda r: httpx.Response(200, json=reply)))
    assert (c.cost_usd, c.prompt_tokens, c.completion_tokens) == (0.0, 0, 0)
    assert openrouter_warnings(caplog) == []  # a reply need not carry a cost


async def test_a_normal_reply_logs_nothing(client_with, caplog):
    await complete(client_with(lambda r: ok_reply({"text": "x"})))
    assert openrouter_warnings(caplog) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cost", "NOTANUMBER-7"),
        ("cost", {"marker": "NOTANUMBER-7"}),
        ("cost", True),
        ("prompt_tokens", "NOTANUMBER-7"),
        ("prompt_tokens", ["NOTANUMBER-7"]),
        ("prompt_tokens", 12.5),
        ("completion_tokens", "NOTANUMBER-7"),
        ("completion_tokens", True),
    ],
    ids=[
        "cost is a string",
        "cost is an object",
        "cost is a bool",
        "prompt tokens is a string",
        "prompt tokens is a list",
        "prompt tokens is fractional",
        "completion tokens is a string",
        "completion tokens is a bool",
    ],
)
async def test_a_usage_field_that_is_no_number_counts_as_zero_and_is_logged(
    client_with, caplog, field, value
):
    # A cost that silently reads as 0 would keep the monthly limit from ever being reached.
    usage = {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.0002}
    usage[field] = value
    reply = {
        "choices": [{"message": {"content": '{"text": "ok"}'}, "finish_reason": "stop"}],
        "usage": usage,
    }
    c = await complete(client_with(lambda r: httpx.Response(200, json=reply)))

    expected = {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.0002}
    expected[field] = 0
    assert c.cost_usd == expected["cost"]
    assert c.prompt_tokens == expected["prompt_tokens"]
    assert c.completion_tokens == expected["completion_tokens"]
    warned = openrouter_warnings(caplog)
    assert len(warned) == 1
    assert field in warned[0]
    assert "NOTANUMBER" not in caplog.text  # the field is named, the value is not


async def test_a_reply_without_usage_or_model_still_completes(client_with):
    reply = {"choices": [{"message": {"content": '{"text": "ok"}'}, "finish_reason": "stop"}]}
    c = await complete(client_with(lambda r: httpx.Response(200, json=reply)), model="asked/for")
    assert c.data == {"text": "ok"}
    assert c.model == "asked/for"
    assert (c.cost_usd, c.prompt_tokens, c.completion_tokens) == (0.0, 0, 0)


async def test_latency_is_the_time_the_http_call_took(client_with):
    async def slow(request):
        await asyncio.sleep(0.1)
        return ok_reply({"text": "x"})

    c = await complete(client_with(slow))
    # Milliseconds: not seconds (0), not microseconds (100000). The clock can be coarse.
    assert 50 <= c.latency_ms < 5000


BAD_REPLIES = {
    "a web page instead of json": lambda: httpx.Response(200, text="<html>Sign in to wifi</html>"),
    "json that is not an object": lambda: httpx.Response(200, json=[1, 2]),
    "no choices": lambda: httpx.Response(200, json={"id": "gen-1"}),
    "empty choices": lambda: httpx.Response(200, json={"choices": []}),
    "choice without a message": lambda: httpx.Response(
        200, json={"choices": [{"finish_reason": "stop"}]}
    ),
    "content is null": lambda: chat_reply(None),
    "content is not json": lambda: chat_reply("Sure! Here is the cleaned text."),
    "content is cut off": lambda: chat_reply('{"text": "明天'),
    "content is json but not an object": lambda: chat_reply("[1, 2]"),
}


@pytest.mark.parametrize("reply", list(BAD_REPLIES.values()), ids=list(BAD_REPLIES))
async def test_a_reply_that_is_not_a_usable_completion_is_a_bad_response(client_with, reply):
    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(lambda r: reply()))
    assert (e.value.status, e.value.kind) == (200, "bad_response")


@pytest.mark.parametrize("content", ['{"text": "par', '{"text": "fine"}'], ids=["cut off", "valid"])
async def test_a_choice_that_ended_in_error_is_a_server_error(client_with, content):
    choice = {
        "message": {"content": content},
        "finish_reason": "error",
        "error": {"code": 502, "message": "Provider disconnected"},
    }
    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(lambda r: httpx.Response(200, json={"choices": [choice]})))
    assert (e.value.status, e.value.kind, e.value.message) == (
        502,
        "server",
        "Provider disconnected",
    )


async def test_a_choice_that_ended_in_error_without_details_is_still_a_server_error(client_with):
    choice = {"message": {"content": ""}, "finish_reason": "error"}
    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(lambda r: httpx.Response(200, json={"choices": [choice]})))
    assert (e.value.status, e.value.kind) == (200, "server")
    assert e.value.message


# --- failures ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (401, {"error": {"code": 401, "message": "User not found."}}, "auth"),
        (402, {"error": {"code": 402, "message": "Insufficient credits."}}, "credits"),
        (
            403,
            {
                "error": {
                    "code": 403,
                    "message": "Budget",
                    "metadata": {"limit_source": "openrouter_key_limit"},
                }
            },
            "credits",
        ),
        (429, {"error": {"code": 429, "message": "Rate limit exceeded"}}, "rate_limit"),
        (200, {"error": {"code": 502, "message": "provider failed"}}, "server"),
    ],
)
async def test_error_kinds(client_with, status, body, kind):
    with pytest.raises(OpenRouterError) as e:
        await client_with(lambda r: httpx.Response(status, json=body)).complete_json(
            model="m",
            fallbacks=[],
            system="s",
            user="u",
            schema={},
            schema_name="x",
            timeout_s=3,
            max_tokens=10,
        )
    assert e.value.kind == kind


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (400, {"error": {"code": 400, "message": "Invalid response_format"}}, "bad_request"),
        (
            403,
            {"error": {"code": 403, "message": "Flagged", "metadata": {"reasons": ["violence"]}}},
            "bad_request",
        ),
        (
            403,
            {"error": {"code": 403, "message": "Blocked", "metadata": "see docs"}},
            "bad_request",
        ),
        (
            403,
            {"error": {"code": 403, "message": "Blocked", "metadata": {"limit_source": ""}}},
            "bad_request",
        ),
        (404, {"error": {"code": 404, "message": "No endpoints found"}}, "bad_request"),
        (408, {"error": {"code": 408, "message": "Your request timed out"}}, "timeout"),
        (500, {"error": {"code": 500, "message": "Internal error"}}, "server"),
        (502, {"error": {"code": 502, "message": "Your chosen model is down"}}, "server"),
        (503, {"error": {"code": 503, "message": "No available model provider"}}, "server"),
        (
            200,
            {
                "error": {"code": 502, "message": "provider failed"},
                "choices": [{"message": {"content": '{"text": "x"}'}, "finish_reason": "stop"}],
            },
            "server",
        ),
    ],
    ids=[
        "400",
        "403 moderation",
        "403 metadata is not an object",
        "403 empty limit_source",
        "404",
        "408",
        "500",
        "502",
        "503",
        "200 error beside a usable choice",
    ],
)
async def test_other_statuses_and_bodies_map_to_kinds(client_with, status, body, kind):
    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(lambda r: httpx.Response(status, json=body)))
    assert e.value.kind == kind


async def test_an_error_carries_the_status_and_message_of_the_reply(client_with):
    body = {"error": {"code": 402, "message": "Insufficient credits. Add more to continue."}}
    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(lambda r: httpx.Response(402, json=body)))
    assert e.value.status == 402
    assert e.value.message == "Insufficient credits. Add more to continue."
    assert str(e.value) == "Insufficient credits. Add more to continue."


@pytest.mark.parametrize(
    "message", [None, "", 42, {"detail": "x"}], ids=["null", "empty", "number", "object"]
)
async def test_an_error_message_that_is_not_text_is_replaced(client_with, message):
    body = {"error": {"code": 500, "message": message}}
    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(lambda r: httpx.Response(500, json=body)))
    assert e.value.message == "HTTP 500"
    assert str(e.value) == "HTTP 500"


async def test_an_error_inside_a_200_reply_carries_the_code_of_the_error(client_with):
    body = {"error": {"code": 502, "message": "Provider returned error"}}
    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(lambda r: httpx.Response(200, json=body)))
    assert e.value.status == 502
    assert e.value.kind == "server"
    assert e.value.message == "Provider returned error"


ERROR_PAGES = {
    "html": lambda status: httpx.Response(status, text="<html>upstream error</html>"),
    "json without an error object": lambda status: httpx.Response(status, json={"detail": "nope"}),
    "empty": lambda status: httpx.Response(status, content=b""),
}


@pytest.mark.parametrize(
    ("status", "kind"),
    [(400, "bad_request"), (401, "auth"), (402, "credits"), (429, "rate_limit"), (504, "server")],
)
@pytest.mark.parametrize("page", list(ERROR_PAGES.values()), ids=list(ERROR_PAGES))
async def test_an_error_status_without_an_error_object_maps_by_status(
    client_with, status, kind, page
):
    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(lambda r: page(status)))
    assert (e.value.status, e.value.kind, e.value.message) == (status, kind, f"HTTP {status}")


async def test_a_redirect_is_a_bad_response(client_with):
    reply = httpx.Response(302, headers={"location": "https://wifi.example/login"})
    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(lambda r: reply))
    assert (e.value.status, e.value.kind) == (302, "bad_response")


@pytest.mark.parametrize(
    "exc_type",
    [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout],
)
async def test_httpx_timeouts_are_timeout_errors(client_with, exc_type):
    def handler(req):
        raise exc_type("", request=req)  # httpx often gives timeouts no message at all

    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(handler))
    assert (e.value.status, e.value.kind) == (0, "timeout")
    assert e.value.message


async def test_network_failure(client_with):
    def handler(req):
        raise httpx.ConnectError("offline", request=req)

    with pytest.raises(OpenRouterError) as e:
        await client_with(handler).key_info()
    assert e.value.kind == "network"


@pytest.mark.parametrize(
    "exc_type",
    [httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.ProxyError],
)
async def test_transport_failures_are_network_errors(client_with, exc_type):
    def handler(req):
        raise exc_type("connection lost", request=req)

    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(handler))
    assert (e.value.status, e.value.kind, e.value.message) == (0, "network", "connection lost")


async def test_a_body_that_cannot_be_decoded_is_a_bad_response(client_with):
    def handler(req):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"not gzip")

    with pytest.raises(OpenRouterError) as e:
        await complete(client_with(handler))
    assert (e.value.status, e.value.kind) == (0, "bad_response")


async def test_cancelling_a_call_is_not_turned_into_an_error(client_with):
    # The cleanup service gives up with asyncio.wait_for; the call must let that happen.
    async def never_answers(request):
        await asyncio.sleep(30)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(complete(client_with(never_answers)), timeout=0.05)


async def test_failures_are_logged_with_status_and_kind_only(client_with, caplog):
    # The messages echo the key, as a careless server or proxy might. Only the status and the kind
    # go to the log, so the key stays out of it.
    secret = "sk-or-v1-0123456789abcdef-SECRET"
    caplog.set_level(logging.DEBUG)

    def refused(request):
        body = {"error": {"code": 401, "message": f"Invalid key {secret}"}}
        return httpx.Response(401, json=body)

    def offline(request):
        raise httpx.ConnectError(f"cannot connect with {secret}", request=request)

    for handler in (refused, offline):
        with pytest.raises(OpenRouterError):
            await complete(client_with(handler, key=secret))
        with pytest.raises(OpenRouterError):
            await client_with(handler, key=secret).key_info()

    logged = [r.getMessage() for r in caplog.records if r.name == openrouter.logger.name]
    assert any("401" in line and "auth" in line for line in logged)
    assert any("network" in line for line in logged)
    assert secret not in caplog.text
    assert "Bearer" not in caplog.text


# --- key info ---------------------------------------------------------------------------------


async def test_key_info_parses_fields(client_with):
    data = {
        "data": {
            "label": "Raphael",
            "limit": 50,
            "limit_remaining": 46.58,
            "limit_reset": "monthly",
            "usage_daily": 0.21,
            "usage_weekly": 1.23,
            "usage_monthly": 3.42,
            "usage": 12.34,
        }
    }
    info = await client_with(lambda r: httpx.Response(200, json=data)).key_info()
    assert info == KeyInfo("Raphael", 50, 46.58, "monthly", 0.21, 3.42, 1.23, 12.34)


async def test_a_key_without_a_limit_has_none_not_zero(client_with):
    data = {
        "data": {
            "label": "Raphael",
            "limit": None,
            "limit_remaining": None,
            "limit_reset": None,
            "usage_daily": 0,
            "usage_monthly": 1.5,
            "is_free_tier": False,
        }
    }
    info = await client_with(lambda r: httpx.Response(200, json=data)).key_info()
    assert info == KeyInfo("Raphael", None, None, None, 0.0, 1.5)


async def test_key_info_fills_in_what_the_reply_leaves_out(client_with):
    info = await client_with(lambda r: httpx.Response(200, json={"data": {}})).key_info()
    assert info == KeyInfo("", None, None, None, None, None)


@pytest.mark.parametrize("value", [None, True, "1.2", [], {}])
async def test_key_usage_unknown_amounts_are_none(client_with, value):
    data = {
        field: value
        for field in (
            "usage_daily", "usage_weekly", "usage_monthly", "usage", "limit", "limit_remaining"
        )
    }
    info = await client_with(lambda r: httpx.Response(200, json={"data": data})).key_info()
    assert (
        info.usage_daily,
        info.usage_weekly,
        info.usage_monthly,
        info.usage_total,
        info.limit,
        info.limit_remaining,
    ) == (None, None, None, None, None, None)


async def test_a_rejected_key_is_an_auth_error(client_with):
    body = {"error": {"code": 401, "message": "No auth credentials found"}}
    with pytest.raises(OpenRouterError) as e:
        await client_with(lambda r: httpx.Response(401, json=body)).key_info(key="sk-or-v1-wrong")
    assert (e.value.status, e.value.kind) == (401, "auth")


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(lambda: httpx.Response(200, json={}), id="no data"),
        pytest.param(lambda: httpx.Response(200, json={"data": None}), id="data is null"),
        pytest.param(lambda: httpx.Response(200, json={"data": [1]}), id="data is a list"),
        pytest.param(lambda: httpx.Response(200, text="<html>wifi login</html>"), id="a web page"),
    ],
)
async def test_a_key_reply_without_data_is_a_bad_response(client_with, reply):
    with pytest.raises(OpenRouterError) as e:
        await client_with(lambda r: reply()).key_info()
    assert (e.value.status, e.value.kind) == (200, "bad_response")


# --- the http client --------------------------------------------------------------------------


def patch_default_client(monkeypatch, build_seconds=0.0):
    """Make the httpx client that OpenRouterClient builds for itself talk to a mock.

    Returns the list of (client, keyword arguments) of each client built so far. `build_seconds`
    is how long a build takes (a real one loads the CA bundle).
    """
    built = []
    real_client = httpx.AsyncClient

    def build(**kwargs):
        if build_seconds:
            time.sleep(build_seconds)
        transport = httpx.MockTransport(lambda request: ok_reply({"text": "x"}))
        client = real_client(transport=transport, **kwargs)
        built.append((client, kwargs))
        return client

    monkeypatch.setattr(httpx, "AsyncClient", build)
    return built


async def test_the_default_http_client_is_made_on_first_use_and_closed_by_aclose(monkeypatch):
    built = patch_default_client(monkeypatch)
    client = OpenRouterClient(lambda: KEY)
    assert built == []
    await complete(client)
    await complete(client)
    assert len(built) == 1
    http = built[0][0]
    assert not http.is_closed
    await client.aclose()
    assert http.is_closed
    await client.aclose()  # closing twice is fine


async def test_a_call_after_aclose_builds_a_fresh_http_client(monkeypatch):
    built = patch_default_client(monkeypatch)
    client = OpenRouterClient(lambda: KEY)
    await complete(client)
    await client.aclose()
    completion = await complete(client)
    assert completion.data == {"text": "x"}
    assert len(built) == 2
    first, second = built[0][0], built[1][0]
    assert first.is_closed
    assert not second.is_closed
    await client.aclose()
    assert second.is_closed


async def test_the_default_http_client_keeps_connections_alive_for_two_minutes(monkeypatch):
    # So that dictations a minute or two apart reuse the TLS connection instead of opening one.
    built = patch_default_client(monkeypatch)
    client = OpenRouterClient(lambda: KEY)
    try:
        await complete(client)
    finally:
        await client.aclose()
    assert built[0][1]["limits"].keepalive_expiry == 120


async def test_building_the_default_http_client_is_not_counted_as_latency(monkeypatch):
    patch_default_client(monkeypatch, build_seconds=0.2)
    client = OpenRouterClient(lambda: KEY)
    try:
        completion = await complete(client)
    finally:
        await client.aclose()
    assert completion.latency_ms < 100


async def test_aclose_leaves_a_client_it_was_given_open():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: ok_reply({"text": "x"}))
    ) as http:
        await OpenRouterClient(lambda: KEY, http=http).aclose()
        assert not http.is_closed


async def test_aclose_without_a_request_does_nothing():
    await OpenRouterClient(lambda: KEY).aclose()


async def test_warm_up_makes_the_http_client_ahead_of_the_first_request():
    # Building an httpx client takes over a second here (it loads the TLS certificates), and the
    # first dictation should not pay for it. No request is made.
    client = OpenRouterClient(lambda: KEY)
    assert client._own_http is None
    client.warm_up()
    made = client._own_http
    assert made is not None and not made.is_closed
    client.warm_up()
    assert client._own_http is made  # once
    await client.aclose()
    assert made.is_closed


async def test_warm_up_leaves_a_client_it_was_given_alone():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: ok_reply({"text": "x"}))
    ) as http:
        client = OpenRouterClient(lambda: KEY, http=http)
        client.warm_up()
        assert client._own_http is None


# --- models -----------------------------------------------------------------------------------


def test_the_model_choices_are_the_default_then_its_fallbacks():
    assert DEFAULTS["cleanup_model"] == DEFAULT_CLEANUP_MODEL
    assert CLEANUP_FALLBACKS == ["anthropic/claude-haiku-5.5", "google/gemini-3.1-flash-lite"]
    assert CLEANUP_MODEL_CHOICES == [
        "openai/gpt-6-luna",
        "anthropic/claude-haiku-5.5",
        "google/gemini-3.1-flash-lite",
    ]
