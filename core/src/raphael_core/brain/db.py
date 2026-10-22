"""The brain's SQLite database: opening it and keeping its schema current.

One connection is shared by all the stores. sqlite3 lets only the thread that opened a connection
use it, so open it on the thread that runs the brain's handlers.
"""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from .mishearing import may_become_rule
from .textnorm import replacement_key

# A migration is one transaction that also sets user_version, so a failure cannot leave half a
# schema behind with the version still saying "empty". One that has to look at the data is a
# function (v3, v4) instead of a script.
_MIGRATION_V1 = """
BEGIN;
CREATE TABLE history (id INTEGER PRIMARY KEY, created_at_ms INTEGER NOT NULL, kind TEXT NOT NULL,
  raw_text TEXT NOT NULL, text TEXT NOT NULL, cleaned INTEGER NOT NULL, fallback_reason TEXT,
  target_app TEXT, audio_seconds REAL NOT NULL, stt_ms INTEGER NOT NULL, llm_ms INTEGER NOT NULL,
  model TEXT, cost_usd REAL NOT NULL DEFAULT 0);
CREATE INDEX history_created ON history(created_at_ms);
CREATE TABLE dictionary (id INTEGER PRIMARY KEY, word TEXT NOT NULL UNIQUE,
  misheard TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'manual',
  created_at_ms INTEGER NOT NULL);
CREATE TABLE dismissed_suggestions (word TEXT PRIMARY KEY);
CREATE TABLE spend (id INTEGER PRIMARY KEY, created_at_ms INTEGER NOT NULL, purpose TEXT NOT NULL,
  model TEXT, cost_usd REAL NOT NULL, prompt_tokens INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
PRAGMA user_version = 1;
COMMIT;
"""

# v2: which speech-to-text heard each dictation (a cloud model's slug or a local engine's name,
# several joined with "+" when the cloud fell back for part of it); NULL for older rows.
_MIGRATION_V2 = """
BEGIN;
ALTER TABLE history ADD COLUMN stt TEXT;
PRAGMA user_version = 2;
COMMIT;
"""


def _adopt_learned_evidence(conn: sqlite3.Connection) -> None:
    """Create `dictionary_evidence` and sort the learned forms into rules and hints.

    A pair learned from a correction used to become a replacement rule at once. Now it is a rule
    only when its form is distinctive, the two sound alike (`mishearing.may_become_rule`) and the
    pair has been seen twice; until then the form is a hint. So here the forms of the learned
    entries (source "learned") that fail that test stop being rules and become hints, pending
    evidence: they are not deleted, and the word stays. The forms that pass stay rules, and are
    recorded as learned (status "rule") so that a correction by the user can still undo them. What
    the user typed (the manual and suggested entries, forms included) is left alone.

    Runs inside the caller's transaction and is safe to run twice.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dictionary_evidence (word_key TEXT NOT NULL,"
        " form_key TEXT NOT NULL, form TEXT NOT NULL, count INTEGER NOT NULL, status TEXT NOT NULL,"
        " updated_at_ms INTEGER NOT NULL, PRIMARY KEY (word_key, form_key))"
    )
    entries = conn.execute(
        "SELECT id, word, misheard, created_at_ms FROM dictionary WHERE source = 'learned'"
    ).fetchall()
    for entry_id, word, misheard, created_at_ms in entries:
        try:
            forms = json.loads(misheard)
        except ValueError:
            continue
        if not isinstance(forms, list) or not all(isinstance(form, str) for form in forms):
            continue
        kept: list[str] = []
        for form in forms:
            is_rule = may_become_rule(word, form)
            if is_rule:
                kept.append(form)
            conn.execute(
                "INSERT OR IGNORE INTO dictionary_evidence"
                " (word_key, form_key, form, count, status, updated_at_ms)"
                " VALUES (?, ?, ?, 1, ?, ?)",
                (
                    replacement_key(word),
                    replacement_key(form),
                    form,
                    "rule" if is_rule else "pending",
                    created_at_ms,
                ),
            )
        if kept != forms:
            conn.execute(
                "UPDATE dictionary SET misheard = ? WHERE id = ?",
                (json.dumps(kept, ensure_ascii=False), entry_id),
            )


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """v3: the evidence behind the pairs the brain learns from corrections (see above).

    Like the scripts above, one transaction with the version.
    """
    conn.execute("BEGIN")
    _adopt_learned_evidence(conn)
    conn.execute("PRAGMA user_version = 3")
    conn.execute("COMMIT")


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """v4: make sure the v3 evidence exists, whichever v3 a database went through.

    An unreleased task-runner build also numbered its own schema v3 (`assistant_runs` and
    `budget_requests`, left in place and unused here). A database that reached v3 that way never
    got `dictionary_evidence`, so it is made here; for one that did, this changes nothing.
    """
    conn.execute("BEGIN")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dictionary_evidence'"
    ).fetchone()
    if not exists:
        _adopt_learned_evidence(conn)
    conn.execute("PRAGMA user_version = 4")
    conn.execute("COMMIT")


# v5: what the voice assistant was asked and what it answered, one row for each turn
# (raphael_core.agent.history). `status` is "done", "confirm" (waiting for a yes or a no),
# "denied", "expired" or "error"; `detail` is the longer text of an answer, if it had one.
_MIGRATION_V5 = """
BEGIN;
CREATE TABLE agent_turns (id INTEGER PRIMARY KEY, created_at_ms INTEGER NOT NULL,
  session_id TEXT NOT NULL, question TEXT NOT NULL, route TEXT NOT NULL, prefix TEXT NOT NULL,
  text TEXT NOT NULL, detail TEXT, status TEXT NOT NULL, cost_usd REAL NOT NULL DEFAULT 0);
