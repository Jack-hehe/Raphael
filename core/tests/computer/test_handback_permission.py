"""The questions of a run that carries on after the owner gave the computer back are permission cards in the
chat (P08c), where P08b refused them: nobody sits at the question any more, so the card is its only door.
Against the production card RPCs, a fake shell and a fake model: nothing here reaches a desktop."""

import asyncio

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.agent.permissions import PermissionCards
from raphael_core.agent.policy import UNATTENDED_WAIT_MS
from raphael_core.computer.handback import HandbackCards
from raphael_core.computer.run import ComputerRuns, Handback
from raphael_core.router import Router

from .conftest import FakeModel, call, element, reply
from .test_handback import Desktop, hands_over


class Rig:
    def __init__(self, conn, shell, make_run):
        self.conn, self.shell, self.make_run = conn, shell, make_run
        self.runs = ComputerRuns(shell.bridge)
        self.router = Router()
        self.events = []
        self.now = [1_800_000_000_000]
        notify = lambda method, params: self.events.append((method, params))  # noqa: E731
        self.permissions = PermissionCards(conn, self.runs, notify=notify, now_ms=lambda: self.now[0])
        self.permissions.register(self.router)
        self.cards = HandbackCards(conn, self.runs, notify=notify, now_ms=lambda: self.now[0], permissions=self.permissions)
        self.cards.register(self.router)
        self.resumed = []

        async def resume(turn_id, report):
            self.resumed.append((turn_id, report))
        self.cards.resume = resume
        self.turn = AgentHistory(conn).add(session_id="s", question="看一下上傳頁", route="answer", prefix="", text="",
                                           status="done", source="app", member_id=0)
        self.desktop = Desktop(shell)

    async def hand_over(self, *turns, **kwargs):
        run = self.make_run(FakeModel(*hands_over(turns)), handback=True, **kwargs)
        self.runs.begin(run)
        event = await asyncio.wait_for(run.next_event(), 5)
        assert isinstance(event, Handback)
        card = self.cards.show(run, event, turn_id=self.turn, member_id=0, conversation_id=None, language="zh-TW",
                               done=[("computer", "d0")])
        return run, card

    async def gives_back_and_waits_for_a_card(self, run, card):
        """The owner signs in and presses 我完成了; the run carries on until it asks, and the question is a card."""
        self.desktop.signed_in = True
        await self.router.call("integrations.card_done", {"id": card.id})
        for _ in range(250):
            cards = self.permissions.store.for_turns([self.turn]).get(self.turn, [])
            if cards:
                return cards[0]
            await asyncio.sleep(0.02)
        raise AssertionError("the run never asked")

    async def answer(self, method, card, **params):
        return await self.router.call(f"integrations.{method}", {"id": card.id, **params})

    def card(self, card):
        return self.permissions.store.get(card.id)

    async def settle(self, seconds=3.0):
        await asyncio.wait_for(self.cards.idle(), seconds)


@pytest.fixture
def rig(conn, shell, make_run):
    return Rig(conn, shell, make_run)


SENDS = reply(call("computer_act", window=7, element=7, action="click", final=True))


async def test_a_step_that_needs_a_yes_after_the_hand_back_is_a_card_in_the_chat_that_handed_the_computer_over(rig):
    run, handed = await rig.hand_over(SENDS, reply(content="已送出。"))
    asked = await rig.gives_back_and_waits_for_a_card(run, handed)
    # The card is on the turn that began the task, says the step in plain words and is bound to its digest.
    assert (asked.turn_id, asked.source, asked.kind, asked.state, asked.always) == (rig.turn, "run", "step", "pending", False)
    assert asked.summary and len(asked.digest) == 64 and asked.language == "zh-TW"
    view = rig.permissions.views([rig.turn])[rig.turn][0]
    assert view["state"] == "pending" and view["digest"] == asked.digest and view["source"] == "run"
    assert ("agent.turn", {"turn_id": rig.turn, "member_id": 0}) in rig.events
    # The run waits for the owner: the step is not taken and the run is not over.
    await asyncio.sleep(0.05)
    assert "act" not in rig.shell.ops() and not run.done and rig.resumed == []
    # It waits as long as a question nobody sits at: ten minutes, not thirty seconds.
    assert asked.expires_at_ms - asked.created_at_ms == UNATTENDED_WAIT_MS


async def test_allowing_once_takes_the_step_once_and_the_task_resumes_with_what_the_run_did(rig):
    run, handed = await rig.hand_over(SENDS, reply(content="已送出。"))
    asked = await rig.gives_back_and_waits_for_a_card(run, handed)
    result = await rig.answer("card_allow", asked, digest=asked.digest)
    assert result["answered"] and result["card"]["state"] == "allowed"
    await rig.settle()
    assert rig.shell.ops().count("act") == 1 and rig.card(asked).state == "allowed"
    assert [(report.kind) for _, report in rig.resumed] == ["done"]
    # The yes opened one step, once: a replay finds the card answered and takes nothing.
    again = await rig.answer("card_allow", asked, digest=asked.digest)
    assert not again["answered"] and again["card"]["state"] == "allowed"
    assert rig.shell.ops().count("act") == 1


async def test_denying_takes_no_step_and_the_task_resumes_told_it_was_denied(rig):
    run, handed = await rig.hand_over(SENDS, reply(content="不應該到這裡。"))
    asked = await rig.gives_back_and_waits_for_a_card(run, handed)
    result = await rig.answer("card_deny", asked)
    assert result["answered"] and result["card"]["state"] == "denied"
    await rig.settle()
    assert "act" not in rig.shell.ops() and rig.card(asked).state == "denied"
    ((turn, report),) = rig.resumed
    assert (turn, report.kind) == (rig.turn, "denied")
    assert not (await rig.answer("card_allow", asked))["answered"]  # a no is final


