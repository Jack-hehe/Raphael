"""The brain's SQLite database: opening it and keeping its schema current.

One connection is shared by all the stores. sqlite3 lets only the thread that opened a connection
use it, so open it on the thread that runs the brain's handlers.
"""

import sqlite3
from pathlib import Path

# A migration is one transaction that also sets user_version, so a failure cannot leave half a
# schema behind with the version still saying "empty".
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

_MIGRATIONS = {1: _MIGRATION_V1, 2: _MIGRATION_V2}
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
        try:
            conn.executescript(_MIGRATIONS[target])
        except BaseException:
            conn.rollback()  # a failed statement leaves the script's transaction open
            raise
