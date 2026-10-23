"""Fish Audio, as an httpx MockTransport fake of its API: the request that is sent (headers, body),
the sound as a stream of chunks that is read as it arrives and closed when it is not read to its end,
each failure by its kind, and the key that never reaches a log. No test reaches the network."""

import asyncio
import json
import logging
from contextlib import aclosing

import httpx
import pytest

from raphael_core.services import tts
from raphael_core.services.common import ServiceError

KEY = "fish-the-owners-key-0123456789abcdef"
VOICE = "0123456789abcdef0123456789abcdef"
PCM = b"\x01\x00\x02\x00\x03\x00\x04\x00"


class Chunks(httpx.AsyncByteStream):
    """A response body that gives its chunks one at a time. A chunk that is an `asyncio.Event`
    waits until it is set; one that is an exception is raised. `closed` says whether the response
    was closed."""

    def __init__(self, *chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, asyncio.Event):
                await chunk.wait()
            elif isinstance(chunk, Exception):
                raise chunk
            else:
                yield chunk

    async def aclose(self):
        self.closed = True


class Fish:
    def __init__(self, answer=None):
        self.answer = httpx.Response(200, content=PCM) if answer is None else answer
        self.requests: list[httpx.Request] = []
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def sound(fish, **overrides):
    arguments = {"http": fish.http, "key": KEY, "voice": VOICE, "model": "s2.1-pro-free"}
    return tts.stream("fish", "告。マスター、ご指示をどうぞ。", **{**arguments, **overrides})


async def heard(source):
    """Everything a stream gives, joined."""
    async with aclosing(source) as chunks:
        return b"".join([chunk async for chunk in chunks])


async def kind_of(source):
    with pytest.raises(ServiceError) as caught:
        await heard(source)
    return caught.value.kind


async def test_the_request_has_the_key_the_model_and_the_voice_in_the_places_fish_documents():
    fish = Fish()
    assert await heard(sound(fish)) == PCM
    (request,) = fish.requests
    assert request.method == "POST" and str(request.url) == "https://api.fish.audio/v1/tts"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["model"] == "s2.1-pro-free"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "text": "告。マスター、ご指示をどうぞ。",
        "reference_id": VOICE,
        "format": "pcm",
        "sample_rate": 44_100,
        "latency": "low",
        "chunk_length": 100,
    }


async def test_the_chunks_arrive_as_they_are_made_not_when_the_body_is_done():
    second = asyncio.Event()
    fish = Fish(httpx.Response(200, stream=Chunks(b"\x01\x00" * 100, second, b"\x02\x00" * 100)))
    async with aclosing(sound(fish)) as chunks:
        first = await asyncio.wait_for(anext(chunks), 2)  # the body is not finished: it waits
        assert first == b"\x01\x00" * 100 and not second.is_set()
        second.set()
        rest = [chunk async for chunk in chunks]
    assert b"".join(rest) == b"\x02\x00" * 100


async def test_a_stream_that_is_not_read_to_its_end_closes_its_request():
    body = Chunks(b"\x01\x00" * 10, asyncio.Event())
    fish = Fish(httpx.Response(200, stream=body))
    async with aclosing(sound(fish)) as chunks:
        await anext(chunks)
    assert body.closed


async def test_a_text_is_held_to_the_limit_of_a_request():
    fish = Fish()
    long_line = tts.stream(
        "fish", "好" * 5_000, http=fish.http, key=KEY, voice=VOICE, model="s2.1-pro-free"
    )
    await heard(long_line)
    assert len(json.loads(fish.requests[0].content)["text"]) == tts.MAX_TEXT_CHARS


async def test_no_model_sends_no_model_header():
    fish = Fish()
    await heard(sound(fish, model=""))
    assert "model" not in fish.requests[0].headers


async def test_no_voice_id_is_not_configured_and_no_request_is_made():
    fish = Fish()
    assert await kind_of(sound(fish, voice="")) == "not_configured"
    assert fish.requests == []


async def test_no_key_is_a_missing_secret_and_no_request_is_made():
    fish = Fish()
    assert await kind_of(sound(fish, key=None)) == "missing_secret"
    assert fish.requests == []


async def test_a_service_that_is_none_is_not_configured():
    fish = Fish()
    source = tts.stream("none", "x", http=fish.http, key=KEY, voice=VOICE)
    assert await kind_of(source) == "not_configured" and fish.requests == []


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
    assert await kind_of(sound(fish)) == kind


async def test_an_error_code_is_known_before_any_sound():
    fish = Fish(httpx.Response(402, json={"message": "no credit"}))
    async with aclosing(sound(fish)) as chunks:
        with pytest.raises(ServiceError) as caught:
            await anext(chunks)
    assert caught.value.kind == "credits"


async def test_a_timeout_and_a_network_failure_are_told_apart():
    request = httpx.Request("POST", tts.FISH_URL)
    assert await kind_of(sound(Fish(httpx.ReadTimeout("slow", request=request)))) == "timeout"
    assert await kind_of(sound(Fish(httpx.ConnectError("down", request=request)))) == "network"


async def test_a_connection_that_breaks_after_the_sound_began_gives_what_came_then_a_kind():
    request = httpx.Request("POST", tts.FISH_URL)
    fish = Fish(
        httpx.Response(200, stream=Chunks(PCM, httpx.ReadError("reset", request=request)))
    )
    async with aclosing(sound(fish)) as chunks:
        assert await anext(chunks) == PCM
        with pytest.raises(ServiceError) as caught:
            await anext(chunks)
    assert caught.value.kind == "network"


async def test_an_answer_with_no_sound_in_it_is_a_bad_response():
    assert await kind_of(sound(Fish(httpx.Response(200, content=b"")))) == "bad_response"


async def test_sound_beyond_the_limit_is_cut_off(monkeypatch):
    monkeypatch.setattr(tts, "MAX_AUDIO_BYTES", 6)
    assert await kind_of(sound(Fish(httpx.Response(200, content=PCM)))) == "bad_response"


async def test_a_failure_never_carries_the_key_the_voice_or_the_text(caplog):
    fish = Fish(httpx.Response(401, text=f"bad key {KEY} for {VOICE}"))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ServiceError) as caught:
            await heard(sound(fish))
    assert str(caught.value) == "auth"
    for needle in (KEY, VOICE, "マスター"):
        assert needle not in caplog.text and needle not in repr(caught.value)
