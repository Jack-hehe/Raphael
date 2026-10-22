"""OpenRouter's model list, with what each model costs, fetched for `models.catalog` and kept.

The list is OpenRouter's public one (GET /models, no key needed); brain/catalog.py reads it and
says what its prices mean.

The list is kept for 24 hours, in memory and in the `settings` table under a key that is no
setting (so it survives a restart and is not offered by `settings.get`). It is fetched only when
`models.catalog` is called, never during a dictation, with a 5 second deadline. If that fails, the
answer is the latest list kept, however old, else the builtin table below: the models Raphael
calls when nobody chose another, at their prices of 2026-10-18, so the page always has something to
show. `source` says which of the
three it was: "live" (just fetched), "cached" or "builtin". Choosing a model reads what is kept
(`known`), and never fetches.
"""

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .catalog import (
    CAPABILITIES,
    LLM,
    ROLES,
    CatalogEntry,
    ModelPrice,
    entry_from_json,
    entry_to_json,
    parse_catalog,
)
from .openrouter import CLEANUP_FALLBACKS, STT_MODELS_WITH_HINTS, OpenRouterError

logger = logging.getLogger(__name__)

PRICING_TTL_MS = 24 * 60 * 60 * 1000
FETCH_TIMEOUT_S = 5.0
# The settings table's row for the cache. It is no setting: SettingsStore knows no such key.
CACHE_KEY = "cache.pricing"
# How the cached list was read. A cache of another reading is no cache: raise this whenever
# `parse_catalog` reads a model differently, or the cache holds something else (2: the MAI models
# per hour, not unknown; 3: every model Raphael could use, not only the prices of his own; 4: the
# batch variants left out; 5: whether a language model takes tools, as a capability; 6: the kind
# of a model whose modalities are not listed, from its `modality`).
CACHE_READING = 6

# The models Raphael calls when nobody chose another: the default of each role (the settings'
# DEFAULTS) and the fallbacks of the language models (openrouter.CLEANUP_FALLBACKS), at their
# prices in OpenRouter's model list of 2026-10-18, read the way `parse_catalog` reads it (a test
# keeps the two alike, with tests/brain/fixtures/openrouter_models.json, a trimmed copy of that
# list). It is no offering of models: every model the settings page offers comes from OpenRouter's
# list. It is what the page shows, and what may be chosen, before that list could ever be read, and
# the prices of those models for what has to know them without it.
BUILTIN_PRICES: dict[str, ModelPrice] = {
    "openai/gpt-6-luna": ModelPrice(input_per_mtok=0.1, output_per_mtok=0.5),
    "anthropic/claude-haiku-5.5": ModelPrice(input_per_mtok=0.1, output_per_mtok=0.5),
    "google/gemini-3.1-flash-lite": ModelPrice(input_per_mtok=0.25, output_per_mtok=1.5),
    "elevenlabs/scribe-v2": ModelPrice(per_minute=0.00183333),
}
# The maker and the name of each of those models, as the same list gives them ("OpenAI: GPT-6
# Luna"), for the catalog without the list.
BUILTIN_LISTING: dict[str, tuple[str, str]] = {
    "openai/gpt-6-luna": ("OpenAI", "GPT-6 Luna"),
    "anthropic/claude-haiku-5.5": ("Anthropic", "Claude Haiku 5.5"),
    "google/gemini-3.1-flash-lite": ("Google", "Gemini 3.1 Flash Lite"),
    "elevenlabs/scribe-v2": ("ElevenLabs", "Scribe v2"),
}


def builtin_kinds() -> dict[str, str]:
    """The kind of each model the builtin table must hold: the default of each role is of the
    role's kind, and the fallbacks of the language models are language models."""
    kinds = {role.default: role.kind for role in ROLES}
    kinds.update(dict.fromkeys(CLEANUP_FALLBACKS, LLM))
    return kinds


