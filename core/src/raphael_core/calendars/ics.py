"""One day's events out of the text of an iCalendar (ICS) file.

`events_on` returns what is on a day of this computer's time zone: timed and all-day events,
recurring ones expanded (an RRULE with its EXDATEs and RDATEs, and the RECURRENCE-ID overrides that
move or replace a single occurrence), cancelled ones left out, and ones that began the day before
and run past midnight included. The parsing is icalendar's (BSD) and the recurrence is
python-dateutil's rrule.

What it leaves out: an override that says THISANDFUTURE (a change to one occurrence and all after
it), RSCALE rules (calendars other than the Gregorian) and periods in an RDATE. A recurrence rule
that dateutil cannot read shows its first occurrence only, when that falls on the day.

A time without a zone (a "floating" time) is the time of day in this computer's zone, and so is one
whose zone name nobody knows. A file is read for at most `READ_BUDGET_S`: a pathological one cannot
hold up the core.

Nothing here logs a title, a description, a place or an address.
"""

import logging
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from datetime import time as clock_time
from math import ceil
from typing import Any

from dateutil.rrule import rrulestr
from icalendar import Calendar

logger = logging.getLogger(__name__)

# The most events read from one calendar, and the most occurrences of one recurring event stepped
# through (a daily event from 1990 is 13 000): a rule such as FREQ=SECONDLY would otherwise run
# for ever.
MAX_EVENTS = 20_000
MAX_STEPS = 50_000
# The longest one calendar may take to read.
READ_BUDGET_S = 5.0
# The most events one calendar gives for a day, and the longest title kept.
MAX_PER_DAY = 200
TITLE_MAX_CHARS = 200


class CalendarFormatError(ValueError):
    """The text is not an iCalendar."""


@dataclass(frozen=True)
class Occurrence:
    """An event on the day. An all-day event runs from the midnight that starts its first day to
    the midnight that ends its last."""

    title: str
    start_ms: int
    end_ms: int
    all_day: bool


class SystemZone(tzinfo):
    """This computer's local time, with its clock changes.

    A fixed offset read once would be wrong for the days on the other side of a change, so every
    time is asked for the offset of its own date.
    """

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        if dt is None:
            return None
        try:
            return dt.replace(tzinfo=None).astimezone().utcoffset()
        except (OverflowError, OSError, ValueError):
            # Windows cannot place a date before 1970 or far ahead: today's offset will do
            return datetime.now().astimezone().utcoffset()

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None

    def fromutc(self, dt: datetime) -> datetime:
        return dt.replace(tzinfo=UTC).astimezone().replace(tzinfo=self)


@dataclass(frozen=True)
class _Window:
    """The day, as the instants it starts and ends at (UTC), and as a date."""

    day: date
    zone: tzinfo
    lo: datetime
    hi: datetime

    @classmethod
    def of(cls, day: date, zone: tzinfo) -> "_Window":
        def midnight(d: date) -> datetime:
            return datetime.combine(d, clock_time.min, tzinfo=zone).astimezone(UTC)

        return cls(day, zone, midnight(day), midnight(day + timedelta(days=1)))

    def holds(self, start: datetime, end: datetime) -> bool:
        """Whether an event from `start` to `end` (UTC) is on the day: it starts before the day
        ends, and starts on the day or ends after the day starts. (A moment on the very midnight
        that starts the day is on that day.)"""
        return start < self.hi and (start >= self.lo or end > self.lo)


