"""Computer cards through the production card RPCs and a run against a fake shell and a fake model: the card
follows the run through the owner's turn, the task resumes once when it ends after the owner gave the computer
back, and Stop, expiry, a restart and a replay resume nothing."""

import asyncio
import json
import logging

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.computer import handback
from raphael_core.computer.handback import HandbackCards, HandbackStore
from raphael_core.computer.run import ComputerRuns, Final, Handback
from raphael_core.protocol import RpcError
from raphael_core.router import Router

from .conftest import FakeModel, call, reply
from .test_handback import Desktop, hands_over


class Rig:
    def __init__(self, conn, shell, make_run, clock=None):
        self.conn, self.shell, self.make_run = conn, shell, make_run
        self.runs = ComputerRuns(shell.bridge)
        self.router = Router()
        self.events = []
        self.now = clock or [1_800_000_000_000]
        self.cards = HandbackCards(conn, self.runs, notify=lambda m, p: self.events.append((m, p)), now_ms=lambda: self.now[0])
        self.cards.register(self.router)
        self.resumed = []

        async def resume(turn_id, report):
            self.resumed.append((turn_id, report))
        self.cards.resume = resume
        self.turn = AgentHistory(conn).add(session_id="s", question="看一下上傳頁", route="answer", prefix="", text="",
                                           status="done", source="app", member_id=0)
        self.desktop = Desktop(shell)

    async def hand_over(self, *turns, reason="sign_in", **kwargs):
        """A run that reaches the sign-in, and the card the service shows for it."""
        run = self.make_run(FakeModel(*hands_over(turns, reason=reason)), handback=True, **kwargs)
        self.runs.begin(run)
        event = await asyncio.wait_for(run.next_event(), 5)
        assert isinstance(event, Handback)
        card = self.cards.show(run, event, turn_id=self.turn, member_id=0, conversation_id=None, language="zh-TW",
                               done=[("computer", "d0")])
        return run, card

    async def done(self, card):
        return await self.router.call("integrations.card_done", {"id": card.id})

    async def stop(self, card):
        return await self.router.call("integrations.card_stop", {"id": card.id})

    def state(self, card):
        return self.cards.store.get(card.id)

    async def settle(self, seconds=3.0):
        await asyncio.wait_for(self.cards.idle(), seconds)


@pytest.fixture
def rig(conn, shell, make_run):
    return Rig(conn, shell, make_run)


async def test_a_card_follows_its_run_and_the_task_resumes_once_when_it_ends_after_the_owners_turn(rig):
    run, card = await rig.hand_over(reply(content="頁面顯示確認碼 YT-4F2A。"))
    assert (card.state, card.reason, card.app, card.seq) == ("control", "sign_in", "Google Chrome", 1)
    rig.desktop.signed_in = True
    result = await rig.done(card)
    assert result["done"] and result["card"]["state"] == "returning"
    await rig.settle()
    card = rig.state(card)
    assert card.state == "done"
    assert [(turn, report.kind, report.reason, report.app, report.language) for turn, report in rig.resumed] == [
        (rig.turn, "done", "sign_in", "Google Chrome", "zh-TW")]
    report = rig.resumed[0][1]
    assert "YT-4F2A" in report.text and report.done == (("computer", "d0"),) and report.steps
    assert ("agent.turn", {"turn_id": rig.turn, "member_id": 0}) in rig.events
    # A replayed 我完成了, before or after the end, gives nothing back and resumes nothing.
    for _ in range(2):
        replay = await rig.done(card)
        assert not replay["done"] and replay["card"]["state"] == "done"
    assert len(rig.resumed) == 1 and not rig.runs.give_back(run.run_id)


async def test_a_done_pressed_twice_at_once_gives_the_computer_back_once(rig):
    run, card = await rig.hand_over(reply(content="完成。"))
    rig.desktop.signed_in = True
    first, second = await asyncio.gather(rig.done(card), rig.done(card))
    assert sorted([first["done"], second["done"]]) == [False, True]
    await rig.settle()
    assert len(rig.resumed) == 1 and rig.shell.ops().count("check") == 0


