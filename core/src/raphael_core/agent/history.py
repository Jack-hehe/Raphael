"""The voice assistant's history: what it was asked and what it said, one row for each turn.

A turn is written when the assistant has its answer, or when it has asked for a yes or a no
(`status` "confirm"), and the row is completed when that is answered (`resolve`). The app's page of
conversations lists them, newest first, and the last few are the context of a discussion.

Each turn keeps where it came from (`source`: "voice", or a chat app such as "telegram") and the
model that answered, and its steps (`agent_steps`, `steps.Step`): what the fast tier did for it, so
the conversation view can show what happened between the question and the answer. The steps go with
their turns when the history is pruned or cleared.

Like the dictation history, it holds what the user said, so it is pruned with the retention setting
and the user can clear it. Nothing here logs a question or an answer.
"""

# Postponed annotations: AgentHistory has a method called `list`, which would otherwise hide the
# builtin in the annotations of the methods defined after it.
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from .steps import Step

# Where a turn came from when nobody says: the hotkey and the overlay.
VOICE = "voice"


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass
class Turn:
    id: int | None
    created_at_ms: int
    session_id: str
    question: str  # what the user said, after the dictionary's replacements
    route: str  # command, answer, delegate, discuss, chat or unclear
    prefix: str  # how Raphael answered, in the language the user reads
    text: str  # the short line of the overlay; empty for a command that was done
    detail: str | None  # the longer text of an answer, if it had one
    # "done", "confirm" (waiting for a yes or a no), "denied", "expired" or "error".
    status: str
    cost_usd: float = 0.0
    source: str = VOICE  # "voice", or the chat app the request came from
    model: str | None = None  # the model that answered, when one did


_COLUMNS = (
    "id, created_at_ms, session_id, question, route, prefix, text, detail, status, cost_usd,"
    " source, model"
)
_STEP_COLUMNS = "turn_id, kind, tool, server, outcome, ms, reason, at_ms"


def _to_turn(row: sqlite3.Row) -> Turn:
    return Turn(**dict(row))


class AgentHistory:
    """The `agent_turns` table. Use it on the thread that owns `conn`."""

    def __init__(
        self, conn: sqlite3.Connection, now_ms: Callable[[], int] = _wall_clock_ms
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms

    def add(
        self,
        *,
        session_id: str,
        question: str,
        route: str,
        prefix: str,
        text: str,
        status: str,
        detail: str | None = None,
        cost_usd: float = 0.0,
        source: str = VOICE,
        model: str | None = None,
        steps: Sequence[Step] = (),
    ) -> int:
        """Store a new turn, made now, with the steps taken for it, and return its id."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO agent_turns (created_at_ms, session_id, question, route, prefix,"
                " text, detail, status, cost_usd, source, model)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._now_ms(),
                    session_id,
                    question,
                    route,
                    prefix,
                    text,
                    detail,
                    status,
                    cost_usd,
                    source,
                    model,
                ),
            )
            turn_id = cursor.lastrowid
            assert turn_id is not None
            self._insert_steps(turn_id, steps)
        return turn_id

    def add_steps(self, turn_id: int, steps: Sequence[Step]) -> None:
        """Add steps to a turn that is already stored: the call made after a yes, say. They come
        after the ones it has."""
        with self._conn:
            self._insert_steps(turn_id, steps)

    def _insert_steps(self, turn_id: int, steps: Sequence[Step]) -> None:
        for step in steps:
            self._conn.execute(
                "INSERT INTO agent_steps (turn_id, n, at_ms, kind, tool, server, outcome, ms,"
                " reason) VALUES (?, (SELECT COALESCE(MAX(n), 0) + 1 FROM agent_steps"
                " WHERE turn_id = ?), ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn_id,
                    turn_id,
                    step.at_ms or self._now_ms(),
                    step.kind,
                    step.tool,
                    step.server,
                    step.outcome,
                    max(int(step.ms), 0),
                    step.reason,
                ),
            )

    def steps(self, turn_ids: Iterable[int]) -> dict[int, list[Step]]:
        """The steps of each of `turn_ids` that has any, in the order they were taken."""
        ids = sorted({int(turn_id) for turn_id in turn_ids})
        found: dict[int, list[Step]] = {}
        # SQLite takes at most 999 parameters in old builds: a page of turns is far fewer.
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ", ".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT {_STEP_COLUMNS} FROM agent_steps WHERE turn_id IN ({marks})"
                " ORDER BY turn_id, n",
                chunk,
            )
            for row in rows:
                data = dict(row)
                found.setdefault(data.pop("turn_id"), []).append(Step(**data))
        return found

    def get(self, turn_id: int) -> Turn | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM agent_turns WHERE id = ?", (turn_id,)
        ).fetchone()
        return None if row is None else _to_turn(row)

    def resolve(
        self,
        turn_id: int,
        *,
        prefix: str,
        text: str,
        status: str,
        detail: str | None = None,
        cost_usd: float = 0.0,
        model: str | None = None,
    ) -> bool:
        """Give a turn its outcome: what Raphael said in the end, how it ended, and the cost of
        getting there on top of what the turn cost so far (and the model that worded it, if one
        did). False if the turn is gone (the history was cleared meanwhile)."""
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE agent_turns SET prefix = ?, text = ?, detail = ?, status = ?,"
                " cost_usd = cost_usd + ?, model = COALESCE(?, model) WHERE id = ?",
                (prefix, text, detail, status, cost_usd, model, turn_id),
            )
        return cursor.rowcount > 0

    def list(self, limit: int = 50, before_id: int | None = None) -> list[Turn]:
        """Newest first. `before_id` keeps the turns made before that one: the cursor for the next
        page."""
        conditions = "" if before_id is None else " WHERE id < ?"
        params: list[object] = [] if before_id is None else [before_id]
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM agent_turns{conditions} ORDER BY id DESC LIMIT ?",
            [*params, limit],
        )
        return [_to_turn(row) for row in rows]

    def recent(self, count: int) -> list[Turn]:
        """The last `count` turns that were answered, oldest first: the context of what is asked
        next. A turn that is waiting for a yes or a no, was refused or failed says nothing."""
        if count <= 0:
            return []
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM agent_turns WHERE status = 'done' ORDER BY id DESC LIMIT ?",
            (count,),
        )
        return [_to_turn(row) for row in reversed(rows.fetchall())]

    def clear(self) -> int:
        """Delete every turn, with its steps; return how many went."""
        with self._conn:
            self._conn.execute("DELETE FROM agent_steps")
            cursor = self._conn.execute("DELETE FROM agent_turns")
        return cursor.rowcount

    def prune(self, older_than_ms: int) -> int:
        """Delete the turns made before `older_than_ms`, with their steps; return how many went."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM agent_steps WHERE turn_id IN"
                " (SELECT id FROM agent_turns WHERE created_at_ms < ?)",
                (older_than_ms,),
            )
            cursor = self._conn.execute(
                "DELETE FROM agent_turns WHERE created_at_ms < ?", (older_than_ms,)
            )
        return cursor.rowcount

    def expire_pending(self) -> int:
        """Mark the turns that still wait for a yes or a no as expired: the confirmation lived in
        a process that is gone. Returns how many there were."""
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE agent_turns SET status = 'expired' WHERE status = 'confirm'"
            )
        return cursor.rowcount
