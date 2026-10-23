"""Tests for the owner's safety rules on the team: adding a member is judged like a plugin's tool that
changes something (forbidden, or asked about first), and asking another is not judged. The model and
the judge are fakes."""

import pytest

from agent.conftest import FakeActions, FakeOpenRouter, decision, noul, tool_plan
from agent.test_service import Rig
from raphael_core.agent.gate import ToolGate
from raphael_core.agent.safety import ALLOW, ASK, DENY, SafetyGuard, SafetyJudge, SafetyRules
from raphael_core.brain.openrouter import Decision

from .test_answering import Team, member, raphael_said, said


@pytest.fixture
def client():
    return FakeOpenRouter()


@pytest.fixture
def rules(conn):
    return SafetyRules(conn)


@pytest.fixture
async def guarded(conn, tmp_path, client, rules):
    team = Team(conn, tmp_path)
    gate = ToolGate(None, conn=conn, safety=SafetyGuard(rules, SafetyJudge(client)))
    rig = Rig(conn, client, FakeActions(), team=team.members, memory=team.memory, gate=gate)
    yield rig, team
    await rig.aclose()
    await team.memory.aclose()


def judged(*matches, among=(1,)):
    answers = {f"rule_{rule_id}": noul(0.95 if rule_id in matches else 0.05) for rule_id in among}
    return Decision(answers=answers, cost_usd=0.00004, latency_ms=500)


CREATE = ("Raphael__create_member", {"name": "小行程", "role": "管行程", "instructions": "每天早上提醒我"})


async def test_a_rule_that_forbids_adding_a_member_keeps_it_from_being_added(guarded, rules, client):
    rig, team = guarded
    rules.create("新增成員", DENY)
    client.decisions += [decision("command"), judged(1)]
    client.plans.append(tool_plan(CREATE))
    reply = await rig.ask("幫我加一個管行程的成員")
    assert (reply["prefix"], reply["text"]) == ("否", "安全規則「新增成員」不准此操作。未執行。")
    assert team.members.store.by_name("小行程") is None
    judged_action = client.decide_calls[1]["state"]["action"]
    assert judged_action["tool"] == "create_member"


async def test_a_rule_that_asks_has_the_member_added_only_after_the_yes(guarded, rules, client):
    rig, team = guarded
    rules.create("新增成員", ASK)
    client.decisions += [decision("command"), judged(1)]
    client.plans.append(tool_plan(CREATE))
    asked = await rig.ask("幫我加一個管行程的成員")
    assert asked["status"] == "confirm"
    assert asked["confirm"]["text"].startswith("此操作符合安全規則「新增成員」。")
    assert team.members.store.by_name("小行程") is None
    done = await rig.confirm(asked["confirm"]["id"])
    assert (done["prefix"], done["text"]) == ("了", "已建立「小行程」。")
    assert team.members.store.by_name("小行程") is not None


async def test_a_no_to_adding_a_member_adds_nobody(guarded, rules, client):
    rig, team = guarded
    rules.create("新增成員", ASK)
    client.decisions += [decision("command"), judged(1)]
    client.plans.append(tool_plan(CREATE))
    asked = await rig.ask("幫我加一個管行程的成員")
    await rig.confirm(asked["confirm"]["id"], approved=False)
    assert team.members.store.by_name("小行程") is None


async def test_a_rule_that_allows_it_or_no_rule_that_matches_adds_it_at_once(guarded, rules, client):
    rig, team = guarded
    rules.create("新增成員", ALLOW)
    client.decisions += [decision("command"), judged(1)]
    client.plans.append(tool_plan(CREATE))
    reply = await rig.ask("幫我加一個管行程的成員")
    assert reply["text"] == "已建立「小行程」。"


async def test_asking_another_member_is_never_judged(guarded, rules, client):
    rig, team = guarded
    member(team)
    rules.create("問別人事情", DENY)
    client.decisions.append(decision("answer"))  # Raphael's request
    client.plans.append(tool_plan(("Raphael__ask_member", {"member": "小行程", "request": "明天呢？"})))
    client.decisions.append(decision("answer"))  # the member's flags
    client.replies.append(said("明天九點開會。", memory=[]))
    client.replies.append(raphael_said("小行程說明天九點開會。"))
    reply = await rig.ask("問小行程我明天有什麼行程")
    assert reply["text"] == "小行程說明天九點開會。"
    assert len(client.decide_calls) == 2  # the two routes, and no judge
