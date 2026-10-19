"""The brain as RPC methods: dictation, history, dictionary, settings, the OpenRouter key, status,
model prices and dictation statistics.

`Brain` owns the stores and the cleanup service and registers its methods on a Router. The device
side and the app reach it only through `router.call`, so every param arrives as JSON and every
result leaves as JSON. What the methods take and return is in the plan (Task 8); what the table
does not say is here:

- Params are checked before anything happens. A missing or wrongly typed one is
  RpcError(INVALID_PARAMS), and so is a value that a store refuses (it raises ValueError,
  KeyError or TypeError). A param that may be null may also be left out. A whole number is an
  int and a bool is no number.
- A dictation is not lost to bookkeeping. If the history cannot be written, the text still comes
  back (with id None); if the key store fails, it comes back as spoken, without cleanup.
- A dictation that is cancelled while its text is being cleaned (`brain.cancel_dictation`, for the
  `session_id` that `brain.finalize_dictation` was given) is paid for, because the request to the
  model is made, but it is not saved: it would be the latest dictation, which "paste last" pastes,
  and it would be counted in the statistics. The answer still carries its text, with id None.
- The status of OpenRouter is the latest contact: a key check or a cleanup call. It starts as
  "unknown" and it is "no_key" for as long as no key is stored, whatever happened before.
- Account usage is from the last successful key check. Status reads refresh it in the background
  after a minute, without making a dictation wait for that check.
- Nothing here logs the dictated text, a key or the message of a failure. A failure of OpenRouter
  is logged by its kind and status, any other by the name of its type: the message, cause or
  traceback of an OpenRouterError can hold the text of the key store.
"""

import asyncio
import contextlib
import hashlib
import logging
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import tzinfo
from typing import Any

from ..languages import chinese_variant, language_groups
from ..protocol import INTERNAL_ERROR, INVALID_PARAMS, RpcError
from ..router import Router
from .cleanup import CleanupService
from .cloud_stt import CloudTranscriber
from .dictionary import DictionaryStore, Learned
from .history import HistoryItem, HistoryStore
from .openrouter import KeyInfo, OpenRouterClient, OpenRouterError
from .pricing import PricingService
from .secrets import KeyStore
from .settings import SettingsStore
from .spend import SpendTracker
from .stats import StatsStore
from .suggest import suggest_words
from .textnorm import (
    apply_replacements,
    finish_cleaned_text,
    normalize_transcript,
    remove_stutters,
)

logger = logging.getLogger(__name__)

HISTORY_DEFAULT_LIMIT = 50
HISTORY_MAX_LIMIT = 200
# The most corrections `dictionary.learn` takes from one dictation.
LEARN_MAX = 3
# Dictionary suggestions look at this many days of history.
SUGGESTION_DAYS = 7
# How long a key check may take in all. The client's timeout applies to each stage of a request
# (connect, send, wait, read), so by itself a check could outlast the 15 s the app waits for it.
KEY_CHECK_TIMEOUT_S = 10.0
# Status polls return cached usage and refresh it in the background at most once a minute.
KEY_USAGE_REFRESH_INTERVAL_MS = 60_000

_DAY_MS = 86_400_000
# SQLite stores integers in 64 bits and refuses to bind a larger one.
_SQLITE_MAX_INT = 2**63 - 1
_SPEND_DAYS = 30
# While the core runs, the history is pruned at most this often (at the start of a dictation's
# finalize), besides at start-up and whenever the retention setting changes.
PRUNE_INTERVAL_MS = _DAY_MS


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


# --- params -------------------------------------------------------------------------------------


def _invalid(message: str) -> RpcError:
    """An INVALID_PARAMS error. `message` names a param, never a value: it may be the key."""
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


def _required(params: dict, name: str) -> Any:
    if name not in params:
        raise _invalid(f"{name} is required")
    return params[name]


def _str(params: dict, name: str) -> str:
    value = _required(params, name)
    if not isinstance(value, str):
        raise _invalid(f"{name} must be a string")
    return value


