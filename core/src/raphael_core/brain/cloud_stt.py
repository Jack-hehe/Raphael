"""Cloud speech-to-text: `stt.transcribe`, a stretch of speech sent to OpenRouter.

The device side records and cuts the speech; when the settings say "cloud" it sends each stretch
here as a WAV file in base64 and falls back to the engine on this computer when this refuses or
fails. So this never falls back itself: it answers with the text, or raises an RpcError whose
`data.kind` says why not (the device side logs that kind, once):

- no_key (NOT_CONFIGURED): no OpenRouter key is stored
- budget (BUDGET_EXCEEDED): this month's spend has reached the limit
- key_store (INTERNAL_ERROR): the key store failed
- the kind of the OpenRouterError (INTERNAL_ERROR): network, timeout, server, auth, credits, ...

What a transcription costs is recorded in the spend, purpose "stt", like a cleanup. Nothing here
logs the audio, the text, the hints or the key. The status of OpenRouter that the app shows follows
the cleanup and the key checks only: a cloud failure here is followed by a cleanup call anyway.
"""

import asyncio
import base64
import binascii
import io
import logging
import math
import wave
from collections.abc import Callable
from typing import Any

from ..protocol import BUDGET_EXCEEDED, INTERNAL_ERROR, INVALID_PARAMS, NOT_CONFIGURED, RpcError
from .openrouter import OpenRouterClient, OpenRouterError
from .settings import SettingsStore
from .spend import SpendTracker

logger = logging.getLogger(__name__)

# OpenRouter takes base64 JSON up to 25 MB for most providers.
MAX_AUDIO_BYTES = 25 * 1024 * 1024
LANGUAGES = {"auto": None, "zh": "zh", "ja": "ja", "en": "en"}


def timeout_for(audio_seconds: float) -> float:
    """Seconds to wait for the transcription of `audio_seconds` of speech: 5 plus the length of
    the audio, 30 at most (OpenRouter's own limit per request is 60)."""
    return min(30.0, 5.0 + audio_seconds)


def _invalid(message: str) -> RpcError:
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


def _refusal(code: int, kind: str, message: str) -> RpcError:
    return RpcError(code, message, {"kind": kind})


def _audio_seconds(params: dict) -> tuple[str, float]:
    """The base64 audio of `params` and how long it is, or INVALID_PARAMS."""
    audio = params.get("audio")
    if not isinstance(audio, str) or not audio:
        raise _invalid("audio must be a WAV file in base64")
    if len(audio) > MAX_AUDIO_BYTES * 4 // 3 + 4:
        raise _invalid("audio is larger than 25 MB")
    try:
        data = base64.b64decode(audio, validate=True)
        with wave.open(io.BytesIO(data), "rb") as reader:
            frames, rate = reader.getnframes(), reader.getframerate()
    except (binascii.Error, ValueError, wave.Error, EOFError):
        raise _invalid("audio must be a WAV file in base64") from None
    if rate < 1:
        raise _invalid("audio must be a WAV file in base64")
    return audio, frames / rate


def _hints(params: dict) -> list[str]:
    hints = params.get("hints")
    if hints is None:
        return []
    if not isinstance(hints, list) or not all(isinstance(h, str) for h in hints):
        raise _invalid("hints must be a list of strings")
    return hints


def _language(params: dict) -> str | None:
    language = params.get("language", "auto")
    if language not in LANGUAGES:
        raise _invalid(f"language must be one of {', '.join(LANGUAGES)}")
    return LANGUAGES[language]


class CloudTranscriber:
    """`stt.transcribe {audio, hints, language}` -> `{text, model, cost_usd}`.

    `has_key` says whether a key is stored (and raises if the key store fails); the model is the
    `stt_cloud_model` setting, read for each call.
    """

    def __init__(
        self,
        client: OpenRouterClient,
        settings: SettingsStore,
        spend: SpendTracker,
        has_key: Callable[[], bool],
    ) -> None:
        self._client = client
        self._settings = settings
        self._spend = spend
        self._has_key = has_key

    async def transcribe(self, params: dict) -> dict[str, Any]:
        audio, seconds = _audio_seconds(params)
        hints = _hints(params)
        language = _language(params)
        try:
            has_key = self._has_key()
        except Exception as exc:
            logger.warning(
                "The key store failed to read the OpenRouter key: %s", type(exc).__name__
            )
            raise _refusal(
                INTERNAL_ERROR, "key_store", "Could not read the OpenRouter key"
            ) from None
        if not has_key:
            raise _refusal(NOT_CONFIGURED, "no_key", "No OpenRouter key is stored")
        if self._spend.is_over(self._settings.get("monthly_limit_usd")):
            raise _refusal(BUDGET_EXCEEDED, "budget", "The monthly limit has been reached")

        model = self._settings.get("stt_cloud_model")
        timeout = timeout_for(seconds)
        try:
            result = await asyncio.wait_for(
                self._client.transcribe(
                    model=model,
                    audio_b64=audio,
                    language=language,
                    hints=hints,
                    timeout_s=timeout,
                ),
                timeout,
            )
        except TimeoutError:
            logger.warning("Cloud speech-to-text timed out after %.1f s", timeout)
            raise _refusal(INTERNAL_ERROR, "timeout", "Cloud speech-to-text timed out") from None
        except OpenRouterError as error:
            logger.warning(
                "Cloud speech-to-text failed: kind=%s status=%s", error.kind, error.status
            )
            raise _refusal(
                INTERNAL_ERROR, error.kind, f"Cloud speech-to-text failed: {error.kind}"
            ) from None
        except Exception as exc:  # anything else the client lets out; its message may hold a key
            logger.warning("Cloud speech-to-text failed unexpectedly: %s", type(exc).__name__)
            raise _refusal(INTERNAL_ERROR, "unexpected", "Cloud speech-to-text failed") from None

        cost = result.cost_usd if math.isfinite(result.cost_usd) and result.cost_usd >= 0 else 0.0
        try:
            self._spend.record(
                purpose="stt",
                model=result.model,
                cost_usd=result.cost_usd,
                prompt_tokens=result.input_tokens,
                completion_tokens=result.output_tokens,
            )
        except Exception as exc:  # a cost it refuses, or a database that fails
            logger.warning("Could not record what a transcription cost: %s", type(exc).__name__)
        return {"text": result.text, "model": result.model, "cost_usd": cost}
