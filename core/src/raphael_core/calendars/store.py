"""The calendars the user has added: an id and a label each, in the brain's database.

The address of a calendar is not here (it is a secret: vault.py). A row without an address in the
vault is a calendar whose address was lost, for instance with a database copied to another
computer; the service lists it and reports it as unreadable.
"""

# Postponed annotations: CalendarStore has a method called `list`, which would otherwise hide the
# builtin in the `list[...]` annotations of the methods defined after it.
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class CalendarRow:
    id: int
    label: str


class CalendarStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list(self) -> list[CalendarRow]:
        rows = self._conn.execute("SELECT id, label FROM calendars ORDER BY id")
        return [CalendarRow(row["id"], row["label"]) for row in rows]

    def get(self, calendar_id: int) -> CalendarRow | None:
        row = self._conn.execute(
            "SELECT id, label FROM calendars WHERE id = ?", (calendar_id,)
        ).fetchone()
        return None if row is None else CalendarRow(row["id"], row["label"])

    def count(self) -> int:
        return self._conn.execute("SELECT count(*) FROM calendars").fetchone()[0]

    def add(self, label: str, created_at_ms: int) -> int:
        """Store a new calendar and return its id."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO calendars (label, created_at_ms) VALUES (?, ?)", (label, created_at_ms)
            )
        return cursor.lastrowid

    def remove(self, calendar_id: int) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM calendars WHERE id = ?", (calendar_id,))
        return cursor.rowcount > 0
