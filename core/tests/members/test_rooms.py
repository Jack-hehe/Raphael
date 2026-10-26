"""Room messages, routing settings, migrations and shared worker dispatch."""
from dataclasses import replace
import pytest
from raphael_core.agent.history import AgentHistory
from raphael_core.members.rooms import RoomsService
from raphael_core.agent.speaker import MAX_DEPTH
from raphael_core.protocol import RpcError

@pytest.fixture
async def rooms(rig):
    service = RoomsService(rig.conn, members=rig.service, background=rig.background,
                           notify=lambda method, params: rig.notes.append((method,params)), now_ms=rig.wall)
    service.register(rig.rpc)
    yield service
    await service.aclose()

async def create(rig, ids, **kwargs):
    if ids == [0] and not kwargs.get("is_team"):
        member = (await rig.call("members.create", {"name":"Room peer"}))["member"]
        ids = [0, member["id"]]
    return (await rig.call("rooms.create", {"name":"Team", "member_ids":ids, **kwargs}))["room"]


async def test_mentions_use_whole_names_text_order_and_ignore_email_addresses(rig, rooms):
    ids = [(await rig.call("members.create", {"name":name}))["member"]["id"]
           for name in ("Ops", "Ops Lead", "Sam")]
    room = await create(rig, [0,*ids])
    assert rooms._targets(room, "@Sam then @Ops Lead then @Ops.") == [ids[2],ids[1],ids[0]]
    assert rooms._targets(room, "jo@Sam.com and @Opsgenie are not mentions") == []
    assert rooms._targets(room, "@Ops Lead: please review") == [ids[1]]
    assert rooms._targets(room, "@所有人，請更新") == [0,*ids]
    assert rooms._targets(room, "@拉斐爾，請收束") == [0]
    assert rooms._targets(room, "@ラファエル、お願いします") == [0]
    await rig.call("rooms.send", {"room_id":room["id"], "request_id":"ordered", "text":"@Sam then @Ops Lead"})
    assert rig.background.submitted[-1]["member_id"] == ids[2]
    AgentHistory(rig.conn).resolve(rig.background.attached[-1][1], prefix="", text="Ready", status="done")
    rooms.turn_arrived(rig.background.attached[-1][1])
    await rooms.wait_idle()
    assert rig.background.submitted[-1]["member_id"] == ids[1]


async def test_hidden_passes_do_not_truncate_visible_history_pages(rig, rooms):
    room = await create(rig, [0])
    for i in range(101):
        rig.conn.execute("INSERT INTO room_messages (room_id,text,created) VALUES (?,?,?)", (room["id"],str(i),i))
    for i in range(110):
        turn = rooms._history.add(session_id=f"pass:{i}", question="Status", route="room", prefix="", text="(pass)", status="done", scope="room", room_id=room["id"], thread_id=rooms._thread(room))
        rig.conn.execute("INSERT INTO room_messages (room_id,member_id,text,created,turn_id) VALUES (?,0,'',?,?)", (room["id"],i,turn))
    rig.conn.commit()
    page = (await rig.call("rooms.messages", {"room_id":room["id"]}))["messages"]
    assert len(page) == 100 and [m["text"] for m in page] == [str(i) for i in range(1,101)]
    older = (await rig.call("rooms.messages", {"room_id":room["id"], "before":page[0]["id"]}))["messages"]
    assert [m["text"] for m in older] == ["0"]

async def test_group_mentions_members_and_duplicate_requests(rig, rooms):
    member = (await rig.call("members.create", {"name":"Alice"}))["member"]
    room = await create(rig, [0,member["id"]])
    request = {"room_id":room["id"], "request_id":"one", "text":"@Alice check progress"}
    sent = await rig.call("rooms.send", request)
    assert not sent["duplicate"]
    assert [s["member_id"] for s in rig.background.submitted] == [member["id"]]
    assert (await rig.call("rooms.send", request))["duplicate"]
    assert len(rig.background.submitted) == 1
    messages = (await rig.call("rooms.messages", {"room_id":room["id"]}))["messages"]
    assert [m["member_id"] for m in messages] == [None,member["id"]]
    AgentHistory(rig.conn).resolve(messages[1]["turn_id"], prefix="", text="@Raphael done", status="done")
    rooms.turn_arrived(messages[1]["turn_id"])
    await rooms.wait_idle()
    assert len(rig.background.submitted) == 2
    assert (await rig.call("rooms.messages", {"room_id":room["id"]}))["messages"][1]["text"] == "@Raphael done"
    assert ("rooms.changed", {"room_id":room["id"]}) in rig.notes

