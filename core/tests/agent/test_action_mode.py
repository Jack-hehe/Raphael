"""The global quick switch adds confirmation for mutations without weakening any rules."""

import pytest

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.ledger import Ledger
from raphael_core.agent.policy import Action, Confirmations, NotApproved, Tier
from raphael_core.agent.safety import ASK, Proposed
from raphael_core.brain.settings import SettingsStore
from .test_service import make_rig  # noqa: F401


def test_global_mode_is_live_and_reads_do_not_ask(conn):
    gate = ToolGate(conn=conn)
    settings = SettingsStore(conn)
    assert not gate.needs_confirmation("lock_screen")
    settings.set("action_mode", "ask")
    assert gate.needs_confirmation("lock_screen")
    assert not gate.needs_confirmation("local_time")
    with pytest.raises(NotApproved):
        gate.check(Action("lock_screen"), None)
    action = Action("lock_screen")
    confirmations = Confirmations()
    pending = confirmations.open("s", 1, action, Tier.T1, "en")
    approval = confirmations.resolve(pending.id, True).approval
    gate.check(action, approval)
    with pytest.raises(NotApproved):
        gate.check(action, approval)
    settings.set("action_mode", "direct")
    gate.check(Action("lock_screen"), None)


async def test_desktop_changes_ask_without_a_model_judge(conn):
    SettingsStore(conn).set("action_mode", "ask")
    gate = ToolGate(conn=conn)
    result = await gate.screen(Proposed("computer_act", "Click Save"), language="en", ledger=None)
    assert result.verdict == ASK


async def test_voice_command_waits_then_runs_once(make_rig, client, actions):
    rig = make_rig()
    rig.settings.set("action_mode", "ask")
    rig.keys.clear()
    result = await rig.ask("鎖定電腦")
    assert result["status"] == "confirm"
    assert actions.calls == []
    await rig.confirm(result["confirm"]["id"])
    assert actions.calls == [("lock_screen",)]


@pytest.mark.parametrize("mode", [True, "other", None])
def test_invalid_modes_are_refused(conn, mode):
    with pytest.raises((TypeError, ValueError)):
        SettingsStore(conn).set("action_mode", mode)
