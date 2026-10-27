"""The owner's page of a private skill: its document is read and edited there, its switch is the trigger, and
deleting it stops the trigger. What the model is shown of a skill (its name and description, which say when to
use it) and what `skill_read` gives follow the document at once."""

import pytest

from raphael_core.agent.team import SKILL_READ, roster_prompt, team_tools_for
from raphael_core.members.capabilities import MAX_SKILL_BYTES
from raphael_core.protocol import INVALID_PARAMS, RpcError

WRITING = "---\nname: writing\ndescription: Use when the owner asks for a clear summary\n---\nKeep it short."


async def a_member_with_a_skill(rig, text=WRITING):
    member = (await rig.call("members.create", {"name": "Ada"}))["member"]["id"]
    skill = rig.service.capabilities.keep_skill(member, "owner/repo", text)
    return member, skill


async def refused(rig, method, params):
    with pytest.raises(RpcError) as error:
        await rig.call(method, params)
    return error.value


async def test_the_page_reads_the_whole_document_even_of_a_skill_that_is_off(rig):
    member, skill = await a_member_with_a_skill(rig)
    await rig.call("members.skills.update", {"member_id": member, "id": skill["id"], "enabled": False})
    answered = await rig.call("members.skills.read", {"member_id": member, "id": skill["id"]})
    assert answered["text"] == WRITING
    assert (answered["skill"]["name"], answered["skill"]["enabled"]) == ("writing", False)
    # The model still reads nothing of a skill that is off.
    assert rig.service.skill_read(member, "writing") is None
    other = (await rig.call("members.create", {"name": "Other"}))["member"]["id"]
    assert (await refused(rig, "members.skills.read", {"member_id": other, "id": skill["id"]})).data["kind"] == "not_found"
    # Raphael has no skills of his own to read.
    assert (await refused(rig, "members.skills.read", {"member_id": 0, "id": skill["id"]})).code == INVALID_PARAMS


async def test_an_edited_document_is_what_the_model_reads_next_and_its_words_follow_it(rig):
    member, skill = await a_member_with_a_skill(rig)
    edited = "---\nname: summaries\ndescription: Use for a clear summary of a long mail thread\n---\nKeep it to three lines."
    answered = await rig.call("members.skills.update", {"member_id": member, "id": skill["id"], "text": edited})
    assert (answered["skill"]["name"], answered["skill"]["description"], answered["skill"]["enabled"]) == (
        "summaries", "Use for a clear summary of a long mail thread", True)
    assert (await rig.call("members.skills.read", {"member_id": member, "id": skill["id"]}))["text"] == edited
    assert [s["name"] for s in (await rig.call("members.skills.list", {"member_id": member}))["skills"]] == ["summaries"]
    # The next turn: the document, the name it is offered by and the words that say when to use it.
    assert rig.service.skill_read(member, "summaries") == edited and rig.service.skill_read(member, "writing") is None
    prompt = roster_prompt(rig.service, rig.service.speaker(member), "en")
    assert "Use for a clear summary of a long mail thread" in prompt and "Use when the owner asks" not in prompt
    # No temporary file is left beside it.
    assert [path.name for path in rig.service.capabilities._path(member, skill["id"]).parent.iterdir()] == [f"{skill['id']}.md"]


async def test_the_switch_and_the_document_change_together_or_not_at_all(rig):
    member, skill = await a_member_with_a_skill(rig)
    both = {"member_id": member, "id": skill["id"], "enabled": False, "text": WRITING.replace("short", "brief")}
    assert (await rig.call("members.skills.update", both))["skill"]["enabled"] is False
    assert "brief" in (await rig.call("members.skills.read", {"member_id": member, "id": skill["id"]}))["text"]
    # A switch that is not one refuses the edit that came with it.
    broken = {"member_id": member, "id": skill["id"], "enabled": "yes", "text": WRITING.replace("short", "long")}
    assert (await refused(rig, "members.skills.update", broken)).data["kind"] == "enabled"
    assert "brief" in (await rig.call("members.skills.read", {"member_id": member, "id": skill["id"]}))["text"]
    # Neither: nothing to change.
    assert (await refused(rig, "members.skills.update", {"member_id": member, "id": skill["id"]})).data["kind"] == "enabled"


