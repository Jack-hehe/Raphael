"""Fish Audio, as an httpx MockTransport fake of its API: the request that is sent (headers, body),
each failure by its kind, and the key that never reaches a log. No test reaches the network."""

import json
import logging

import httpx
import pytest

from raphael_core.services import tts
from raphael_core.services.common import ServiceError

KEY = "fish-the-owners-key-0123456789abcdef"
VOICE = "0123456789abcdef0123456789abcdef"
MP3 = b"ID3\x04\x00\x00fake-mp3-bytes"


class Fish:
    def __init__(self, answer=None):
        self.answer = httpx.Response(200, content=MP3) if answer is None else answer
        self.requests: list[httpx.Request] = []
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def speak(fish, **overrides):
    arguments = {"http": fish.http, "key": KEY, "voice": VOICE, "model": "s2.1-pro-free"}
    return await tts.synthesize("fish", "告。主人，請下達指示。", **{**arguments, **overrides})


async def kind_of(awaitable):
    with pytest.raises(ServiceError) as caught:
        await awaitable
    return caught.value.kind


async def test_the_request_has_the_key_the_model_and_the_voice_in_the_places_fish_documents():
    fish = Fish()
    audio = await speak(fish)
    assert audio == tts.Audio(MP3, "mp3")
    (request,) = fish.requests
    assert request.method == "POST" and str(request.url) == "https://api.fish.audio/v1/tts"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["model"] == "s2.1-pro-free"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "text": "告。主人，請下達指示。",
        "reference_id": VOICE,
        "format": "mp3",
        "latency": "low",
    }


async def test_a_text_is_held_to_the_limit_of_a_request():
    fish = Fish()
    await tts.synthesize(
        "fish", "好" * 5_000, http=fish.http, key=KEY, voice=VOICE, model="s2.1-pro-free"
    )
    assert len(json.loads(fish.requests[0].content)["text"]) == tts.MAX_TEXT_CHARS


async def test_no_model_sends_no_model_header():
    fish = Fish()
    await speak(fish, model="")
    assert "model" not in fish.requests[0].headers


async def test_no_voice_id_is_not_configured_and_no_request_is_made():
    fish = Fish()
    assert await kind_of(speak(fish, voice="")) == "not_configured"
    assert fish.requests == []


async def test_no_key_is_a_missing_secret_and_no_request_is_made():
    fish = Fish()
    assert await kind_of(speak(fish, key=None)) == "missing_secret"
    assert fish.requests == []


async def test_a_service_that_is_none_is_not_configured():
    fish = Fish()
    kind = await kind_of(tts.synthesize("none", "x", http=fish.http, key=KEY, voice=VOICE))
    assert kind == "not_configured" and fish.requests == []


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, "auth"),
        (403, "auth"),
        (402, "credits"),
        (429, "rate_limit"),
        (422, "http_error"),
        (500, "http_error"),
        (503, "http_error"),
    ],
)
async def test_each_error_code_is_a_kind_and_nothing_else(status, kind):
    fish = Fish(httpx.Response(status, json={"status": status, "message": f"secret {KEY} {VOICE}"}))
    assert await kind_of(speak(fish)) == kind


async def test_a_timeout_and_a_network_failure_are_told_apart():
    request = httpx.Request("POST", tts.FISH_URL)
    assert await kind_of(speak(Fish(httpx.ReadTimeout("slow", request=request)))) == "timeout"
    assert await kind_of(speak(Fish(httpx.ConnectError("down", request=request)))) == "network"


async def test_an_answer_with_no_sound_in_it_is_a_bad_response():
    assert await kind_of(speak(Fish(httpx.Response(200, content=b"")))) == "bad_response"


async def test_a_failure_never_carries_the_key_the_voice_or_the_text(caplog):
    fish = Fish(httpx.Response(401, text=f"bad key {KEY} for {VOICE}"))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ServiceError) as caught:
            await speak(fish)
    assert str(caught.value) == "auth"
    for needle in (KEY, VOICE, "主人"):
        assert needle not in caplog.text and needle not in repr(caught.value)
