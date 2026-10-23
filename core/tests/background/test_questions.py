"""Tests for asking from the background: a task whose call needs the owner's yes pauses, its question
waits in the app (never in a chat app) for at most a day, and the task goes on from where it was.

The worker is a real `Worker` joined to the host by two queues; the plugins are real ones; the model,
the judge of the safety rules and the clocks are fakes. No test spends money or touches the network.
"""

import json
import logging

import pytest

from raphael_core.agent.safety import ALLOW, ASK, DENY, SafetyGuard, SafetyRules
from raphael_core.agent.speaker import Speaker
from raphael_core.agent.tools import ToolResult
from raphael_core.background.run import OWNER_SAID_NO
from raphael_core.background.service import QUESTION_WAIT_S, continued
from raphael_core.protocol import INVALID_PARAMS, RpcError

from .conftest import ARGUMENT, TASK_TEXT, Clock, call, report, turn, until


class ScriptedJudge:
    """Says the rules in `matches` match every call it is shown."""

    def __init__(self, matches=()):
        self.matches = frozenset(matches)
        self.shown = []

    async def matching(self, proposed, rules, ledger):
        self.shown.append(proposed)
        return self.matches


class OneMember:
    """A team of one member, 3, as the background asks who a task's member is."""

    def speaker(self, member_id):
        return Speaker(id=3, name="進度官") if member_id == 3 else None

    def has_members(self):
        return True


async def no_team_tool(speaker, name, arguments, **_):
    return ToolResult(name, False, None, "blocked")


def questions(rig):
    return [params for method, params in rig.notifications if method == "background.question"]


async def paused(rig, task_id):
    await until(lambda: rig.task(task_id).state == "waiting")
    return questions(rig)[-1]


def note_then_report(rig, text="Noted."):
    rig.client.replies = [
        turn("Noting.", [call("Notes__note", {"text": ARGUMENT}, "c1")], cost=0.002),
        report(text, cost=0.003),
    ]


@pytest.fixture
def clock():
    return Clock(5000.0)


# --- the pause --------------------------------------------------------------------------------------


async def test_a_call_that_needs_a_yes_pauses_the_task_with_its_question(make_rig, plugins, calls, clock):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.submit()
    asked = await paused(rig, task_id)
    assert calls == [] and rig.reports() == []
    assert asked["task_id"] == task_id and asked["member_id"] == 0 and asked["state"] == "waiting"
    assert asked["prefix"] == "Question"
    assert asked["text"].startswith("Allow Notes to run note (") and ARGUMENT[:20] in asked["text"]
    assert asked["notice"] == {"prefix": "Notice", "text": "Something is waiting for you."}
    listed = (await rig.rpc.call("background.questions.list", {"member_id": 0}))["questions"]
    assert [q["id"] for q in listed] == [asked["question_id"]]
    assert listed[0]["expires_at_ms"] - listed[0]["created_at_ms"] == QUESTION_WAIT_S * 1000
    assert (await rig.rpc.call("background.questions.list", {"member_id": 7}))["questions"] == []
    # The app is told the task moved on, and that no task runs while it waits.
    last = rig.changes()[-1]
    assert (last["state"], last["running"]) == ("waiting", False)


async def test_a_paused_task_does_not_hold_the_worker(make_rig, plugins, calls, clock):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2", look="T0")
    note_then_report(rig)
    first = rig.submit()
    await paused(rig, first)
    rig.client.replies = [report("Second one done.")]
    second = rig.submit("another task")
    [done] = await rig.wait_for_reports()
    assert done["task_id"] == second and rig.task(first).state == "waiting"


async def test_the_member_of_a_task_is_carried_by_its_question(make_rig, plugins, clock):
    rig = make_rig(clock=clock)
    rig.service.use_member_names(lambda member_id: {3: "進度官"}.get(member_id))
    rig.service.use_team(OneMember(), no_team_tool)
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.service.submit(
        session_id="s1", text=TASK_TEXT, language="zh-TW", model="fake/model", member_id=3
    )
    asked = await paused(rig, task_id)
    assert asked["member_id"] == 3
    assert asked["notice"] == {"prefix": "告", "text": "進度官 等你處理。"}
    assert rig.service.waiting_members() == {3}


async def test_a_routines_run_pauses_too_and_is_never_asked_at_the_overlay(make_rig, plugins, calls, clock):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.service.submit(
        session_id="routine:1", text=TASK_TEXT, language="en", model="fake/model", routine_id=1
    )
    asked = await paused(rig, task_id)
    assert asked["state"] == "waiting" and calls == []
    # The overlay is only told, once, that something waits (no buttons there): no report is said.
    assert set(asked["notice"]) == {"prefix", "text"} and rig.reports() == []


