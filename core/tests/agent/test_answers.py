"""Tests for the answers: what the language model is asked, and what comes back as Raphael's words.
The OpenRouter client is a fake that answers as told."""

import asyncio
import logging

import pytest

from raphael_core.agent import answers as answers_module
from raphael_core.agent import persona
from raphael_core.agent.answers import (
    REPLY_MAX_TOKENS,
    Reply,
    Voice,
    parse_reply,
    reply_schema,
)
from raphael_core.agent.failure import AgentFailure
from raphael_core.agent.history import Turn
from raphael_core.agent.ledger import Ledger
from raphael_core.agent.tools import ToolResult
from raphael_core.brain.openrouter import CLEANUP_FALLBACKS, Completion, OpenRouterError
from raphael_core.brain.spend import SpendTracker

NOW = {
    "iso": "2026-10-20T14:05:09+08:00",
    "date": "2026-10-20",
    "weekday": "Tuesday",
    "time": "14:05",
    "utc_offset": "UTC+08:00",
}


def test_member_replies_keep_up_to_four_separate_messages_with_a_limit_per_message():
    reply = parse_reply({"messages": ["Correction", "Example", "x" * 3000, "Your turn?", "extra"], "detail": ""}, "en", plain=True)
    assert reply.messages[:2] == ["Correction", "Example"]
    assert len(reply.messages) == 4
    assert len(reply.messages[2]) == persona.MEMBER_TEXT_MAX
    assert reply.text == "Your turn?"


def completion(data, cost=0.0009, model="openai/gpt-6-luna"):
    return Completion(
        data=data, model=model, cost_usd=cost, prompt_tokens=700, completion_tokens=45,
        latency_ms=1300,
    )  # fmt: skip


def spoken(prefix="答", text="台北現在 24 度，多雲。", detail=""):
    return {"prefix": prefix, "text": text, "detail": detail}


def turn(question, text, *, prefix="答", route="chat", number=1):
    return Turn(
        id=number, created_at_ms=number, session_id="s", question=question, route=route,
        prefix=prefix, text=text, detail=None, status="done",
    )  # fmt: skip


@pytest.fixture
def ledger(conn):
    return Ledger(SpendTracker(conn, now_ms=lambda: 1_000))


@pytest.fixture
def voice(client):
    return Voice(client)


async def reply(voice, ledger, **overrides):
    args = {
        "model": "openai/gpt-6-luna",
        "language": "zh-TW",
        "route": "answer",
        "question": "台北天氣如何",
        "context": [],
        "tools": [],
        "moment": NOW,
        "ledger": ledger,
    }
    return await voice.reply(**{**args, **overrides})


# --- what is asked ---------------------------------------------------------------------------------


async def test_the_model_is_asked_for_json_with_a_little_reasoning(voice, client, ledger):
    client.replies.append(completion(spoken()))
    await reply(voice, ledger)
    call = client.json_calls[0]
    assert call["model"] == "openai/gpt-6-luna"
    assert call["fallbacks"] == CLEANUP_FALLBACKS
    assert call["system"] == persona.system_prompt("zh-TW", "answer")
    assert call["schema_name"] == "raphael_reply"
    assert call["reasoning_effort"] == "none"
    assert call["max_tokens"] == REPLY_MAX_TOKENS
    assert call["timeout_s"] > 0


