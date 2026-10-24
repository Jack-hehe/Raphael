"""Schedule boundaries and the persisted routine execution ledger."""
from datetime import datetime, timedelta, timezone
import pytest
from raphael_core.agent.history import AgentHistory
from raphael_core.members.schedule import parse_schedule, next_run, ScheduleError

TZ = timezone(timedelta(hours=8))
def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=TZ).timestamp() * 1000)

@pytest.mark.parametrize("plan, after, expected", [
    ({"kind":"once", "start_date":"2026-10-24", "time":"09:00"}, "2026-10-24T08:59", "2026-10-24T09:00"),
    ({"kind":"once", "start_date":"2026-10-24", "time":"09:00"}, "2026-10-24T09:00", None),
    ({"kind":"weekdays", "time":"09:00"}, "2026-10-23T09:00", "2026-10-26T09:00"),
    ({"kind":"daily", "time":"09:00", "start_date":"2026-11-01"}, "2026-10-23T09:00", "2026-11-01T09:00"),
    ({"kind":"daily", "time":"23:59", "until":"2026-10-24"}, "2026-10-24T23:58", "2026-10-24T23:59"),
    ({"kind":"daily", "time":"23:59", "until":"2026-10-24"}, "2026-10-24T23:59", None),
    ({"kind":"every", "hours":3, "time":"09:00", "start_date":"2026-10-24"}, "2026-10-24T13:30", "2026-10-24T15:00"),
])
def test_taipei_schedule_boundaries(plan, after, expected):
    schedule = parse_schedule(plan)
    assert schedule.to_json() == plan
    assert next_run(schedule, ms(after), tz=TZ) == (ms(expected) if expected else None)

@pytest.mark.parametrize("change", [{"max_runs":True}, {"max_runs":0}, {"max_runs":10001}, {"start_date":"2026-02-30"}, {"start_date":"2026-10-25", "until":"2026-10-24"}])
def test_invalid_end_conditions(change):
    with pytest.raises(ScheduleError):
        parse_schedule({"kind":"daily", "time":"09:00", **change})

@pytest.mark.parametrize("time", [None, 10, "25:00", "09:70"])
def test_interval_rejects_invalid_optional_time(time):
    with pytest.raises(ScheduleError):
        parse_schedule({"kind": "every", "hours": 3, "time": time})

async def test_run_limit_and_completion_ledger(rig):
    made = await rig.call("routines.create", {"member_id":0, "title":"Report", "prompt":"Check progress", "schedule":{"kind":"daily", "time":"09:00", "max_runs":1}})
    routine = made["routine"]
    rig.wall.now = routine["next_run"]
    assert rig.service.tick() == [routine["id"]]
    updated = (await rig.call("routines.list", {}))["routines"][0]
    assert updated["next_run"] is None
    runs = (await rig.call("routines.runs", {"routine_id":routine["id"]}))["runs"]
    assert runs[0]["status"] == "running"
    assert runs[0]["scheduled"] == routine["next_run"]
    turn_id = runs[0]["turn_id"]
    AgentHistory(rig.conn).resolve(turn_id, prefix="", text="All clear\nNo new items", status="done", cost_usd=0.02)
    rig.service.turn_arrived(turn_id)
    done = (await rig.call("routines.runs", {"routine_id":routine["id"]}))["runs"][0]
    assert done["status"] == "done" and done["finished"] is not None
    assert done["summary"] == "All clear No new items" and done["cost"] == 0.02
    assert rig.service.tick(rig.wall.now + 86400000) == []
