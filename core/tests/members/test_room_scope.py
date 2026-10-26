"""Room isolation, source identity and lossless migration contracts."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.background.service import BackgroundService
from raphael_core.background.tasks import TaskStore
from raphael_core.brain import db
from raphael_core.protocol import RpcError
from .test_rooms import rooms, create


async def test_room_turns_never_enter_private_queries_badges_or_worker_context(rig, rooms):
    peer = (await rig.call("members.create", {"name":"Alice"}))["member"]["id"]
    room = await create(rig, [0, peer])
    history = AgentHistory(rig.conn)
    private = history.add(session_id="private", question="private nonce", route="answer", prefix="", text="private answer", status="done", member_id=peer)
    await rig.call("rooms.send", {"room_id":room["id"], "text":"@Alice roomnonce", "request_id":"isolation"})
    task_id, turn_id = rig.background.attached[-1]
    history.resolve(turn_id, prefix="", text="roomnonce answer", status="done")
    turn = history.get(turn_id)
    assert turn.scope == "room" and turn.room_id == room["id"] and turn.conversation_id is None
    assert [t.id for t in history.list(member_id=peer)] == [private]
    assert [t.id for t in history.recent(10, member_id=peer)] == [private]
    assert [t.id for t in history.after(0, member_id=peer)] == [private]
    assert history.search("roomnonce", member_id=None) == []
    assert history.latest_by_member()[peer].id == private
    assert TaskStore(rig.conn).for_brief(0, 100, member_id=peer) == []
    rig.service.turn_arrived(turn_id)
    assert peer not in rig.service.store.unread()
    worker = SimpleNamespace(_history=history)
    task = TaskStore(rig.conn).get(task_id)
    assert BackgroundService._context(worker, task) == []
    assert not rig.conn.execute("SELECT 1 FROM conversations WHERE title LIKE '%roomnonce%'").fetchone()
    history.clear_member(peer)
    assert history.get(turn_id).scope == "room"
    history.delete(turn_id)
    orphan = TaskStore(rig.conn).get(task_id)
    assert orphan.scope == "room" and BackgroundService._context(worker, orphan) == []
    assert TaskStore(rig.conn).for_brief(0, 100, member_id=peer) == []


async def test_room_creation_retry_is_idempotent_and_owner_can_rename(rig, rooms):
    peer = (await rig.call("members.create", {"name":"Alice"}))["member"]["id"]
    request = {"member_ids":[0,peer],"request_id":"create-once"}
    first = (await rig.call("rooms.create", request))["room"]
    second = (await rig.call("rooms.create", request))["room"]
    assert first["id"] == second["id"] and first["name"]
    renamed = (await rig.call("rooms.update", {"room_id":first["id"],"name":"Owner title"}))["room"]
    assert renamed["name"] == "Owner title"
    assert rig.conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 1


async def test_all_room_paths_require_chief_distinct_members_and_two_to_six(rig, rooms):
    ids = [(await rig.call("members.create", {"name":f"Peer {i}"}))["member"]["id"] for i in range(6)]
    for members in ([0], ids, [0,*ids], [0,ids[0],ids[0]], [False,ids[0]]):
        with pytest.raises(RpcError):
            await rig.call("rooms.create", {"member_ids":members})
    room = (await rig.call("rooms.create", {"member_ids":[0,ids[0]]}))["room"]
    assert room["name"] and room["member_ids"] == [0,ids[0]]
    for members in ([0], ids, [0,*ids], [0,ids[0],ids[0]]):
        with pytest.raises(RpcError):
            await rig.call("rooms.update", {"room_id":room["id"], "member_ids":members})
    with pytest.raises(sqlite3.IntegrityError):
        rig.conn.execute("DELETE FROM room_members WHERE room_id=? AND member_id=0", (room["id"],))


async def test_reply_identity_comes_from_source_and_rejects_self_and_cross_scope(rig, rooms):
    ids = [(await rig.call("members.create", {"name":name}))["member"]["id"] for name in ("Alice","Bob")]
    room = await create(rig, [0,*ids])
    await rig.call("rooms.send", {"room_id":room["id"], "text":"@Alice check", "request_id":"first"})
    messages = (await rooms._messages({"room_id":room["id"]}))["messages"]
    owner, alice = messages
    assert alice["reply_reference"] == {"message_id":owner["id"],"author_kind":"owner","author_member_id":None}
    source = rooms._reply_reference(room, alice["thread_id"], alice["id"], ids[1])
    assert source["author_member_id"] == ids[0]
    await rig.call("members.update", {"id":ids[0],"name":"Renamed Alice"})
    assert rooms._reply_reference(room, alice["thread_id"], alice["id"], ids[1]) == source
    with pytest.raises(RpcError):
        rooms._reply_reference(room, alice["thread_id"], alice["id"], ids[0])
    other = await create(rig, [0,ids[0]])
    with pytest.raises(RpcError):
        await rooms._send({"room_id":other["id"],"text":"cross", "request_id":"cross", "reply_to":alice["id"]})
    with rig.conn:
        thread = rig.conn.execute("INSERT INTO room_threads(room_id,created) VALUES (?,1)",(room["id"],)).lastrowid
    with pytest.raises(RpcError):
        await rooms._send({"room_id":room["id"],"thread_id":thread,"text":"cross", "request_id":"cross-thread", "reply_to":alice["id"]})
    with pytest.raises(RpcError):
        await rooms._send({"room_id":room["id"],"text":"self", "request_id":"self", "reply_to":owner["id"]})
    with rig.conn:
        reply_id = rig.conn.execute("INSERT INTO room_messages(room_id,thread_id,member_id,text,created,reply_to) VALUES (?,?,?,'Peer answer',1,?)",
                                   (room["id"],alice["thread_id"],ids[1],alice["id"])).lastrowid
        rig.conn.execute("DELETE FROM room_messages WHERE id=?", (alice["id"],))
    message = rooms._message(rig.conn.execute("SELECT * FROM room_messages WHERE id=?",(reply_id,)).fetchone())
    assert message["reply_reference"] == {"message_id":alice["id"],"unavailable":True}


def test_legacy_dev_shapes_migrate_losslessly_and_quarantine_ambiguity(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for version in range(1,29):
        step = db._MIGRATIONS[version]
        step(conn) if callable(step) else conn.executescript(step)
    conversation = conn.execute("INSERT INTO conversations(member_id,title,created_at_ms,updated_at_ms,is_default) VALUES (1,'roomnonce',1,2,1)").lastrowid
    for room in range(1,4):
        conn.execute("INSERT INTO rooms(id,name,created) VALUES (?,?,1)",(room,f"Room {room}"))
    conn.executemany("INSERT INTO room_members VALUES (3,?)", [(i,) for i in range(1,7)])
    conn.execute("INSERT INTO room_members VALUES (1,1)")
    for turn, route, source, session in ((1,'room','room','r1'),(2,'exchange','member','r1'),(3,'exchange','member','r1'),(4,'room','room','ambiguous'),(5,'exchange','member','unknown'),(6,'answer','app','private'),(7,'room','room','wrong-author'),(8,'exchange','member','wrong-author'),(9,'room','room','missing-room'),(10,'exchange','member','missing-room')):
        conn.execute("INSERT INTO agent_turns(id,created_at_ms,session_id,question,route,prefix,text,status,source,member_id,conversation_id) VALUES (?,1,?,'roomnonce',?,'','original body','done',?,1,?)",(turn,session,route,source,conversation))
    for room, turn in ((1,1),(1,4),(2,4)):
        conn.execute("INSERT INTO room_messages(room_id,member_id,text,created,turn_id) VALUES (?,1,'',1,?)", (room,turn))
    conn.execute("INSERT INTO room_messages(room_id,member_id,text,created,turn_id) VALUES (1,2,'',1,7)")
    conn.execute("INSERT INTO worker_tasks(id,created_at_ms,state,session_id,turn_id,language,text) VALUES (1,1,'done','r1',1,'en','Room task')")
    conn.execute("INSERT INTO worker_tasks(id,created_at_ms,state,session_id,language,text) VALUES (2,1,'done','room:1:deleted','en','Orphan room task')")
    conn.execute("INSERT INTO conversation_context_summaries VALUES (?,'mixed roomnonce',5,2)", (conversation,))
    conn.execute("INSERT INTO agent_conversation_cli_sessions VALUES (?,'codex','mixed-session')", (conversation,))
    conn.execute("INSERT INTO member_unread VALUES (1)")
    conn.commit(); conn.close()
    conn = db.connect(path)
    rows = {r['id']:dict(r) for r in conn.execute("SELECT * FROM agent_turns")}
    assert [rows[i]['scope'] for i in range(1,7)] == ['room','room','room','quarantine','quarantine','private']
    assert all(rows[i]['scope'] == 'quarantine' for i in range(7,11))
    assert rows[2]['source_turn_id'] == rows[3]['source_turn_id'] == 1
    assert all(r['text'] == 'original body' for r in rows.values())
    assert all(rows[i]['conversation_id'] is None for i in range(1,6))
    records = conn.execute("SELECT * FROM scope_migrations").fetchall()
    assert len(records) == 9 and all(json.loads(r['original'])['conversation_id'] == conversation for r in records)
    assert [r[0] for r in conn.execute("SELECT member_id FROM room_members WHERE room_id=3")] == list(range(1,7))
    assert conn.execute("SELECT membership_state FROM rooms WHERE id=3").fetchone()[0] == 'needs_adjustment'
    assert [r[0] for r in conn.execute("SELECT member_id FROM room_members WHERE room_id=1 ORDER BY member_id")] == [0,1]
    assert not conn.execute("SELECT * FROM conversation_context_summaries").fetchall()
    assert not conn.execute("SELECT * FROM agent_conversation_cli_sessions").fetchall()
    assert len(conn.execute("SELECT * FROM scope_context_archive WHERE kind!='room_messages' AND kind!='conversations'").fetchall()) == 4
    assert conn.execute('SELECT scope FROM worker_tasks WHERE id=1').fetchone()[0] == 'room'
    assert conn.execute('SELECT scope FROM worker_tasks WHERE id=2').fetchone()[0] == 'quarantine'
    conn.close()
    conn = db.connect(path)
    assert len(conn.execute("SELECT * FROM scope_migrations").fetchall()) == 9
    conn.close()


def test_v33_lets_a_member_reply_twice_and_keeps_rows_triggers_and_ids(tmp_path):
    path = tmp_path / "v32.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for version in range(1,33):
        step = db._MIGRATIONS[version]
        step(conn) if callable(step) else conn.executescript(step)
    conn.execute("INSERT INTO members(id,name,position,created_at_ms) VALUES (1,'Alice',0,1),(2,'Bob',1,1)")
    conn.executemany("INSERT INTO rooms(id,name,created) VALUES (?,?,1)", [(1,'Plans'),(2,'Other')])
    conn.executemany("INSERT INTO room_members VALUES (?,?)", [(1,0),(1,1),(1,2),(2,0),(2,1)])
    conn.execute("INSERT INTO room_messages(room_id,text,created,request_id) VALUES (1,'@Alice plan',1,'first')")
    conn.execute("INSERT INTO room_messages(room_id,member_id,text,created,reply_to,origin_message_id) VALUES (1,1,'Plan',2,1,1)")
    conn.execute("INSERT INTO room_messages(room_id,member_id,text,created,reply_to,origin_message_id) VALUES (1,2,'Removed',3,2,1)")
    conn.execute("DELETE FROM room_messages WHERE id=3")
    conn.execute("INSERT INTO room_exchanges(origin_message_id,room_id,state,language) VALUES (1,1,'running','en')")
    conn.execute("INSERT INTO room_reply_intents(origin_message_id,source_message_id,recipient_id,recipient_name,state) VALUES (1,1,1,'Alice','done')")
    before = [tuple(r) for r in conn.execute("SELECT * FROM room_messages ORDER BY id")]
    conn.commit(); conn.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 33
    assert [tuple(r) for r in conn.execute("SELECT * FROM room_messages ORDER BY id")] == before
    assert conn.execute("SELECT state FROM room_exchanges").fetchone()[0] == 'running'
    assert conn.execute("SELECT recipient_name FROM room_reply_intents").fetchone()[0] == 'Alice'
    thread = before[0][-1]
    with conn:
        again = conn.execute("INSERT INTO room_messages(room_id,member_id,text,created,reply_to,origin_message_id) "
                             "VALUES (1,1,'Round two',4,1,1)").lastrowid
    assert again == 4, "a deleted message id is never reused"
    assert conn.execute("SELECT thread_id FROM room_messages WHERE id=?", (again,)).fetchone()[0] == thread
    other = conn.execute("SELECT id FROM room_threads WHERE room_id=2").fetchone()[0]
    for statement, values in (
        ("INSERT INTO room_messages(room_id,text,created,request_id) VALUES (1,'Again',5,'first')", ()),
        ("INSERT INTO room_messages(room_id,member_id,text,created,reply_to) VALUES (1,1,'Self',5,2)", ()),
        ("INSERT INTO room_messages(room_id,member_id,text,created,reply_to) VALUES (2,2,'Cross',5,1)", ()),
        ("INSERT INTO room_messages(room_id,thread_id,text,created) VALUES (1,?,'Cross thread',5)", (other,)),
        ("UPDATE room_messages SET room_id=2 WHERE id=2", ()),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, values)
    conn.close()


def test_scope_migration_rolls_back_data_and_schema_on_failure(monkeypatch):
    from raphael_core.brain import room_scope
    conn = sqlite3.connect(":memory:")
    for version in range(1,29):
        step = db._MIGRATIONS[version]
        step(conn) if callable(step) else conn.executescript(step)
    conn.execute("INSERT INTO rooms(name,created) VALUES ('Original',1)")
    conn.commit()
    script = room_scope._script
    def fail_before_triggers(connection, sql):
        if 'CREATE TRIGGER room_default_thread' in sql:
            raise sqlite3.OperationalError('injected migration failure')
        return script(connection, sql)
    monkeypatch.setattr(room_scope, '_script', fail_before_triggers)
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(conn)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 28
    assert 'scope' not in {r[1] for r in conn.execute('PRAGMA table_info(agent_turns)')}
    assert conn.execute('SELECT name FROM rooms').fetchone()[0] == 'Original'
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='scope_migrations'").fetchone()
    conn.close()
