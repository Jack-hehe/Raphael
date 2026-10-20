"""`pricing.get`: what the models Raphael offers cost, for the settings page to show.

The prices come from OpenRouter's public model list (GET /models, no key needed): the model-level
price, which is the cheapest endpoint's headline price, in US dollars. What a call really costs
depends on the provider OpenRouter picks for it, so these are a guide, not a bill. Tiers (a higher
price for a very long prompt, or a cheaper one for batch use) are ignored: a dictation is short.

- A cleanup model is priced per million tokens: `input_per_mtok` and `output_per_mtok`.
- A speech-to-text model is priced per second of audio, which is shown per minute (`per_minute`), or
  per token like a chat model. The list does not say which: a model with a price for the tokens it
  writes (`completion`) is priced per token, any other per second. A per-second price over
  US$1 a minute cannot be right: Microsoft's MAI models are listed by the hour of audio instead
  (MAI-Transcribe 2 at "0.1", its published US$0.10 an hour; MAI-Transcribe 1.5 at "0.36"), so such
  a price is read per hour. One that is too dear even so is left unknown (None) instead of shown.

The answer is kept for 24 hours, in memory and in the `settings` table under a key that is no
setting (so it survives a restart and is not offered by `settings.get`). It is fetched only when
`pricing.get` is called, never during a dictation, with a 5 second deadline. If that fails, the
answer is the latest cached prices, however old, else the builtin table below: prices as of
2026-10-18, so the page always has something to show. `source` says which of the three it was:
"live" (just fetched), "cached" or "builtin".
"""

import asyncio
import json
import logging
import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .openrouter import CLEANUP_MODEL_CHOICES, OpenRouterError
from .settings import STT_CLOUD_MODELS

logger = logging.getLogger(__name__)

PRICING_TTL_MS = 24 * 60 * 60 * 1000
FETCH_TIMEOUT_S = 5.0
# The settings table's row for the cache. It is no setting: SettingsStore knows no such key.
CACHE_KEY = "cache.pricing"
# How the cached prices were read from the list. A cache of another reading is no cache: raise this
# whenever `parse_models` reads a price differently (2: the MAI models per hour, not unknown).
CACHE_READING = 2
# What a minute of speech-to-text can plausibly cost at most, in US dollars. The dearest model
# (Google Chirp 3) is 1.6 cents.
_MAX_PER_MINUTE = 1.0
_PER_MTOK = 1_000_000
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60

# Short names for the app to show.
MODEL_NAMES = {
    "openai/gpt-6-luna": "GPT-6 Luna",
    "anthropic/claude-haiku-5.5": "Claude Haiku 5.5",
    "google/gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite",
    "elevenlabs/scribe-v2": "ElevenLabs Scribe v2",
    "openai/gpt-transcribe": "OpenAI GPT Transcribe",
    "microsoft/mai-transcribe-2": "Microsoft MAI Transcribe 2",
    "assemblyai/universal-3-6-pro": "AssemblyAI Universal 3.6 Pro",
    "qwen/qwen3-asr-1.7b": "Qwen3 ASR 1.7B",
    "google/gemini-3.5-transcribe": "Gemini 3.5 Transcribe",
    "openai/whisper-large-v3-turbo": "Whisper Large v3 Turbo",
}


@dataclass(frozen=True)
class ModelPrice:
    """US dollars; None is unknown or does not apply."""

    per_minute: float | None = None  # of audio, for speech-to-text priced per second
    input_per_mtok: float | None = None  # per million tokens
    output_per_mtok: float | None = None


# OpenRouter's model list of 2026-10-18, read the way `parse_models` reads it (a test keeps the two
# alike, with tests/brain/fixtures/openrouter_models.json, a trimmed copy of that list).
BUILTIN_PRICES: dict[str, ModelPrice] = {
    "openai/gpt-6-luna": ModelPrice(input_per_mtok=0.1, output_per_mtok=0.5),
    "anthropic/claude-haiku-5.5": ModelPrice(input_per_mtok=0.1, output_per_mtok=0.5),
    "google/gemini-3.1-flash-lite": ModelPrice(input_per_mtok=0.25, output_per_mtok=1.5),
    "elevenlabs/scribe-v2": ModelPrice(per_minute=0.00183333),
    "openai/gpt-transcribe": ModelPrice(per_minute=0.0045),
    "microsoft/mai-transcribe-2": ModelPrice(per_minute=0.00166667),  # listed per hour
    "assemblyai/universal-3-6-pro": ModelPrice(per_minute=0.005),
    "qwen/qwen3-asr-1.7b": ModelPrice(per_minute=0.00045),
    "google/gemini-3.5-transcribe": ModelPrice(input_per_mtok=2.0, output_per_mtok=12.0),
    "openai/whisper-large-v3-turbo": ModelPrice(per_minute=0.0001998),
}


