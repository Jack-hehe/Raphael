"""The background tasks and their steps, in the database.

`worker_tasks` is one row for each task: what was handed over, how it stands and the report it ended
with. `worker_steps` is one row for each model call and each tool call: which tool, at which tier,
how it ended and what it cost. A step holds no argument and no result. The text of a task is what the
user said, so tasks go with the assistant's history when it is cleared; like the conversation they
are part of, they are not pruned by age.

`state` is one of:

- "queued" and "running": not over;
- "waiting": not over either: the task paused for the owner's yes to one of its calls, which is a
  row of `worker_questions` (the question as the card shows it). It goes on when the owner answers
  in the app, and ends when nobody has after 24 hours;
- "done": the model reported;
- "failed": the model failed (no network, no credit, no key) or the worker could not start;
- "limit": a limit stopped it (model calls, tool calls, time, the cost of one task, the month's);
- "cancelled": the user stopped it;
- "interrupted": the worker died, or the core was closed or restarted while it ran. It is never
  started again by itself.

A task is someone's (`member_id`: 0 for Raphael, else a member of the owner's team, whose persona,
tools and memory it runs with), and a routine's run says which routine started it (`routine_id`).
Such a task is `quiet`: its report stays in its thread and is never said on the overlay.
`worker_questions` is one row for each question a task asked (see `Question`): it goes with its
task.

Use it on the thread that owns `conn` (the event loop). Nothing here logs a task.
"""

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..clock import wall_clock_ms

QUEUED, RUNNING, WAITING = "queued", "running", "waiting"
FINAL_STATES = ("done", "failed", "limit", "cancelled", "interrupted")


