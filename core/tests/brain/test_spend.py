import time
from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from raphael_core.brain import db
from raphael_core.brain.spend import SpendTracker

TPE = timezone(timedelta(hours=8))


def ms(y, m, d, h=12):
    return int(datetime(y, m, d, h, tzinfo=TPE).timestamp() * 1000)


def add(conn, when_ms, cost, *, purpose="cleanup"):
    """Record a call that cost `cost` US dollars at the time `when_ms`."""
    SpendTracker(conn, now_ms=lambda: when_ms).record(
        purpose=purpose, model="m", cost_usd=cost, prompt_tokens=1, completion_tokens=1
    )


def tracker_at(conn, now_ms, tz=TPE):
    return SpendTracker(conn, now_ms=lambda: now_ms, tz=tz)


# The month's and the days' totals.


def test_month_total_uses_local_calendar_month(conn):
    s = SpendTracker(conn, now_ms=lambda: ms(2026, 10, 17), tz=TPE)
    for when, cost in [(ms(2026, 9, 30), 5.0), (ms(2026, 10, 1, 1), 1.0), (ms(2026, 10, 17), 0.7)]:
        SpendTracker(conn, now_ms=lambda w=when: w, tz=TPE).record(
            purpose="cleanup", model="m", cost_usd=cost, prompt_tokens=1, completion_tokens=1
        )
    assert s.month_total() == pytest.approx(1.7)
    assert s.today_total() == pytest.approx(0.7)
    assert s.forecast_month_end() == pytest.approx(1.7 / 17 * 31)
    assert s.is_over(1.7) and not s.is_over(1.71)


def test_daily_totals_has_today_last(conn):
    s = SpendTracker(conn, now_ms=lambda: ms(2026, 10, 17), tz=TPE)
    s.record(purpose="cleanup", model="m", cost_usd=0.2, prompt_tokens=1, completion_tokens=1)
    days = s.daily_totals(30)
    assert len(days) == 30 and days[-1] == pytest.approx(0.2) and sum(days[:-1]) == 0


# Recording.


@pytest.mark.parametrize("period, expected", [
    ("week", 1.7), ("month", 2.0), ("last_month", 5.0), ("all", 7.0),
])
def test_bill_uses_ledger_calendar_and_groups_every_purpose(conn, period, expected):
    for when, cost, purpose in [
        (ms(2026, 9, 30), 5.0, "agent"),
        (ms(2026, 10, 1, 1), .3, "agent"),
        (ms(2026, 10, 19), .8, "stt"),
        (ms(2026, 10, 20), .4, "cleanup"),
        (ms(2026, 10, 21), .2, "memory"),
        (ms(2026, 10, 22), .1, "future-purpose"),
        (ms(2026, 10, 23), .2, "another-purpose"),
    ]:
        add(conn, when, cost, purpose=purpose)
    ledger = tracker_at(conn, ms(2026, 10, 24))
    bill = ledger.bill(period)
    assert bill["cost_usd"] == pytest.approx(expected)
    assert sum(row["share"] for row in bill["purposes"]) == pytest.approx(1)
    if period == "month":
        assert bill["cost_usd"] == ledger.month_total()
        amounts = {row["purpose"]: row["cost_usd"] for row in bill["purposes"]}
        assert amounts == pytest.approx(dict(agent=.3, stt=.8, cleanup=.4, memory=.2, other=.3))


def test_empty_bill_has_finite_zero_shares(conn):
    bill = tracker_at(conn, ms(2026, 10, 24)).bill("all")
    assert bill["cost_usd"] == 0
    assert len(bill["purposes"]) == 4
    assert all(row["cost_usd"] == row["share"] == 0 for row in bill["purposes"])


def test_month_bill_matches_sidebar_at_local_boundary_with_future_rows(conn):
    ledger = tracker_at(conn, ms(2026, 10, 1, 1))
    for when, cost in [(ms(2026, 9, 30), 9), (ms(2026, 10, 1, 0), .1),
                       (ms(2026, 10, 31), .2), (ms(2026, 11, 1, 0), 8)]:
        add(conn, when, cost)
    assert ledger.bill("month")["cost_usd"] == ledger.month_total() == .1 + .2


def test_bill_uses_dst_aware_calendar_month(conn):
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    now = int(datetime(2026, 11, 20, tzinfo=zone).timestamp() * 1000)
    ledger = tracker_at(conn, now, tz=zone)
    for day in [datetime(2026, 11, 1, tzinfo=zone), datetime(2026, 12, 1, tzinfo=zone)]:
        add(conn, int(day.timestamp() * 1000), .2)
    assert ledger.bill("month")["cost_usd"] == ledger.month_total() == .2


