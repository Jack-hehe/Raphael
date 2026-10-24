"""Member profile preferences, local commands and imported instruction documents."""

import pytest

from raphael_core.members.store import MemberError, MemberStore


def test_member_profile_preferences_round_trip_and_validate(conn):
    store = MemberStore(conn)
    member = store.create_member(name="Ada", blurb="Research partner", reply_style="conversational",
                                 voice="a" * 32, read_aloud=True, notify=True,
                                 ask_before_members=True, pinned=True, hidden=True)
    assert member == store.member(member.id)
    assert member.speaker().reply_style == "conversational"
    assert member.speaker().ask_before_members
    with pytest.raises(MemberError):
        store.update_member(member.id, reply_style="unknown")
    with pytest.raises(MemberError):
        store.update_member(member.id, notify="yes")


async def test_command_crud_expansion_and_member_scope(rig):
    member = (await rig.call("members.create", {"name": "Ada"}))["member"]
    saved = await rig.call("members.commands.save", {"member_id": member["id"], "name": "summarize",
                           "description": "Short summary", "template": "Summarize: {input}"})
    assert saved["command"]["name"] == "summarize"
    assert rig.service.expand_command(member["id"], "/summarize hello") == "Summarize: hello"
    assert rig.service.expand_command(0, "/summarize hello") == "/summarize hello"
    assert len((await rig.call("members.commands.list", {"member_id": member["id"]}))["commands"]) == 1
    await rig.call("members.commands.delete", {"member_id": member["id"], "id": saved["command"]["id"]})
    assert rig.service.expand_command(member["id"], "/summarize hello") == "/summarize hello"


async def test_duplicate_copies_profile_and_commands_without_history(rig):
    original = (await rig.call("members.create", {"name": "Ada", "blurb": "Partner", "pinned": True}))["member"]
    await rig.call("members.commands.save", {"member_id": original["id"], "name": "help",
                   "description": "Help", "template": "Help with {input}"})
    copy = (await rig.call("members.duplicate", {"id": original["id"]}))["member"]
    assert copy["name"] != original["name"] and copy["blurb"] == "Partner"
    assert len((await rig.call("members.commands.list", {"member_id": copy["id"]}))["commands"]) == 1
    rig.service.store.mark_unread(original["id"])
    await rig.call("members.mark_all_read")
    assert rig.service.store.unread() == set()


async def test_manual_memory_create_is_bounded_private_and_owner_authored(rig):
    from raphael_core.protocol import RpcError

    one = (await rig.call("members.create", {"name": "Ada"}))["member"]["id"]
    note = (await rig.call("memory.create", {"member_id": one, "space": "own", "body": "My favorite topic is history."}))["note"]
    assert note["body"] == "My favorite topic is history." and note["origin"] == "said"
    assert (await rig.call("memory.list", {"member_id": one}))["memories"][0]["name"] == note["name"]
    assert (await rig.call("memory.list", {"member_id": 0}))["memories"] == []
    for bad in ({"space": "shared", "body": "Topic"}, {"body": "x" * 1201}, {"member_id": 999, "body": "Topic"}):
        with pytest.raises(RpcError):
            await rig.call("memory.create", {"member_id": one, **bad})


async def test_finished_and_waiting_notices_require_opt_in_and_read_aloud_is_once(rig):
    import asyncio
    from raphael_core.agent.history import AgentHistory

    class Speech:
        def __init__(self):
            self.lines = []

        async def say_member(self, text, **kwargs):
            self.lines.append((text, kwargs))

    speech = Speech()
    rig.service.use_speech(speech)
    member = rig.service.store.create_member(name="Ada", notify=True, read_aloud=True, voice="b" * 32)
    history = AgentHistory(rig.conn)
    turn_id = history.add(session_id="s", question="Q", route="answer", prefix="", text="Answer", status="done", source="app", member_id=member.id)
    rig.service.turn_arrived(turn_id)
    rig.service.turn_arrived(turn_id)
    await asyncio.sleep(0)
    notices = [params for method, params in rig.notes if method == "member.notice"]
    assert notices == [{"member_id": member.id, "name": "Ada", "kind": "finished", "notify": True}]
    assert speech.lines == [("Answer", {"voice": "b" * 32, "turn_id": turn_id})]
    rig.service.needs_input(member.id)
    assert rig.notes[-1][1]["kind"] == "needs_input"
    rig.service.store.update_member(member.id, notify=False)
    before = len(rig.notes)
    rig.service.needs_input(member.id)
    assert len(rig.notes) == before
