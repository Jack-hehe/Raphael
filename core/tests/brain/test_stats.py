"""Tests for `stats.summary` and the word count behind it.

The clock and the time zone are injected, the way test_spend.py does, so no test depends on the
machine's zone or on today. Zone for the dates: Taipei (UTC+8). The stores are real ones on an
in-memory database.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from raphael_core.brain.history import HistoryStore
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.spend import SpendTracker
from raphael_core.brain.stats import TYPING_WPM, StatsStore, count_words
from raphael_core.router import Router

from .conftest import item

TPE = timezone(timedelta(hours=8))
DAY = timedelta(days=1)


def at(y, m, d, h=12, minute=0, second=0):
    """The time in Taipei, in ms since the epoch."""
    return int(datetime(y, m, d, h, minute, second, tzinfo=TPE).timestamp() * 1000)


def say(conn, when_ms, text, *, audio=2.0, stt_ms=0, llm_ms=0, kind="dictation"):
    """A dictation of `text` in the history."""
    HistoryStore(conn).add(
        item(
            created_at_ms=when_ms,
            text=text,
            raw_text=text,
            audio_seconds=audio,
            stt_ms=stt_ms,
            llm_ms=llm_ms,
            kind=kind,
        )
    )


def pay(conn, when_ms, cost, purpose="cleanup"):
    SpendTracker(conn, now_ms=lambda: when_ms, tz=TPE).record(
        purpose=purpose, model="m", cost_usd=cost, prompt_tokens=1, completion_tokens=1
    )


def store_at(conn, now_ms, tz=TPE):
    return StatsStore(conn, now_ms=lambda: now_ms, tz=tz)


def period(dictations=0, words=0, audio_seconds=0.0, cost_usd=0.0):
    return {
        "dictations": dictations,
        "words": words,
        "audio_seconds": audio_seconds,
        "cost_usd": cost_usd,
    }


def approx_period(p):
    return {k: pytest.approx(v) for k, v in p.items()}


# --- counting words -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("   \n\t ", 0),
        # Chinese: one word for each character.
        ("你好", 2),
        ("今天天氣很好", 6),
        ("你好\n\n世界\t好", 5),
        # English: one word for each run of letters.
        ("Hello world", 2),
        ("  Hello   world  ", 2),
        ("café au lait", 3),
        # Mixed.
        ("我用 iPhone 15 Pro 打電話", 8),
        ("iPhone手機", 3),
        ("用React寫網頁", 5),
        # Japanese kana and kanji.
        ("こんにちは", 5),
        ("コーヒーを飲む", 7),
        ("ｺﾋﾟｰ", 3),
        ("コ・ヒ", 2),
        # Korean.
        ("안녕하세요", 5),
        ("ㅋㅋㅋ", 3),
        # Numbers.
        ("2026年10月18日", 6),
        ("42", 1),
        ("3.14", 1),
        ("1,000 dollars", 2),
        ("三百五十", 4),
        # Punctuation and symbols are not words.
        ("你好，世界！Hello, world... ??? ——（）「」『』《》、。", 6),
        ("!?.,;:-—…·~`@#$%^&*()[]{}<>/\\|", 0),
        ("好👍", 1),
        ("👍👍👍", 0),
        # A contraction is one word; a hyphen or an underscore splits.
        ("don't stop", 2),
        ("it’s", 1),
        ("'quoted'", 1),
        ("well-known", 2),
        ("snake_case", 2),
        ("end.Next", 2),
        # Full-width letters and digits, other scripts.
        ("ＡＢＣ１２３", 1),
        ("привет мир", 2),
        (chr(0x20000), 1),  # a Han character outside the basic block
        (chr(0x3005) + chr(0x3007), 2),  # the iteration mark and the ideographic zero
    ],
)
def test_count_words(text, expected):
    assert count_words(text) == expected


def test_a_long_text_is_counted_without_trouble():
    assert count_words("你好 hello " * 20_000) == 60_000


# --- the periods --------------------------------------------------------------------------------

# Now: Wednesday 14 October 2026, 12:00 in Taipei. The week began on Monday the 12th.
NOW = at(2026, 10, 14)


def test_periods_count_the_local_day_the_iso_week_from_monday_and_the_calendar_month(conn):
    say(conn, at(2026, 9, 30, 23, 59), "你好", audio=1.0)  # last month
    say(conn, at(2026, 10, 11, 12), "Hello world", audio=2.0)  # the Sunday before the week
    say(conn, at(2026, 10, 12, 0, 0), "你好嗎", audio=3.0)  # Monday 00:00: the week begins
    say(conn, at(2026, 10, 13, 12), "one two", audio=4.0)
    say(conn, at(2026, 10, 14, 0, 0), "a b c", audio=5.0)  # today 00:00
    say(conn, at(2026, 10, 14, 11, 59), "你好", audio=6.0)
    pay(conn, at(2026, 9, 30, 23, 59), 1.0)
    pay(conn, at(2026, 10, 11, 12), 0.01)
    pay(conn, at(2026, 10, 12, 0, 0), 0.02)
    pay(conn, at(2026, 10, 13, 12), 0.04)
    pay(conn, at(2026, 10, 14, 0, 0), 0.08, purpose="cleanup")
    pay(conn, at(2026, 10, 14, 11, 59), 0.16, purpose="stt")

    summary = store_at(conn, NOW).summary()

    assert approx_period(summary["today"]) == approx_period(period(2, 5, 11.0, 0.24))
    assert approx_period(summary["week"]) == approx_period(period(4, 10, 18.0, 0.30))
    assert approx_period(summary["month"]) == approx_period(period(5, 12, 20.0, 0.31))
    assert approx_period(summary["all"]) == approx_period(period(6, 14, 21.0, 1.31))


def test_an_empty_history_is_all_zeros(conn):
    summary = store_at(conn, NOW).summary()
    for name in ("today", "week", "month", "all"):
        assert summary[name] == period()
        assert isinstance(summary[name]["audio_seconds"], float)
        assert isinstance(summary[name]["cost_usd"], float)
    assert summary["streak_days"] == 0
    assert summary["speed_wpm"] is None
    assert summary["time_saved_seconds"] == 0.0


def test_the_week_is_the_one_that_began_on_the_latest_monday_even_on_a_sunday(conn):
    sunday = at(2026, 10, 18, 9)
    say(conn, at(2026, 10, 11, 23, 59), "before")  # the Sunday of the week before
    say(conn, at(2026, 10, 12, 0, 0), "monday")
    say(conn, at(2026, 10, 18, 8), "sunday")
    summary = store_at(conn, sunday).summary()
    assert summary["week"]["dictations"] == 2
    assert summary["today"]["dictations"] == 1


def test_a_monday_morning_starts_a_new_week(conn):
    monday = at(2026, 10, 12, 0, 30)
    say(conn, at(2026, 10, 11, 20), "last week")
    say(conn, at(2026, 10, 12, 0, 10), "this week")
    assert store_at(conn, monday).summary()["week"]["dictations"] == 1


def test_the_month_begins_at_local_midnight_not_utc_midnight(conn):
    # 01:00 on 1 November in Taipei is still 31 October in UTC.
    now = at(2026, 11, 1, 1, 0)
    say(conn, at(2026, 10, 31, 23, 59), "old month")
    say(conn, at(2026, 11, 1, 0, 0), "new month")
    pay(conn, at(2026, 10, 31, 23, 59), 5.0)
    pay(conn, at(2026, 11, 1, 0, 30), 0.7)
    month = store_at(conn, now).summary()["month"]
    assert month["dictations"] == 1
    assert month["cost_usd"] == pytest.approx(0.7)


def test_the_month_ends_where_the_next_begins(conn):
    now = at(2026, 12, 31, 12)
    say(conn, at(2026, 12, 1, 0, 0), "first")
    say(conn, at(2026, 12, 31, 23, 59), "last")
    say(conn, at(2027, 1, 1, 0, 0), "after")  # a clock that ran ahead: not this month
    say(conn, at(2026, 11, 30, 23, 59), "before")
    summary = store_at(conn, now).summary()
    assert summary["month"]["dictations"] == 2
    assert summary["all"]["dictations"] == 4


def test_the_cost_of_a_month_agrees_with_the_spend_tracker(conn):
    for when, cost in [(at(2026, 9, 30), 5.0), (at(2026, 10, 1, 1), 1.0), (at(2026, 10, 17), 0.7)]:
        pay(conn, when, cost)
    now = at(2026, 10, 17)
    tracker = SpendTracker(conn, now_ms=lambda: now, tz=TPE)
    summary = store_at(conn, now).summary()
    assert summary["month"]["cost_usd"] == pytest.approx(tracker.month_total())
    assert summary["today"]["cost_usd"] == pytest.approx(tracker.today_total())


def test_the_cost_counts_every_paid_call_whatever_its_purpose(conn):
    pay(conn, NOW, 0.1, purpose="cleanup")
    pay(conn, NOW, 0.2, purpose="stt")
    assert store_at(conn, NOW).summary()["today"]["cost_usd"] == pytest.approx(0.3)


def test_spend_without_a_dictation_still_counts_as_cost(conn):
    # The spend table is not pruned with the history: the cost is what was really spent.
    pay(conn, NOW, 0.5)
    summary = store_at(conn, NOW).summary()
    assert summary["today"] == approx_period(period(0, 0, 0.0, 0.5))


def test_only_dictations_are_counted(conn):
    say(conn, NOW, "你好")
    say(conn, NOW, "其他", kind="something-else")
    assert store_at(conn, NOW).summary()["all"]["dictations"] == 1


def test_a_dictation_with_no_text_counts_as_a_dictation_with_no_words(conn):
    say(conn, NOW, "", audio=1.5)
    today = store_at(conn, NOW).summary()["today"]
    assert (today["dictations"], today["words"], today["audio_seconds"]) == (1, 0, 1.5)


def test_the_words_are_counted_in_the_pasted_text_not_the_raw_text(conn):
    HistoryStore(conn).add(item(created_at_ms=NOW, raw_text="嗯 那個 就是 你好", text="你好"))
    assert store_at(conn, NOW).summary()["all"]["words"] == 2


def test_the_zone_decides_which_day_a_dictation_belongs_to(conn):
    # 20:00 UTC on the 13th is 04:00 on the 14th in Taipei, 16:00 on the 13th in New York (UTC-4).
    moment = int(datetime(2026, 10, 13, 20, tzinfo=UTC).timestamp() * 1000)
    say(conn, moment, "你好")
    new_york = timezone(timedelta(hours=-4))
    now = moment + 3_600_000
    assert store_at(conn, now, tz=TPE).summary()["today"]["dictations"] == 1
    assert store_at(conn, now, tz=new_york).summary()["today"]["dictations"] == 1
    later = at(2026, 10, 14, 23)
    assert store_at(conn, later, tz=TPE).summary()["today"]["dictations"] == 1
    assert store_at(conn, later, tz=new_york).summary()["today"]["dictations"] == 0


def test_without_a_zone_the_machines_zone_is_used(conn):
    say(conn, NOW, "你好")
    summary = StatsStore(conn, now_ms=lambda: NOW).summary()
    assert summary["all"]["dictations"] == 1


# --- the streak ---------------------------------------------------------------------------------


def streak(conn, now=NOW):
    return store_at(conn, now).summary()["streak_days"]


def say_on(conn, *days_ago, hour=12, now_day=(2026, 10, 14)):
    base = datetime(*now_day, hour, tzinfo=TPE)
    for n in days_ago:
        say(conn, int((base - n * DAY).timestamp() * 1000), "你好")


def test_the_streak_counts_the_days_in_a_row_up_to_today(conn):
    say_on(conn, 0, 1, 2)
    assert streak(conn) == 3


def test_today_alone_is_a_streak_of_one(conn):
    say_on(conn, 0)
    assert streak(conn) == 1


def test_the_streak_is_kept_alive_until_the_end_of_today(conn):
    # Nothing yet today: yesterday's streak still stands.
    say_on(conn, 1, 2, 3)
    assert streak(conn) == 3


def test_two_days_without_a_dictation_end_the_streak(conn):
    say_on(conn, 2, 3, 4)
    assert streak(conn) == 0


def test_a_gap_ends_the_streak_at_the_gap(conn):
    say_on(conn, 0, 1, 3, 4, 5)
    assert streak(conn) == 2


def test_several_dictations_in_a_day_are_one_day(conn):
    say_on(conn, 0, 0, 0, 1, 1)
    assert streak(conn) == 2


def test_a_streak_can_be_longer_than_the_chart(conn):
    say_on(conn, *range(40))
    assert streak(conn) == 40


def test_the_streak_follows_local_days(conn):
    # 00:30 on the 14th in Taipei is 16:30 UTC on the 13th; 23:30 on the 13th is the 13th.
    say(conn, at(2026, 10, 14, 0, 30), "你好")
    say(conn, at(2026, 10, 13, 23, 30), "你好")
    say(conn, at(2026, 10, 12, 0, 30), "你好")
    assert streak(conn, at(2026, 10, 14, 8)) == 3


def test_only_dictations_make_a_streak(conn):
    say(conn, NOW, "其他", kind="something-else")
    assert streak(conn) == 0


# --- by day -------------------------------------------------------------------------------------


def test_by_day_lists_the_last_fourteen_days_oldest_first_with_zeros_for_quiet_days(conn):
    say(conn, at(2026, 10, 14, 9), "你好")
    say(conn, at(2026, 10, 14, 10), "Hello world")
    say(conn, at(2026, 10, 12, 8), "一二三")
    by_day = store_at(conn, NOW).summary()["by_day"]
    assert len(by_day) == 14
    assert by_day[0]["date"] == "2026-10-01"
    assert by_day[-1]["date"] == "2026-10-14"
    assert by_day[-1] == {"date": "2026-10-14", "dictations": 2, "words": 4}
    assert by_day[-2] == {"date": "2026-10-13", "dictations": 0, "words": 0}
    assert by_day[-3] == {"date": "2026-10-12", "dictations": 1, "words": 3}
    assert sum(d["dictations"] for d in by_day) == 3
    assert all(set(d) == {"date", "dictations", "words"} for d in by_day)


def test_by_day_crosses_a_month_and_a_year(conn):
    now = at(2027, 1, 3, 12)
    say(conn, at(2026, 12, 31, 23, 59), "你好")
    by_day = store_at(conn, now).summary()["by_day"]
    assert by_day[0]["date"] == "2026-12-21"
    assert by_day[-1]["date"] == "2027-01-03"
    assert by_day[-4] == {"date": "2026-12-31", "dictations": 1, "words": 2}


def test_by_day_includes_the_day_thirteen_days_ago_and_not_the_one_before(conn):
    say(conn, at(2026, 10, 1, 0, 0), "first")  # 13 days before the 14th: the oldest day shown
    say(conn, at(2026, 9, 30, 23, 59), "too old")
    by_day = store_at(conn, NOW).summary()["by_day"]
    assert by_day[0] == {"date": "2026-10-01", "dictations": 1, "words": 1}
    assert sum(d["dictations"] for d in by_day) == 1


def test_by_day_with_nothing_said_is_fourteen_zero_days(conn):
    by_day = store_at(conn, NOW).summary()["by_day"]
    assert len(by_day) == 14
    assert all(d["dictations"] == 0 and d["words"] == 0 for d in by_day)


def test_by_day_can_be_asked_for_more_days(conn):
    say(conn, at(2026, 5, 1, 9), "你好")
    by_day = store_at(conn, NOW).summary(days=182)["by_day"]
    assert len(by_day) == 182
    assert by_day[-1]["date"] == "2026-10-14"
    assert by_day[0]["date"] == "2026-04-16"
    assert {"date": "2026-05-01", "dictations": 1, "words": 2} in by_day


@pytest.mark.parametrize(("asked", "length"), [(0, 1), (-5, 1), (1, 1), (371, 371), (1000, 371)])
def test_the_days_of_by_day_are_kept_between_one_and_371(conn, asked, length):
    assert len(store_at(conn, NOW).summary(days=asked)["by_day"]) == length


@pytest.mark.parametrize("asked", ["182", 1.5, True, None, [14]])
def test_days_that_are_not_a_whole_number_are_refused(conn, asked):
    with pytest.raises(ValueError):
        store_at(conn, NOW).summary(days=asked)


def test_the_other_numbers_do_not_depend_on_the_days_asked_for(conn):
    say_on(conn, 0, 1, 20)
    short = store_at(conn, NOW).summary()
    long = store_at(conn, NOW).summary(days=60)
    for key in ("today", "week", "month", "all", "streak_days", "active_days", "longest_streak_days"):
        assert short[key] == long[key]


# --- active days and the longest streak -------------------------------------------------------


def test_active_days_counts_each_day_with_a_dictation_once(conn):
    say_on(conn, 0, 0, 1, 5, 40)
    say(conn, NOW, "其他", kind="something-else")
    assert store_at(conn, NOW).summary()["active_days"] == 4


def test_the_longest_streak_is_the_longest_run_of_days_ever(conn):
    say_on(conn, 0, 1, 10, 11, 12, 13, 30)
    summary = store_at(conn, NOW).summary()
    assert summary["longest_streak_days"] == 4
    assert summary["streak_days"] == 2


def test_with_nothing_said_there_are_no_active_days_and_no_streak(conn):
    summary = store_at(conn, NOW).summary()
    assert summary["active_days"] == 0
    assert summary["longest_streak_days"] == 0


async def test_stats_summary_takes_the_days_of_by_day(conn):
    router = brain_with(conn, NOW, tz=TPE)
    assert len((await router.call("stats.summary", {"days": 182}))["by_day"]) == 182
    assert len((await router.call("stats.summary", {}))["by_day"]) == 14


# --- speed and time saved -----------------------------------------------------------------------


def test_speed_is_all_time_words_per_speaking_minute(conn):
    say(conn, at(2026, 9, 1), "你好世界", audio=2.0)  # 4 words
    say(conn, NOW, "Hello there friend", audio=1.0)  # 3 words
    # 7 words in 3 seconds of speech.
    assert store_at(conn, NOW).summary()["speed_wpm"] == pytest.approx(140.0)


def test_speed_is_unknown_without_any_audio(conn):
    say(conn, NOW, "你好", audio=0.0)
    assert store_at(conn, NOW).summary()["speed_wpm"] is None


def test_time_saved_is_typing_time_minus_speaking_and_processing_time(conn):
    assert TYPING_WPM == 40
    # 80 words would take 120 s to type at 40 a minute.
    say(conn, NOW, "字" * 50, audio=12.0, stt_ms=3000, llm_ms=1000)
    say(conn, NOW, "word " * 30, audio=8.0, stt_ms=0, llm_ms=1000)
    # 120 - (12 + 8) - (3 + 1 + 0 + 1) = 95
    assert store_at(conn, NOW).summary()["time_saved_seconds"] == pytest.approx(95.0)


def test_time_saved_is_never_negative(conn):
    say(conn, NOW, "你好", audio=30.0, stt_ms=2000, llm_ms=2000)
    assert store_at(conn, NOW).summary()["time_saved_seconds"] == 0.0


def test_time_saved_counts_everything_in_the_history_not_only_this_month(conn):
    say(conn, at(2026, 1, 5), "字" * 40, audio=0.0)  # 60 s of typing
    assert store_at(conn, NOW).summary()["time_saved_seconds"] == pytest.approx(60.0)


# --- through the router -------------------------------------------------------------------------


class NoClient:
    """The stats never call out."""


def brain_with(conn, now_ms, **kwargs):
    router = Router()
    Brain(conn, NoClient(), MemoryKeyStore(), now_ms=lambda: now_ms, **kwargs).register(router)
    return router


async def test_stats_summary_is_a_method_of_the_brain(conn):
    say(conn, NOW, "你好世界", audio=2.0, stt_ms=100, llm_ms=100)
    pay(conn, NOW, 0.25)
    router = brain_with(conn, NOW, tz=TPE)
    assert "stats.summary" in router.methods()
    summary = await router.call("stats.summary", {})
    assert set(summary) == {
        "today",
        "week",
        "month",
        "all",
        "streak_days",
        "by_day",
        "speed_wpm",
        "time_saved_seconds",
        "active_days",
        "longest_streak_days",
    }
    assert summary["today"]["dictations"] == 1
    assert summary["today"]["words"] == 4
    assert summary["today"]["cost_usd"] == pytest.approx(0.25)
    assert summary["streak_days"] == 1
    assert summary["by_day"][-1] == {"date": "2026-10-14", "dictations": 1, "words": 4}
    assert summary["speed_wpm"] == pytest.approx(120.0)


async def test_stats_summary_only_sees_what_the_history_keeps(conn):
    # Retention is 30 days by default: the Brain prunes older rows when it starts.
    say(conn, NOW - 40 * 86_400_000, "老的一句話")
    say(conn, NOW - 1 * 86_400_000, "新的")
    summary = await brain_with(conn, NOW, tz=TPE).call("stats.summary", {})
    assert summary["all"]["dictations"] == 1
    assert summary["all"]["words"] == 2
