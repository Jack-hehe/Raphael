"""Team administration through real turns, the shared gate and member service."""

import httpx

import pytest

from agent.test_narration import chat
from .test_answering import client, setup, member, typed, live_said  # noqa: F401


@pytest.fixture(autouse=True)
def member_rpc(setup):
    rig, team = setup
    team.members.register(rig.rpc)


async def test_api_turn_creates_the_requested_company_roles(setup, client):
    rig, team = setup
    client.chats = [
        chat("Creating the team.", *(("member_create", {"name": role, "role": role, "group": "Company"}) for role in ("COO", "PM", "RD"))),
        live_said("Created COO, PM and RD."),
    ]
    result = await typed(rig, "Create COO, PM, RD", 0, language="en")
    assert result["status"] == "done"
    roster = await rig.call("members.list", {})
    assert [(m["name"], m["role"]) for m in roster["members"][1:]] == [("COO", "COO"), ("PM", "PM"), ("RD", "RD")]
    assert len(roster["groups"]) == 1
    assert all(m["reply_style"] == "full" for m in roster["members"])
    assert all(m["group_id"] == roster["groups"][0]["id"] for m in roster["members"][1:])


@pytest.mark.parametrize("mode,approved", [("direct", True), ("ask", True), ("ask", False)])
@pytest.mark.parametrize("operation", ["member_create", "member_update"])
async def test_cli_turn_applies_or_denies_the_whole_batch_through_http_mcp(setup, tmp_path, monkeypatch, mode, approved, operation):
    from raphael_core.agent.cli.discovery import CliBrains
    from raphael_core.agent.cli.drivers import Driver, Event

    rig, team = setup
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/auth.json").touch()
    rig.service._cli_brains = CliBrains(home=tmp_path, environ={}, commands={"codex": ["unused-test-boundary"]})
    rig.settings.set("agent_model", "cli:codex:test")
    rig.settings.set("action_mode", mode)

    roles = ("COO", "PM", "RD", "CEO", "CTO", "Marketing", "Finance", "Design")
    existing = {role: member(team, name=role, role="Original") for role in roles} if operation == "member_update" else {}

    async def run(driver, **kwargs):
        assert "COO, PM, RD" in kwargs["user"]
        assert "You lead the owner's team" in kwargs["system"]
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + kwargs["token"]}, trust_env=False) as http:
            async def rpc(method, params):
                response = await http.post(kwargs["endpoint"], json={"jsonrpc": "2.0", "id": method, "method": method, "params": params})
                return response.json()["result"]

            tools = (await rpc("tools/list", {}))["tools"]
            offered = {tool["name"]: tool for tool in tools}
            assert "member_create" in offered
            assert "member_id" in offered["routine_create"]["inputSchema"]["properties"]
            assert (await rpc("tools/call", {"name": "group_create", "arguments": {"name": "Company"}}))["isError"] is False
            for role in roles:
                arguments = {"name": role, "role": role, "blurb": role + " lead", "group": "Company"}
                if existing:
                    arguments["id"] = existing[role].id
                result = await rpc("tools/call", {"name": operation, "arguments": arguments})
                assert result["isError"] is False
        yield Event("text_delta", '《告。{"text":"我先確認可用介面…","messages":[…],"say_ja":"…"}》')
        yield Event("tool_start")
        yield Event("final", "Created COO, PM and RD.")

    monkeypatch.setattr(Driver, "run", run)
    result = await typed(rig, "Create COO, PM, RD", 0, language="en")
    if mode == "ask":
        assert result["status"] == "confirm"
        text = result["confirm"]["text"]
        for role in roles:
            assert f"Name: {role}" in text and f"Role: {role}" in text and f"About: {role} lead" in text
        assert text.count("Group: Company") == 8
        before = await rig.call("members.list", {})
        assert before["groups"] == []
        assert len(before["members"][1:]) == len(existing)
        assert all(item["role"] == "Original" for item in before["members"][1:])
        result = await rig.confirm(result["confirm"]["id"], approved)
    assert result["status"] == "done"
    roster = await rig.call("members.list", {})
    assert [item["role"] for item in roster["members"][1:]] == (list(roles) if approved else ["Original"] * len(existing))
    assert len(roster["groups"]) == int(approved)
    [turn] = await rig.turns()
    assert turn["status"] == ("done" if approved else "denied")
    assert not any("{" in item["text"] for item in turn["messages"] if item["kind"] == "progress")


