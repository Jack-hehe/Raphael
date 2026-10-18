"""Settings shared by the whole brain, kept in the database as JSON text.

Only keys in DEFAULTS exist. A key nobody has set reads as its default. Most keys are only
type-checked; the speech-to-text ones must also be one of their listed values.
"""

import json
import sqlite3
from typing import Any

DEFAULTS: dict[str, Any] = {
    "monthly_limit_usd": 10.0,
    # The best of the cleanup eval on 2026-10-18 (33/34 cases, cheapest, ~1.25 s on average).
    "cleanup_model": "openai/gpt-6-luna",
    "language": "auto",
    "history_retention_days": 30,
    # Speech-to-text: "cloud" (OpenRouter, with the local engine as the fallback) or "local" (the
    # engine on this computer only; no audio leaves it). Read at the start of each dictation.
    "stt_engine": "cloud",
    "stt_cloud_model": "openai/gpt-transcribe",
}

STT_ENGINES = ("cloud", "local")
# The OpenRouter transcription models that can be chosen, the default first. All are listed in
# OpenRouter's speech-to-text collection, each with an endpoint (checked 2026-10-18).
STT_CLOUD_MODELS = (
    "openai/gpt-transcribe",
    "elevenlabs/scribe-v2",
    "microsoft/mai-transcribe-2",
    "assemblyai/universal-3-6-pro",
    "qwen/qwen3-asr-1.7b",
    "google/gemini-3.5-transcribe",
    "openai/whisper-large-v3-turbo",
)
_CHOICES: dict[str, tuple[str, ...]] = {
    "stt_engine": STT_ENGINES,
    "stt_cloud_model": STT_CLOUD_MODELS,
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

    def get(self, key: str) -> Any:
        """The saved value, else the default. KeyError for a key that is not a setting."""
        default = DEFAULTS[key]
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def set(self, key: str, value: Any) -> None:
        """Save a value.

        Raises KeyError for an unknown key, TypeError for a value of the wrong type and ValueError
        for a number JSON cannot hold (nan, inf) or a value that is not one of the key's choices.
        """
        encoded = json.dumps(_allowed(key, _checked(key, value)), allow_nan=False)
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
        return values
