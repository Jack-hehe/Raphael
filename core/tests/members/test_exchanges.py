"""Room exchanges route once, hand off explicitly and serialize shared member turns."""
import pytest
from raphael_core.agent.history import AgentHistory
from raphael_core.members.rooms import RoomsService


class Decision:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def room_responder(self, **kwargs):
        self.calls.append(kwargs)
        return self.answers.pop(0) if self.answers else {"choice":"none", "confidence":1, "reason":"nothing material"}


@pytest.fixture
def exchange(rig):
    service = RoomsService(rig.conn, members=rig.service, background=rig.background, decision=Decision())
    service.register(rig.rpc)
    return service


async def test_routing_cost_counts_toward_exchange_cap_and_deleted_rooms_leave_no_queue(rig, exchange):
    room, ids = await team(rig)
    class CostlyDecision:
        async def room_responder(self, **kwargs):
            kwargs["ledger"].add(model="fake/router", cost_usd=10)
            return {"choice":ids[0], "confidence":.9}
    exchange._exchanges.decision = CostlyDecision()
    await send(rig, room, "Choose a lead")
    result = await history(rig, room)
    assert result["exchange"]["cost"] == 10 and result["exchange"]["reason"] == "cost"
    assert not rig.background.submitted
    await rig.call("rooms.delete", {"room_id":room["id"]})
    assert not rig.conn.execute("SELECT 1 FROM room_exchanges").fetchone()
    assert not exchange._exchanges.attachments


async def team(rig, **patch):
    ids = [(await rig.call("members.create", {"name":name, "role":role}))["member"]["id"]
           for name, role in (("Alice","PM"), ("Bob","CTO"))]
    return (await rig.call("rooms.create", {"name":"Plans", "member_ids":[0,*ids], **patch}))["room"], ids


async def send(rig, room, text, key="one", **patch):
    return await rig.call("rooms.send", {"room_id":room["id"], "text":text, "request_id":key, **patch})


async def finish(rig, exchange, text, cost=.001):
    turn = rig.background.attached[-1][1]
    AgentHistory(rig.conn).resolve(turn, prefix="", text=text, status="done", cost_usd=cost)
    exchange.turn_arrived(turn)
    await exchange.wait_idle()


async def history(rig, room):
    return await rig.call("rooms.messages", {"room_id":room["id"]})


async def test_mention_and_reply_target_do_not_force_a_discussion(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, "@Alice status")
    assert [s["member_id"] for s in rig.background.submitted] == [ids[0]]
    await finish(rig, exchange, "Ready")
    messages = (await history(rig, room))["messages"]
    alice = next(m for m in messages if m["member_id"] == ids[0])
    await finish(rig, exchange, "Conclusion")
    await send(rig, room, "More details", "two", reply_to=alice["id"])
    assert rig.background.submitted[-1]["member_id"] == ids[0]
    assert (await history(rig, room))["messages"][-1]["reply_reference"]["author_kind"] == "owner"


async def test_decider_role_address_and_low_confidence_fallback(rig, exchange):
    room, ids = await team(rig, default_responder="member:2")
    exchange._exchanges.decision = Decision({"choice":ids[0], "confidence":.9, "reason":"PM owns planning"})
    await send(rig, room, "PM, what is the plan?")
    assert rig.background.submitted[-1]["member_id"] == ids[0]
    await finish(rig, exchange, "Ready")
    await finish(rig, exchange, "Conclusion")
    exchange._exchanges.decision = Decision({"choice":ids[0], "confidence":.4, "reason":"uncertain"})
    await send(rig, room, "Hello", "two")
    assert rig.background.submitted[-1]["member_id"] == ids[1]


async def test_decider_and_worker_read_only_the_origin_thread(rig, exchange):
    room, ids = await team(rig)
    with rig.conn:
        rig.conn.execute("INSERT INTO room_messages(room_id,text,created) VALUES (?,'Other thread secret',1)", (room['id'],))
        thread = rig.conn.execute("INSERT INTO room_threads(room_id,created) VALUES (?,1)", (room['id'],)).lastrowid
    decision = Decision({"choice":ids[0], "confidence":1})
    exchange._exchanges.decision = decision
    await send(rig, room, "Current thread question", thread_id=thread)
    assert [m['text'] for m in decision.calls[0]['transcript']] == ['Current thread question']
    assert 'Other thread secret' not in rig.background.submitted[-1]['text']
    assert 'Current thread question' in rig.background.submitted[-1]['text']


async def test_everyone_is_sequential_pass_is_hidden_and_wrapup_is_once(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, "@everyone report")
    assert len(rig.background.submitted) == 1
    await finish(rig, exchange, "Alice owns testing")
    assert rig.background.submitted[-1]["member_id"] == ids[0]
    assert "Alice owns testing" in rig.background.submitted[-1]["text"]
    await finish(rig, exchange, "(pass)")
    assert rig.background.submitted[-1]["member_id"] == ids[1]
    await finish(rig, exchange, "Bob owns release")
    assert rig.background.submitted[-1]["member_id"] == 0
    await finish(rig, exchange, "Conclusion: test first. Owner decides release. Alice: test; Bob: release.")
    result = await history(rig, room)
    assert all(m["text"] != "(pass)" for m in result["messages"])
    assert result["messages"][-1]["kind"] == "wrapup"
    assert result["exchange"]["state"] == "done"
    assert result["exchange"]["turns_taken"] == 4