CREATE INDEX agent_turns_created ON agent_turns(created_at_ms);
PRAGMA user_version = 5;
COMMIT;
"""

# v6: once the table of iCal calendars for a Today card on Home. The feature was removed, and a
# version number is never reused (a database may already be at 6), so v6 stays and makes nothing.
_MIGRATION_V6 = """
BEGIN;
PRAGMA user_version = 6;
COMMIT;
"""

# v7: drops the calendars table that v6 once made. A new database never has it, hence IF EXISTS.
_MIGRATION_V7 = """
BEGIN;
DROP TABLE IF EXISTS calendars;
PRAGMA user_version = 7;
COMMIT;
"""

# v8: what the plugins' tools were asked to do (raphael_core.integrations.audit), one row for
# each call: when, which server and tool, at which tier, who approved ("auto", "user" or
# "none") and how it ended. It holds no arguments and no results.
_MIGRATION_V8 = """
BEGIN;
CREATE TABLE tool_audit (id INTEGER PRIMARY KEY, at_ms INTEGER NOT NULL, server TEXT NOT NULL,
  tool TEXT NOT NULL, tier TEXT, approver TEXT NOT NULL, outcome TEXT NOT NULL);
CREATE INDEX tool_audit_at ON tool_audit(at_ms);
PRAGMA user_version = 8;
COMMIT;
"""

# v9: the background worker (raphael_core.background). `worker_tasks` is one row for each task:
# what was handed over (`text`, which is what the user said, so it is pruned and cleared with the
# assistant's history), how it stands (`state`: queued, running, done, failed, limit, cancelled or
# interrupted) and the report it ended with. `worker_steps` is one row for each model call and each
# tool call of a task: which tool, at which tier, how it ended and what it cost; never an
# argument or a result. `tool_audit.task_id` ties a tool call to the task that made it.
_MIGRATION_V9 = """
BEGIN;
ALTER TABLE tool_audit ADD COLUMN task_id INTEGER;
CREATE TABLE worker_tasks (id INTEGER PRIMARY KEY, created_at_ms INTEGER NOT NULL,
  started_at_ms INTEGER, finished_at_ms INTEGER, state TEXT NOT NULL, session_id TEXT NOT NULL,
  turn_id INTEGER, language TEXT NOT NULL, text TEXT NOT NULL, report_prefix TEXT,
  report_text TEXT, report_detail TEXT, reason TEXT, model_calls INTEGER NOT NULL DEFAULT 0,
  tool_calls INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0);
