"""A background task or a routine is told the owner's primary contact from the roster as it is when the task starts."""

import json
from datetime import timezone

import pytest

from members.conftest import QuietModel
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.members.service import MembersService
from raphael_core.memory.service import MemoryService
from .conftest import turn, until


def told(system):
    return system.split("\nPrimary contact: ")[1].split("\nApps:")[0] if "\nPrimary contact: " in system else ""


@pytest.mark.parametrize("selected,expected", [
    ("teacher", ["hand it to the primary contact", "ask_member"]),
    ("coach", ["That is you", "hand such a request to him"]),
    ("raphael", ["conclude each one and alone administer the team"]),
])
async def test_a_task_is_told_who_the_primary_contact_is_and_what_that_makes_of_it(make_rig, conn, tmp_path, selected, expected):
    rig = make_rig()
    memory = MemoryService(conn, tmp_path / "memory", QuietModel(), MemoryKeyStore(), quiet_s=3600, start_delay_s=3600)
    members = MembersService(conn, tmp_path, background=rig.service, memory=memory, now_ms=rig.wall, tz=timezone.utc)
    memory.member_exists = members.member_exists

    async def unused(*args, **kwargs):
        pytest.fail("No team tool is needed")

    rig.service.use_memory(memory)
    rig.service.use_team(members, unused)
    teacher = members.store.create_member(name="Teacher", role="Japanese")
    coach = members.store.create_member(name="Coach", role="Running")
    members.store.set_primary(coach.id)
    speaker = {"teacher": teacher, "coach": coach}.get(selected)
    rig.client.replies.append(turn(json.dumps({"prefix": "告", "text": "Ready.", "detail": ""})))
    try:
        task_id = rig.service.submit(session_id="task", text="Start.", language="en", model="fake/model",
                                     member_id=speaker.id if speaker else 0)
        await until(lambda: rig.task(task_id).state not in ("queued", "running"))
        system = rig.client.chat_calls[0]["messages"][0]["content"]
        assert told(system).startswith(f'"Coach" (id {coach.id}).'), system
        for words in expected:
            assert words in told(system)
        assert f'"primary_id": {coach.id}' in system and '"chief_id": 0' in system
    finally:
        await members.aclose()
        await memory.aclose()
