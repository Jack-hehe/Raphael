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
# what was handed over (`text`, which is what the user said, so it is cleared with the assistant's
# history), how it stands (`state`: queued, running, done, failed, limit, cancelled or
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

def create_turn_search(conn: sqlite3.Connection) -> None:
    """Create `agent_turns_fts`, the search over the assistant's turns, if it is not there.

    It is an FTS5 table of two columns, `pairs` and `chars`, made in Python from the question and
    the answer of each turn (`memory.terms.index_terms`), its rowid the turn's id. If this SQLite has
    no FTS5 it is a plain table of the same columns instead, and the search reads it with LIKE
    (`AgentHistory.search` tells the two apart by the table's SQL). Python's own SQLite has had FTS5
    since 3.9; the plain table is for a build that left it out."""
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS agent_turns_fts USING fts5(pairs, chars,"
            " tokenize = 'unicode61 remove_diacritics 0')"
        )
    except sqlite3.OperationalError:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_turns_fts (rowid INTEGER PRIMARY KEY,"
            " pairs TEXT NOT NULL, chars TEXT NOT NULL)"
        )


def _migrate_v13(conn: sqlite3.Connection) -> None:
    """v13: one continuous conversation with Raphael, and what it is searched by.

    - `agent_summary` is the rolling summary of the conversation (`memory.conversation`): one row,
      its text and the last turn it covers. The turns after it are the ones the model reads as
      they were said.
    - `memory_state` holds the few numbers the memory keeps between runs (the last turn the
      reflection pass read, when the memory was last consolidated), by name.
    - `agent_turns_fts` is the search over the turns (`create_turn_search`), filled here with the
      turns there already are.

    Like the others, one transaction with the version.
    """
    from ..memory.terms import index_terms  # the brain must not need the memory to import

    conn.execute("BEGIN")
    conn.execute(
        "CREATE TABLE agent_summary (id INTEGER PRIMARY KEY CHECK (id = 1), text TEXT NOT NULL,"
        " through_turn_id INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL)"
    )
    conn.execute("CREATE TABLE memory_state (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    create_turn_search(conn)
    rows = conn.execute("SELECT id, question, text, detail FROM agent_turns").fetchall()
    for turn_id, question, text, detail in rows:
        terms = index_terms(question, text, detail)
        conn.execute(
            "INSERT INTO agent_turns_fts (rowid, pairs, chars) VALUES (?, ?, ?)",
            (turn_id, terms.pairs, terms.chars),
        )
    conn.execute("PRAGMA user_version = 13")
    conn.execute("COMMIT")


# v14: what the tools of the services left in a turn. `agent_turns.attachments` is the files the
# owner handed Raphael with the request, as JSON (`[{"name", "mime"}]`, so the conversation view can
# show which), or NULL: never a byte of them, which stay in memory for the turn. `agent_steps.detail`
# and `worker_steps.detail` say which service a step used (the search engine of a web search, the
# reader of a page, "image" or "pdf" for a file read), as one word of a fixed list: never what it
# was given.
_MIGRATION_V14 = """
BEGIN;
ALTER TABLE agent_turns ADD COLUMN attachments TEXT;
ALTER TABLE agent_steps ADD COLUMN detail TEXT;
ALTER TABLE worker_steps ADD COLUMN detail TEXT;
PRAGMA user_version = 14;
COMMIT;
"""


# v15: the members of the owner's team (raphael_core.members), each with a conversation of its own.
#
# - `agent_turns.member_id` is whose thread a turn is in: 0 for Raphael (every turn there was until
#   now), else the id of a row of `members`. `agent_turns.peer_id` is, for an exchange between two
#   of them (`route` "exchange": one asked the other through `ask_member`), the other one.
# - `worker_tasks.member_id` is whose task it is (0: Raphael's), and `worker_tasks.routine_id` the
#   routine that started it, if one did.
# - `conversation_summaries` is the rolling summary of each conversation, one row a member, Raphael's
#   as member 0; it takes the place of `agent_summary`, whose one row becomes Raphael's.
# - `member_groups` are the groups of the member column: a name, a place, and the places their
#   members look for progress (`sources`, JSON: `[{kind, ref}]`, GitHub or Notion).
# - `members` are the members themselves. `avatar` is "", "preset:<stem>" or "file:<name>" (a copy
#   in the app's local folder, never the path it came from); `model` "" is the answer model; `tools`
#   JSON is the tools and connections it may use, NULL for every one that is on.
# - `routines` are what a member does on a schedule (`schedule` JSON), with when it last ran and
#   when it runs next.
# - `member_unread` holds the members (Raphael too, as 0) whose thread has news the owner has not
#   seen.
#
# The members and the safety rules (v16) were built side by side, and each first took the number 15.
# A database can therefore be at 15 with either of them, so neither migration assumes what the other
# made: each part is applied only where it is missing (`_add_members`, `_add_safety`), v15 applies
# the members, and v16 applies both, which for a database that went through this v15 adds only the
# safety rules, and for one that took the other v15 adds only the members.
_MEMBERS_TABLES = (
    "CREATE TABLE IF NOT EXISTS member_groups (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " name TEXT NOT NULL, position INTEGER NOT NULL, sources TEXT NOT NULL DEFAULT '[]')",
    "CREATE TABLE IF NOT EXISTS members (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " name TEXT NOT NULL UNIQUE COLLATE NOCASE, role TEXT NOT NULL DEFAULT '',"
    " instructions TEXT NOT NULL DEFAULT '', avatar TEXT NOT NULL DEFAULT '', group_id INTEGER,"
    " model TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL, created_at_ms INTEGER NOT NULL,"
    " tools TEXT)",
    "CREATE TABLE IF NOT EXISTS routines (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " member_id INTEGER NOT NULL, title TEXT NOT NULL, prompt TEXT NOT NULL,"
    " schedule TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, last_run_ms INTEGER,"
    " next_run_ms INTEGER, created_at_ms INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS routines_member ON routines(member_id)",
    "CREATE TABLE IF NOT EXISTS member_unread (member_id INTEGER PRIMARY KEY)",
)