@pytest.mark.parametrize("language", persona.LANGUAGES)
def test_the_schema_offers_the_prefixes_of_the_language_and_requires_every_field(language):
    schema = reply_schema(language)
    assert schema["properties"]["prefix"]["enum"] == list(persona.PREFIXES[language])
    assert schema["properties"]["text"] == {"type": "string"}
    assert schema["properties"]["detail"] == {"type": "string"}
    assert schema["required"] == ["prefix", "text", "detail"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("route", ["answer", "discuss", "chat"])
async def test_the_system_prompt_is_the_one_of_the_route(voice, client, ledger, route):
    client.replies.append(completion(spoken()))
    await reply(voice, ledger, route=route, language="ja")
    assert client.json_calls[0]["system"] == persona.system_prompt("ja", route)


async def test_the_question_goes_in_tags_after_the_time(voice, client, ledger):
    client.replies.append(completion(spoken()))
    await reply(voice, ledger, question="台北天氣如何")
    message = client.json_calls[0]["user"]
    assert message.startswith("Current date and time: 2026-10-20 14:05, Tuesday (UTC+08:00)")
    assert "<question>\n台北天氣如何\n</question>\n\nReply in " in message
    assert "Conversation so far" not in message and "Tool results" not in message


@pytest.mark.parametrize(
    ("language", "named"),
    [("en", "English"), ("ja", "Japanese"), ("zh-TW", "Traditional Chinese")],
)
async def test_the_last_thing_the_model_reads_is_the_language_to_reply_in(
    voice, client, ledger, language, named
):
    # A Chinese question in the English interface was answered in Chinese until this was said
    # last.
    client.replies.append(completion(spoken()))
    await reply(voice, ledger, question="台北天氣如何", language=language)
    last_line = client.json_calls[0]["user"].rsplit("\n\n", 1)[1]
    assert last_line.startswith(f"Reply in {named}") and last_line.endswith(" only.")


async def test_the_background_tasks_are_given_as_their_own_block_before_the_question(
    voice, client, ledger
):
    client.replies.append(completion(spoken()))
    block = "<background_tasks>\nfacts\n</background_tasks>"
    await reply(voice, ledger, question="寄出去了嗎", background=block)
    message = client.json_calls[0]["user"]
    assert block in message
    assert message.index(block) < message.index("<question>")
    client.replies.append(completion(spoken()))
    await reply(voice, ledger)
    assert "background_tasks" not in client.json_calls[1]["user"]


async def test_the_conversation_so_far_is_given_oldest_first(voice, client, ledger):
    client.replies.append(completion(spoken()))
    context = [
        turn("今天幾號", "今天是十月二十日。", number=1),
        turn("打開 YouTube", "", prefix="了", route="command", number=2),
    ]
    await reply(voice, ledger, route="discuss", context=context)
    message = client.json_calls[0]["user"]
    section = message.split("Conversation so far (oldest first):\n")[1].split("\n\n")[0]
    assert section == "User: 今天幾號\nRaphael: 今天是十月二十日。\nUser: 打開 YouTube\nRaphael: 了"


async def test_the_tool_results_are_given_as_data_and_a_tool_that_failed_is_said_to(
    voice, client, ledger
):
    client.replies.append(completion(spoken()))
    tools = [
        ToolResult("weather", True, {"city": "Taipei", "now": {"temperature_c": 24.1}}),
        ToolResult("web_search", False, None, "timeout"),
        ToolResult("local_time", True, NOW),
    ]
    await reply(voice, ledger, tools=tools)
    message = client.json_calls[0]["user"]
    section = message.split("Tool results:\n")[1].split("\n\n")[0].splitlines()
    assert section[0] == 'weather: {"city": "Taipei", "now": {"temperature_c": 24.1}}'
    assert section[1] == "web_search: unavailable (timeout)"
    assert section[2].startswith('local_time: {"iso": "2026-10-20T14:05:09+08:00"')


async def test_chinese_in_a_tool_result_goes_as_it_is_and_not_escaped(voice, client, ledger):
    client.replies.append(completion(spoken()))
    await reply(voice, ledger, tools=[ToolResult("web_search", True, {"answer": "明天有雨"})])
    assert "明天有雨" in client.json_calls[0]["user"]


# --- what comes back -------------------------------------------------------------------------------


async def test_a_good_reply_is_the_prefix_the_text_and_the_detail(voice, client, ledger):
    client.replies.append(completion(spoken("告", "明天有雨。", "降雨機率 70%。")))
    result = await reply(voice, ledger)
    assert result == Reply(prefix="告", text="明天有雨。", detail="降雨機率 70%。")


async def test_a_reply_without_a_detail_has_none(voice, client, ledger):
    client.replies.append(completion(spoken(detail="  ")))
    assert (await reply(voice, ledger)).detail is None


async def test_a_detail_that_only_repeats_the_text_is_none(voice, client, ledger):
    client.replies.append(completion(spoken(text="好。", detail="好。")))
    assert (await reply(voice, ledger)).detail is None


async def test_what_the_model_cost_is_written_to_the_spend(voice, client, ledger, conn):
    client.replies.append(completion(spoken(), cost=0.0009))
    await reply(voice, ledger)
    assert [tuple(r) for r in conn.execute("SELECT purpose, model, cost_usd FROM spend")] == [
        ("agent", "openai/gpt-6-luna", 0.0009)
    ]


def test_the_prefix_is_made_one_of_the_language_and_defaults_to_an_answer():
    assert parse_reply(spoken("Answer"), "zh-TW").prefix == "答"
    assert parse_reply(spoken("解"), "en").prefix == "Answer"
    assert parse_reply(spoken("whatever"), "ja").prefix == "解"
    assert parse_reply(spoken(None), "en").prefix == "Answer"


def test_the_text_is_one_line_cut_to_the_length_raphael_speaks_with_the_rest_in_the_detail():
    long = "這是一句剛好十個字的話。" * 12  # 144 characters
    result = parse_reply(spoken(text=long, detail="簡短"), "zh-TW")
    assert result.text == "這是一句剛好十個字的話。" * 7
    assert result.detail == long  # the whole of it, which is more than the detail it had


def test_a_detail_longer_than_the_text_that_was_cut_is_kept():
    long = "這是一句剛好十個字的話。" * 12
    detail = long + "還有更多的內容。"
    assert parse_reply(spoken(text=long, detail=detail), "zh-TW").detail == detail


def test_line_breaks_in_the_text_become_spaces():
    assert parse_reply(spoken(text="one\ntwo", prefix="Answer"), "en").text == "one two"


def test_simplified_characters_in_a_traditional_reply_are_made_traditional():
    result = parse_reply(spoken(text="这个软件很好用。", detail="详细说明"), "zh-TW")
    assert result.text == "這個軟體很好用。"
    assert result.detail == "詳細說明"


def test_japanese_and_english_are_not_converted():
    assert parse_reply(spoken("解", "学校は会議です。"), "ja").text == "学校は会議です。"
    assert parse_reply(spoken("Answer", "It is 24 degrees."), "en").text == (
        "It is 24 degrees."
    )


@pytest.mark.parametrize(
    "data",
    [
        spoken(text=""),
        spoken(text="   "),
        {"prefix": "答"},
        {"prefix": "答", "text": None, "detail": ""},
        {"prefix": "答", "text": 5, "detail": ""},
        {},
        [],
        "text",
        None,
    ],
)
def test_a_reply_with_nothing_to_say_is_a_failure(data):
    with pytest.raises(AgentFailure) as failure:
        parse_reply(data, "zh-TW")
    assert failure.value.kind == "error"


def test_a_detail_that_is_not_text_is_none():
    assert parse_reply({"prefix": "答", "text": "好。", "detail": 5}, "zh-TW").detail is None


# --- the pages it drew on --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "label"), [("zh-TW", "來源"), ("en", "Sources"), ("ja", "出典")]
)
async def test_the_pages_a_search_drew_on_are_listed_in_the_detail(
    voice, client, ledger, language, label
):
    prefix = persona.PREFIXES[language].answer
    client.replies.append(completion(spoken(prefix, "好。", "更多。")))
    sources = [
        {"title": "CWA", "url": "https://www.cwa.gov.tw/"},
        {"title": "", "url": "https://example.org/rain"},
    ]
    tools = [ToolResult("web_search", True, {"answer": "x", "sources": sources})]
    result = await reply(voice, ledger, language=language, tools=tools)
    assert result.detail == (
        "更多。\n\n" + label + ":\n- CWA https://www.cwa.gov.tw/\n- https://example.org/rain"
    )


