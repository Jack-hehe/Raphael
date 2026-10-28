"""Contextual typed requests use one guarded harness, without router dead ends."""

import json

import pytest

from .test_narration import chat, typed
from .test_service import make_rig  # noqa: F401


@pytest.mark.parametrize("source", ["app", "telegram"])
async def test_a_follow_up_uses_the_conversation_without_the_fast_router(make_rig, client, source):
    rig = make_rig()
    rig.seed("Read today's NHK news", "An article about space: https://news.example/article", conversation_id=rig.service.conversations.main())
    client.chats = [chat(json.dumps({"prefix": "Answer", "text": "Here is the full article.", "detail": ""}))]
    result = await rig.call("agent.ask", {"session_id": "followup", "text": "Show it here in full", "ui_language": "en", "source": source})
    assert result["text"] == "Here is the full article."
    assert client.decide_calls == []
    assert "NHK" in client.chat_calls[0]["messages"][1]["content"]


async def test_public_thinking_and_progress_are_stored_before_the_tool_result(make_rig, client):
    rig = make_rig()

    async def finish(**kwargs):
        [turn] = await rig.turns()
        assert [m["kind"] for m in turn["messages"]] == ["thinking", "progress"]
        return chat(json.dumps({"prefix": "Answer", "text": "It is Friday.", "detail": ""}))

    client.chats = [chat(json.dumps({"thinking": "The local date will help choose today's article.", "progress": "I will check today's date."}), ("local_time", {})), finish]
    await typed(rig)
    [turn] = await rig.turns()
    assert [m["kind"] for m in turn["messages"]] == ["thinking", "progress", "reply"]


async def test_show_accepts_a_url_seen_in_an_earlier_turn_and_keeps_media_json_plain(make_rig, client, monkeypatch):
    rig = make_rig()
    url = "https://news.example/article"
    rig.seed("Find NHK", f"Here: {url}", conversation_id=rig.service.conversations.main())

    async def show(address):
        from raphael_core.agent.tools import ToolResult
        return ToolResult("show", True, {"type": "link", "url": address, "title": "NHK", "site": "NHK", "description": "News", "thumbnail": None, "embed": None})

    monkeypatch.setattr(rig.service._tools, "show", show)
    client.chats = [chat("I will show the article here.", ("show", {"url": url})), chat(json.dumps({"text": "Here it is.", "detail": ""}))]
    await typed(rig, "Show that here")
    [turn, _old] = await rig.turns()
    media = next(m for m in turn["messages"] if m["kind"] == "media")
    assert json.loads(media["text"])["url"] == url


async def test_a_voice_follow_up_is_given_context_and_can_fall_back_to_the_harness(make_rig, client):
    from .conftest import decision
    rig = make_rig()
    rig.seed("NHK article", "https://news.example/article")
    client.decisions.append(decision("unclear"))
    client.chats = [chat('{"text":"Here is the longer article.","detail":""}')]
    result = await rig.ask("More detail", language="en")
    assert "Here is" in result["text"]
    assert "NHK" in client.decide_calls[0]["state"]["conversation"]
    assert "NHK" in client.chat_calls[0]["messages"][1]["content"]


async def test_full_article_replies_are_kept_in_order_without_the_voice_length_limit(make_rig, client):
    rig = make_rig()
    article = "A full paragraph with a concrete fact.\n" * 30
    client.chats = [chat(json.dumps({"messages": [article, "Second paragraph.", "Third.", "Fourth.", "Dropped."], "text": "Fourth.", "detail": ""}))]
    await typed(rig, "Show the full article here")
    [turn] = await rig.turns()
    assert len(turn["messages"]) == 4
    assert article.strip() in turn["messages"][0]["text"]
    assert "Dropped" not in str(turn)


async def test_show_refuses_an_unseen_url_before_any_fetch(make_rig, client, monkeypatch):
    rig = make_rig()

    async def unexpected(url):
        pytest.fail("Unseen URL reached the fetcher")

    monkeypatch.setattr(rig.service._tools, "show", unexpected)
    client.chats = [chat("Showing a reference.", ("show", {"url": "https://unseen.example/"})), chat('{"text":"Which reference did you mean?","detail":""}')]
    await typed(rig, "Show it")
    assert "url_not_seen" in client.chat_calls[-1]["messages"][-1]["content"]


async def test_only_explicit_public_fields_are_published_from_structured_narration(make_rig, client):
    rig = make_rig()
    client.chats = [chat('```json\n{"thinking":"I will use the local date to choose the article.","progress":"Checking the date.","reasoning":"PRIVATE-REASONING"}\n```', ("local_time", {})), chat('{"text":"Done.","detail":""}')]
    await typed(rig)
    [turn] = await rig.turns()
    assert [m["kind"] for m in turn["messages"]] == ["thinking", "progress", "reply"]
    assert "PRIVATE-REASONING" not in str(turn)


async def test_every_raphael_reply_is_converted_to_traditional_chinese(make_rig, client):
    rig = make_rig()
    client.chats = [chat('{"messages":["这是第一段。","这是第二段。"],"text":"这是第二段。","detail":""}')]
    await rig.call("agent.ask", {"session_id": "s", "source": "app", "text": "完整文章", "ui_language": "zh-TW"})
    [turn] = await rig.turns()
    assert all("這是第" in m["text"] for m in turn["messages"])
