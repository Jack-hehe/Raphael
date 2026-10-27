"""Conversation RPCs keep context, ownership and deletion within the chosen conversation."""

import json

import pytest

from raphael_core.protocol import RpcError
from .test_service import make_rig  # noqa: F401
from .test_narration import chat


async def test_new_conversation_starts_clean_and_switching_restores_context(make_rig, client):
    rig = make_rig()
    first = (await rig.call("conversations.create", {"member_id": 0}))["conversation"]
    client.chats.append(chat('{"prefix":"Answer","text":"Saved secret","detail":""}'))
    await rig.call("agent.ask", {"session_id": "one", "text": "Remember the cobalt elephant", "source": "app", "ui_language": "en", "conversation_id": first["id"]})
    second = (await rig.call("conversations.create", {"member_id": 0}))["conversation"]
    client.chats.append(chat('{"prefix":"Answer","text":"Hello","detail":""}'))
    await rig.call("agent.ask", {"session_id": "two", "text": "Hello", "source": "app", "ui_language": "en", "conversation_id": second["id"]})
    assert "cobalt elephant" not in json.dumps(client.chat_calls[-1])
    first_turns = await rig.call("agent.history.list", {"conversation_id": first["id"]})
    assert [turn["question"] for turn in first_turns["turns"]] == ["Remember the cobalt elephant"]
    assert first_turns["turns"][0]["conversation_id"] == first["id"]
    listed = (await rig.call("conversations.list", {"member_id": 0}))["conversations"]
    assert next(c for c in listed if c["id"] == first["id"])["title"] == "Remember the cobalt elephant"


async def test_rename_search_delete_and_default_entry(make_rig):
    rig = make_rig()
    default = (await rig.call("conversations.list", {"member_id": 0}))["conversations"][0]
    turn_id = rig.history.add(session_id="voice", question="cobalt elephant", route="answer", prefix="", text="hello", status="done")
    assert rig.history.get(turn_id).conversation_id == default["id"]
    await rig.call("conversations.rename", {"id": default["id"], "title": "Cobalt notes"})
    assert len((await rig.call("conversations.list", {"member_id": 0, "query": "cobalt"}))["conversations"]) == 1
    await rig.call("conversations.delete", {"id": default["id"]})
    assert rig.history.get(turn_id) is None
    assert rig.history.search("elephant") == []
    replacement = (await rig.call("conversations.list", {"member_id": 0}))["conversations"][0]
    assert replacement["id"] != default["id"]


async def test_deleting_the_default_hands_over_to_the_latest_conversation_instead_of_a_new_one(make_rig):
    rig = make_rig()
    default = (await rig.call("conversations.list", {"member_id": 0}))["conversations"][0]
    older = (await rig.call("conversations.create", {"member_id": 0}))["conversation"]
    latest = (await rig.call("conversations.create", {"member_id": 0}))["conversation"]
    await rig.call("conversations.delete", {"id": default["id"]})
    listed = [c for c in (await rig.call("conversations.list", {"member_id": 0}))["conversations"] if c["kind"] == "standard"]
    assert sorted(c["id"] for c in listed) == sorted([older["id"], latest["id"]])
    assert [c["id"] for c in listed if c["is_default"]] == [latest["id"]]
    turn_id = rig.history.add(session_id="voice", question="after", route="answer", prefix="", text="ok", status="done")
    assert rig.history.get(turn_id).conversation_id == latest["id"]


@pytest.mark.parametrize("conversation_id", [True, -1, "1", 999999])
async def test_invalid_conversation_never_runs_a_model(make_rig, conversation_id):
    rig = make_rig()
    with pytest.raises(RpcError):
        await rig.call("agent.ask", {"session_id": "bad", "text": "hello", "source": "app", "ui_language": "en", "conversation_id": conversation_id})
    assert await rig.turns() == []


async def test_search_reaches_named_conversations_and_delete_clears_native_context(make_rig):
    rig = make_rig()
    conversation = (await rig.call("conversations.create", {"member_id": 0}))["conversation"]
    turn_id = rig.history.add(session_id="named", question="Cobalt discovery", route="answer", prefix="", text="Answer", status="done", conversation_id=conversation["id"])
    rig.service._cli_sessions.put(0, "cli:codex", "native-context", conversation["id"])
    hits = (await rig.call("messages.search", {"query": "Cobalt"}))["turns"]
    assert [(hit["id"], hit["conversation_id"], hit["conversation_title"]) for hit in hits] == [(turn_id, conversation["id"], "Cobalt discovery")]
    around = await rig.call("agent.history.list", {"conversation_id": conversation["id"], "around_id": turn_id})
    assert around["turns"][0]["id"] == turn_id
    with pytest.raises(RpcError):
        await rig.call("agent.history.delete", {"turn_id": turn_id})
    await rig.call("agent.history.delete", {"turn_id": turn_id, "conversation_id": conversation["id"]})
    assert rig.service._cli_sessions.get(0, "cli:codex", conversation["id"]) is None
    assert (await rig.call("messages.search", {"query": "Cobalt"}))["turns"] == []


async def test_owner_context_reaches_named_conversation_and_desktop_context(make_rig, client):
    rig = make_rig()
    rig.settings.set('owner_name', 'Named Owner')
    conversation = (await rig.call('conversations.create', {'member_id':0}))['conversation']
    client.chats.append(chat('{"prefix":"Answer","text":"Hello","detail":""}'))
    await rig.call('agent.ask', {'session_id':'named-owner','text':'Hello','source':'app','ui_language':'en','conversation_id':conversation['id']})
    assert 'Named Owner' in json.dumps(client.chat_calls[-1])
    assert '<owner_context>' in rig.service._before_run(conversation_id=conversation['id'])
