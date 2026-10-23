"""Tests for one turn of a conversation in which the model may call tools (`chat_tools`), which the
background worker's loop is built on.

Every request goes to an httpx.MockTransport, so no test touches the network.
"""

import json
import logging

import httpx
import pytest

from raphael_core.brain.openrouter import (
    CLEANUP_FALLBACKS,
    ChatToolCall,
    ChatTurn,
    OpenRouterClient,
    OpenRouterError,
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
MESSAGES = [
    {"role": "system", "content": "Work."},
    {"role": "user", "content": "<task>tea?</task>"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "Notes__look", "arguments": '{"topic": "tea"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "<tool_result>green</tool_result>"},
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


def reply(content=None, calls=None, cost=0.0004, finish="stop", **choice):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return httpx.Response(
        200,
        json={
            "model": "openai/gpt-6-luna",
            "choices": [{"message": message, "finish_reason": finish, **choice}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 30, "cost": cost},
        },
    )


def tool_call(name="Notes__look", arguments='{"topic": "tea"}', id="call_1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": arguments}}


async def turn(client, **overrides) -> ChatTurn:
    args = {
        "model": "openai/gpt-6-luna",
        "fallbacks": CLEANUP_FALLBACKS,
        "messages": MESSAGES,
        "tools": TOOLS,
        "timeout_s": 8,
        "max_tokens": 900,
    }
    return await client.chat_tools(**{**args, **overrides})


async def test_the_whole_conversation_and_the_tools_are_sent_and_every_parameter_is_required(
    client_with,
):
    seen = []

    def handler(request):
        seen.append(request)
        return reply("done")

    await turn(client_with(handler))
    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", "https://openrouter.ai/api/v1/chat/completions")
    body = json.loads(request.content)
    assert body["messages"] == MESSAGES
    assert body["tools"] == TOOLS and body["tool_choice"] == "auto"
    # A provider that cannot take tools is left out, and the text is not kept or trained on.
    assert body["provider"] == {"require_parameters": True, "sort": "latency", "data_collection": "deny"}
    assert body["reasoning"] == {"effort": "none"}
    assert body["max_tokens"] == 900
    assert body["models"] == CLEANUP_FALLBACKS
    assert "temperature" not in body and "response_format" not in body
    assert request.headers["authorization"] == "Bearer sk-or-v1-test"


async def test_without_tools_the_model_can_only_answer(client_with):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return reply("done")

    await turn(client_with(handler), tools=[])
    assert "tools" not in seen[0] and "tool_choice" not in seen[0]


async def test_the_chosen_model_is_not_listed_again_as_a_fallback(client_with):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return reply("done")

    await turn(client_with(handler), model=CLEANUP_FALLBACKS[0])
    assert CLEANUP_FALLBACKS[0] not in seen[0]["models"]


async def test_the_words_and_the_calls_come_back_with_the_ids_and_the_arguments_as_written(
    client_with,
):
    calls = [
        tool_call(arguments='{"topic": "tea"}'),
        tool_call("Notes__note", '{"text": "milk"}', id="call_2"),
        tool_call("Notes__wipe", "", id="call_3"),
        tool_call("Notes__big", {"already": "parsed"}, id="call_4"),
        {"function": {"name": "Notes__look", "arguments": "{}"}},
    ]
    result = await turn(client_with(lambda request: reply("Looking.", calls)))
    assert isinstance(result, ChatTurn)
    assert result.content == "Looking."
    assert result.calls[:4] == [
        ChatToolCall("call_1", "Notes__look", {"topic": "tea"}, '{"topic": "tea"}'),
        ChatToolCall("call_2", "Notes__note", {"text": "milk"}, '{"text": "milk"}'),
        ChatToolCall("call_3", "Notes__wipe", {}, ""),
        ChatToolCall("call_4", "Notes__big", {"already": "parsed"}, '{"already": "parsed"}'),
    ]
    last = result.calls[4]  # a call with no id is given one
    assert (last.name, last.arguments, last.raw_arguments) == ("Notes__look", {}, "{}")
    assert last.id == "unnamed_call_4"
    assert (result.model, result.cost_usd, result.prompt_tokens, result.completion_tokens) == (
        "openai/gpt-6-luna",
        0.0004,
        500,
        30,
    )


async def test_a_call_whose_arguments_are_not_json_is_kept_with_none_so_the_model_can_be_told(
    client_with,
):
    result = await turn(client_with(lambda request: reply(None, [tool_call(arguments="{not json")])))
    assert result.calls == [ChatToolCall("call_1", "Notes__look", None, "{not json")]


@pytest.mark.parametrize("calls", [None, [], "nothing", {"a": 1}])
async def test_a_model_that_asks_for_no_tool_has_no_calls(client_with, calls):
    result = await turn(client_with(lambda request: reply("The report.", calls)))
    assert result.calls == [] and result.content == "The report."


async def test_a_call_with_no_name_is_left_out(client_with):
    calls = [
        {"id": "x", "type": "function", "function": {"arguments": "{}"}},
        {"id": "y", "type": "function"},
        "text",
        tool_call("Notes__note", '{"text": "milk"}'),
    ]
    result = await turn(client_with(lambda request: reply(None, calls)))
    assert [c.name for c in result.calls] == ["Notes__note"]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("plain", "plain"),
        ([{"type": "text", "text": "a"}, {"type": "image", "url": "x"}, {"type": "text", "text": "b"}], "ab"),
        (None, ""),
        (7, ""),
    ],
)
async def test_the_words_may_come_as_parts(client_with, content, expected):
    result = await turn(client_with(lambda request: reply(content)))
    assert result.content == expected


