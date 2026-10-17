"""Settings shared by the whole brain, kept in the database as JSON text.

Only keys in DEFAULTS exist. A key nobody has set reads as its default.
"""

import json
import sqlite3
from typing import Any

DEFAULTS: dict[str, Any] = {
    "monthly_limit_usd": 10.0,
    "cleanup_model": "google/gemini-3.1-flash-lite",
    "language": "auto",
    "history_retention_days": 30,
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
        for a number JSON cannot hold (nan, inf).
        """
        encoded = json.dumps(_checked(key, value), allow_nan=False)
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
