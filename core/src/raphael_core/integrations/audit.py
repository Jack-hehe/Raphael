"""The audit of the plugins' tools: one row for every call, or call that was refused.

A row says when, which server and tool, at which tier, who approved it and how it ended. It says
nothing of what the call was given or what came back: not an argument, not a result, not a word of
the question that led to it. The window lists the latest rows (`integrations.audit`).

- `approver`: "auto" (tiers T0 and T1 run without asking), "user" (a yes on the overlay) or "none"
  (nobody: the call was refused, denied or ran out of time, or never got to ask).
- `outcome`: "ok", "error" (the call failed or the tool reported an error), "timeout" (no answer in
  time: it may or may not have been done), "denied" (the user said no, or pressed Esc), "expired"
  (nobody answered in time), or "blocked" (the tool had no tier, the plugin was off, or the tool
  was not the one the user gave its tier to).
- `task_id`: the background task that made the call, or None for a call of the fast tier.
"""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from ..clock import wall_clock_ms

logger = logging.getLogger(__name__)

APPROVERS = ("auto", "user", "none")
OUTCOMES = ("ok", "error", "timeout", "denied", "expired", "blocked")
# The newest rows kept; the oldest go as new ones come in.
KEEP_ROWS = 2000
LIST_DEFAULT = 50
LIST_MAX = 200


@dataclass
class AuditEntry:
    id: int
    at_ms: int
    server: str
    tool: str
    tier: str | None  # None when the tool had none
    approver: str
    outcome: str
    task_id: int | None = None  # the background task that made the call; None: the fast tier


class AuditLog:
    """The `tool_audit` table. Use it on the thread that owns `conn`."""

    def __init__(
        self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms

    def add(
        self,
        *,
        server: str,
        tool: str,
        tier: str | None,
        approver: str,
        outcome: str,
        task_id: int | None = None,
    ) -> None:
        """Write a row, made now. A database that fails does not stop the call it records: it is
        logged by the type of the error."""
        if approver not in APPROVERS or outcome not in OUTCOMES:
            raise ValueError("Not an approver or an outcome the audit knows")
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO tool_audit (at_ms, server, tool, tier, approver, outcome, task_id)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (self._now_ms(), server, tool, tier, approver, outcome, task_id),
                )
                self._conn.execute(
                    "DELETE FROM tool_audit WHERE id <= (SELECT max(id) FROM tool_audit) - ?",
                    (KEEP_ROWS,),
                )
        except sqlite3.Error as exc:
            logger.warning("Could not write the audit of a tool call: %s", type(exc).__name__)

    def recent(self, limit: int = LIST_DEFAULT) -> list[AuditEntry]:
        """The latest rows, newest first."""
        limit = min(max(limit, 1), LIST_MAX)
        rows = self._conn.execute(
            "SELECT id, at_ms, server, tool, tier, approver, outcome, task_id FROM tool_audit"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [AuditEntry(**dict(row)) for row in rows]
