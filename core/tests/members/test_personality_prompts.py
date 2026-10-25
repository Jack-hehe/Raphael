"""Only the selected speaker's character reaches the answering model."""

import pytest

from agent.conftest import decision, tool_plan
from agent.test_narration import chat
from .test_answering import client, setup, voiced, member, typed, raphael_said, live_said  # noqa: F401


@pytest.mark.parametrize("source", ["app", "telegram"])
@pytest.mark.parametrize("selected", ["first", "second", "raphael"])
async def test_narrated_prompt_contains_only_its_own_persona_and_thread(setup, client, selected, source):
    rig, team = setup
    first = member(team, name="Teacher", role="Japanese", personality="STRICT-TEACHER-STYLE", instructions="Teach N1.")
    second = member(team, name="Coach", personality="WARM-COACH-STYLE", instructions="Coach running.")
    for person, secret in ((first, "TEACHER-THREAD"), (second, "COACH-THREAD")):
        rig.history.add(session_id="old", question=secret, route="answer", prefix="", text=secret, status="done", member_id=person.id)
    who = {"first": first, "second": second}.get(selected)
    client.chats = [chat('{"text":"Ready."}')]
    await typed(rig, "Start", who.id if who else 0, source=source)
    system = client.chat_calls[0]["messages"][0]["content"]
    messages = str(client.chat_calls[0]["messages"])
    assert "<team_roster>" in system
    for person in (first, second):
        if who is None or person.id != who.id:
            assert person.name in system and person.role in system
    if who:
        assert "拉斐爾" in system and "Core assistant" in system
    assert "ask_member" in system and "another member's thread" in system
    assert "zh-TW" in system and "Traditional Chinese" in system
    assert "ask_member" in {t["function"]["name"] for t in client.chat_calls[0]["tools"]}
    if who:
        assert who.personality in system and who.instructions in system
        assert "<persona>" in system and "<personality>" in system
        for marker in ("主人", "マスター", "《", "your master", "Quietly devoted", "You are Raphael"):
            assert marker not in system
        other = second if who.id == first.id else first
        assert other.personality not in messages and other.instructions not in messages
        assert ("COACH-THREAD" if who.id == first.id else "TEACHER-THREAD") not in messages
        assert "Address the user according to your own personality" in system
    else:
        assert "You are Raphael" in system and "主人" in system
        assert "Quietly devoted" in system
        for secret in (first.personality, second.personality, "TEACHER-THREAD", "COACH-THREAD"):
            assert secret not in messages


async def test_voice_answer_keeps_raphael_persona_without_member_personalities(setup, client):
    rig, team = setup
    member(team, personality="MEMBER-ONLY-CHARACTER")
    client.decisions.append(decision("chat"))
    client.replies.append(raphael_said("你好。"))
    await rig.ask("你好")
    system = client.json_calls[0]["system"]
    assert "You are Raphael" in system and "主人" in system
    assert "MEMBER-ONLY-CHARACTER" not in system
    assert "<team_roster>" in system


@pytest.mark.parametrize("caller", ["raphael", "coach"])
async def test_naming_another_members_lesson_queries_the_member_as_itself(setup, client, caller):
    rig, team = setup
    teacher = member(team, name="紫苑", role="日文老師", personality="STRICT-ONLY", instructions="Teach N1.")
    coach = member(team, name="Coach", personality="WARM-ONLY")
    client.chats = [
        chat("", ("ask_member", {"member": "紫苑", "request": "請回顧剛才教的日文句子。"})),
        live_said("雨が降りそうです。"),
        live_said("紫苑教的是：雨が降りそうです。"),
    ]
    result = await typed(rig, "剛剛紫苑教了幾句日文，你可以看一下嗎？", 0 if caller == "raphael" else coach.id)
    assert "雨が降りそうです。" in result["text"]
    assert "我無法讀取" not in result["text"]
    caller_system = client.chat_calls[0]["messages"][0]["content"]
    target_system = client.chat_calls[1]["messages"][0]["content"]
    assert "ask_member" in caller_system and "another member's thread" in caller_system
    assert teacher.name in caller_system and teacher.role in caller_system
    assert teacher.personality not in caller_system
    assert teacher.personality in target_system and coach.personality not in target_system
    for marker in ("主人", "マスター", "《", "You are Raphael"):
        assert marker not in target_system
    turns = (await rig.call("agent.history.list", {"member_id": 0 if caller == "raphael" else coach.id}))["turns"]
    turn = next(t for t in turns if t["id"] == result["turn_id"])
    assert any(s["tool"] == "ask_member" and s["outcome"] == "ok" for s in turn["steps"])