# --- the answers ------------------------------------------------------------------------------------


async def test_a_yes_makes_the_call_and_the_task_goes_on_from_where_it_was(make_rig, plugins, calls, clock):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig, "Noted it.")
    task_id = rig.submit()
    asked = await paused(rig, task_id)
    clock.now += 3600  # an hour waiting is not the task's time
    answer = {"task_id": task_id, "question_id": asked["question_id"], "approved": True}
    assert await rig.rpc.call("background.answer", answer) == {"status": "approved"}
    [done] = await rig.wait_for_reports()
    assert (done["state"], done["text"]) == ("done", "Noted it.") and calls == ["note"]
    # The model went on with the conversation it had, and the result of the call that waited.
    second = rig.client.chat_calls[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[-1]["tool_call_id"] == "c1" and "noted" in second[-1]["content"]
    assert ("Notes", "note", None, "user", "ok", task_id) in plugins.audit()
    assert [q["state"] for q in questions(rig)] == ["waiting", "approved"]
    assert rig.service._store.question(asked["question_id"]).state == "approved"
    task = rig.task(task_id)
    assert (task.model_calls, task.tool_calls) == (2, 1)
    assert task.cost_usd == pytest.approx(0.005)
    # A second answer finds the question closed.
    assert await rig.rpc.call("background.answer", answer) == {"status": "approved"}


async def test_a_no_leaves_the_call_unmade_and_the_task_reports(make_rig, plugins, calls, clock):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig, "Did not note it.")
    task_id = rig.submit()
    asked = await paused(rig, task_id)
    answer = {"task_id": task_id, "question_id": asked["question_id"], "approved": False}
    assert await rig.rpc.call("background.answer", answer) == {"status": "denied"}
    [done] = await rig.wait_for_reports()
    assert done["text"] == "Did not note it." and calls == []
    told = rig.client.chat_calls[1]["messages"][-1]
    assert told["role"] == "tool" and OWNER_SAID_NO in told["content"]
    assert ("Notes", "note", None, "none", "denied", task_id) in plugins.audit()
    steps = [(s.tool, s.outcome, s.reason) for s in rig.steps(task_id) if s.kind == "tool"]
    assert steps == [("note", "blocked", "denied")]


async def test_a_question_nobody_answers_in_a_day_ends_the_task_politely(make_rig, plugins, calls, clock):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.submit()
    asked = await paused(rig, task_id)
    clock.now += QUESTION_WAIT_S - 1
    rig.service.sweep_questions()
    assert rig.task(task_id).state == "waiting"
    clock.now += 2
    rig.service.sweep_questions()
    [ended] = rig.reports()
    assert (ended["state"], ended["text"]) == (
        "cancelled",
        "No answer came within a day. The task ended without that action.",
    )
    task = rig.task(task_id)
    assert (task.state, task.reason) == ("cancelled", "expired") and calls == []
    assert questions(rig)[-1]["state"] == "expired"
    assert ("Notes", "note", None, "none", "expired", task_id) in plugins.audit()
    answer = {"task_id": task_id, "question_id": asked["question_id"], "approved": True}
    assert await rig.rpc.call("background.answer", answer) == {"status": "expired"}
    assert len(rig.client.chat_calls) == 1  # nothing more was spent


async def test_a_question_that_ran_out_while_another_was_answered_still_ends_its_task(
    make_rig, plugins, clock
):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    first = rig.submit()
    await paused(rig, first)
    rig.client.replies = [turn("", [call("Notes__note", {"text": "x"}, "c9")])]
    second = rig.submit("another task")
    await paused(rig, second)
    clock.now += QUESTION_WAIT_S + 1
    # Stopping the second answers its question, which sweeps the first one's out of the way.
    await rig.rpc.call("worker.cancel", {"task_id": second})
    rig.service.sweep_questions()
    assert (rig.task(first).state, rig.task(first).reason) == ("cancelled", "expired")


async def test_a_yes_from_a_chat_app_is_never_taken(make_rig, plugins, calls, clock):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.submit(session="telegram:42")
    asked = await paused(rig, task_id)
    for source in ("telegram", "voice", None, 1):
        with pytest.raises(RpcError) as error:
            await rig.rpc.call(
                "background.answer",
                {"task_id": task_id, "question_id": asked["question_id"], "approved": True, "source": source},
            )
        assert error.value.code == INVALID_PARAMS and error.value.data == {"kind": "not_here"}
    assert rig.task(task_id).state == "waiting" and calls == []
    assert len((await rig.rpc.call("background.questions.list"))["questions"]) == 1


