"""When a routine runs: its schedule, and the next moment it is due.

A schedule is one of three, as JSON (the routine's `schedule`, and the RPC's):

- `{"kind": "daily", "time": "HH:MM"}`: every day at that local time;
- `{"kind": "weekly", "days": [0-6, ...], "time": "HH:MM"}`: on those days of the week at that local
  time. The days count as JavaScript's `Date.getDay` does, the app's own: 0 is Sunday, 1 Monday, up
  to 6, Saturday;
- `{"kind": "every", "hours": 1-24}`: every so many hours, counted from when it was last due (from
  when the routine was made or switched on, for the first time).

`next_run` is the first moment strictly after a given one at which the schedule is due, in
milliseconds since the epoch. The time of day is this PC's local time (or `tz`, for the tests), so a
routine at 09:00 runs at nine in the morning wherever the PC is, summer time included.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

KINDS = ("daily", "weekly", "every")
_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
HOUR_MS = 3_600_000
MAX_HOURS = 24


class ScheduleError(ValueError):
    """A schedule that is none of the three, or one with a value out of range."""


@dataclass(frozen=True)
class Schedule:
    kind: str
    time: str = ""  # "HH:MM" for daily and weekly
    days: tuple[int, ...] = ()  # weekly: 0 Sunday ... 6 Saturday, sorted, each once
    hours: int = 0  # every

    def to_json(self) -> dict[str, Any]:
        if self.kind == "daily":
            return {"kind": "daily", "time": self.time}
        if self.kind == "weekly":
            return {"kind": "weekly", "days": list(self.days), "time": self.time}
        return {"kind": "every", "hours": self.hours}


def _whole(value: Any) -> int | None:
    return value if type(value) is int else None


def parse_schedule(value: Any) -> Schedule:
    """The schedule `value` stands for, or ScheduleError."""
    if not isinstance(value, dict):
        raise ScheduleError("a schedule is an object")
    kind = value.get("kind")
    if kind not in KINDS:
        raise ScheduleError("kind must be daily, weekly or every")
    if kind == "every":
        hours = _whole(value.get("hours"))
        if hours is None or not 1 <= hours <= MAX_HOURS:
            raise ScheduleError(f"hours must be a whole number from 1 to {MAX_HOURS}")
        return Schedule("every", hours=hours)
    time = value.get("time")
    if not isinstance(time, str) or not _TIME.fullmatch(time):
        raise ScheduleError("time must be HH:MM")
    if kind == "daily":
        return Schedule("daily", time=time)
    days = value.get("days")
    if not isinstance(days, list) or not days:
        raise ScheduleError("days must be a list of days of the week")
    picked = {_whole(day) for day in days}
    if None in picked or not picked <= set(range(7)):
        raise ScheduleError("a day of the week is a whole number from 0 (Sunday) to 6")
    return Schedule("weekly", time=time, days=tuple(sorted(picked)))  # type: ignore[arg-type]


def _local(ms: int, tz: tzinfo | None) -> datetime:
    """The moment `ms` as a local time: naive in this PC's own time zone, or aware in `tz`."""
    if tz is None:
        return datetime.fromtimestamp(ms / 1000)
    return datetime.fromtimestamp(ms / 1000, tz)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _js_day(moment: datetime) -> int:
    """The day of the week as `Date.getDay` counts it: 0 Sunday ... 6 Saturday."""
    return (moment.weekday() + 1) % 7


def next_run(schedule: Schedule, after_ms: int, *, tz: tzinfo | None = None) -> int:
    """The first moment strictly after `after_ms` at which `schedule` is due, in ms."""
    if schedule.kind == "every":
        return after_ms + schedule.hours * HOUR_MS
    hour, minute = (int(part) for part in schedule.time.split(":"))
    start = _local(after_ms, tz)
    for offset in range(8):  # today and the seven days after it cover every weekly schedule
        day = (start + timedelta(days=offset)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if schedule.kind == "weekly" and _js_day(day) not in schedule.days:
            continue
        due = _ms(day)
        if due > after_ms:
            return due
    raise AssertionError("a daily or weekly schedule is due within eight days")  # pragma: no cover
