"""Lasting dictation statistics: `stats.summary`, independent of retained text.

A word is counted like this, in mixed text too: each Chinese, Japanese or Korean character
(Han, kana, Hangul) is one word, and each run of letters or digits of any other script is one
word. Punctuation, symbols and emoji are no words. A contraction ("don't") and a number with a
decimal point or a thousands comma ("3.14", "1,000") each count as one; a hyphen or an underscore
splits.

The days, weeks and months are local, like the spend tracker's: each starts at midnight in the time
zone (the week starts on Monday, the ISO week). A dictation's local day is fixed when recorded.
Daily aggregates retain counts and durations without text; history deletion never changes them.
Costs still come from the spend table, which history retention does not prune.
"""

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

# The same day and month boundaries as the spend tracker's.
from .spend import _day_start_ms, _month_range

# Typical keyboard typing speed, in words a minute. For Chinese and Japanese a person types roughly
# 40 characters a minute with an input method editor, and a character is one word here, so the
# same number serves for them.
TYPING_WPM = 40
# `by_day` lists this many days, today last, unless asked for another number of them (at least
# one, at most MAX_BY_DAY_DAYS: a year of weeks for the home page's activity grid, and a day).
BY_DAY_DAYS = 14
MAX_BY_DAY_DAYS = 371

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_DAY = timedelta(days=1)

# Characters that are one word each, as ranges of code points (first, last): Han (with the
# iteration mark and the ideographic zero), the kana (the prolonged sound mark among them, half-width
# ones too) and Hangul.
_CJK_RANGES = (
    (0x3005, 0x3007),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x323AF),  # Han
    (0x3041, 0x3096),
    (0x309D, 0x309F),  # Hiragana
    (0x30A1, 0x30FA),
    (0x30FC, 0x30FF),
    (0x31F0, 0x31FF),
    (0xFF66, 0xFF9D),  # Katakana
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7FF),  # Hangul
)
# The half-width voiced marks: no word by themselves, and no part of a run of letters either.
_CJK_MARKS = ((0xFF9E, 0xFF9F),)
_APOSTROPHES = (0x27, 0x2019)


def _char_class(ranges: tuple[tuple[int, int], ...]) -> str:
    return "".join(f"{re.escape(chr(first))}-{re.escape(chr(last))}" for first, last in ranges)


_CJK = _char_class(_CJK_RANGES)
_NOT_ALNUM = _CJK + _char_class(_CJK_MARKS)
_APOSTROPHE = "[" + "".join(re.escape(chr(code)) for code in _APOSTROPHES) + "]"
# A letter or digit of any script but those, full-width ones included.
_ALNUM = rf"(?:(?![{_NOT_ALNUM}])[^\W_])"
_WORD = re.compile(
    rf"[{_CJK}]"
    # A run, which an apostrophe between letters or a . or , between digits does not break.
    rf"|{_ALNUM}+(?:(?:{_APOSTROPHE}|(?<=\d)[.,](?=\d)){_ALNUM}+)*"
)


def count_words(text: str) -> int:
    """How many words `text` has (see the module docstring)."""
    return sum(1 for _ in _WORD.finditer(text))


def _zone_and_today(now_ms: int, tz: tzinfo | None) -> tuple[tzinfo, date]:
    """The time zone in force, and the date it is now in that zone (as the spend tracker's)."""
    zone = tz
    if zone is None:
        zone = datetime.now(UTC).astimezone().tzinfo
    return zone, (_EPOCH + timedelta(milliseconds=now_ms)).astimezone(zone).date()


def local_day(created_at_ms: int, tz: tzinfo | None = None) -> date:
    """The owner's local calendar day at capture (or at migration for legacy rows)."""
    return _zone_and_today(created_at_ms, tz)[1]


def record_dictation(
    conn: sqlite3.Connection, *, created_at_ms: int, text: str, audio_seconds: float,
    stt_ms: int, llm_ms: int, tz: tzinfo | None = None,
) -> None:
    """Increment lasting counts inside the history writer's transaction; never commit here."""
    conn.execute(
        "INSERT INTO dictation_daily (day, dictations, words, audio_seconds, processing_ms)"
        " VALUES (?, 1, ?, ?, ?) ON CONFLICT(day) DO UPDATE SET"
        " dictations = dictations + 1, words = words + excluded.words,"
        " audio_seconds = audio_seconds + excluded.audio_seconds,"
        " processing_ms = processing_ms + excluded.processing_ms",
        (local_day(created_at_ms, tz).isoformat(), count_words(text), audio_seconds, stt_ms + llm_ms),
    )


@dataclass
class _Totals:
    dictations: int = 0
    words: int = 0
    audio_seconds: float = 0.0

    def add(self, dictations: int, words: int, audio_seconds: float) -> None:
        self.dictations += dictations
        self.words += words
        self.audio_seconds += audio_seconds