CREATE INDEX worker_tasks_created ON worker_tasks(created_at_ms);
CREATE TABLE worker_steps (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, n INTEGER NOT NULL,
  at_ms INTEGER NOT NULL, kind TEXT NOT NULL, tool TEXT, server TEXT, tier TEXT,
  outcome TEXT NOT NULL, cost_usd REAL NOT NULL DEFAULT 0, ms INTEGER NOT NULL DEFAULT 0);
CREATE INDEX worker_steps_task ON worker_steps(task_id, n);
PRAGMA user_version = 9;
COMMIT;
"""

# v10: what opening anything taught Raphael (raphael_core.agent.targets), one row for each name
# of a web page that opened: the name as it was said (`name_key`, short, in lower case) and the
# address it opened. Never what was said around it. It is cleared with the assistant's history
# and pruned by `last_used_ms`.
_MIGRATION_V10 = """
BEGIN;
CREATE TABLE open_targets (name_key TEXT PRIMARY KEY, url TEXT NOT NULL,
  uses INTEGER NOT NULL DEFAULT 1, last_used_ms INTEGER NOT NULL);
CREATE INDEX open_targets_used ON open_targets(last_used_ms);
PRAGMA user_version = 10;
COMMIT;
"""

# v11: what the conversation view shows of a turn. `agent_turns.source` is where the request came
# from ("voice", or a chat app such as "telegram") and `model` the model that answered, if one did.
# `agent_steps` is one row for each thing the fast tier did for a turn (a lookup, a plugin's tool, a
# command for this computer): which tool of which server, how it ended, how long it took and, when
# it failed, a short classified reason (`agent.steps.REASONS`). `worker_steps.reason` is the same
# for the background worker. Like the audit, never an argument or a result, nor an error message,
# which a service may write with what it was given in it.
_MIGRATION_V11 = """
BEGIN;
ALTER TABLE agent_turns ADD COLUMN source TEXT NOT NULL DEFAULT 'voice';
ALTER TABLE agent_turns ADD COLUMN model TEXT;
ALTER TABLE worker_steps ADD COLUMN reason TEXT;
CREATE TABLE agent_steps (id INTEGER PRIMARY KEY, turn_id INTEGER NOT NULL, n INTEGER NOT NULL,
  at_ms INTEGER NOT NULL, kind TEXT NOT NULL, tool TEXT NOT NULL, server TEXT,
  outcome TEXT NOT NULL, ms INTEGER NOT NULL DEFAULT 0, reason TEXT);
CREATE INDEX agent_steps_turn ON agent_steps(turn_id, n);
PRAGMA user_version = 11;
COMMIT;
"""

# v12: `worker_tasks.announced` is 0 for the report of a task that was interrupted and has not been
# said yet (it is said once, when the app next says hello), and `rerun_of` the task a rerun started
# again.
_MIGRATION_V12 = """
BEGIN;
ALTER TABLE worker_tasks ADD COLUMN announced INTEGER NOT NULL DEFAULT 1;
ALTER TABLE worker_tasks ADD COLUMN rerun_of INTEGER;
PRAGMA user_version = 12;
COMMIT;
"""

_MIGRATIONS: dict[int, str | Callable[[sqlite3.Connection], None]] = {
    1: _MIGRATION_V1,
    2: _MIGRATION_V2,
    3: _migrate_v3,
    4: _migrate_v4,
    5: _MIGRATION_V5,
    6: _MIGRATION_V6,
    7: _MIGRATION_V7,
    8: _MIGRATION_V8,
    9: _MIGRATION_V9,
    10: _MIGRATION_V10,
    11: _MIGRATION_V11,
    12: _MIGRATION_V12,
}
LATEST_VERSION = max(_MIGRATIONS)


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database at `path` (":memory:" works) with its schema in place.

    Rows come back as sqlite3.Row. A file database runs in WAL mode.
    """
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the schema up to LATEST_VERSION, one migration at a time from `user_version`.

    A database that is already current is left alone, so reopening a file is safe. Each migration
    is its own transaction: one that fails leaves the database at the version before it.
    """
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    for target in range(version + 1, LATEST_VERSION + 1):
        step = _MIGRATIONS[target]
        try:
            if callable(step):
                step(conn)
            else:
                conn.executescript(step)
        except BaseException:
            conn.rollback()  # a failed statement leaves the script's transaction open
            raise