@pytest.mark.parametrize("tool", ["member_create", "create_member"])
async def test_ask_mode_previews_the_complete_change_and_resumes_the_same_turn(setup, client, tool):
    rig, team = setup
    rig.settings.set("action_mode", "ask")
    instructions = "Preserve all details. " * 50
    client.chats = [chat("Creating COO.", (tool, {"name": "COO", "role": "Operations", "instructions": instructions, "group": "Company"})), live_said("Created COO: Operations.")]
    result = await typed(rig, "Create COO", 0, language="en")
    assert result["status"] == "confirm"
    assert (await rig.call("members.list", {}))["members"][1:] == []
    assert instructions in result["confirm"]["text"]
    done = await rig.confirm(result["confirm"]["id"])
    assert done["status"] == "done" and done["turn_id"] == result["turn_id"]
    assert (await rig.call("members.list", {}))["members"][1]["instructions"] == instructions.strip()


async def test_ask_mode_names_the_target_and_shows_the_full_routine_prompt(setup, client):
    rig, team = setup
    one = member(team, name="COO")
    rig.settings.set("action_mode", "ask")
    prompt = "Review company operations and report concrete decisions. " * 12
    client.chats = [chat("Scheduling COO.", ("routine_create", {"member_id": one.id, "title": "Operations", "prompt": prompt, "schedule": {"kind": "daily", "time": "09:00"}})), live_said("Scheduled COO.")]
    result = await typed(rig, "Schedule COO's operations report", 0, language="en")
    assert result["status"] == "confirm"
    assert "COO" in result["confirm"]["text"] and prompt in result["confirm"]["text"]
    await rig.confirm(result["confirm"]["id"])
    assert (await rig.call("routines.list", {"member_id": one.id}))["routines"][0]["prompt"] == prompt.strip()


@pytest.mark.parametrize("approved", [True, False])
async def test_member_deletion_always_asks_and_obeys_the_answer(setup, client, approved):
    rig, team = setup
    one = member(team, name="COO")
    rig.settings.set("action_mode", "direct")
    client.chats = [chat("Deleting COO.", ("member_delete", {"id": one.id})), live_said("Deleted COO.")]
    asked = await typed(rig, "Delete COO", 0, language="en")
    assert asked["status"] == "confirm" and asked["confirm"]["tier"] == "T3"
    assert "COO" in asked["confirm"]["text"] and "all its data" in asked["confirm"]["text"]
    assert team.members.speaker(one.id) is not None
    await rig.confirm(asked["confirm"]["id"], approved)
    assert (team.members.speaker(one.id) is None) == approved


async def test_peer_cannot_discover_or_execute_admin_tools_or_select_peer_routines(setup, client):
    from raphael_core.protocol import RpcError
    from raphael_core.agent.team_admin import DEFINITIONS

    rig, team = setup
    one, other = member(team), member(team, name="Other")
    client.chats = [chat("Trying an unauthorized change.", ("member_create", {"name": "Hidden", "role": "COO"}), ("create_member", {"name": "Legacy hidden", "role": "COO", "instructions": ""})), live_said("No change.")]
    await typed(rig, "Create Hidden", one.id, language="en")
    offered = {item["function"]["name"]: item for item in client.chat_calls[0]["tools"]}
    assert not set(DEFINITIONS) & set(offered)
    assert "create_member" not in offered
    assert "member_id" not in offered["routine_create"]["function"]["parameters"]["properties"]
    assert len((await rig.call("members.list", {}))["members"]) == 3
    for name in DEFINITIONS:
        with pytest.raises(RpcError):
            await team.members.admin_tool(one.id, name, {})
    with pytest.raises(RpcError):
        await team.members.routine_tool(one.id, "routine_create", {"member_id": other.id, "title": "Stolen", "prompt": "X", "schedule": {"kind": "daily", "time": "08:00"}})