async def test_everyone_starts_only_one_cost_limited_worker(rig, rooms, monkeypatch):
    ids = [(await rig.call("members.create", {"name":f"Person{i}"}))["member"]["id"] for i in range(5)]
    room = await create(rig,[0,*ids])
    called = []
    submit = rig.background.submit
    def capture(**kwargs):
        called.append(kwargs)
        return submit(**kwargs)
    monkeypatch.setattr(rig.background,"submit",capture)
    await rig.call("rooms.send", {"room_id":room["id"], "request_id":"all", "text":"@所有人 report"})
    assert len(called) == 1
    assert called[0]["member_id"] == 0
    for c in called:
        assert c["asked_by"].depth == MAX_DEPTH and not c["asked_by"].may_ask
        assert c["model"] == rig.service._settings.get("agent_model")

async def test_group_mentions_stay_inside_room_and_chief_handles_unmentioned(rig, rooms):
    group = (await rig.call("groups.create", {"name":"Engineering"}))["group"]
    a = (await rig.call("members.create", {"name":"Alice", "group_id":group["id"]}))["member"]
    await rig.call("members.create", {"name":"Outside", "group_id":group["id"]})
    room = await create(rig,[0,a["id"]])
    await rig.call("rooms.send", {"room_id":room["id"], "request_id":"group", "text":"@Engineering status"})
    assert [s["member_id"] for s in rig.background.submitted] == [a["id"]]
    turn_id = rig.background.attached[-1][1]
    AgentHistory(rig.conn).resolve(turn_id, prefix="", text="Ready", status="done")
    rooms.turn_arrived(turn_id)
    await rooms.wait_idle()
    await rig.call("rooms.send", {"room_id":room["id"], "request_id":"chief", "text":"Hello"})
    assert rig.background.submitted[-1]["member_id"] == 0
    await rig.call("rooms.send", {"room_id":room["id"], "request_id":"boundary", "text":"@AliceExtra hi"})
    assert rig.background.submitted[-1]["member_id"] == 0

async def test_team_room_reused_and_new_members_join_live(rig, rooms):
    first = await create(rig,[0],is_team=True)
    a = (await rig.call("members.create", {"name":"Alice"}))["member"]
    second = await create(rig,[0],is_team=True)
    assert first["id"] == second["id"] and a["id"] in second["member_ids"]

async def test_existing_worker_refusal_is_a_visible_error_not_a_lost_message(rig, rooms):
    room = await create(rig,[0]); rig.background.refuse = "worker_busy"
    await rig.call("rooms.send", {"room_id":room["id"], "request_id":"busy", "text":"Hi"})
    messages = (await rig.call("rooms.messages", {"room_id":room["id"]}))["messages"]
    assert messages[0]["text"] == "Hi" and messages[1]["status"] == "error" and messages[1]["text"]

async def test_validates_room_members_payload_and_pagination(rig, rooms):
    for ids in ([],[True],[999]):
        with pytest.raises(RpcError): await create(rig,ids)
    room = await create(rig,[0])
    for payload in ({"text":"", "request_id":"x"},{"text":"hi", "request_id":"bad id"},{"text":"x"*10001,"request_id":"x"}):
        with pytest.raises(RpcError): await rig.call("rooms.send", {"room_id":room["id"],**payload})
    with pytest.raises(RpcError): await rig.call("rooms.messages", {"room_id":room["id"],"before":True})
    assert (await rig.call("rooms.delete", {"room_id":room["id"]}))["deleted"]
    with pytest.raises(RpcError): await rig.call("rooms.messages", {"room_id":room["id"]})


async def test_room_management_validates_and_persists_changes(rig, rooms):
    member = (await rig.call("members.create", {"name":"Alice"}))["member"]
    room = await create(rig, [0, member["id"]])
    updated = (await rig.call("rooms.update", {"room_id":room["id"], "name":"Planning", "member_ids":[0,member["id"]]}))["room"]
    assert updated["name"] == "Planning" and updated["member_ids"] == [0,member["id"]]
    for payload in ({"name":""}, {"member_ids":[]}, {"member_ids":[True]}, {"member_ids":[999]}, {"member_ids":[member["id"]]}):
        with pytest.raises(RpcError):
            await rig.call("rooms.update", {"room_id":room["id"], **payload})
    assert (await rig.call("rooms.list", {}))["rooms"][0]["name"] == "Planning"
    team = await create(rig, [0], is_team=True)
    with pytest.raises(RpcError):
        await rig.call("rooms.update", {"room_id":team["id"], "member_ids":[0]})