async def test_a_search_with_no_pages_adds_nothing(voice, client, ledger):
    client.replies.append(completion(spoken(detail="")))
    tools = [ToolResult("web_search", True, {"answer": "x", "sources": []})]
    assert (await reply(voice, ledger, tools=tools)).detail is None


async def test_the_pages_are_listed_even_when_the_model_wrote_no_detail(voice, client, ledger):
    client.replies.append(completion(spoken(detail="")))
    tools = [ToolResult("web_search", True, {"answer": "x", "sources": [{"title": "CWA", "url": "u"}]})]
    result = await reply(voice, ledger, tools=tools)
    assert result.detail == "來源:\n- CWA u"


# --- when it goes wrong ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (OpenRouterError(401, "auth", "no"), "auth"),
        (OpenRouterError(402, "credits", "broke"), "credits"),
        (OpenRouterError(0, "network", "offline"), "offline"),
        (OpenRouterError(0, "timeout", "slow"), "timeout"),
        (OpenRouterError(503, "server", "down"), "error"),
        (OpenRouterError(429, "rate_limit", "slow"), "error"),
        (OpenRouterError(400, "bad_request", "no"), "error"),
        (OpenRouterError(200, "bad_response", "html"), "error"),
        (RuntimeError("boom"), "error"),
    ],
)
async def test_a_failing_model_is_a_failure_of_a_kind(voice, client, ledger, error, kind):
    client.replies.append(error)
    with pytest.raises(AgentFailure) as failure:
        await reply(voice, ledger)
    assert failure.value.kind == kind


