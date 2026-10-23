"""Which of Raphael's replies are said aloud, in what language, and what stops the voice: the
assistant with a fake speaker that notes what it is told to say and when it is stopped. No sound, no
network.

The voice speaks Japanese only: on a Japanese screen the line is spoken as shown; on another, a fixed
phrase is spoken in its Japanese wording and any other line as the `say_ja` the answer model wrote in
the same call. The reply, and the announcement of its turn to the app, wait for the voice to be ready."""

import asyncio
import json
import logging

import pytest

from raphael_core.agent.persona import phrase

from .conftest import completion, decision
from .test_narration import chat
from .test_service import (  # noqa: F401  (make_rig and _copying_asks_first are fixtures)
    _copying_asks_first,
    ask_to_copy,
    make_rig,
    spoken,
    until,
)

SAY_JA = "台北は現在二十四度、曇りです。"


class FakeSpeaker:
    """A voice that notes what it is asked to say. `gate`, when set, is an event that `say` waits
    for, as the real one waits for the first sound; `can` is whether there is a voice to hear."""

    def __init__(self):
        self.said = []
        self.stops = 0
        self.fail = False
        self.can = True
        self.gate = None
        self.waiting = False
        self.turn_ids = []

    def speaks(self):
        return self.can

    async def say(self, prefix, text, *, turn_id=None):
        self.turn_ids.append(turn_id)
        if self.fail:
            raise RuntimeError("the voice broke")
        self.said.append((prefix, text))
        if self.gate is not None:
            self.waiting = True
            try:
                await self.gate.wait()
            finally:
                self.waiting = False

    def stop(self):
        self.stops += 1


def with_japanese(prefix="答", text="台北現在 24 度。", say_ja=SAY_JA, detail=""):
    """The model's reply when it was asked for the line in Japanese too."""
    return completion({"prefix": prefix, "text": text, "detail": detail, "say_ja": say_ja})


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


# --- what is spoken, and in which language ----------------------------------------------------------


async def test_a_chinese_answer_is_shown_in_chinese_and_spoken_in_japanese(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese())
    result = await rig.ask("台北幾度")
    assert (result["prefix"], result["text"]) == ("答", "台北現在 24 度。")
    assert speaker.said == [("解", SAY_JA)]  # 答 is spoken as 解
    assert result["voiced"] is True
    assert speaker.turn_ids == [result["turn_id"]]


async def test_an_english_answer_is_shown_in_english_and_spoken_in_japanese(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese("Notice", "It is 24 degrees in Taipei."))
    result = await rig.ask("how warm is it", language="en")
    assert (result["prefix"], result["text"]) == ("Notice", "It is 24 degrees in Taipei.")
    assert speaker.said == [("告", SAY_JA)]


@pytest.mark.parametrize(
    ("shown", "spoken_as"),
    [("答", "解"), ("告", "告"), ("否", "否"), ("了", "了"), ("問", "問")],
)
async def test_each_chinese_prefix_is_spoken_as_its_japanese_counterpart(
    rig, client, speaker, shown, spoken_as
):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese(shown, "好。"))
    await rig.ask("你好")
    assert speaker.said == [(spoken_as, SAY_JA)]


@pytest.mark.parametrize(
    ("shown", "spoken_as"),
    [("Answer", "解"), ("Notice", "告"), ("Negative", "否"), ("Understood", "了"), ("Question", "問")],
)
async def test_each_english_prefix_is_spoken_as_its_japanese_counterpart(
    rig, client, speaker, shown, spoken_as
):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese(shown, "Fine."))
    await rig.ask("hello", language="en")
    assert speaker.said == [(spoken_as, SAY_JA)]


async def test_on_a_japanese_screen_what_is_shown_is_what_is_spoken(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("解", "台北は現在二十四度です。"))
    result = await rig.ask("台北は何度", language="ja")
    assert speaker.said == [("解", "台北は現在二十四度です。")] == [(result["prefix"], result["text"])]


async def test_the_japanese_line_is_asked_for_in_the_same_call_when_the_screen_is_not_japanese(
    rig, client, speaker
):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese())
    await rig.ask("台北幾度")
    assert len(client.json_calls) == 1  # no second call for the Japanese
    call = client.json_calls[0]
    assert call["schema"]["properties"]["say_ja"] == {"type": "string"}
    assert "say_ja" in call["schema"]["required"]
    assert '"say_ja"' in call["system"] and "Japanese" in call["system"]
    assert call["user"].rstrip().endswith('except the field "say_ja", which is Japanese.')


