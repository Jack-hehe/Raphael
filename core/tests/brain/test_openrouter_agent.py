"""Tests for what the voice assistant asks of the OpenRouter client: a decision from Jev, a reply
from a model that thinks a little, and text from a model that has searched the web.

Every request goes to an httpx.MockTransport, so no test touches the network.
"""

import json
import logging

import httpx
import pytest

from raphael_core.brain import openrouter
from raphael_core.brain.openrouter import (
    CLEANUP_FALLBACKS,
    Decision,
    OpenRouterClient,
    OpenRouterError,
    TextCompletion,
)

KEY = "sk-or-v1-test"
SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}
QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "What is it?",
        "criteria": {"command": "Do it now.", "chat": "Small talk."},
    },
    "needs_time": {
        "type": "noul",
        "instructions": "Is the time needed?",
        "criteria": {"true": "Yes.", "false": "No."},
    },
}
ANSWERS = {
    "route": {
        "type": "choice",
        "choice": "chat",
        "confidence": 0.91,
        "probabilities": {"command": 0.09, "chat": 0.91},
    },
    "needs_time": {"type": "noul", "noul": 0.02},
}


@pytest.fixture
async def client_with():
    """`client_with(handler, key=KEY)` is a client whose HTTP goes to `handler`."""
    opened: list[httpx.AsyncClient] = []

    def make(handler, key=KEY):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        opened.append(http)
        return OpenRouterClient(key if callable(key) else (lambda: key), http=http)

    yield make
    for http in opened:
        await http.aclose()


def decision_reply(answers=ANSWERS, cost=0.00004):
    body = {"answers": answers}
    if cost is not None:
        body["usage"] = {"cost": cost}
    return httpx.Response(200, json=body)


def text_reply(content="Rain is likely tomorrow.", cost=0.012, annotations=None, **message):
    message = {"role": "assistant", "content": content, **message}
    if annotations is not None:
        message["annotations"] = annotations
    return httpx.Response(
        200,
        json={
            "model": "openai/gpt-6-luna",
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 40, "cost": cost},
        },
    )


def json_reply(content: dict):
    return httpx.Response(
        200,
        json={
            "model": "openai/gpt-6-luna",
            "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.0002},
        },
    )


def answering(reply, seen):
    def handler(request):
        seen.append(request)
        return reply()

    return handler


async def decide(client, **overrides):
    args = {
        "model": "typesafe/jev-1.14",
        "state": {"utterance": "打開 YouTube"},
        "questions": QUESTIONS,
        "timeout_s": 2.5,
    }
    return await client.decide(**{**args, **overrides})


async def chat(client, **overrides):
    args = {
        "model": "openai/gpt-6-luna",
        "system": "Be brief.",
        "user": "Will it rain tomorrow?",
        "plugins": [{"id": "web", "max_results": 3}],
        "timeout_s": 10,
        "max_tokens": 400,
    }
    return await client.chat_text(**{**args, **overrides})


# --- a decision from Jev -------------------------------------------------------------------------


async def test_a_decision_is_posted_to_the_alpha_decisions_endpoint_as_documented(client_with):
    seen = []
    await decide(client_with(answering(decision_reply, seen)))
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
    assert json.loads(request.content) == {
        "model": "typesafe/jev-1.14",
        "state": {"utterance": "打開 YouTube"},
        "questions": QUESTIONS,
    }
    assert request.headers["authorization"] == "Bearer sk-or-v1-test"
    assert request.headers["http-referer"] == "http://localhost/raphael"
    assert request.headers["x-openrouter-title"] == "Raphael"


async def test_a_decision_carries_the_answers_the_cost_and_the_time_it_took(client_with):
    result = await decide(client_with(lambda request: decision_reply(cost=0.00004)))
    assert isinstance(result, Decision)
    assert result.answers == ANSWERS
    assert result.cost_usd == 0.00004
    assert isinstance(result.latency_ms, int) and result.latency_ms >= 0


@pytest.mark.parametrize("cost", [None, "free", -1, True])
async def test_a_decision_without_a_usable_cost_costs_nothing(client_with, cost):
    result = await decide(client_with(lambda request: decision_reply(cost=cost)))
    assert result.cost_usd == 0.0


async def test_the_timeout_applies_to_the_decision(client_with):
    seen = []
    await decide(client_with(answering(decision_reply, seen)), timeout_s=1.5)
    assert seen[0].extensions["timeout"] == {"connect": 1.5, "read": 1.5, "write": 1.5, "pool": 1.5}


@pytest.mark.parametrize(
    "body",
    [{}, {"answers": None}, {"answers": []}, {"answers": "x"}, [], "text"],
    ids=["no answers", "null", "a list", "a string", "an array", "a string body"],
)
async def test_a_decision_reply_without_answers_is_a_bad_response(client_with, body):
    client = client_with(lambda request: httpx.Response(200, json=body))
    with pytest.raises(OpenRouterError) as e:
        await decide(client)
    assert (e.value.status, e.value.kind) == (200, "bad_response")


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (402, "credits"), (429, "rate_limit"), (400, "bad_request"), (503, "server")],
)
async def test_a_failed_decision_raises_the_error_it_earns(client_with, status, kind):
    client = client_with(lambda request: httpx.Response(status, json={"error": {"message": "no"}}))
    with pytest.raises(OpenRouterError) as e:
        await decide(client)
    assert (e.value.status, e.value.kind) == (status, kind)


async def test_a_decision_that_times_out_is_a_timeout(client_with):
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(OpenRouterError) as e:
        await decide(client_with(handler))
    assert (e.value.status, e.value.kind) == (0, "timeout")


