"""Model content is published as public narration and clean replies, in every thread."""

import json

import pytest

from agent.test_narration import chat
from .test_answering import client, setup, member, typed  # noqa: F401


@pytest.mark.parametrize("speaker", ["member", "raphael"])
async def test_reported_two_fenced_objects_publish_narration_then_only_final_messages(setup, client, speaker):
    rig, team = setup
    speaker_id = member(team, name="紫苑", role="Japanese teacher").id if speaker == "member" else 0
    client.chats = [chat('''```json
{"thinking":"主人說不會這段文章，先從基礎開始。","progress":"我來逐句拆解這段新聞。"}
```
你需要打好基礎。我來分段教。**這是重複的說明。**
```json
{"text":"換你練習。","detail":"","messages":["先看第一句。","弱点是弱點。","換你練習。"],"memory":[],"need":{}}
```''')]
    result = await typed(rig, "我都不會，可以解釋一下嗎？", speaker_id)
    [turn] = (await rig.call("agent.history.list", {"member_id": speaker_id}))["turns"]
    assert result["text"] == turn["text"] == "換你練習。"
    assert [m["kind"] for m in turn["messages"]] == ["thinking", "progress", "reply", "reply", "reply"]
    assert turn["messages"][0]["text"] == "主人說不會這段文章，先從基礎開始。"
    expected = ["我來逐句拆解這段新聞。", "先看第一句。", "弱點是弱點。", "換你練習。"]
    if speaker == "raphael":
        expected = ["《告。我來逐句拆解這段新聞。》", *[f"《答。{text}》" for text in expected[1:]]]
    assert [m["text"] for m in turn["messages"][1:]] == expected


@pytest.mark.parametrize(("content", "expected"), [
    ('{"text":"A clean answer.","detail":""}', "A clean answer."),
    ('```json\n{"text":"A clean answer."}\n```', "A clean answer."),
    ('```\n{"messages":["A clean answer."]}\n```', "A clean answer."),
    ("A clean answer.", "A clean answer."),
    ('{"thinking":"Check the example."}\nDuplicate prose.\n{"text":"A clean answer."}', "A clean answer."),
    ('{"thinking":"Check the example."}\nA clean answer.\n{"metadata":{"ignored":true}}', "A clean answer."),
    ('A clean answer.\n```json\n{"text": broken, "need": {}}\n```', "A clean answer."),
    ('```json\n{"text": "unfinished\n```\nA clean answer.', "A clean answer."),
    ('A clean answer.\n{"text": "unfinished', "A clean answer."),
    (json.dumps({"text": 'A clean answer.\n```json\n{"progress":"Echoed protocol."}\n```',
                 "detail": 'Details.\n{"thinking":"Echoed protocol."}'}), "A clean answer."),
    (json.dumps({"messages": ['A clean answer.\n{"text":"Echoed protocol."}']}), "A clean answer."),
    (json.dumps({"text": 'Braces in a string: } and an escaped "quote".', "memory": [], "need": {}}),
     'Braces in a string: } and an escaped "quote".'),
], ids=["bare", "json-fence", "plain-fence", "prose", "two-objects", "public-and-metadata",
        "malformed-balanced", "malformed-fenced", "malformed-bare", "echoed-text", "echoed-message", "escaped-braces"])
async def test_final_content_uses_reply_objects_or_prose_without_protocol(setup, client, content, expected):
    rig, team = setup
    teacher = member(team)
    client.chats = [chat(content)]
    result = await typed(rig, "Explain this", teacher.id, language="en")
    [turn] = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
    assert result["text"] == turn["text"] == expected
    assert [m["text"] for m in turn["messages"] if m["kind"] == "reply"] == [expected]
    assert "```" not in str(turn) and "Echoed protocol" not in str(turn)
    if "Details." in content:
        assert turn["detail"] == "Details."


