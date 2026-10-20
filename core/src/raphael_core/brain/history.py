"""Dictation history: what was said, what was pasted, how long it took and what it cost."""

# Postponed annotations: HistoryStore has a method called `list`, which would otherwise hide the
# builtin in the `list[...]` annotations of the methods defined after it.
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass


@dataclass
class HistoryItem:
    id: int | None
    created_at_ms: int
    kind: str  # "dictation"
    raw_text: str  # the speech-to-text result, before cleanup
    text: str  # the final text: cleaned, or the raw text when cleanup fell back
    cleaned: bool
    fallback_reason: str | None
    target_app: str | None
    audio_seconds: float
    stt_ms: int
    llm_ms: int
    model: str | None  # the cleanup model
    cost_usd: float
    stt: str | None = None  # the speech-to-text that heard it (cloud model or local engine)


_COLUMNS = (
    "id, created_at_ms, kind, raw_text, text, cleaned, fallback_reason, target_app, "
    "audio_seconds, stt_ms, llm_ms, model, cost_usd, stt"
)
_NEWEST_FIRST = "ORDER BY created_at_ms DESC, id DESC"


def _to_item(row: sqlite3.Row) -> HistoryItem:
    values = dict(row)
    values["cleaned"] = bool(values["cleaned"])
    return HistoryItem(**values)


def _contains_pattern(query: str) -> str:
    """A LIKE pattern for "contains `query`", with the wildcards inside `query` as plain text."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class HistoryStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, item: HistoryItem) -> int:
        """Store a new row and return its id (the id on `item` is ignored)."""
        values = {**asdict(item), "cleaned": int(item.cleaned)}
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO history (created_at_ms, kind, raw_text, text, cleaned,"
                " fallback_reason, target_app, audio_seconds, stt_ms, llm_ms, model, cost_usd, stt)"
                " VALUES (:created_at_ms, :kind, :raw_text, :text, :cleaned,"
                " :fallback_reason, :target_app, :audio_seconds, :stt_ms, :llm_ms, :model,"
                " :cost_usd, :stt)",
                values,
            )
        return cursor.lastrowid

    def get(self, id: int) -> HistoryItem | None:
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM history WHERE id = ?", (id,)).fetchone()
        return None if row is None else _to_item(row)

    def list(
        self,
        query: str | None = None,
        limit: int = 50,
        before_ms: int | None = None,
        after_ms: int | None = None,
    ) -> list[HistoryItem]:
        """Newest first.

        `query` keeps the items whose text or raw text contains it. `before_ms` keeps the ones
        created strictly before that time, which is the cursor for the next page. `after_ms` keeps
        the ones created at or after that time: with `before_ms` it names a stretch, such as a day
        (the page works out the day's midnights in its own zone).
        """
        conditions: list[str] = []
        params: list[object] = []
        if query:
            pattern = _contains_pattern(query)
            conditions.append("(text LIKE ? ESCAPE '\\' OR raw_text LIKE ? ESCAPE '\\')")
            params += [pattern, pattern]
        if before_ms is not None:
            conditions.append("created_at_ms < ?")
            params.append(before_ms)
        if after_ms is not None:
            conditions.append("created_at_ms >= ?")
            params.append(after_ms)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM history{where} {_NEWEST_FIRST} LIMIT ?", [*params, limit]
        )
        return [_to_item(row) for row in rows]

    def delete(self, id: int) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM history WHERE id = ?", (id,))
        return cursor.rowcount > 0

    def latest(self) -> HistoryItem | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM history {_NEWEST_FIRST} LIMIT 1"
        ).fetchone()
        return None if row is None else _to_item(row)

    def prune(self, older_than_ms: int) -> int:
        """Delete the items created before `older_than_ms`; return how many went."""
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM history WHERE created_at_ms < ?", (older_than_ms,)
            )
        return cursor.rowcount

    def texts_since(self, ms: int) -> list[str]:
        """The text of every item created at or after `ms`, newest first."""
        rows = self._conn.execute(
            f"SELECT text FROM history WHERE created_at_ms >= ? {_NEWEST_FIRST}", (ms,)
        )
        return [row["text"] for row in rows]
