"""Which of Raphael's replies are said aloud, and what stops the voice: the assistant with a fake
speaker that notes what it is told to say and when it is stopped. No sound, no network."""

import asyncio

import pytest

from .conftest import decision
from .test_service import (  # noqa: F401  (make_rig and _copying_asks_first are fixtures)
    _copying_asks_first,
    ask_to_copy,
    make_rig,
    spoken,
    until,
)


class FakeSpeaker:
    def __init__(self):
        self.said = []
        self.stops = 0
        self.fail = False

    def say(self, prefix, text):
        if self.fail:
            raise RuntimeError("the voice broke")
        self.said.append((prefix, text))

    def stop(self):
        self.stops += 1


@pytest.fixture
def speaker():
    return FakeSpeaker()


@pytest.fixture
def rig(make_rig, speaker):
    return make_rig(speaker=speaker)


async def ask_from(rig, source, text="你好", language="zh-TW"):
    return await rig.call(
        "agent.ask",
        {"session_id": "s1", "text": text, "ui_language": language, "source": source},
    )


async def test_an_answer_to_a_spoken_request_is_said_with_its_prefix(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "台北現在 24 度。"))
    result = await rig.ask("台北幾度")
    assert result["text"] == "台北現在 24 度。"
    assert speaker.said == [("答", "台北現在 24 度。")]


async def test_an_answer_to_a_request_typed_in_the_app_is_said_too(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    await ask_from(rig, "app")
    assert speaker.said == [("答", "好。")]


async def test_a_message_from_a_phone_is_never_said(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    result = await ask_from(rig, "telegram")
    assert result["status"] == "done" and speaker.said == []


async def test_a_message_from_a_phone_does_not_stop_what_is_being_said(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    await ask_from(rig, "telegram")
    assert speaker.stops == 0


async def test_a_new_request_at_this_computer_stops_the_voice_before_it_is_answered(
    rig, client, speaker
):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    await rig.ask("你好")
    assert speaker.stops == 1 and speaker.said == [("答", "好。")]


async def test_a_failure_line_is_said_like_any_answer(make_rig, client, speaker):
    rig = make_rig(speaker=speaker)
    rig.keys.clear()
    result = await rig.ask("你好")
    assert result["prefix"] == "否"
    assert speaker.said == [("否", result["text"])]


async def test_the_longer_detail_and_the_notice_are_not_said(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "一句話。", detail="很長很長的詳細內容。"))
    await rig.ask("說明一下")
    assert speaker.said == [("答", "一句話。")]


async def test_cancelling_stops_the_voice_and_the_cancelled_request_says_nothing(
    make_rig, client, speaker
):
    from raphael_core.agent.routing import Router as Decider

    rig = make_rig(speaker=speaker, decider=Decider(client, jev_total_s=10.0, jev_first_s=10.0))

    async def hang(**kwargs):
        await asyncio.sleep(30)

    client.decisions.append(hang)
    asking = asyncio.ensure_future(rig.ask("打開 YouTube"))
    await until(lambda: client.decide_calls)
    stops = speaker.stops
    await rig.call("agent.cancel", {"session_id": "s1"})
    assert speaker.stops == stops + 1
    result = await asyncio.wait_for(asking, 2)
    assert result["text"] == "已取消。" and speaker.said == []


async def test_a_request_that_arrives_after_its_cancel_says_nothing(rig, speaker):
    await rig.call("agent.cancel", {"session_id": "s1"})
    result = await rig.ask("打開 YouTube")
    assert result["turn_id"] == 0 and speaker.said == []


async def test_a_question_for_a_yes_is_not_said_but_the_answer_to_the_yes_is(
    rig, client, actions, speaker
):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    assert asked["status"] == "confirm" and speaker.said == []
    done = await rig.confirm(asked["confirm"]["id"], True)
    assert speaker.said == [(done["prefix"], done["text"])]


async def test_the_owners_no_is_not_said_back(rig, client, actions, speaker):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    await rig.confirm(asked["confirm"]["id"], False)
    assert speaker.said == []


async def test_a_yes_that_came_too_late_is_not_said(rig, speaker):
    result = await rig.confirm("nope", True)
    assert result["prefix"] == "否" and speaker.said == []


async def test_a_voice_that_breaks_never_costs_the_answer(rig, client, speaker):
    speaker.fail = True
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    result = await rig.ask("你好")
    assert result["text"] == "好。"


async def test_an_assistant_with_no_voice_answers_in_silence(make_rig, client):
    rig = make_rig()
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    assert (await rig.ask("你好"))["text"] == "好。"