async def test_while_the_owner_holds_the_computer_the_card_waits_and_the_run_asks_for_nothing(rig):
    run, card = await rig.hand_over(reply(content="完成。"))
    ops = list(rig.shell.ops())
    await asyncio.sleep(0.05)
    assert rig.shell.ops() == ops and rig.state(card).state == "control" and rig.resumed == []
    # The owner's own input ends nothing; their input is what this is for.
    rig.runs.interrupt()
    assert not run.done and rig.state(card).state == "control"


async def test_stop_ends_the_run_and_nothing_ever_resumes(rig):
    run, card = await rig.hand_over(reply(content="不應該到這裡。"))
    stopped = await rig.stop(card)
    assert stopped["stopped"] and stopped["card"]["state"] == "stopped"
    await rig.settle()
    assert run.done and rig.state(card).state == "stopped" and rig.resumed == []
    rig.desktop.signed_in = True
    ops = list(rig.shell.ops())
    late = await rig.done(card)
    assert not late["done"] and not (await rig.stop(card))["stopped"]
    await asyncio.sleep(0.05)
    assert rig.shell.ops() == ops and rig.resumed == []


async def test_stop_pressed_just_after_done_ends_the_run_and_nothing_resumes(rig):
    run, card = await rig.hand_over(reply(content="完成。"))
    rig.desktop.signed_in = True
    await rig.done(card)
    assert (await rig.stop(card))["stopped"]  # the run was checking the page: Stop ends it
    await rig.settle()
    assert rig.state(card).state == "stopped" and rig.resumed == [] and "act" not in rig.shell.ops()
    assert run.final is not None and run.final.kind == "stopped"


async def test_a_session_stop_ends_a_waiting_run_and_its_card_never_resumes(rig):
    run, card = await rig.hand_over(reply(content="不應該到這裡。"))
    assert rig.runs.stop_session("s-1")
    await rig.settle()
    assert rig.state(card).state == "stopped" and rig.resumed == []


async def test_a_newer_computer_task_replaces_a_waiting_run_and_its_card_never_resumes(rig):
    run, card = await rig.hand_over(reply(content="不應該到這裡。"))
    second = rig.make_run(FakeModel(reply(content="另一件事。")), handback=True)
    rig.runs.begin(second)
    await rig.settle()
    assert rig.state(card).state == "stopped" and rig.resumed == []
    assert (await asyncio.wait_for(second.next_event(), 5)).kind == "done"


async def test_the_form_still_there_gives_the_card_back_to_the_owner_who_can_try_again(rig):
    run, card = await rig.hand_over(reply(content="頁面顯示確認碼 YT-4F2A。"))
    assert (await rig.done(card))["done"]  # too early: the page still asks for the password

    async def back():
        return rig.state(card).state == "control" and rig.state(card).note == "still"
    for _ in range(100):
        if await back():
            break
        await asyncio.sleep(0.02)
    assert await back() and rig.resumed == [] and rig.state(card).view()["note"] == "still"
    rig.desktop.signed_in = True
    assert (await rig.done(card))["done"]
    await rig.settle()
    assert rig.state(card).state == "done" and len(rig.resumed) == 1


async def test_a_later_hand_over_of_the_same_run_has_a_card_of_its_own(rig):
    run, first = await rig.hand_over(reply(call("computer_hand_over", reason="payment")), reply(content="付款完成。"))
    rig.desktop.signed_in = True
    await rig.done(first)
    for _ in range(100):
        cards = rig.cards.cards_of(rig.turn)
        if len(cards) == 2:
            break
        await asyncio.sleep(0.02)
    first, second = rig.cards.cards_of(rig.turn)
    assert (first.seq, first.state, first.reason) == (1, "done", "sign_in")
    assert (second.seq, second.state, second.reason) == (2, "control", "payment")
    assert rig.resumed == []  # the task waits for the run's end, not for each hand-over
    assert (await rig.done(second))["done"]
    await rig.settle()
    assert len(rig.resumed) == 1 and rig.resumed[0][1].reason == "payment"


