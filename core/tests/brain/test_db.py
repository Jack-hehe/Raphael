import json
import sqlite3

import pytest

from raphael_core.brain import db
from raphael_core.brain.dictionary import DictionaryStore, Learned
from raphael_core.brain.history import HistoryStore
from raphael_core.brain.settings import SettingsStore

from .conftest import item

# Schema v1 as it was made: every column with its type, NOT NULL, DEFAULT and primary key.
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
# Schema v3 adds the evidence behind the pairs the brain learns from corrections.
SCHEMA_V3 = {
    **SCHEMA_V2,
    "dictionary_evidence": [
        "word_key TEXT PK NOT NULL",
        "form_key TEXT PK NOT NULL",
        "form TEXT NOT NULL",
        "count INTEGER NOT NULL",
        "status TEXT NOT NULL",
        "updated_at_ms INTEGER NOT NULL",
    ],
}
# Schema v4 adds no table: it makes sure v3's evidence exists.
# Schema v5 adds the voice assistant's turns.
SCHEMA_V5 = {
    **SCHEMA_V3,
    "agent_turns": [
        "id INTEGER PK",
        "created_at_ms INTEGER NOT NULL",
        "session_id TEXT NOT NULL",
        "question TEXT NOT NULL",
        "route TEXT NOT NULL",
        "prefix TEXT NOT NULL",
        "text TEXT NOT NULL",
        "detail TEXT",
        "status TEXT NOT NULL",
        "cost_usd REAL NOT NULL DEFAULT 0",
    ],
}
# Schemas v6 and v7 add no table: v6 was the removed iCal feature's calendars table, which v7 drops.
# Schema v8 adds the audit of the plugins' tool calls.
SCHEMA_V8 = {
    **SCHEMA_V5,
    "tool_audit": [
        "id INTEGER PK",
        "at_ms INTEGER NOT NULL",
        "server TEXT NOT NULL",
        "tool TEXT NOT NULL",
        "tier TEXT",
        "approver TEXT NOT NULL",
        "outcome TEXT NOT NULL",
    ],
}
# Schema v9 adds the background worker's tasks and steps, and ties the audit to the task.
SCHEMA_V9 = {
    **{**SCHEMA_V8, "tool_audit": [*SCHEMA_V8["tool_audit"], "task_id INTEGER"]},
    "worker_tasks": [
        "id INTEGER PK",
        "created_at_ms INTEGER NOT NULL",
        "started_at_ms INTEGER",
        "finished_at_ms INTEGER",
        "state TEXT NOT NULL",
        "session_id TEXT NOT NULL",
        "turn_id INTEGER",
        "language TEXT NOT NULL",
        "text TEXT NOT NULL",
        "report_prefix TEXT",
        "report_text TEXT",
        "report_detail TEXT",
        "reason TEXT",
        "model_calls INTEGER NOT NULL DEFAULT 0",
        "tool_calls INTEGER NOT NULL DEFAULT 0",
        "cost_usd REAL NOT NULL DEFAULT 0",
    ],
    "worker_steps": [
        "id INTEGER PK",
        "task_id INTEGER NOT NULL",
        "n INTEGER NOT NULL",
        "at_ms INTEGER NOT NULL",
        "kind TEXT NOT NULL",
        "tool TEXT",
        "server TEXT",
        "tier TEXT",
        "outcome TEXT NOT NULL",
        "cost_usd REAL NOT NULL DEFAULT 0",
        "ms INTEGER NOT NULL DEFAULT 0",
    ],
}
# Schema v10 adds what opening taught Raphael: the names of web pages and the address each opened.
SCHEMA_V10 = {
    **SCHEMA_V9,
    "open_targets": [
        "name_key TEXT PK",
        "url TEXT NOT NULL",
        "uses INTEGER NOT NULL DEFAULT 1",
        "last_used_ms INTEGER NOT NULL",
    ],
}
# Schema v11 adds what the conversation view shows: where a turn came from, the model that
# answered, the fast tier's steps of a turn and why a step of the worker failed.
SCHEMA_V11 = {
    **SCHEMA_V10,
    "agent_turns": [
        *SCHEMA_V10["agent_turns"],
        "source TEXT NOT NULL DEFAULT 'voice'",
        "model TEXT",
    ],
    "worker_steps": [*SCHEMA_V10["worker_steps"], "reason TEXT"],
    "agent_steps": [
        "id INTEGER PK",
        "turn_id INTEGER NOT NULL",
        "n INTEGER NOT NULL",
        "at_ms INTEGER NOT NULL",
        "kind TEXT NOT NULL",
        "tool TEXT NOT NULL",
        "server TEXT",
        "outcome TEXT NOT NULL",
        "ms INTEGER NOT NULL DEFAULT 0",
        "reason TEXT",
    ],
}
# Schema v12 adds what a rerun and an interrupted task's report need: whether the report was
# said yet and the task a rerun came from.
SCHEMA_V12 = {
    **SCHEMA_V11,
    "worker_tasks": [
        *SCHEMA_V11["worker_tasks"],
        "announced INTEGER NOT NULL DEFAULT 1",
        "rerun_of INTEGER",
    ],
}
# Schema v13 adds one continuous conversation: its rolling summary, the numbers the memory keeps,
# and the search over the turns (an FTS5 table, which SQLite keeps in shadow tables of its own).
SCHEMA_V13 = {
    **SCHEMA_V12,
    "agent_summary": [
        "id INTEGER PK",
        "text TEXT NOT NULL",
        "through_turn_id INTEGER NOT NULL",
        "updated_at_ms INTEGER NOT NULL",
    ],
    "memory_state": ["key TEXT PK", "value INTEGER NOT NULL"],
    "agent_turns_fts": [" ", " "],  # a virtual table: its columns have no type
}
# Schema v14 adds what the tools of the services leave: the names and types of the files handed with
# a turn, and the service a step used.
SCHEMA_V14 = {
    **SCHEMA_V13,
    "agent_turns": [*SCHEMA_V13["agent_turns"], "attachments TEXT"],
    "agent_steps": [*SCHEMA_V13["agent_steps"], "detail TEXT"],
    "worker_steps": [*SCHEMA_V13["worker_steps"], "detail TEXT"],
}
# Schema v15 adds the members of the owner's team: whose thread a turn is in and whose a task is, a
# summary for each conversation in place of the one, the groups, the members, their routines and
# which threads have news.
SCHEMA_V15 = {
    **{name: columns for name, columns in SCHEMA_V14.items() if name != "agent_summary"},
    "agent_turns": [
        *SCHEMA_V14["agent_turns"],
        "member_id INTEGER NOT NULL DEFAULT 0",
        "peer_id INTEGER",
    ],
    "worker_tasks": [
        *SCHEMA_V14["worker_tasks"],
        "member_id INTEGER NOT NULL DEFAULT 0",
        "routine_id INTEGER",
    ],
    "conversation_summaries": [
        "member_id INTEGER PK",
        "text TEXT NOT NULL",
        "through_turn_id INTEGER NOT NULL",
        "updated_at_ms INTEGER NOT NULL",
    ],
    "member_groups": [
        "id INTEGER PK",
        "name TEXT NOT NULL",
        "position INTEGER NOT NULL",
        "sources TEXT NOT NULL DEFAULT '[]'",
    ],
    "members": [
        "id INTEGER PK",
        "name TEXT NOT NULL",
        "role TEXT NOT NULL DEFAULT ''",
        "instructions TEXT NOT NULL DEFAULT ''",
        "avatar TEXT NOT NULL DEFAULT ''",
        "group_id INTEGER",
        "model TEXT NOT NULL DEFAULT ''",
        "position INTEGER NOT NULL",
        "created_at_ms INTEGER NOT NULL",
        "tools TEXT",
    ],
    "routines": [
        "id INTEGER PK",
        "member_id INTEGER NOT NULL",
        "title TEXT NOT NULL",
        "prompt TEXT NOT NULL",
        "schedule TEXT NOT NULL",
        "enabled INTEGER NOT NULL DEFAULT 1",
        "last_run_ms INTEGER",
        "next_run_ms INTEGER",
        "created_at_ms INTEGER NOT NULL",
    ],
    "member_unread": ["member_id INTEGER PK"],
}
# Where SQLite keeps the counters of the tables with AUTOINCREMENT.
SEQUENCE = {"sqlite_sequence"}
# Schema v16 adds the owner's safety rules (and whether the first run's were put there), and the
# questions the background tasks wait on.
SCHEMA_V16 = {
    **SCHEMA_V15,
    "safety_rules": [
        "id INTEGER PK",
        "action TEXT NOT NULL",
        "verdict TEXT NOT NULL",
        "position INTEGER NOT NULL",
    ],
    "safety_state": ["key TEXT PK", "value INTEGER NOT NULL"],
    "worker_questions": [
        "id TEXT PK",
        "task_id INTEGER NOT NULL",
        "member_id INTEGER NOT NULL DEFAULT 0",
        "prefix TEXT NOT NULL",
        "text TEXT NOT NULL",
        "tier TEXT",
        "created_at_ms INTEGER NOT NULL",
        "expires_at_ms INTEGER NOT NULL",
        "remaining_s REAL NOT NULL",
        "state TEXT NOT NULL DEFAULT 'waiting'",
        "answered_at_ms INTEGER",
    ],
}
FTS_SHADOWS = {f"agent_turns_fts_{part}" for part in ("data", "idx", "content", "docsize", "config")}
SCHEMA_V17 = {
    **SCHEMA_V16,
    "agent_messages": ["turn_id INTEGER PK NOT NULL", "seq INTEGER PK NOT NULL", "kind TEXT NOT NULL", "text TEXT NOT NULL", "at_ms INTEGER NOT NULL"],
}
SCHEMA_V19 = {**SCHEMA_V17, "members": [*SCHEMA_V17["members"], "personality TEXT NOT NULL DEFAULT ''"]}
SCHEMA_V20 = {**SCHEMA_V19, "dictation_daily": [
    "day TEXT PK NOT NULL", "dictations INTEGER NOT NULL DEFAULT 0", "words INTEGER NOT NULL DEFAULT 0",
    "audio_seconds REAL NOT NULL DEFAULT 0", "processing_ms INTEGER NOT NULL DEFAULT 0",
    "estimated INTEGER NOT NULL DEFAULT 0", "estimated_words INTEGER NOT NULL DEFAULT 0",
    "estimated_dictations INTEGER NOT NULL DEFAULT 0",
]}
SCHEMA_V21 = {**SCHEMA_V20, "agent_turns": [*SCHEMA_V20["agent_turns"], "quote TEXT"]}
SCHEMA_V22 = {
    **SCHEMA_V21,
    "members": [*SCHEMA_V21["members"], "blurb TEXT NOT NULL DEFAULT ''",
                "reply_style TEXT NOT NULL DEFAULT 'full'", "voice TEXT NOT NULL DEFAULT ''",
                *[f"{field} INTEGER NOT NULL DEFAULT 0" for field in
                  ("read_aloud", "notify", "ask_before_members", "pinned", "hidden")]],
    "member_skills": ["id INTEGER PK", "member_id INTEGER NOT NULL", "name TEXT NOT NULL",
                      "description TEXT NOT NULL", "source TEXT NOT NULL", "enabled INTEGER NOT NULL DEFAULT 1"],
    "member_commands": ["id INTEGER PK", "member_id INTEGER NOT NULL", "name TEXT NOT NULL",
                        "description TEXT NOT NULL", "template TEXT NOT NULL"],
}
SCHEMA_V23 = {**SCHEMA_V22,
    "routine_runs": ["id INTEGER PK", "routine_id INTEGER NOT NULL", "started INTEGER NOT NULL", "scheduled INTEGER", "finished INTEGER", "status TEXT NOT NULL", "turn_id INTEGER", "summary TEXT NOT NULL DEFAULT ''", "cost REAL NOT NULL DEFAULT 0"],
    "rooms": ["id INTEGER PK", "name TEXT NOT NULL", "group_id INTEGER", "created INTEGER NOT NULL", "is_team INTEGER NOT NULL DEFAULT 0"],
    "room_members": ["room_id INTEGER PK NOT NULL", "member_id INTEGER PK NOT NULL"],
    "room_messages": ["id INTEGER PK", "room_id INTEGER NOT NULL", "member_id INTEGER", "text TEXT NOT NULL", "created INTEGER NOT NULL", "request_id TEXT", "attachments TEXT", "reply_to INTEGER", "turn_id INTEGER"],
}
SCHEMA_V24 = {
    **SCHEMA_V23,
    "agent_turns": [*SCHEMA_V23["agent_turns"], "conversation_id INTEGER"],
    "conversations": ["id INTEGER PK", "member_id INTEGER NOT NULL", "title TEXT NOT NULL DEFAULT ''", "created_at_ms INTEGER NOT NULL", "updated_at_ms INTEGER NOT NULL", "is_default INTEGER NOT NULL DEFAULT 0"],
    "conversation_context_summaries": ["conversation_id INTEGER PK", "text TEXT NOT NULL", "through_turn_id INTEGER NOT NULL", "updated_at_ms INTEGER NOT NULL"],
    "agent_conversation_cli_sessions": ["conversation_id INTEGER PK NOT NULL", "brain TEXT PK NOT NULL", "session_id TEXT NOT NULL"],
    "subscription_quotas": ["provider TEXT PK NOT NULL", "reset_at_ms INTEGER", "updated_at_ms INTEGER NOT NULL"],
}
SCHEMA_V25 = {**SCHEMA_V24, "run_metrics": [
    "kind TEXT PK NOT NULL", "run_id INTEGER PK NOT NULL", "started_at_ms INTEGER NOT NULL",
    "finished_at_ms INTEGER", "tokens INTEGER NOT NULL DEFAULT 0", "ai_steps INTEGER NOT NULL DEFAULT 0",
    "model_spans TEXT NOT NULL DEFAULT '[]'",
]}
SCHEMA_V26 = {**SCHEMA_V25, "subscription_allowances": [
    "provider TEXT PK NOT NULL", "period TEXT PK NOT NULL", "remaining_percent REAL", "resets_at INTEGER",
    "status TEXT NOT NULL", "updated_at_ms INTEGER NOT NULL",
]}
SCHEMA_V27 = {
    **SCHEMA_V26,
    "rooms": [*SCHEMA_V26["rooms"], "default_responder TEXT NOT NULL DEFAULT 'auto'"],
    "room_messages": [*SCHEMA_V26["room_messages"], "reply_to_member INTEGER", "kind TEXT NOT NULL DEFAULT 'message'", "origin_message_id INTEGER", "exchange_settled INTEGER NOT NULL DEFAULT 0"],
    "room_exchanges": [
        "origin_message_id INTEGER PK", "room_id INTEGER NOT NULL", "state TEXT NOT NULL", "language TEXT NOT NULL",
        "pending TEXT", "active_turn INTEGER", "turns_taken INTEGER NOT NULL DEFAULT 0", "spoken TEXT NOT NULL DEFAULT '[]'",
        "hops INTEGER NOT NULL DEFAULT 0", "chime_ins INTEGER NOT NULL DEFAULT 0", "cost REAL NOT NULL DEFAULT 0",
        "stop_requested INTEGER NOT NULL DEFAULT 0", "wrapup_sent INTEGER NOT NULL DEFAULT 0", "reason TEXT NOT NULL DEFAULT ''",
    ],
}
LATEST = 29