def test_record_saves_what_it_is_given_and_the_time_of_the_clock(conn):
    s = SpendTracker(conn, now_ms=lambda: 1_700_000_000_123, tz=TPE)
    s.record(
        purpose="cleanup",
        model="google/gemini-3.1-flash-lite",
        cost_usd=0.0003,
        prompt_tokens=120,
        completion_tokens=45,
    )
    s.record(purpose="other", model=None, cost_usd=0.5, prompt_tokens=0, completion_tokens=0)
    rows = conn.execute(
        "SELECT created_at_ms, purpose, model, cost_usd, prompt_tokens, completion_tokens"
        " FROM spend ORDER BY id"
    )
    assert [tuple(row) for row in rows] == [
        (1_700_000_000_123, "cleanup", "google/gemini-3.1-flash-lite", 0.0003, 120, 45),
        (1_700_000_000_123, "other", None, 0.5, 0, 0),
    ]


def test_the_default_clock_is_the_wall_clock_in_milliseconds(conn):
    before = time.time_ns() // 1_000_000
    SpendTracker(conn).record(
        purpose="cleanup", model="m", cost_usd=0.1, prompt_tokens=1, completion_tokens=1
    )
    after = time.time_ns() // 1_000_000
    assert before <= conn.execute("SELECT created_at_ms FROM spend").fetchone()[0] <= after


def test_a_recorded_call_is_committed_for_other_connections(tmp_path):
    # A second connection sees only what the first has committed.
    path = tmp_path / "raphael.db"
    writer, reader = db.connect(path), db.connect(path)
    tracker_at(writer, ms(2026, 10, 17)).record(
        purpose="cleanup", model="m", cost_usd=0.2, prompt_tokens=1, completion_tokens=1
    )
    assert tracker_at(reader, ms(2026, 10, 17)).month_total() == pytest.approx(0.2)
    writer.close()
    reader.close()


def test_a_free_call_is_recorded(conn):
    s = tracker_at(conn, ms(2026, 10, 17))
    s.record(purpose="cleanup", model="m", cost_usd=0, prompt_tokens=1, completion_tokens=1)
    assert tuple(conn.execute("SELECT count(*), sum(cost_usd) FROM spend").fetchone()) == (1, 0.0)
    assert s.month_total() == 0.0


@pytest.mark.parametrize("cost", [-0.0001, float("nan"), float("inf"), float("-inf")])
def test_a_cost_that_is_negative_or_not_finite_is_refused_and_not_saved(conn, cost):
    s = tracker_at(conn, ms(2026, 10, 17))
    with pytest.raises(ValueError, match="cost_usd"):
        s.record(purpose="cleanup", model="m", cost_usd=cost, prompt_tokens=1, completion_tokens=1)
    assert conn.execute("SELECT count(*) FROM spend").fetchone()[0] == 0


# Totals.


def test_nothing_spent_is_zero_dollars_and_a_zero_forecast(conn):
    s = tracker_at(conn, ms(2026, 10, 17))
    for total in (s.month_total(), s.today_total(), s.forecast_month_end()):
        assert total == 0.0 and isinstance(total, float)
    assert s.daily_totals(5) == [0.0] * 5
    assert not s.is_over(10.0)


def test_every_purpose_counts_towards_the_month(conn):
    add(conn, ms(2026, 10, 17), 1.0, purpose="cleanup")
    add(conn, ms(2026, 10, 17), 2.0, purpose="something else")
    assert tracker_at(conn, ms(2026, 10, 17)).month_total() == pytest.approx(3.0)


def test_a_month_runs_from_local_midnight_on_the_first_up_to_the_next_first(conn):
    add(conn, ms(2026, 10, 1, 0) - 1, 1000.0)  # the last millisecond of September
    add(conn, ms(2026, 10, 1, 0), 1.0)  # the first millisecond of October
    add(conn, ms(2026, 11, 1, 0) - 1, 2.0)  # the last millisecond of October
    add(conn, ms(2026, 11, 1, 0), 400.0)  # the first millisecond of November
    # The clock moves from one month to the next at the same instant as the rows do.
    assert tracker_at(conn, ms(2026, 10, 1, 0) - 1).month_total() == pytest.approx(1000.0)
    assert tracker_at(conn, ms(2026, 10, 1, 0)).month_total() == pytest.approx(3.0)
    assert tracker_at(conn, ms(2026, 10, 17)).month_total() == pytest.approx(3.0)
    assert tracker_at(conn, ms(2026, 11, 1, 0) - 1).month_total() == pytest.approx(3.0)
    assert tracker_at(conn, ms(2026, 11, 1, 0)).month_total() == pytest.approx(400.0)


