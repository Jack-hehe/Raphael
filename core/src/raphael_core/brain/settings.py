"""Settings shared by the whole brain, kept in the database as JSON text.

Only keys in DEFAULTS exist. A key nobody has set reads as its default. Most keys are only
type-checked; the speech-to-text engine and the local model must also be one of their listed
values, `languages` a valid selection (see raphael_core.languages), `model_favorites` the starred
model ids of each role (at most 50 each), and the models and the assistant's city a text of some
substance (saved without the spaces around it; a model id has none inside). Which models may be
chosen is for the brain to say, which knows OpenRouter's list of them (brain/catalog.py): here a
model is only an id.

A key that an older version saved but that is no longer a setting stays in the table and is never
read: `all` lists only the keys of DEFAULTS, and `get` and `set` refuse the others, so nothing has
to be rewritten at start-up. Two are such keys: `agent_router` (whether Jev or the language model
routed the assistant's requests; Jev always does now, so a saved "luna" changes nothing) and
`learn_corrections` (whether the words corrected after a paste were learned; they always are now,
so a saved false changes nothing).

`language` is the setting `languages` replaced, kept for one release: it reads as what the
selection amounts to ("zh", "ja", "en" or "auto"), and setting it sets `languages`. A saved
`language` of an older version reads as the selection it stands for until `languages` is saved.
"""

import copy
import json
import sqlite3
from typing import Any

from ..device.models import LOCAL_STT_MODELS
from ..languages import (
    DEFAULT_LANGUAGES,
    LEGACY_LANGUAGES,
    checked_languages,
    from_legacy,
    legacy_language,
)

DEFAULTS: dict[str, Any] = {
    "monthly_limit_usd": 10.0,
    # Chosen from the cleanup eval of 2026-10-18 for its cost and its speed (~1.25 s on average).
    # The cases and the prompt have changed since, so no pass rate is claimed for it: compare the
    # models again with `python -m raphael_core.evaluate.cleanup`.
    "cleanup_model": "openai/gpt-6-luna",
    # The dictation languages, in the user's order of priority (raphael_core.languages).
    "languages": list(DEFAULT_LANGUAGES),
    # Derived from `languages`, never saved (see the module's docstring).
    "language": legacy_language(DEFAULT_LANGUAGES),
    "history_retention_days": 30,
    # Speech-to-text: "cloud" (OpenRouter, with the local engine as the fallback) or "local" (the
    # engine on this computer only; no audio leaves it). Read at the start of each dictation.
    "stt_engine": "cloud",
    # Which of the catalogue's models (device/models.py) the local engine is. The one Raphael has
    # always used, so that nobody who runs locally needs a new download.
    "stt_local_model": "qwen3-asr",
    # The most accurate on the samples tried live on 2026-10-18 (gpt-transcribe returned nothing
    # for an English clip and garbled Chinese).
    "stt_cloud_model": "elevenlabs/scribe-v2",
    # The voice assistant (raphael_core.agent). The model that words its answers, and that decides
    # what a request is when Jev (agent/routing.py, not a setting) fails or is unsure.
    "agent_model": "openai/gpt-6-luna",
    # Where the weather is looked up when the user does not say.
    "agent_city": "台北",
    # The models the user starred in the pickers, per role (the page's names for them), in the order
    # they were added. The page shows the ones the catalog still has at the top of its list; the
    # others stay here, in case the catalog gets them back.
    "model_favorites": {"cleanup": [], "answer": [], "stt": []},
}

# The longest model id or city name taken: far more than any real one, short of an accident.
AGENT_TEXT_MAX_CHARS = 100
# The roles that have favourites, and the most that one of them holds.
FAVORITE_ROLES = ("cleanup", "answer", "stt")
MAX_FAVORITES = 50
STT_ENGINES = ("cloud", "local")
# The OpenRouter transcription models the settings page offered before it offered every one, the
# default first. All are listed in OpenRouter's speech-to-text collection, each with an endpoint
# (checked 2026-10-18).
STT_CLOUD_MODELS = (
    "elevenlabs/scribe-v2",
    "openai/gpt-transcribe",
    "microsoft/mai-transcribe-2",
    "assemblyai/universal-3-6-pro",
    "qwen/qwen3-asr-1.7b",
    "google/gemini-3.5-transcribe",
    "openai/whisper-large-v3-turbo",
)
_CHOICES: dict[str, tuple[str, ...]] = {
    "stt_engine": STT_ENGINES,
    "stt_local_model": tuple(entry.name for entry in LOCAL_STT_MODELS),
    "language": LEGACY_LANGUAGES,
}
# The settings that hold the id of an OpenRouter model.
MODEL_SETTINGS = ("cleanup_model", "stt_cloud_model", "agent_model")
_TRIMMED = (*MODEL_SETTINGS, "agent_city")


def _checked(key: str, value: Any) -> Any:
    """The value to save, or TypeError if it does not have the type of the key's default.

    A whole number is accepted for a float setting and saved as a float. Types are compared
    exactly, so True is not a number here.
    """
    expected = type(DEFAULTS[key])
    if type(value) is expected:
        return value
    if expected is float and type(value) is int:
        return float(value)
    raise TypeError(f"Setting {key!r} must be a {expected.__name__}, not {type(value).__name__}")


# The settings that may also be None: the local model, when there is none to fall back on.
_NULLABLE = ("stt_local_model",)


