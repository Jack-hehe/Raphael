"""Summarize dictation metadata in copied app logs, without printing dictated text.

Usage: python -m raphael_core.tools.dictation_timings LOG [LOG ...]
Legacy HTTP completion times are estimates: old logs do not associate requests with sessions.
Missing events stay unknown; they are never inferred as zero latency.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\d)\]\[(\d\d:\d\d:\d\d(?:\.\d+)?)\]")


@dataclass
class Timeline:
    started: datetime | None = None
    stopped: datetime | None = None
    transcribed: datetime | None = None
    cleaned: datetime | None = None
    pasted: datetime | None = None
    done_at_stop: int | None = None
    segments: int | None = None
    rate_limits: int = 0
    dropped: int = 0

    def delay(self, end: str, start: str = "stopped") -> float | None:
        a, b = getattr(self, start), getattr(self, end)
        return (b - a).total_seconds() if a is not None and b is not None else None


def parse(lines: list[str]) -> list[Timeline]:
    sessions: list[Timeline] = []
    current: Timeline | None = None
    for line in lines:
        stamp = STAMP.match(line)
        if stamp is None:
            continue
        at = datetime.fromisoformat(f"{stamp[1]}T{stamp[2]}")
        if "dictation: recording" in line:
            current = Timeline(started=at)
            sessions.append(current)
        elif "dictation: stopping" in line:
            if current is None or current.stopped is not None:
                current = Timeline()
                sessions.append(current)
            current.stopped = at
        if current is None:
            continue
        if "/audio/transcriptions" in line and "429 Too Many Requests" in line:
            current.rate_limits += 1
        if "A stretch of speech could not be transcribed; it is left out" in line:
            current.dropped += 1
        if 'event {"type":"progress"' in line:
            try:
                event = json.loads(line[line.index('event {') + 6 :])
            except (ValueError, json.JSONDecodeError):
                continue
            if event.get("stage") == "transcribing":
                if current.done_at_stop is None:
                    current.done_at_stop = event.get("done")
                current.segments = event.get("total")
            elif event.get("stage") == "cleaning":
                current.transcribed = at
                current.segments = event.get("total")
            elif event.get("stage") == "done":
                current.cleaned = at
        if current.stopped is not None and current.transcribed is None:
            if "/audio/transcriptions" in line and "200 OK" in line:
                current.transcribed = at
        # HTTPX logs response headers, before the cleanup response body has arrived. Only the
        # shell's done event establishes that text is ready for delivery.
        if ("[app_lib::paste::windows] paste:" in line and current.stopped is not None
                and current.pasted is None):
            current.pasted = at
        elif "dictation: cancelled" in line:
            current = None
    return sessions


def percentile(values: list[float], share: float) -> float | None:
    """Nearest rank; reports an observed duration even for a small sample."""
    return sorted(values)[max(0, math.ceil(len(values) * share) - 1)] if values else None


def report(sessions: list[Timeline]) -> str:
    def number(value: float | None) -> str:
        return "?" if value is None else f"{value:.3f}"

    rows = ["stop | transcribed | cleaned | pasted | segments(done@stop/total) | 429 | dropped"]
    for session in sessions:
        stop = session.stopped.isoformat(sep=" ") if session.stopped else "? (no stop)"
        rows.append(
            f"{stop} | {number(session.delay('transcribed'))} | "
            f"{number(session.delay('cleaned'))} | {number(session.delay('pasted'))} | "
            f"{session.done_at_stop}/{session.segments} | {session.rate_limits} | {session.dropped}"
        )
    for stage in ("transcribed", "cleaned", "pasted"):
        values = [value for s in sessions if (value := s.delay(stage)) is not None and value >= 0]
        rows.append(
            f"stop->{stage}: n={len(values)} p50={number(percentile(values, .5))}s "
            f"p90={number(percentile(values, .9))}s"
        )
    rows.append("Legacy HTTP times are estimates; timestamps may have only 1-second precision. ? = unknown.")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", type=Path, nargs="+")
    args = parser.parse_args()
    lines = [line for path in args.logs for line in path.read_text(encoding="utf-8").splitlines()]
    stamped = [line for line in lines if STAMP.match(line)]
    # Keep file order for events sharing a second; sorting their messages would reorder stages.
    stamped.sort(key=lambda line: STAMP.match(line).groups())
    print(report(parse(stamped)))


if __name__ == "__main__":
    main()
