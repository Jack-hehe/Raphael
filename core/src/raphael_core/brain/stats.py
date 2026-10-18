"""Dictation statistics: `stats.summary`, what the history says about how much was dictated.

A word is counted the way Typeless counts mixed text: each Chinese, Japanese or Korean character
(Han, kana, Hangul) is one word, and each run of letters or digits of any other script is one
word. Punctuation, symbols and emoji are no words. A contraction ("don't") and a number with a
decimal point or a thousands comma ("3.14", "1,000") each count as one; a hyphen or an underscore
splits.

The days, weeks and months are local, like the spend tracker's: each starts at midnight in the time
zone and ends where the next one starts (the week starts on Monday, the ISO week). The statistics
see the history only as far as it is kept: the retention setting prunes older dictations, so they
are not counted anywhere here, and "all" means all that is left. What the dictations cost comes
from the spend table instead, which the retention does not prune.
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
# `by_day` lists this many days, today last.
BY_DAY_DAYS = 14

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


@dataclass
class _Totals:
    dictations: int = 0
    words: int = 0
    audio_seconds: float = 0.0

    def add(self, words: int, audio_seconds: float) -> None:
        self.dictations += 1
        self.words += words
        self.audio_seconds += audio_seconds


class StatsStore:
    """What the dictation history says, as `stats.summary`.

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

    def summary(self) -> dict:
        """The statistics of the dictation history.

        Only the dictations the history still keeps are counted: the history retention setting
        prunes the older ones, and the statistics shrink with it. The cost comes from the spend
        table, which is not pruned. See the module docstring for how words and days are counted.

        - today, week, month, all: dictations, words, audio_seconds and cost_usd of each period
        - streak_days: the days in a row, up to today or else up to yesterday, with a dictation
        - by_day: the last BY_DAY_DAYS days, oldest first, with the dictations and words of each
        - speed_wpm: all words over all speaking time in minutes, or None without any audio
        - time_saved_seconds: what typing all the words would have taken at TYPING_WPM, less the
          speaking and the processing time (speech-to-text and cleanup); 0 at the least
        """
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
        chart_days = [today - (BY_DAY_DAYS - 1 - i) * _ONE_DAY for i in range(BY_DAY_DAYS)]
        by_day = {day: [0, 0] for day in chart_days}  # day -> [dictations, words]
        days_with_dictations: set[date] = set()
        processing_ms = 0

        rows = self._conn.execute(
            "SELECT created_at_ms, text, audio_seconds, stt_ms, llm_ms FROM history"
            " WHERE kind = 'dictation'"
        )
        for created_at_ms, text, audio_seconds, stt_ms, llm_ms in rows:
            words = count_words(text)
            totals["all"].add(words, audio_seconds)
            processing_ms += stt_ms + llm_ms
            for name, (start, end) in bounds.items():
                if start <= created_at_ms < end:
                    totals[name].add(words, audio_seconds)
            day = (_EPOCH + timedelta(milliseconds=created_at_ms)).astimezone(zone).date()
            days_with_dictations.add(day)
            if day in by_day:
                by_day[day][0] += 1
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
        summary["speed_wpm"] = speed_wpm
        summary["time_saved_seconds"] = max(0.0, time_saved)
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
