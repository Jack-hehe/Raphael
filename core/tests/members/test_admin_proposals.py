"""Owner-editable proposals exercise both production loops and authenticated MCP."""

import httpx
import pytest

from agent.test_narration import chat
from .test_answering import client, setup, typed, live_said, member  # noqa: F401
from .test_admin import member_rpc  # noqa: F401


@pytest.fixture(params=["api", "cli"])
def brain(request, setup, client, tmp_path, monkeypatch):
    rig, team = setup
    operations = []
    if request.param == "cli":
        from raphael_core.agent.cli.discovery import CliBrains
        from raphael_core.agent.cli.drivers import Driver, Event

        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex/auth.json").touch()
        rig.service._cli_brains = CliBrains(home=tmp_path, environ={}, commands={"codex": ["fake-driver"]})
        rig.settings.set("agent_model", "cli:codex:test")

        async def run(driver, **kwargs):
            before = await rig.call("members.list", {})
            async with httpx.AsyncClient(headers={"Authorization": "Bearer " + kwargs["token"]}, trust_env=False) as http:
                for index, (name, args) in enumerate(operations):
                    response = await http.post(kwargs["endpoint"], json={"jsonrpc": "2.0", "id": index,
                        "method": "tools/call", "params": {"name": name, "arguments": args}})
                    result = response.json()["result"]
                    assert not result["isError"]
                    assert "pending_approval" in str(result)
                    assert await rig.call("members.list", {}) == before
            yield Event("session", "codex-thread-1")
            yield Event("final", "草稿已準備好，等待確認。")

        monkeypatch.setattr(Driver, "run", run)

    def prepare(*calls):
        operations[:] = calls
        if request.param == "api":
            client.chats = [chat("Preparing proposals.", *calls), live_said("草稿已準備好，等待確認。")]
    prepare.cli = request.param == "cli"
    return prepare


@pytest.mark.parametrize("mode", ["ask", "direct"])
async def test_every_brain_collects_edits_and_applies_once(setup, brain, mode):
    rig, team = setup
    rig.settings.set("action_mode", mode)
    roles = ["COO", "PM", "RD", "QA", "Design", "Marketing", "Finance"]
    brain(("group_create", {"name": "Company"}), *[("member_create", {"name": role, "role": role,
        "personality": "Precise", "instructions": "Review evidence", "group": "Company"}) for role in roles])
    asked = await typed(rig, "Create the team", 0)
    assert len(asked["confirm"]["drafts"]) == 7
    assert len((await rig.call("members.list", {}))["members"]) == 1
    edited = asked["confirm"]["drafts"][:-1]
    edited[0] = {**edited[0], "instructions": "Owner edited prompt"}
    args = {"id": asked["confirm"]["id"], "approved": True, "drafts": edited}
    await rig.call("agent.confirm", args)
    before = await rig.call("members.list", {})
    assert len(before["members"]) == 7
    assert before["members"][1]["instructions"] == "Owner edited prompt"
    await rig.call("agent.confirm", args)
    assert await rig.call("members.list", {}) == before


@pytest.mark.parametrize("ending", ["cancel", "expiry", "resend"])
async def test_cancel_expiry_and_resend_never_revive_a_proposal(setup, brain, ending):
    rig, team = setup
    brain(("member_create", {"name": "Research", "role": "Research"}))
    asked = await typed(rig, "Create Research", 0)
    old = asked["confirm"]["id"]
    if ending == "expiry":
        rig.clock.now += 31
    elif ending == "cancel":
        await rig.confirm(old, False)
    else:
        brain(("member_create", {"name": "Research", "role": "Research"}))
        newer = await typed(rig, "Create Research", 0)
        await rig.confirm(newer["confirm"]["id"], False)
    await rig.confirm(old, True)
    assert (await rig.call("members.list", {}))["members"][1:] == []
    assert (await rig.call("members.list", {}))["groups"] == []


@pytest.mark.parametrize("approved", [False, True])
async def test_once_the_owner_answers_a_proposal_a_cli_brain_starts_a_new_thread(setup, brain, approved):
    # Live on Codex, the owner cancelled five member drafts; asked the same again, the brain's own thread still ended
    # at "the drafts are ready", so it said so and showed none. The next request starts from the history instead.
    rig, team = setup
    brain(("member_create", {"name": "Research", "role": "Research"}))
    asked = await typed(rig, "Create Research", 0)
    main = rig.service.conversations.main(0)
    if brain.cli:
        assert rig.service._cli_sessions.get(0, "cli:codex:test", main) == "codex-thread-1"
    await rig.confirm(asked["confirm"]["id"], approved)
    assert rig.service._cli_sessions.get(0, "cli:codex:test", main) is None


