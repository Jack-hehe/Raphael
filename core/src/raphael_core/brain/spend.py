"""Spend tracking: what the paid calls cost, and whether this month's limit is used up.

Every paid call is one row of the `spend` table, with the cost the API reported for it. The month
and the day are local calendar ones: each starts at midnight in the tracker's time zone and ends
where the next one starts. In Taipei, a call made at 01:00 on the 1st counts for the new month
although it is still the old month in UTC.
"""

import math
import sqlite3
import time
from bisect import bisect_right
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, tzinfo

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_DAY = timedelta(days=1)
_ONE_MS = timedelta(milliseconds=1)


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _day_start_ms(day: date, zone: tzinfo) -> int:
    """The moment `day` starts in `zone`, in milliseconds since the epoch.

    This is plain arithmetic on aware datetimes. The platform's local-time functions are avoided on
    purpose: on Windows they fail (OSError) for instants near the epoch, which a test clock can use.
    """
    return (datetime(day.year, day.month, day.day, tzinfo=zone) - _EPOCH) // _ONE_MS


def _month_range(day: date) -> tuple[date, date]:
    """The first day of the month `day` is in, and the first day of the month after it."""
    first = day.replace(day=1)
    return first, date(first.year + first.month // 12, first.month % 12 + 1, 1)


class SpendTracker:
    """Records what each paid call cost and answers how much has been spent.

    `now_ms` is the clock (milliseconds since the epoch) and is read on every call, so the month
    and the day follow it. `tz` is the time zone of the months and days. With the default, None,
    it is this machine's local zone: its UTC offset is looked up on every call, so it follows a
    machine that moves between zones. That offset is today's, used for every date, so in a zone with
    summer time a day or month that began before the last clock change is cut an hour off, and a
    call made in that hour counts for the neighbouring day or month. A zone that knows its own
    rules (zoneinfo) has no such error.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        now_ms: Callable[[], int] = _wall_clock_ms,
        tz: tzinfo | None = None,
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms
        self._tz = tz

    def record(
        self,
        *,
        purpose: str,
        model: str | None,
        cost_usd: float,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Write down one paid call, made now.

        Raises ValueError, and stores nothing, for a cost that is negative or not a finite number:
        it would lower the month's total or ruin it, and the monthly limit rests on that total.
        """
        if not (math.isfinite(cost_usd) and cost_usd >= 0):
            raise ValueError(f"cost_usd must be a finite number, 0 or more, not {cost_usd!r}")
        with self._conn:
            self._conn.execute(
                "INSERT INTO spend (created_at_ms, purpose, model, cost_usd, prompt_tokens,"
                " completion_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                (self._now_ms(), purpose, model, cost_usd, prompt_tokens, completion_tokens),
            )

    def month_total(self) -> float:
        """What has been spent this calendar month, in US dollars."""
        zone, today = self._zone_and_today()
        first, following = _month_range(today)
        return self._spent_between(_day_start_ms(first, zone), _day_start_ms(following, zone))

    def today_total(self) -> float:
        """What has been spent today, in US dollars."""
        zone, today = self._zone_and_today()
        return self._spent_between(
            _day_start_ms(today, zone), _day_start_ms(today + _ONE_DAY, zone)
        )

    def forecast_month_end(self) -> float:
        """What the month will have cost by its last day, if every day costs the average so far.

        That is the month's total, divided by the days gone (today counts as a whole one) and
        multiplied by the days in the month. It is 0.0 while nothing has been spent.
        """
        zone, today = self._zone_and_today()
        first, following = _month_range(today)
        spent = self._spent_between(_day_start_ms(first, zone), _day_start_ms(following, zone))
        return spent / today.day * (following - first).days

    def is_over(self, limit_usd: float) -> bool:
        """Whether this month's total has reached `limit_usd`. Being at the limit is being over."""
        return self.month_total() >= limit_usd

    def daily_totals(self, days: int = 30) -> list[float]:
        """What was spent on each of the last `days` days, the oldest first and today last.

        Raises ValueError for fewer than one day.
        """
        if days < 1:
            raise ValueError(f"days must be at least 1, not {days}")
        zone, today = self._zone_and_today()
        first = today - timedelta(days=days - 1)
        # Day i runs from starts[i] to starts[i + 1]; the last start is the start of tomorrow.
        starts = [_day_start_ms(first + timedelta(days=i), zone) for i in range(days + 1)]
        totals = [0.0] * days
        rows = self._conn.execute(
            "SELECT created_at_ms, cost_usd FROM spend"
            " WHERE created_at_ms >= ? AND created_at_ms < ?",
            (starts[0], starts[-1]),
        )
        for created_at_ms, cost_usd in rows:
            totals[bisect_right(starts, created_at_ms) - 1] += cost_usd
        return totals

    def _zone_and_today(self) -> tuple[tzinfo, date]:
        """The time zone in force now, and the date it is now in that zone."""
        zone = self._tz
        if zone is None:
            zone = datetime.now(UTC).astimezone().tzinfo
        moment = _EPOCH + timedelta(milliseconds=self._now_ms())
        return zone, moment.astimezone(zone).date()

    def _spent_between(self, start_ms: int, end_ms: int) -> float:
        """What was spent from `start_ms` up to, but not including, `end_ms`."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM spend"
            " WHERE created_at_ms >= ? AND created_at_ms < ?",
            (start_ms, end_ms),
        ).fetchone()
        return float(row[0])