@pytest.mark.parametrize("text,kind", [
    pytest.param("No frontmatter at all.", "name", id="no-frontmatter"),
    pytest.param("---\nname: writing\n---\nNo description.", "description", id="no-description"),
    pytest.param("---\nname: writing\ndescription: ok\n---\n" + "x" * MAX_SKILL_BYTES, "size", id="too-large"),
    pytest.param("   ", "skill", id="blank"),
    pytest.param("---\nname: writing\ndescription: ok\n---\nnul\x00byte", "skill", id="nul-byte"),
    pytest.param(42, "skill", id="not-text"),
])
async def test_an_edit_that_cannot_be_kept_changes_nothing(rig, text, kind):
    member, skill = await a_member_with_a_skill(rig)
    error = await refused(rig, "members.skills.update", {"member_id": member, "id": skill["id"], "text": text})
    assert error.data["kind"] == kind
    assert (await rig.call("members.skills.read", {"member_id": member, "id": skill["id"]}))["text"] == WRITING
    assert [s["name"] for s in (await rig.call("members.skills.list", {"member_id": member}))["skills"]] == ["writing"]


async def test_a_skill_cannot_take_the_name_of_another_of_the_same_member(rig):
    member, writing = await a_member_with_a_skill(rig)
    reading = rig.service.capabilities.keep_skill(member, "owner/other", "---\nname: reading\ndescription: Use to read\n---\nBody")
    error = await refused(rig, "members.skills.update", {"member_id": member, "id": reading["id"], "text": WRITING})
    assert error.data["kind"] == "taken"
    # Its own name is its own: keeping the name while editing the rest is no clash, nor is another case of it.
    same = await rig.call("members.skills.update", {"member_id": member, "id": writing["id"], "text": WRITING + "\nMore."})
    assert same["skill"]["name"] == "writing"


async def test_the_edit_is_announced_so_that_every_open_page_follows(rig):
    member, skill = await a_member_with_a_skill(rig)
    rig.notes.clear()
    await rig.call("members.skills.update", {"member_id": member, "id": skill["id"], "text": WRITING + "\nMore."})
    assert ("members.changed", {}) in rig.notes
    rig.notes.clear()
    await rig.call("members.skills.delete", {"member_id": member, "id": skill["id"]})
    assert ("members.changed", {}) in rig.notes


async def test_deleting_a_skill_stops_its_trigger(rig):
    member, skill = await a_member_with_a_skill(rig)
    speaker = rig.service.speaker(member)
    # With a skill, the model is told of it and is offered the tool that reads it.
    assert SKILL_READ in team_tools_for(rig.service, speaker, always=True)
    assert "Use when the owner asks for a clear summary" in roster_prompt(rig.service, speaker, "en")
    assert rig.service.skill_read(member, "writing") == WRITING
    document = rig.service.capabilities._path(member, skill["id"])
    assert (await rig.call("members.skills.delete", {"member_id": member, "id": skill["id"]}))["deleted"] is True
    # Without it: not offered, not told of, not readable by a name a model remembers, and its file is gone.
    assert SKILL_READ not in team_tools_for(rig.service, speaker, always=True)
    assert "Use when the owner asks" not in roster_prompt(rig.service, speaker, "en")
    assert rig.service.skill_read(member, "writing") is None and not document.exists()
    assert (await rig.call("members.skills.delete", {"member_id": member, "id": skill["id"]}))["deleted"] is False
    assert (await refused(rig, "members.skills.read", {"member_id": member, "id": skill["id"]})).data["kind"] == "not_found"
