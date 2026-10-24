"""Full member turns preserve typed commands, approval cards and skill boundaries."""

import json

import pytest

from agent.test_narration import chat
from raphael_core.agent.history import AgentHistory
from raphael_core.agent.team import roster_prompt, team_tools_for

from .test_answering import client, setup, member, typed, live_said  # noqa: F401


async def test_slash_expands_model_input_but_preserves_original_owner_bubble(setup, client):
    rig, team = setup
    one = member(team)
    team.members.capabilities.save_command(one.id, None, "lesson", "Teach", "Teach this: {input}")
    client.chats = [live_said("Lesson.")]
    await typed(rig, "/lesson verbs", one.id, language="en")
    messages = str(client.chat_calls[0]["messages"])
    assert "Teach this: verbs" in messages
    assert AgentHistory(rig.conn).list(member_id=one.id)[0].question == "/lesson verbs"


@pytest.mark.parametrize("approved", [True, False])
async def test_member_contact_preference_uses_inline_yes_before_contact(setup, client, approved):
    rig, team = setup
    one = member(team, ask_before_members=True)
    member(team, name="Researcher")
    client.chats = [chat("Ask Researcher.", ("ask_member", {"member": "Researcher", "request": "Find it"})),
                    live_said("Research answer."), live_said("Final reply.")]
    asked = await typed(rig, "Ask Researcher", one.id, language="en")
    assert asked["status"] == "confirm"
    assert AgentHistory(rig.conn).list(member_id=team.members.store.by_name("Researcher").id) == []
    await rig.confirm(asked["confirm"]["id"], approved=approved)
    research = AgentHistory(rig.conn).list(member_id=team.members.store.by_name("Researcher").id)
    assert bool(research) is approved


async def test_skills_are_scoped_enabled_metadata_and_lazy_untrusted_document(setup, client):
    rig, team = setup
    one = member(team, reply_style="conversational", blurb="Language partner")
    other = member(team, name="Other")
    body = "a" * 5000 + "MIDDLE-SKILL-CONTENT" + "b" * 5000
    skill = team.members.capabilities.keep_skill(one.id, "owner/repo", "---\nname: verbs\ndescription: Practice verbs\n---\nPRIVATE-SKILL-BODY" + body)
    assert "skill_read" in team_tools_for(team.members, one.speaker(), always=True)
    assert "skill_read" not in team_tools_for(team.members, other.speaker(), always=True)
    prompt = roster_prompt(team.members, one.speaker(), "en")
    assert "Practice verbs" in prompt and "conversational" in prompt
    assert "Usually use one message" in prompt
    assert "only when the content is naturally separate" in prompt
    assert "no padding" in prompt
    assert "split into a few small messages" not in prompt
    assert "PRIVATE-SKILL-BODY" not in prompt
    assert "Language partner" in roster_prompt(team.members, other.speaker(), "en")
    client.chats = [chat("Reading the skill.", ("skill_read", {"name": "verbs"})), live_said("Try this verb.")]
    await typed(rig, "Help me practice", one.id, language="en")
    assert "PRIVATE-SKILL-BODY" in str(client.chat_calls[1]["messages"])
    assert "MIDDLE-SKILL-CONTENT" in str(client.chat_calls[1]["messages"])
    team.members.capabilities.update_skill(one.id, skill["id"], False)
    assert team.members.skill_read(one.id, "verbs") is None
    assert "skill_read" not in team_tools_for(team.members, one.speaker(), always=True)


async def test_clear_member_history_preserves_other_threads_and_own_notes(setup):
    rig, team = setup
    one, other = member(team), member(team, name="Other")
    history = AgentHistory(rig.conn)
    for person in (one, other):
        history.add(session_id=f"s{person.id}", question="Question", route="answer", prefix="", text="Answer", status="done", member_id=person.id)
    team.memory.store_for(one.id).write("reference-topic", type="reference", description="Topic", body="Topic details")
    rig.service._cli_sessions.put(one.id, "claude", "old-session")
    rig.service._cli_sessions.put(other.id, "claude", "other-session")
    named = rig.service.conversations.create(one.id)
    other_named = rig.service.conversations.create(other.id)
    for person, conversation in ((one, named), (other, other_named)):
        rig.service._cli_sessions.put(person.id, "claude", "named-session", conversation["id"])
        rig.service._conversation_of(person.id, conversation["id"])
        rig.conn.execute("INSERT INTO conversation_context_summaries VALUES (?, 'Private summary', 1, 1)", (conversation["id"],))
    rig.conn.commit()
    rig.service._background = team.background
    cleared = await rig.call("agent.history.clear", {"member_id": one.id})
    assert cleared["deleted"] == 1
    assert history.list(member_id=one.id) == []
    assert len(history.list(member_id=other.id)) == 1
    assert team.memory.store_for(one.id).get("reference-topic") is not None
    assert rig.service._cli_sessions.get(one.id, "claude") is None
    assert rig.service._cli_sessions.get(other.id, "claude") == "other-session"

    assert rig.service._cli_sessions.get(one.id, "claude", named["id"]) is None
    assert rig.service._cli_sessions.get(other.id, "claude", other_named["id"]) == "named-session"
    assert rig.conn.execute("SELECT conversation_id FROM conversation_context_summaries").fetchall()[0][0] == other_named["id"]
    assert rig.conn.execute("SELECT COUNT(*) FROM conversation_context_summaries").fetchone()[0] == 1
