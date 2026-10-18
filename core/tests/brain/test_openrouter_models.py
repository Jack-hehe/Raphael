"""Tests for the OpenRouter client's model list (GET /models, which needs no key).

Every request goes to an httpx.MockTransport, so no test touches the network.
"""

import httpx
import pytest

from raphael_core.brain.openrouter import OpenRouterClient, OpenRouterError

KEY = "sk-or-v1-test"


@pytest.fixture
async def client_with():
    opened: list[httpx.AsyncClient] = []

    def make(handler, key=KEY):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        opened.append(http)
        return OpenRouterClient(key if callable(key) else (lambda: key), http=http)

    yield make
    for http in opened:
        await http.aclose()


async def test_models_asks_for_every_kind_of_model_and_sends_no_key(client_with):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "a/b"}, {"id": "c/d"}]})

    models = await client_with(handler).models()
    assert models == [{"id": "a/b"}, {"id": "c/d"}]
    (request,) = seen
    assert request.method == "GET"
    assert str(request.url) == "https://openrouter.ai/api/v1/models?output_modalities=all"
    assert "authorization" not in request.headers
    assert request.headers["http-referer"] == "http://localhost/raphael"


async def test_models_works_without_a_key_and_with_a_key_store_that_fails(client_with):
    def broken():
        raise RuntimeError("vault locked")

    def handler(request):
        return httpx.Response(200, json={"data": []})

    assert await client_with(handler, key=None).models() == []
    assert await client_with(handler, key=broken).models() == []


async def test_models_uses_the_timeout_it_is_given(client_with):
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"data": []})

    await client_with(handler).models(timeout_s=2.5)
    assert seen == [{"connect": 2.5, "read": 2.5, "write": 2.5, "pool": 2.5}]


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, text="<html>Sign in to the wifi</html>"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"models": []}),
        httpx.Response(200, json={"data": {"id": "a/b"}}),
    ],
)
async def test_a_reply_that_is_no_model_list_is_a_bad_response(client_with, reply):
    with pytest.raises(OpenRouterError) as caught:
        await client_with(lambda request: reply).models()
    assert caught.value.kind == "bad_response"


async def test_models_failures_carry_their_kind(client_with):
    with pytest.raises(OpenRouterError) as server:
        await client_with(lambda request: httpx.Response(503, json={})).models()
    assert (server.value.kind, server.value.status) == ("server", 503)

    def refuse(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(OpenRouterError) as network:
        await client_with(refuse).models()
    assert network.value.kind == "network"

    def slow(request):
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(OpenRouterError) as timeout:
        await client_with(slow).models()
    assert timeout.value.kind == "timeout"