def events_on(
    data: str | bytes,
    day: date,
    tz: tzinfo | None = None,
    *,
    budget_s: float = READ_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> list[Occurrence]:
    """The events of `day` in `tz` (this computer's zone by default) that the iCalendar `data`
    holds, all-day events first and then by start. Raises CalendarFormatError when `data` is not
    an iCalendar; an event that cannot be read is skipped."""
    zone = SystemZone() if tz is None else tz
    window = _Window.of(day, zone)
    deadline = clock() + budget_s
    events = _vevents(_parse(data))

    # A series is told apart from its overrides by RECURRENCE-ID. An override takes the place of the
    # occurrence it names, wherever it moved it to, so the series must not show that one as well.
    series: list[tuple[str, Any]] = []
    overrides: list[Any] = []
    replaced: dict[str, list[Any]] = {}
    skipped = 0
    for index, event in enumerate(events):
        uid = _text(event, "UID") or f"#{index}"
        try:
            if event.get("RECURRENCE-ID") is None:
                series.append((uid, event))
            else:
                replaced.setdefault(uid, []).append(event["RECURRENCE-ID"].dt)
                overrides.append(event)
        except Exception:
            skipped += 1

    found: list[Occurrence] = []
    jobs = [(event, replaced.get(uid, []), True) for uid, event in series]
    jobs += [(event, [], False) for event in overrides]
    for event, replaces, recurring in jobs:
        if clock() > deadline:
            logger.warning("A calendar took more than %.0f s to read; the rest was left out", budget_s)
            break
        try:
            found.extend(_occurrences(event, replaces, recurring, window, lambda: clock() > deadline))
        except Exception:
            skipped += 1
    if skipped:
        logger.warning("%d events of a calendar could not be read", skipped)
    found.sort(key=lambda o: (not o.all_day, o.start_ms, o.end_ms, o.title))
    return found[:MAX_PER_DAY]


def _parse(data: str | bytes) -> Calendar:
    try:
        calendar = Calendar.from_ical(data)
    except Exception:  # icalendar raises ValueError and others; the text may be anything
        raise CalendarFormatError("not an iCalendar") from None
    if not isinstance(calendar, Calendar):
        raise CalendarFormatError("not an iCalendar")
    return calendar


def _vevents(calendar: Calendar) -> list[Any]:
    events: list[Any] = []
    for event in calendar.walk("VEVENT"):
        if len(events) >= MAX_EVENTS:
            logger.warning("A calendar has more than %d events; the rest was left out", MAX_EVENTS)
            break
        events.append(event)
    return events


def _text(event: Any, name: str) -> str:
    value = event.get(name)
    return "" if value is None else str(value).strip()


def _title(event: Any) -> str:
    return " ".join(_text(event, "SUMMARY").split())[:TITLE_MAX_CHARS]


def _aware(value: datetime, zone: tzinfo) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=zone)


def _ms(value: datetime) -> int:
    return round(value.timestamp() * 1000)