def _allowed(key: str, value: Any) -> Any:
    """`value`, or ValueError if the key has a list of choices and `value` is not on it."""
    choices = _CHOICES.get(key)
    if choices is not None and value not in choices:
        raise ValueError(f"Setting {key!r} must be one of {', '.join(choices)}")
    return value


def _trimmed(key: str, value: str) -> str:
    """`value` without the spaces around it, or ValueError if that leaves nothing or too much.

    A model id also has no space inside it. A city may: "New York".
    """
    text = value.strip()
    if not text:
        raise ValueError(f"Setting {key!r} must not be blank")
    if len(text) > AGENT_TEXT_MAX_CHARS:
        raise ValueError(f"Setting {key!r} must be at most {AGENT_TEXT_MAX_CHARS} characters")
    if key in (*MODEL_SETTINGS, "model_favorites") and any(char.isspace() for char in text):
        raise ValueError(f"Setting {key!r} must not contain spaces")
    return text


def _favorites(value: dict) -> dict[str, list[str]]:
    """The favourites to save: every role with its list, each id once and in order.

    TypeError for a list that is not a list of texts, ValueError for a role that does not exist,
    an id that is blank, long or has a space in it, and more than MAX_FAVORITES in one list.
    """
    unknown = [role for role in value if role not in FAVORITE_ROLES]
    if unknown:
        raise ValueError(f"Setting 'model_favorites' has no role {unknown[0]!r}")
    result: dict[str, list[str]] = {}
    for role in FAVORITE_ROLES:
        ids = value.get(role, [])
        if type(ids) is not list or any(type(i) is not str for i in ids):
            raise TypeError(f"Favourites of {role!r} must be a list of model ids")
        kept = list(dict.fromkeys(_trimmed("model_favorites", i) for i in ids))
        if len(kept) > MAX_FAVORITES:
            raise ValueError(f"At most {MAX_FAVORITES} favourite models for {role!r}")
        result[role] = kept
    return result


def _no_favorites() -> dict[str, list[str]]:
    return copy.deepcopy(DEFAULTS["model_favorites"])


def _read_favorites(saved: Any) -> dict[str, list[str]]:
    """The favourites as saved; a saved value that is damaged reads as no favourites."""
    try:
        return _favorites(saved) if type(saved) is dict else _no_favorites()
    except (TypeError, ValueError):
        return _no_favorites()


def checked(key: str, value: Any) -> tuple[str, Any]:
    """The key and the value that setting `key` to `value` saves, or the error `SettingsStore.set`
    raises for them (see there). Nothing is saved."""
    if key == "languages":
        return key, checked_languages(key, value)
    if value is None and key in _NULLABLE:
        return key, None
    value = _allowed(key, _checked(key, value))
    if key in _TRIMMED:
        value = _trimmed(key, value)
    if key == "model_favorites":
        value = _favorites(value)
    if key == "language":
        return "languages", from_legacy(value)
    return key, value


class SettingsStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def _saved(self, key: str) -> Any:
        """The saved value of `key`, or None when it has none."""
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else json.loads(row["value"])

    def _languages(self) -> list[str]:
        """The saved selection; else the one a saved old `language` stands for; else the default.
        A saved selection that is no longer valid reads as the default."""
        saved = self._saved("languages")
        if saved is None:
            old = self._saved("language")
            return list(DEFAULT_LANGUAGES) if old is None else from_legacy(old)
        try:
            return checked_languages("languages", saved)
        except (TypeError, ValueError):
            return list(DEFAULT_LANGUAGES)

    def get(self, key: str) -> Any:
        """The saved value, else the default. KeyError for a key that is not a setting."""
        default = DEFAULTS[key]
        if key == "languages":
            return self._languages()
        if key == "language":
            return legacy_language(self._languages())
        saved = self._saved(key)
        if key == "model_favorites":
            return _read_favorites(saved)
        if saved is None and key in _NULLABLE and self._conn.execute(
            "SELECT 1 FROM settings WHERE key = ?", (key,)
        ).fetchone():
            return None  # saved as "none", which is not the same as never saved
        return default if saved is None else saved

    def set(self, key: str, value: Any) -> None:
        """Save a value.

        Raises KeyError for an unknown key, TypeError for a value of the wrong type and ValueError
        for a number JSON cannot hold (nan, inf), a value that is not one of the key's choices, a
        blank or absurd text or a selection of languages that is not valid. Setting `language`
        saves `languages`.
        """
        key, value = checked(key, value)
        if key == "stt_engine" and value == "local" and self.get("stt_local_model") is None:
            raise ValueError("Setting 'stt_local_model' must name a model for the local engine")
        if key == "stt_local_model" and value is None and self.get("stt_engine") == "local":
            raise ValueError("Setting 'stt_local_model' must name a model while the engine is local")
        encoded = json.dumps(value, allow_nan=False)
        with self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, encoded),
            )

    def all(self) -> dict[str, Any]:
        """Every setting: the defaults with the saved values on top."""
        values = dict(DEFAULTS)
        for row in self._conn.execute("SELECT key, value FROM settings"):
            if row["key"] in values:
                values[row["key"]] = json.loads(row["value"])
        values["model_favorites"] = _read_favorites(values["model_favorites"])
        values["languages"] = self._languages()
        values["language"] = legacy_language(values["languages"])
        return values
