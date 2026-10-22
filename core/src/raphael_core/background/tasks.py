"""The background tasks and their steps, in the database.

`worker_tasks` is one row for each task: what was handed over, how it stands and the report it ended
with. `worker_steps` is one row for each model call and each tool call: which tool, at which tier,
how it ended and what it cost. A step holds no argument and no result. The text of a task is what the
user said, so tasks are pruned with the assistant's history and go with its "clear".

`state` is one of:

- "queued" and "running": not over;
- "done": the model reported;
- "failed": the model failed (no network, no credit, no key) or the worker could not start;
- "limit": a limit stopped it (model calls, tool calls, time, the cost of one task, the month's);
- "cancelled": the user stopped it;
- "interrupted": the worker died, or the core was closed or restarted while it ran. It is never
  started again by itself.

Use it on the thread that owns `conn` (the event loop). Nothing here logs a task.
"""

# Postponed annotations: TaskStore has a method called `list`, which would otherwise hide the builtin
# in the annotations of the methods defined after it.
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

QUEUED, RUNNING = "queued", "running"
ACTIVE_STATES = (QUEUED, RUNNING)
FINAL_STATES = ("done", "failed", "limit", "cancelled", "interrupted")
LIST_DEFAULT = 20
LIST_MAX = 100


class Refused(Exception):
    """A task cannot be taken. `kind` names the phrase that says why (`persona.Phrases`):
    "worker_busy" (the queue is full) or "worker_disabled" (the worker crashed too often)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass
class Task:
    id: int
    created_at_ms: int
    started_at_ms: int | None
    finished_at_ms: int | None
    state: str
    session_id: str
    turn_id: int | None  # the turn of the assistant's history that handed the task over
    language: str
    text: str  # what was handed over: what the user said
    report_prefix: str | None
    report_text: str | None
    report_detail: str | None
    reason: str | None  # why it did not finish: a kind, never a message
    model_calls: int
    tool_calls: int
    cost_usd: float
    # 0 for the report of an interrupted task that has not been said yet; 1 otherwise.
    announced: int = 1
    rerun_of: int | None = None  # the task this one runs again


@dataclass
class Step:
    n: int
    at_ms: int
    kind: str  # "model" or "tool"
    tool: str | None
    server: str | None
    tier: str | None
    outcome: str  # "ok", "error", "timeout", "blocked", "cancelled" ...
    cost_usd: float
    ms: int
    reason: str | None = None  # why it failed, one word of agent.steps.REASONS; never a message


_TASK_COLUMNS = (
    "id, created_at_ms, started_at_ms, finished_at_ms, state, session_id, turn_id, language, text,"
    " report_prefix, report_text, report_detail, reason, model_calls, tool_calls, cost_usd,"
    " announced, rerun_of"
)
_STEP_COLUMNS = "n, at_ms, kind, tool, server, tier, outcome, cost_usd, ms, reason"


class TaskStore:
    """The `worker_tasks` and `worker_steps` tables."""

    def __init__(
        self, conn: sqlite3.Connection, now_ms: Callable[[], int] = _wall_clock_ms
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms

    def create(
        self, *, session_id: str, language: str, text: str, rerun_of: int | None = None
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO worker_tasks (created_at_ms, state, session_id, language, text,"
                " rerun_of) VALUES (?, 'queued', ?, ?, ?, ?)",
                (self._now_ms(), session_id, language, text, rerun_of),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def attach_turn(self, task_id: int, turn_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE worker_tasks SET turn_id = ? WHERE id = ?", (turn_id or None, task_id)
            )

    def start(self, task_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE worker_tasks SET state = 'running', started_at_ms = ? WHERE id = ?",
                (self._now_ms(), task_id),
            )

    def set_state(self, task_id: int, state: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE worker_tasks SET state = ? WHERE id = ?", (state, task_id))

    def progress(self, task_id: int, *, model_calls: int, tool_calls: int, cost_usd: float) -> None:
        """What a running task has used so far."""
        with self._conn:
            self._conn.execute(
                "UPDATE worker_tasks SET model_calls = ?, tool_calls = ?, cost_usd = ?"
                " WHERE id = ?",
                (model_calls, tool_calls, cost_usd, task_id),
            )

    def finish(
        self,
        task_id: int,
        state: str,
        *,
        prefix: str,
        text: str,
        detail: str | None,
        reason: str | None,
    ) -> None:
        if state not in FINAL_STATES:
            raise ValueError(f"{state!r} is not a state a task ends in")
        with self._conn:
            self._conn.execute(
                "UPDATE worker_tasks SET state = ?, finished_at_ms = ?, report_prefix = ?,"
                " report_text = ?, report_detail = ?, reason = ? WHERE id = ?",
                (state, self._now_ms(), prefix, text, detail, reason, task_id),
            )

    def add_step(
        self,
        task_id: int,
        *,
        kind: str,
        outcome: str,
        tool: str | None = None,
        server: str | None = None,
        tier: str | None = None,
        cost_usd: float = 0.0,
        ms: int = 0,
        reason: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO worker_steps (task_id, n, at_ms, kind, tool, server, tier, outcome,"
                " cost_usd, ms, reason) VALUES (?, (SELECT COALESCE(MAX(n), 0) + 1 FROM"
                " worker_steps WHERE task_id = ?), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    task_id,
                    self._now_ms(),
                    kind,
                    tool,
                    server,
                    tier,
                    outcome,
                    cost_usd,
                    ms,
                    reason,
                ),
            )

    def get(self, task_id: int) -> Task | None:
        row = self._conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM worker_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return None if row is None else Task(**dict(row))

    def list(self, limit: int = LIST_DEFAULT) -> list[Task]:
        """Newest first."""
        limit = min(max(limit, 1), LIST_MAX)
        rows = self._conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM worker_tasks ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [Task(**dict(row)) for row in rows]

    def for_turns(self, turn_ids: Iterable[int]) -> dict[int, Task]:
        """The task each of `turn_ids` handed over, if it handed one over (the newest, should
        there be more)."""
        ids = sorted({int(turn_id) for turn_id in turn_ids})
        found: dict[int, Task] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ", ".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT {_TASK_COLUMNS} FROM worker_tasks WHERE turn_id IN ({marks})"
                " ORDER BY id",
                chunk,
            )
            for row in rows:
                task = Task(**dict(row))
                assert task.turn_id is not None
                found[task.turn_id] = task
        return found

    def steps(self, task_id: int) -> list[Step]:
        rows = self._conn.execute(
            f"SELECT {_STEP_COLUMNS} FROM worker_steps WHERE task_id = ? ORDER BY n", (task_id,)
        )
        return [Step(**dict(row)) for row in rows]

    def count_state(self, state: str) -> int:
        return self._conn.execute(
            "SELECT count(*) FROM worker_tasks WHERE state = ?", (state,)
        ).fetchone()[0]

    def interrupt_unfinished(
        self,
        report: Callable[[str, int], tuple[str, str]] | None = None,
        reason: str = "restart",
    ) -> list[Task]:
        """Mark the tasks that were queued or running as interrupted: the worker they ran in is
        gone, and a task is never started again by itself. With `report` (the task's language and
        the tool calls it made, to a prefix and a line) each gets that report, to be said once
        (`announced` 0). Returns the tasks as they are now."""
        with self._conn:
            rows = self._conn.execute(
                "SELECT id, language FROM worker_tasks WHERE state IN ('queued', 'running')"
            ).fetchall()
            for row in rows:
                if report is None:
                    self._conn.execute(
                        "UPDATE worker_tasks SET state = 'interrupted', finished_at_ms = ?,"
                        " reason = ? WHERE id = ?",
                        (self._now_ms(), reason, row["id"]),
                    )
                    continue
                prefix, text = report(row["language"], self.tool_steps(row["id"]))
                self._conn.execute(
                    "UPDATE worker_tasks SET state = 'interrupted', finished_at_ms = ?,"
                    " reason = ?, report_prefix = ?, report_text = ?, announced = 0 WHERE id = ?",
                    (self._now_ms(), reason, prefix, text, row["id"]),
                )
        return [task for task in (self.get(row["id"]) for row in rows) if task is not None]

    def unannounced(self) -> list[Task]:
        """The interrupted tasks whose report has not been said, newest first."""
        rows = self._conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM worker_tasks WHERE announced = 0 ORDER BY id DESC"
        )
        return [Task(**dict(row)) for row in rows]

    def mark_announced(self) -> None:
        with self._conn:
            self._conn.execute("UPDATE worker_tasks SET announced = 1 WHERE announced = 0")

    def finish_interrupted(self, task_id: int, *, prefix: str, text: str, reason: str) -> None:
        """End a task that was interrupted while it ran, with a report nobody has heard yet."""
        with self._conn:
            self._conn.execute(
                "UPDATE worker_tasks SET state = 'interrupted', finished_at_ms = ?, reason = ?,"
                " report_prefix = ?, report_text = ?, report_detail = NULL, announced = 0"
                " WHERE id = ?",
                (self._now_ms(), reason, prefix, text, task_id),
            )

    def tool_steps(self, task_id: int) -> int:
        """How many tool calls the task made."""
        return self._conn.execute(
            "SELECT count(*) FROM worker_steps WHERE task_id = ? AND kind = 'tool'", (task_id,)
        ).fetchone()[0]

    def delete(self, task_id: int) -> None:
        """Delete a task and its steps, whatever state it is in."""
        with self._conn:
            self._conn.execute("DELETE FROM worker_steps WHERE task_id = ?", (task_id,))
            self._conn.execute("DELETE FROM worker_tasks WHERE id = ?", (task_id,))

    def scrub(self, task_id: int) -> None:
        """Take what the user said, and anything reported, out of a task's row."""
        with self._conn:
            self._conn.execute(
                "UPDATE worker_tasks SET text = '', report_prefix = NULL, report_text = NULL,"
                " report_detail = NULL WHERE id = ?",
                (task_id,),
            )

    def prune(self, older_than_ms: int) -> int:
        """Delete the finished tasks made before `older_than_ms`, with their steps."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM worker_steps WHERE task_id IN (SELECT id FROM worker_tasks"
                " WHERE created_at_ms < ? AND state NOT IN ('queued', 'running'))",
                (older_than_ms,),
            )
            cursor = self._conn.execute(
                "DELETE FROM worker_tasks WHERE created_at_ms < ?"
                " AND state NOT IN ('queued', 'running')",
                (older_than_ms,),
            )
        return cursor.rowcount

    def clear(self) -> int:
        """Delete every finished task, with its steps; one that is not over stays."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM worker_steps WHERE task_id IN (SELECT id FROM worker_tasks"
                " WHERE state NOT IN ('queued', 'running'))"
            )
            cursor = self._conn.execute(
                "DELETE FROM worker_tasks WHERE state NOT IN ('queued', 'running')"
            )
        return cursor.rowcount