def _date_of(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _values(event: Any, name: str) -> list[Any]:
    """The dates or times of every EXDATE (or RDATE) line of the event: there may be several
    lines, each with a list."""
    found = event.get(name)
    if found is None:
        return []
    lines = found if isinstance(found, list) else [found]
    return [item.dt for line in lines for item in line.dts]


def _occurrences(
    event: Any, replaced: list[Any], recurring: bool, window: _Window, late: Callable[[], bool]
) -> list[Occurrence]:
    """The occurrences of one event that fall on the day. `replaced` are the RECURRENCE-IDs of the
    overrides of the series; `recurring` is false for an override, whose own rule (it has none)
    would only be a mistake."""
    if _text(event, "STATUS").upper() == "CANCELLED":
        return []
    prop = event.get("DTSTART")
    if prop is None:
        return []
    start = prop.dt
    if isinstance(start, datetime):
        return _timed(event, start, replaced, recurring, window, late)
    return _all_day(event, start, replaced, recurring, window, late)


def _all_day(
    event: Any, first: date, replaced: list[Any], recurring: bool, window: _Window, late: Callable[[], bool]
) -> list[Occurrence]:
    title = _title(event)
    length = timedelta(days=_days_long(event, first))
    skip = {_date_of(value) for value in replaced + _values(event, "EXDATE")}
    zone = window.zone

    def starts() -> Iterator[date]:
        rule = _rule(event, datetime.combine(first, clock_time.min)) if recurring else None
        if rule is None:
            yield first
        else:
            for step, occurrence in enumerate(rule):
                if occurrence.date() > window.day or step >= MAX_STEPS or _out_of_time(step, late):
                    break
                yield occurrence.date()
        if recurring:
            yield from (_date_of(value) for value in _values(event, "RDATE"))

    found = []
    for begin in starts():
        if begin in skip or not begin <= window.day < begin + length:
            continue
        start = datetime.combine(begin, clock_time.min, tzinfo=zone)
        found.append(Occurrence(title, _ms(start), _ms(start + length), True))
    return found


def _timed(
    event: Any, start: datetime, replaced: list[Any], recurring: bool, window: _Window, late: Callable[[], bool]
) -> list[Occurrence]:
    title = _title(event)
    zone = window.zone
    first = _aware(start, zone)
    span = _span(event, first, zone)
    # An exact time skips that occurrence; a plain date (EXDATE;VALUE=DATE) skips the whole day.
    skipped = replaced + _values(event, "EXDATE")
    skip_instants = {_aware(v, zone).astimezone(UTC) for v in skipped if isinstance(v, datetime)}
    skip_days = {v for v in skipped if not isinstance(v, datetime)}

    def starts() -> Iterator[datetime]:
        rule = _rule(event, first) if recurring else None
        if rule is None:
            yield first
        else:
            for step, occurrence in enumerate(rule):
                if occurrence.astimezone(UTC) >= window.hi or step >= MAX_STEPS or _out_of_time(step, late):
                    break
                yield occurrence
        if recurring:
            yield from (_aware(v, zone) for v in _values(event, "RDATE") if isinstance(v, datetime))

    found = []
    for begin in starts():
        begin_utc = begin.astimezone(UTC)
        if begin_utc in skip_instants or begin.date() in skip_days:
            continue
        end_utc = begin_utc + span
        if window.holds(begin_utc, end_utc):
            found.append(Occurrence(title, _ms(begin_utc), _ms(end_utc), False))
    return found


def _out_of_time(step: int, late: Callable[[], bool]) -> bool:
    """Whether the time is up; asked every 256 steps, because the clock costs more than a step."""
    return step % 256 == 255 and late()


def _days_long(event: Any, first: date) -> int:
    """How many days an all-day event covers (at least one): its DTEND is the day after its last."""
    end = event.get("DTEND")
    if end is not None:
        days = (_date_of(end.dt) - first).days
    elif event.get("DURATION") is not None:
        days = ceil(event["DURATION"].dt / timedelta(days=1))
    else:
        days = 1
    return max(days, 1)


def _span(event: Any, first: datetime, zone: tzinfo) -> timedelta:
    """How long a timed event lasts: to its DTEND or for its DURATION, else no time at all."""
    end = event.get("DTEND")
    span = timedelta(0)
    if end is not None and isinstance(end.dt, datetime):
        span = _aware(end.dt, zone).astimezone(UTC) - first.astimezone(UTC)
    elif end is None and event.get("DURATION") is not None:
        span = event["DURATION"].dt
    return max(span, timedelta(0))


# UNTIL as a rule writes it: a day, then maybe a time of day, in UTC if it ends in Z.
_UNTIL = re.compile(r"UNTIL=(\d{8})(?:T(\d{6}))?(Z?)", re.IGNORECASE)


def _rule(event: Any, first: datetime) -> Any:
    """The event's RRULE as dateutil's rrule starting at `first`; None for an event that does not
    recur, and for a rule dateutil cannot read."""
    prop = event.get("RRULE")
    if prop is None:
        return None
    if isinstance(prop, list):
        prop = prop[0]
    try:
        return rrulestr(_until_fitted(prop.to_ical().decode("utf-8"), first), dtstart=first)
    except Exception:
        return None


def _until_fitted(text: str, first: datetime) -> str:
    """The rule with its UNTIL written the way dateutil wants it for a start like `first`: in UTC
    when the start has a zone, and as the end of its day when the start is an all-day date."""
    match = _UNTIL.search(text)
    if match is None:
        return text
    day, at, utc = match.groups()
    if first.tzinfo is None:
        until = f"{day}T235959"
    elif at and utc:
        return text
    else:
        moment = datetime.combine(
            datetime.strptime(day, "%Y%m%d").date(),
            datetime.strptime(at, "%H%M%S").time() if at else clock_time(23, 59, 59),
            tzinfo=first.tzinfo,
        )
        until = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{text[: match.start()]}UNTIL={until}{text[match.end():]}"
