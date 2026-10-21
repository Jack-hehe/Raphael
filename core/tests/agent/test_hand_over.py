"""Tests for the assistant handing a task to the background worker: what it answers, what it records,
and what it leaves to the worker. The worker here is a fake that notes how it is called; the real one
is tested in tests/background."""

import pytest

from raphael_core.agent.persona import PHRASES, phrase
from raphael_core.background.tasks import Refused

from .conftest import decision
from .test_plugin_model import Catalog, llm  # noqa: F401
from .test_service import make_rig, spoken  # noqa: F401


class FakeBackground:
    def __init__(self):
        self.submitted = []
        self.attached = []
        self.pruned = []
        self.cleared = 0
        self.refuse = None
        self.fail = None

    def submit(self, *, session_id, text, language, model):
        if self.refuse:
            raise Refused(self.refuse)
        if self.fail:
            raise self.fail
        self.submitted.append((session_id, text, language, model))
        return len(self.submitted)

    def attach_turn(self, task_id, turn_id):
        self.attached.append((task_id, turn_id))

    def prune(self, older_than_ms):
        self.pruned.append(older_than_ms)

    def clear(self):
        self.cleared += 1


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


async def test_without_a_worker_the_route_is_answered_as_before(make_rig, client):
    rig = make_rig()
    client.decisions.append(decision("delegate"))
    result = await rig.ask("do it", language="en")
    assert result["text"] == phrase("en", "delegate")
