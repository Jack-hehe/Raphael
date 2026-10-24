"""The brain as RPC methods: dictation, cloud speech-to-text, history, dictionary, settings, the
OpenRouter key, status, OpenRouter's catalog of models with their prices, and dictation statistics.

`Brain` owns the stores and the cleanup service and registers its methods on a Router (`register`
lists them). The device side and the app reach it only through `router.call`, so every param
arrives as JSON and every result leaves as JSON. What each method takes and returns is in its
handler below; what a handler alone does not show is here:

- Params are checked before anything happens. A missing or wrongly typed one is
  RpcError(INVALID_PARAMS), and so is a value that a store refuses (it raises ValueError,
  KeyError or TypeError). A param that may be null may also be left out. A whole number is an
  int and a bool is no number.
- A dictation is not lost to bookkeeping. If the history cannot be written, the text still comes
  back (with id None); if the key store fails, it comes back as spoken, without cleanup.
- What the voice assistant is asked (`brain.normalize_transcript`) is a transcript with the
  dictionary's replacements and the Chinese script of the chosen variant, as a dictation is
  normalized before its cleanup. No model is asked, nothing is written and nothing is paid for.
- A dictation that is cancelled while its text is being cleaned (`brain.cancel_dictation`, for the
  `session_id` that `brain.finalize_dictation` was given) is paid for, because the request to the
  model is made, but it is not saved: it would be the latest dictation, which "paste last" pastes,
  and it would be counted in the statistics. The answer still carries its text, with id None.
- The status of OpenRouter is the latest contact: a key check or a cleanup call. It starts as
  "unknown" and it is "no_key" for as long as no key is stored, whatever happened before.
- Account usage is from the last successful key check. Status reads refresh it in the background
  after a minute, without making a dictation wait for that check.
- A model is chosen (`settings.set` of `cleanup_model`, `agent_model` or `stt_cloud_model`) from
  the catalog OpenRouter gave last, as `models.catalog` showed it: any model it lists that the
  role can use (catalog.choice_problem). Before any list could be had, the catalog is the builtin
  table of the models Raphael calls when nobody chose another (pricing.builtin_catalog).
  Choosing fetches nothing. When `models.catalog` finds a chosen model gone from OpenRouter's
  list, the setting goes back to its default, and the answer's `replaced` says so for the app to
  tell the user, as it tells of a setting it could not change. The model that reads images
  (`vision_model`) is "" for the answer model, or a model of that list that reads images.
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
from dataclasses import asdict, dataclass, replace
from datetime import tzinfo
from typing import Any

from ..clock import wall_clock_ms
from ..languages import chinese_variant, language_groups
from ..protocol import INTERNAL_ERROR, RpcError, invalid_params
from ..router import Router
from .catalog import (
    IMAGE_INPUT,
    LLM,
    ROLE_OF_SETTING,
    ROLES,
    CatalogEntry,
    Role,
    catalog_view,
    choice_problem,
    find,
)
from .cleanup import CleanupService, join_parts, split_for_cleanup
from .cloud_stt import CloudTranscriber
from .dictionary import DictionaryStore, Learned
from .history import HistoryItem, HistoryStore
from .openrouter import KeyInfo, OpenRouterError
from .providers.base import ModelProvider
from .pricing import PricingService, builtin_catalog
from .providers.registry import ProviderRegistry, ProviderRouter
from .secrets import KeyStore
from .settings import SettingsStore, checked
from .spend import SpendTracker
from .stats import StatsStore
from .usage import UsageAnalytics
from .textnorm import (
    apply_replacements,
    finish_cleaned_text,
    normalize_transcript,
    remove_stutters,
)

logger = logging.getLogger(__name__)

HISTORY_DEFAULT_LIMIT = 50
HISTORY_MAX_LIMIT = 200
# The most words `dictionary.import` takes in one call.
IMPORT_MAX = 5000
# The most corrections `dictionary.learn` takes from one dictation.
LEARN_MAX = 3
# How long a key check may take in all. The client's timeout applies to each stage of a request
# (connect, send, wait, read), so by itself a check could outlast the 15 s the app waits for it.
KEY_CHECK_TIMEOUT_S = 10.0
# Status polls return cached usage and refresh it in the background at most once a minute.
KEY_USAGE_REFRESH_INTERVAL_MS = 60_000

_DAY_MS = 86_400_000
# Why a model cannot be chosen (catalog.choice_problem), as the message of the error; any other
# problem is a reason the role cannot use the model.
_MODEL_PROBLEMS = {
    "not_listed": "Setting {key!r} must be a model that OpenRouter lists",
}
_MODEL_UNUSABLE = "Setting {key!r} must be a model it can use, and this one cannot: {reason}"
# SQLite stores integers in 64 bits and refuses to bind a larger one.
_SQLITE_MAX_INT = 2**63 - 1
_SPEND_DAYS = 30
# While the core runs, the history is pruned at most this often (at the start of a dictation's
# finalize), besides at start-up and whenever the retention setting changes.
PRUNE_INTERVAL_MS = _DAY_MS


# --- params -------------------------------------------------------------------------------------


def _required(params: dict, name: str) -> Any:
    if name not in params:
        raise invalid_params(f"{name} is required")
    return params[name]


def _str(params: dict, name: str) -> str:
    value = _required(params, name)
    if not isinstance(value, str):
        raise invalid_params(f"{name} must be a string")
    return value


def _opt_str(params: dict, name: str) -> str | None:
    value = params.get(name)
    if value is not None and not isinstance(value, str):
        raise invalid_params(f"{name} must be a string")
    return value


def _whole(value: Any, name: str) -> int:
    # `type is int`, not isinstance: True is an int to Python, but it is no id and no time.
    if type(value) is not int or not (-_SQLITE_MAX_INT - 1 <= value <= _SQLITE_MAX_INT):
        raise invalid_params(f"{name} must be a whole number")
    return value


def _int(params: dict, name: str) -> int:
    return _whole(_required(params, name), name)


def _opt_int(params: dict, name: str) -> int | None:
    value = params.get(name)
    return None if value is None else _whole(value, name)


def _non_negative(params: dict, name: str) -> float:
    value = _required(params, name)
    if type(value) not in (int, float) or value < 0:
        raise invalid_params(f"{name} must be a number, 0 or more")
    return value


def _milliseconds(params: dict, name: str) -> int:
    """A duration in ms. Any number is taken, since a float is a fair way to say 312.6 ms."""
    value = _non_negative(params, name)
    if value > _SQLITE_MAX_INT:
        raise invalid_params(f"{name} is too large")
    return round(value)


def _opt_strings(params: dict, name: str) -> list[str]:
    value = params.get(name)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(text, str) for text in value):
        raise invalid_params(f"{name} must be a list of strings")
    return value


@contextlib.contextmanager
def _store_call() -> Iterator[None]:
    """Run a store call whose params came from outside: what a store refuses is invalid params."""
    try:
        yield
    except KeyError as exc:
        raise invalid_params(f"unknown key {exc.args[0]!r}" if exc.args else "unknown key") from exc
    except (ValueError, TypeError) as exc:
        raise invalid_params(str(exc)) from exc


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
        client: ModelProvider,
        keys: KeyStore,
        now_ms: Callable[[], int] = wall_clock_ms,
        *,
        key_check_timeout_s: float = KEY_CHECK_TIMEOUT_S,
        tz: tzinfo | None = None,
        providers: ProviderRegistry | None = None,
    ) -> None:
        self._client = client
        self._providers = providers
        self._notify_providers = lambda: None
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
        self._history = HistoryStore(conn, tz=tz)
        self._dictionary = DictionaryStore(conn, now_ms)
        self._settings = SettingsStore(conn)
        self._spend = SpendTracker(conn, now_ms, tz)
        self._stats = StatsStore(conn, now_ms, tz)
        self._usage = UsageAnalytics(conn, now_ms, tz, providers)
        self._pricing = PricingService(conn, client, now_ms)
        self._cleanup = CleanupService(
            client,
            self._settings,
            self._dictionary,
            self._spend,
            has_key=lambda: self._model_available("cleanup_model"),
            on_model_result=self._on_cleanup_result,
        )
        self._stt = CloudTranscriber(client, self._settings, self._spend,
                                     has_key=lambda: self._model_available("stt_cloud_model"))
        self._prune_history()

    def known_models(self) -> list[CatalogEntry] | None:
        """The models of OpenRouter's list as it was given last, however old, without fetching it;
        None if it never gave one. What the assistant asks, request by request, what a model can
        do (`tool_calling`): quick, and never a wait for the network."""
        known = self._pricing.known()
        if self._providers and not self._providers.config("openrouter")["enabled"]:
            known = []
        extra = self._providers.catalog_entries() if self._providers else []
        if not extra:
            return known
        return [*(known if known is not None else builtin_catalog()), *extra]

    def _model_available(self, setting: str) -> bool:
        if isinstance(self._client, ProviderRouter):
            purpose = "stt" if setting == "stt_cloud_model" else setting.removesuffix("_model")
            return self._client.for_purpose(purpose).available(self._settings.get(setting))
        return self._has_key()

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
        self._notify_action_mode = lambda: router.notify("action_mode.changed", {})
        if self._providers:
            self._notify_providers = lambda: router.notify("providers.changed", {})
        methods = {
            "brain.finalize_dictation": self._finalize_dictation,
            "brain.normalize_transcript": self._normalize_transcript,
            "brain.cancel_dictation": self._cancel_dictation,
            "stt.transcribe": self._stt.transcribe,
            "history.list": self._history_list,
            "history.latest": self._history_latest,
            "history.delete": self._history_delete,
            "history.clear": self._history_clear,
            "dictionary.list": self._dictionary_list,
            "dictionary.add": self._dictionary_add,
            "dictionary.import": self._dictionary_import,
            "dictionary.delete": self._dictionary_delete,
            "dictionary.learn": self._dictionary_learn,
            "settings.get": self._settings_get,
            "settings.set": self._settings_set,
            "languages.list": self._languages_list,
            "secrets.status": self._secrets_status,
            "secrets.set_openrouter_key": self._secrets_set_openrouter_key,
            "secrets.clear_openrouter_key": self._secrets_clear_openrouter_key,
            "status.get": self._status_get,
            "status.check_openrouter": self._status_check_openrouter,
            "models.catalog": self._models_catalog,
            "stats.summary": self._stats_summary,
            "usage.analytics": self._usage.read,
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
        # A long dictation is cleaned in pieces (cleanup.CLEANUP_CHUNK_CHARS), cut where
        # speech-to-text ended a sentence, each made ready the way the whole is.
        pieces = split_for_cleanup(raw_text)
        chunks = (
            [
                (normalize_transcript(piece, rules, chinese=chinese), apply_replacements(piece, rules))
                for piece in pieces
            ]
            if len(pieces) > 1
            else None
        )
        if session_id is not None:
            self._cleaning[session_id] = False
        try:
            result = await self._cleanup.clean(
                normalized,
                audio_seconds=audio_seconds,
                heard=apply_replacements(raw_text, rules),
                target_app=target_app,
                chunks=chunks,
                is_cancelled=lambda: self._cleaning.get(session_id, False),
            )
        finally:
            # Taken in the same breath as the text is saved, so that no cancel comes in between.
            cancelled = self._cleaning.pop(session_id, False)
        if result.model is not None and not result.model.startswith("@") and self._openrouter.state == "ok":
            # The model answered, so the time it took is a round trip to OpenRouter.
            self._openrouter.latency_ms = result.llm_ms
        if result.parts:
            # Cleaned in pieces, some of which may have fallen back: each is finished as it would
            # be alone, and they are joined with what they were cut at: a paragraph break, a space.
            text = join_parts(
                [
                    replace(
                        part,
                        text=finish_cleaned_text(part.text, rules, chinese=chinese)
                        if part.cleaned
                        else remove_stutters(part.text),
                    )
                    for part in result.parts
                ]
            )
        elif result.cleaned:
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

    async def _normalize_transcript(self, params: dict) -> dict:
        """`{raw_text}` -> `{text}`: what speech-to-text heard, with the dictionary's replacements
        and the Chinese written form the user chose, as `brain.finalize_dictation` starts with.
        This is the whole of it: for the voice assistant, whose requests are not cleaned."""
        raw_text = _str(params, "raw_text")
        chinese = chinese_variant(self._settings.get("languages"))
        return {"text": normalize_transcript(raw_text, self._dictionary.replacements(), chinese=chinese)}

    async def _cancel_dictation(self, params: dict) -> dict:
        """`{session_id}`: the app cancelled that dictation. If its text is being cleaned, the
        current request goes on (it is paid for), without a retry, but the text is not saved.
        A session that is not being
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
        after_ms = _opt_int(params, "after_ms")
        # A limit is clamped into 1..200. The lower end matters: SQLite reads a negative LIMIT as
        # "no limit", which would hand over the whole history.
        limit = HISTORY_DEFAULT_LIMIT if limit is None else min(max(limit, 1), HISTORY_MAX_LIMIT)
        with _store_call():
            items = self._history.list(
                query=query, limit=limit, before_ms=before_ms, after_ms=after_ms
            )
        return {"items": [asdict(item) for item in items]}

    async def _history_latest(self, params: dict) -> dict:
        latest = self._history.latest()
        return {"item": None if latest is None else asdict(latest)}

    async def _history_delete(self, params: dict) -> dict:
        item_id = _int(params, "id")
        with _store_call():
            return {"deleted": self._history.delete(item_id)}

    async def _history_clear(self, params: dict) -> dict:
        """Delete the whole dictation history: Settings › 資料, once its 10 seconds of 復原 ran out."""
        with _store_call():
            return {"deleted": self._history.clear()}

    # --- dictionary -----------------------------------------------------------------------------

    async def _dictionary_list(self, params: dict) -> dict:
        return {"entries": [asdict(entry) for entry in self._dictionary.list()]}

    async def _dictionary_add(self, params: dict) -> dict:
        word = _str(params, "word")
        misheard = _opt_strings(params, "misheard")
        with _store_call():
            entry = self._dictionary.add(word, misheard)
        return {"entry": asdict(entry)}

    async def _dictionary_import(self, params: dict) -> dict:
        """`{words: [word]}` -> `{added, skipped, invalid}`: adds the words as manual entries in
        one transaction (at most IMPORT_MAX). A word that is in the dictionary already (in any
        letter case) or repeats is skipped, a blank one is invalid, and a word the user had
        dismissed is added all the same. Only the counts are logged."""
        if _required(params, "words") is None:
            raise invalid_params("words must be a list of strings")
        words = _opt_strings(params, "words")
        if len(words) > IMPORT_MAX:
            raise invalid_params(f"words takes at most {IMPORT_MAX} entries")
        with _store_call():
            added, skipped, invalid = self._dictionary.import_words(words)
        logger.info(
            "Imported %d words into the dictionary (%d skipped, %d invalid)",
            added,
            skipped,
            invalid,
        )
        return {"added": added, "skipped": skipped, "invalid": invalid}

    async def _dictionary_delete(self, params: dict) -> dict:
        entry_id = _int(params, "id")
        with _store_call():
            return {"deleted": self._dictionary.delete(entry_id)}

    async def _dictionary_learn(self, params: dict) -> dict:
        """`{corrections: [{word, misheard}]}` from the app's correction watch: the user replaced
        `misheard` with `word` in a dictation it pasted. Learns at most LEARN_MAX different ones
        and returns `{learned: [word]}`: the words that changed the dictionary. Learning is always
        on: there is no setting to turn it off.

        The core decides what a correction becomes (`DictionaryStore.learn_pair`): the word is a
        term of the dictionary, and the pair a hint for the models until it has been seen again
        and is fit for a replacement rule. So a word in `learned` need not have a rule. Two
        optional fields are left out when empty: `rules`, the words of `learned` that gained a
        replacement rule, and `reverted`, the words of which a learned rule was withdrawn
        because the user changed the text back (those are not in `learned`). The words are never
        logged, only how many."""
        corrections = _required(params, "corrections")
        if not isinstance(corrections, list):
            raise invalid_params("corrections must be a list")
        pairs: list[tuple[str, str]] = []
        for correction in corrections:
            if not isinstance(correction, dict):
                raise invalid_params("corrections must be a list of objects")
            pairs.append((_str(correction, "word").strip(), _str(correction, "misheard").strip()))
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
            role = ROLE_OF_SETTING.get(key)
            if role is not None:
                self._check_model(role, value)
            if key == "vision_model":
                self._check_vision_model(value)
            self._settings.set(key, value)
        if key == "action_mode":
            self._notify_action_mode()
        if key == "history_retention_days":
            self._prune_history()
        return {"settings": self._settings.all()}

    def _check_model(self, role: Role, value: Any) -> None:
        """ValueError (or the store's TypeError) if `value` cannot be chosen for `role`, by the
        catalog OpenRouter gave last, or the builtin one if it never gave one: the catalog the
        settings page showed (catalog.choice_problem)."""
        from ..agent.cli.discovery import brain_ref
        _, model_id = checked(role.setting, value)
        if role.setting == "agent_model" and brain_ref(model_id):
            return
        current = self._settings.get(role.setting)
        known = self.known_models()
        listed = builtin_catalog() if known is None else known
        problem = choice_problem(role, model_id, listed, current)
        if problem is not None:
            message = _MODEL_PROBLEMS.get(problem, _MODEL_UNUSABLE)
            raise ValueError(message.format(key=role.setting, reason=problem))

    def _check_vision_model(self, value: Any) -> None:
        """ValueError (or the store's TypeError) if `value` cannot be the model that reads images:
        "" (the answer model) always can, and so can the model chosen now; another must be a
        language model of OpenRouter's list that reads images (`IMAGE_INPUT`). Before any list
        could be had, a model id is taken as it is: only the list says what a model reads."""
        _, model_id = checked("vision_model", value)
        known = self.known_models()
        if not model_id or known is None or model_id == self._settings.get("vision_model"):
            return
        entry = find(known, LLM, model_id)
        if entry is None:
            raise ValueError(_MODEL_PROBLEMS["not_listed"].format(key="vision_model"))
        if IMAGE_INPUT not in entry.capabilities:
            raise ValueError(_MODEL_UNUSABLE.format(key="vision_model", reason="no_image_input"))

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
        self._notify_providers()
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
        self._notify_providers()
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

    def _on_cleanup_result(self, model: str, error: Exception | None) -> None:
        """How a call to the model went, as the cleanup service reports it."""
        if model.startswith("@"):
            return
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

    # --- the catalog and statistics ------------------------------------------------------------

    async def _models_catalog(self, params: dict) -> dict:
        """Every OpenRouter model Raphael could use (catalog.py). A chosen model that OpenRouter no
        longer lists goes back to its role's default first, which `replaced` reports."""
        if self._providers and not self._providers.config("openrouter")["enabled"]:
            entries, fetched_at_ms, source = [], None, "builtin"
        else:
            entries, fetched_at_ms, source = await self._pricing.catalog()
        if self._providers:
            await self._providers.refresh_catalog()
            entries = [*entries, *self._providers.catalog_entries()]
        replaced = [] if source == "builtin" else self._replace_vanished(entries)
        view = catalog_view(entries, fetched_at_ms, source, replaced)
        for kind in ("llm", "stt"):
            for model in view[kind]:
                pid = model["id"][1:].partition(":")[0] if model["id"].startswith("@") else "openrouter"
                model["provider_id"] = pid
                model["provider_name"] = self._providers.config(pid)["name"] if self._providers else "OpenRouter"
        return view

    def _replace_vanished(self, entries: list[CatalogEntry]) -> list[dict[str, str]]:
        """Set each chosen OpenRouter model that is not on its list (`entries`) back to its role's
        default (a model of another provider, `@id:…`, or of a subscription, `cli:…`, is not on that
        list and stays), and say which: `{key, role, model, default}`. Only while the default is on the
        list: if it went too, it is no better a choice. A value that is no text is no choice and is
        left alone: the store's readers fall back on their own."""
        replaced: list[dict[str, str]] = []
        for role in ROLES:
            chosen, default = self._settings.get(role.setting), role.default
            if (
                not isinstance(chosen, str)
                or chosen.startswith(("@", "cli:"))  # another provider's, or a subscription's
                or chosen == default
                or find(entries, role.kind, chosen) is not None
                or find(entries, role.kind, default) is None
            ):
                continue
            try:
                self._settings.set(role.setting, default)
            except sqlite3.Error as exc:
                logger.warning(
                    "Could not set %s back to its default: %s", role.setting, type(exc).__name__
                )
                continue
            logger.warning(
                "The model of %s is no longer on OpenRouter's list: back to the default",
                role.setting,
            )
            replaced.append(
                {"key": role.setting, "role": role.name, "model": chosen, "default": default}
            )
        return replaced

    async def _stats_summary(self, params: dict) -> dict:
        days = _opt_int(params, "days")
        return self._stats.summary() if days is None else self._stats.summary(days=days)

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