class Refused(Exception):
    """A task cannot be taken. `kind` names the phrase that says why (`persona.Phrases`):
    "worker_busy" (the queue is full) or "worker_disabled" (the worker crashed too often)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


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
    member_id: int = 0  # whose task it is: 0 for Raphael's, else a member of the team's
    routine_id: int | None = None  # the routine that started it, if one did

    @property
    def quiet(self) -> bool:
        """Whether its report stays in its thread: a member's task, or a routine's run, is never
        said on the overlay (the owner reads it in the app)."""
        return self.member_id != 0 or self.routine_id is not None


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
    # The service the step used (agent.steps.Step.detail): "brave", "builtin", "pdf"...; or None.
    detail: str | None = None


@dataclass
class Question:
    """A question a task asked the owner. `member_id` is the member whose thread shows it (0 is
    Raphael); `prefix` and `text` are what the card says; `remaining_s` is the task's own time that
    was left when it paused; `state` is "waiting", "approved", "denied", "expired" or "cancelled"."""

    id: str
    task_id: int
    member_id: int
    prefix: str
    text: str
    tier: str | None
    created_at_ms: int
    expires_at_ms: int
    remaining_s: float
    state: str
    answered_at_ms: int | None = None


_QUESTION_COLUMNS = (
    "id, task_id, member_id, prefix, text, tier, created_at_ms, expires_at_ms, remaining_s, state,"
    " answered_at_ms"
)
_TASK_COLUMNS = (
    "id, created_at_ms, started_at_ms, finished_at_ms, state, session_id, turn_id, language, text,"
    " report_prefix, report_text, report_detail, reason, model_calls, tool_calls, cost_usd,"
    " announced, rerun_of, member_id, routine_id"
)
_STEP_COLUMNS = "n, at_ms, kind, tool, server, tier, outcome, cost_usd, ms, reason, detail"


class TaskStore:
    """The `worker_tasks` and `worker_steps` tables."""

    def __init__(
        self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms

    def create(
        self,
        *,
        session_id: str,
        language: str,
        text: str,
        rerun_of: int | None = None,
        member_id: int = 0,
        routine_id: int | None = None,
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO worker_tasks (created_at_ms, state, session_id, language, text,"
                " rerun_of, member_id, routine_id) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?)",
                (self._now_ms(), session_id, language, text, rerun_of, member_id, routine_id),
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
        """Move a task that is not over to another state that is not over: it pauses
        ("waiting"), waits to go on ("queued") or goes on ("running")."""
        if state not in (QUEUED, RUNNING, WAITING):
            raise ValueError(f"{state!r} is not a state of a task that is not over")
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
        self.settle_in_progress(task_id)
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
        detail: str | None = None,
    ) -> int:
        """Write a step; its id. A step with the outcome "in_progress" is a call that was sent and
        has not come back; `update_step` ends it, and `settle_in_progress` does when nobody can."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO worker_steps (task_id, n, at_ms, kind, tool, server, tier, outcome,"
                " cost_usd, ms, reason, detail) VALUES (?, (SELECT COALESCE(MAX(n), 0) + 1 FROM"
                " worker_steps WHERE task_id = ?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    detail,
                ),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def update_step(
        self,
        step_id: int,
        *,
        outcome: str,
        tier: str | None = None,
        ms: int = 0,
        reason: str | None = None,
    ) -> None:
        """End a step that was written as "in_progress"."""
        with self._conn:
            self._conn.execute(
                "UPDATE worker_steps SET outcome = ?, tier = ?, ms = ?, reason = ? WHERE id = ?",
                (outcome, tier, ms, reason, step_id),
            )

    def settle_in_progress(self, task_id: int) -> None:
        """A call still "in_progress" when its task is over was cut off: it may have been made."""
        with self._conn:
            self._conn.execute(
                "UPDATE worker_steps SET outcome = 'cancelled', reason = 'cancelled'"
                " WHERE task_id = ? AND outcome = 'in_progress'",
                (task_id,),
            )

    def get(self, task_id: int) -> Task | None:
        row = self._conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM worker_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return None if row is None else Task(**dict(row))


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


    def interrupt_unfinished(
        self,
        report: Callable[[str, int], tuple[str, str]] | None = None,
        reason: str = "restart",
    ) -> list[Task]:
        """Mark the tasks that were queued, running or waiting as interrupted: the worker they ran
        in is gone (and the conversation a waiting one would go on with), and a task is never
        started again by itself. Their questions are cancelled. With `report` (the task's language and
        the tool calls it made, to a prefix and a line) each gets that report, to be said once
        (`announced` 0). Returns the tasks as they are now."""
        with self._conn:
            rows = self._conn.execute(
                "SELECT id, language FROM worker_tasks"
                " WHERE state IN ('queued', 'running', 'waiting')"
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    "UPDATE worker_questions SET state = 'cancelled' WHERE task_id = ?"
                    " AND state = 'waiting'",
                    (row["id"],),
                )
                self._conn.execute(
                    "UPDATE worker_steps SET outcome = 'cancelled', reason = 'cancelled'"
                    " WHERE task_id = ? AND outcome = 'in_progress'",
                    (row["id"],),
                )
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

    def for_brief(self, since_ms: int, limit: int, *, member_id: int = 0) -> list[Task]:
        """The tasks that run, then the newest of those that are queued or ended at `since_ms` or
        later, with something said (a cleared task keeps nothing)."""
        rows = self._conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM worker_tasks WHERE text != '' AND member_id = ?"
            " AND (state IN ('queued', 'running', 'waiting') OR finished_at_ms >= ?)"
            " ORDER BY CASE WHEN state = 'running' THEN 0 ELSE 1 END, id DESC LIMIT ?",
            (member_id, since_ms, limit),
        )
        return [Task(**dict(row)) for row in rows]

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
        self.settle_in_progress(task_id)
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
        """Delete a task, its steps and its questions, whatever state it is in."""
        with self._conn:
            self._conn.execute("DELETE FROM worker_steps WHERE task_id = ?", (task_id,))
            self._conn.execute("DELETE FROM worker_questions WHERE task_id = ?", (task_id,))
            self._conn.execute("DELETE FROM worker_tasks WHERE id = ?", (task_id,))

    def scrub(self, task_id: int) -> None:
        """Take what the user said, and anything reported, out of a task's row."""
        with self._conn:
            self._conn.execute(
                "UPDATE worker_tasks SET text = '', report_prefix = NULL, report_text = NULL,"
                " report_detail = NULL WHERE id = ?",
                (task_id,),
            )

    def of_member(self, member_id: int) -> list[Task]:
        """The tasks of `member_id`, whatever their state, oldest first."""
        rows = self._conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM worker_tasks WHERE member_id = ? ORDER BY id",
            (member_id,),
        )
        return [Task(**dict(row)) for row in rows]

    def clear(self) -> int:
        """Delete every task that is not queued or running, with its steps and its questions (a
        task that waits for the owner goes too); one that is queued or running stays."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM worker_questions WHERE task_id IN (SELECT id FROM worker_tasks"
                " WHERE state NOT IN ('queued', 'running'))"
            )
            self._conn.execute(
                "DELETE FROM worker_steps WHERE task_id IN (SELECT id FROM worker_tasks"
                " WHERE state NOT IN ('queued', 'running'))"
            )
            cursor = self._conn.execute(
                "DELETE FROM worker_tasks WHERE state NOT IN ('queued', 'running')"
            )
        return cursor.rowcount

    # --- the questions -------------------------------------------------------------------------

    def add_question(self, question: Question) -> None:
        with self._conn:
            self._conn.execute(
                f"INSERT INTO worker_questions ({_QUESTION_COLUMNS}) VALUES"
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    question.id,
                    question.task_id,
                    question.member_id,
                    question.prefix,
                    question.text,
                    question.tier,
                    question.created_at_ms,
                    question.expires_at_ms,
                    question.remaining_s,
                    question.state,
                    question.answered_at_ms,
                ),
            )

    def close_question(self, question_id: str, state: str) -> None:
        """End a question that waited: "approved", "denied", "expired" or "cancelled"."""
        with self._conn:
            self._conn.execute(
                "UPDATE worker_questions SET state = ?, answered_at_ms = ? WHERE id = ?"
                " AND state = 'waiting'",
                (state, self._now_ms(), question_id),
            )

    def question(self, question_id: str) -> Question | None:
        row = self._conn.execute(
            f"SELECT {_QUESTION_COLUMNS} FROM worker_questions WHERE id = ?", (question_id,)
        ).fetchone()
        return None if row is None else Question(**dict(row))

    def waiting_questions(self, member_id: int | None = None) -> list[Question]:
        """The questions that wait for the owner, the oldest first; of one member, or of all."""
        sql = f"SELECT {_QUESTION_COLUMNS} FROM worker_questions WHERE state = 'waiting'"
        args: tuple = ()
        if member_id is not None:
            sql += " AND member_id = ?"
            args = (member_id,)
        rows = self._conn.execute(sql + " ORDER BY created_at_ms, id", args)
        return [Question(**dict(row)) for row in rows]