def test_a_day_runs_from_local_midnight_up_to_the_next_midnight(conn):
    add(conn, ms(2026, 10, 17, 0) - 1, 0.1)  # the last millisecond of the 16th
    add(conn, ms(2026, 10, 17, 0), 0.2)  # the first millisecond of the 17th
    add(conn, ms(2026, 10, 18, 0) - 1, 0.4)  # the last millisecond of the 17th
    add(conn, ms(2026, 10, 18, 0), 0.8)  # the first millisecond of the 18th
    assert tracker_at(conn, ms(2026, 10, 17, 0) - 1).today_total() == pytest.approx(0.1)
    assert tracker_at(conn, ms(2026, 10, 17, 0)).today_total() == pytest.approx(0.6)
    assert tracker_at(conn, ms(2026, 10, 18, 0) - 1).today_total() == pytest.approx(0.6)
    assert tracker_at(conn, ms(2026, 10, 18, 0)).today_total() == pytest.approx(0.8)


def test_the_totals_follow_the_clock_while_the_tracker_lives(conn):
    now = ms(2026, 10, 31, 23)
    s = SpendTracker(conn, now_ms=lambda: now, tz=TPE)
    s.record(purpose="cleanup", model="m", cost_usd=2.0, prompt_tokens=1, completion_tokens=1)
    assert s.month_total() == pytest.approx(2.0)
    assert s.today_total() == pytest.approx(2.0)

    now = ms(2026, 11, 1, 0)  # midnight: a new day and a new month
    assert s.month_total() == 0.0
    assert s.today_total() == 0.0
    assert not s.is_over(1.0)
    assert s.daily_totals(2) == pytest.approx([2.0, 0.0])


def test_the_month_after_december_is_january_of_the_next_year(conn):
    add(conn, ms(2026, 12, 31), 3.0)
    add(conn, ms(2027, 1, 1, 1), 5.0)

    december = tracker_at(conn, ms(2026, 12, 31, 23))
    assert december.month_total() == pytest.approx(3.0)
    assert december.forecast_month_end() == pytest.approx(3.0)  # 3.0 / 31 * 31

    january = tracker_at(conn, ms(2027, 1, 1))
    assert january.month_total() == pytest.approx(5.0)
    assert january.daily_totals(2) == pytest.approx([3.0, 5.0])


@pytest.mark.parametrize(
    ("year", "month", "day", "days_in_month"),
    [
        (2026, 10, 1, 31),
        (2026, 10, 17, 31),
        (2026, 4, 30, 30),
        (2026, 2, 14, 28),
        (2028, 2, 10, 29),  # a leap year
        (2026, 12, 31, 31),
    ],
)
def test_forecast_spreads_the_month_so_far_over_the_whole_month(
    conn, year, month, day, days_in_month
):
    s = tracker_at(conn, ms(year, month, day))
    s.record(purpose="cleanup", model="m", cost_usd=3.0, prompt_tokens=1, completion_tokens=1)
    assert s.forecast_month_end() == pytest.approx(3.0 / day * days_in_month)


def test_is_over_counts_the_limit_itself(conn):
    s = tracker_at(conn, ms(2026, 10, 17))
    s.record(purpose="cleanup", model="m", cost_usd=10.0, prompt_tokens=1, completion_tokens=1)
    assert s.is_over(10.0)
    assert s.is_over(9.99)
    assert not s.is_over(10.01)


def test_a_limit_of_zero_is_always_reached(conn):
    # With nothing spent, the month is already "at" a limit of 0: cleanup is switched off.
    assert tracker_at(conn, ms(2026, 10, 17)).is_over(0.0)


# Daily totals.


def test_daily_totals_adds_up_each_day_oldest_first(conn):
    add(conn, ms(2026, 10, 14, 23), 9.0)  # before the three days asked for
    add(conn, ms(2026, 10, 15, 8), 0.1)
    add(conn, ms(2026, 10, 15, 20), 0.2)
    add(conn, ms(2026, 10, 17, 9), 0.4)
    add(conn, ms(2026, 10, 18, 0), 8.0)  # tomorrow
    s = tracker_at(conn, ms(2026, 10, 17))
    assert s.daily_totals(3) == pytest.approx([0.3, 0.0, 0.4])
    assert s.daily_totals(1) == pytest.approx([0.4])
    assert s.daily_totals(2) == pytest.approx([0.0, 0.4])


def test_daily_totals_covers_30_days_by_default(conn):
    add(conn, ms(2026, 9, 17, 23), 9.0)  # the 30th day back is 18 September
    add(conn, ms(2026, 9, 18, 0), 0.5)
    add(conn, ms(2026, 10, 17, 9), 0.4)
    days = tracker_at(conn, ms(2026, 10, 17)).daily_totals()
    assert len(days) == 30
    assert days[0] == pytest.approx(0.5)
    assert days[-1] == pytest.approx(0.4)
    assert sum(days[1:-1]) == 0