def test_v25_preserves_conversations_and_quotas_and_cleans_deleted_run_metrics(tmp_path):
    path = tmp_path / "v24.db"
    raw = sqlite3.connect(path)
    for version in range(1, 25):
        step = db._MIGRATIONS[version]
        step(raw) if callable(step) else raw.executescript(step)
    raw.execute("INSERT INTO subscription_quotas VALUES ('claude', 9999999999999, 100)")
    raw.execute("INSERT INTO agent_turns (session_id,question,route,prefix,text,status,created_at_ms,conversation_id) SELECT 's','Kept','answer','','ok','done',100,id FROM conversations WHERE member_id=0")
    raw.commit()
    raw.close()
    migrated = db.connect(path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert migrated.execute("SELECT provider FROM subscription_quotas").fetchone()[0] == "claude"
    turn = migrated.execute("SELECT id,conversation_id FROM agent_turns").fetchone()
    assert migrated.execute("SELECT id FROM conversations WHERE id=?", (turn[1],)).fetchone()
    migrated.execute("INSERT INTO run_metrics VALUES('turn',?,100,200,5,1,'[]')", (turn[0],))
    migrated.execute("DELETE FROM agent_turns WHERE id=?", (turn[0],))
    assert migrated.execute("SELECT COUNT(*) FROM run_metrics").fetchone()[0] == 0
    migrated.close()


SCHEMA_V28 = {**SCHEMA_V27, "conversations": [*SCHEMA_V27["conversations"], "kind TEXT NOT NULL DEFAULT 'standard'"]}
SCHEMA_V29 = {
    **SCHEMA_V28,
    "rooms": [*SCHEMA_V28["rooms"], "membership_state TEXT NOT NULL DEFAULT 'ready'", "creation_request_id TEXT"],
    "room_messages": [*SCHEMA_V28["room_messages"], "thread_id INTEGER"],
    "agent_turns": [*SCHEMA_V28["agent_turns"], "scope TEXT NOT NULL DEFAULT 'private'", "room_id INTEGER", "thread_id INTEGER", "source_turn_id INTEGER"],
    "worker_tasks": [*SCHEMA_V28["worker_tasks"], "scope TEXT NOT NULL DEFAULT 'private'", "room_id INTEGER", "thread_id INTEGER"],
    "room_threads": ["id INTEGER PK", "room_id INTEGER NOT NULL", "title TEXT NOT NULL DEFAULT ''", "created INTEGER NOT NULL"],
    "scope_migrations": ["turn_id INTEGER PK", "original TEXT NOT NULL", "disposition TEXT NOT NULL", "reason TEXT NOT NULL"],
    "room_membership_migrations": ["room_id INTEGER PK", "original_members TEXT NOT NULL", "disposition TEXT NOT NULL"],
    "scope_context_archive": ["kind TEXT NOT NULL", "original TEXT NOT NULL"],
}


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
    assert schema_names(conn, "table") == set(SCHEMA_V29) | FTS_SHADOWS | SEQUENCE
    assert {
        "history_created",
        "agent_turns_created",
        "tool_audit_at",
        "worker_tasks_created",
        "worker_steps_task",
        "open_targets_used",
        "agent_steps_turn",
        "agent_turns_member",
        "routines_member",
        "worker_questions_task",
    } <= schema_names(conn, "index")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    for table, columns in SCHEMA_V29.items():
        if table != "agent_turns_fts":
            assert describe_columns(conn, table) == columns
    assert [c["name"] for c in conn.execute("PRAGMA table_info(agent_turns_fts)")] == [
        "pairs",
        "chars",
    ]


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


def v2_database(path, entries):
    """A database as the version before the evidence left it, with these dictionary entries:
    (word, misheard forms, source)."""
    raw = sqlite3.connect(path)
    raw.executescript(db._MIGRATION_V1)
    raw.executescript(db._MIGRATION_V2)
    for number, (word, misheard, source) in enumerate(entries, 1):
        raw.execute(
            "INSERT INTO dictionary (word, misheard, source, created_at_ms) VALUES (?, ?, ?, ?)",
            (word, misheard if isinstance(misheard, str) else json.dumps(misheard), source, number),
        )
    raw.commit()
    raw.close()


def test_the_v3_migration_turns_learned_rules_that_fail_the_test_into_hints(tmp_path):
    path = tmp_path / "old.db"
    v2_database(
        path,
        [
            ("手機", ["首機"], "learned"),  # two CJK characters: too short for a rule
            ("Claude", ["cloud", "to"], "learned"),  # one fit form, one that is not
            ("Typeless", ["type less"], "learned"),
            ("meeting", ["workshop"], "learned"),  # not alike
            ("拉斐爾", ["拉菲爾", "拉非爾"], "manual"),  # typed by the user: left alone
            ("Tailscale", ["tail"], "suggested"),
        ],
    )
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    store = DictionaryStore(conn)
    forms = {entry.word: entry.misheard for entry in store.list()}
    assert forms == {
        "手機": [],
        "Claude": ["cloud"],
        "Typeless": ["type less"],
        "meeting": [],
        "拉斐爾": ["拉菲爾", "拉非爾"],
        "Tailscale": ["tail"],
    }
    assert {pair for pair in store.replacements()} == {
        ("cloud", "Claude"),
        ("type less", "Typeless"),
        ("拉菲爾", "拉斐爾"),
        ("拉非爾", "拉斐爾"),
        ("tail", "Tailscale"),
    }
    # Nothing was deleted: the words stay, and the forms that stopped being rules are hints.
    assert set(store.hint_forms()) - set(store.replacements()) == {
        ("首機", "手機"),
        ("to", "Claude"),
        ("workshop", "meeting"),
    }
    rows = conn.execute("SELECT word_key, form_key, count, status FROM dictionary_evidence")
    assert {tuple(row) for row in rows} == {
        ("手機", "首機", 1, "pending"),
        ("claude", "cloud", 1, "rule"),
        ("claude", "to", 1, "pending"),
        ("typeless", "type less", 1, "rule"),
        ("meeting", "workshop", 1, "pending"),
    }
    conn.close()


def test_the_v3_migration_leaves_a_current_database_alone(tmp_path):
    path = tmp_path / "old.db"
    v2_database(path, [("手機", ["首機"], "learned")])
    first = db.connect(path)
    first.close()
    second = db.connect(path)  # must not run the migration again
    assert [e.misheard for e in DictionaryStore(second).list()] == [[]]
    assert second.execute("SELECT count(*) FROM dictionary_evidence").fetchone()[0] == 1
    second.close()


@pytest.mark.parametrize("misheard", ["not json", '"首機"', "[1, 2]", "null"])
def test_the_v3_migration_skips_a_row_whose_forms_it_cannot_read(tmp_path, misheard):
    path = tmp_path / "old.db"
    v2_database(path, [("手機", misheard, "learned"), ("Claude", ["cloud"], "learned")])
    conn = db.connect(path)
    assert conn.execute("SELECT misheard FROM dictionary WHERE word = '手機'").fetchone()[0] == (
        misheard
    )
    claude = conn.execute("SELECT misheard FROM dictionary WHERE word = 'Claude'").fetchone()[0]
    assert json.loads(claude) == ["cloud"]  # the rows it can read are migrated all the same
    conn.close()


def test_a_failed_v3_migration_leaves_the_database_at_v2():
    raw = sqlite3.connect(":memory:")
    raw.executescript(db._MIGRATION_V1)
    raw.executescript(db._MIGRATION_V2)
    raw.execute("INSERT INTO dictionary (word, misheard, source, created_at_ms)"
                " VALUES ('手機', '[\"首機\"]', 'learned', 1)")
    raw.execute("CREATE TABLE dictionary_evidence (x)")  # in the way of the v3 migration
    raw.commit()
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(raw)
    assert not raw.in_transaction
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
    assert raw.execute("SELECT misheard FROM dictionary").fetchone()[0] == '["首機"]'
    raw.close()


def test_a_v3_database_from_the_task_runner_build_gets_the_evidence_at_v4(tmp_path):
    # An unreleased build numbered its own schema v3 without dictionary_evidence.
    path = tmp_path / "other-v3.db"
    v2_database(path, [("手機", ["首機"], "learned"), ("Claude", ["cloud"], "learned")])
    raw = sqlite3.connect(path)
    raw.executescript(
        "BEGIN; CREATE TABLE assistant_runs (id TEXT PRIMARY KEY);"
        " CREATE TABLE budget_requests (id TEXT PRIMARY KEY); PRAGMA user_version = 3; COMMIT;"
    )
    raw.close()

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    store = DictionaryStore(conn)
    assert {entry.word: entry.misheard for entry in store.list()} == {"手機": [], "Claude": ["cloud"]}
    rows = conn.execute("SELECT word_key, form_key, status FROM dictionary_evidence")
    assert {tuple(row) for row in rows} == {("手機", "首機", "pending"), ("claude", "cloud", "rule")}
    # The other build's tables are left as they were.
    assert {"assistant_runs", "budget_requests"} <= schema_names(conn, "table")
    conn.close()


def test_the_v4_migration_leaves_a_v3_evidence_table_alone(tmp_path):
    path = tmp_path / "ours-v3.db"
    v2_database(path, [("手機", ["首機"], "learned")])
    raw = sqlite3.connect(path)
    db._migrate_v3(raw)
    raw.execute("UPDATE dictionary_evidence SET count = 2")  # evidence gathered since v3
    raw.commit()
    raw.close()

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert [tuple(row) for row in conn.execute("SELECT count FROM dictionary_evidence")] == [(2,)]
    conn.close()


def v4_database(path):
    """A database as the version before the voice assistant left it, with a dictation and a word."""
    raw = sqlite3.connect(path)
    raw.executescript(db._MIGRATION_V1)
    raw.executescript(db._MIGRATION_V2)
    db._migrate_v3(raw)
    db._migrate_v4(raw)
    raw.execute(
        "INSERT INTO history (created_at_ms, kind, raw_text, text, cleaned, audio_seconds,"
        " stt_ms, llm_ms) VALUES (5, 'dictation', 'a', 'A', 1, 1.0, 10, 20)"
    )
    raw.execute("INSERT INTO dictionary (word, created_at_ms) VALUES ('拉斐爾', 1)")
    raw.commit()
    raw.close()


def test_a_v4_database_gets_the_turns_table_at_v5_and_keeps_its_rows(tmp_path):
    path = tmp_path / "v4.db"
    v4_database(path)

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert describe_columns(conn, "agent_turns") == SCHEMA_V29["agent_turns"]
    assert conn.execute("SELECT count(*) FROM agent_turns").fetchone()[0] == 0
    assert HistoryStore(conn).latest().text == "A"
    assert [e.word for e in DictionaryStore(conn).list()] == ["拉斐爾"]
    conn.close()


def v5_database(path):
    """A database as the version before the iCal calendars left it, with a dictation, a word and a turn."""
    v4_database(path)
    raw = sqlite3.connect(path)
    raw.executescript(db._MIGRATION_V5)
    raw.execute(
        "INSERT INTO agent_turns (created_at_ms, session_id, question, route, prefix, text, status)"
        " VALUES (1, 's', 'q', 'chat', '答', 'a', 'done')"
    )
    raw.commit()
    raw.close()


def assert_has_the_latest_schema(migrated, fresh):
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert schema_names(migrated, "table") == schema_names(fresh, "table")
    assert schema_names(migrated, "index") == schema_names(fresh, "index")
    for table in SCHEMA_V16:
        assert describe_columns(migrated, table) == describe_columns(fresh, table)
    assert migrated.execute("SELECT question FROM agent_turns").fetchone()[0] == "q"
    assert HistoryStore(migrated).latest().text == "A"


def test_a_v5_database_ends_with_the_same_schema_as_a_new_one_and_keeps_its_rows(tmp_path, conn):
    path = tmp_path / "v5.db"
    v5_database(path)

    migrated = db.connect(path)
    assert_has_the_latest_schema(migrated, conn)
    migrated.close()


def test_a_v6_database_with_the_removed_calendars_table_loses_it_and_keeps_its_rows(tmp_path, conn):
    path = tmp_path / "v6.db"
    v5_database(path)
    raw = sqlite3.connect(path)
    raw.executescript(  # what the iCal feature's v6 made, before the feature was removed
        "BEGIN; CREATE TABLE calendars (id INTEGER PRIMARY KEY, label TEXT NOT NULL,"
        " created_at_ms INTEGER NOT NULL); PRAGMA user_version = 6; COMMIT;"
    )
    raw.execute("INSERT INTO calendars (label, created_at_ms) VALUES ('Personal', 1)")
    raw.commit()
    raw.close()

    migrated = db.connect(path)
    assert_has_the_latest_schema(migrated, conn)
    migrated.close()


def v7_database(path):
    """A database as the version before the plugins' audit left it: at v7, with a dictation, a word and
    a turn."""
    v5_database(path)
    raw = sqlite3.connect(path)
    raw.executescript(db._MIGRATION_V6)
    raw.executescript(db._MIGRATION_V7)
    raw.close()


def test_a_v7_database_gets_the_audit_table_at_v8_and_keeps_its_rows(tmp_path, conn):
    path = tmp_path / "v7.db"
    v7_database(path)

    migrated = db.connect(path)
    assert_has_the_latest_schema(migrated, conn)
    assert describe_columns(migrated, "tool_audit") == SCHEMA_V10["tool_audit"]
    assert migrated.execute("SELECT count(*) FROM tool_audit").fetchone()[0] == 0
    migrated.close()


def v8_database(path):
    """A database as the version before the background worker left it: at v8, with the audit of a
    plugin's tool call in it."""
    v7_database(path)
    raw = sqlite3.connect(path)
    raw.executescript(db._MIGRATION_V8)
    raw.execute(
        "INSERT INTO tool_audit (at_ms, server, tool, tier, approver, outcome)"
        " VALUES (7, 'Notes', 'look', 'T0', 'auto', 'ok')"
    )
    raw.commit()
    raw.close()


def test_a_v8_database_gets_the_worker_tables_at_v9_and_keeps_its_audit(tmp_path, conn):
    path = tmp_path / "v8.db"
    v8_database(path)

    migrated = db.connect(path)
    assert_has_the_latest_schema(migrated, conn)
    kept = migrated.execute("SELECT server, tool, outcome, task_id FROM tool_audit").fetchall()
    assert [tuple(row) for row in kept] == [("Notes", "look", "ok", None)]  # the fast tier's
    assert migrated.execute("SELECT count(*) FROM worker_tasks").fetchone()[0] == 0
    assert migrated.execute("SELECT count(*) FROM worker_steps").fetchone()[0] == 0
    migrated.close()


def test_a_failed_v9_migration_leaves_the_database_at_v8(tmp_path):
    path = tmp_path / "v8.db"
    v8_database(path)
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE worker_tasks (x)")  # in the way of the v9 migration
    raw.commit()
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(raw)
    assert not raw.in_transaction
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 8
    assert "task_id" not in [row[1] for row in raw.execute("PRAGMA table_info(tool_audit)")]
    raw.close()


def v9_database(path):
    """A database as the version before opening anything left it: at v9, with a task of the
    worker in it."""
    v8_database(path)
    raw = sqlite3.connect(path)
    raw.executescript(db._MIGRATION_V9)
    raw.execute(
        "INSERT INTO worker_tasks (created_at_ms, state, session_id, language, text)"
        " VALUES (7, 'done', 's', 'en', 'look it up')"
    )
    raw.commit()
    raw.close()


def test_a_v9_database_gets_the_learned_names_at_v10_and_keeps_its_tasks(tmp_path, conn):
    path = tmp_path / "v9.db"
    v9_database(path)

    migrated = db.connect(path)
    assert_has_the_latest_schema(migrated, conn)
    assert migrated.execute("SELECT count(*) FROM open_targets").fetchone()[0] == 0
    assert migrated.execute("SELECT text FROM worker_tasks").fetchone()[0] == "look it up"
    migrated.close()


def test_a_failed_v10_migration_leaves_the_database_at_v9(tmp_path):
    path = tmp_path / "v9.db"
    v9_database(path)
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE open_targets (x)")  # in the way of the v10 migration
    raw.commit()
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(raw)
    assert not raw.in_transaction
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 9
    raw.close()


def v10_database(path):
    """A database as the version before the conversation view left it: at v10, with a step of the
    worker in it."""
    v9_database(path)
    raw = sqlite3.connect(path)
    raw.executescript(db._MIGRATION_V10)
    raw.execute(
        "INSERT INTO worker_steps (task_id, n, at_ms, kind, outcome) VALUES (1, 1, 8, 'tool', 'ok')"
    )
    raw.commit()
    raw.close()


def test_a_v10_database_gets_the_steps_at_v11_and_its_turns_are_voice(tmp_path, conn):
    path = tmp_path / "v10.db"
    v10_database(path)

    migrated = db.connect(path)
    assert_has_the_latest_schema(migrated, conn)
    turn = migrated.execute("SELECT source, model FROM agent_turns").fetchone()
    assert (turn["source"], turn["model"]) == ("voice", None)  # every turn so far was spoken
    assert migrated.execute("SELECT reason FROM worker_steps").fetchone()[0] is None
    assert migrated.execute("SELECT count(*) FROM agent_steps").fetchone()[0] == 0
    migrated.close()


def test_a_failed_v11_migration_leaves_the_database_at_v10(tmp_path):
    path = tmp_path / "v10.db"
    v10_database(path)
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE agent_steps (x)")  # in the way of the v11 migration
    raw.commit()
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(raw)
    assert not raw.in_transaction
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 10
    assert "source" not in [c[1] for c in raw.execute("PRAGMA table_info(agent_turns)")]
    raw.close()


def test_a_failed_v8_migration_leaves_the_database_at_v7(tmp_path):
    path = tmp_path / "v7.db"
    v7_database(path)
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE tool_audit (x)")  # in the way of the v8 migration
    raw.commit()
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(raw)
    assert not raw.in_transaction
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 7
    assert "tool_audit_at" not in schema_names(raw, "index")
    raw.close()


def test_a_database_that_is_at_the_latest_version_is_left_alone(tmp_path):
    path = tmp_path / "latest.db"
    first = db.connect(path)
    first.execute(
        "INSERT INTO agent_turns (created_at_ms, session_id, question, route, prefix, text, status)"
        " VALUES (1, 's', 'q', 'chat', '答', 'a', 'done')"
    )
    first.commit()
    first.close()

    second = db.connect(path)  # must not try to create the table again
    assert second.execute("SELECT question, cost_usd FROM agent_turns").fetchone()[:] == ("q", 0)
    second.close()


def test_a_failed_v5_migration_leaves_the_database_at_v4(tmp_path):
    path = tmp_path / "v4.db"
    v4_database(path)
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE agent_turns (x)")  # in the way of the v5 migration
    raw.commit()
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(raw)
    assert not raw.in_transaction
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 4
    assert "agent_turns_created" not in schema_names(raw, "index")
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
    SettingsStore(first).set("languages", ["ja-JP"])
    first.close()

    second = db.connect(str(path))  # must not try to create the tables again
    assert SettingsStore(second).get("languages") == ["ja-JP"]
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
    dictionary.delete(dictionary.add("Tailscale", source="learned").id)  # dismissed with it
    assert DictionaryStore(reader).dismissed() == {"Tailscale"}

    seen = DictionaryStore(reader)
    # A hint, with its evidence.
    assert dictionary.learn_pair("Claude", "cloud") is Learned.TERM
    assert ("cloud", "Claude") in seen.hint_forms()
    assert ("cloud", "Claude") not in seen.replacements()
    # The second sighting: a rule.
    assert dictionary.learn_pair("Claude", "cloud") is Learned.RULE
    assert ("cloud", "Claude") in seen.replacements()
    # The user changed it back: the rule is withdrawn.
    assert dictionary.learn_pair("cloud", "Claude") is Learned.REVERTED
    assert ("cloud", "Claude") not in seen.hint_forms()
    # And the pair is blocked; the word is not.
    assert dictionary.learn_pair("Claude", "cloud") is Learned.NOTHING
    assert "Claude" in seen.hint_words()

    settings.set("languages", ["ja-JP"])
    assert SettingsStore(reader).get("languages") == ["ja-JP"]

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


def v14_database(path):
    """A database as the version before the members left it: at v14, with a turn, its summary and a
    task."""
    raw = sqlite3.connect(path)
    for version in range(1, 15):
        step = db._MIGRATIONS[version]
        if callable(step):
            step(raw)
        else:
            raw.executescript(step)
    raw.execute(
        "INSERT INTO agent_turns (created_at_ms, session_id, question, route, prefix, text, status)"
        " VALUES (1, 's', 'q', 'chat', '答', 'a', 'done')"
    )
    raw.execute(
        "INSERT INTO agent_summary (id, text, through_turn_id, updated_at_ms)"
        " VALUES (1, 'the summary', 1, 9)"
    )
    raw.execute(
        "INSERT INTO worker_tasks (created_at_ms, state, session_id, language, text)"
        " VALUES (2, 'done', 's', 'en', 't')"
    )
    raw.commit()
    raw.close()


def test_a_v14_database_gets_the_members_and_the_rules_and_everything_old_is_raphaels(tmp_path, conn):
    path = tmp_path / "v14.db"
    v14_database(path)

    migrated = db.connect(path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert schema_names(migrated, "table") == schema_names(conn, "table")
    assert schema_names(migrated, "index") == schema_names(conn, "index")
    assert tuple(migrated.execute("SELECT member_id, peer_id FROM agent_turns").fetchone()) == (0, None)
    assert tuple(migrated.execute("SELECT member_id, routine_id FROM worker_tasks").fetchone()) == (
        0,
        None,
    )
    assert tuple(migrated.execute("SELECT * FROM conversation_summaries").fetchone()) == (
        0,
        "the summary",
        1,
        9,
    )
    migrated.close()


def test_a_failed_v15_migration_leaves_the_database_at_v14(tmp_path, monkeypatch):
    path = tmp_path / "v14.db"
    v14_database(path)
    monkeypatch.setattr(db, "_MEMBERS_TABLES", (*db._MEMBERS_TABLES, "NOT SQL"))
    with pytest.raises(sqlite3.Error):
        db.connect(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 14
    assert raw.execute("SELECT text FROM agent_summary").fetchone()[0] == "the summary"
    assert "members" not in schema_names(raw, "table")
    raw.close()


# What the safety rules' branch made as its own v15, before the members took that number and the
# rules moved to v16. A database the branch's build opened is at 15 with these tables and none of
# the members'.
SAFETY_BRANCH_V15 = """
BEGIN;
CREATE TABLE safety_rules (id INTEGER PRIMARY KEY, action TEXT NOT NULL, verdict TEXT NOT NULL,
  position INTEGER NOT NULL);
CREATE TABLE safety_state (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE worker_questions (id TEXT PRIMARY KEY, task_id INTEGER NOT NULL,
  member_id INTEGER NOT NULL DEFAULT 0, prefix TEXT NOT NULL, text TEXT NOT NULL, tier TEXT,
  created_at_ms INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL, remaining_s REAL NOT NULL,
  state TEXT NOT NULL DEFAULT 'waiting', answered_at_ms INTEGER);
CREATE INDEX worker_questions_task ON worker_questions(task_id);
PRAGMA user_version = 15;
COMMIT;
"""


def assert_same_schema(migrated, fresh):
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert schema_names(migrated, "table") == schema_names(fresh, "table")
    assert schema_names(migrated, "index") == schema_names(fresh, "index")
    for table in SCHEMA_V16:
        assert describe_columns(migrated, table) == describe_columns(fresh, table)


def test_a_database_at_the_members_v15_gets_only_the_rules(tmp_path, conn):
    path = tmp_path / "v15.db"
    v14_database(path)
    raw = sqlite3.connect(path)
    db._migrate_v15(raw)
    raw.execute("INSERT INTO members (name, position, created_at_ms) VALUES ('Ann', 0, 1)")
    raw.commit()
    raw.close()

    migrated = db.connect(path)
    assert_same_schema(migrated, conn)
    assert migrated.execute("SELECT name FROM members").fetchone()[0] == "Ann"
    assert migrated.execute("SELECT text FROM conversation_summaries").fetchone()[0] == "the summary"
    migrated.close()


def test_a_database_at_the_safety_branchs_v15_gets_the_members_and_keeps_its_rules(tmp_path, conn):
    path = tmp_path / "v15-safety.db"
    v14_database(path)
    raw = sqlite3.connect(path)
    raw.executescript(SAFETY_BRANCH_V15)
    raw.execute("INSERT INTO safety_rules (action, verdict, position) VALUES ('pay', 'ask', 0)")
    raw.execute(
        "INSERT INTO worker_questions (id, task_id, member_id, prefix, text, created_at_ms,"
        " expires_at_ms, remaining_s) VALUES ('q1', 1, 0, '問', 'line', 1, 2, 3.0)"
    )
    raw.commit()
    raw.close()

    migrated = db.connect(path)
    assert_same_schema(migrated, conn)
    assert migrated.execute("SELECT action FROM safety_rules").fetchone()[0] == "pay"
    assert migrated.execute("SELECT state FROM worker_questions").fetchone()[0] == "waiting"
    assert tuple(migrated.execute("SELECT * FROM conversation_summaries").fetchone()) == (
        0,
        "the summary",
        1,
        9,
    )
    assert tuple(migrated.execute("SELECT member_id, peer_id FROM agent_turns").fetchone()) == (0, None)
    migrated.close()


def test_the_v15_and_v16_migrations_can_run_again_on_a_current_database(conn):
    db._migrate_v15(conn)
    db._migrate_v16(conn)
    db._migrate_v16(conn)
    # Historical migrations set their own version; compare their schema additions at today's version.
    conn.execute(f"PRAGMA user_version = {LATEST}")
    fresh = db.connect(":memory:")
    assert_same_schema(conn, fresh)
    fresh.close()


def test_v18_preserves_live_messages_and_their_order_and_cascade(tmp_path):
    path = tmp_path / "v17.db"
    raw = sqlite3.connect(path)
    for version in range(1, 18):
        step = db._MIGRATIONS[version]
        step(raw) if callable(step) else raw.executescript(step)
    raw.execute("INSERT INTO agent_turns (session_id, question, route, prefix, text, status, created_at_ms) VALUES ('s', 'q', 'answer', '', '', 'done', 1)")
    raw.execute("INSERT INTO agent_messages VALUES (1, 0, 'progress', 'Checking', 1)")
    raw.execute("INSERT INTO agent_messages VALUES (1, 1, 'reply', 'Done', 2)")
    raw.commit()
    raw.close()
    migrated = db.connect(path)
    assert [tuple(row) for row in migrated.execute("SELECT seq, kind, text FROM agent_messages ORDER BY seq")] == [(0, "progress", "Checking"), (1, "reply", "Done")]
    migrated.execute("INSERT INTO agent_messages VALUES (1, 2, 'thinking', 'A public decision', 3)")
    migrated.execute("INSERT INTO agent_messages VALUES (1, 3, 'media', '{}', 4)")
    migrated.execute("PRAGMA foreign_keys = ON")
    migrated.commit()
    migrated.execute("PRAGMA foreign_keys = ON")
    migrated.execute("DELETE FROM agent_turns WHERE id = 1")
    assert migrated.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0] == 0
    migrated.close()


def test_v21_quotes_preserve_old_turns_and_survive_reopening(tmp_path):
    from raphael_core.agent.history import AgentHistory

    path = tmp_path / "v20.db"
    raw = sqlite3.connect(path)
    for version in range(1, 21):
        step = db._MIGRATIONS[version]
        step(raw) if callable(step) else raw.executescript(step)
    raw.execute("INSERT INTO agent_turns (session_id, question, route, prefix, text, status, created_at_ms) VALUES ('old', 'q', 'chat', '', 'a', 'done', 1)")
    raw.commit(); raw.close()
    migrated = db.connect(path)
    history = AgentHistory(migrated)
    assert history.get(1).quote is None
    quote = {"text": "Selected passage", "author": "Teacher"}
    new_id = history.add(session_id="new", question="Explain", route="chat", prefix="", text="Answer", status="done", quote=quote)
    migrated.close()
    reopened = db.connect(path)
    assert AgentHistory(reopened).get(new_id).quote == quote
    assert AgentHistory(reopened).get(1).question == "q"
    reopened.close()


def test_v23_automation_and_rooms_survive_reopen_and_keep_existing_routines(tmp_path):
    path = tmp_path / "automations.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for version in range(1, 23):
        step = db._MIGRATIONS[version]
        step(conn) if callable(step) else conn.executescript(step)
    conn.execute("INSERT INTO routines (member_id,title,prompt,schedule,enabled,created_at_ms) VALUES (0,'Report','Check','{\"kind\":\"daily\",\"time\":\"09:00\"}',1,1)")
    conn.commit()
    db.migrate(conn)
    conn.execute("INSERT INTO routine_runs (routine_id, started, status) VALUES (1, 1, 'running')")
    conn.execute("INSERT INTO rooms (name,created) VALUES ('Team',1)")
    conn.execute("INSERT INTO room_members VALUES (1,0)")
    conn.execute("INSERT INTO room_messages (room_id,text,created,request_id) VALUES (1,'Hello',1,'request')")
    conn.commit(); conn.close()
    reopened = db.connect(path)
    assert reopened.execute("SELECT title FROM routines").fetchone()[0] == "Report"
    assert reopened.execute("SELECT status FROM routine_runs").fetchone()[0] == "running"
    assert reopened.execute("SELECT text FROM room_messages").fetchone()[0] == "Hello"
    with pytest.raises(sqlite3.IntegrityError):
        reopened.execute("INSERT INTO room_messages (room_id,text,created,request_id) VALUES (1,'Again',1,'request')")
    reopened.close()


def test_v22_preserves_old_members_and_adds_private_preferences(tmp_path):
    path = tmp_path / "v21.db"
    raw = sqlite3.connect(path)
    for version in range(1, 22):
        step = db._MIGRATIONS[version]
        step(raw) if callable(step) else raw.executescript(step)
    raw.execute("INSERT INTO members (name, position, created_at_ms, instructions) VALUES ('Ada', 0, 1, 'Existing instructions')")
    raw.commit()
    raw.close()
    migrated = db.connect(path)
    row = migrated.execute("SELECT * FROM members").fetchone()
    assert row["instructions"] == "Existing instructions"
    assert row["reply_style"] == "full" and row["blurb"] == ""
    assert all(row[field] == 0 for field in ("read_aloud", "notify", "ask_before_members", "pinned", "hidden"))
    migrated.close()
    reopened = db.connect(path)
    assert reopened.execute("SELECT COUNT(*) FROM member_commands").fetchone()[0] == 0
    assert reopened.execute("SELECT COUNT(*) FROM member_skills").fetchone()[0] == 0
    reopened.close()


def test_v24_assigns_each_legacy_member_a_stable_default_conversation(tmp_path, conn):
    from raphael_core.agent.conversations import Conversations
    from raphael_core.agent.history import AgentHistory

    assert Conversations(conn).get(Conversations(conn).main(0))["created_at_ms"] > 0

    path = tmp_path / "v23.db"
    raw = sqlite3.connect(path)
    for version in range(1, 24):
        step = db._MIGRATIONS[version]
        step(raw) if callable(step) else raw.executescript(step)
    for member, question, stamp in [(0, "First\nquestion", 100), (7, "Member question", 200), (0, "Later", 300)]:
        raw.execute("INSERT INTO agent_turns (session_id, member_id, question, route, prefix, text, status, created_at_ms) VALUES (?, ?, ?, 'chat', '', 'answer', 'done', ?)", (str(stamp), member, question, stamp))
    raw.commit(); raw.close()
    migrated = db.connect(path)
    conversations = Conversations(migrated)
    main, member = conversations.main(0), conversations.main(7)
    assert main != member
    assert conversations.get(main)["title"] == "First question"
    assert [AgentHistory(migrated).get(id).conversation_id for id in (1, 2, 3)] == [main, member, main]
    assert (conversations.get(main)["created_at_ms"], conversations.get(main)["updated_at_ms"]) == (100, 300)
    migrated.close()
    reopened = db.connect(path)
    assert Conversations(reopened).main(0) == main
    assert len(Conversations(reopened).list(7)) == 1
    reopened.close()
