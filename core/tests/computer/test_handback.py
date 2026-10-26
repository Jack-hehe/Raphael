"""A run that reaches a sign-in pauses for the owner instead of ending, and carries on, once, when the
owner gives the computer back. Against a fake shell and a fake model: nothing here reaches a desktop."""

import asyncio
import json

import pytest

from raphael_core.computer.run import ComputerRuns, Final, Handback, LeaseHeld, RunLimits

from .conftest import Clock, FakeModel, call, element, reply

PAGE = {"window": 7, "app": {"exe": "chrome.exe", "name": "Google Chrome"}, "title": "Studio - 私人頻道"}


def sign_in_page():
    return {**PAGE, "elements": [
        element(1, "Window", "Studio", depth=0, actions=[]),
        element(2, "Edit", "電子郵件", actions=["value"]),
        element(3, "Edit", "密碼", password=True, value="hunter2", actions=["value"]),
        element(4, "Button", "Sign in"),
    ]}


def signed_in_page():
    return {**PAGE, "elements": [
        element(1, "Window", "Studio", depth=0, actions=[]),
        element(5, "Text", "上傳頁面", actions=[]),
        element(6, "Text", "確認碼 YT-4F2A", actions=[]),
        element(7, "Button", "選擇檔案"),
    ]}


class Desktop:
    """A browser window whose page asks for a sign-in until the owner has signed in."""

    def __init__(self, shell):
        self.signed_in = False
        shell.handlers.update(
            apps={"windows": [{"id": 7, "exe": "chrome.exe", "name": "Google Chrome", "title": "Studio - 私人頻道",
                               "foreground": True, "always_allowed": True}]},
            look=lambda args: signed_in_page() if self.signed_in else sign_in_page(),
            act={"method": "invoke"}, allow={}, check={})


def hands_over(model_turns=(), reason="sign_in"):
    return [reply(call("computer_apps")), reply(call("computer_look", window=7)),
            reply(call("computer_hand_over", reason=reason)), *model_turns]


async def event(run, timeout=5):
    return await asyncio.wait_for(run.next_event(), timeout)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def seen_by_the_model(model):
    return json.dumps([c["messages"] for c in model.calls], ensure_ascii=False)


async def test_a_hand_over_pauses_the_run_and_takes_it_off_the_desktop(make_run, shell):
    Desktop(shell)
    model = FakeModel(*hands_over([reply(content="已上傳。")]))
    run = make_run(model, handback=True)
    run.start()
    paused = await event(run)
    assert isinstance(paused, Handback) and (paused.reason, paused.app, paused.note) == ("sign_in", "Google Chrome", "")
    assert run.owner_has_control and not run.done
    # The run took itself off the desktop: the overlay and the watch on the owner's input are down.
    assert [phase for phase, _ in shell.states][-1] == "end"
    assert shell.states[-1][1] == "《告。這一步需要主人親自操作。》"
    # It waits: no more of the run reaches the shell, and no model call is made.
    ops = list(shell.ops())
    calls = len(model.calls)
    await asyncio.sleep(0.05)
    assert shell.ops() == ops and len(model.calls) == calls and run.final is None
    run.stop("owner")
    await run.task


async def test_while_the_owner_holds_the_computer_nothing_reaches_the_shell_and_their_input_ends_nothing(make_run, shell):
    Desktop(shell)
    runs = ComputerRuns(shell.bridge)
    run = make_run(FakeModel(*hands_over()), handback=True)
    runs.begin(run)
    assert isinstance(await event(run), Handback)
    ops = list(shell.ops())
    # The lease is the run's before every request: a request now never leaves the core.
    for op in ("apps", "look", "act", "check", "allow"):
        with pytest.raises(LeaseHeld):
            await run._shell(op, {}, 1.0)
    assert shell.ops() == ops
    # The owner's own input is what the hand-over is for: it ends nothing, however often it comes.
    runs.interrupt()
    runs.interrupt()
    assert not run.done and run.owner_has_control
    # A request that is none of the run's business leaves it alone too.
    runs.stop_all("replaced", spare_owner=True)
    assert not run.done
    runs.stop_all("replaced")
    final = await event(run)
    assert isinstance(final, Final) and final.kind == "stopped"
    assert shell.ops() == ops


async def test_giving_the_computer_back_carries_on_the_same_run_once_and_repeats_nothing(make_run, shell):
    desktop = Desktop(shell)
    model = FakeModel(*hands_over([
        reply(call("computer_act", window=7, element=7, action="click", final=False)),
        reply(content="已選擇檔案，頁面上的確認碼是 YT-4F2A。"),
    ]))
    # one step before the pause
    model.turns.insert(2, reply(call("computer_act", window=7, element=2, action="focus", final=False)))
    run = make_run(model, handback=True)
    run.start()
    assert isinstance(await event(run), Handback)
    acts = shell.ops().count("act")
    assert acts == 1
    desktop.signed_in = True
    assert run.give_back() and not run.give_back()  # once: a replayed 我完成了 puts nobody in charge
    final = await event(run)
    assert (final.kind, final.status) == ("done", "done") and "YT-4F2A" in final.text
    # It looked again before it did anything, then went on from where it was: each step once.
    after = shell.ops()[shell.ops().index("allow") + 1:] if "allow" in shell.ops() else shell.ops()
    assert shell.ops().count("act") == 2
    states = [phase for phase, _ in shell.states]
    assert states[:2] == ["begin", "step"] and states.index("end") < len(states) - 1
    assert states.count("begin") == 2  # the run is the shell's again once the owner gave the computer back
    told = seen_by_the_model(model)
    assert "says it is done" in told and "上傳頁面" in told and "do not repeat it" in told
    # The model that asked for the sign-in sees the page as the owner left it, never the password.
    assert "hunter2" not in told
    assert not run.owner_has_control and not run.give_back()


