"""Tests for what a turn keeps of what Raphael did for it: the steps the conversation view shows
between the question and the answer, where the request came from, and the notice to the app.

A step is a tool, its server, how it ended, how long it took and, when it failed, a short reason;
never an argument, a result or a message of the service."""

import json

import pytest

from raphael_core.protocol import INVALID_PARAMS, RpcError

from .conftest import decision, tool_plan
from .test_plugins import ARGUMENT, ask_for_note, calls, plugins, route  # noqa: F401 (fixtures)
from .test_service import make_rig, spoken  # noqa: F401 (make_rig is the fixture the rig uses)

STEP_KEYS = {"kind", "tool", "server", "outcome", "ms", "reason", "at_ms", "detail"}


@pytest.fixture
def heard():
    return []


@pytest.fixture
def rig(make_rig, plugins, heard):
    return make_rig(plugins=plugins.hub, notify=lambda method, params: heard.append((method, params)))


def brief(steps):
    return [(s["kind"], s["tool"], s["server"], s["outcome"], s["reason"]) for s in steps]


async def test_a_turn_lists_where_it_came_from_what_it_cost_and_the_model(rig, client):
    route(client)
    client.replies.append(spoken("答", "好。"))
    await rig.ask("你好")
    (turn,) = await rig.turns()
    assert turn["source"] == "voice" and turn["session_id"] == "s1"
    assert turn["model"] == "openai/gpt-6-luna" and turn["cost_usd"] > 0
    assert turn["steps"] == [] and turn["task"] is None


async def test_a_request_from_a_chat_app_says_so(rig, client):
    route(client, "chat")
    client.replies.append(spoken("答", "嗨。"))
    await rig.call(
        "agent.ask",
        {"session_id": "tg-42", "text": "hi", "ui_language": "en", "source": "telegram"},
    )
    assert (await rig.turns())[0]["source"] == "telegram"


@pytest.mark.parametrize("source", [5, "", "Telegram", "tele gram", "x" * 21])
async def test_a_source_that_is_not_a_short_word_is_refused(rig, source):
    with pytest.raises(RpcError) as e:
        await rig.call(
            "agent.ask", {"session_id": "s", "text": "x", "ui_language": "en", "source": source}
        )
    assert e.value.code == INVALID_PARAMS


async def test_the_lookups_and_the_tools_of_a_turn_are_its_steps(rig, plugins, client, calls):
    await plugins.connect(look="T0", broken="T0")
    client.decisions.append(decision("answer", weather=0.9))
    client.plans.append(tool_plan(("Notes__look", {"topic": ARGUMENT}), ("Notes__broken", {})))
    client.replies.append(spoken("答", "好。"))
    await rig.ask("台北天氣，還有查筆記")
    (turn,) = await rig.turns()
    assert sorted(brief(turn["steps"])) == [
        ("tool", "broken", "Notes", "error", "tool_error"),
        ("tool", "look", "Notes", "ok", None),
        ("tool", "weather", "Raphael", "ok", None),
    ]
    for step in turn["steps"]:
        assert set(step) == STEP_KEYS and step["ms"] >= 0 and step["at_ms"] > 0
    # What the tools were given and what they said stay out of the steps.
    assert ARGUMENT not in json.dumps(turn["steps"]) and "secret" not in json.dumps(turn["steps"])


async def test_a_lookup_that_fails_is_a_step_with_its_reason(rig, client):
    rig.meteo.down = True
    client.decisions.append(decision("answer", weather=0.9))
    client.replies.append(spoken("否", "查不到。"))
    await rig.ask("台北天氣如何")
    (turn,) = await rig.turns()
    assert brief(turn["steps"]) == [("tool", "weather", "Raphael", "error", "network")]


async def test_a_command_is_a_step(rig, client, actions):
    client.decisions.append(decision("command", "volume_mute"))
    await rig.ask("靜音")
    (turn,) = await rig.turns()
    assert brief(turn["steps"]) == [("command", "volume_mute", None, "ok", None)]


async def test_the_call_made_after_a_yes_is_added_to_the_turn_that_asked(rig, plugins, client, calls):
    asked = await ask_for_note(rig, plugins, client, "T2")
    assert (await rig.turns())[0]["steps"] == []  # nothing was done while it waited
    client.replies.append(spoken("了", "記下了。"))
    await rig.confirm(asked["confirm"]["id"], True)
    (turn,) = await rig.turns()
    assert brief(turn["steps"]) == [("tool", "note", "Notes", "ok", None)]


async def test_the_calls_a_turn_could_not_ask_about_are_steps_that_were_not_made(
    rig, plugins, client, calls
):
    await plugins.connect(look="T0", note="T2", wipe="T3")
    route(client)
    client.plans.append(
        tool_plan(("Notes__look", {"topic": "tea"}), ("Notes__note", {"text": "x"}), ("Notes__wipe", {}))
    )
    await rig.ask("查茶，記下，然後全部刪掉")
    (turn,) = await rig.turns()
    assert brief(turn["steps"]) == [
        ("tool", "look", "Notes", "ok", None),
        ("tool", "wipe", "Notes", "blocked", "not_made"),
    ]


async def test_the_app_is_told_when_a_turn_is_written_and_when_it_is_answered(
    rig, plugins, client, calls, heard
):
    asked = await ask_for_note(rig, plugins, client, "T2")
    assert heard == [("agent.turn", {"turn_id": asked["turn_id"]})]
    client.replies.append(spoken("了", "記下了。"))
    await rig.confirm(asked["confirm"]["id"], True)
    assert heard[-1] == ("agent.turn", {"turn_id": asked["turn_id"]}) and len(heard) == 2


async def test_clearing_the_history_takes_the_steps_too(rig, client, conn):
    client.decisions.append(decision("command", "volume_mute"))
    await rig.ask("靜音")
    await rig.call("agent.history.clear", {})
    assert conn.execute("SELECT count(*) FROM agent_steps").fetchone()[0] == 0