def _opt_str(params: dict, name: str) -> str | None:
    value = params.get(name)
    if value is not None and not isinstance(value, str):
        raise _invalid(f"{name} must be a string")
    return value


def _whole(value: Any, name: str) -> int:
    # `type is int`, not isinstance: True is an int to Python, but it is no id and no time.
    if type(value) is not int or not (-_SQLITE_MAX_INT - 1 <= value <= _SQLITE_MAX_INT):
        raise _invalid(f"{name} must be a whole number")
    return value


def _int(params: dict, name: str) -> int:
    return _whole(_required(params, name), name)


def _opt_int(params: dict, name: str) -> int | None:
    value = params.get(name)
    return None if value is None else _whole(value, name)


def _non_negative(params: dict, name: str) -> float:
    value = _required(params, name)
    if type(value) not in (int, float) or value < 0:
        raise _invalid(f"{name} must be a number, 0 or more")
    return value


def _milliseconds(params: dict, name: str) -> int:
    """A duration in ms. Any number is taken, since a float is a fair way to say 312.6 ms."""
    value = _non_negative(params, name)
    if value > _SQLITE_MAX_INT:
        raise _invalid(f"{name} is too large")
    return round(value)


def _opt_strings(params: dict, name: str) -> list[str]:
    value = params.get(name)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(text, str) for text in value):
        raise _invalid(f"{name} must be a list of strings")
    return value


@contextlib.contextmanager
def _store_call() -> Iterator[None]:
    """Run a store call whose params came from outside: what a store refuses is invalid params."""
    try:
        yield
    except KeyError as exc:
        raise _invalid(f"unknown key {exc.args[0]!r}" if exc.args else "unknown key") from exc
    except (ValueError, TypeError) as exc:
        raise _invalid(str(exc)) from exc


# --- the key and the status ---------------------------------------------------------------------


def _log_failure(what: str, error: OpenRouterError) -> None:
    logger.warning("%s: kind=%s status=%s", what, error.kind, error.status)


def _key_store_failure(exc: Exception, action: str) -> RpcError:
    """The error for a key store that failed. Its message is dropped: it may hold the key."""
    logger.warning("The key store failed to %s the OpenRouter key: %s", action, type(exc).__name__)
    return RpcError(INTERNAL_ERROR, f"Could not {action} the OpenRouter key")


@dataclass
class _OpenRouterState:
    """What is known about OpenRouter: how the latest contact went, and the last key info."""

    state: str = "unknown"  # "ok", "error" or "unknown"; "no_key" is told from the key store
    latency_ms: int | None = None
    checked_at_ms: int | None = None
    error: str | None = None  # the OpenRouterError's kind, when state is "error"
    key: KeyInfo | None = None  # kept through a failure: it is the last one OpenRouter gave