# v16: the owner's safety rules, and the questions the background waits on.
#
# - `safety_rules` is one row for each rule (`agent.safety`): the owner's own words for an action
#   (one line, at most 80 characters), what Raphael does when an action falls under it (`ask`,
#   `allow` or `deny`) and its place in the list. `safety_state` says, by the key "seeded", that the
#   three rules of the first run were put there once (they are not put back when deleted).
# - `worker_questions` is one row for each question a background task asked the owner
#   (`background.service`): the task, the member whose thread it shows in (0 is Raphael), its
#   prefix and its line as the card says it, the tier it was asked at, when it was asked and when it
#   runs out (24 hours later), the task's time that was left when it paused, and how it ended
#   (`waiting`, `approved`, `denied`, `expired`, `cancelled`). The line names the service and the
#   tool and shows the start of what it is given, as a question of the fast tier does in the
#   history; never the whole of it. The questions go with their tasks.
_SAFETY_TABLES = (
    "CREATE TABLE IF NOT EXISTS safety_rules (id INTEGER PRIMARY KEY, action TEXT NOT NULL,"
    " verdict TEXT NOT NULL, position INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS safety_state (key TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS worker_questions (id TEXT PRIMARY KEY, task_id INTEGER NOT NULL,"
    " member_id INTEGER NOT NULL DEFAULT 0, prefix TEXT NOT NULL, text TEXT NOT NULL, tier TEXT,"
    " created_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL, remaining_s REAL NOT NULL,"
    " state TEXT NOT NULL DEFAULT 'waiting', answered_at_ms INTEGER)",
    "CREATE INDEX IF NOT EXISTS worker_questions_task ON worker_questions(task_id)",
)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Whether `table` has a column named `column`."""
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    """Whether a table named `table` exists."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _add_members(conn: sqlite3.Connection) -> None:
    """Add the members' part of the schema (see v15) where it is missing; safe to run twice.

    Runs inside the caller's transaction. `agent_summary`'s one row becomes Raphael's row of
    `conversation_summaries` only while `agent_summary` is still there, so a second run moves
    nothing."""
    for table, column, decl in (
        ("agent_turns", "member_id", "INTEGER NOT NULL DEFAULT 0"),
        ("agent_turns", "peer_id", "INTEGER"),
        ("worker_tasks", "member_id", "INTEGER NOT NULL DEFAULT 0"),
        ("worker_tasks", "routine_id", "INTEGER"),
    ):
        if not _has_column(conn, table, column):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS agent_turns_member ON agent_turns(member_id, id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversation_summaries (member_id INTEGER PRIMARY KEY,"
        " text TEXT NOT NULL, through_turn_id INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL)"
    )
    if _has_table(conn, "agent_summary"):
        conn.execute(
            "INSERT OR IGNORE INTO conversation_summaries"
            " (member_id, text, through_turn_id, updated_at_ms)"
            " SELECT 0, text, through_turn_id, updated_at_ms FROM agent_summary"
        )
        conn.execute("DROP TABLE agent_summary")
    for statement in _MEMBERS_TABLES:
        conn.execute(statement)


def _add_safety(conn: sqlite3.Connection) -> None:
    """Add the safety rules' part of the schema (see v16) where it is missing; safe to run twice.

    Runs inside the caller's transaction."""
    for statement in _SAFETY_TABLES:
        conn.execute(statement)


def _migrate_v15(conn: sqlite3.Connection) -> None:
    """v15: the members of the owner's team (see the notes above `_MEMBERS_TABLES`).

    Like the others, one transaction with the version."""
    conn.execute("BEGIN")
    _add_members(conn)
    conn.execute("PRAGMA user_version = 15")
    conn.execute("COMMIT")


def _migrate_v16(conn: sqlite3.Connection) -> None:
    """v16: the owner's safety rules and the background's questions (see `_SAFETY_TABLES`).

    It also applies the members' part where it is missing, for a database that took the safety
    rules' own v15 before the two were numbered apart. One transaction with the version."""
    conn.execute("BEGIN")
    _add_members(conn)
    _add_safety(conn)
    conn.execute("PRAGMA user_version = 16")
    conn.execute("COMMIT")


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
    13: _migrate_v13,
    14: _MIGRATION_V14,
    15: _migrate_v15,
    16: _migrate_v16,
    17: """
BEGIN;
CREATE TABLE agent_messages (
    turn_id INTEGER NOT NULL REFERENCES agent_turns(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('progress', 'reply', 'event')),
    text TEXT NOT NULL,
    at_ms INTEGER NOT NULL,
    PRIMARY KEY (turn_id, seq)
);
PRAGMA user_version = 17;
COMMIT;
""",
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