def builtin_catalog() -> list[CatalogEntry]:
    """The builtin table as a catalog: what `models.catalog` answers when OpenRouter's list cannot
    be had. Its models were all tried live, so every role of their kind can use them."""
    kinds = builtin_kinds()
    entries = []
    for model_id, price in BUILTIN_PRICES.items():
        kind = kinds[model_id]
        capabilities = set(CAPABILITIES[kind])
        if model_id not in STT_MODELS_WITH_HINTS:
            capabilities.discard("dictionary_hints")
        provider, name = BUILTIN_LISTING[model_id]
        entries.append(
            CatalogEntry(
                id=model_id,
                kind=kind,
                name=name,
                provider=provider,
                price=price,
                variable_price=False,
                context_length=None,
                created_at_ms=None,
                capabilities=frozenset(capabilities),
            )
        )
    return entries


# --- the cache ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    fetched_at_ms: int
    entries: tuple[CatalogEntry, ...]


def _encode(snapshot: _Snapshot) -> str:
    return json.dumps(
        {
            "reading": CACHE_READING,
            "fetched_at_ms": snapshot.fetched_at_ms,
            "models": [entry_to_json(entry) for entry in snapshot.entries],
        }
    )


def _decode(text: str) -> _Snapshot | None:
    """The snapshot saved as `text`, or None if it is not one (the cache is only a cache), or if
    it was read from the list by an older `parse_catalog` (another `reading`)."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("reading") != CACHE_READING:
        return None
    fetched_at_ms = data.get("fetched_at_ms")
    saved = data.get("models")
    if type(fetched_at_ms) is not int or not isinstance(saved, list):
        return None
    entries = tuple(entry for entry in map(entry_from_json, saved) if entry is not None)
    return _Snapshot(fetched_at_ms, entries) if entries else None


# --- the service --------------------------------------------------------------------------------


class PricingService:
    """OpenRouter's model list, with the prices, from OpenRouter, a day-long cache or the builtin
    table: what `models.catalog` answers with.

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

    async def catalog(self) -> tuple[list[CatalogEntry], int | None, str]:
        """Every model of the list, when the list was fetched (None for the builtin table), and
        where it came from: the parts of the answer of `models.catalog`."""
        snapshot, source = await self._latest()
        if snapshot is None:
            return builtin_catalog(), None, "builtin"
        return list(snapshot.entries), snapshot.fetched_at_ms, source

    def known(self) -> list[CatalogEntry] | None:
        """The models of the list OpenRouter gave last, however old, without fetching it; None if it
        never gave one. For what has to be quick: `settings.set` checks a model against it."""
        self._load_once()
        return None if self._snapshot is None else list(self._snapshot.entries)

    def _load_once(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._snapshot = self._load()

    async def _latest(self) -> tuple[_Snapshot | None, str]:
        """The list to answer with and where it came from; (None, "builtin") without one."""
        async with self._lock:
            self._load_once()
            known = self._snapshot
            now = self._now_ms()
            # A clock that went back (the user changed it) counts the cache as stale.
            if known is not None and 0 <= now - known.fetched_at_ms < PRICING_TTL_MS:
                return known, "cached"
            fresh = await self._fetch(now)
            if fresh is not None:
                self._snapshot = fresh
                self._save(fresh)
                return fresh, "live"
            if known is not None:
                return known, "cached"
            return None, "builtin"

    async def _fetch(self, now_ms: int) -> _Snapshot | None:
        """The models OpenRouter lists now, or None if it cannot be reached or lists none that
        Raphael could use."""
        try:
            models = await asyncio.wait_for(
                self._client.models(timeout_s=self._fetch_timeout_s), self._fetch_timeout_s
            )
        except OpenRouterError as error:
            logger.info(
                "No model list from OpenRouter: kind=%s status=%s", error.kind, error.status
            )
            return None
        except TimeoutError:
            logger.info("No model list from OpenRouter: no answer in time")
            return None
        except Exception as exc:
            # The message could hold anything; the type says enough.
            logger.warning("No model list from OpenRouter: %s", type(exc).__name__)
            return None
        entries = parse_catalog(models)
        if not entries:
            logger.warning("The model list of OpenRouter has no model Raphael could use")
            return None
        return _Snapshot(now_ms, tuple(entries))

    def _load(self) -> _Snapshot | None:
        try:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (CACHE_KEY,)
            ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("Could not read the cached model list: %s", type(exc).__name__)
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
            logger.warning("Could not save the model list: %s", type(exc).__name__)