@pytest.mark.parametrize("source", ["app", "voice"])
async def test_relaying_a_lesson_preserves_the_owner_as_the_japanese_quotes_speaker(setup, client, source):
    rig, team = setup
    teacher = member(team, name="紫苑", role="日文老師")
    question = "先生、今日は何を勉強しますか？"
    client.chats = [live_said("今天練習自我介紹。\n先用中文說說你的興趣。")]
    await typed(rig, question, teacher.id)
    request = "紫苑剛剛教了我什麼？我想聽日文的部分。"
    answer = f"當時的日文原句只有你問的：『{question}』\n我當時用中文教了自我介紹。"
    final = f"那句日文是你問紫苑的：『{question}』。紫苑當時用中文教自我介紹。"
    arguments = {"member": teacher.name, "request": request}
    if source == "app":
        client.chats = [chat("", ("ask_member", arguments)), live_said(answer), live_said(final)]
        await typed(rig, request, 0)
        target_prompt = client.chat_calls[-2]["messages"][-1]["content"]
        tool = next(m["content"] for m in client.chat_calls[-1]["messages"] if m["role"] == "tool")
    else:
        client.decisions.append(decision("chat"))
        client.plans.append(tool_plan(("Raphael__ask_member", arguments)))
        client.chats = [live_said(answer)]
        client.replies = [raphael_said(final)]
        await rig.ask(request)
        target_prompt = client.chat_calls[-1]["messages"][-1]["content"]
        tool = client.json_calls[-1]["user"]
    assert f"owner: {question}" in target_prompt
    assert f"assistant (member {teacher.id}): 今天練習自我介紹。" in target_prompt
    assert f"assistant (member {teacher.id}): 先用中文說說你的興趣。" in target_prompt
    # The fake repeats the live model's unmarked quote; provenance comes from the real thread.
    assert f"紫苑: {answer.splitlines()[0]}" in str(tool)
    assert f"owner: {question}" in str(tool)
    for member_id in (0, teacher.id):
        scope = {"member_id": member_id}
        if source == "voice" and member_id == 0:
            scope["conversation_id"] = rig.service.conversations.quick()
        turns = (await rig.call("agent.history.list", scope))["turns"]
        exchange = next(t for t in turns if t["route"] == "exchange")
        assert exchange["question"] == f"Raphael: {request}"
        assert f"紫苑: {answer.splitlines()[0]}" in exchange["text"]
        assert f"owner: {question}" in exchange["text"]


async def test_relay_keeps_all_messages_without_misidentifying_other_sources_as_the_owner(setup, client):
    rig, team = setup
    teacher = member(team, name="紫苑")
    other = member(team, name="Other")
    for route, source, question, member_id in (
        ("exchange", "member", "ASSISTANT-REQUEST", teacher.id),
        ("routine", "routine", "ROUTINE-TITLE", teacher.id),
        ("answer", "app", "OTHER-THREAD-QUESTION", other.id),
        ("answer", "app", "PRIVATE-UNRELATED-QUESTION", teacher.id),
    ):
        rig.history.add(session_id="old", question=question, route=route, source=source,
                        prefix="", text="Done.", status="done", member_id=member_id)
    client.chats = [
        chat("", ("ask_member", {"member": teacher.name, "request": "回顧"})),
        chat('{"text":"Second.","messages":["ASSISTANT-REQUEST\\nROUTINE-TITLE\\nOTHER-THREAD-QUESTION","Second."]}'),
        live_said("Relayed."),
    ]
    await typed(rig, "問紫苑", 0)
    tool = next(m["content"] for m in client.chat_calls[-1]["messages"] if m["role"] == "tool")
    assert "紫苑: ASSISTANT-REQUEST" in tool and "紫苑: Second." in tool
    for line in ("ASSISTANT-REQUEST", "ROUTINE-TITLE", "OTHER-THREAD-QUESTION"):
        assert f"owner: {line}" not in tool
    assert "PRIVATE-UNRELATED-QUESTION" not in tool
    turns = (await rig.call("agent.history.list", {"member_id": 0}))["turns"]
    exchange = next(t for t in turns if t["route"] == "exchange")
    assert "紫苑: ASSISTANT-REQUEST" in exchange["text"] and "紫苑: Second." in exchange["text"]


