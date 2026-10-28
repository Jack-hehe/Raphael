"""Committed member and routine changes announce invalidations without private field values."""

import base64

import pytest

from raphael_core.protocol import RpcError


@pytest.mark.parametrize("tool", [False, True])
async def test_routine_changes_notify_for_rpc_and_chat_tools(rig, tool):
    member_id = (await rig.call("members.create", {"name": "Routine owner"}))["member"]["id"]

    async def change(verb, params):
        rig.notes.clear()
        if tool:
            result = await rig.service.routine_tool(member_id, f"routine_{verb}", params)
        else:
            result = await rig.call(f"routines.{verb}", params)
        assert rig.notes == [("routines.changed", {"member_id": member_id})]
        return result

    routine = (await change("create", {
        "member_id": member_id, "title": "Private title", "prompt": "Private prompt",
        "schedule": {"kind": "daily", "time": "09:00"},
    }))["routine"]
    await change("update", {"id": routine["id"], "enabled": False})
    await change("delete", {"id": routine["id"]})


@pytest.mark.parametrize("late", [False, True])
async def test_scheduler_announces_advanced_times_even_when_a_run_was_missed(rig, late):
    routine = (await rig.call("routines.create", {
        "member_id": 0, "title": "Daily", "prompt": "Check progress",
        "schedule": {"kind": "daily", "time": "09:00"},
    }))["routine"]
    rig.notes.clear()
    rig.wall.now += 60_000 + (13 * 3_600_000 if late else 0)
    rig.service.tick()
    assert ("routines.changed", {"member_id": 0}) in rig.notes
    assert rig.service.store.routine(routine["id"]).next_run_ms > rig.wall.now


async def test_member_rpc_avatar_and_harness_creation_announce_changes(rig):
    made = rig.service.create(name="Assistant", role="", instructions="", group="")
    assert rig.notes == [("members.changed", {})]
    for method, params in [
        ("members.create", {"name": "Other"}),
        ("members.update", {"id": made.id, "name": "Renamed"}),
        ("members.set_avatar", {"id": made.id, "name": "avatar.png", "mime": "image/png",
                                "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 32).decode()}),
        ("members.delete", {"id": made.id}),
    ]:
        rig.notes.clear()
        await rig.call(method, params)
        assert ("members.changed", {}) in rig.notes


async def test_groups_and_read_marks_invalidate_the_member_list(rig):
    rig.notes.clear()
    group = (await rig.call("groups.create", {"name": "Work"}))["group"]
    assert rig.notes == [("members.changed", {})]
    for method, params in [
        ("groups.update", {"id": group["id"], "name": "Projects"}),
        ("groups.delete", {"id": group["id"]}),
    ]:
        rig.notes.clear()
        await rig.call(method, params)
        assert rig.notes == [("members.changed", {})]
    rig.service.store.mark_unread(0)
    rig.notes.clear()
    await rig.call("members.read", {"id": 0})
    assert rig.notes == [("members.changed", {})]


async def test_refused_changes_do_not_announce_success(rig):
    with pytest.raises(RpcError):
        await rig.call("members.create", {"name": ""})
    with pytest.raises(RpcError):
        await rig.call("routines.create", {"member_id": 0, "title": "", "schedule": {}})
    assert rig.notes == []


async def test_memory_writes_rpc_moves_and_deletes_announce_the_affected_spaces(rig):
    member = (await rig.call("members.create", {"name": "Memory owner"}))["member"]
    member_id = member["id"]
    rig.notes.clear()
    store = rig.memory.store_for(member_id)
    store.write("reference-project", type="reference", description="Project", body="We are building an app.")
    own = ("memory.changed", {"member_id": member_id, "space": "own"})
    shared = ("memory.changed", {"member_id": 0, "space": "shared"})
    assert rig.notes == [own]
    rig.notes.clear()
    await rig.call("memory.update", {"member_id": member_id, "space": "own", "name": "reference-project", "body": "We are testing an app."})
    assert rig.notes == [own]
    rig.notes.clear()
    await rig.call("memory.move", {"member_id": member_id, "name": "reference-project", "to_space": "shared"})
    assert own in rig.notes and shared in rig.notes
    rig.notes.clear()
    await rig.call("memory.delete", {"space": "shared", "name": "reference-project"})
    assert rig.notes == [shared]
    store.write("reference-next", type="reference", description="Next", body="Review the app.")
    rig.notes.clear()
    await rig.call("memory.clear", {"member_id": member_id, "space": "own"})
    assert rig.notes == [own]
    rig.notes.clear()
    await rig.call("members.delete", {"id": member_id})
    assert own in rig.notes


async def test_harness_group_creation_and_viewing_marks_are_announced(rig):
    member = rig.service.create(name="Grouped helper", role="", instructions="", group="New group")
    assert rig.notes == [("members.changed", {}), ("members.changed", {})]
    rig.service.store.mark_unread(member.id)
    rig.notes.clear()
    await rig.call("members.viewing", {"id": member.id})
    assert rig.notes == [("members.changed", {})]
    rig.notes.clear()
    await rig.call("members.viewing", {"id": member.id})
    assert rig.notes == []  # Reading the list must not start a notification loop.


async def test_conversation_summary_changes_announce_the_owner_without_text(rig):
    member_id = (await rig.call("members.create", {"name": "Summary owner"}))["member"]["id"]
    rig.notes.clear()
    rig.memory.conversation_for(member_id)._save("Private conversation summary", 1)
    assert rig.notes == [("memory.changed", {"member_id": member_id, "space": "own"})]
    rig.notes.clear()
    await rig.call("memory.summary_clear", {"member_id": member_id, "space": "own"})
    assert rig.notes == [("memory.changed", {"member_id": member_id, "space": "own"})]