async def test_admin_turn_assigns_distinct_presets_and_enforces_ten_creates(setup, client, tmp_path):
    rig, team = setup
    folder = tmp_path / "avatars/presets"
    folder.mkdir(parents=True)
    for name in ("alpha", "beta"):
        (folder / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    client.chats = [chat("Creating the team.", *(("member_create" if i % 2 else "create_member", {"name": f"Member {i}", "role": "RD"}) for i in range(11))), live_said("Created ten members.")]
    await typed(rig, "Create eleven members", 0, language="en")
    roster = (await rig.call("members.list", {}))["members"][1:]
    assert len(roster) == 10
    assert {roster[0]["avatar"], roster[1]["avatar"]} == {"preset:alpha", "preset:beta"}
    assert all(item["avatar"] == "" for item in roster[2:])
    assert "member_create_limit" in client.chat_calls[-1]["messages"][-1]["content"]


async def test_legacy_create_reports_invalid_fields_without_failing_the_turn(setup, client):
    rig, team = setup
    member(team, name="COO")
    client.chats = [chat("Creating COO.", ("create_member", {"name": "COO", "role": "Operations"})), live_said("COO already exists.")]
    result = await typed(rig, "Create COO", 0, language="en")
    assert result["status"] == "done"
    assert len((await rig.call("members.list", {}))["members"]) == 2
    assert "bad_arguments" in client.chat_calls[-1]["messages"][-1]["content"]


async def test_admin_turn_updates_groups_profiles_routines_and_commands_and_emits_changes(setup, client):
    rig, team = setup
    one = member(team, name="COO")
    notes = []
    team.members._notify = lambda method, data: notes.append((method, data))
    client.chats = [
        chat("Organizing the team.", ("member_update", {"id": one.id, "blurb": "Operations lead", "personality": "Precise", "instructions": "Keep decisions clear.", "group": "Company"})),
        chat("Saving the command.", ("member_command_save", {"member_id": one.id, "name": "report", "description": "Weekly report", "template": "Report on {input}"})),
        chat("Scheduling the report.", ("routine_create", {"member_id": one.id, "title": "Report", "prompt": "Prepare report", "schedule": {"kind": "daily", "time": "09:00"}})),
        chat("Reading the team.", ("team_overview", {})), live_said("Updated COO."),
    ]
    await typed(rig, "Set up COO reports", 0, language="en")
    roster = await team.members.admin_tool(0, "team_overview", {})
    coo = next(item for item in roster["members"] if item["id"] == one.id)
    assert (coo["group"], coo["blurb"], coo["commands_count"]) == ("Company", "Operations lead", 1)
    assert coo["routines"][0]["title"] == "Report"
    updated = (await rig.call("members.list", {}))["members"][1]
    assert updated["personality"] == "Precise" and updated["instructions"] == "Keep decisions clear."
    assert {"members.changed", "members.commands.changed", "routines.changed"} <= {method for method, _ in notes}
    group_id = roster["groups"][0]["id"]
    await team.members.admin_tool(0, "group_rename", {"id": group_id, "name": "Operations"})
    await team.members.admin_tool(0, "member_update", {"id": one.id, "group": ""})
    assert (await rig.call("members.list", {}))["members"][1]["group_id"] is None
    assert (await team.members.admin_tool(0, "team_overview", {}))["groups"][0]["name"] == "Operations"
    routine_id = coo["routines"][0]["id"]
    assert (await team.members.routine_tool(0, "routine_update", {"member_id": one.id, "id": routine_id, "title": "New report"}))["routine"]["title"] == "New report"
    await team.members.routine_tool(0, "routine_delete", {"member_id": one.id, "id": routine_id})
    assert (await team.members.routine_tool(0, "routine_list", {"member_id": one.id}))["routines"] == []


async def test_skill_import_uses_the_shared_download_and_notifies_the_member(setup, client, monkeypatch):
    rig, team = setup
    one = member(team, name="COO")
    notes = []
    team.members._notify = lambda method, data: notes.append((method, data))
    real_client = httpx.AsyncClient
    document = "---\nname: operations\ndescription: Plan weekly operations\n---\nKeep clear decisions."

    def handle(request):
        assert request.url.host == "raw.githubusercontent.com"
        return httpx.Response(200, text=document)

    monkeypatch.setattr("raphael_core.members.capabilities.httpx.AsyncClient",
                        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)))
    client.chats = [chat("Importing the skill.", ("member_skill_import", {"member_id": one.id, "source": "owner/operations"})), live_said("Imported operations.")]
    await typed(rig, "Import the operations skill for COO", 0, language="en")
    [skill] = (await rig.call("members.skills.list", {"member_id": one.id}))["skills"]
    assert skill["name"] == "operations" and skill["enabled"] is True
    assert team.members.skill_read(one.id, "operations") == document
    assert ("members.skills.changed", {"member_id": one.id}) in notes
    overview = await team.members.admin_tool(0, "team_overview", {})
    assert next(item for item in overview["members"] if item["id"] == one.id)["skills_count"] == 1