# --- reading the model list ---------------------------------------------------------------------


def _price(value: object) -> float | None:
    """A price of the model list (a string such as "0.0000001") as a number, or None if it is
    missing, no number, negative or not finite."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _tidy(number: float) -> float:
    """`number` to six significant digits: 0.09999999999999999 is 0.1."""
    return float(f"{number:.6g}")


def _per_mtok(per_token: float | None) -> float | None:
    return None if per_token is None else _tidy(per_token * _PER_MTOK)


def _cleanup_price(pricing: dict) -> ModelPrice:
    return ModelPrice(
        input_per_mtok=_per_mtok(_price(pricing.get("prompt"))),
        output_per_mtok=_per_mtok(_price(pricing.get("completion"))),
    )


def _stt_price(pricing: dict) -> ModelPrice:
    prompt, completion = _price(pricing.get("prompt")), _price(pricing.get("completion"))
    if completion is not None and completion > 0:  # priced per token, like a chat model
        return ModelPrice(input_per_mtok=_per_mtok(prompt), output_per_mtok=_per_mtok(completion))
    if prompt is None:
        return ModelPrice()
    per_minute = _tidy(prompt * _SECONDS_PER_MINUTE)  # `prompt` is a price per second of audio
    if per_minute > _MAX_PER_MINUTE:  # no price per second, but per hour (see the module docstring)
        per_minute = _tidy(prompt / _MINUTES_PER_HOUR)
    return ModelPrice(per_minute=per_minute if per_minute <= _MAX_PER_MINUTE else None)


def parse_models(models: object) -> dict[str, ModelPrice]:
    """The prices of the models Raphael offers, from the `data` list of GET /models.

    A model the list does not have is not in the result; entries that are no models are skipped.
    """
    cleanup, stt = set(CLEANUP_MODEL_CHOICES), set(STT_CLOUD_MODELS)
    prices: dict[str, ModelPrice] = {}
    for model in models if isinstance(models, list) else []:
        model_id = model.get("id") if isinstance(model, dict) else None
        if model_id not in cleanup and model_id not in stt:
            continue
        pricing = model.get("pricing")
        pricing = pricing if isinstance(pricing, dict) else {}
        prices[model_id] = _cleanup_price(pricing) if model_id in cleanup else _stt_price(pricing)
    return prices


# --- the cache ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    fetched_at_ms: int
    prices: dict[str, ModelPrice]


def _encode(snapshot: _Snapshot) -> str:
    prices = {
        model_id: {
            "per_minute": price.per_minute,
            "input_per_mtok": price.input_per_mtok,
            "output_per_mtok": price.output_per_mtok,
        }
        for model_id, price in snapshot.prices.items()
    }
    return json.dumps(
        {"reading": CACHE_READING, "fetched_at_ms": snapshot.fetched_at_ms, "prices": prices}
    )


def _decode(text: str) -> _Snapshot | None:
    """The snapshot saved as `text`, or None if it is not one (the cache is only a cache), or if
    it was read from the list by an older `parse_models` (another `reading`)."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("reading") != CACHE_READING:
        return None
    fetched_at_ms = data.get("fetched_at_ms")
    saved = data.get("prices")
    if type(fetched_at_ms) is not int or not isinstance(saved, dict):
        return None
    prices: dict[str, ModelPrice] = {}
    for model_id in (*CLEANUP_MODEL_CHOICES, *STT_CLOUD_MODELS):
        entry = saved.get(model_id)
        if isinstance(entry, dict):
            prices[model_id] = ModelPrice(
                per_minute=_price(entry.get("per_minute")),
                input_per_mtok=_price(entry.get("input_per_mtok")),
                output_per_mtok=_price(entry.get("output_per_mtok")),
            )
    return _Snapshot(fetched_at_ms, prices) if prices else None


