"""Settings shared by the whole brain, kept in the database as JSON text.

Only keys in DEFAULTS exist. A key nobody has set reads as its default. Most keys are only
type-checked; the speech-to-text ones must also be one of their listed values, and `languages` a
valid selection (see raphael_core.languages).

`language` is the setting `languages` replaced, kept for one release: it reads as what the
selection amounts to ("zh", "ja", "en" or "auto"), and setting it sets `languages`. A saved
`language` of an older version reads as the selection it stands for until `languages` is saved.
"""

import json
import sqlite3
from typing import Any

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
    # models again with `python -m raphael_core.evaluate.cleanup` (eval/README.md).
    "cleanup_model": "openai/gpt-6-luna",
    # The dictation languages, in the user's order of priority (raphael_core.languages).
    "languages": list(DEFAULT_LANGUAGES),
    # Derived from `languages`, never saved (see the module's docstring).
    "language": legacy_language(DEFAULT_LANGUAGES),
    "history_retention_days": 30,
    # Speech-to-text: "cloud" (OpenRouter, with the local engine as the fallback) or "local" (the
    # engine on this computer only; no audio leaves it). Read at the start of each dictation.
    "stt_engine": "cloud",
    # The most accurate on the samples tried live on 2026-10-18 (gpt-transcribe returned nothing
    # for an English clip and garbled Chinese).
    "stt_cloud_model": "elevenlabs/scribe-v2",
    # After a paste, the app watches the text field for up to 60 s and learns the words the user
    # corrects by hand (`dictionary.learn`). Off: the app does not read the field at all.
    "learn_corrections": True,
}

STT_ENGINES = ("cloud", "local")
# The OpenRouter transcription models that can be chosen, the default first. All are listed in
# OpenRouter's speech-to-text collection, each with an endpoint (checked 2026-10-18).
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
    "stt_cloud_model": STT_CLOUD_MODELS,
    "language": LEGACY_LANGUAGES,
}


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


def _allowed(key: str, value: Any) -> Any:
    """`value`, or ValueError if the key has a list of choices and `value` is not on it."""
    choices = _CHOICES.get(key)
    if choices is not None and value not in choices:
        raise ValueError(f"Setting {key!r} must be one of {', '.join(choices)}")
    return value


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
        return default if saved is None else saved

    def set(self, key: str, value: Any) -> None:
        """Save a value.

        Raises KeyError for an unknown key, TypeError for a value of the wrong type and ValueError
        for a number JSON cannot hold (nan, inf), a value that is not one of the key's choices or
        a selection of languages that is not valid. Setting `language` saves `languages`.
        """
        if key == "languages":
            value = checked_languages(key, value)
        else:
            value = _allowed(key, _checked(key, value))
        if key == "language":
            key, value = "languages", from_legacy(value)
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
        values["languages"] = self._languages()
        values["language"] = legacy_language(values["languages"])
        return values