@pytest.mark.parametrize("name,args", [
    ("member_create", {"name": "COO", "role": "RD", "group": "Unused"}),
    ("member_create", {"name": "X" * 17, "role": "RD", "group": "Unused"}),
    ("member_create", {"name": "New", "role": "RD", "avatar_preset": "missing"}),
    ("member_create", {"name": "New", "role": "RD", "model": "unapproved/model"}),
    ("member_delete", {"id": 0}),
    ("group_create", {"name": "X" * 25}),
])
async def test_admin_validation_matches_rpc_and_does_not_leave_partial_groups(setup, name, args):
    from raphael_core.protocol import RpcError

    rig, team = setup
    member(team, name="COO")
    before = await rig.call("members.list", {})
    with pytest.raises(RpcError):
        await team.members.admin_tool(0, name, args)
    assert await rig.call("members.list", {}) == before


@pytest.mark.parametrize("approved", [True, False])
async def test_api_one_turn_approves_or_denies_every_member_and_group(setup, client, approved):
    rig, team = setup
    rig.settings.set("action_mode", "ask")
    roles = ("COO", "PM", "RD", "CEO", "CTO", "Marketing", "Finance", "Design")
    client.chats = [chat("Planning the group.", ("group_create", {"name": "Company"})),
                    *[chat("Planning a member.", ("member_create", {"name": role, "role": role, "blurb": role + " lead", "group": "Company"})) for role in roles],
                    live_said("Team changes are ready for approval.")]
    asked = await typed(rig, "Create eight company roles", 0, language="en")
    assert asked["status"] == "confirm"
    for role in roles:
        assert f"Name: {role}" in asked["confirm"]["text"]
        assert f"Role: {role}" in asked["confirm"]["text"]
        assert f"About: {role} lead" in asked["confirm"]["text"]
    assert asked["confirm"]["text"].count("Group: Company") == 8
    before = await rig.call("members.list", {})
    assert len(before["members"]) == 1 and before["groups"] == []
    done = await rig.confirm(asked["confirm"]["id"], approved)
    assert done["status"] == "done"
    assert (await rig.turns())[0]["status"] == ("done" if approved else "denied")
    assert done["turn_id"] == asked["turn_id"]
    after = await rig.call("members.list", {})
    assert [m["role"] for m in after["members"][1:]] == (list(roles) if approved else [])
    assert len(after["groups"]) == int(approved)


@pytest.mark.parametrize("language,label,separator", [("en", "Name", ": "), ("zh-TW", "名稱", "："), ("ja", "名前", "：")])
async def test_approval_labels_use_the_language_colon(setup, client, language, label, separator):
    rig, team = setup
    rig.settings.set("action_mode", "ask")
    client.chats = [chat("Preparing the member.", ("member_create", {"name": "COO", "role": "Operations"})), live_said("Ready for approval.")]
    asked = await typed(rig, "Create COO", 0, language=language)
    assert label + separator + "COO" in asked["confirm"]["text"]
    if language != "en":
        assert ": " not in asked["confirm"]["text"]