async def test_a_model_that_hangs_is_a_timeout(client, ledger):
    async def hang(**kwargs):
        await asyncio.sleep(30)

    client.replies.append(hang)
    with pytest.raises(AgentFailure) as failure:
        await reply(Voice(client, timeout_s=0.1), ledger)
    assert failure.value.kind == "timeout"


async def test_a_reply_that_is_paid_for_but_empty_is_still_written_to_the_spend(
    voice, client, ledger, conn
):
    client.replies.append(completion(spoken(text=""), cost=0.002))
    with pytest.raises(AgentFailure):
        await reply(voice, ledger)
    assert conn.execute("SELECT count(*) FROM spend").fetchone()[0] == 1


async def test_nothing_the_user_said_or_the_model_answered_is_logged(
    voice, client, ledger, caplog
):
    client.replies.append(OpenRouterError(503, "server", "秘密 message"))
    with caplog.at_level(logging.DEBUG, logger=answers_module.logger.name):
        with pytest.raises(AgentFailure):
            await reply(voice, ledger, question="我的秘密問題")
    assert "秘密" not in caplog.text


# --- the line in Japanese, for the voice ----------------------------------------------------------


def test_the_schema_has_the_japanese_field_only_when_asked_for():
    assert "say_ja" not in reply_schema("zh-TW")["properties"]
    asking = reply_schema("zh-TW", say_ja=True)
    assert asking["properties"]["say_ja"] == {"type": "string"}
    assert asking["required"] == ["prefix", "text", "detail", "say_ja"]
    assert reply_schema("zh-TW", memory=True, say_ja=True)["required"][-3:] == ["say_ja", "memory", "need"]


def test_the_japanese_line_is_one_line_without_the_brackets_and_within_the_limit():
    assert answers_module.japanese_line("《解。\n二十四度です。》") == "解。 二十四度です。"
    long_line = "二十四度です。" * 60
    cut = answers_module.japanese_line(long_line)
    assert len(cut) <= answers_module.SAY_JA_MAX and cut.endswith("。")


@pytest.mark.parametrize("value", [None, 5, "", "  ", "It is 24 degrees.", "台北現在 24 度。"])
def test_a_japanese_line_with_no_kana_is_none(value):
    assert answers_module.japanese_line(value) == ""


def test_a_reply_carries_the_japanese_line_beside_the_shown_one():
    reply = parse_reply(
        {"prefix": "答", "text": "台北現在 24 度。", "detail": "", "say_ja": "台北は二十四度です。"}, "zh-TW"
    )
    assert (reply.prefix, reply.text, reply.say_ja) == ("答", "台北現在 24 度。", "台北は二十四度です。")
    assert parse_reply({"prefix": "答", "text": "好。", "detail": ""}, "zh-TW").say_ja == ""


async def test_the_voice_asks_for_the_japanese_in_the_same_call_when_told_to(voice, client, ledger):
    client.replies.append(completion({**spoken("答", "好。"), "say_ja": "はい。"}))
    got = await reply(voice, ledger, say_ja=True)
    assert len(client.json_calls) == 1 and got.say_ja == "はい。"
    call = client.json_calls[0]
    assert "say_ja" in call["schema"]["properties"]
    assert call["system"] == persona.system_prompt("zh-TW", "answer", say_ja=True)
    assert call["user"].endswith(persona.language_reminder("zh-TW", say_ja=True))


async def test_the_voice_does_not_ask_for_it_by_default_nor_for_a_japanese_screen(
    voice, client, ledger
):
    client.replies.append(completion(spoken()))
    await reply(voice, ledger)
    assert "say_ja" not in client.json_calls[0]["schema"]["properties"]
    client.replies.append(completion(spoken("解", "はい。")))
    await reply(voice, ledger, language="ja", say_ja=True)
    assert "say_ja" not in client.json_calls[1]["schema"]["properties"]
    assert "say_ja" not in client.json_calls[1]["system"]
