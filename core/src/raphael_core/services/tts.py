"""Text to speech: the services that turn Raphael's answer into sound, one provider at a time.

Only Fish Audio is built (`FISH_URL`), with the owner's own voice model; the others the settings
could one day name (Edge TTS, ElevenLabs, OpenAI, a GPT-SoVITS server of the owner's own) are each
another branch of `stream`, as the search engines are of `web.search`. A provider gives the sound
as it is made: raw PCM, in chunks (`stream`), which `playback` writes to the sound device as they
arrive. Nothing is written to disk.

Fish Audio's request (as of 2026-10, docs.fish.audio): `POST /v1/tts` with

- the key as `Authorization: Bearer <key>`, and the model as a header of its own (`model`), not in
  the body: `s2.1-pro-free` is the free developer tier's;
- a JSON body `{text, reference_id, format, sample_rate, latency, chunk_length}`. `reference_id` is
  the id of the voice model (the owner's clone, from the settings: never in this repository).
  `format` is "pcm": raw 16-bit little-endian mono samples with no container, "for low-latency
  playback", so nothing has to be decoded and every chunk of the body is playable as it comes.
  `sample_rate` is 44100, the default of the service and the rate of its codec (8, 16, 24, 32 and
  44.1 kHz are allowed for PCM). `latency` is "low", the lowest of its three (the others trade
  latency for quality, and a short answer needs speed). `chunk_length` is 100, the smallest: the
  text is cut into pieces of about that many characters and each is sent on as soon as it is made,
  so the sound starts after the first piece instead of after the whole line. `normalize` is left
  at its default, on: numbers, dates and units are spelled out as spoken.

Why this path (HTTP, chunked) and not the WebSocket (`/v1/tts/live`): the docs describe the
response of `/v1/tts` as `Transfer-Encoding: chunked`, which is a stream of the body, and the
WebSocket is for text that arrives in pieces (a model's tokens). Raphael's line is complete when it
is spoken, so a WebSocket would add a handshake and a protocol (start, text, stop events, msgpack)
to the first sound and gain nothing: one request, whose body is read as it is made, is the lowest
latency that is also simple.

Reading the body as it comes means that a failure may come after the sound has begun: a status
code is known before the first chunk (a `ServiceError` of a kind, raised by the first step of the
stream), a break in the connection after it (the stream ends with the same kinds). The kinds
(`common.ServiceError`) are: 401 and 403 "auth", 402 "credits", 429 "rate_limit", any other status
"http_error" (503, the service under load, among them), "timeout", "network", and "bad_response"
for an answer with no sound in it. Fish reports nothing of what the request cost in a header or a
body, so there is nothing to write to the spend. Nothing here logs the text, the voice, the key or
the audio.
"""

from collections.abc import AsyncIterator
from contextlib import aclosing

import httpx

from .common import ServiceError

FISH_URL = "https://api.fish.audio/v1/tts"
# The sound of Fish Audio's "pcm" format: 16-bit little-endian samples, mono, at this rate.
PCM_RATE = 44_100
# A silence this long, in a request or in its body, is a failure. (The line itself takes seconds.)
TTS_TIMEOUT_S = 20.0
# The most that is sent to a service at once: an answer is a few sentences, and a long one is cut
# before the speech (`speakable`), so this is only the guard of the request itself.
MAX_TEXT_CHARS = 1_000
# Audio larger than this is not an answer of a few sentences: the stream is closed unplayed.
MAX_AUDIO_BYTES = 20_000_000


def _status_error(status: int) -> ServiceError:
    if status in (401, 403):
        return ServiceError("auth")
    if status == 402:
        return ServiceError("credits")
    if status == 429:
        return ServiceError("rate_limit")
    return ServiceError("http_error")


def stream(
    service: str,
    text: str,
    *,
    http: httpx.AsyncClient,
    key: str | None = None,
    voice: str = "",
    model: str = "",
) -> AsyncIterator[bytes]:
    """The speech of `text` by `service` ("fish") as chunks of PCM (`PCM_RATE`, mono, 16-bit),
    each as soon as it has arrived. `voice` is the id of the voice model and `model` the service's
    model. The iterator must be closed (`contextlib.aclosing`) when it is not read to its end, which
    closes the connection of the request. ServiceError of a kind, raised by the first step or by a
    later one: "not_configured" for a service that is none or has no voice, "missing_secret" for
    one that needs a key and has none, and the kinds above."""
    if service == "fish":
        return _fish(text, http=http, key=key, voice=voice, model=model)
    return _refused()


async def _refused() -> AsyncIterator[bytes]:
    raise ServiceError("not_configured")
    yield b""  # pragma: no cover  (makes this an asynchronous generator)


async def _fish(
    text: str, *, http: httpx.AsyncClient, key: str | None, voice: str, model: str
) -> AsyncIterator[bytes]:
    if not voice:
        raise ServiceError("not_configured")
    if not key:
        raise ServiceError("missing_secret")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if model:
        headers["model"] = model
    body = {
        "text": text[:MAX_TEXT_CHARS],
        "reference_id": voice,
        "format": "pcm",
        "sample_rate": PCM_RATE,
        "latency": "low",
        "chunk_length": 100,
    }
    total = 0
    try:
        async with http.stream(
            "POST", FISH_URL, headers=headers, json=body, timeout=TTS_TIMEOUT_S
        ) as response:
            if not response.is_success:
                raise _status_error(response.status_code)
            # No chunk size is given: httpx would gather that many bytes before it gave any, and
            # the sound must be played as whatever has arrived.
            async with aclosing(response.aiter_bytes()) as body_chunks:
                async for chunk in body_chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_AUDIO_BYTES:
                        raise ServiceError("bad_response")
                    yield chunk
    except httpx.TimeoutException:
        raise ServiceError("timeout") from None
    except (httpx.TransportError, httpx.DecodingError):
        raise ServiceError("network") from None
    if total == 0:
        raise ServiceError("bad_response")