# --- the service --------------------------------------------------------------------------------


def _view(prices: dict[str, ModelPrice], fetched_at_ms: int | None, source: str) -> dict:
    """The answer of `pricing.get`. A model without a price in `prices` is unknown."""
    cleanup, stt = [], []
    for model_id in CLEANUP_MODEL_CHOICES:
        price = prices.get(model_id, ModelPrice())
        cleanup.append(
            {
                "id": model_id,
                "name": MODEL_NAMES.get(model_id, model_id),
                "input_per_mtok": price.input_per_mtok,
                "output_per_mtok": price.output_per_mtok,
            }
        )
    for model_id in STT_CLOUD_MODELS:
        price = prices.get(model_id, ModelPrice())
        stt.append(
            {
                "id": model_id,
                "name": MODEL_NAMES.get(model_id, model_id),
                "per_minute": price.per_minute,
                "input_per_mtok": price.input_per_mtok,
                "output_per_mtok": price.output_per_mtok,
            }
        )
    return {"cleanup": cleanup, "stt": stt, "fetched_at_ms": fetched_at_ms, "source": source}


class PricingService:
    """The prices of the models, from OpenRouter, a day-long cache or the builtin table.

    `conn` is the brain's database (the cache is a row of its settings table), `client` has the
    `models(timeout_s=...)` coroutine of OpenRouterClient and `now_ms` is the clock. The fetch
    gives up after `fetch_timeout_s` in all. Use it on the thread that owns `conn`.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: Any,
        now_ms: Callable[[], int],
        *,
        fetch_timeout_s: float = FETCH_TIMEOUT_S,
    ) -> None:
        self._conn = conn
        self._client = client
        self._now_ms = now_ms
        self._fetch_timeout_s = fetch_timeout_s
        self._snapshot: _Snapshot | None = None
        self._loaded = False  # whether the database has been read for a snapshot
        self._lock = asyncio.Lock()  # callers at the same time share one fetch

    async def get(self) -> dict:
        """The answer of `pricing.get`."""
        async with self._lock:
            if not self._loaded:
                self._loaded = True
                self._snapshot = self._load()
            known = self._snapshot
            now = self._now_ms()
            # A clock that went back (the user changed it) counts the cache as stale.
            if known is not None and 0 <= now - known.fetched_at_ms < PRICING_TTL_MS:
                return _view(known.prices, known.fetched_at_ms, "cached")
            fresh = await self._fetch(now)
            if fresh is not None:
                self._snapshot = fresh
                self._save(fresh)
                return _view(fresh.prices, fresh.fetched_at_ms, "live")
            if known is not None:
                return _view(known.prices, known.fetched_at_ms, "cached")
            return _view(BUILTIN_PRICES, None, "builtin")

    async def _fetch(self, now_ms: int) -> _Snapshot | None:
        """The prices OpenRouter lists now, or None if it cannot be reached or lists none."""
        try:
            models = await asyncio.wait_for(
                self._client.models(timeout_s=self._fetch_timeout_s), self._fetch_timeout_s
            )
        except OpenRouterError as error:
            logger.info(
                "No model prices from OpenRouter: kind=%s status=%s", error.kind, error.status
            )
            return None
        except TimeoutError:
            logger.info("No model prices from OpenRouter: no answer in time")
            return None
        except Exception as exc:
            # The message could hold anything; the type says enough.
            logger.warning("No model prices from OpenRouter: %s", type(exc).__name__)
            return None
        prices = parse_models(models)
        if not prices:
            logger.warning("The model list of OpenRouter has none of the models Raphael offers")
            return None
        return _Snapshot(now_ms, prices)

    def _load(self) -> _Snapshot | None:
        try:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (CACHE_KEY,)
            ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("Could not read the cached model prices: %s", type(exc).__name__)
            return None
        return None if row is None else _decode(row["value"])

    def _save(self, snapshot: _Snapshot) -> None:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (CACHE_KEY, _encode(snapshot)),
                )
        except sqlite3.Error as exc:
            logger.warning("Could not save the model prices: %s", type(exc).__name__)