@pytest.mark.parametrize("language", ["zh-TW", "en", "ja"])
async def test_the_japanese_line_is_not_asked_for_when_nothing_will_be_spoken(
    make_rig, client, speaker, language
):
    speaker.can = False  # no voice that can be heard
    rig = make_rig(speaker=speaker)
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答" if language != "en" else "Answer", "好。"))
    await rig.ask("你好", language=language)
    assert "say_ja" not in client.json_calls[0]["schema"]["properties"]
    assert "say_ja" not in client.json_calls[0]["system"]
    assert speaker.said == []


async def test_the_japanese_line_is_not_asked_for_on_a_japanese_screen(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("解", "はい。"))
    await rig.ask("こんにちは", language="ja")
    assert "say_ja" not in client.json_calls[0]["schema"]["properties"]
    assert "say_ja" not in client.json_calls[0]["system"]


async def test_the_japanese_line_is_not_asked_for_with_no_speaker_at_all(make_rig, client):
    rig = make_rig()
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    await rig.ask("你好")
    assert "say_ja" not in client.json_calls[0]["schema"]["properties"]


async def test_the_japanese_line_is_not_asked_for_a_message_from_a_phone(rig, client, speaker):
    client.chats.append(chat('{"prefix":"答","text":"好。","detail":""}'))
    await ask_from(rig, "telegram")
    assert "say_ja" not in client.chat_calls[0]["messages"][0]["content"]
    assert speaker.said == []


async def test_only_the_line_that_is_shown_is_kept_in_the_history(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese())
    await rig.ask("台北幾度")
    (turn,) = await rig.turns()
    assert (turn["prefix"], turn["text"]) == ("答", "台北現在 24 度。")
    assert SAY_JA not in repr(turn)
    stored = [tuple(row) for row in rig.conn.execute("SELECT * FROM agent_turns")]
    assert SAY_JA not in repr(stored)


async def test_nothing_of_the_line_in_any_language_is_logged(rig, client, speaker, caplog):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese("答", "祕密的答案。", say_ja="ひみつの答えです。"))
    with caplog.at_level(logging.DEBUG):
        await rig.ask("祕密的問題")
    for needle in ("祕密", "ひみつ"):
        assert needle not in caplog.text


async def test_a_reply_with_no_japanese_in_it_is_shown_and_not_spoken(rig, client, speaker, caplog):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))  # the model left the field out
    with caplog.at_level(logging.DEBUG):
        result = await rig.ask("你好")
    assert result["text"] == "好。" and speaker.said == []
    assert not result.get("voiced", False)


async def test_a_reply_whose_japanese_is_not_japanese_is_not_spoken(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese("答", "好。", say_ja="Taipei is 24 degrees."))
    await rig.ask("你好")
    assert speaker.said == []


async def test_the_brackets_and_the_length_of_the_japanese_line_are_kept_in_bounds(
    rig, client, speaker
):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese("答", "好。", say_ja="《" + "はい。" * 150 + "》"))
    await rig.ask("你好")
    ((_, said),) = speaker.said
    assert "《" not in said and "》" not in said and len(said) <= 200


@pytest.mark.parametrize(
    ("language", "phrase_name"),
    [("zh-TW", "no_key"), ("en", "no_key"), ("zh-TW", "unclear"), ("en", "unclear")],
)
async def test_a_fixed_line_is_spoken_in_its_japanese_wording(
    make_rig, client, speaker, language, phrase_name
):
    rig = make_rig(speaker=speaker)
    if phrase_name == "no_key":
        rig.keys.clear()
        result = await rig.ask("你好", language=language)
    else:
        result = await rig.ask("  ", language=language)
    assert result["text"] == phrase(language, phrase_name)
    prefix = {"zh-TW": "否", "en": "Negative"}[language]
    assert result["prefix"] == prefix
    assert speaker.said == [("否", phrase("ja", phrase_name))]
    assert client.calls == 0


async def test_a_fixed_line_with_a_name_in_it_keeps_the_name_in_the_japanese_wording(
    rig, client, speaker
):
    from raphael_core.agent.persona import fixed_line

    shown = phrase("zh-TW", "exchange_waits", member="小行程")
    assert fixed_line("zh-TW", shown, "ja") == phrase("ja", "exchange_waits", member="小行程")