async def test_the_first_use_of_another_app_after_the_hand_back_is_a_card_with_always_and_always_keeps_the_app(rig):
    excel = {"window": 9, "app": {"exe": "excel.exe", "name": "Excel"}, "title": "報表",
             "elements": [element(1, "Window", "報表", depth=0, actions=[]), element(2, "Button", "儲存")]}
    rig.shell.handlers["apps"] = {"windows": [
        {"id": 7, "exe": "chrome.exe", "name": "Google Chrome", "title": "Studio", "foreground": True, "always_allowed": True},
        {"id": 9, "exe": "excel.exe", "name": "Excel", "title": "報表", "foreground": False, "always_allowed": False}]}
    page = rig.shell.handlers["look"]
    rig.shell.handlers["look"] = lambda args: excel if args["window"] == 9 else page(args)
    run, handed = await rig.hand_over(reply(call("computer_look", window=9)), reply(call("computer_act", window=9, element=2,
                                                                                         action="click", final=True)),
                                      reply(content="已儲存。"))
    asked = await rig.gives_back_and_waits_for_a_card(run, handed)
    assert (asked.kind, asked.always, asked.state) == ("app", True, "pending") and "Excel" in asked.summary
    result = await rig.answer("card_always", asked, digest=asked.digest)
    assert result["answered"] and result["card"]["state"] == "always"
    # The app is the owner's to allow for good: the shell is told (the list of the apps always allowed).
    for _ in range(100):
        if "allow" in rig.shell.ops():
            break
        await asyncio.sleep(0.02)
    assert "allow" in rig.shell.ops()
    # ... and the step that sends still asks, as a card of its own: an Always never loosens that.
    for _ in range(250):
        cards = rig.permissions.store.for_turns([rig.turn])[rig.turn]
        if len(cards) == 2:
            break
        await asyncio.sleep(0.02)
    second = rig.permissions.store.for_turns([rig.turn])[rig.turn][1]
    assert (second.kind, second.always, second.state) == ("step", False, "pending") and "act" not in rig.shell.ops()
    assert (await rig.answer("card_deny", second))["answered"]
    await rig.settle()


async def test_a_card_of_another_digest_answers_nothing_and_the_question_still_waits(rig):
    run, handed = await rig.hand_over(SENDS, reply(content="已送出。"))
    asked = await rig.gives_back_and_waits_for_a_card(run, handed)
    stale = await rig.answer("card_allow", asked, digest="0" * 64)
    assert not stale["answered"] and stale["card"]["state"] == "pending"
    await asyncio.sleep(0.05)
    assert "act" not in rig.shell.ops() and not run.done
    assert (await rig.answer("card_allow", asked, digest=asked.digest))["answered"]
    await rig.settle()
    assert rig.shell.ops().count("act") == 1


async def test_there_is_no_always_on_a_step_that_sends(rig):
    run, handed = await rig.hand_over(SENDS, reply(content="不應該到這裡。"))
    asked = await rig.gives_back_and_waits_for_a_card(run, handed)
    result = await rig.answer("card_always", asked)
    assert not result["answered"] and result["card"]["state"] == "pending"
    assert (await rig.answer("card_deny", asked))["answered"]
    await rig.settle()


async def test_a_yes_that_comes_after_the_question_ran_out_takes_nothing(rig):
    run, handed = await rig.hand_over(SENDS, reply(content="不應該到這裡。"))
    asked = await rig.gives_back_and_waits_for_a_card(run, handed)
    rig.now[0] += UNATTENDED_WAIT_MS  # the owner was away for ten minutes
    late = await rig.answer("card_allow", asked, digest=asked.digest)
    assert not late["answered"] and late["card"]["state"] == "expired"
    run.stop("closing")
    await rig.settle()
    assert "act" not in rig.shell.ops() and rig.card(asked).state == "expired"


async def test_the_question_of_a_run_that_is_stopped_while_it_waits_is_a_cancelled_card(rig):
    run, handed = await rig.hand_over(SENDS, reply(content="不應該到這裡。"))
    asked = await rig.gives_back_and_waits_for_a_card(run, handed)
    assert rig.runs.stop_session("s-1")
    await rig.settle()
    assert rig.card(asked).state == "cancelled" and rig.resumed == [] and "act" not in rig.shell.ops()
    assert not (await rig.answer("card_allow", asked))["answered"]


async def test_a_restart_leaves_no_question_to_say_yes_to(rig, conn):
    run, handed = await rig.hand_over(SENDS, reply(content="不應該到這裡。"))
    asked = await rig.gives_back_and_waits_for_a_card(run, handed)
    await rig.cards.aclose()
    await rig.runs.aclose()
    restarted = PermissionCards(conn, ComputerRuns(rig.shell.bridge), now_ms=lambda: rig.now[0])
    assert restarted.store.get(asked.id).state == "expired"
    router = Router()
    restarted.register(router)
    result = await router.call("integrations.card_allow", {"id": asked.id, "digest": asked.digest})
    assert not result["answered"] and "act" not in rig.shell.ops()


async def test_unknown_cards_and_bad_calls_are_refused(rig):
    from raphael_core.protocol import RpcError

    for method in ("integrations.card_allow", "integrations.card_always", "integrations.card_deny"):
        for params in ({}, {"id": 3}, {"id": "missing"}):
            with pytest.raises(RpcError):
                await rig.router.call(method, params)