@pytest.mark.parametrize("days", [0, -1])
def test_daily_totals_needs_at_least_one_day(conn, days):
    with pytest.raises(ValueError, match="days"):
        tracker_at(conn, ms(2026, 10, 17)).daily_totals(days)


# Time zones.


class Berlin2026(tzinfo):
    """Central European Time with summer time, for 2026 only (it is winter time in other years).

    Winter time is UTC+1. Summer time, UTC+2, starts on 29 March, when the clocks jump from 02:00
    to 03:00, and ends on 25 October, when they go back from 03:00 to 02:00. The first of those
    days lasts 23 hours and the second 25.
    """

    _HOUR = timedelta(hours=1)
    _STARTS = datetime(2026, 3, 29, 2)  # on the wall clock of winter time
    _ENDS = datetime(2026, 10, 25, 3)  # on the wall clock of summer time

    def dst(self, dt):
        wall = dt.replace(tzinfo=None)
        if self._STARTS + self._HOUR <= wall < self._ENDS - self._HOUR:
            return self._HOUR
        if self._ENDS - self._HOUR <= wall < self._ENDS:  # 02:00-03:00 happens twice
            return timedelta(0) if dt.fold else self._HOUR
        if self._STARTS <= wall < self._STARTS + self._HOUR:  # 02:00-03:00 does not happen
            return self._HOUR if dt.fold else timedelta(0)
        return timedelta(0)

    def utcoffset(self, dt):
        return self._HOUR + self.dst(dt)

    def tzname(self, dt):
        return "CEST" if self.dst(dt) else "CET"


BERLIN = Berlin2026()


@pytest.mark.parametrize(
    ("month", "first_day", "hours"),
    [(3, 28, 23), (10, 24, 25)],
    ids=["summer time starts", "summer time ends"],
)
def test_a_day_is_a_calendar_day_even_when_summer_time_changes_its_length(
    conn, month, first_day, hours
):
    def at(day, hour, minute=0):
        return int(datetime(2026, month, day, hour, minute, tzinfo=BERLIN).timestamp() * 1000)

    odd_day, today = first_day + 1, first_day + 2
    assert at(today, 0) - at(odd_day, 0) == hours * 3_600_000  # the middle day is that long
    add(conn, at(first_day, 23, 30), 0.1)
    add(conn, at(odd_day, 0, 30), 0.2)
    add(conn, at(odd_day, 23, 30), 0.4)
    add(conn, at(today, 0, 30), 0.8)

    s = tracker_at(conn, at(today, 12), tz=BERLIN)
    assert s.daily_totals(3) == pytest.approx([0.1, 0.6, 0.8])
    assert s.today_total() == pytest.approx(0.8)


def test_without_a_tz_the_local_time_zone_is_used(conn):
    here = timezone(timedelta(seconds=time.localtime().tm_gmtoff))  # this machine's zone

    def local_ms(y, m, d, h=0):
        return int(datetime(y, m, d, h, tzinfo=here).timestamp() * 1000)

    add(conn, local_ms(2026, 10, 1) - 1, 5.0)  # the last millisecond of September, locally
    add(conn, local_ms(2026, 10, 1), 1.0)
    add(conn, local_ms(2026, 10, 17) - 1, 0.3)  # the last millisecond of the 16th, locally
    add(conn, local_ms(2026, 10, 17), 0.7)
    s = SpendTracker(conn, now_ms=lambda: local_ms(2026, 10, 17, 12))
    assert s.month_total() == pytest.approx(2.0)
    assert s.today_total() == pytest.approx(0.7)
    assert s.daily_totals(2) == pytest.approx([0.3, 0.7])


@pytest.mark.parametrize("now", [0, 1_000, 86_400_000])
def test_a_clock_at_the_start_of_1970_works_without_a_tz(conn, now):
    # The platform's local-time functions fail for such instants on Windows (OSError), so the
    # tracker must not depend on them for the day and month boundaries.
    s = SpendTracker(conn, now_ms=lambda: now)
    s.record(purpose="cleanup", model="m", cost_usd=0.5, prompt_tokens=1, completion_tokens=1)
    assert s.month_total() == pytest.approx(0.5)
    assert s.today_total() == pytest.approx(0.5)
    assert s.daily_totals(2)[-1] == pytest.approx(0.5)
    assert s.forecast_month_end() >= 0.5 - 1e-9  # at least the month so far, in any zone
    assert s.is_over(0.5)