@pytest.mark.parametrize("approved", [True, False])
async def test_api_batches_updates_with_their_target_profiles(setup, client, approved):
    rig, team = setup
    people = [member(team, name=role, role=role, blurb=role + " lead") for role in ("COO", "PM", "RD")]
    rig.settings.set("action_mode", "ask")
    client.chats = [chat("Planning updates.", *(("member_update", {"id": person.id, "instructions": "Report weekly", "group": "Company"}) for person in people)), live_said("Updates ready.")]
    asked = await typed(rig, "Update all profiles", 0, language="en")
    assert asked["status"] == "confirm"
    for person in people:
        for expected in (f"Name: {person.name}", f"Role: {person.role}", f"About: {person.blurb}", "Group: Company", "Instructions: Report weekly"):
            assert expected in asked["confirm"]["text"]
    assert all(person.instructions != "Report weekly" for person in team.members.store.members())
    await rig.confirm(asked["confirm"]["id"], approved)
    assert all((person.instructions == "Report weekly") == approved for person in team.members.store.members())
    assert len((await rig.call("members.list", {}))["groups"]) == int(approved)


@pytest.mark.parametrize("operation", ["member_create", "member_update"])
async def test_invalid_approved_batch_rolls_back_every_change_and_notification(setup, client, operation):
    rig, team = setup
    people = [member(team, name=role, role=role) for role in ("COO", "PM")] if operation == "member_update" else []
    notes = []
    team.members._notify = lambda method, data: notes.append((method, data))
    rig.settings.set("action_mode", "ask")
    changes = [{"name": "Valid", "role": "RD", "group": "Company"}, {"name": "X" * 17, "role": "PM", "group": "Company"}]
    if people:
        for change, person in zip(changes, people):
            change["id"] = person.id
    before = await rig.call("members.list", {})
    client.chats = [chat("Planning changes.", *((operation, change) for change in changes)), live_said("Changes ready.")]
    asked = await typed(rig, "Change both members", 0, language="en")
    assert asked["status"] == "confirm"
    await rig.confirm(asked["confirm"]["id"])
    after = await rig.call("members.list", {})
    assert after["members"][1:] == before["members"][1:] and after["groups"] == []
    assert not notes
    assert (await rig.turns())[0]["status"] == "error"


@pytest.mark.parametrize("language", ["en", "zh-TW", "ja"])
def test_every_approval_label_including_target_id_and_schedule_uses_local_punctuation(language):
    from raphael_core.agent.team_admin import CONFIRM_TOOLS, confirmation

    for name in CONFIRM_TOOLS:
        text = confirmation(name, {"id": 1, "name": "COO", "role": "Operations", "blurb": "Lead",
                                  "personality": "Precise", "instructions": "Report", "group": "Company",
                                  "avatar_preset": "alpha", "source": "repository", "description": "Daily",
                                  "template": "Report {input}", "title": "Report", "prompt": "Review",
                                  "schedule": {"kind": "daily", "time": "09:00"}, "enabled": True}, language, "COO")
        assert ("ID: 1" if language == "en" else "ID\uff1a1") in text
        assert ("time: 09:00" if language == "en" else "time\uff1a09:00") in text
        if language != "en":
            assert ": " not in text


async def test_roster_readers_never_observe_a_partially_applied_batch(setup, client):
    import asyncio

    rig, team = setup
    rig.settings.set("action_mode", "ask")
    roles = ("COO", "PM", "RD", "CEO", "CTO", "Marketing", "Finance", "Design")
    client.chats = [chat("Planning the team.", *(("member_create", {"name": role, "role": role, "group": "Company"}) for role in roles)), live_said("Ready.")]
    asked = await typed(rig, "Create eight roles", 0, language="en")
    assert asked["status"] == "confirm"
    applying = asyncio.create_task(rig.confirm(asked["confirm"]["id"]))
    observed = set()
    try:
        while not applying.done():
            roster = await rig.call("members.list", {})
            observed.add((len(roster["members"]) - 1, len(roster["groups"])))
            await asyncio.sleep(0)
        assert (await applying)["status"] == "done"
    finally:
        if not applying.done():
            applying.cancel()
            await asyncio.gather(applying, return_exceptions=True)
    assert observed <= {(0, 0), (8, 1)}
    assert len((await rig.call("members.list", {}))["members"]) == 9
