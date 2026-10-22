"""Tests for the assistant handing a task to the background worker: what it answers, what it records,
and what it leaves to the worker. The worker here is a fake that notes how it is called; the real one
is tested in tests/background."""

import pytest

from raphael_core.agent.persona import PHRASES, phrase

from .conftest import FakeBackground, decision
from .test_plugin_model import Catalog, llm  # noqa: F401
from .test_service import make_rig, spoken  # noqa: F401


@pytest.fixture
def background():
    return FakeBackground()


@pytest.fixture
def rig(make_rig, background):
    return make_rig(background=background)


@pytest.mark.parametrize(("language", "prefix"), [("zh-TW", "了"), ("en", "Understood"), ("ja", "了")])
async def test_a_task_is_handed_over_and_answered_at_once_with_the_handoff_line(
    rig, client, background, language, prefix
):
    client.decisions.append(decision("delegate", cost=0.00004))
    result = await rig.ask("查東京和大阪明天的天氣", language=language)
    assert result["status"] == "done"
    assert (result["prefix"], result["text"]) == (prefix, PHRASES[language].handoff)
    assert background.submitted == [("s1", "查東京和大阪明天的天氣", language, "openai/gpt-6-luna")]
    # Only the router was asked: no model words the handoff and nothing is looked up.
    assert client.json_calls == [] and client.plan_calls == [] and client.text_calls == []
    assert rig.spent() == [("agent", "typesafe/jev-1.14", 0.00004)]


async def test_the_turn_of_the_history_is_the_handoff_and_the_task_is_tied_to_it(
    rig, client, background
):
    client.decisions.append(decision("delegate"))
    result = await rig.ask("do the long thing", language="en")
    (turn,) = await rig.turns()
    assert (turn["id"], turn["route"], turn["status"]) == (result["turn_id"], "delegate", "done")
    assert (turn["question"], turn["prefix"], turn["text"]) == (
        "do the long thing", "Understood", "Handed to the background.",
    )  # fmt: skip
    assert background.attached == [(1, result["turn_id"])]
    # The history shows the task with its turn, and the worker's steps after the turn's own.
    assert turn["task"] == {"id": 1, "state": "running", "started_at_ms": 5}
    assert [step["tool"] for step in turn["steps"]] == ["look"]


def some_briefs():
    from raphael_core.background.brief import TaskBrief, ToolUse

    return [
        TaskBrief(2, "send the report to Ann", "running", 4, (ToolUse("Gmail", "send", "cut_off"),), None, None),
        TaskBrief(1, "look up tea", "done", 3, (), "Found three kinds.", 5),
    ]  # fmt: skip


async def test_the_router_and_the_answer_know_about_the_background_without_another_model_call(
    rig, client, background
):
    background.briefs = some_briefs()
    client.decisions.append(decision("answer"))
    client.replies.append(spoken("答", "還在做。"))
    await rig.ask("寄出去了嗎", language="zh-TW")
    [call] = client.decide_calls
    assert call["state"]["background"] == "1 running (step 4, last: Gmail send), 1 done 5 min ago"
    [answer] = client.json_calls
    assert "<background_tasks>" in answer["user"] and "send the report to Ann" in answer["user"]
    assert "Gmail / send: cut_off (unknown whether it ran)" in answer["user"]
    assert background.submitted == []  # not handed over again
    assert len(client.decide_calls) == 1 and client.plan_calls == []  # one router call, one answer


async def test_nothing_about_the_background_is_added_when_there_are_no_tasks(rig, client, background):
    client.decisions.append(decision("answer"))
    client.replies.append(spoken("答", "好。"))
    await rig.ask("你好嗎", language="zh-TW")
    assert "background" not in client.decide_calls[0]["state"]
    assert "background_tasks" not in client.json_calls[0]["user"]


async def test_a_background_that_cannot_be_read_does_not_stop_the_request(rig, client, background):
    def broken(limit=3):
        raise RuntimeError("PRIVATE")

    background.brief_for_assistant = broken
    client.decisions.append(decision("answer"))
    client.replies.append(spoken("答", "好。"))
    result = await rig.ask("你好嗎", language="zh-TW")
    assert result["text"] == "好。" and "PRIVATE" not in str(result)


async def test_a_queue_that_is_full_or_a_worker_that_is_off_is_a_refusal_with_the_reason(
    rig, client, background
):
    for kind in ("worker_busy", "worker_disabled"):
        background.refuse = kind
        client.decisions.append(decision("delegate"))
        result = await rig.ask("again", language="en")
        assert (result["prefix"], result["text"]) == ("Negative", phrase("en", kind))
    assert background.attached == [] and background.submitted == []


async def test_a_worker_that_cannot_take_a_task_is_a_refusal_and_not_a_raise(rig, client, background):
    background.fail = RuntimeError("the database is gone: PRIVATE")
    client.decisions.append(decision("delegate"))
    result = await rig.ask("again", language="en")
    assert (result["prefix"], result["text"]) == ("Negative", phrase("en", "error"))
    assert "PRIVATE" not in str(result)


async def test_a_model_that_takes_no_tools_cannot_work_in_the_background(make_rig, client, background):
    catalog = Catalog(llm("openai/gpt-6-luna", tools=False))
    rig = make_rig(background=background, models=catalog)
    client.decisions.append(decision("delegate"))
    result = await rig.ask("do it", language="en")
    assert (result["prefix"], result["text"]) == ("Negative", phrase("en", "worker_model"))
    assert background.submitted == []


async def test_only_the_delegate_route_goes_to_the_worker(rig, client, background, actions):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "hello"))
    await rig.ask("hello", language="zh-TW")
    client.decisions.append(decision("command", "lock_screen"))
    await rig.ask("lock", language="zh-TW")
    assert background.submitted == [] and actions.calls == [("lock_screen",)]


async def test_esc_does_not_stop_the_background(rig, client, background):
    client.decisions.append(decision("delegate"))
    await rig.ask("do it", language="en", session="s9")
    await rig.call("agent.cancel", {"session_id": "s9"})
    assert background.submitted and background.attached  # nothing was taken back


async def test_the_old_tasks_go_with_the_history_when_it_is_pruned_and_cleared(rig, client, background):
    client.decisions.append(decision("delegate"))
    await rig.ask("do it", language="en")
    assert await rig.call("agent.history.clear") == {"deleted": 1}
    assert background.cleared == 1
    assert background.pruned  # the retention was applied when the service started