async def test_wrapup_answers_the_last_visible_reply_and_all_passes_remain_safe(rig, exchange):
    room, ids = await team(rig)
    for key, first in (("one", "Alice owns testing"), ("two", "(pass)")):
        await send(rig, room, "@everyone report", key)
        await finish(rig, exchange, first)
        await finish(rig, exchange, "(pass)")
        await finish(rig, exchange, "")
        pending = (await history(rig, room))["messages"][-1]
        assert pending["kind"] == "wrapup"
        assert pending["reply_to_member"] is None
        assert pending["reply_to"] is None
        await finish(rig, exchange, "Conclusion")


async def test_a_removed_default_responder_is_presented_as_auto_for_room_editing(rig, exchange):
    room, ids = await team(rig, default_responder="member:1")
    await rig.call("members.delete", {"id":ids[0]})
    [view] = (await rig.call("rooms.list", {}))["rooms"]
    assert view["default_responder"] == "auto"
    await rig.call("rooms.update", {"room_id":room["id"], "member_ids":view["member_ids"], "default_responder":view["default_responder"]})


async def test_handoff_allows_only_explicit_repeats_and_three_hops(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, "@Alice plan")
    for text in ("@Bob check", "@Alice disagree", "@Bob please revise", "@Alice check again"):
        await finish(rig, exchange, text)
    assert [s["member_id"] for s in rig.background.submitted] == [ids[0],ids[1],ids[0],ids[1],0]
    await finish(rig, exchange, "Wrap-up")
    assert (await history(rig, room))["exchange"]["hops"] == 3


async def test_material_chime_ins_are_bounded_and_cannot_repeat(rig, exchange):
    room, ids = await team(rig)
    exchange._exchanges.decision = Decision(*[{"choice":i,"confidence":.9,"reason":"material concern"} for i in (ids[1],0,ids[1])])
    await send(rig, room, "@Alice plan")
    await finish(rig, exchange, "Ship today")
    assert rig.background.submitted[-1]["member_id"] == ids[1]
    await finish(rig, exchange, "Test first")
    await finish(rig, exchange, "Owner decides")
    if (await history(rig, room))["exchange"]["state"] == "running":
        await finish(rig, exchange, "Wrap-up")
    assert (await history(rig, room))["exchange"]["chime_ins"] <= 2


async def test_interjection_preempts_old_queue_stop_and_duplicate_notifications(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, "@everyone report")
    first = rig.background.attached[-1][1]
    await send(rig, room, "@Bob only after testing", "two")
    assert len(rig.background.submitted) == 1
    await finish(rig, exchange, "Current turn finishes")
    assert rig.background.submitted[-1]["member_id"] == ids[1]
    exchange.turn_arrived(first)
    await exchange.wait_idle()
    assert len(rig.background.submitted) == 2
    await rig.call("rooms.stop", {"room_id":room["id"]})
    await finish(rig, exchange, "Done")
    assert (await history(rig, room))["exchange"]["state"] == "stopped"
    assert len(rig.background.submitted) == 2


async def test_cost_and_restart_stop_without_replay(rig, exchange):
    room, ids = await team(rig)
    await send(rig, room, "@everyone report")
    await finish(rig, exchange, "@Alice continue", cost=10)
    assert len(rig.background.submitted) == 1
    assert (await history(rig, room))["exchange"]["reason"] == "cost"
    await send(rig, room, "@Alice again", "two")
    restored = RoomsService(rig.conn, members=rig.service, background=rig.background)
    assert (await restored._messages({"room_id":room["id"]}))["exchange"]["state"] == "stopped"
    assert len(rig.background.submitted) == 2


async def test_six_turn_cap_reserves_one_wrapup_and_removes_repeated_queued_members(rig, exchange):
    room, ids = await team(rig)
    extra = [(await rig.call("members.create", {"name":f"Person{i}"}))["member"]["id"] for i in range(3)]
    await rig.call("rooms.update", {"room_id":room["id"], "member_ids":[0,*ids,*extra]})
    await send(rig, room, "@everyone report")
    for _ in range(6):
        await finish(rig, exchange, "Progress confirmed")
        if (await history(rig, room))["exchange"]["state"] == "done":
            break
    result = await history(rig, room)
    assert result["exchange"]["turns_taken"] == 6
    assert result["messages"][-1]["kind"] == "wrapup"
    assert len(rig.background.submitted) == 6


async def test_default_seniority_and_unavailable_cli_decider(rig, exchange):
    room, ids = await team(rig)
    from raphael_core.protocol import RpcError
    with pytest.raises(RpcError):
        await rig.call("rooms.update", {"room_id":room["id"], "member_ids":ids})
    exchange._settings.set("agent_model", "cli:codex:gpt-5.6-terra")
    await send(rig, room, "Status")
    assert rig.background.submitted[-1]["member_id"] == 0
    assert exchange._exchanges.decision.calls == []
    for responder in ("member:999", "wrong", True):
        from raphael_core.protocol import RpcError
        with pytest.raises(RpcError):
            await rig.call("rooms.update", {"room_id":room["id"], "default_responder":responder})