class StatsStore:
    """The durable daily aggregates, as `stats.summary`.

    `now_ms` is the clock (milliseconds since the epoch), read on every call. `tz` is the time zone
    of the days; with the default, None, it is this machine's local zone, found on every call.
    Reads only; use it on the thread that owns `conn`.
    """

    def __init__(
        self, conn: sqlite3.Connection, now_ms: Callable[[], int], tz: tzinfo | None = None
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms
        self._tz = tz

    def summary(self, days: int = BY_DAY_DAYS) -> dict:
        """The lasting statistics, including marked estimates recovered during migration.

        History retention and deletion have no effect. Cost comes from the spend table.

        - today, week, month, all: dictations, words, audio_seconds and cost_usd of each period
        - streak_days: the days in a row, up to today or else up to yesterday, with a dictation
        - by_day: the last `days` days (BY_DAY_DAYS unless asked, kept between 1 and
          MAX_BY_DAY_DAYS), oldest first, with the dictations and words of each
        - active_days: how many days have at least one dictation
        - longest_streak_days: the most days in a row with a dictation, ever
        - speed_wpm: all words over all speaking time in minutes, or None without any audio
        - time_saved_seconds: what typing all the words would have taken at TYPING_WPM, less the
          speaking and the processing time (speech-to-text and cleanup); 0 at the least
        - estimated_words, estimated_dictations: the recovered estimates already included above
        """
        if isinstance(days, bool) or not isinstance(days, int):
            raise ValueError("days must be a whole number")
        days = max(1, min(MAX_BY_DAY_DAYS, days))
        zone, today = _zone_and_today(self._now_ms(), self._tz)
        week_first = today - timedelta(days=today.weekday())
        month_first, month_next = _month_range(today)
        # Each period runs from its first moment up to, not including, the first of the next.
        ranges = {
            "today": (today, today + _ONE_DAY),
            "week": (week_first, week_first + 7 * _ONE_DAY),
            "month": (month_first, month_next),
        }
        bounds = {
            name: (_day_start_ms(first, zone), _day_start_ms(after, zone))
            for name, (first, after) in ranges.items()
        }
        totals = {name: _Totals() for name in (*bounds, "all")}
        chart_days = [today - (days - 1 - i) * _ONE_DAY for i in range(days)]
        by_day = {day: [0, 0] for day in chart_days}  # day -> [dictations, words]
        days_with_dictations: set[date] = set()
        processing_ms = 0
        estimated_words = estimated_dictations = 0

        rows = self._conn.execute(
            "SELECT day, dictations, words, audio_seconds, processing_ms, estimated_words,"
            " estimated_dictations FROM dictation_daily"
        )
        for day_text, dictations, words, audio_seconds, elapsed, est_words, est_count in rows:
            day = date.fromisoformat(day_text)
            totals["all"].add(dictations, words, audio_seconds)
            processing_ms += elapsed
            estimated_words += est_words
            estimated_dictations += est_count
            for name, (start, end) in ranges.items():
                if start <= day < end:
                    totals[name].add(dictations, words, audio_seconds)
            days_with_dictations.add(day)
            if day in by_day:
                by_day[day][0] += dictations
                by_day[day][1] += words

        everything = totals["all"]
        speed_wpm = (
            everything.words / (everything.audio_seconds / 60)
            if everything.audio_seconds > 0
            else None
        )
        typing_seconds = everything.words / TYPING_WPM * 60
        time_saved = typing_seconds - everything.audio_seconds - processing_ms / 1000
        summary: dict = {}
        for name, period in totals.items():
            summary[name] = {
                "dictations": period.dictations,
                "words": period.words,
                "audio_seconds": float(period.audio_seconds),
                "cost_usd": self._spent_between(*bounds.get(name, (None, None))),
            }
        summary["streak_days"] = _streak(days_with_dictations, today)
        summary["by_day"] = [
            {"date": day.isoformat(), "dictations": by_day[day][0], "words": by_day[day][1]}
            for day in chart_days
        ]
        summary["active_days"] = len(days_with_dictations)
        summary["longest_streak_days"] = _longest_streak(days_with_dictations)
        summary["speed_wpm"] = speed_wpm
        summary["time_saved_seconds"] = max(0.0, time_saved)
        summary["estimated_words"] = estimated_words
        summary["estimated_dictations"] = estimated_dictations
        return summary

    def _spent_between(self, start_ms: int | None, end_ms: int | None) -> float:
        """What was spent from `start_ms` up to, but not including, `end_ms`; None is no limit."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM spend"
            " WHERE (? IS NULL OR created_at_ms >= ?) AND (? IS NULL OR created_at_ms < ?)",
            (start_ms, start_ms, end_ms, end_ms),
        ).fetchone()
        return float(row[0])


def _streak(days: set[date], today: date) -> int:
    """How many days in a row, ending today or else yesterday, are in `days`."""
    day = today if today in days else today - _ONE_DAY
    count = 0
    while day in days:
        count += 1
        day -= _ONE_DAY
    return count


def _longest_streak(days: set[date]) -> int:
    """The longest run of consecutive days in `days`."""
    longest = 0
    for day in days:
        if day - _ONE_DAY in days:
            continue  # not the first day of a run
        length = 1
        while day + length * _ONE_DAY in days:
            length += 1
        longest = max(longest, length)
    return longest
