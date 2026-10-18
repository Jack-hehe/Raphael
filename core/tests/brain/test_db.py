import sqlite3

import pytest

from raphael_core.brain import db
from raphael_core.brain.dictionary import DictionaryStore
from raphael_core.brain.history import HistoryStore
from raphael_core.brain.settings import SettingsStore

from .conftest import item

# Schema v1 as the plan fixes it: every column with its type, NOT NULL, DEFAULT and primary key.
SCHEMA_V1 = {
    "history": [
        "id INTEGER PK",
        "created_at_ms INTEGER NOT NULL",
        "kind TEXT NOT NULL",
        "raw_text TEXT NOT NULL",
        "text TEXT NOT NULL",
        "cleaned INTEGER NOT NULL",
        "fallback_reason TEXT",
        "target_app TEXT",
        "audio_seconds REAL NOT NULL",
        "stt_ms INTEGER NOT NULL",
        "llm_ms INTEGER NOT NULL",
        "model TEXT",
        "cost_usd REAL NOT NULL DEFAULT 0",
    ],
    "dictionary": [
        "id INTEGER PK",
        "word TEXT NOT NULL",
        "misheard TEXT NOT NULL DEFAULT '[]'",
        "source TEXT NOT NULL DEFAULT 'manual'",
        "created_at_ms INTEGER NOT NULL",
    ],
    "dismissed_suggestions": ["word TEXT PK"],
    "spend": [
        "id INTEGER PK",
        "created_at_ms INTEGER NOT NULL",
        "purpose TEXT NOT NULL",
        "model TEXT",
        "cost_usd REAL NOT NULL",
        "prompt_tokens INTEGER NOT NULL",
        "completion_tokens INTEGER NOT NULL",
    ],
    "settings": ["key TEXT PK", "value TEXT NOT NULL"],
}
# Schema v2 adds the speech-to-text that heard each dictation to the history.
SCHEMA_V2 = {**SCHEMA_V1, "history": [*SCHEMA_V1["history"], "stt TEXT"]}
LATEST = 2


def schema_names(conn, kind):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
    return {r[0] for r in rows}


def describe_columns(conn, table):
    """The columns of `table` written the way SCHEMA_V1 spells them."""
    columns = []
    for col in conn.execute(f"PRAGMA table_info({table})"):
        text = f"{col['name']} {col['type']}"
        if col["pk"]:
            text += " PK"
        if col["notnull"]:
            text += " NOT NULL"
        if col["dflt_value"] is not None:
            text += f" DEFAULT {col['dflt_value']}"
        columns.append(text)
    return columns


def test_connect_creates_the_latest_schema(conn):
    assert schema_names(conn, "table") == set(SCHEMA_V2)
    assert "history_created" in schema_names(conn, "index")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    for table, columns in SCHEMA_V2.items():
        assert describe_columns(conn, table) == columns


def test_a_v1_database_is_migrated_to_v2_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(db._MIGRATION_V1)
    raw.execute(
        "INSERT INTO history (created_at_ms, kind, raw_text, text, cleaned, audio_seconds,"
        " stt_ms, llm_ms, model) VALUES (5, 'dictation', 'a', 'A', 1, 1.0, 10, 20, 'm')"
    )
    raw.commit()
    raw.close()

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert describe_columns(conn, "history") == SCHEMA_V2["history"]
    old = HistoryStore(conn).latest()
    assert (old.text, old.model, old.stt) == ("A", "m", None)
    conn.close()


def test_a_failed_v2_migration_leaves_the_database_at_v1():
    raw = sqlite3.connect(":memory:")
    raw.executescript(db._MIGRATION_V1)
    raw.execute("ALTER TABLE history ADD COLUMN stt TEXT")  # in the way of the v2 migration
    raw.commit()
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(raw)
    assert not raw.in_transaction
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 1
    raw.close()


def test_dictionary_words_are_unique(conn):
    conn.execute("INSERT INTO dictionary (word, created_at_ms) VALUES ('拉斐爾', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO dictionary (word, created_at_ms) VALUES ('拉斐爾', 2)")


def test_rows_can_be_read_by_name(conn):
    assert conn.row_factory is sqlite3.Row


def test_a_file_database_uses_wal_and_keeps_its_data_when_reopened(tmp_path):
    path = tmp_path / "raphael.db"
    first = db.connect(path)
    assert first.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    SettingsStore(first).set("language", "ja")
    first.close()

    second = db.connect(str(path))  # must not try to create the tables again
    assert SettingsStore(second).get("language") == "ja"
    assert second.execute("PRAGMA user_version").fetchone()[0] == LATEST
    second.close()


def test_every_write_the_stores_make_is_committed(tmp_path):
    # A second connection sees only what the first has committed. Each write is checked right
    # away, because the next write on the same connection would commit the one before it.
    path = tmp_path / "raphael.db"
    writer, reader = db.connect(path), db.connect(path)
    history, dictionary, settings = (
        HistoryStore(writer),
        DictionaryStore(writer),
        SettingsStore(writer),
    )

    def history_texts():
        return [i.text for i in HistoryStore(reader).list()]

    def dictionary_words():
        return [(e.word, e.misheard) for e in DictionaryStore(reader).list()]

    history.add(item(created_at_ms=1000, text="old"))
    assert history_texts() == ["old"]
    history.prune(older_than_ms=1500)
    assert history_texts() == []
    gone = history.add(item(created_at_ms=2000, text="gone"))
    assert history_texts() == ["gone"]
    history.delete(gone)
    assert history_texts() == []

    dictionary.add("拉斐爾", ["拉菲爾"])
    assert dictionary_words() == [("拉斐爾", ["拉菲爾"])]
    dictionary.add("拉斐爾", ["拉非爾"])  # merging is an UPDATE
    assert dictionary_words() == [("拉斐爾", ["拉菲爾", "拉非爾"])]
    extra = dictionary.add("甲")
    assert [word for word, _ in dictionary_words()] == ["甲", "拉斐爾"]
    dictionary.delete(extra.id)
    assert [word for word, _ in dictionary_words()] == ["拉斐爾"]
    dictionary.dismiss("Tailscale")
    assert DictionaryStore(reader).dismissed() == {"Tailscale"}

    settings.set("language", "ja")
    assert SettingsStore(reader).get("language") == "ja"

    writer.close()
    reader.close()


def test_a_failed_migration_leaves_no_partial_schema():
    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE TABLE settings (x)")  # in the way of the last table of the schema
    raw.commit()

    with pytest.raises(sqlite3.OperationalError):
        db.migrate(raw)

    assert not raw.in_transaction
    assert schema_names(raw, "table") == {"settings"}
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    raw.close()


def test_connect_closes_the_connection_when_setting_it_up_fails(monkeypatch):
    opened = []

    def failing_migrate(conn):
        opened.append(conn)
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "migrate", failing_migrate)
    with pytest.raises(RuntimeError):
        db.connect(":memory:")
    with pytest.raises(sqlite3.ProgrammingError):  # "Cannot operate on a closed database."
        opened[0].execute("SELECT 1")