async def test_first_bot_profile_uses_the_same_draft_and_survives_service_restart(setup, brain, tmp_path):
    from raphael_core.members.service import MembersService

    rig, team = setup
    before = await rig.call("members.list", {})
    assert before["members"][0]["profile_ready"] is False
    brain(("member_update", {"id": 0, "name": "晨星", "personality": "Precise", "instructions": "Research daily"}))
    asked = await typed(rig, "Name yourself 晨星", 0)
    assert asked["confirm"]["drafts"][0]["member_id"] == 0
    waiting = await rig.call("members.list", {})
    assert waiting["members"][0]["name"] == before["members"][0]["name"]
    assert waiting["members"][0]["profile_ready"] is False
    await rig.call("agent.confirm", {"id": asked["confirm"]["id"], "approved": True, "drafts": asked["confirm"]["drafts"]})
    after = await rig.call("members.list", {})
    assert after["members"][0]["name"] == "晨星"
    assert after["members"][0]["profile_ready"] is True
    assert len(after["members"]) == 1
    assert (await rig.turns())[0]["text"] == "已重新命名為 晨星"
    restarted = MembersService(team.conn, tmp_path, background=team.background)
    assert restarted.speaker(0).personality == "Precise"
    assert restarted.speaker(0).instructions == "Research daily"
    assert restarted.find("晨星").id == 0
    await rig.confirm(asked["confirm"]["id"])
    assert await rig.call("members.list", {}) == after


async def test_edited_draft_cannot_replace_the_proposed_target(setup, brain):
    rig, team = setup
    one, other = member(team, name="One"), member(team, name="Other")
    brain(("member_update", {"id": one.id, "name": "Edited"}))
    asked = await typed(rig, "Rename One", 0)
    draft = {**asked["confirm"]["drafts"][0], "member_id": other.id}
    await rig.call("agent.confirm", {"id": asked["confirm"]["id"], "approved": True, "drafts": [draft]})
    assert [item.name for item in team.members.store.members()] == ["One", "Other"]


async def test_sequential_profile_edits_merge_into_one_complete_draft(setup, brain):
    rig, team = setup
    one = member(team, name="Research")
    brain(("member_update", {"id": one.id, "personality": "Precise"}),
          ("member_update", {"id": one.id, "instructions": "Check sources"}))
    asked = await typed(rig, "Update Research", 0)
    drafts = asked["confirm"]["drafts"]
    assert len(drafts) == 1
    assert (drafts[0]["personality"], drafts[0]["instructions"]) == ("Precise", "Check sources")
    await rig.call("agent.confirm", {"id": asked["confirm"]["id"], "approved": True, "drafts": drafts})
    saved = team.members.store.member(one.id)
    assert (saved.personality, saved.instructions) == ("Precise", "Check sources")


async def test_group_only_proposal_accepts_owner_edits_and_applies_once(setup, brain):
    rig, team = setup
    group = team.members.store.create_group("Original")
    brain(("group_rename", {"id": group.id, "name": "Proposed"}))
    asked = await typed(rig, "Rename the group", 0)
    assert team.members.store.group(group.id).name == "Original"
    args = {"id": asked["confirm"]["id"], "approved": True, "drafts": [],
            "groups": [{**asked["confirm"]["groups"][0], "name": "Owner edited"}]}
    await rig.call("agent.confirm", args)
    assert team.members.store.group(group.id).name == "Owner edited"
    await rig.call("agent.confirm", args)
    assert len(team.members.store.groups()) == 1


async def test_first_bot_cancel_and_invalid_batch_leave_profile_unset(setup, brain):
    rig, team = setup
    brain(("member_update", {"id": 0, "name": "Morning", "instructions": "Read notes"}))
    asked = await typed(rig, "Set up the first Bot", 0)
    await rig.confirm(asked["confirm"]["id"], False)
    assert not team.members.first_bot_profile()
    brain(("member_update", {"id": 0, "name": "Morning"}),
          ("member_create", {"name": "X" * 17, "role": "Invalid"}))
    asked = await typed(rig, "Set up both", 0)
    await rig.confirm(asked["confirm"]["id"])
    assert not team.members.first_bot_profile()
    assert not team.members.store.members()


async def test_owner_edits_are_checked_against_current_safety_rules(setup, brain):
    from types import SimpleNamespace
    from raphael_core.agent.safety import Screening, DENY

    rig, team = setup
    brain(("member_create", {"name": "Research", "role": "Research"}))
    asked = await typed(rig, "Create Research", 0)
    seen = []
    async def blocked(proposed, **kwargs):
        seen.append(proposed)
        return Screening(DENY)
    rig.service._gate._safety = SimpleNamespace(screen=blocked)
    draft = {**asked["confirm"]["drafts"][0], "instructions": "Edited after proposal"}
    await rig.call("agent.confirm", {"id": asked["confirm"]["id"], "approved": True, "drafts": [draft]})
    assert seen
    assert not team.members.store.members()