class Brain:
    """The brain's stores and cleanup, and the RPC methods that use them.

    `conn` is the open database, `client` the OpenRouter client and `keys` where the key lives.
    `now_ms` is the clock. Making a Brain prunes the history older than the retention setting and
    makes no network call. The history is pruned again when the retention changes, and at most once
    every PRUNE_INTERVAL_MS while dictations are finalized. `key_check_timeout_s` is the longest a
    key check may take. `tz` is the time zone of the days and months of the spend and the
    statistics (this machine's, if None).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: OpenRouterClient,
        keys: KeyStore,
        now_ms: Callable[[], int] = _wall_clock_ms,
        *,
        key_check_timeout_s: float = KEY_CHECK_TIMEOUT_S,
        tz: tzinfo | None = None,
    ) -> None:
        self._client = client
        self._keys = keys
        self._now_ms = now_ms
        self._key_check_timeout_s = key_check_timeout_s
        self._openrouter = _OpenRouterState()
        self._key_identity: bytes | None = None
        self._key_generation = 0
        self._key_check_revision = 0
        self._key_checked_at_ms: int | None = None
        self._key_refresh_task: asyncio.Task[None] | None = None
        self._closed = False
        # The dictations whose text is being cleaned, by session; True once one was cancelled.
        self._cleaning: dict[str, bool] = {}
        self._pruned_at_ms: int | None = None
        self._history = HistoryStore(conn)
        self._dictionary = DictionaryStore(conn, now_ms)
        self._settings = SettingsStore(conn)
        self._spend = SpendTracker(conn, now_ms, tz)
        self._stats = StatsStore(conn, now_ms, tz)
        self._pricing = PricingService(conn, client, now_ms)
        self._cleanup = CleanupService(
            client,
            self._settings,
            self._dictionary,
            self._spend,
            has_key=self._has_key,
            on_result=self._on_cleanup_result,
        )
        self._stt = CloudTranscriber(client, self._settings, self._spend, has_key=self._has_key)
        self._prune_history()

    async def aclose(self) -> None:
        """Stop the status refresh before its HTTP client is closed."""
        self._closed = True
        task = self._key_refresh_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def register(self, router: Router) -> None:
        """Put every method of the brain on `router`."""
        methods = {
            "brain.finalize_dictation": self._finalize_dictation,
            "brain.cancel_dictation": self._cancel_dictation,
            "stt.transcribe": self._stt.transcribe,
            "history.list": self._history_list,
            "history.latest": self._history_latest,
            "history.delete": self._history_delete,
            "dictionary.list": self._dictionary_list,
            "dictionary.add": self._dictionary_add,
            "dictionary.delete": self._dictionary_delete,
            "dictionary.suggestions": self._dictionary_suggestions,
            "dictionary.dismiss": self._dictionary_dismiss,
            "dictionary.learn": self._dictionary_learn,
            "settings.get": self._settings_get,
            "settings.set": self._settings_set,
            "languages.list": self._languages_list,
            "secrets.status": self._secrets_status,
            "secrets.set_openrouter_key": self._secrets_set_openrouter_key,
            "secrets.clear_openrouter_key": self._secrets_clear_openrouter_key,
            "status.get": self._status_get,
            "status.check_openrouter": self._status_check_openrouter,
            "pricing.get": self._pricing_get,
            "stats.summary": self._stats_summary,
        }
        for method, handler in methods.items():
            router.register(method, handler)

    # --- dictation ------------------------------------------------------------------------------

    async def _finalize_dictation(self, params: dict) -> dict:
        raw_text = _str(params, "raw_text")
        audio_seconds = _non_negative(params, "audio_seconds")
        stt_ms = _milliseconds(params, "stt_ms")
        target_app = _opt_str(params, "target_app")
        stt = _opt_str(params, "stt")
        session_id = _opt_str(params, "session_id")
        self._prune_history_if_due()

        rules = self._dictionary.replacements()
        # The Chinese written form the user chose decides the script of the text (or none).
        chinese = chinese_variant(self._settings.get("languages"))
        normalized = normalize_transcript(raw_text, rules, chinese=chinese)
        if not normalized.strip():
            return {
                "id": None,
                "text": "",
                "raw_text": "",
                "cleaned": False,
                "fallback_reason": "empty",
                "llm_ms": 0,
            }

        # The model reads the text as speech-to-text heard it, with the replacements but without
        # OpenCC: s2twp would hand it errors such as 隻要 for 只要, which it then keeps. The
        # normalized text is what is saved, and what is pasted when there is no cleanup.
        if session_id is not None:
            self._cleaning[session_id] = False
        try:
            result = await self._cleanup.clean(
                normalized,
                audio_seconds=audio_seconds,
                heard=apply_replacements(raw_text, rules),
                target_app=target_app,
            )
        finally:
            # Taken in the same breath as the text is saved, so that no cancel comes in between.
            cancelled = self._cleaning.pop(session_id, False)
        if result.model is not None and self._openrouter.state == "ok":
            # The model answered, so the time it took is a round trip to OpenRouter.
            self._openrouter.latency_ms = result.llm_ms
        if result.cleaned:
            text = finish_cleaned_text(result.text, rules, chinese=chinese)
        else:
            # A fallback is the normalized text already; doing it twice could repeat a replacement.
            # Nobody has tidied it, so its obvious stutters go; the history keeps it as it was.
            text = remove_stutters(result.text)

        item = HistoryItem(
            id=None,
            created_at_ms=self._now_ms(),
            kind="dictation",
            raw_text=normalized,
            text=text,
            cleaned=result.cleaned,
            fallback_reason=result.fallback_reason,
            target_app=target_app,
            audio_seconds=float(audio_seconds),
            stt_ms=stt_ms,
            llm_ms=result.llm_ms,
            model=result.model,
            cost_usd=result.cost_usd,
            stt=stt,
        )
        if cancelled:
            logger.info("A dictation was cancelled while its text was cleaned; not keeping it")
            entry_id = None
        else:
            entry_id = self._save(item)
        return {
            "id": entry_id,
            "text": text,
            "raw_text": normalized,
            "cleaned": result.cleaned,
            "fallback_reason": result.fallback_reason,
            "llm_ms": result.llm_ms,
        }

    async def _cancel_dictation(self, params: dict) -> dict:
        """`{session_id}`: the app cancelled that dictation. If its text is being cleaned, the
        cleanup goes on (it is paid for) but the text is not saved. A session that is not being
        cleaned, or no longer is, is no matter."""
        session_id = _str(params, "session_id")
        if session_id in self._cleaning:
            self._cleaning[session_id] = True
        return {}

    def _save(self, item: HistoryItem) -> int | None:
        """Write the history row, or return None if that fails: the text matters more."""
        try:
            return self._history.add(item)
        except Exception as exc:
            logger.warning("Could not write the dictation to the history: %s", type(exc).__name__)
            return None

    # --- history --------------------------------------------------------------------------------

    async def _history_list(self, params: dict) -> dict:
        query = _opt_str(params, "query")
        limit = _opt_int(params, "limit")
        before_ms = _opt_int(params, "before_ms")
        # A limit is clamped into 1..200. The lower end matters: SQLite reads a negative LIMIT as
        # "no limit", which would hand over the whole history.
        limit = HISTORY_DEFAULT_LIMIT if limit is None else min(max(limit, 1), HISTORY_MAX_LIMIT)
        with _store_call():
            items = self._history.list(query=query, limit=limit, before_ms=before_ms)
        return {"items": [asdict(item) for item in items]}

    async def _history_latest(self, params: dict) -> dict:
        latest = self._history.latest()
        return {"item": None if latest is None else asdict(latest)}

    async def _history_delete(self, params: dict) -> dict:
        item_id = _int(params, "id")
        with _store_call():
            return {"deleted": self._history.delete(item_id)}

    # --- dictionary -----------------------------------------------------------------------------

    async def _dictionary_list(self, params: dict) -> dict:
        return {"entries": [asdict(entry) for entry in self._dictionary.list()]}

    async def _dictionary_add(self, params: dict) -> dict:
        word = _str(params, "word")
        misheard = _opt_strings(params, "misheard")
        with _store_call():
            entry = self._dictionary.add(word, misheard)
        return {"entry": asdict(entry)}

    async def _dictionary_delete(self, params: dict) -> dict:
        entry_id = _int(params, "id")
        with _store_call():
            return {"deleted": self._dictionary.delete(entry_id)}

    async def _dictionary_suggestions(self, params: dict) -> dict:
        texts = self._history.texts_since(self._now_ms() - SUGGESTION_DAYS * _DAY_MS)
        known = set(self._dictionary.hint_words())
        suggestions = suggest_words(texts, known, self._dictionary.dismissed())
        return {"words": [{"word": word, "count": count} for word, count in suggestions]}

    async def _dictionary_dismiss(self, params: dict) -> dict:
        word = _str(params, "word")
        with _store_call():
            self._dictionary.dismiss(word)
        return {}

    async def _dictionary_learn(self, params: dict) -> dict:
        """`{corrections: [{word, misheard}]}` from the app's correction watch: the user replaced
        `misheard` with `word` in a dictation it pasted. Learns at most LEARN_MAX different ones,
        none if the setting is off, and returns `{learned: [word]}`: the words that changed the
        dictionary.

        The core decides what a correction becomes (`DictionaryStore.learn_pair`): the word is a
        term of the dictionary, and the pair a hint for the models until it has been seen again
        and is fit for a replacement rule. So a word in `learned` need not have a rule. Two
        optional fields are left out when empty: `rules`, the words of `learned` that gained a
        replacement rule, and `reverted`, the words of which a learned rule was withdrawn
        because the user changed the text back (those are not in `learned`). The words are never
        logged, only how many."""
        corrections = _required(params, "corrections")
        if not isinstance(corrections, list):
            raise _invalid("corrections must be a list")
        pairs: list[tuple[str, str]] = []
        for correction in corrections:
            if not isinstance(correction, dict):
                raise _invalid("corrections must be a list of objects")
            pairs.append((_str(correction, "word").strip(), _str(correction, "misheard").strip()))
        if not self._settings.get("learn_corrections"):
            return {"learned": []}
        learned: list[str] = []
        rules: list[str] = []
        reverted: list[str] = []
        # The same pair twice in one dictation is one sighting, not two.
        for word, misheard in list(dict.fromkeys(pairs))[:LEARN_MAX]:
            outcome = self._dictionary.learn_pair(word, misheard)
            if outcome is Learned.REVERTED:
                if misheard not in reverted:
                    reverted.append(misheard)  # the word whose rule was withdrawn
            elif outcome is not Learned.NOTHING and word not in learned:
                learned.append(word)
            if outcome is Learned.RULE and word not in rules:
                rules.append(word)
        logger.info(
            "Learned %d of %d corrections, withdrew %d", len(learned), len(pairs), len(reverted)
        )
        result: dict = {"learned": learned}
        if rules:
            result["rules"] = rules
        if reverted:
            result["reverted"] = reverted
        return result

    # --- settings -------------------------------------------------------------------------------

    async def _settings_get(self, params: dict) -> dict:
        return {"settings": self._settings.all()}

    async def _settings_set(self, params: dict) -> dict:
        key = _str(params, "key")
        value = _required(params, "value")
        with _store_call():
            self._settings.set(key, value)
        if key == "history_retention_days":
            self._prune_history()
        return {"settings": self._settings.all()}

    async def _languages_list(self, params: dict) -> dict:
        """The languages the `languages` setting can hold, by group, in the order shown."""
        return language_groups()

    # --- the key --------------------------------------------------------------------------------

    def _has_key(self) -> bool:
        """Whether a key is stored. Raises if the key store fails; each caller decides what then."""
        key = self._keys.get()
        self._remember_key(key)
        return self._key_identity is not None

    def _has_key_or_none(self) -> bool | None:
        """Like `_has_key`, but None when the key store fails (logged by the type of the error)."""
        try:
            return self._has_key()
        except Exception as exc:
            logger.warning(
                "The key store failed to read the OpenRouter key: %s", type(exc).__name__
            )
            return None

    def _remember_key(self, key: str | None) -> None:
        """Discard status from a different key, including an in-flight refresh's result.

        Keep only a digest for comparison, never an extra copy of the credential.
        """
        key = (key or "").strip()
        identity = hashlib.sha256(key.encode()).digest() if key else None
        if identity == self._key_identity:
            return
        self._key_identity = identity
        self._key_generation += 1
        self._key_checked_at_ms = None
        self._openrouter = _OpenRouterState()
        if self._key_refresh_task is not None:
            self._key_refresh_task.cancel()
            self._key_refresh_task = None

    async def _secrets_status(self, params: dict) -> dict:
        try:
            return {"has_key": self._has_key()}
        except Exception as exc:
            raise _key_store_failure(exc, "read") from None

    async def _secrets_set_openrouter_key(self, params: dict) -> dict:
        # What is checked is what is stored, so the key is trimmed first, as the client trims it.
        key = _str(params, "key").strip()
        if not key:
            return {"valid": False, "reason": "auth"}
        try:
            info, latency_ms = await self._check_key(key)
        except OpenRouterError as error:
            _log_failure("The OpenRouter key was not accepted", error)
            return {"valid": False, "reason": error.kind}
        try:
            self._keys.set(key)
        except Exception as exc:
            raise _key_store_failure(exc, "store") from None
        self._remember_key(key)
        self._key_check_revision += 1
        self._note("ok", latency_ms=latency_ms, key=info)
        return {
            "valid": True,
            "label": info.label,
            "limit": info.limit,
            "limit_remaining": info.limit_remaining,
            "limit_reset": info.limit_reset,
        }

    async def _secrets_clear_openrouter_key(self, params: dict) -> dict:
        try:
            self._keys.clear()
        except Exception as exc:
            raise _key_store_failure(exc, "remove") from None
        self._remember_key(None)
        self._openrouter = _OpenRouterState()  # what was known belonged to the key that is gone
        return {}

    async def _check_key(self, key: str | None) -> tuple[KeyInfo, int]:
        """What OpenRouter says about `key` (the stored one if None), and how long it took in ms.

        Raises OpenRouterError; of kind "timeout" if OpenRouter does not answer in time.
        """
        started = time.perf_counter()
        call = self._client.key_info() if key is None else self._client.key_info(key=key)
        try:
            info = await asyncio.wait_for(call, self._key_check_timeout_s)
        except OpenRouterError:
            raise
        except TimeoutError:
            raise OpenRouterError(0, "timeout", "OpenRouter did not answer in time") from None
        except Exception as exc:
            # The client plans for nothing but OpenRouterError. This is a bug in it, and its
            # message could hold the key, which the router would log and send on: keep the type.
            logger.warning("The OpenRouter key check failed unexpectedly: %s", type(exc).__name__)
            raise RpcError(INTERNAL_ERROR, "Could not check the OpenRouter key") from None
        return info, round((time.perf_counter() - started) * 1000)

    # --- status ---------------------------------------------------------------------------------

    def _note(
        self,
        state: str,
        *,
        latency_ms: int | None = None,
        error: str | None = None,
        key: KeyInfo | None = None,
    ) -> None:
        """Record how the latest contact with OpenRouter went. The last key info stays if no new
        one is given."""
        if key is not None:
            self._key_checked_at_ms = self._now_ms()
        self._openrouter = _OpenRouterState(
            state=state,
            latency_ms=latency_ms,
            checked_at_ms=self._now_ms(),
            error=error,
            key=key if key is not None else self._openrouter.key,
        )

    def _on_cleanup_result(self, error: Exception | None) -> None:
        """How a call to the model went, as the cleanup service reports it."""
        if error is None:
            self._note("ok")
        elif isinstance(error, OpenRouterError):
            self._note("error", error=error.kind)
        else:
            self._note("error", error="unexpected")

    def _openrouter_view(self, has_key: bool | None) -> dict:
        """The `openrouter` object. `has_key` is None when the key store failed."""
        if has_key is None:
            # The client calls a key it cannot read an auth error, and so does the status.
            state = _OpenRouterState(state="error", error="auth")
        else:
            state = self._openrouter if has_key else _OpenRouterState(state="no_key")
        view = asdict(state)
        info = state.key
        view["usage"] = (
            None
            if info is None
            else {
                "daily": info.usage_daily,
                "weekly": info.usage_weekly,
                "monthly": info.usage_monthly,
                "total": info.usage_total,
                "limit": info.limit,
                "limit_remaining": info.limit_remaining,
            }
        )
        return view

    def _refresh_key_usage_if_due(self) -> None:
        task = self._key_refresh_task
        if self._closed or (task is not None and not task.done()):
            return
        now, last = self._now_ms(), self._key_checked_at_ms
        if last is not None and 0 <= now - last < KEY_USAGE_REFRESH_INTERVAL_MS:
            return
        # Record attempts too, so an outage does not turn every UI poll into another request.
        self._key_checked_at_ms = now
        self._key_refresh_task = asyncio.create_task(self._refresh_key_usage())

    async def _refresh_key_usage(self) -> None:
        try:
            await self._status_check_openrouter({})
        except RpcError:
            # _check_key already logs the type only; background failures never escape as an
            # unobserved task exception or put the key store's message into a log.
            pass
        finally:
            if self._key_refresh_task is asyncio.current_task():
                self._key_refresh_task = None

    async def _status_get(self, params: dict) -> dict:
        has_key = self._has_key_or_none()
        if has_key:
            self._refresh_key_usage_if_due()
        latest = self._history.latest()
        return {
            "openrouter": self._openrouter_view(has_key),
            "spend": {
                "month_usd": self._spend.month_total(),
                "today_usd": self._spend.today_total(),
                "limit_usd": self._settings.get("monthly_limit_usd"),
                "forecast_usd": self._spend.forecast_month_end(),
                "daily": self._spend.daily_totals(_SPEND_DAYS),
            },
            "last": None
            if latest is None
            else {
                "text": latest.text,
                "created_at_ms": latest.created_at_ms,
                "target_app": latest.target_app,
                "stt_ms": latest.stt_ms,
                "llm_ms": latest.llm_ms,
            },
        }

    async def _status_check_openrouter(self, params: dict) -> dict:
        has_key = self._has_key_or_none()
        if has_key:
            generation = self._key_generation
            self._key_check_revision += 1
            revision = self._key_check_revision
            self._key_checked_at_ms = self._now_ms()
            try:
                info, latency_ms = await self._check_key(None)
            except OpenRouterError as error:
                _log_failure("The OpenRouter check failed", error)
                has_key = self._has_key_or_none()
                if (
                    has_key
                    and generation == self._key_generation
                    and revision == self._key_check_revision
                ):
                    self._note("error", error=error.kind)
            except RpcError:
                has_key = self._has_key_or_none()
                if (
                    has_key
                    and generation == self._key_generation
                    and revision == self._key_check_revision
                ):
                    self._note("error", error="unexpected")
                raise
            else:
                has_key = self._has_key_or_none()
                # An older background response must not replace a newer explicit check's data,
                # even when both requests checked the same key.
                if (
                    has_key
                    and generation == self._key_generation
                    and revision == self._key_check_revision
                ):
                    self._note("ok", latency_ms=latency_ms, key=info)
        return self._openrouter_view(has_key)

    # --- pricing and statistics -----------------------------------------------------------------

    async def _pricing_get(self, params: dict) -> dict:
        return await self._pricing.get()

    async def _stats_summary(self, params: dict) -> dict:
        if "days" in params:
            return self._stats.summary(days=params["days"])
        return self._stats.summary()

    # --- history retention ----------------------------------------------------------------------

    def _prune_history_if_due(self) -> None:
        last = self._pruned_at_ms
        now = self._now_ms()
        # A clock that went back (the user changed it) counts as due, rather than never.
        if last is None or now - last >= PRUNE_INTERVAL_MS or now < last:
            self._prune_history()

    def _prune_history(self) -> None:
        # Tried now, whether it works or not: a failing prune is not retried at every dictation.
        self._pruned_at_ms = self._now_ms()
        try:
            days = self._settings.get("history_retention_days")
            if days < 0:
                return  # not a retention: keep what there is rather than guess
            # A retention longer than the epoch has nothing to prune, and a huge one would overflow
            # SQLite's 64-bit integers, so the cutoff never goes below 0.
            self._history.prune(max(self._now_ms() - days * _DAY_MS, 0))
        except sqlite3.Error as exc:
            # A history that cannot be pruned is no reason to stop dictating.
            logger.warning("Could not prune the history: %s", type(exc).__name__)
