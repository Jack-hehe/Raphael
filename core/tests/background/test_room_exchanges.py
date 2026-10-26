"""A small fake team completes an exchange through the real background worker."""
from datetime import timezone

import pytest

from members.conftest import QuietModel
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.members.rooms import RoomsService
from raphael_core.members.service import MembersService
from raphael_core.memory.service import MemoryService
from .conftest import report, until


async def test_api_room_team_hands_off_and_wraps_up_through_shared_worker(make_rig, conn, tmp_path):
    rig = make_rig()
    memory = MemoryService(conn, tmp_path / "memory", QuietModel(), MemoryKeyStore(), quiet_s=3600, start_delay_s=3600)
    members = MembersService(conn, tmp_path, background=rig.service, memory=memory, now_ms=rig.wall, tz=timezone.utc)
    memory.member_exists = members.member_exists
    members.register(rig.rpc)
    rooms = RoomsService(conn, members=members, background=rig.service)
    rooms.register(rig.rpc)

    async def unused(*args, **kwargs):
        pytest.fail("No team administration is needed")

    rig.service.use_memory(memory)
    rig.service.use_team(members, unused)
    notify = rig.service._notify
    def arrived(method, params):
        notify(method, params)
        if method == "agent.turn":
            rooms.turn_arrived(params["turn_id"])
    rig.service._notify = arrived
    alice = members.store.create_member(name="Alice", role="PM", personality="PRECISE-PLANNER")
    bob = members.store.create_member(name="Bob", role="CTO", personality="CAUTIOUS-ENGINEER")
    rig.client.replies.extend(report(text) for text in (
        "@Alice lead the release plan", "@Bob I propose shipping today", "@Alice I disagree: test first",
        "Agreed, I own testing",
        "### Conclusion\nTest first.\n### Owner decision\nThe release date.\n### Next steps\nAlice owns testing; Bob owns rollout.",
    ))
    try:
        room = (await rig.rpc.call("rooms.create", {"name":"Release", "member_ids":[0,alice.id,bob.id]}))["room"]
        await rig.rpc.call("rooms.send", {"room_id":room["id"], "text":"Discuss a release plan", "request_id":"team", "language":"en"})
        await until(lambda: rooms._exchanges.latest(room["id"])["state"] == "done", timeout=10)
        messages = (await rig.rpc.call("rooms.messages", {"room_id":room["id"]}))["messages"]
        assert [m["member_id"] for m in messages] == [None,0,alice.id,bob.id,alice.id,0]
        assert [m["reply_to_member"] for m in messages[2:]] == [0,alice.id,bob.id,None]
        # Live, Raphael's wrap-up came out as one line and its three headings ran together.
        assert messages[-1]["kind"] == "wrapup" and messages[-1]["text"].splitlines() == [
            "### Conclusion", "Test first.", "### Owner decision", "The release date.", "### Next steps",
            "Alice owns testing; Bob owns rollout."]
        assert len(rig.client.chat_calls) == 5 and not rig.client.replies and rig.reports() == []
        assert "PRECISE-PLANNER" in rig.client.chat_calls[1]["messages"][0]["content"]
        assert "CAUTIOUS-ENGINEER" in rig.client.chat_calls[2]["messages"][0]["content"]
        assert "I disagree: test first" in str(rig.client.chat_calls[3]["messages"])
    finally:
        await rooms.aclose()
        await members.aclose()
        await memory.aclose()
