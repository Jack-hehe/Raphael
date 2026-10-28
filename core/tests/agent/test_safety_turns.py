"""Tests for the owner's safety rules in the turns of the fast tier: the plugins' tools are judged
before they are done; what only reads is not, nor are Raphael's commands for this computer. The judge
(Jev) and the model are fakes; the plugins are real ones that live in the test.
"""

import pytest

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.safety import ALLOW, ASK, DENY, SafetyGuard, SafetyJudge, SafetyRules
from raphael_core.brain.openrouter import Decision

from .conftest import decision, noul, tool_plan
from .test_plugins import Plugins, calls, plugins  # noqa: F401  (fixtures)
from .test_service import make_rig, spoken  # noqa: F401  (make_rig is the fixture the rig uses)


@pytest.fixture
def rules(conn):
    return SafetyRules(conn)


@pytest.fixture
def guarded(make_rig, plugins, rules, conn, client):
    """An assistant whose gate reads the owner's rules, with the plugins of the test."""
    gate = ToolGate(plugins.hub, conn=conn, safety=SafetyGuard(rules, SafetyJudge(client)))
    return make_rig(plugins=plugins.hub, gate=gate)


def judged(*matches, among=(1,)):
    answers = {f"rule_{rule_id}": noul(0.95 if rule_id in matches else 0.05) for rule_id in among}
    return Decision(answers=answers, cost_usd=0.00004, latency_ms=500)


# --- the commands ----------------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", [DENY, ASK, ALLOW])
async def test_a_command_for_this_computer_is_never_judged(guarded, rules, client, actions, verdict):
    # T1, local and easily undone: no paid call and no wait in front of 「靜音」, whatever the rules say.
    rules.create("調整音量", verdict)
    client.decisions.append(decision("command", "volume_mute"))
    reply = await guarded.ask("靜音")
    assert (reply["status"], reply["prefix"]) == ("done", "了")
    assert actions.calls == [("press_key", "volume_mute", 1)]
    assert len(client.decide_calls) == 1  # the route only


# --- the plugins' tools ----------------------------------------------------------------------------


async def test_a_tool_a_rule_forbids_is_not_called(guarded, rules, plugins, client, calls):
    await plugins.connect(note="T1")
    rules.create("寫筆記", DENY)
    client.decisions += [decision("answer"), judged(1)]
    client.plans.append(tool_plan(("Notes__note", {"text": "x"})))
    reply = await guarded.ask("幫我記一下")
    assert reply["text"] == "安全規則「寫筆記」不准此操作。未執行。" and calls == []
    assert client.decide_calls[1]["state"]["action"]["tool"] == "Notes / note"


async def test_a_tool_a_rule_asks_about_waits_for_the_yes(guarded, rules, plugins, client, calls):
    await plugins.connect(note="T1")
    rules.create("寫筆記", ASK)
    client.decisions += [decision("answer"), judged(1)]
    client.plans.append(tool_plan(("Notes__note", {"text": "x"})))
    asked = await guarded.ask("幫我記一下")
    assert asked["status"] == "confirm" and calls == []
    assert asked["confirm"]["text"].startswith("此操作符合安全規則「寫筆記」。是否讓「Notes」執行「note」")
    client.replies.append(spoken("答", "記好了。"))
    done = await guarded.call("agent.confirm", {"id": asked["confirm"]["id"], "approved": True})
    assert done["text"] == "記好了。" and calls == ["note"]


async def test_allow_never_waives_the_question_of_a_tool_that_needs_a_yes(
    guarded, rules, plugins, client, calls
):
    await plugins.connect(note="T2")
    rules.create("寫筆記", ALLOW)
    client.decisions += [decision("answer"), judged(1)]
    client.plans.append(tool_plan(("Notes__note", {"text": "x"})))
    asked = await guarded.ask("幫我記一下")
    assert asked["status"] == "confirm" and calls == []
    assert asked["confirm"]["text"].startswith("是否讓「Notes」執行「note」")  # the tier's own question


async def test_a_tool_that_only_reads_is_never_judged(guarded, rules, plugins, client, calls):
    await plugins.connect(look="T1")
    rules.create("查資料", DENY)
    client.decisions.append(decision("answer"))
    client.plans.append(tool_plan(("Notes__look", {"topic": "x"})))
    client.replies.append(spoken("答", "查到了。"))
    assert (await guarded.ask("查一下"))["text"] == "查到了。"
    assert calls == ["look"] and len(client.decide_calls) == 1  # the route only


async def test_a_judge_that_fails_has_the_tool_asked_about(guarded, rules, plugins, client, calls):
    await plugins.connect(note="T1")
    rules.create("付款", ALLOW)
    client.decisions += [decision("answer"), Decision(answers={}, cost_usd=0.0, latency_ms=1)]
    client.plans.append(tool_plan(("Notes__note", {"text": "x"})))
    asked = await guarded.ask("幫我記一下")
    assert asked["status"] == "confirm" and calls == []
    assert asked["confirm"]["text"].startswith("無法比對安全規則。")