async def test_the_run_looks_again_before_it_acts_and_the_ids_of_the_old_look_are_dead(make_run, shell):
    desktop = Desktop(shell)
    # After the hand-over the model uses an id of the look before (the old Sign in button): the shell
    # dropped it with the old look, the new page has no such element, and the step is never taken.
    model = FakeModel(*hands_over([
        reply(call("computer_act", window=7, element=4, action="click", final=False)),
        reply(content="好。"),
    ]))
    run = make_run(model, handback=True)
    run.start()
    assert isinstance(await event(run), Handback)
    before = len(shell.ops())
    desktop.signed_in = True
    run.give_back()
    await event(run)
    after = shell.ops()[before:]
    assert after[:2] == ["apps", "look"] and "act" not in after
    assert "There is no element with that id in the latest look" in seen_by_the_model(model)


async def test_a_look_that_still_shows_the_form_gives_the_computer_back_to_the_owner_once(make_run, shell):
    desktop = Desktop(shell)
    model = FakeModel(*hands_over([reply(content="頁面仍要求登入。")]))
    run = make_run(model, handback=True)
    run.start()
    first = await event(run)
    assert isinstance(first, Handback) and first.note == ""
    assert run.give_back()  # too early: the page still asks for the password
    again = await event(run)
    assert isinstance(again, Handback) and (again.id, again.note) == (first.id, "still")
    assert run.owner_has_control
    states = [phase for phase, _ in shell.states]
    assert states.count("end") == 2
    calls = len(model.calls)
    desktop.signed_in = True
    assert run.give_back()
    final = await event(run)
    assert final.kind == "done" and len(model.calls) == calls + 1
    # No one is trapped: when the second go still shows the form, the run carries on and the model reads it.
    desktop2 = Desktop(shell)
    shell.requests.clear()
    shell.states.clear()
    model2 = FakeModel(*hands_over([reply(content="頁面仍要求登入。")]))
    run2 = make_run(model2, handback=True)
    run2.start()
    assert isinstance(await event(run2), Handback)
    run2.give_back()
    assert (await event(run2)).note == "still"
    run2.give_back()
    final = await event(run2)
    assert final.kind == "done" and desktop2.signed_in is False


async def test_a_step_on_a_password_field_pauses_and_is_never_taken(make_run, shell):
    Desktop(shell)
    model = FakeModel(
        reply(call("computer_apps")), reply(call("computer_look", window=7)),
        reply(call("computer_act", window=7, element=3, action="type", text="hunter2", final=False)),
        reply(content="完成。"))
    run = make_run(model, handback=True)
    run.start()
    paused = await event(run)
    assert isinstance(paused, Handback) and paused.reason == "password"
    assert "act" not in shell.ops()
    assert run.take_steps()[-1].reason == "handoff"
    run.stop("owner")
    await run.task
    assert "hunter2" not in seen_by_the_model(model)


@pytest.mark.parametrize("label,reason", [("Sign in", "sign_in"), ("Card number", "payment"), ("Pay now", "payment")])
async def test_the_reason_a_step_was_the_owners_is_told_to_the_card(make_run, shell, label, reason):
    Desktop(shell)
    page = {**PAGE, "elements": [element(1, "Window", "Studio", depth=0, actions=[]), element(2, "Button", label)]}
    shell.handlers["look"] = page
    model = FakeModel(
        reply(call("computer_apps")), reply(call("computer_look", window=7)),
        reply(call("computer_act", window=7, element=2, action="click", final=False)))
    run = make_run(model, handback=True)
    run.start()
    paused = await event(run)
    assert isinstance(paused, Handback) and paused.reason == reason and "act" not in shell.ops()
    run.stop("owner")


@pytest.mark.parametrize("kind,reason", [("password", "password"), ("handoff", "sign_in")])
async def test_the_shell_saying_the_focus_is_the_owners_pauses_the_run_too(make_run, shell, kind, reason):
    Desktop(shell)
    shell.handlers["act"] = kind
    model = FakeModel(
        reply(call("computer_apps")), reply(call("computer_look", window=7)),
        reply(call("computer_act", window=7, action="keys", keys="ctrl+v", final=False)))
    run = make_run(model, handback=True)
    run.start()
    paused = await event(run)
    assert isinstance(paused, Handback) and paused.reason == reason
    run.stop("owner")