async def test_a_wrong_or_missing_answer_is_refused_or_unknown(make_rig, plugins, clock):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.submit()
    asked = await paused(rig, task_id)
    for params in (
        {"task_id": "1", "question_id": "x", "approved": True},
        {"task_id": task_id, "question_id": "", "approved": True},
        {"task_id": task_id, "question_id": "x", "approved": "yes"},
    ):
        with pytest.raises(RpcError):
            await rig.rpc.call("background.answer", params)
    other = {"task_id": task_id, "question_id": "not-this-one", "approved": True}
    assert await rig.rpc.call("background.answer", other) == {"status": "unknown"}
    wrong_task = {"task_id": task_id + 1, "question_id": asked["question_id"], "approved": True}
    assert await rig.rpc.call("background.answer", wrong_task) == {"status": "unknown"}
    assert rig.task(task_id).state == "waiting"


async def test_a_waiting_task_can_be_stopped(make_rig, plugins, calls, clock):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.submit()
    asked = await paused(rig, task_id)
    assert await rig.rpc.call("worker.cancel", {"task_id": task_id}) == {"cancelled": True}
    [ended] = rig.reports()
    assert ended["state"] == "cancelled" and calls == []
    assert questions(rig)[-1]["state"] == "cancelled"
    answer = {"task_id": task_id, "question_id": asked["question_id"], "approved": True}
    assert await rig.rpc.call("background.answer", answer) == {"status": "cancelled"}


async def test_a_task_that_waited_when_the_core_ended_is_interrupted(make_rig, plugins, clock, conn):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.submit()
    asked = await paused(rig, task_id)
    await rig.service.aclose()
    assert rig.task(task_id).state == "interrupted"
    assert rig.service._store.question(asked["question_id"]).state == "cancelled"


async def test_clearing_the_history_takes_a_waiting_task_and_its_question(make_rig, plugins, clock, conn):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.submit()
    await paused(rig, task_id)
    rig.service.clear()
    assert rig.task(task_id) is None
    assert conn.execute("SELECT count(*) FROM worker_questions").fetchone()[0] == 0
    assert (await rig.rpc.call("background.questions.list"))["questions"] == []


# --- the owner's safety rules -----------------------------------------------------------------------


async def test_a_rule_that_asks_pauses_a_call_that_would_run_at_once(make_rig, plugins, calls, clock, conn):
    rules = SafetyRules(conn)
    rules.create("寫筆記", ASK)
    judge = ScriptedJudge({1})
    rig = make_rig(clock=clock, safety=SafetyGuard(rules, judge))
    await plugins.connect(note="T1")
    note_then_report(rig)
    task_id = rig.submit()
    asked = await paused(rig, task_id)
    assert asked["text"].startswith("This falls under the safety rule 「寫筆記」. Allow Notes to run note")
    assert judge.shown[0].tool == "Notes / note" and ARGUMENT in judge.shown[0].summary
    answer = {"task_id": task_id, "question_id": asked["question_id"], "approved": True}
    await rig.rpc.call("background.answer", answer)
    await rig.wait_for_reports()
    assert calls == ["note"] and len(judge.shown) == 1  # the yes is not judged again


async def test_a_rule_that_forbids_refuses_the_call_and_the_task_goes_on(make_rig, plugins, calls, clock, conn):
    rules = SafetyRules(conn)
    rules.create("寫筆記", DENY)
    rig = make_rig(clock=clock, safety=SafetyGuard(rules, ScriptedJudge({1})))
    await plugins.connect(note="T1")
    note_then_report(rig, "Could not note it.")
    task_id = rig.submit()
    [done] = await rig.wait_for_reports()
    assert done["text"] == "Could not note it." and calls == [] and questions(rig) == []
    told = rig.client.chat_calls[1]["messages"][-1]["content"]
    assert "(forbidden)" in told and "「寫筆記」 forbids this call" in told
    steps = [(s.tool, s.outcome, s.reason) for s in rig.steps(task_id) if s.kind == "tool"]
    assert steps == [("note", "blocked", "forbidden")]