async def test_a_reply_that_is_a_note_taken_is_a_fixed_line_and_spoken_in_japanese(
    rig, client, speaker
):
    # "Noted." is worded by the service, not by the model: the shown line is a phrase of the table.
    from raphael_core.agent.persona import spoken_line

    assert spoken_line("zh-TW", "了", phrase("zh-TW", "remembered")) == (
        "了",
        phrase("ja", "remembered"),
    )


# --- which replies are said --------------------------------------------------------------------------


async def test_an_answer_to_a_request_typed_in_the_app_is_said_too(rig, client, speaker):
    client.chats.append(chat('{"prefix":"答","text":"好。","detail":"","say_ja":"はい。"}'))
    await ask_from(rig, "app")
    assert speaker.said == [("解", "はい。")]


async def test_a_message_from_a_phone_is_never_said(rig, client, speaker):
    client.chats.append(chat('{"prefix":"答","text":"好。","detail":""}'))
    result = await ask_from(rig, "telegram")
    assert result["status"] == "done" and speaker.said == []
    assert not result.get("voiced", False)


async def test_a_message_from_a_phone_does_not_stop_what_is_being_said(rig, client, speaker):
    client.chats.append(chat('{"prefix":"答","text":"好。","detail":""}'))
    await ask_from(rig, "telegram")
    assert speaker.stops == 0


async def test_a_new_request_at_this_computer_stops_the_voice_before_it_is_answered(
    rig, client, speaker
):
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese("答", "好。", say_ja="はい。"))
    await rig.ask("你好")
    assert speaker.stops == 1 and speaker.said == [("解", "はい。")]


async def test_the_longer_detail_and_the_notice_are_not_said(rig, client, speaker):
    client.decisions.append(decision("chat"))
    client.replies.append(
        with_japanese("答", "一句話。", say_ja="一言です。", detail="很長很長的詳細內容。")
    )
    await rig.ask("說明一下")
    assert speaker.said == [("解", "一言です。")]


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
    assert done["text"] == phrase("zh-TW", "copied")
    assert speaker.said == [("了", phrase("ja", "copied"))]
    assert done["voiced"] is True
    assert speaker.turn_ids == [done["turn_id"]]


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
    client.replies.append(with_japanese("答", "好。", say_ja="はい。"))
    result = await rig.ask("你好")
    assert result["text"] == "好。"


async def test_an_assistant_with_no_voice_answers_in_silence(make_rig, client):
    rig = make_rig()
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    assert (await rig.ask("你好"))["text"] == "好。"


# --- the line is shown when the voice is ready -------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "prefix", "shown"),
    [("zh-TW", "答", "現在是下午兩點。"), ("en", "Answer", "It is two o'clock.")],
)
async def test_a_narrated_final_speaks_japanese_and_waits_for_voice_but_progress_does_not(
    make_rig, client, speaker, caplog, language, prefix, shown
):
    announced = []

    def notify(method, params):
        if method == "agent.turn":
            turn = rig.history.get(params["turn_id"])
            messages = rig.history.messages([turn.id]).get(turn.id, [])
            announced.append((turn.status, [m["kind"] for m in messages]))

    rig = make_rig(speaker=speaker, notify=notify)
    speaker.gate = asyncio.Event()
    progress = "我先確認現在的時間。" if language == "zh-TW" else "I will check the current time."
    japanese = "現在は午後二時です。"

    async def finish(**kwargs):
        assert speaker.said == []
        assert announced[-1] == ("running", ["progress"])
        return chat(json.dumps({"prefix": prefix, "text": shown, "say_ja": japanese, "detail": ""}))

    client.decisions.append(decision("answer"))
    client.chats = [chat(progress, ("local_time", {})), finish]
    with caplog.at_level(logging.DEBUG):
        asking = asyncio.create_task(ask_from(rig, "app", language=language))
        try:
            await until(lambda: speaker.waiting)
            assert not asking.done()
            assert all(status == "running" for status, _ in announced)
            count = len(announced)
        finally:
            speaker.gate.set()
            result = await asyncio.wait_for(asking, 2)

    assert (result["prefix"], result["text"]) == (prefix, shown)
    assert speaker.said == [("解", japanese)]
    assert len(announced) == count + 1
    assert announced[-1] == ("done", ["progress", "reply"])
    assert len(client.chat_calls) == 2 and client.json_calls == []
    first = client.chat_calls[0]["messages"]
    assert '"say_ja"' in first[0]["content"]
    assert first[1]["content"].rstrip().endswith('except the field "say_ja", which is Japanese.')
    [turn] = await rig.turns()
    assert [m["kind"] for m in turn["messages"]] == ["progress", "reply"]
    assert japanese not in repr(result) + repr(turn) + caplog.text
    assert japanese not in repr([tuple(row) for row in rig.conn.execute("SELECT * FROM agent_messages")])