async def test_an_administrators_prompt_still_ends_the_run(make_run, shell):
    Desktop(shell)
    shell.handlers["act"] = "elevated"
    model = FakeModel(
        reply(call("computer_apps")), reply(call("computer_look", window=7)),
        reply(call("computer_act", window=7, action="keys", keys="ctrl+v", final=False)))
    run = make_run(model, handback=True)
    run.start()
    final = await event(run)
    assert isinstance(final, Final) and final.kind == "handoff"
    run2 = make_run(FakeModel(*hands_over(reason="admin")), handback=True)
    run2.start()
    assert (await event(run2)).kind == "handoff"


async def test_a_run_that_may_not_hand_back_ends_with_the_hand_off_line_as_before(make_run, shell):
    Desktop(shell)
    run = make_run(FakeModel(*hands_over()))
    run.start()
    final = await event(run)
    assert (final.kind, final.text) == ("handoff", "這一步需要主人親自操作。")
    assert not run.owner_has_control and not run.give_back()


async def test_stop_a_newer_run_the_time_and_closing_end_a_waiting_run_and_never_resume_it(make_run, shell):
    Desktop(shell)
    # Stop on the card, a newer computer task, the app closing, Esc
    for reason, kind in (("owner", "stopped"), ("replaced", "stopped"), ("closing", "stopped"), ("cancelled", "stopped")):
        run = make_run(FakeModel(*hands_over([reply(content="不應該到這裡。")])), handback=True)
        run.start()
        assert isinstance(await event(run), Handback)
        run.stop(reason)
        final = await event(run)
        assert (final.kind, final.status) == (kind, "denied")
        assert not run.give_back() and run.final is final
        assert shell.ops().count("allow") == 0
    # the time the owner may keep the computer runs out
    run = make_run(FakeModel(*hands_over([reply(content="不應該到這裡。")])), limits=RunLimits(handback_s=0.05), handback=True)
    run.start()
    assert isinstance(await event(run), Handback)
    final = await event(run)
    assert (final.kind, final.status) == ("expired", "expired") and not run.give_back()


async def test_a_give_back_after_the_run_ended_changes_nothing(make_run, shell):
    Desktop(shell)
    runs = ComputerRuns(shell.bridge)
    run = make_run(FakeModel(*hands_over()), handback=True)
    runs.begin(run)
    assert isinstance(await event(run), Handback)
    assert runs.give_back("0" * 32) is False and runs.find(run.run_id) is run
    runs.stop_run(run.run_id, "owner")
    assert (await event(run)).kind == "stopped"
    await run.task
    assert runs.give_back(run.run_id) is False and runs.find(run.run_id) is None


async def test_the_time_the_owner_holds_the_computer_is_not_the_runs(make_run, shell):
    desktop = Desktop(shell)
    clock = Clock()
    model = FakeModel(*hands_over([reply(content="完成。")]))
    run = make_run(model, limits=RunLimits(seconds=30.0), clock=clock, handback=True)
    run.start()
    assert isinstance(await event(run), Handback)
    clock.now += 3600.0  # an hour at the keyboard
    desktop.signed_in = True
    run.give_back()
    final = await event(run)
    assert (final.kind, final.status) == ("done", "done")


async def test_the_calls_after_the_one_that_handed_over_are_not_made(make_run, shell):
    desktop = Desktop(shell)
    model = FakeModel(
        reply(call("computer_apps")), reply(call("computer_look", window=7)),
        reply(call("computer_hand_over", reason="sign_in"), call("computer_look", window=7)),
        reply(content="完成。"))
    run = make_run(model, handback=True)
    run.start()
    assert isinstance(await event(run), Handback)
    looks = shell.ops().count("look")
    desktop.signed_in = True
    run.give_back()
    await event(run)
    last = model.calls[-1]["messages"]
    tool_messages = [m["content"] for m in last if m["role"] == "tool"][-2:]
    assert "says it is done" in tool_messages[0] and "Not made" in tool_messages[1]
    # the look after the hand-back is the run's own: one, not two
    assert shell.ops().count("look") == looks + 1


async def test_a_run_that_pauses_twice_hands_over_twice(make_run, shell):
    desktop = Desktop(shell)
    model = FakeModel(*hands_over([
        reply(call("computer_hand_over", reason="payment")),
        reply(content="付款完成。"),
    ]))
    run = make_run(model, handback=True)
    run.start()
    first = await event(run)
    desktop.signed_in = True
    run.give_back()
    second = await event(run)
    assert (first.reason, second.reason) == ("sign_in", "payment") and first.id != second.id
    run.give_back()
    assert (await event(run)).kind == "done"


async def test_nothing_the_screen_showed_is_logged_while_a_run_waits(make_run, shell, caplog):
    Desktop(shell)
    run = make_run(FakeModel(*hands_over()), handback=True)
    run.start()
    await event(run)
    run.stop("owner")
    await run.task
    assert "hunter2" not in caplog.text and "私人頻道" not in caplog.text
