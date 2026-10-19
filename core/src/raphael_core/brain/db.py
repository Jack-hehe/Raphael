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


_MIGRATIONS: dict[int, str | Callable[[sqlite3.Connection], None]] = {
    1: _MIGRATION_V1,
    2: _MIGRATION_V2,
    3: _migrate_v3,
    4: _migrate_v4,
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
