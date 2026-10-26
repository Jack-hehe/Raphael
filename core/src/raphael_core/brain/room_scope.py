"""Lossless migration of legacy room/exchange audit rows out of private history."""
import json
import sqlite3


def _script(conn, sql):
    statement = ''
    for line in sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ''


def migrate(conn):
    original_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        _migrate(conn)
    finally:
        conn.row_factory = original_factory


def _migrate(conn):
    _script(conn, """
BEGIN;
ALTER TABLE rooms ADD COLUMN membership_state TEXT NOT NULL DEFAULT 'ready';
ALTER TABLE rooms ADD COLUMN creation_request_id TEXT;
CREATE UNIQUE INDEX room_creation_request ON rooms(creation_request_id) WHERE creation_request_id IS NOT NULL;
CREATE TABLE room_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    created INTEGER NOT NULL,
    UNIQUE(id, room_id)
);
INSERT INTO room_threads(room_id, created) SELECT id, created FROM rooms;
ALTER TABLE room_messages ADD COLUMN thread_id INTEGER REFERENCES room_threads(id);
UPDATE room_messages SET thread_id = (SELECT id FROM room_threads WHERE room_id = room_messages.room_id);
ALTER TABLE agent_turns ADD COLUMN scope TEXT NOT NULL DEFAULT 'private'
    CHECK(scope IN ('private','room','exchange','quarantine'));
ALTER TABLE agent_turns ADD COLUMN room_id INTEGER REFERENCES rooms(id);
ALTER TABLE agent_turns ADD COLUMN thread_id INTEGER REFERENCES room_threads(id);
ALTER TABLE agent_turns ADD COLUMN source_turn_id INTEGER REFERENCES agent_turns(id);
ALTER TABLE worker_tasks ADD COLUMN scope TEXT NOT NULL DEFAULT 'private';
ALTER TABLE worker_tasks ADD COLUMN room_id INTEGER;
ALTER TABLE worker_tasks ADD COLUMN thread_id INTEGER;
CREATE INDEX agent_turns_scope ON agent_turns(scope, member_id, conversation_id, id);
CREATE TABLE scope_migrations (
    turn_id INTEGER PRIMARY KEY,
    original TEXT NOT NULL,
    disposition TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE room_membership_migrations (
    room_id INTEGER PRIMARY KEY, original_members TEXT NOT NULL, disposition TEXT NOT NULL
);
""")
    affected = set()
    for row in conn.execute("SELECT * FROM agent_turns").fetchall():
        links = conn.execute("SELECT room_id, thread_id, member_id FROM room_messages WHERE turn_id = ?", (row['id'],)).fetchall()
        exchange = row['route'] == 'exchange' or row['source'] == 'member'
        if not links and not exchange and row['source'] != 'room' and row['route'] != 'room':
            continue
        room, thread, source = None, None, None
        if len(links) == 1 and links[0]['member_id'] == row['member_id']:
            scope, reason = 'room', 'room_message_link'
            room, thread = links[0]['room_id'], links[0]['thread_id']
        elif links:
            scope, reason = 'quarantine', 'conflicting_room_links'
        elif exchange:
            # Exact session provenance only. Never infer scope from the question's words.
            roots = conn.execute("SELECT id, room_id, thread_id, member_id, route, source FROM agent_turns WHERE session_id = ? "
                                 "AND route != 'exchange' AND source != 'member'", (row['session_id'],)).fetchall()
            if len(roots) == 1:
                source = roots[0]['id']
                source_links = conn.execute("SELECT room_id, thread_id, member_id FROM room_messages WHERE turn_id = ?", (source,)).fetchall()
                if len(source_links) == 1 and source_links[0]['member_id'] == roots[0]['member_id']:
                    scope, reason = 'room', 'exchange_room_source'
                    room, thread = source_links[0]['room_id'], source_links[0]['thread_id']
                elif not source_links and roots[0]['room_id'] is None and roots[0]['route'] != 'room' and roots[0]['source'] != 'room':
                    scope, reason = 'exchange', 'exchange_source'
                else:
                    scope, reason = 'quarantine', 'ambiguous_exchange_source'
            else:
                scope, reason = 'quarantine', 'missing_or_ambiguous_exchange_source'
        else:
            scope, reason = 'quarantine', 'missing_room_link'
        conn.execute("INSERT INTO scope_migrations VALUES (?, ?, ?, ?)",
                     (row['id'], json.dumps(dict(row), ensure_ascii=False), scope, reason))
        conn.execute("UPDATE agent_turns SET scope=?, room_id=?, thread_id=?, source_turn_id=?, conversation_id=NULL WHERE id=?",
                     (scope, room, thread, source, row['id']))
        affected.add(row['member_id'])
    # Summaries and CLI sessions may contain the old mixed transcript. Preserve them in audit,
    # invalidate the derived context, and rebuild private previews from private turns only.
    conn.execute("CREATE TABLE scope_context_archive (kind TEXT NOT NULL, original TEXT NOT NULL)")
    for task in conn.execute("SELECT * FROM worker_tasks WHERE turn_id IN (SELECT turn_id FROM scope_migrations)").fetchall():
        conn.execute("INSERT INTO scope_context_archive VALUES ('worker_tasks',?)", (json.dumps(dict(task)),))
        conn.execute("UPDATE worker_tasks SET scope=(SELECT scope FROM agent_turns WHERE id=turn_id), "
                     "room_id=(SELECT room_id FROM agent_turns WHERE id=turn_id), thread_id=(SELECT thread_id FROM agent_turns WHERE id=turn_id) WHERE id=?", (task['id'],))
    # Legacy room workers whose source was deleted cannot be safely reattached. Keep their
    # reports for audit, but do not let a missing source turn make them private again.
    for task in conn.execute("SELECT * FROM worker_tasks WHERE scope='private' AND session_id GLOB 'room:*' "
                             "AND NOT EXISTS(SELECT 1 FROM agent_turns WHERE id=worker_tasks.turn_id)").fetchall():
        conn.execute("INSERT INTO scope_context_archive VALUES ('worker_tasks',?)", (json.dumps(dict(task)),))
        conn.execute("UPDATE worker_tasks SET scope='quarantine' WHERE id=?", (task['id'],))
    for table in ('conversation_context_summaries', 'agent_conversation_cli_sessions',
                  'agent_cli_sessions', 'conversation_summaries', 'agent_summary'):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            marks = ','.join('?' for _ in affected)
            condition = (f"member_id IN ({marks})" if 'member_id' in columns else
                         f"conversation_id IN (SELECT id FROM conversations WHERE member_id IN ({marks}))" if 'conversation_id' in columns else
                         "1" if 0 in affected else "0")
            params = list(affected) if 'member_id' in columns or 'conversation_id' in columns else []
            for row in conn.execute(f"SELECT * FROM {table} WHERE {condition}", params).fetchall():
                conn.execute("INSERT INTO scope_context_archive VALUES (?, ?)", (table, json.dumps(dict(row))))
            conn.execute(f"DELETE FROM {table} WHERE {condition}", params)
    for member in affected:
        conn.execute("DELETE FROM member_unread WHERE member_id=?", (member,))
        for conversation in conn.execute("SELECT id FROM conversations WHERE member_id=?", (member,)).fetchall():
            original = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation['id'],)).fetchone()
            conn.execute("INSERT INTO scope_context_archive VALUES ('conversations',?)", (json.dumps(dict(original)),))
            private = conn.execute("SELECT question, created_at_ms FROM agent_turns WHERE conversation_id=? AND scope='private' ORDER BY id",
                                   (conversation['id'],)).fetchall()
            title = original['title']
            contaminated_titles = {' '.join(json.loads(r[0])['question'].split())[:80]
                                   for r in conn.execute("SELECT original FROM scope_migrations")
                                   if json.loads(r[0])['conversation_id'] == conversation['id']}
            if title in contaminated_titles or not private:
                title = ' '.join(private[0]['question'].split())[:80] if private else ''
            conn.execute("UPDATE conversations SET title=?, updated_at_ms=COALESCE(?,created_at_ms) WHERE id=?",
                         (title, private[-1]['created_at_ms'] if private else None, conversation['id']))
    for room in conn.execute("SELECT id FROM rooms WHERE is_team=0").fetchall():
        ids = [r[0] for r in conn.execute("SELECT member_id FROM room_members WHERE room_id=? ORDER BY member_id", (room['id'],))]
        state = 'ready' if 0 in ids and 2 <= len(ids) <= 6 else 'needs_adjustment'
        if 0 not in ids and 1 <= len(ids) <= 5:
            conn.execute("INSERT INTO room_members VALUES (?,0)", (room['id'],))
            state = 'ready'
        conn.execute("INSERT INTO room_membership_migrations VALUES (?, ?, ?)", (room['id'], json.dumps(ids), state))
        conn.execute("UPDATE rooms SET membership_state=? WHERE id=?", (state, room['id']))
    # Recompute reply identity from the actual source, never the selected recipient.
    for original in conn.execute("SELECT * FROM room_messages").fetchall():
        conn.execute("INSERT INTO scope_context_archive VALUES ('room_messages',?)", (json.dumps(dict(original)),))
    conn.execute("UPDATE room_messages SET reply_to_member=(SELECT member_id FROM room_messages s WHERE s.id=room_messages.reply_to)")
    exchanges = conn.execute("SELECT * FROM room_exchanges").fetchall()
    # Reply IDs are durable provenance, not ownership cascades: deleting a source must keep
    # its replies, whose source is then explicitly unavailable.
    _script(conn, """
CREATE TABLE room_messages_scoped (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    member_id INTEGER, text TEXT NOT NULL, created INTEGER NOT NULL,
    request_id TEXT, attachments TEXT, reply_to INTEGER,
    turn_id INTEGER REFERENCES agent_turns(id) ON DELETE SET NULL,
    reply_to_member INTEGER,
    kind TEXT NOT NULL DEFAULT 'message' CHECK(kind IN ('message','wrapup')),
    origin_message_id INTEGER, exchange_settled INTEGER NOT NULL DEFAULT 0,
    thread_id INTEGER REFERENCES room_threads(id),
    UNIQUE(room_id,request_id), UNIQUE(reply_to,member_id)
);
INSERT INTO room_messages_scoped SELECT * FROM room_messages;
DROP TABLE room_messages;
ALTER TABLE room_messages_scoped RENAME TO room_messages;
CREATE INDEX room_messages_room ON room_messages(room_id,id);
""")
    if exchanges:
        conn.executemany("INSERT OR IGNORE INTO room_exchanges VALUES (" + ','.join('?' for _ in exchanges[0]) + ")", exchanges)
    _script(conn, """
CREATE TRIGGER room_default_thread AFTER INSERT ON rooms BEGIN
    INSERT INTO room_threads(room_id,created) VALUES (NEW.id,NEW.created);
END;
CREATE TRIGGER room_message_thread AFTER INSERT ON room_messages WHEN NEW.thread_id IS NULL BEGIN
    UPDATE room_messages SET thread_id=(SELECT id FROM room_threads WHERE room_id=NEW.room_id ORDER BY id LIMIT 1) WHERE id=NEW.id;
END;
CREATE TRIGGER room_message_scope_insert BEFORE INSERT ON room_messages
WHEN NEW.thread_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM room_threads WHERE id=NEW.thread_id AND room_id=NEW.room_id)
BEGIN SELECT RAISE(ABORT,'cross-scope room thread'); END;
CREATE TRIGGER room_reply_insert BEFORE INSERT ON room_messages WHEN NEW.reply_to IS NOT NULL
AND NOT EXISTS(SELECT 1 FROM room_messages s WHERE s.id=NEW.reply_to AND s.room_id=NEW.room_id
    AND (NEW.thread_id IS NULL OR s.thread_id=NEW.thread_id) AND s.member_id IS NOT NEW.member_id)
BEGIN SELECT RAISE(ABORT,'invalid room reply'); END;
CREATE TRIGGER room_message_scope_update BEFORE UPDATE OF room_id, thread_id, reply_to, member_id ON room_messages
WHEN NOT EXISTS(SELECT 1 FROM room_threads WHERE id=NEW.thread_id AND room_id=NEW.room_id)
OR (NEW.reply_to IS NOT NULL AND NOT EXISTS(SELECT 1 FROM room_messages s WHERE s.id=NEW.reply_to
    AND s.room_id=NEW.room_id AND s.thread_id=NEW.thread_id AND s.member_id IS NOT NEW.member_id))
BEGIN SELECT RAISE(ABORT,'invalid room message scope or reply'); END;
CREATE TRIGGER scoped_turn_insert BEFORE INSERT ON agent_turns
WHEN (NEW.scope='room' AND (NEW.conversation_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM room_threads WHERE id=NEW.thread_id AND room_id=NEW.room_id)))
OR (NEW.scope!='private' AND NEW.conversation_id IS NOT NULL)
OR (NEW.scope='private' AND (NEW.room_id IS NOT NULL OR NEW.thread_id IS NOT NULL OR NEW.source IN ('room','member') OR NEW.route IN ('room','exchange')))
BEGIN SELECT RAISE(ABORT,'invalid turn scope'); END;
CREATE TRIGGER room_chief_required BEFORE DELETE ON room_members WHEN OLD.member_id=0
AND EXISTS(SELECT 1 FROM rooms WHERE id=OLD.room_id AND is_team=0)
BEGIN SELECT RAISE(ABORT,'Raphael cannot leave a room'); END;
CREATE TRIGGER scoped_turn_update BEFORE UPDATE OF scope,room_id,thread_id,conversation_id,source,route ON agent_turns
WHEN (NEW.scope='room' AND (NEW.conversation_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM room_threads WHERE id=NEW.thread_id AND room_id=NEW.room_id)))
OR (NEW.scope!='private' AND NEW.conversation_id IS NOT NULL)
OR (NEW.scope='private' AND (NEW.room_id IS NOT NULL OR NEW.thread_id IS NOT NULL OR NEW.source IN ('room','member') OR NEW.route IN ('room','exchange')))
BEGIN SELECT RAISE(ABORT,'invalid turn scope'); END;
PRAGMA user_version=29;
COMMIT;
""")