async def test_each_pre_tool_object_is_published_in_order_without_duplicate_prose(setup, client):
    rig, team = setup
    teacher = member(team)

    async def finish(**kwargs):
        [turn] = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
        assert [(m["kind"], m["text"]) for m in turn["messages"]] == [
            ("thinking", "The date chooses the lesson."), ("progress", "Checking the date."),
            ("thinking", "Then I can choose the example."), ("progress", "Choosing the example."),
        ]
        return chat('{"text":"Done.","thinking":"The example is ready."}')

    client.chats = [chat('''```json
{"thinking":"The date chooses the lesson.","progress":"Checking the date."}
```
Duplicate prose.
{"thinking":"Then I can choose the example.","progress":"Choosing the example."}
''', ("local_time", {})), finish]
    await typed(rig, "Choose a lesson", teacher.id, language="en")
    [turn] = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
    assert [(m["kind"], m["text"]) for m in turn["messages"]][-2:] == [
        ("thinking", "The example is ready."), ("reply", "Done."),
    ]


@pytest.mark.parametrize("before_tool", [True, False])
@pytest.mark.parametrize(("kind", "content", "expected", "limit"), [
    ("thinking", "先從基礎開始。" + "這段補充太長" * 30, "先從基礎開始。", 120),
    ("progress", "我來查找文章。" + "這段補充太長" * 15, "我來查找文章。", 60),
    ("thinking", "One choice. Another choice. Unneeded third sentence.", "One choice. Another choice.", 120),
    ("progress", "x" * 100, "x" * 59 + "…", 60),
], ids=["thinking-length", "progress-length", "two-sentences", "no-sentence-end"])
async def test_public_narration_has_hard_length_and_sentence_caps(setup, client, before_tool, kind, content, expected, limit):
    rig, team = setup
    teacher = member(team)
    public = json.dumps({kind: content})
    final = '{"text":"Done."}'
    client.chats = ([chat(public, ("local_time", {})), chat(final)] if before_tool
                    else [chat(public + "\n" + final)])
    await typed(rig, "Explain this", teacher.id)
    [turn] = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
    [message] = [m for m in turn["messages"] if m["kind"] == kind]
    assert message["text"] == expected and len(message["text"]) <= limit


@pytest.mark.parametrize("speaker", ["member", "raphael"])
@pytest.mark.parametrize(("language", "name"), [("zh-TW", "Traditional Chinese"), ("en", "English")])
async def test_live_prompt_separates_ui_narration_language_and_persona_replies(setup, client, speaker, language, name):
    rig, team = setup
    teacher = member(team, role="Japanese teacher", instructions="Teach and reply in Japanese.")
    speaker_id = teacher.id if speaker == "member" else 0

    client.chats = [chat('{"text":"練習しましょう。"}')]
    result = await typed(rig, "Explain the article", speaker_id, language=language)
    system = client.chat_calls[0]["messages"][0]["content"]
    user = client.chat_calls[0]["messages"][1]["content"]
    assert system.count('content must be ONLY {"thinking": "...", "progress": "..."}') == 1
    assert system.count("Final content must be ONLY one JSON object") == 1
    assert "no code fences, no prose before or after" in system
    assert "no separate thinking object" in system
    assert "Thinking and progress are ALWAYS in the owner's UI language" in system
    assert "even when the persona teaches or replies in another language" in system
    assert "at most 2 sentences (about 60 CJK characters)" in system
    assert "25 CJK characters or 25 words" in system
    assert '"text": the reply, at most 2000 characters' in system
    assert '"messages": up to four replies' in system
    assert '"text": the reply as one line' not in system
    assert name in user and "Owner's UI language:" in user
    assert "Reply messages follow the persona's language rules" in user
    if speaker == "member":
        assert "Teach and reply in Japanese." in system
        assert "Reply ONLY in" not in system
        assert "Replies follow the persona and the owner's instructions" in system
    assert result["text"] == "練習しましょう。"
