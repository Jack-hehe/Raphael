"""When a routine runs: its schedule, and the next moment it is due.

A schedule is one of five, as JSON (the routine's `schedule`, and the RPC's):

- `{"kind": "daily", "time": "HH:MM"}`: every day at that local time;
- `{"kind": "once", "start_date": "YYYY-MM-DD", "time": "HH:MM"}`: one local moment;
- `{"kind": "weekdays", "time": "HH:MM"}`: Monday through Friday;
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

KINDS = ("once", "daily", "weekdays", "weekly", "every")
_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
HOUR_MS = 3_600_000
MAX_HOURS = 24


class ScheduleError(ValueError):
    """An unknown schedule or one with a value out of range."""


@dataclass(frozen=True)
class Schedule:
    kind: str
    time: str = ""  # "HH:MM" for daily and weekly
    days: tuple[int, ...] = ()  # weekly: 0 Sunday ... 6 Saturday, sorted, each once
    hours: int = 0  # every
    start_date: str = ""
    max_runs: int | None = None
    until: str = ""

    def to_json(self) -> dict[str, Any]:
        value: dict[str, Any] = {"kind": self.kind}
        if self.time:
            value["time"] = self.time
        if self.kind == "weekly":
            value["days"] = list(self.days)
        if self.kind == "every":
            value["hours"] = self.hours
        if self.start_date:
            value["start_date"] = self.start_date
        if self.max_runs is not None:
            value["max_runs"] = self.max_runs
        if self.until:
            value["until"] = self.until
        return value


def _whole(value: Any) -> int | None:
    return value if type(value) is int else None


def _date(value: Any, field: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ScheduleError(f"{field} must be YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ScheduleError(f"{field} must be a calendar date") from None
    return value


def parse_schedule(value: Any) -> Schedule:
    schedule = _parse_kind(value)
    start = _date(value.get("start_date"), "start_date")
    until = _date(value.get("until"), "until")
    if start and until and until < start:
        raise ScheduleError("until must not precede start_date")
    count = value.get("max_runs")
    if count is not None and (_whole(count) is None or not 1 <= count <= 10000):
        raise ScheduleError("max_runs must be a whole number from 1 to 10000")
    time = value.get("time", schedule.time)
    if schedule.kind == "every" and (start or "time" in value) and (not isinstance(time, str) or not _TIME.fullmatch(time)):
        raise ScheduleError("a start date needs HH:MM")
    if schedule.kind == "once" and not start:
        raise ScheduleError("once needs start_date")
    return Schedule(schedule.kind, time=time, days=schedule.days, hours=schedule.hours,
                    start_date=start, max_runs=count, until=until)


def _parse_kind(value: Any) -> Schedule:
    """The schedule `value` stands for, or ScheduleError."""
    if not isinstance(value, dict):
        raise ScheduleError("a schedule is an object")
    kind = value.get("kind")
    if kind not in KINDS:
        raise ScheduleError("kind must be once, daily, weekdays, weekly or every")
    if kind == "every":
        hours = _whole(value.get("hours"))
        if hours is None or not 1 <= hours <= MAX_HOURS:
            raise ScheduleError(f"hours must be a whole number from 1 to {MAX_HOURS}")
        return Schedule("every", hours=hours)
    time = value.get("time")
    if not isinstance(time, str) or not _TIME.fullmatch(time):
        raise ScheduleError("time must be HH:MM")
    if kind in ("once", "daily", "weekdays"):
        return Schedule(kind, time=time)
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


def next_run(schedule: Schedule, after_ms: int, *, tz: tzinfo | None = None) -> int | None:
    """The next occurrence, including the end date's whole local day; None after the end."""
    due = _next(schedule, after_ms, tz=tz)
    if due is not None and schedule.until and _local(due, tz).date().isoformat() > schedule.until:
        return None
    return due


def _next(schedule: Schedule, after_ms: int, *, tz: tzinfo | None = None) -> int | None:
    """The first moment strictly after `after_ms` at which `schedule` is due, in ms."""
    anchor = None
    if schedule.start_date:
        anchor = datetime.fromisoformat(schedule.start_date + "T" + (schedule.time or "00:00"))
        if tz is not None:
            anchor = anchor.replace(tzinfo=tz)
    if schedule.kind == "once":
        assert anchor is not None
        return _ms(anchor) if _ms(anchor) > after_ms else None
    if schedule.kind == "every":
        step = schedule.hours * HOUR_MS
        return after_ms + step if anchor is None else (
            _ms(anchor) + max(0, (after_ms - _ms(anchor)) // step + 1) * step
        )
    hour, minute = (int(part) for part in schedule.time.split(":"))
    start = _local(after_ms, tz)
    if anchor is not None:
        start = max(start, anchor)
    for offset in range(8):  # today and the seven days after it cover every weekly schedule
        day = (start + timedelta(days=offset)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if schedule.kind == "weekly" and _js_day(day) not in schedule.days:
            continue
        if schedule.kind == "weekdays" and _js_day(day) not in (1, 2, 3, 4, 5):
            continue
        due = _ms(day)
        if due > after_ms:
            return due
    raise AssertionError("a daily or weekly schedule is due within eight days")  # pragma: no cover