async def test_a_question_nobody_can_be_asked_while_the_task_carries_on_is_not_taken(rig):
    run, card = await rig.hand_over(
        reply(call("computer_act", window=7, element=7, action="click", final=True)),
        reply(content="不應該到這裡。"))
    rig.desktop.signed_in = True
    await rig.done(card)
    await rig.settle()
    assert "act" not in rig.shell.ops() and rig.state(card).state == "done"
    ((turn, report),) = rig.resumed
    assert report.kind == "denied"


async def test_a_card_whose_time_ran_out_is_refused_and_lets_its_run_go(conn, shell, make_run):
    rig = Rig(conn, shell, make_run)
    run, card = await rig.hand_over(reply(content="不應該到這裡。"))
    rig.now[0] += handback.HANDBACK_WAIT_MS
    late = await rig.done(card)
    assert not late["done"] and late["card"]["state"] == "expired"
    await rig.settle()
    assert run.done and rig.resumed == [] and rig.state(card).state == "expired"
    assert rig.cards.views([rig.turn])[rig.turn][0]["state"] == "expired"


async def test_a_run_that_ends_by_itself_while_the_owner_holds_the_computer_resumes_nothing(rig):
    run, card = await rig.hand_over(reply(content="不應該到這裡。"))
    run.stop("closing")
    await rig.settle()
    assert rig.state(card).state == "stopped" and rig.resumed == []


async def test_a_restart_while_the_owner_holds_the_computer_fails_the_card_and_resumes_nothing(rig, conn):
    run, card = await rig.hand_over(reply(content="不應該到這裡。"))
    # The core ends (the run goes with it); a new core finds the card and nothing waiting for it.
    await rig.cards.aclose()
    await rig.runs.aclose()
    restarted = HandbackCards(conn, ComputerRuns(rig.shell.bridge), now_ms=lambda: rig.now[0])
    again = restarted.store.get(card.id)
    assert (again.state, again.error) == ("failed", "interrupted")
    router = Router()
    restarted.register(router)
    result = await router.call("integrations.card_done", {"id": card.id})
    assert not result["done"] and result["card"]["state"] == "failed"
    assert rig.resumed == []


async def test_the_app_closing_resumes_nothing_and_gives_nothing_back(rig):
    run, card = await rig.hand_over(reply(content="不應該到這裡。"))
    ops = list(rig.shell.ops())
    await rig.cards.aclose()
    await rig.runs.aclose()
    assert run.done and rig.resumed == [] and rig.shell.ops() == ops


async def test_a_card_holds_the_app_and_the_reason_and_never_a_title_or_a_password(rig, caplog):
    caplog.set_level(logging.DEBUG)
    run, card = await rig.hand_over(reply(content="完成。"))
    rig.desktop.signed_in = True
    await rig.done(card)
    await rig.settle()
    rows = json.dumps([[list(row) for row in rig.conn.execute(f'SELECT * FROM "{table}"')]
                       for (table,) in rig.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()],
                      default=str)
    seen = rows + json.dumps(rig.events) + json.dumps(rig.cards.views([rig.turn])) + caplog.text
    assert "hunter2" not in seen and "私人頻道" not in seen and "Studio" not in seen
    assert "Google Chrome" in rows


async def test_a_card_moves_only_along_its_edges(conn):
    store = HandbackStore(conn, lambda: 1)
    AgentHistory(conn).add(session_id="s", question="q", route="answer", prefix="", text="", status="done", source="app")
    card = store.open(turn_id=1, member_id=0, conversation_id=None, run_id="r" * 32, reason="sign_in", app="Chrome", language="en")
    assert store.open(turn_id=1, member_id=0, conversation_id=None, run_id="r" * 32, reason="payment", app="Chrome",
                      language="en").seq == 2
    states = list(handback.MOVES)
    for start in states:
        for to in states:
            with conn:
                conn.execute("UPDATE handback_cards SET state=? WHERE id=?", (start, card.id))
            moved = store.move(card.id, to)
            assert (moved is not None) == (to in handback.MOVES[start]), (start, to)


async def test_unknown_cards_and_calls_without_a_card_are_refused(rig):
    for method in ("integrations.card_done", "integrations.card_stop"):
        for params in ({}, {"id": 3}, {"id": "missing"}):
            with pytest.raises(RpcError):
                await rig.router.call(method, params)
