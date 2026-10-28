"""Tests for the policy: which tools may run at once, and what must be answered with a yes first."""

import uuid

import pytest

from raphael_core.agent.policy import (
    CONFIRM_TIMEOUT_MS,
    CONFIRM_TIMEOUT_S,
    UNATTENDED_WAIT_MS,
    UNATTENDED_WAIT_S,
    Action,
    Approval,
    Confirmations,
    NotApproved,
    Outcome,
    Tier,
    ToolRegistry,
    UnknownTool,
    action_digest,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def registry():
    return ToolRegistry({"look": Tier.T0, "tweak": Tier.T1, "write": Tier.T2, "burn": Tier.T3})


@pytest.fixture
def confirmations(clock):
    return Confirmations(clock)


def open_one(confirmations, tool="write", session="s1", turn=1, tier=Tier.T2, language="en"):
    return confirmations.open(session, turn, Action(tool, {"text": "x"}), tier, language)


# --- the tiers -----------------------------------------------------------------------------------


def test_the_tiers_run_from_reading_to_what_cannot_be_undone():
    assert [tier.name for tier in Tier] == ["T0", "T1", "T2", "T3"]
    assert Tier.T0 < Tier.T1 < Tier.T2 < Tier.T3


def test_every_tool_has_a_tier(registry):
    assert [registry.tier(name) for name in ("look", "tweak", "write", "burn")] == list(Tier)


def test_a_tool_nobody_registered_is_unknown_and_never_runs(registry):
    with pytest.raises(UnknownTool):
        registry.tier("format_disk")
    with pytest.raises(UnknownTool):
        registry.needs_confirmation("format_disk")
    with pytest.raises(UnknownTool):
        registry.check(Action("format_disk"), None)


def test_reading_and_reversible_changes_run_at_once(registry):
    assert not registry.needs_confirmation("look")
    assert not registry.needs_confirmation("tweak")
    registry.check(Action("look"), None)
    registry.check(Action("tweak"), None)


def test_a_change_of_state_and_an_irreversible_one_need_a_yes(registry):
    assert registry.needs_confirmation("write")
    assert registry.needs_confirmation("burn")


@pytest.mark.parametrize("tool", ["write", "burn"])
def test_nothing_at_t2_or_above_runs_without_an_approval(registry, tool):
    with pytest.raises(NotApproved):
        registry.check(Action(tool, {"text": "x"}), None)


def test_the_registry_can_be_grown_but_not_a_tool_registered_twice(registry):
    registry.register("copy", Tier.T2)
    assert registry.tier("copy") is Tier.T2
    with pytest.raises(ValueError):
        registry.register("copy", Tier.T0)  # a tool cannot be made safer by registering it again
    assert registry.tier("copy") is Tier.T2


# --- the approval --------------------------------------------------------------------------------


def test_an_approval_cannot_be_made_by_hand():
    with pytest.raises(TypeError):
        Approval()
    with pytest.raises(TypeError):
        Approval("id", Action("write"))


def test_an_approval_lets_its_own_action_run_and_no_other(registry, confirmations):
    pending = open_one(confirmations, "write")
    other = open_one(confirmations, "write", turn=2)
    approval = confirmations.resolve(pending.id, True).approval
    registry.check(pending.action, approval)  # the action it was given for
    with pytest.raises(NotApproved):
        registry.check(other.action, approval)  # not an action that looks the same
    with pytest.raises(NotApproved):
        registry.check(Action("write", {"text": "x"}), approval)  # nor an equal one made anew
    with pytest.raises(NotApproved):
        registry.check(Action("burn"), approval)


def test_an_approval_that_is_not_one_is_refused(registry):
    with pytest.raises(NotApproved):
        registry.check(Action("write"), object())  # type: ignore[arg-type]
    with pytest.raises(NotApproved):
        registry.check(Action("write"), True)  # type: ignore[arg-type]


# --- opening a confirmation ----------------------------------------------------------------------


def test_a_confirmation_has_a_uuid_the_tier_and_thirty_seconds(confirmations):
    pending = open_one(confirmations, tier=Tier.T3, session="s9", turn=42, language="ja")
    assert str(uuid.UUID(pending.id)) == pending.id  # a uuid
    assert (pending.session_id, pending.turn_id, pending.tier, pending.language) == (
        "s9",
        42,
        Tier.T3,
        "ja",
    )
    assert pending.action.tool == "write"
    assert CONFIRM_TIMEOUT_S == 30.0 and CONFIRM_TIMEOUT_MS == 30_000


def test_every_confirmation_has_an_id_of_its_own(confirmations):
    ids = {open_one(confirmations, turn=n).id for n in range(50)}
    assert len(ids) == 50


# --- answering it --------------------------------------------------------------------------------


def test_yes_approves_the_action_once(confirmations):
    pending = open_one(confirmations)
    result = confirmations.resolve(pending.id, True)
    assert result.outcome is Outcome.APPROVED
    assert result.pending is pending
    assert isinstance(result.approval, Approval)
    again = confirmations.resolve(pending.id, True)  # a second yes does not run it a second time
    assert again.outcome is Outcome.UNKNOWN and again.approval is None


def test_no_denies_it_and_gives_no_approval(confirmations):
    pending = open_one(confirmations)
    result = confirmations.resolve(pending.id, False)
    assert result.outcome is Outcome.DENIED and result.pending is pending
    assert result.approval is None
    assert confirmations.resolve(pending.id, True).outcome is Outcome.UNKNOWN  # gone


@pytest.mark.parametrize("confirm_id", ["", "nope", "00000000-0000-0000-0000-000000000000", None, 5])
def test_an_id_nobody_issued_is_unknown(confirmations, confirm_id):
    open_one(confirmations)
    result = confirmations.resolve(confirm_id, True)
    assert result.outcome is Outcome.UNKNOWN
    assert result.pending is None and result.approval is None


def test_a_yes_for_one_confirmation_does_not_approve_another(confirmations):
    first, second = open_one(confirmations, turn=1), open_one(confirmations, turn=2)
    confirmations.resolve(first.id, True)
    assert confirmations.resolve(second.id, False).outcome is Outcome.DENIED


# --- thirty seconds ------------------------------------------------------------------------------


def test_a_yes_within_thirty_seconds_counts(confirmations, clock):
    pending = open_one(confirmations)
    clock.now += 29.9
    assert confirmations.resolve(pending.id, True).outcome is Outcome.APPROVED


def test_a_yes_after_thirty_seconds_is_too_late_and_gives_no_approval(confirmations, clock):
    pending = open_one(confirmations)
    clock.now += CONFIRM_TIMEOUT_S
    result = confirmations.resolve(pending.id, True)
    assert result.outcome is Outcome.EXPIRED
    assert result.pending is pending  # the language it was asked in is known
    assert result.approval is None
    # Still too late the second time, and never a yes.
    assert confirmations.resolve(pending.id, True).outcome is Outcome.EXPIRED


def test_a_no_after_thirty_seconds_is_expired_too(confirmations, clock):
    pending = open_one(confirmations)
    clock.now += 31
    assert confirmations.resolve(pending.id, False).outcome is Outcome.EXPIRED


def test_sweeping_takes_out_the_confirmations_that_ran_out_and_leaves_the_others(
    confirmations, clock
):
    old = open_one(confirmations, turn=1)
    clock.now += 20
    young = open_one(confirmations, turn=2)
    clock.now += 11  # old is 31 s old, young 11
    assert confirmations.sweep() == [old]
    assert confirmations.sweep() == []  # once
    assert confirmations.resolve(old.id, True).outcome is Outcome.EXPIRED
    assert confirmations.resolve(young.id, True).outcome is Outcome.APPROVED


def test_the_confirmations_that_ran_out_are_remembered_only_so_far(confirmations, clock):
    first = open_one(confirmations, turn=0)
    clock.now += 31
    confirmations.sweep()
    for number in range(1, 100):
        open_one(confirmations, turn=number)
        clock.now += 31
        confirmations.sweep()
    assert confirmations.resolve(first.id, True).outcome is Outcome.UNKNOWN  # forgotten, not yes


# --- cancelling ----------------------------------------------------------------------------------


def test_cancelling_a_session_denies_its_confirmations_and_only_those(confirmations):
    mine = open_one(confirmations, session="s1", turn=1)
    also_mine = open_one(confirmations, session="s1", turn=2)
    theirs = open_one(confirmations, session="s2", turn=3)
    assert confirmations.cancel_session("s1") == [mine, also_mine]
    assert confirmations.resolve(mine.id, True).outcome is Outcome.UNKNOWN  # no yes comes late
    assert confirmations.resolve(theirs.id, True).outcome is Outcome.APPROVED
    assert confirmations.cancel_session("s1") == []


def test_cancelling_all_denies_every_confirmation(confirmations):
    a, b = open_one(confirmations, session="s1"), open_one(confirmations, session="s2")
    assert confirmations.cancel_all() == [a, b]
    assert confirmations.resolve(a.id, True).outcome is Outcome.UNKNOWN
    assert confirmations.resolve(b.id, True).outcome is Outcome.UNKNOWN
    assert confirmations.cancel_all() == []


def test_cancelling_where_denies_only_the_picked_confirmations(confirmations):
    a, b = open_one(confirmations, session="s1", turn=1), open_one(confirmations, session="s2", turn=2)
    assert confirmations.cancel_where(lambda pending: pending.turn_id == 1) == [a]
    assert confirmations.resolve(a.id, True).outcome is Outcome.UNKNOWN
    assert confirmations.resolve(b.id, True).outcome is Outcome.APPROVED
    assert confirmations.cancel_where(lambda pending: True) == []


def test_a_confirmation_that_ran_out_is_not_returned_by_a_cancel(confirmations, clock):
    pending = open_one(confirmations)
    clock.now += 40
    assert confirmations.cancel_session("s1") == []
    assert confirmations.resolve(pending.id, True).outcome is Outcome.EXPIRED


def test_the_number_waiting_is_counted(confirmations):
    assert confirmations.waiting == 0
    pending = open_one(confirmations)
    open_one(confirmations, turn=2)
    assert confirmations.waiting == 2
    confirmations.resolve(pending.id, False)
    assert confirmations.waiting == 1


def test_one_yes_lets_an_action_run_once(registry, confirmations):
    pending = open_one(confirmations, "write")
    approval = confirmations.resolve(pending.id, True).approval
    registry.check(pending.action, approval)
    with pytest.raises(NotApproved):
        registry.check(pending.action, approval)  # not a second time


# --- a yes is for one digest (P08c) --------------------------------------------------------------


def test_the_digest_is_of_the_tool_and_exactly_what_it_is_given():
    one = Action("write", {"to": "a@b.c", "subject": "hi"})
    assert one.digest == action_digest("write", {"subject": "hi", "to": "a@b.c"})  # the order does not matter
    assert one.digest != Action("write", {"to": "a@b.c", "subject": "ho"}).digest
    assert one.digest != Action("burn", {"to": "a@b.c", "subject": "hi"}).digest
    assert len(one.digest) == 64 and int(one.digest, 16) >= 0


def test_a_confirmation_keeps_the_digest_of_its_action_when_it_was_asked(confirmations):
    pending = open_one(confirmations)
    assert pending.digest == pending.action.digest
    assert confirmations.resolve(pending.id, True).approval.digest == pending.digest


def test_inputs_changed_after_the_question_never_use_the_yes(registry, confirmations):
    args = {"text": "x"}
    action = Action("write", args)
    pending = confirmations.open("s1", 1, action, Tier.T2, "en")
    approval = confirmations.resolve(pending.id, True).approval
    args["text"] = "something else"  # the inputs change between the question and the call
    with pytest.raises(NotApproved):
        registry.check(action, approval)
    assert approval.spent is False  # refused, not used up
    args["text"] = "x"  # put back, it is what was said yes to
    registry.check(action, approval)


def test_an_answer_of_another_digest_changes_nothing_and_the_question_still_waits(confirmations):
    pending = open_one(confirmations)
    stale = confirmations.resolve(pending.id, True, "0" * 64)
    assert stale.outcome is Outcome.CHANGED and stale.approval is None
    assert confirmations.waiting == 1
    assert confirmations.resolve(pending.id, False, "0" * 64).outcome is Outcome.CHANGED
    assert confirmations.resolve(pending.id, True, pending.digest).outcome is Outcome.APPROVED


def test_a_replayed_yes_finds_nothing(confirmations, registry):
    pending = open_one(confirmations)
    first = confirmations.resolve(pending.id, True, pending.digest)
    registry.check(pending.action, first.approval)
    again = confirmations.resolve(pending.id, True, pending.digest)
    assert again.outcome is Outcome.UNKNOWN and again.approval is None


def test_a_core_that_started_again_has_no_questions_so_an_old_yes_finds_nothing(clock):
    before = Confirmations(clock)
    pending = open_one(before)
    after = Confirmations(clock)  # the questions live in memory only
    assert after.resolve(pending.id, True, pending.digest).outcome is Outcome.UNKNOWN


def test_a_question_nobody_sits_at_waits_ten_minutes_not_thirty_seconds(confirmations, clock):
    assert UNATTENDED_WAIT_S == 600.0 and UNATTENDED_WAIT_MS == 600_000
    attended = open_one(confirmations, turn=1)
    unattended = confirmations.open("s1", 2, Action("write", {"text": "x"}), Tier.T2, "en", wait_s=UNATTENDED_WAIT_S)
    clock.now += CONFIRM_TIMEOUT_S + 1
    assert confirmations.sweep() == [attended]
    clock.now += UNATTENDED_WAIT_S - CONFIRM_TIMEOUT_S - 2
    result = confirmations.resolve(unattended.id, True, unattended.digest)
    assert result.outcome is Outcome.APPROVED  # 599 s: still in time
    late = confirmations.open("s1", 3, Action("write", {"text": "y"}), Tier.T2, "en", wait_s=UNATTENDED_WAIT_S)
    clock.now += UNATTENDED_WAIT_S
    expired = confirmations.resolve(late.id, True, late.digest)
    assert expired.outcome is Outcome.EXPIRED and expired.approval is None