@pytest.mark.parametrize("cost", [None, "free", -1, True])
async def test_a_turn_without_a_usable_cost_costs_nothing_and_says_it_did_not_say(client_with, cost):
    result = await turn(client_with(lambda request: reply("x", cost=cost)))
    assert result.cost_usd == 0.0 and result.usage_known is False


async def test_a_turn_that_gives_its_cost_says_so_even_when_it_is_zero(client_with):
    for cost in (0.0004, 0, 0.0):
        result = await turn(client_with(lambda request, cost=cost: reply("x", cost=cost)))
        assert result.usage_known is True


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": ["x"]}, [], "text"])
async def test_a_reply_without_a_choice_is_a_bad_response(client_with, body):
    client = client_with(lambda request: httpx.Response(200, json=body))
    with pytest.raises(OpenRouterError) as e:
        await turn(client)
    assert (e.value.status, e.value.kind) == (200, "bad_response")


async def test_a_turn_that_ended_with_an_error_is_a_server_error(client_with):
    answer = reply("part", finish="error", error={"code": 502, "message": "up"})
    with pytest.raises(OpenRouterError) as e:
        await turn(client_with(lambda request: answer))
    assert e.value.kind == "server"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (402, "credits"), (429, "rate_limit"), (400, "bad_request"), (503, "server")],
)
async def test_a_failed_turn_raises_the_error_it_earns(client_with, status, kind):
    client = client_with(lambda request: httpx.Response(status, json={"error": {"message": "no"}}))
    with pytest.raises(OpenRouterError) as e:
        await turn(client)
    assert (e.value.status, e.value.kind) == (status, kind)


async def test_a_model_none_of_whose_providers_takes_tools_is_a_404(client_with):
    client = client_with(lambda request: httpx.Response(404, json={"error": {"message": "none"}}))
    with pytest.raises(OpenRouterError) as e:
        await turn(client)
    assert e.value.status == 404


async def test_a_turn_without_a_key_sends_nothing(client_with):
    seen = []
    client = client_with(lambda request: seen.append(request) or reply("x"), key="")
    with pytest.raises(OpenRouterError) as e:
        await turn(client)
    assert e.value.kind == "auth" and seen == []


async def test_a_failed_turn_logs_its_kind_and_not_the_conversation(client_with, caplog):
    client = client_with(lambda request: httpx.Response(500, json={"error": {"message": "x"}}))
    with caplog.at_level(logging.DEBUG), pytest.raises(OpenRouterError):
        await turn(client)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "tea?" not in logged and "green" not in logged and "Notes__look" not in logged
    assert KEY not in logged