async def test_allow_never_waives_the_question_of_a_tool_at_t2(make_rig, plugins, calls, clock, conn):
    rules = SafetyRules(conn)
    rules.create("寫筆記", ALLOW)
    rig = make_rig(clock=clock, safety=SafetyGuard(rules, ScriptedJudge({1})))
    await plugins.connect(note="T2")
    note_then_report(rig)
    task_id = rig.submit()
    asked = await paused(rig, task_id)
    assert asked["text"].startswith("Allow Notes to run note") and calls == []


async def test_a_tool_that_only_reads_is_not_judged_in_the_background(make_rig, plugins, calls, clock, conn):
    rules = SafetyRules(conn)
    rules.create("查資料", DENY)
    judge = ScriptedJudge({1})
    rig = make_rig(clock=clock, safety=SafetyGuard(rules, judge))
    await plugins.connect(look="T0")
    rig.client.replies = [turn("", [call("Notes__look", {"topic": "x"}, "c1")]), report("Found.")]
    rig.submit()
    await rig.wait_for_reports()
    assert calls == ["look"] and judge.shown == []


# --- what is kept and logged ------------------------------------------------------------------------


async def test_neither_the_logs_nor_the_tasks_hold_the_arguments(make_rig, plugins, clock, caplog, conn):
    rig = make_rig(clock=clock)
    await plugins.connect(note="T2")
    note_then_report(rig)
    with caplog.at_level(logging.DEBUG):
        task_id = rig.submit()
        asked = await paused(rig, task_id)
        await rig.rpc.call(
            "background.answer", {"task_id": task_id, "question_id": asked["question_id"], "approved": True}
        )
        await rig.wait_for_reports()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert ARGUMENT not in logged and TASK_TEXT not in logged and "Allow Notes" not in logged
    kept = json.dumps(
        [dict(r) for r in conn.execute("SELECT * FROM worker_tasks")]
        + [dict(r) for r in conn.execute("SELECT * FROM worker_steps")]
    )
    assert ARGUMENT not in kept
    # The question keeps only the start of what the call is given, as the card says it.
    row = rig.service._store.question(asked["question_id"])
    assert len(row.text) < 120


def test_the_conversation_goes_on_with_a_result_for_every_call_of_the_last_reply():
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "one", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "two", "arguments": "{}"}},
                {"id": "c", "type": "function", "function": {"name": "three", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "done a"},
    ]
    out = continued(messages, "b", "result of b")
    assert out[:4] == messages
    assert [(m["tool_call_id"], m["content"][:12]) for m in out[4:]] == [
        ("b", "result of b"),
        ("c", "<tool_result"),
    ]
    assert "not made" in out[5]["content"]


# --- adding a member from the background ------------------------------------------------------------


class Creating:
    """A team with nobody in it yet, and the team tools as the assistant makes them: it notes the
    members added."""

    def __init__(self):
        self.created = []

    def speaker(self, member_id):
        return Speaker() if member_id == 0 else None

    def has_members(self):
        return False

    async def tool(self, speaker, name, arguments, **_):
        self.created.append(arguments["name"])
        return ToolResult(name, True, {"created": arguments["name"]})


def create_then_report(rig):
    made = {"name": "MEMBER-NAME", "role": "r", "instructions": "i"}
    rig.client.replies = [
        turn("Adding.", [call("create_member", made, "c1")]),
        report("Added."),
    ]


@pytest.mark.parametrize("verdict", [ASK, DENY])
async def test_adding_a_member_is_judged_in_the_background(make_rig, clock, conn, verdict):
    rules = SafetyRules(conn)
    rules.create("新增成員", verdict)
    judge = ScriptedJudge({1})
    rig = make_rig(clock=clock, safety=SafetyGuard(rules, judge))
    team = Creating()
    rig.service.use_team(team, team.tool)
    create_then_report(rig)
    task_id = rig.submit()
    if verdict == DENY:
        [done] = await rig.wait_for_reports()
        assert done["text"] == "Added." and team.created == [] and questions(rig) == []
        steps = [(s.tool, s.outcome, s.reason) for s in rig.steps(task_id) if s.kind == "tool"]
        assert steps == [("create_member", "blocked", "forbidden")]
    else:
        asked = await paused(rig, task_id)
        assert asked["text"].startswith("This falls under the safety rule 「新增成員」.")
        assert team.created == []
        answer = {"task_id": task_id, "question_id": asked["question_id"], "approved": True}
        assert (await rig.rpc.call("background.answer", answer))["status"] == "approved"
        await rig.wait_for_reports()
        assert team.created == ["MEMBER-NAME"]
    assert judge.shown[0].tool == "create_member"