async def test_a_decision_without_a_key_sends_nothing(client_with):
    seen = []
    client = client_with(answering(decision_reply, seen), key=None)
    with pytest.raises(OpenRouterError) as e:
        await decide(client)
    assert (e.value.status, e.value.kind) == (0, "auth")
    assert seen == []


async def test_a_failed_decision_logs_its_kind_and_not_the_utterance_or_the_key(
    client_with, caplog
):
    client = client_with(lambda request: httpx.Response(503, json={"error": {"message": "oops"}}))
    with caplog.at_level(logging.DEBUG, logger=openrouter.logger.name), pytest.raises(
        OpenRouterError
    ):
        await decide(client, state={"utterance": "秘密的話"})
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "kind=server" in logged
    assert "秘密的話" not in logged and KEY not in logged


# --- a reply from a model that thinks --------------------------------------------------------------


async def test_json_is_asked_without_reasoning_unless_the_caller_wants_some(client_with):
    seen = []
    client = client_with(answering(lambda: json_reply({"text": "x"}), seen))
    kwargs = {
        "model": "openai/gpt-6-luna",
        "fallbacks": CLEANUP_FALLBACKS,
        "system": "s",
        "user": "u",
        "schema": SCHEMA,
        "schema_name": "cleaned_dictation",
        "timeout_s": 3,
        "max_tokens": 256,
    }
    await client.complete_json(**kwargs)
    await client.complete_json(**kwargs, reasoning_effort="low")
    assert [json.loads(r.content)["reasoning"] for r in seen] == [
        {"effort": "none"},
        {"effort": "low"},
    ]


# --- text from a model that has searched the web -----------------------------------------------------


async def test_a_chat_is_posted_with_the_plugins_and_no_response_format(client_with):
    seen = []
    await chat(client_with(answering(text_reply, seen)))
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert json.loads(request.content) == {
        "model": "openai/gpt-6-luna",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Will it rain tomorrow?"},
        ],
        "plugins": [{"id": "web", "max_results": 3}],
        "provider": {"sort": "latency", "data_collection": "deny"},
        "reasoning": {"effort": "none"},
        "max_tokens": 400,
    }
    assert request.headers["authorization"] == "Bearer sk-or-v1-test"
    assert request.extensions["timeout"]["read"] == 10


async def test_a_chat_without_plugins_sends_none(client_with):
    seen = []
    await chat(client_with(answering(text_reply, seen)), plugins=None, reasoning_effort="low")
    body = json.loads(seen[0].content)
    assert "plugins" not in body and body["reasoning"] == {"effort": "low"}


async def test_a_chat_returns_the_text_the_cost_and_the_sources(client_with):
    annotations = [
        {
            "type": "url_citation",
            "url_citation": {
                "url": "https://example.com/weather",
                "title": "Weather",
                "content": "a long excerpt",
                "start_index": 0,
                "end_index": 5,
            },
        },
        {"type": "file", "file": {}},  # not a source
        {"type": "url_citation", "url_citation": {"url": "https://example.org/rain"}},
        {"type": "url_citation", "url_citation": {"title": "no url"}},  # unusable
        {"type": "url_citation", "url_citation": {"url": "https://example.com/weather"}},  # again
    ]
    result = await chat(client_with(lambda request: text_reply(annotations=annotations)))
    assert isinstance(result, TextCompletion)
    assert result.text == "Rain is likely tomorrow."
    assert result.model == "openai/gpt-6-luna"
    assert result.cost_usd == 0.012
    assert (result.prompt_tokens, result.completion_tokens) == (900, 40)
    assert result.sources == [
        {"title": "Weather", "url": "https://example.com/weather"},
        {"title": "", "url": "https://example.org/rain"},
    ]
    assert isinstance(result.latency_ms, int)


async def test_a_chat_without_annotations_has_no_sources(client_with):
    result = await chat(client_with(lambda request: text_reply()))
    assert result.sources == []


@pytest.mark.parametrize("content", [None, "", "   ", ["part"], 5])
async def test_a_chat_reply_without_text_is_a_bad_response(client_with, content):
    client = client_with(lambda request: text_reply(content=content))
    with pytest.raises(OpenRouterError) as e:
        await chat(client)
    assert (e.value.status, e.value.kind) == (200, "bad_response")


async def test_a_chat_that_ended_with_an_error_is_a_server_error(client_with):
    def handler(request):
        choice = {
            "message": {"content": "half an ans"},
            "finish_reason": "error",
            "error": {"code": 502, "message": "provider died"},
        }
        return httpx.Response(200, json={"choices": [choice]})

    with pytest.raises(OpenRouterError) as e:
        await chat(client_with(handler))
    assert (e.value.status, e.value.kind) == (502, "server")


async def test_a_chat_without_a_key_sends_nothing(client_with):
    seen = []
    with pytest.raises(OpenRouterError) as e:
        await chat(client_with(answering(text_reply, seen), key=""))
    assert e.value.kind == "auth" and seen == []


async def test_a_failed_chat_logs_its_kind_and_not_the_question(client_with, caplog):
    client = client_with(lambda request: httpx.Response(429, json={"error": {"message": "slow"}}))
    with caplog.at_level(logging.DEBUG, logger=openrouter.logger.name), pytest.raises(
        OpenRouterError
    ):
        await chat(client, user="我的秘密問題")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "kind=rate_limit" in logged
    assert "我的秘密問題" not in logged and KEY not in logged
