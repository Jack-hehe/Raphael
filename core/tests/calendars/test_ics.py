"""Tests for the events of a day out of an iCalendar text (calendars/ics.py).

The zone is injected, so no test depends on the machine's: Taipei (UTC+8, no clock changes) for most
of them, New York where a clock change is the point. Today is Tuesday 2026-10-20.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from raphael_core.calendars import ics
from raphael_core.calendars.ics import CalendarFormatError, SystemZone, events_on

TPE = ZoneInfo("Asia/Taipei")
NYC = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 20)


def calendar(*events: str, extra: str = "") -> str:
    """An iCalendar with the events (each the lines between BEGIN:VEVENT and END:VEVENT)."""
    body = "".join(f"BEGIN:VEVENT\n{event.strip()}\nEND:VEVENT\n" for event in events)
    text = f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//\n{extra}{body}END:VCALENDAR\n"
    return text.replace("\n", "\r\n")


def ms(y, m, d, h=0, minute=0, tz=TPE) -> int:
    return int(datetime(y, m, d, h, minute, tzinfo=tz).timestamp() * 1000)


def titles(found) -> list[str]:
    return [o.title for o in found]


def on(text: str, day: date = TODAY, tz=TPE):
    return events_on(text, day, tz)


# --- timed and all-day events -----------------------------------------------------------------


def test_a_timed_event_with_a_zone_is_on_its_day_with_its_times():
    found = on(calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261020T093000\nDTEND;TZID=Asia/Taipei:20261020T103000\nSUMMARY:Standup"))
    assert found == [ics.Occurrence("Standup", ms(2026, 10, 20, 9, 30), ms(2026, 10, 20, 10, 30), False)]


def test_an_event_in_utc_is_placed_on_the_day_of_the_zone():
    # 18:00 UTC on the 19th is 02:00 on the 20th in Taipei, and 22:00 on the 19th in New York
    text = calendar("UID:a\nDTSTART:20261019T180000Z\nDTEND:20261019T190000Z\nSUMMARY:Late")
    assert titles(on(text)) == ["Late"]
    assert titles(on(text, tz=NYC)) == []
    assert titles(on(text, date(2026, 10, 19), tz=NYC)) == ["Late"]


def test_an_event_in_another_zone_is_converted():
    text = calendar("UID:a\nDTSTART;TZID=America/New_York:20261019T200000\nDTEND;TZID=America/New_York:20261019T210000\nSUMMARY:Call")
    found = on(text)  # 20:00 in New York (EDT) is 08:00 the next day in Taipei
    assert found == [ics.Occurrence("Call", ms(2026, 10, 20, 8), ms(2026, 10, 20, 9), False)]


def test_a_time_without_a_zone_is_the_time_of_day_in_the_zone():
    found = on(calendar("UID:a\nDTSTART:20261020T140000\nDTEND:20261020T150000\nSUMMARY:Floating"))
    assert found[0].start_ms == ms(2026, 10, 20, 14)
    assert titles(on(calendar("UID:a\nDTSTART:20261020T140000\nSUMMARY:x"), tz=NYC)) == ["x"]


def test_a_windows_zone_name_is_understood_and_an_unknown_one_is_local_time():
    windows = on(calendar("UID:a\nDTSTART;TZID=Taipei Standard Time:20261020T090000\nSUMMARY:Outlook"))
    assert windows[0].start_ms == ms(2026, 10, 20, 9)
    unknown = on(calendar("UID:a\nDTSTART;TZID=Mars/Olympus:20261020T090000\nSUMMARY:Mars"))
    assert unknown[0].start_ms == ms(2026, 10, 20, 9)


def test_a_duration_gives_the_end_and_no_end_means_a_moment():
    found = on(calendar("UID:a\nDTSTART:20261020T010000Z\nDURATION:PT1H30M\nSUMMARY:a", "UID:b\nDTSTART:20261020T040000Z\nSUMMARY:b"))
    assert [(o.title, o.end_ms - o.start_ms) for o in found] == [("a", 90 * 60_000), ("b", 0)]


def test_an_all_day_event_runs_from_midnight_to_midnight_and_comes_first():
    found = on(
        calendar(
            "UID:t\nDTSTART:20261020T000500Z\nDTEND:20261020T010000Z\nSUMMARY:Timed",
            "UID:a\nDTSTART;VALUE=DATE:20261020\nDTEND;VALUE=DATE:20261021\nSUMMARY:Holiday",
        )
    )
    assert titles(found) == ["Holiday", "Timed"]
    assert found[0] == ics.Occurrence("Holiday", ms(2026, 10, 20), ms(2026, 10, 21), True)


def test_an_all_day_event_ends_before_the_day_its_dtend_names():
    text = calendar("UID:a\nDTSTART;VALUE=DATE:20261019\nDTEND;VALUE=DATE:20261020\nSUMMARY:Yesterday")
    assert on(text) == []
    assert titles(on(text, date(2026, 10, 19))) == ["Yesterday"]


def test_a_several_day_event_is_on_each_of_its_days():
    text = calendar("UID:a\nDTSTART;VALUE=DATE:20261018\nDTEND;VALUE=DATE:20261022\nSUMMARY:Trip")
    for day in (18, 19, 20, 21):
        assert titles(on(text, date(2026, 10, day))) == ["Trip"], day
    assert on(text, date(2026, 10, 17)) == []
    assert on(text, date(2026, 10, 22)) == []


def test_an_all_day_event_without_an_end_or_a_duration_is_one_day():
    text = calendar("UID:a\nDTSTART;VALUE=DATE:20261020\nSUMMARY:One")
    assert titles(on(text)) == ["One"]
    assert on(text, date(2026, 10, 21)) == []


def test_an_event_on_another_day_is_left_out():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261021T090000\nDTEND;TZID=Asia/Taipei:20261021T100000\nSUMMARY:Tomorrow")
    assert on(text) == []


def test_events_are_sorted_by_start_then_title():
    found = on(
        calendar(
            "UID:c\nDTSTART:20261020T060000Z\nSUMMARY:Late",
            "UID:b\nDTSTART:20261020T010000Z\nSUMMARY:B",
            "UID:a\nDTSTART:20261020T010000Z\nSUMMARY:A",
        )
    )
    assert titles(found) == ["A", "B", "Late"]


def test_a_title_has_its_lines_joined_and_may_be_empty():
    text = calendar("UID:a\nDTSTART:20261020T010000Z\nSUMMARY:  Plan\\n  the   week \\, again ", "UID:b\nDTSTART:20261020T020000Z")
    assert titles(on(text)) == ["Plan the week , again", ""]


def test_a_very_long_title_is_cut():
    assert len(on(calendar("UID:a\nDTSTART:20261020T010000Z\nSUMMARY:" + "x" * 1000))[0].title) == ics.TITLE_MAX_CHARS


# --- cancelled events and midnight ------------------------------------------------------------


def test_a_cancelled_event_is_left_out():
    text = calendar("UID:a\nDTSTART:20261020T010000Z\nSTATUS:CANCELLED\nSUMMARY:Gone", "UID:b\nDTSTART:20261020T020000Z\nSTATUS:CONFIRMED\nSUMMARY:Here")
    assert titles(on(text)) == ["Here"]


def test_an_event_across_midnight_is_on_both_days():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261019T230000\nDTEND;TZID=Asia/Taipei:20261020T020000\nSUMMARY:Night")
    assert titles(on(text, date(2026, 10, 19))) == ["Night"]
    found = on(text)
    assert found == [ics.Occurrence("Night", ms(2026, 10, 19, 23), ms(2026, 10, 20, 2), False)]
    assert on(text, date(2026, 10, 21)) == []


def test_an_event_that_ends_at_midnight_is_not_on_the_next_day():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261019T220000\nDTEND;TZID=Asia/Taipei:20261020T000000\nSUMMARY:Evening")
    assert on(text) == []
    assert titles(on(text, date(2026, 10, 19))) == ["Evening"]


def test_a_moment_at_midnight_is_on_the_day_it_starts():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261020T000000\nSUMMARY:Midnight")
    assert titles(on(text)) == ["Midnight"]
    assert on(text, date(2026, 10, 19)) == []


def test_a_recurring_event_that_started_yesterday_evening_is_still_on():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261001T230000\nDTEND;TZID=Asia/Taipei:20261002T010000\nRRULE:FREQ=DAILY\nSUMMARY:Backup")
    found = on(text)
    assert [(o.start_ms, o.end_ms) for o in found] == [
        (ms(2026, 10, 19, 23), ms(2026, 10, 20, 1)),
        (ms(2026, 10, 20, 23), ms(2026, 10, 21, 1)),
    ]


# --- recurrence -------------------------------------------------------------------------------


def test_a_daily_rule_is_expanded_to_the_day():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20250101T090000\nDTEND;TZID=Asia/Taipei:20250101T093000\nRRULE:FREQ=DAILY\nSUMMARY:Daily")
    assert on(text) == [ics.Occurrence("Daily", ms(2026, 10, 20, 9), ms(2026, 10, 20, 9, 30), False)]


def test_a_weekly_rule_with_days_and_an_interval():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261005T100000\nDTEND;TZID=Asia/Taipei:20261005T110000\nRRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TU\nSUMMARY:Biweekly")
    # Mon 5th and Tue 6th, then Mon 19th and Tue 20th
    assert titles(on(text)) == ["Biweekly"]
    assert titles(on(text, date(2026, 10, 13))) == []
    assert titles(on(text, date(2026, 10, 6))) == ["Biweekly"]


def test_a_monthly_rule_on_the_third_tuesday():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20260101T150000\nRRULE:FREQ=MONTHLY;BYDAY=3TU\nSUMMARY:Review")
    assert titles(on(text)) == ["Review"]  # the third Tuesday of October 2026 is the 20th
    assert titles(on(text, date(2026, 10, 13))) == []


def test_until_in_utc_ends_a_timed_series():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261001T090000\nRRULE:FREQ=DAILY;UNTIL=20261019T010000Z\nSUMMARY:Sprint")
    assert titles(on(text, date(2026, 10, 19))) == ["Sprint"]  # 09:00 on the 19th is 01:00Z: the last one
    assert on(text) == []


def test_until_as_a_date_ends_a_timed_series_at_the_end_of_that_day():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261001T090000\nRRULE:FREQ=DAILY;UNTIL=20261020\nSUMMARY:Sprint")
    assert titles(on(text)) == ["Sprint"]
    assert on(text, date(2026, 10, 21)) == []


def test_until_without_a_zone_follows_the_zone_of_the_start():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261001T090000\nRRULE:FREQ=DAILY;UNTIL=20261020T090000\nSUMMARY:Sprint")
    assert titles(on(text)) == ["Sprint"]
    assert on(text, date(2026, 10, 21)) == []


def test_count_ends_a_series():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261018T090000\nRRULE:FREQ=DAILY;COUNT=3\nSUMMARY:Three")
    assert [titles(on(text, date(2026, 10, d))) for d in (17, 18, 19, 20, 21)] == [[], ["Three"], ["Three"], ["Three"], []]


def test_an_all_day_yearly_rule_is_a_birthday():
    text = calendar("UID:a\nDTSTART;VALUE=DATE:19901020\nDTEND;VALUE=DATE:19901021\nRRULE:FREQ=YEARLY\nSUMMARY:Birthday")
    found = on(text)
    assert found == [ics.Occurrence("Birthday", ms(2026, 10, 20), ms(2026, 10, 21), True)]
    assert on(text, date(2026, 10, 21)) == []


def test_an_all_day_series_with_until_as_a_date_or_in_utc():
    for until in ("20261020", "20261020T000000Z", "20261020T150000Z"):
        text = calendar(f"UID:a\nDTSTART;VALUE=DATE:20261015\nRRULE:FREQ=DAILY;UNTIL={until}\nSUMMARY:Week")
        assert titles(on(text)) == ["Week"], until
        assert on(text, date(2026, 10, 21)) == [], until


def test_an_all_day_series_can_be_several_days_long():
    text = calendar("UID:a\nDTSTART;VALUE=DATE:20261012\nDTEND;VALUE=DATE:20261014\nRRULE:FREQ=WEEKLY\nSUMMARY:Camp")
    assert titles(on(text, date(2026, 10, 19))) == ["Camp"]
    assert titles(on(text, date(2026, 10, 20))) == ["Camp"]
    assert on(text, date(2026, 10, 21)) == []


def test_exdate_removes_an_occurrence_whatever_the_way_it_is_written():
    series = "UID:a\nDTSTART;TZID=Asia/Taipei:20261001T090000\nDTEND;TZID=Asia/Taipei:20261001T100000\nRRULE:FREQ=DAILY\nSUMMARY:Daily\n"
    # a list on one line, a line of its own, a time in UTC, and a plain date
    for exdate in (
        "EXDATE;TZID=Asia/Taipei:20261019T090000,20261020T090000",
        "EXDATE;TZID=Asia/Taipei:20261019T090000\nEXDATE;TZID=Asia/Taipei:20261020T090000",
        "EXDATE:20261020T010000Z",
        "EXDATE;VALUE=DATE:20261020",
    ):
        text = calendar(series + exdate)
        assert on(text) == [], exdate
        assert titles(on(text, date(2026, 10, 21))) == ["Daily"], exdate


def test_an_exdate_of_a_day_before_does_not_hide_today():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261001T090000\nRRULE:FREQ=DAILY\nEXDATE;TZID=Asia/Taipei:20261019T090000\nSUMMARY:Daily")
    assert titles(on(text)) == ["Daily"]


def test_exdate_on_an_all_day_series():
    text = calendar("UID:a\nDTSTART;VALUE=DATE:20261001\nRRULE:FREQ=DAILY\nEXDATE;VALUE=DATE:20261020\nSUMMARY:Daily")
    assert on(text) == []
    assert titles(on(text, date(2026, 10, 21))) == ["Daily"]


def test_rdate_adds_an_occurrence():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261001T090000\nDTEND;TZID=Asia/Taipei:20261001T100000\nRDATE;TZID=Asia/Taipei:20261020T140000\nSUMMARY:Extra")
    found = on(text)
    assert [(o.title, o.start_ms) for o in found] == [("Extra", ms(2026, 10, 20, 14))]


def test_an_unreadable_rule_still_shows_the_first_occurrence_on_its_day():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20261020T090000\nRRULE:FREQ=FORTNIGHTLY\nSUMMARY:Odd")
    assert titles(on(text)) == ["Odd"]
    assert on(text, date(2026, 10, 21)) == []


def test_a_rule_that_never_ends_stops_at_the_limit():
    text = calendar("UID:a\nDTSTART;TZID=Asia/Taipei:20000101T090000\nRRULE:FREQ=SECONDLY\nSUMMARY:Flood")
    assert on(text) == []  # stepped through MAX_STEPS seconds from the year 2000 and stopped


def test_a_recurring_event_keeps_its_time_of_day_over_a_clock_change():
    # New York went back an hour on 2026-11-01: 09:00 is 13:00Z before it and 14:00Z after
    text = calendar("UID:a\nDTSTART;TZID=America/New_York:20261019T090000\nDTEND;TZID=America/New_York:20261019T100000\nRRULE:FREQ=DAILY\nSUMMARY:Standup")
    before = events_on(text, date(2026, 10, 30), NYC)
    after = events_on(text, date(2026, 11, 2), NYC)
    assert before[0].start_ms == ms(2026, 10, 30, 13, tz=UTC)
    assert after[0].start_ms == ms(2026, 11, 2, 14, tz=UTC)


# --- overrides --------------------------------------------------------------------------------


SERIES = "UID:s\nDTSTART;TZID=Asia/Taipei:20261001T090000\nDTEND;TZID=Asia/Taipei:20261001T100000\nRRULE:FREQ=DAILY\nSUMMARY:Series\n"


def test_an_override_replaces_its_occurrence_with_its_own_time_and_title():
    override = "UID:s\nRECURRENCE-ID;TZID=Asia/Taipei:20261020T090000\nDTSTART;TZID=Asia/Taipei:20261020T150000\nDTEND;TZID=Asia/Taipei:20261020T160000\nSUMMARY:Moved"
    found = on(calendar(SERIES, override))
    assert [(o.title, o.start_ms) for o in found] == [("Moved", ms(2026, 10, 20, 15))]
    assert titles(on(calendar(SERIES, override), date(2026, 10, 21))) == ["Series"]


def test_an_override_that_moves_to_another_day_empties_the_original_day_and_fills_the_new_one():
    weekly = "UID:w\nDTSTART;TZID=Asia/Taipei:20261006T090000\nDTEND;TZID=Asia/Taipei:20261006T100000\nRRULE:FREQ=WEEKLY\nSUMMARY:Weekly"
    override = "UID:w\nRECURRENCE-ID;TZID=Asia/Taipei:20261020T090000\nDTSTART;TZID=Asia/Taipei:20261022T090000\nDTEND;TZID=Asia/Taipei:20261022T100000\nSUMMARY:Later"
    text = calendar(weekly, override)
    assert on(text) == []
    assert titles(on(text, date(2026, 10, 22))) == ["Later"]
    assert titles(on(text, date(2026, 10, 27))) == ["Weekly"]


def test_an_override_that_moves_onto_today_from_another_day():
    override = "UID:s\nRECURRENCE-ID;TZID=Asia/Taipei:20261019T090000\nDTSTART;TZID=Asia/Taipei:20261020T180000\nDTEND;TZID=Asia/Taipei:20261020T190000\nSUMMARY:Moved here"
    found = on(calendar(SERIES, override))
    assert titles(found) == ["Series", "Moved here"]
    assert on(calendar(SERIES, override), date(2026, 10, 19)) == []


def test_a_cancelled_override_removes_the_occurrence():
    override = "UID:s\nRECURRENCE-ID;TZID=Asia/Taipei:20261020T090000\nDTSTART;TZID=Asia/Taipei:20261020T090000\nSTATUS:CANCELLED\nSUMMARY:Series"
    text = calendar(SERIES, override)
    assert on(text) == []
    assert titles(on(text, date(2026, 10, 21))) == ["Series"]


def test_an_override_is_matched_by_instant_not_by_the_way_the_zone_is_written():
    override = "UID:s\nRECURRENCE-ID:20261020T010000Z\nDTSTART:20261020T070000Z\nDTEND:20261020T080000Z\nSUMMARY:Moved"
    assert [(o.title, o.start_ms) for o in on(calendar(SERIES, override))] == [("Moved", ms(2026, 10, 20, 15))]


def test_an_override_of_an_all_day_series():
    series = "UID:s\nDTSTART;VALUE=DATE:20261001\nRRULE:FREQ=DAILY\nSUMMARY:Series\n"
    override = "UID:s\nRECURRENCE-ID;VALUE=DATE:20261020\nDTSTART;VALUE=DATE:20261020\nSUMMARY:Special"
    assert titles(on(calendar(series, override))) == ["Special"]


def test_an_override_with_no_series_in_the_file_is_an_event_of_its_own():
    override = "UID:s\nRECURRENCE-ID;TZID=Asia/Taipei:20261020T090000\nDTSTART;TZID=Asia/Taipei:20261020T150000\nSUMMARY:Alone"
    assert titles(on(calendar(override))) == ["Alone"]


def test_overrides_of_one_series_do_not_touch_another_with_a_different_uid():
    other = "UID:other\nDTSTART;TZID=Asia/Taipei:20261001T090000\nRRULE:FREQ=DAILY\nSUMMARY:Other"
    override = "UID:s\nRECURRENCE-ID;TZID=Asia/Taipei:20261020T090000\nDTSTART;TZID=Asia/Taipei:20261020T150000\nSUMMARY:Moved"
    assert sorted(titles(on(calendar(SERIES, other, override)))) == ["Moved", "Other"]


# --- bad input --------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "<html><body>Sign in</body></html>", "garbage\nnot ical", "BEGIN:VEVENT\r\nEND:VEVENT\r\n"])
def test_text_that_is_not_a_calendar_is_refused(text):
    with pytest.raises(CalendarFormatError):
        on(text)


def test_bytes_are_read_too():
    assert titles(on(calendar("UID:a\nDTSTART:20261020T010000Z\nSUMMARY:Bytes").encode())) == ["Bytes"]


def test_a_calendar_with_no_events_is_fine():
    assert on(calendar()) == []


def test_an_event_that_cannot_be_read_is_skipped_and_the_others_stay():
    broken = "UID:a\nDTSTART:not-a-time\nSUMMARY:Broken"
    assert titles(on(calendar(broken, "UID:b\nDTSTART:20261020T010000Z\nSUMMARY:Fine"))) == ["Fine"]


def test_an_event_without_a_start_is_skipped():
    assert on(calendar("UID:a\nSUMMARY:No start")) == []


def test_reading_stops_when_the_time_is_up():
    events = [f"UID:{n}\nDTSTART:20261020T01{n % 60:02d}00Z\nSUMMARY:E{n}" for n in range(10)]
    ticks = iter(range(100))
    # the deadline is 3 s after the first reading of the clock; each reading is a second later
    found = events_on(calendar(*events), TODAY, TPE, budget_s=3, clock=lambda: next(ticks))
    assert 0 < len(found) < 10


def test_no_more_than_the_limit_of_events_is_returned():
    # 00:00Z to 15:00Z is 08:00 to 23:00 in Taipei: all of them on the day
    events = [f"UID:{n}\nDTSTART:20261020T{n % 16:02d}0000Z\nSUMMARY:E{n}" for n in range(ics.MAX_PER_DAY + 50)]
    assert len(on(calendar(*events))) == ics.MAX_PER_DAY


def test_nothing_the_calendar_says_reaches_the_log(caplog):
    caplog.set_level("DEBUG")
    secret = "Secret board meeting"
    on(calendar("UID:a\nDTSTART:20261020T010000Z\nSUMMARY:" + secret, "UID:b\nDTSTART:bad\nSUMMARY:" + secret))
    assert secret not in caplog.text


# --- this computer's zone ---------------------------------------------------------------------


def test_the_system_zone_gives_the_offset_this_computer_has_on_that_date():
    zone = SystemZone()
    for moment in (datetime(2026, 1, 15, 12), datetime(2026, 7, 15, 12)):
        assert zone.utcoffset(moment) == moment.astimezone().utcoffset()
    assert zone.utcoffset(None) is None


def test_the_system_zone_works_for_a_date_windows_cannot_place():
    assert isinstance(SystemZone().utcoffset(datetime(1960, 1, 1, 12)), timedelta)


def test_by_default_the_day_is_a_day_of_this_computers_zone():
    found = events_on(calendar("UID:a\nDTSTART;VALUE=DATE:20261020\nSUMMARY:Day"), TODAY)
    midnight = datetime(2026, 10, 20).astimezone()
    assert found[0].start_ms == int(midnight.timestamp() * 1000)
