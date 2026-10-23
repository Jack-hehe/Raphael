"""Text to speech: the services that turn Raphael's answer into sound, one provider at a time.

Only Fish Audio is built (`FISH_URL`), with the owner's own voice model; the others the settings
could one day name (Edge TTS, ElevenLabs, OpenAI, a GPT-SoVITS server of the owner's own) are each
another branch of `synthesize`, as the search engines are of `web.search`. A provider gives back the
audio as the bytes of a file (`Audio`), which `playback` decodes and plays; it is never written to
disk.

Fish Audio's request (as of 2026-10, docs.fish.audio): `POST /v1/tts` with

- the key as `Authorization: Bearer <key>`, and the model as a header of its own (`model`), not in
  the body: `s2.1-pro-free` is the free developer tier's;
- a JSON body `{text, reference_id, format, latency}`. `reference_id` is the id of the voice model
  (the owner's clone, from the settings: never in this repository). `format` is "mp3", which the
  decoder here reads (libsndfile 1.1 and later) and which is small to fetch. `latency` is "low",
  the lowest of its three (the others trade latency for quality, and a short answer needs speed).
  `normalize` is left at its default, on: numbers, dates and units are spelled out as spoken.

The answer is the audio itself, chunked; it is read whole (an answer is a few sentences). Fish
reports nothing of what the request cost in a header or a body, so there is nothing to write to the
spend. A failure is a `ServiceError` of a kind (`common.ServiceError`): 401 and 403 "auth", 402
"credits", 429 "rate_limit", any other status "http_error" (503, the service under load, among
them), "timeout", "network", and "bad_response" for an answer with no sound in it. Nothing here
logs the text, the voice, the key or the audio.
"""

from dataclasses import dataclass

import httpx

from .common import ServiceError

FISH_URL = "https://api.fish.audio/v1/tts"
# The audio of a few sentences takes seconds; a request that takes this long has failed.
TTS_TIMEOUT_S = 20.0
# The most that is sent to a service at once: an answer is a few sentences, and a long one is cut
# before the speech (`speakable`), so this is only the guard of the request itself.
MAX_TEXT_CHARS = 1_000
# Audio larger than this is not an answer of a few sentences: it is refused unplayed.
MAX_AUDIO_BYTES = 20_000_000


@dataclass(frozen=True)
class Audio:
    """What a service gave back: the bytes of an audio file, and its format ("mp3")."""

    data: bytes
    format: str


def _status_error(status: int) -> ServiceError:
    if status in (401, 403):
        return ServiceError("auth")
    if status == 402:
        return ServiceError("credits")
    if status == 429:
        return ServiceError("rate_limit")
    return ServiceError("http_error")


async def synthesize(
    service: str,
    text: str,
    *,
    http: httpx.AsyncClient,
    key: str | None = None,
    voice: str = "",
    model: str = "",
) -> Audio:
    """The speech of `text` by `service` ("fish"): `voice` is the id of the voice model and `model`
    the service's model. ServiceError of a kind: "not_configured" for a service that is none or
    has no voice, "missing_secret" for one that needs a key and has none, and the kinds above."""
    if service == "fish":
        return await _fish(text, http=http, key=key, voice=voice, model=model)
    raise ServiceError("not_configured")


async def _fish(
    text: str, *, http: httpx.AsyncClient, key: str | None, voice: str, model: str
) -> Audio:
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
        "format": "mp3",
        "latency": "low",
    }
    try:
        response = await http.post(FISH_URL, headers=headers, json=body, timeout=TTS_TIMEOUT_S)
    except httpx.TimeoutException:
        raise ServiceError("timeout") from None
    except (httpx.TransportError, httpx.DecodingError):
        raise ServiceError("network") from None
    if not response.is_success:
        raise _status_error(response.status_code)
    data = response.content
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise ServiceError("bad_response")
    return Audio(data, "mp3")
