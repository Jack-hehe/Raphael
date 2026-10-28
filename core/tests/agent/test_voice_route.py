"""Voice routing failures fall through to the narrated answer without losing the turn."""

import asyncio
import json

import httpx
import pytest

from raphael_core.brain.openrouter import OpenRouterError

from .conftest import classification, completion, decision
from .test_narration import chat
from .test_service import make_rig  # noqa: F401


async def test_router_timeout_hands_voice_to_the_harness_with_conversation(make_rig, client):
    rig = make_rig()
    rig.seed("Earlier example", "The earlier explanation.")
    client.decisions.append(decision("answer", route_confidence=0.1))
    client.replies.append(TimeoutError())
    client.chats.append(chat(json.dumps({"prefix": "Answer", "text": "Here is the pronunciation.", "detail": ""})))
    result = await rig.ask("Read this example", language="en")
    assert result["text"] == "Here is the pronunciation."
    [current, previous] = await rig.turns()
    assert current["status"] == "done" and current["route"] == "answer"
    assert previous["text"] == "The earlier explanation."
    assert "Earlier example" in client.chat_calls[0]["messages"][1]["content"]
    assert not rig.actions.calls


@pytest.mark.parametrize("error", [TimeoutError(), OpenRouterError(0, "timeout", "slow")])
async def test_a_harness_timeout_after_routing_failed_uses_the_timeout_phrase(make_rig, client, error):
    rig = make_rig()
    client.decisions.append(decision("answer", route_confidence=0.1))
    client.replies.append(TimeoutError())
    client.chats.append(error)
    result = await rig.ask("Read this example", language="en")
    assert result["text"] == "The request took too long. Please try again."
    [turn] = await rig.turns()
    assert turn["status"] == "error" and turn["route"] == "answer"


@pytest.mark.parametrize("connection", [
    OpenRouterError(0, "network", "unreachable"), ConnectionError("unreachable"),
    httpx.ConnectError("unreachable"),
])
async def test_a_connection_failure_is_offline_only_after_the_harness_also_fails(make_rig, client, connection):
    rig = make_rig()
    client.decisions.append(OpenRouterError(0, "network", "unreachable"))
    client.replies.append(OpenRouterError(0, "network", "unreachable"))
    client.chats.append(connection)
    result = await rig.ask("Read this example", language="en")
    assert result["text"] == "The network is unreachable."
    assert len(client.chat_calls) == 1


@pytest.mark.parametrize("route", ["unclear", "unsure", "timeout", "broken"])
async def test_an_indecisive_router_never_dead_ends_even_without_prior_context(make_rig, client, route):
    rig = make_rig()
    if route == "unclear":
        client.decisions.append(decision("unclear"))
    elif route == "timeout":
        client.decisions.extend([TimeoutError(), TimeoutError()])
        client.replies.append(TimeoutError())
    elif route == "broken":
        client.decisions.append(RuntimeError("router bug"))
        client.replies.append(completion({}))
    else:
        client.decisions.append(decision("chat", route_confidence=0.1))
        client.replies.append(completion(classification(route="unclear")))
    client.chats.append(chat(json.dumps({"prefix": "Answer", "text": "Here is an example.", "detail": ""})))
    result = await rig.ask("Explain this sentence", language="en")
    assert result["text"] == "Here is an example."
    assert (await rig.turns())[0]["status"] == "done"


async def test_voice_harness_has_a_total_deadline_and_cancels_a_hanging_request(make_rig, client, monkeypatch):
    from raphael_core.agent import service

    monkeypatch.setattr(service, "VOICE_TURN_TIMEOUT_S", 0.05)
    cancelled = asyncio.Event()

    async def hang(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    rig = make_rig()
    client.decisions.append(decision("unclear"))
    client.chats.append(hang)
    result = await asyncio.wait_for(rig.ask("Read this example", language="en"), 1)
    assert result["text"] == "The request took too long. Please try again."
    assert cancelled.is_set()
    assert (await rig.turns())[0]["status"] == "error"
