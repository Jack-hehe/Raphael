"""Tests for the members' RPC methods: the list, making, changing and deleting members, their
pictures, groups, routines, and which threads are unread or waiting."""

import base64
import json
import logging

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.protocol import INVALID_PARAMS, RpcError

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
WEBP = b"RIFF\x10\0\0\0WEBPVP8 " + b"\0" * 16
METHODS = {
    "members.list", "members.create", "members.update", "members.delete", "members.set_avatar",
    "members.presets", "members.read", "members.viewing", "members.tool_choices",
    "groups.create", "groups.update", "groups.delete",
    "routines.list", "routines.create", "routines.update", "routines.delete", "routines.template",
}  # fmt: skip


async def refused(rig, method, params):
    with pytest.raises(RpcError) as caught:
        await rig.call(method, params)
    assert caught.value.code == INVALID_PARAMS
    return (caught.value.data or {}).get("kind")


def test_the_methods_of_the_members_are_registered(rig):
    assert METHODS <= set(rig.rpc.methods())


async def test_raphael_is_first_in_the_list_in_the_language_asked(rig):
    listed = await rig.call("members.list", {"ui_language": "en"})
    assert listed["groups"] == []
    (raphael,) = listed["members"]
    assert raphael == {
        "id": 0, "name": "Raphael", "role": "", "instructions": "", "avatar": "", "group_id": None,
        "model": "", "tools": None, "position": -1, "created": 0, "unread": False,
        "waiting": False, "preview": "", "last_at": None,
    }  # fmt: skip
    rig.language = "ja"
    assert (await rig.call("members.list"))["members"][0]["name"] == "ラファエル"