async def test_attachment_only_room_message_is_saved_and_retry_does_not_resave(rig, rooms, tmp_path, monkeypatch):
    import base64
    from raphael_core.services.attachment_store import AttachmentStore
    rooms._attachment_store = AttachmentStore(tmp_path / "attachments")
    room = await create(rig, [0])
    submitted = []
    original = rig.background.submit
    def capture(**kwargs):
        submitted.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(rig.background, "submit", capture)
    request = {"room_id":room["id"], "request_id":"file", "text":"", "attachments":[{"name":"notes.txt", "mime":"text/plain", "data":base64.b64encode(b"notes").decode()}]}
    await rig.call("rooms.send", request)
    messages = (await rig.call("rooms.messages", {"room_id":room["id"]}))["messages"]
    attachment = messages[0]["attachments"][0]
    assert attachment["name"] == "notes.txt" and attachment["id"] and attachment["size"] == 5
    assert submitted[0]["attachments"][0].data == b"notes"
    assert (await rig.call("rooms.send", request))["duplicate"]
    assert len(submitted) == 1
    with pytest.raises(RpcError):
        await rig.call("rooms.send", {**request, "request_id":"bad", "attachments":[{"data":"bad"}]})


async def test_voice_uses_existing_transcriber_and_rejects_invalid_audio(rig, rooms):
    import base64
    import io
    import wave
    from unittest.mock import AsyncMock
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 10)
    rooms._transcribe_audio = AsyncMock(return_value="Draft transcript")
    assert await rig.call("rooms.transcribe", {"data":base64.b64encode(audio.getvalue()).decode(), "ui_language":"en"}) == {"text":"Draft transcript"}
    rooms._transcribe_audio.assert_awaited_once_with(audio.getvalue(), "en")
    for value in (None, "!", base64.b64encode(b"not audio").decode()):
        with pytest.raises(RpcError):
            await rig.call("rooms.transcribe", {"data":value})


async def test_room_reply_metadata_defaults_and_persisted_recipient_zero(rig, rooms):
    room = await create(rig, [0])
    await rig.call("rooms.send", {"room_id":room["id"], "request_id":"metadata", "text":"Hello"})
    history = (await rig.call("rooms.messages", {"room_id":room["id"]}))["messages"]
    assert all(m["reply_to_member"] is None and m["kind"] == "message" for m in history)
    with rig.conn:
        rig.conn.execute("UPDATE room_messages SET reply_to_member = 0, kind = 'wrapup' WHERE id = ?", (history[1]["id"],))
    message = (await rig.call("rooms.messages", {"room_id":room["id"]}))["messages"][1]
    assert message["reply_to_member"] == 0 and message["kind"] == "wrapup"
    # Metadata must never trigger a new member dispatch.
    assert len(rig.background.submitted) == 1


def test_room_message_metadata_migrates_existing_rows_and_reopens(tmp_path):
    import sqlite3
    from raphael_core.brain.db import _MIGRATIONS, connect
    path = tmp_path / "rooms.db"
    conn = sqlite3.connect(path)
    for version in range(1, 27):
        step = _MIGRATIONS[version]
        step(conn) if callable(step) else conn.executescript(step)
    conn.execute("INSERT INTO rooms (name, created) VALUES ('Existing', 1)")
    conn.execute("INSERT INTO room_messages (room_id, text, created) VALUES (1, 'Preserved', 1)")
    conn.commit()
    conn.close()
    conn = connect(path)
    row = conn.execute("SELECT * FROM room_messages").fetchone()
    assert row["text"] == "Preserved" and row["reply_to_member"] is None and row["kind"] == "message"
    with conn:
        conn.execute("UPDATE room_messages SET reply_to_member = 0, kind = 'wrapup'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE room_messages SET kind = 'invalid'")
    conn.close()
    conn = connect(path)
    row = conn.execute("SELECT * FROM room_messages").fetchone()
    assert row["reply_to_member"] == 0 and row["kind"] == "wrapup"
    conn.close()
