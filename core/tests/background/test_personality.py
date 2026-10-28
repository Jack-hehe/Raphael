"""Background tasks and scheduled runs keep each speaker's character and private context."""

import json
from datetime import timezone

import pytest

from members.conftest import QuietModel
from raphael_core.agent.history import AgentHistory
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.members.service import MembersService
from raphael_core.memory.service import MemoryService
from .conftest import call, turn, until


@pytest.mark.parametrize("routine", [False, True], ids=["task", "routine"])
@pytest.mark.parametrize("selected", ["teacher", "coach", "raphael"])
async def test_worker_and_scheduler_use_only_the_selected_persona(make_rig, conn, tmp_path, routine, selected):
    rig = make_rig()
    memory = MemoryService(conn, tmp_path / "memory", QuietModel(), MemoryKeyStore(), quiet_s=3600, start_delay_s=3600)
    members = MembersService(conn, tmp_path, background=rig.service, memory=memory, now_ms=rig.wall, tz=timezone.utc)
    memory.member_exists = members.member_exists

    async def unused(*args, **kwargs):
        pytest.fail("No team tool is needed")

    rig.service.use_memory(memory)
    rig.service.use_team(members, unused)
    teacher = members.store.create_member(name="Teacher", role="Japanese", personality="STRICT-STYLE", instructions="Teach N1.")
    coach = members.store.create_member(name="Coach", personality="WARM-STYLE", instructions="Coach running.")
    who = {"teacher": teacher, "coach": coach}.get(selected)
    speaker_id = who.id if who else 0
    history = AgentHistory(conn)
    for person in (teacher, coach):
        history.add(session_id="old", question=f"THREAD-{person.name}", route="answer", prefix="", text="Previous answer.", status="done", member_id=person.id)
        memory.store_for(person.id).write(f"reference-{person.id}", type="reference", description=f"OWN-{person.name}", body="Private work context.")
    memory.shared.write("user-city", type="user", description="CITY-TAIPEI", body="The user lives in Taipei.")
    rig.client.replies.append(turn(json.dumps({"prefix": "告", "text": "Ready.", "detail": ""})))
    try:
        if routine:
            rig.client.replies.insert(0, turn("我来检查。", calls=[call("local_time", {})]))
            schedule = members.store.create_routine(member_id=speaker_id, title="Practice", prompt="Start a lesson.", schedule={"kind": "every", "hours": 1}, next_run_ms=rig.wall.now)
            assert members.tick() == [schedule.id]
            task_id = rig.changes()[-1]["task_id"]
        else:
            task_id = rig.service.submit(session_id="task", text="Start a lesson.", language="zh-TW", model="fake/model", member_id=speaker_id)
        await until(lambda: rig.task(task_id).state not in ("queued", "running"))
        assert rig.task(task_id).state == "done"
        if routine:
            turns = history.list(member_id=speaker_id)
            current = next(t for t in turns if t.id == rig.task(task_id).turn_id)
            progress = [m["text"] for m in history.messages([current.id])[current.id] if m["kind"] == "progress"]
            assert not progress
            assert any(method == "worker.progress" and data["turn_id"] == current.id and "我來檢查。" in data["text"] for method,data in rig.notifications)
        model_call = rig.client.chat_calls[0]
        system = model_call["messages"][0]["content"]
        shown = json.dumps(model_call["messages"], ensure_ascii=False)
        assert "CITY-TAIPEI" in shown
        assert "<team_roster>" in system
        assert "another member's thread" in system and "ask_member" in system
        assert "zh-TW" in system and "Traditional Chinese" in system
        assert "ask_member" in {t["function"]["name"] for t in model_call["tools"]}
        for person in (teacher, coach):
            if person.id != speaker_id:
                assert person.name in system and person.role in system
        if who:
            assert "拉斐爾" in system and "Core assistant" in system
            other = coach if who.id == teacher.id else teacher
            assert who.personality in system and who.instructions in system
            assert f"OWN-{who.name}" in shown and f"THREAD-{who.name}" in shown
            for marker in ("主人", "マスター", "《", "your master", "You are Raphael", other.personality, f"OWN-{other.name}", f"THREAD-{other.name}"):
                assert marker not in shown
        else:
            assert "You are Raphael" in system and "主人" in system
            for person in (teacher, coach):
                assert person.personality not in shown and f"OWN-{person.name}" not in shown
    finally:
        await members.aclose()
        await memory.aclose()


def test_background_briefs_are_scoped_before_limiting_to_the_latest_tasks(make_rig):
    rig = make_rig()
    store = rig.service._store
    own = store.create(session_id="teacher", language="zh-TW", text="TEACHER-ONLY", member_id=1)
    for index in range(4):
        store.create(session_id="coach", language="zh-TW", text=f"COACH-ONLY-{index}", member_id=2)
    store.create(session_id="core", language="zh-TW", text="CORE-ONLY", member_id=0)
    assert [b.text for b in rig.service.brief_for_assistant()] == ["CORE-ONLY"]
    assert [b.id for b in rig.service.brief_for_assistant(member_id=1)] == [own]