@pytest.mark.parametrize("selected", ["raphael", "member"])
async def test_shared_harness_requires_original_quote_attribution(setup, client, selected):
    rig, team = setup
    teacher = member(team)
    client.chats = [live_said("Ready.")]
    await typed(rig, "Start", 0 if selected == "raphael" else teacher.id)
    system = client.chat_calls[0]["messages"][0]["content"]
    assert "attribute each quote to its original speaker" in system
    assert "Never present the owner's own words as another member's words" in system
    assert "owner:" in system


async def test_traditional_chinese_public_updates_and_reply_preserve_japanese(setup, client):
    rig, team = setup
    teacher = member(team)
    client.chats = [
        chat('{"thinking":"这句話需要检查。","progress":"我来确认。"}', ("local_time", {})),
        live_said("这句话的意思是什麼？\n学校で会いましょう。"),
    ]
    result = await typed(rig, "解釋這句話", teacher.id)
    assert result["text"] == "這句話的意思是什麼？\n学校で会いましょう。"
    turns = (await rig.call("agent.history.list", {"member_id": teacher.id}))["turns"]
    messages = turns[0]["messages"]
    assert next(m["text"] for m in messages if m["kind"] == "thinking") == "這句話需要檢查。"
    assert next(m["text"] for m in messages if m["kind"] == "progress") == "我來確認。"


async def test_voice_fetches_the_lesson_and_speaks_the_actual_japanese_sentences(voiced, client):
    rig, team, voice = voiced
    teacher = member(team, name="紫苑", role="日文老師", personality="STRICT-ONLY")
    sentence = "雨が降りそうです。"
    client.decisions.append(decision("chat"))
    client.plans.append(tool_plan(("Raphael__ask_member", {"member": "紫苑", "request": "剛才教的句子是什麼？"})))
    client.chats.append(live_said(sentence))
    client.replies.append(raphael_said(f"紫苑教的句子是：{sentence}", say_ja=sentence))
    result = await rig.ask("我剛剛在跟紫苑聊天，她在教我幾句日文，你可以看一下嗎？因為我想聽一下語音")
    assert sentence in result["text"] and "我無法讀取" not in result["text"]
    assert voice.said == [("解", sentence)]
    assert "Raphael__ask_member" in {t["function"]["name"] for t in client.plan_calls[0]["tools"]}
    prompt = client.json_calls[0]["system"]
    assert "<team_roster>" in prompt and teacher.name in prompt and teacher.role in prompt
    assert teacher.personality not in prompt
    assert "include the actual sentences" in prompt
    assert "Preserve those sentences in" in prompt


async def test_team_creation_keeps_personality_separate_from_work_instructions(setup, client):
    rig, team = setup
    client.chats = [
        chat("", ("create_member", {"name": "Teacher", "role": "Japanese", "personality": "Strict, patient.", "instructions": "Teach N1."})),
        live_said("Added."),
    ]
    await typed(rig, "Add a strict but patient Japanese teacher.", 0)
    created = team.members.find("Teacher")
    assert created is not None and created.personality == "Strict, patient."
    assert created.instructions == "Teach N1."
    definition = next(t for t in client.chat_calls[0]["tools"] if t["function"]["name"] == "create_member")
    assert "personality" in definition["function"]["parameters"]["properties"]
