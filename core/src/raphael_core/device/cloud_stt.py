"""Cloud speech-to-text on the device side: a stretch of speech goes to the brain's
`stt.transcribe`, and the local engine takes over whenever that does not work.

`CloudSttEngine` has the interface of the local engines (`transcribe(samples, hints=, language=)`),
so the dictation service uses it like any of them. It runs on the dictation's worker thread and
reaches the brain only through the router, with JSON params: the app hands it a `call(method,
params, timeout)` that runs `router.call` on the event loop and waits for it.

A refusal that passes (too many requests, a server error) is tried again after a short wait. Any
other failure (no key, the monthly limit, no network, a timeout, an error), or one that does not
pass, makes the segment go to the local engine, and the rest of that dictation too: a dictation
should not wait for a cloud that has just failed once per segment. Without a local engine the next
segment goes to the cloud again, since there is nothing else to hear it. Each kind of failure is logged as a warning once per process (the
app passes the same `warned` set to every engine), at debug level after that. Neither the audio nor
the text is ever logged.
"""

from __future__ import annotations

import base64
import concurrent.futures
import io
import logging
import time
import wave
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from raphael_core.device.dictation import join_segments
from raphael_core.protocol import BUDGET_EXCEEDED, NO_LOCAL_MODEL, NOT_CONFIGURED, RpcError

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
# The local engines take 20 seconds at a time at most (stt.SttEngine); a longer cloud segment is
# cut into equal pieces for them.
MAX_LOCAL_SECONDS = 20.0
# Beyond the brain's own timeout, min(30, 5 + seconds): the router call and the JSON of the audio.
_MARGIN_S = 3.0

# The kinds of failure that pass, and the waits before each new try of the same segment.
_PASSING = frozenset({"rate_limit", "server"})
_RETRY_WAITS_S = (1.0, 2.0)

Call = Callable[[str, dict, float], Any]


def wait_for(seconds: float) -> float:
    """How long the worker waits for the brain to transcribe `seconds` of speech."""
    return min(30.0, 5.0 + seconds) + _MARGIN_S


def encode_wav(samples: np.ndarray) -> bytes:
    """`samples` (float32 in [-1, 1] at 16 kHz) as a 16-bit PCM mono WAV file, in memory. Samples
    beyond full scale are clipped."""
    pcm = np.clip(np.asarray(samples, dtype=np.float32) * 32768.0, -32768, 32767)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(np.round(pcm).astype("<i2").tobytes())
    return buffer.getvalue()


def _kind(error: Exception) -> str:
    """What kind of failure `error` is, for the log: the brain's `data.kind` when it says one."""
    if isinstance(error, RpcError):
        data = error.data if isinstance(error.data, dict) else {}
        kind = data.get("kind")
        if isinstance(kind, str) and kind:
            return kind
        return {NOT_CONFIGURED: "no_key", BUDGET_EXCEEDED: "budget"}.get(error.code, "error")
    if isinstance(error, concurrent.futures.TimeoutError | TimeoutError):
        return "timeout"
    return "error"


class CloudSttEngine:
    """Speech-to-text through the brain's `stt.transcribe`, with `fallback()` (the local engine,
    which may block while it loads) for whatever the cloud does not do. One per dictation.

    `last_used` is the model that heard the latest segment: the cloud model's slug, or the local
    engine's name.
    """

    name = "cloud"

    def __init__(
        self,
        call: Call,
        *,
        fallback: Callable[[], Any],
        warned: set[str],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._call = call
        self._sleep = sleep
        self._fallback = fallback
        self._warned = warned
        self._failed: str | None = None
        self.last_used: str | None = None

    def transcribe(
        self, samples: np.ndarray, *, hints: Sequence[str] = (), language: str = "auto"
    ) -> str:
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1:
            raise ValueError(f"Expected 1-D samples, not {samples.ndim}-D")
        if not samples.any():  # no samples, or all of them zero: there is nothing to hear
            return ""
        if self._failed is not None:
            return self._local(samples, hints, language)
        try:
            return self._cloud_with_retries(samples, hints, language)
        except Exception as error:
            kind = _kind(error)
        try:
            text = self._local(samples, hints, language)
        except RpcError as error:
            if error.code == NO_LOCAL_MODEL:
                # Nothing else can hear the rest: the next segment tries the cloud again.
                self._warn(
                    kind, "Cloud speech-to-text failed (%s) and there is no local engine", "alone"
                )
            raise
        self._note_failure(kind)
        return text

    def _cloud_with_retries(self, samples: np.ndarray, hints: Sequence[str], language: str) -> str:
        for wait in _RETRY_WAITS_S:
            try:
                return self._cloud(samples, hints, language)
            except Exception as error:
                if _kind(error) not in _PASSING:
                    raise
                logger.debug("Cloud speech-to-text refused (%s); trying again", _kind(error))
            self._sleep(wait)
        return self._cloud(samples, hints, language)

    def _cloud(self, samples: np.ndarray, hints: Sequence[str], language: str) -> str:
        params = {
            "audio": base64.b64encode(encode_wav(samples)).decode("ascii"),
            "hints": list(hints),
            "language": language,
        }
        result = self._call("stt.transcribe", params, wait_for(samples.size / SAMPLE_RATE))
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str):
            raise RpcError(0, "The reply has no text", {"kind": "bad_response"})
        model = result.get("model")
        self.last_used = model if isinstance(model, str) and model else self.name
        return text

    def _note_failure(self, kind: str) -> None:
        self._failed = kind
        self._warn(
            kind,
            "Cloud speech-to-text failed (%s); this dictation uses the local engine "
            "(said once per kind of failure)",
        )

    def _warn(self, kind: str, message: str, case: str = "") -> None:
        """Logs `message` (with `kind`) as a warning once per process for each kind and case."""
        key = f"{kind}/{case}" if case else kind
        if key in self._warned:
            logger.debug(message, kind)
            return
        self._warned.add(key)
        logger.warning(message, kind)

    def _local(self, samples: np.ndarray, hints: Sequence[str], language: str) -> str:
        engine = self._fallback()
        self.last_used = getattr(engine, "name", None) or "local"
        limit = int(MAX_LOCAL_SECONDS * SAMPLE_RATE)
        count = -(-samples.size // limit)  # ceiling
        pieces = [samples] if count <= 1 else np.array_split(samples, count)
        return join_segments(
            [str(engine.transcribe(piece, hints=hints, language=language)) for piece in pieces]
        )