async def test_raphaels_own_picture_is_read_from_the_shells_copy(rig):
    folder = rig.local / "avatar"
    folder.mkdir(parents=True)
    (folder / "picture").write_bytes(PNG)
    (folder / "picture.json").write_text(json.dumps({"mime": "image/png", "crop": {"x": 0.5, "y": 0.4, "zoom": 1.5}}))
    raphael = (await rig.call("members.list"))["members"][0]
    assert raphael["avatar_url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert raphael["avatar_crop"] == {"x": 0.5, "y": 0.4, "zoom": 1.5}


async def test_a_member_is_made_listed_changed_and_deleted(rig, caplog):
    caplog.set_level(logging.DEBUG)
    group = (await rig.call("groups.create", {"name": "工作"}))["group"]
    made = await rig.call(
        "members.create",
        {"name": "小行程", "role": "管行程", "instructions": "PRIVATE-INSTRUCTIONS", "group_id": group["id"]},
    )
    member = made["member"]
    assert made["routine_offer"] is None  # the group has no place to look
    assert {k: member[k] for k in ("name", "role", "instructions", "group_id", "tools", "unread")} == {
        "name": "小行程", "role": "管行程", "instructions": "PRIVATE-INSTRUCTIONS",
        "group_id": group["id"], "tools": None, "unread": False,
    }  # fmt: skip
    listed = await rig.call("members.list")
    assert [m["id"] for m in listed["members"]] == [0, member["id"]]
    assert listed["groups"] == [{"id": group["id"], "name": "工作", "position": 0, "sources": []}]
    changed = (await rig.call("members.update", {"id": member["id"], "role": "秘書", "tools": ["web_search"]}))["member"]
    assert (changed["role"], changed["tools"]) == ("秘書", ["web_search"])
    assert await rig.call("members.delete", {"id": member["id"]}) == {"deleted": True}
    assert rig.background.forgotten == [member["id"]]
    assert [m["id"] for m in (await rig.call("members.list"))["members"]] == [0]
    for secret in ("小行程", "管行程", "PRIVATE-INSTRUCTIONS", "秘書"):
        assert secret not in caplog.text


async def test_what_cannot_be_is_refused_with_the_field_named(rig):
    assert await refused(rig, "members.create", {"name": ""}) == "name"
    assert await refused(rig, "members.create", {"name": "拉斐爾"}) == "taken"
    assert await refused(rig, "members.create", {"name": "a", "avatar": "file:x.png"}) == "avatar"
    assert await refused(rig, "members.update", {"id": 0, "role": "x"}) == "raphael"
    assert await refused(rig, "members.delete", {"id": 0}) == "raphael"
    assert await refused(rig, "members.update", {"id": 42, "role": "x"}) == "not_found"
    assert await refused(rig, "groups.update", {"id": 42, "name": "x"}) == "not_found"


async def test_a_member_of_a_group_with_places_to_look_is_offered_a_morning_routine(rig):
    group = (await rig.call("groups.create", {"name": "專案"}))["group"]
    sources = [{"kind": "github", "ref": "octo/repo"}, {"kind": "notion", "ref": "0123456789abcdef0123456789abcdef"}]
    updated = (await rig.call("groups.update", {"id": group["id"], "sources": sources}))["group"]
    assert updated["sources"] == sources
    made = await rig.call("members.create", {"name": "小進度", "group_id": group["id"]})
    offer = made["routine_offer"]
    assert offer["title"] == "每天早上的進度"
    assert offer["schedule"] == {"kind": "daily", "time": "09:00"}
    assert "GitHub：octo/repo" in offer["prompt"] and "Notion：0123456789abcdef0123456789abcdef" in offer["prompt"]
    assert (await rig.call("routines.list", {"member_id": made["member"]["id"]}))["routines"] == []  # offered, not made
    english = await rig.call("routines.template", {"member_id": made["member"]["id"], "ui_language": "en"})
    assert english["offer"]["title"] == "Morning progress"
    assert (await rig.call("routines.template", {"member_id": 0}))["offer"] is None


async def test_deleting_a_group_moves_its_members_to_no_group(rig):
    group = (await rig.call("groups.create", {"name": "g"}))["group"]
    member = (await rig.call("members.create", {"name": "A", "group_id": group["id"]}))["member"]
    assert await rig.call("groups.delete", {"id": group["id"]}) == {"deleted": True}
    assert (await rig.call("members.list"))["members"][1]["group_id"] is None
    assert member["group_id"] == group["id"]


# --- pictures --------------------------------------------------------------------------------------


async def test_a_picture_of_the_owners_is_copied_in_and_shown_and_the_old_one_goes(rig):
    member = (await rig.call("members.create", {"name": "A"}))["member"]
    data = base64.b64encode(PNG).decode()
    shown = (await rig.call("members.set_avatar", {"id": member["id"], "name": "C:/Users/me/a.png", "mime": "image/png", "data": data}))["member"]
    assert shown["avatar"].startswith(f"file:{member['id']}-") and shown["avatar"].endswith(".png")
    assert shown["avatar_url"] == f"data:image/png;base64,{data}"
    folder = rig.local / "avatars" / "members"
    (first,) = folder.iterdir()
    assert first.read_bytes() == PNG and "Users" not in first.name  # the path it came from is never kept
    again = (await rig.call("members.set_avatar", {"id": member["id"], "name": "b.webp", "mime": "image/webp", "data": base64.b64encode(WEBP).decode()}))["member"]
    assert [p.name for p in folder.iterdir()] == [again["avatar"].removeprefix("file:")]
    await rig.call("members.update", {"id": member["id"], "avatar": ""})
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize(
    ("mime", "data", "kind"),
    [
        ("image/png", base64.b64encode(b"<svg/>").decode(), "type"),
        ("application/pdf", base64.b64encode(b"%PDF-1.7").decode(), "type"),
        ("image/png", "not base64!", "data"),
        ("image/png", "", "size"),
    ],
)
async def test_what_is_not_a_picture_is_refused(rig, mime, data, kind):
    member = (await rig.call("members.create", {"name": "A"}))["member"]
    assert await refused(rig, "members.set_avatar", {"id": member["id"], "name": "x", "mime": mime, "data": data}) == kind


async def test_the_presets_are_the_owners_own_files_if_the_folder_is_there(rig):
    assert await rig.call("members.presets") == {"presets": []}
    folder = rig.local / "avatars" / "presets"
    folder.mkdir(parents=True)
    (folder / "chibi-01.webp").write_bytes(WEBP)
    (folder / "chibi-02.png").write_bytes(PNG)
    (folder / "notes.txt").write_text("x")
    (folder / "fake.png").write_bytes(b"<html>")
    presets = (await rig.call("members.presets"))["presets"]
    assert [p["avatar"] for p in presets] == ["preset:chibi-01", "preset:chibi-02"]
    assert presets[0]["url"].startswith("data:image/webp;base64,")
    member = (await rig.call("members.create", {"name": "A", "avatar": "preset:chibi-02"}))["member"]
    assert member["avatar_url"].startswith("data:image/png;base64,")
    gone = (await rig.call("members.create", {"name": "B", "avatar": "preset:nobody"}))["member"]
    assert "avatar_url" not in gone  # the app shows the first character of the name


# --- unread and waiting ----------------------------------------------------------------------------


async def test_a_turn_in_a_thread_that_is_not_on_screen_is_unread_until_it_is_read(rig, conn):
    member = (await rig.call("members.create", {"name": "A"}))["member"]
    history = AgentHistory(conn)

    def turn(member_id, source="app"):
        turn_id = history.add(
            session_id="s", question="q", route="chat", prefix="", text="好的，已經整理好了。", status="done",
            source=source, member_id=member_id,
        )  # fmt: skip
        rig.service.turn_arrived(turn_id)

    await rig.call("members.viewing", {"id": member["id"]})
    turn(member["id"])  # on screen: no news
    turn(0, source="voice")  # heard on the overlay: no news
    listed = {m["id"]: m for m in (await rig.call("members.list"))["members"]}
    assert not listed[member["id"]]["unread"] and not listed[0]["unread"]
    assert listed[member["id"]]["preview"] == "好的，已經整理好了。"
    await rig.call("members.viewing", {"id": None})
    turn(member["id"], source="routine")
    turn(0, source="telegram")
    listed = {m["id"]: m for m in (await rig.call("members.list"))["members"]}
    assert listed[member["id"]]["unread"] and listed[0]["unread"]
    await rig.call("members.read", {"id": member["id"]})
    listed = {m["id"]: m for m in (await rig.call("members.list"))["members"]}
    assert not listed[member["id"]]["unread"] and listed[0]["unread"]


async def test_a_thread_waits_while_a_question_waits_there_or_the_background_says_so(rig, conn):
    member = (await rig.call("members.create", {"name": "A"}))["member"]
    other = (await rig.call("members.create", {"name": "B"}))["member"]
    AgentHistory(conn).add(
        session_id="s", question="q", route="answer", prefix="問", text="?", status="confirm",
        member_id=member["id"],
    )  # fmt: skip
    rig.service.add_waiting_source(lambda: [other["id"]])
    rig.service.add_waiting_source(lambda: 1 / 0)  # a source that breaks breaks nothing
    listed = {m["id"]: m["waiting"] for m in (await rig.call("members.list"))["members"]}
    assert listed == {0: False, member["id"]: True, other["id"]: True}


async def test_the_tool_choices_are_raphaels_own_and_the_connections(rig):
    assert await rig.call("members.tool_choices") == {
        "tools": ["web_search", "web_read", "weather", "local_time"],
        "connections": [{"id": "srv-1", "name": "GitHub", "enabled": True}],
    }


# --- routines --------------------------------------------------------------------------------------


async def test_a_routine_is_made_listed_switched_and_deleted(rig):
    member = (await rig.call("members.create", {"name": "A"}))["member"]
    routine = (await rig.call(
        "routines.create",
        {"member_id": member["id"], "title": "晨報", "prompt": "看新聞", "schedule": {"kind": "daily", "time": "09:00"}},
    ))["routine"]  # fmt: skip
    assert routine["next_run"] == rig.wall.now + 60_000  # 09:00, a minute from now
    assert routine["last_run"] is None and routine["enabled"] is True
    assert (await rig.call("routines.list", {"member_id": member["id"]}))["routines"] == [routine]
    off = (await rig.call("routines.update", {"id": routine["id"], "enabled": False}))["routine"]
    assert (off["enabled"], off["next_run"]) == (False, None)
    assert await refused(rig, "routines.create", {"member_id": 42, "title": "t", "prompt": "p", "schedule": {"kind": "every", "hours": 1}}) == "not_found"
    assert await refused(rig, "routines.create", {"member_id": 0, "title": "t", "prompt": "p", "schedule": {"kind": "weekly", "days": [9], "time": "09:00"}}) == "schedule"
    assert await rig.call("routines.delete", {"id": routine["id"]}) == {"deleted": True}


async def test_deleting_a_member_takes_its_conversation_memory_and_routines(rig, conn):
    member = (await rig.call("members.create", {"name": "A"}))["member"]
    member_id = member["id"]
    await rig.call("routines.create", {"member_id": member_id, "title": "t", "prompt": "p", "schedule": {"kind": "every", "hours": 2}})
    history = AgentHistory(conn)
    history.add(session_id="s", question="q", route="chat", prefix="", text="a", status="done", member_id=member_id)
    history.add(session_id="s", question="r", route="exchange", prefix="", text="b", status="done", member_id=0, peer_id=member_id)
    kept = history.add(session_id="s", question="mine", route="chat", prefix="", text="c", status="done")
    rig.memory.store_for(member_id).write("preference-format", type="preference", description="用條列", body="用條列")
    folder = rig.memory.store_for(member_id).root
    assert folder.exists()
    await rig.call("members.delete", {"id": member_id})
    assert [t.id for t in history.list(member_id=0)] == [kept]  # the exchange with it went too
    assert history.list(member_id=member_id) == []
    assert not folder.exists()
    assert rig.service.store.routines(member_id) == []
