"""Tests for asking a model which of the plugins' tools a request needs (`plan_tools`).

Every request goes to an httpx.MockTransport, so no test touches the network.
"""

import json
import logging

import httpx
import pytest

from raphael_core.brain.openrouter import (
    CLEANUP_FALLBACKS,
    OpenRouterClient,
    OpenRouterError,
    ToolCall,
    ToolPlan,
)

KEY = "sk-or-v1-test"
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "Notes__look",
            "description": "[Notes] Look something up.",
            "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}},
        },
    }
]


@pytest.fixture
async def client_with():
    opened: list[httpx.AsyncClient] = []

    def make(handler, key=KEY):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        opened.append(http)
        return OpenRouterClient(lambda: key, http=http)

    yield make
    for http in opened:
        await http.aclose()


def calls_reply(calls, cost=0.0004, **choice):
    message = {"role": "assistant", "content": None}
    if calls is not None:
        message["tool_calls"] = calls
    return httpx.Response(
        200,
        json={
            "model": "openai/gpt-6-luna",
            "choices": [{"message": message, "finish_reason": "tool_calls", **choice}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 30, "cost": cost},
        },
    )


def tool_call(name="Notes__look", arguments='{"topic": "tea"}', id="call_1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": arguments}}


async def plan(client, **overrides):
    args = {
        "model": "openai/gpt-6-luna",
        "fallbacks": CLEANUP_FALLBACKS,
        "system": "Choose tools.",
        "user": "<question>tea?</question>",
        "tools": TOOLS,
        "timeout_s": 8,
        "max_tokens": 400,
    }
    return await client.plan_tools(**{**args, **overrides})


async def test_the_request_offers_the_tools_and_requires_every_parameter(client_with):
    seen = []

    def handler(request):
        seen.append(request)
        return calls_reply([tool_call()])

    await plan(client_with(handler))
    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", "https://openrouter.ai/api/v1/chat/completions")
    body = json.loads(request.content)
    assert body["tools"] == TOOLS and body["tool_choice"] == "auto"
    assert body["messages"] == [
        {"role": "system", "content": "Choose tools."},
        {"role": "user", "content": "<question>tea?</question>"},
    ]
    # A provider that cannot take tools is left out, and the text is not kept or trained on.
    assert body["provider"] == {"require_parameters": True, "sort": "latency", "data_collection": "deny"}
    assert body["reasoning"] == {"effort": "none"}
    assert body["max_tokens"] == 400
    assert body["models"] == CLEANUP_FALLBACKS
    assert "temperature" not in body and "response_format" not in body
    assert request.headers["authorization"] == "Bearer sk-or-v1-test"


async def test_the_chosen_model_is_not_listed_again_as_a_fallback(client_with):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return calls_reply([])

    await plan(client_with(handler), model=CLEANUP_FALLBACKS[0])
    assert CLEANUP_FALLBACKS[0] not in seen[0]["models"]


async def test_the_calls_come_back_with_their_arguments_parsed(client_with):
    reply = calls_reply(
        [
            tool_call(arguments='{"topic": "tea"}'),
            tool_call("Notes__note", '{"text": "milk"}', id="call_2"),
            tool_call("Notes__wipe", "", id="call_3"),
            tool_call("Notes__big", {"already": "parsed"}, id="call_4"),
        ]
    )
    result = await plan(client_with(lambda request: reply))
    assert isinstance(result, ToolPlan)
    assert result.calls == [
        ToolCall("Notes__look", {"topic": "tea"}),
        ToolCall("Notes__note", {"text": "milk"}),
        ToolCall("Notes__wipe", {}),
        ToolCall("Notes__big", {"already": "parsed"}),
    ]
    assert (result.model, result.cost_usd, result.prompt_tokens, result.completion_tokens) == (
        "openai/gpt-6-luna",
        0.0004,
        500,
        30,
    )


@pytest.mark.parametrize("calls", [None, [], "nothing", {"a": 1}])
async def test_a_model_that_asks_for_no_tool_has_no_calls(client_with, calls):
    result = await plan(client_with(lambda request: calls_reply(calls)))
    assert result.calls == []


async def test_a_call_that_cannot_be_made_is_left_out(client_with):
    reply = calls_reply(
        [
            tool_call(arguments="{not json"),
            {"id": "x", "type": "function", "function": {"arguments": "{}"}},
            {"id": "y", "type": "function"},
            "text",
            tool_call("Notes__note", '{"text": "milk"}'),
        ]
    )
    result = await plan(client_with(lambda request: reply))
    assert [c.name for c in result.calls] == ["Notes__note"]


@pytest.mark.parametrize("cost", [None, "free", -1, True])
async def test_a_plan_without_a_usable_cost_costs_nothing(client_with, cost):
    result = await plan(client_with(lambda request: calls_reply([], cost=cost)))
    assert result.cost_usd == 0.0


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": ["x"]}, [], "text"])
async def test_a_reply_without_a_choice_is_a_bad_response(client_with, body):
    client = client_with(lambda request: httpx.Response(200, json=body))
    with pytest.raises(OpenRouterError) as e:
        await plan(client)
    assert (e.value.status, e.value.kind) == (200, "bad_response")


async def test_a_plan_that_ended_with_an_error_is_a_server_error(client_with):
    reply = calls_reply([tool_call()], finish_reason="error", error={"code": 502, "message": "up"})
    with pytest.raises(OpenRouterError) as e:
        await plan(client_with(lambda request: reply))
    assert e.value.kind == "server"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (402, "credits"), (429, "rate_limit"), (400, "bad_request"), (503, "server")],
)
async def test_a_failed_plan_raises_the_error_it_earns(client_with, status, kind):
    client = client_with(lambda request: httpx.Response(status, json={"error": {"message": "no"}}))
    with pytest.raises(OpenRouterError) as e:
        await plan(client)
    assert (e.value.status, e.value.kind) == (status, kind)


async def test_a_plan_without_a_key_sends_nothing(client_with):
    seen = []
    client = client_with(lambda request: seen.append(request) or calls_reply([]), key="")
    with pytest.raises(OpenRouterError) as e:
        await plan(client)
    assert e.value.kind == "auth" and seen == []


async def test_a_failed_plan_logs_its_kind_and_not_the_question_or_the_tools(client_with, caplog):
    client = client_with(lambda request: httpx.Response(500, json={"error": {"message": "x"}}))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(OpenRouterError):
            await plan(client, user="<question>my private question</question>")
    assert "private" not in caplog.text and "Notes__look" not in caplog.text
    assert KEY not in caplog.text
