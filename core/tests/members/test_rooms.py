"""Room dispatch is bounded, idempotent and driven only by owner messages."""
from dataclasses import replace
import pytest
from raphael_core.agent.history import AgentHistory
from raphael_core.members.rooms import RoomsService, MAX_REPLIES
from raphael_core.agent.speaker import MAX_DEPTH
from raphael_core.protocol import RpcError

@pytest.fixture
def rooms(rig):
    service = RoomsService(rig.conn, members=rig.service, background=rig.background,
                           notify=lambda method, params: rig.notes.append((method,params)), now_ms=rig.wall)
    service.register(rig.rpc)
    return service

async def create(rig, ids, **kwargs):
    return (await rig.call("rooms.create", {"name":"Team", "member_ids":ids, **kwargs}))["room"]

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
    assert len(rig.background.submitted) == 1
    assert (await rig.call("rooms.messages", {"room_id":room["id"]}))["messages"][1]["text"] == "@Raphael done"
    assert ("rooms.changed", {"room_id":room["id"]}) in rig.notes

async def test_fanout_limit_and_worker_chain_prevent_member_rings(rig, rooms, monkeypatch):
    ids = [(await rig.call("members.create", {"name":f"Person{i}"}))["member"]["id"] for i in range(7)]
    room = await create(rig,[0,*ids])
    called = []
    submit = rig.background.submit
    def capture(**kwargs):
        called.append(kwargs)
        return submit(**kwargs)
    monkeypatch.setattr(rig.background,"submit",capture)
    await rig.call("rooms.send", {"room_id":room["id"], "request_id":"all", "text":"@所有人 report"})
    assert len(called) == MAX_REPLIES
    assert len({c["member_id"] for c in called}) == MAX_REPLIES
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
    updated = (await rig.call("rooms.update", {"room_id":room["id"], "name":"Planning", "member_ids":[member["id"]]}))["room"]
    assert updated["name"] == "Planning" and updated["member_ids"] == [member["id"]]
    for payload in ({"name":""}, {"member_ids":[]}, {"member_ids":[True]}, {"member_ids":[999]}):
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
