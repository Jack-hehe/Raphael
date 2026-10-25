"""A bounded, read-only home projection of existing services and conversation records."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, tzinfo
from typing import Any, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .clock import wall_clock_ms
from .members.schedule import next_run
from .protocol import RpcError, invalid_params

TODAY_LIMIT = 12
REPORT_LIMIT = 8
CALENDAR_LIMIT = 24
logger = logging.getLogger(__name__)


class HomeItem(TypedDict):
    id: str
    kind: str
    title: str
    summary: str
    at_ms: int
    member_id: int
    name: str
    avatar_url: str | None
    turn_id: int | None
    conversation_id: int | None
    routine_id: int | None


class DictationNumbers(TypedDict):
    words: int
    time_saved_seconds: float
    speed_wpm: float | None
    audio_seconds: float


class HomeUsage(TypedDict):
    month_usd: float
    limit_usd: float
    daily: list[float]
    allowances: dict[str, Any]


class Overview(TypedDict):
    generated_at_ms: int
    report: dict[str, Any]
    today: list[HomeItem]
    reports: list[HomeItem]
    calendar: list[HomeItem]
    usage: HomeUsage
    dictation: DictationNumbers
    counts: dict[str, int]


async def no_calendar_events(start_ms: int, end_ms: int) -> list[HomeItem]:
    """Connector seam: replace with the private connected-calendar reader when available."""
    return []


class HomeOverview:
    def __init__(self, conn, router, members, *, now_ms=wall_clock_ms, tz: tzinfo | None = None,
                 calendar_events: Callable[[int, int], Awaitable[list[HomeItem]]] = no_calendar_events):
        self.conn, self.router, self.members = conn, router, members
        self.now_ms, self.tz, self.calendar_events = now_ms, tz, calendar_events
        self._allowance_task = None

    def register(self):
        self.router.register("home.overview", self.read)

    async def _allowances(self):
        # Concurrent refreshes share one CLI read, without persisting a second allowance store.
        if self._allowance_task is None or self._allowance_task.done():
            self._allowance_task = asyncio.create_task(self.router.call("usage.allowances"))
        try:
            return await asyncio.wait_for(asyncio.shield(self._allowance_task), timeout=12)
        except (RpcError, TimeoutError):
            return {"brains": []}

    async def read(self, params: dict) -> Overview:
        zone = self.tz
        if "tz" in params:
            try:
                if not isinstance(params["tz"], str) or len(params["tz"]) > 100:
                    raise ValueError
                zone = ZoneInfo(params["tz"])
            except (ValueError, ZoneInfoNotFoundError):
                raise invalid_params("tz must be an IANA time zone") from None
        now = self.now_ms()
        day = datetime.fromtimestamp(now / 1000, zone).replace(hour=0, minute=0, second=0, microsecond=0)
        start, tomorrow, end = [int((day + timedelta(days=n)).timestamp() * 1000) for n in (0, 1, 7)]
        roster = (await self.router.call("members.list", {k: params[k] for k in ("ui_language",) if k in params}))["members"]
        people = {m["id"]: m for m in roster}

        def item(kind, key, mid, title, at, *, summary="", turn=None, conversation=None, routine=None) -> HomeItem:
            member = people[mid]
            return {"id": f"{kind}:{key}", "kind": kind, "title": " ".join(title.split())[:120],
                    "summary": " ".join(summary.split())[:160], "at_ms": at, "member_id": mid,
                    "name": member["name"], "avatar_url": member.get("avatar_url"),
                    "turn_id": turn, "conversation_id": conversation, "routine_id": routine}

        reports = []
        # Rank completed reports, rather than the latest turn, which can still be a question.
        rows = self.conn.execute(
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY member_id ORDER BY created_at_ms DESC, id DESC) AS rank"
            " FROM agent_turns WHERE status = 'done' AND (text != '' OR detail IS NOT NULL)) WHERE rank = 1"
            " ORDER BY created_at_ms DESC, id DESC")
        for row in rows:
            mid = row["member_id"]
            if mid not in people or not people[mid]["unread"]:
                continue
            reports.append(item("report", row["id"], mid, row["question"], row["created_at_ms"],
                                summary=row["text"] or row["detail"] or "", turn=row["id"], conversation=row["conversation_id"]))
        today = []
        for row in self.conn.execute("SELECT * FROM agent_turns WHERE status='confirm' ORDER BY created_at_ms, id"):
            if row["member_id"] in people:
                today.append(item("question", row["id"], row["member_id"], row["text"] or row["question"], row["created_at_ms"],
                                  turn=row["id"], conversation=row["conversation_id"]))
        for row in self.conn.execute(
            "SELECT q.*, t.turn_id, a.conversation_id FROM worker_questions q JOIN worker_tasks t ON t.id=q.task_id"
            " LEFT JOIN agent_turns a ON a.id=t.turn_id WHERE q.state='waiting' AND t.state='waiting' ORDER BY q.created_at_ms, q.id"):
            if row["member_id"] in people:
                today.append(item("question", f"worker:{row['id']}", row["member_id"], row["text"], row["created_at_ms"],
                                  turn=row["turn_id"], conversation=row["conversation_id"]))
        calendar = []
        for routine in self.members.store.routines():
            if not routine.enabled or routine.next_run_ms is None or routine.member_id not in people:
                continue
            due = routine.next_run_ms
            completed = self.conn.execute("SELECT COUNT(*) FROM routine_runs WHERE routine_id=?", (routine.id,)).fetchone()[0]
            remaining = CALENDAR_LIMIT if routine.schedule.max_runs is None else max(0, routine.schedule.max_runs - completed)
            # At most one page of occurrences per routine, even for hourly schedules.
            for _ in range(min(CALENDAR_LIMIT, remaining)):
                if due is None or due >= end:
                    break
                if due >= start:
                    occurrence = item("routine", f"{routine.id}:{due}", routine.member_id, routine.title, due, routine=routine.id)
                    calendar.append(occurrence)
                    if due < tomorrow:
                        today.append(occurrence)
                due = next_run(routine.schedule, due, tz=zone)
        try:
            events = await self.calendar_events(start, end)
        except (ConnectionError, TimeoutError):
            logger.warning("Home calendar is temporarily unavailable")
            events = []
        calendar.extend(event for event in events if start <= event["at_ms"] < end)
        today.sort(key=lambda r: (0 if r["kind"] == "question" else 1, r["at_ms"], r["id"]))
        today.extend(reports)
        calendar.sort(key=lambda r: (r["at_ms"], r["id"]))
        status = await self.router.call("status.get")
        stats = await self.router.call("stats.summary", {"days": 14})
        allowances = await self._allowances()
        spend = status["spend"]
        return {"generated_at_ms": now, "report": status, "today": today[:TODAY_LIMIT], "reports": reports[:REPORT_LIMIT],
                "calendar": calendar[:CALENDAR_LIMIT], "counts": {"today": len(today), "reports": len(reports), "calendar": len(calendar)},
                "usage": {"month_usd": spend["month_usd"], "limit_usd": spend["limit_usd"], "daily": spend["daily"][-14:], "allowances": allowances},
                "dictation": {"words": stats["all"]["words"], "audio_seconds": stats["all"]["audio_seconds"],
                              "time_saved_seconds": stats["time_saved_seconds"], "speed_wpm": stats.get("speed_wpm")}}
