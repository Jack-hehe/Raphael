"""The owner's safety rules in a run on the desktop: each step is judged after the built-in checks,
which the rules never loosen. Against a fake shell, a fake model and a judge that answers from a
script."""

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.ledger import Ledger
from raphael_core.agent.safety import ALLOW, ASK, DENY, SafetyGuard, SafetyRules
from raphael_core.brain.spend import SpendTracker
from raphael_core.computer.run import ComputerRun

from .conftest import FakeModel, call, drive, reply
from .test_run import answering


class ScriptedJudge:
    """Says that the rules in `matches` match every step (None: it failed), and keeps what it was
    shown."""

    def __init__(self, matches):
        self.matches = matches
        self.shown = []

    async def matching(self, proposed, rules, ledger):
        self.shown.append(proposed)
        return None if self.matches is None else frozenset(self.matches)


def a_run(conn, shell, model, rules, judge):
    async def no_app(name):
        return None

    return ComputerRun(
        session_id="s-1",
        language="zh-TW",
        request="在小算盤按七",
        model="openai/gpt-6-luna",
        client=model,
        bridge=shell.bridge,
        gate=ToolGate(None, conn=conn, safety=SafetyGuard(rules, judge)),
        ledger=Ledger(SpendTracker(conn, now_ms=lambda: 1_000)),
        over_budget=lambda: False,
        open_app=no_app,
    )


def press_seven(final=False):
    return FakeModel(
        reply(call("computer_apps")),
        reply(call("computer_look", window=7)),
        reply(call("computer_act", window=7, element=3, action="click", final=final)),
        reply(content="好了。"),
    )


async def test_a_step_a_rule_forbids_is_not_taken_and_the_model_is_told(conn, shell):
    answering(shell, always=True)
    rules = SafetyRules(conn)
    rules.create("按計算機", DENY)
    model = press_seven()
    run = a_run(conn, shell, model, rules, ScriptedJudge({1}))
    asked, final = await drive(run)
    assert asked == [] and "act" not in shell.ops()
    told = next(m for m in model.calls[3]["messages"] if m["role"] == "tool" and "computer_act" in m["content"])
    assert "「按計算機」 forbids this step" in told["content"]
    assert [(s.tool, s.outcome, s.reason) for s in run.take_steps() if s.tool == "click"] == [
        ("click", "blocked", "forbidden")
    ]
    assert final.status == "done"


async def test_a_step_a_rule_asks_about_waits_for_the_yes_and_is_not_confirmed(conn, shell):
    answering(shell, always=True)
    rules = SafetyRules(conn)
    rules.create("按計算機", ASK)
    judge = ScriptedJudge({1})
    run = a_run(conn, shell, press_seven(), rules, judge)
    asked, final = await drive(run, [(True, False)])
    assert [q.text for q in asked] == ["此操作符合安全規則「按計算機」。是否點擊「七」？"]
    act = dict(shell.requests)["act"]
    # A yes a rule asked for is not the yes to a step that sends: the shell is not told it is one.
    assert act == {"window": 7, "element": 3, "action": "click"}
    assert judge.shown[0].tool == "computer_act" and '"element":"七"' in judge.shown[0].summary
    assert final.status == "done"


async def test_no_to_a_step_a_rule_asked_about_ends_the_run(conn, shell):
    answering(shell, always=True)
    rules = SafetyRules(conn)
    rules.create("按計算機", ASK)
    run = a_run(conn, shell, press_seven(), rules, ScriptedJudge({1}))
    asked, final = await drive(run, [(False, False)])
    assert len(asked) == 1 and final.status == "denied" and "act" not in shell.ops()


async def test_allow_never_waives_the_question_of_a_step_that_sends(conn, shell):
    answering(shell, always=True)
    rules = SafetyRules(conn)
    rules.create("按計算機", ALLOW)
    run = a_run(conn, shell, press_seven(final=True), rules, ScriptedJudge({1}))
    asked, final = await drive(run, [(True, False)])
    assert [q.text for q in asked] == ["是否點擊「七」？"]  # the built-in question, still asked
    assert dict(shell.requests)["act"]["confirmed"] is True
    assert final.status == "done"


async def test_a_judge_that_fails_has_the_step_asked_about(conn, shell):
    answering(shell, always=True)
    rules = SafetyRules(conn)
    rules.create("付款", ALLOW)
    run = a_run(conn, shell, press_seven(), rules, ScriptedJudge(None))
    asked, _ = await drive(run, [(True, False)])
    assert [q.text for q in asked] == ["無法比對安全規則。是否點擊「七」？"]
