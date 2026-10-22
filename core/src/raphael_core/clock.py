"""The wall clock as the database keeps times: whole milliseconds since the Unix epoch, in UTC.

Each store takes the clock as a parameter (`now_ms`), so that a test can give it one of its own;
this is the one they use otherwise.
"""

import time


def wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000