@pytest.fixture
def announced():
    return []


@pytest.fixture
def timed_rig(make_rig, speaker, announced):
    return make_rig(speaker=speaker, notify=lambda method, params: announced.append((method, params)))


async def test_the_reply_waits_for_the_voice_and_comes_when_it_is_ready(timed_rig, client, speaker):
    speaker.gate = asyncio.Event()
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese())
    asking = asyncio.ensure_future(timed_rig.ask("台北幾度"))
    await until(lambda: speaker.waiting)
    await asyncio.sleep(0.05)
    assert not asking.done()  # the line is not shown before the voice is ready
    speaker.gate.set()
    result = await asyncio.wait_for(asking, 2)
    assert result["text"] == "台北現在 24 度。"


async def test_the_turn_is_announced_to_the_app_when_the_voice_is_ready_not_before(
    timed_rig, announced, client, speaker
):
    speaker.gate = asyncio.Event()
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese())
    asking = asyncio.ensure_future(timed_rig.ask("台北幾度"))
    await until(lambda: speaker.waiting)
    assert [m for m, _ in announced] == []  # written, but the conversation does not show it yet
    speaker.gate.set()
    result = await asyncio.wait_for(asking, 2)
    assert announced == [("agent.turn", {"turn_id": result["turn_id"], "member_id": 0})]


async def test_the_answer_to_a_yes_is_announced_when_the_voice_is_ready_too(
    timed_rig, announced, client, speaker
):
    timed_rig.seed("q", "a")
    asked = await ask_to_copy(timed_rig, client)
    announced.clear()
    speaker.gate = asyncio.Event()
    confirming = asyncio.ensure_future(timed_rig.confirm(asked["confirm"]["id"], True))
    await until(lambda: speaker.waiting)
    assert announced == []
    speaker.gate.set()
    done = await asyncio.wait_for(confirming, 2)
    assert announced == [("agent.turn", {"turn_id": done["turn_id"], "member_id": 0})]


async def test_with_no_voice_the_reply_and_its_announcement_do_not_wait(
    make_rig, announced, client, speaker
):
    speaker.can = False
    speaker.gate = asyncio.Event()  # would hold a reply for ever if it were waited for
    rig = make_rig(speaker=speaker, notify=lambda m, p: announced.append((m, p)))
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    result = await asyncio.wait_for(rig.ask("你好"), 2)
    assert result["text"] == "好。" and speaker.said == []
    assert announced == [("agent.turn", {"turn_id": result["turn_id"], "member_id": 0})]


async def test_a_message_from_a_phone_is_announced_at_once(timed_rig, announced, client, speaker):
    speaker.gate = asyncio.Event()
    client.chats.append(chat('{"prefix":"答","text":"好。","detail":""}'))
    result = await asyncio.wait_for(ask_from(timed_rig, "telegram"), 2)
    assert announced and all(note == ("agent.turn", {"turn_id": result["turn_id"], "member_id": 0}) for note in announced)


async def test_a_question_for_a_yes_is_announced_at_once(timed_rig, announced, client, speaker):
    speaker.gate = asyncio.Event()
    timed_rig.seed("q", "a")
    asked = await asyncio.wait_for(ask_to_copy(timed_rig, client), 2)
    assert asked["status"] == "confirm"
    assert ("agent.turn", {"turn_id": asked["turn_id"], "member_id": 0}) in announced


async def test_a_cancel_while_the_voice_is_awaited_still_announces_the_turn(
    timed_rig, announced, client, speaker
):
    speaker.gate = asyncio.Event()
    client.decisions.append(decision("chat"))
    client.replies.append(with_japanese())
    asking = asyncio.ensure_future(timed_rig.ask("台北幾度"))
    await until(lambda: speaker.waiting)
    asking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asking
    assert [m for m, _ in announced] == ["agent.turn"]  # never left unannounced
