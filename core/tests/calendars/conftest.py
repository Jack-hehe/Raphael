"""Fixtures and helpers shared by the calendars' tests."""

import sqlite3
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from raphael_core.brain import db
from raphael_core.calendars.fetch import FetchError
from raphael_core.calendars.service import CalendarService
from raphael_core.calendars.vault import MemoryAddressVault
from raphael_core.router import Router

TPE = ZoneInfo("Asia/Taipei")
# Tuesday 2026-10-20, 12:00 in Taipei
NOON = int(datetime(2026, 10, 20, 12, tzinfo=TPE).timestamp() * 1000)

GOOGLE = "https://calendar.google.com/calendar/ical/me%40example.com/private-0123456789abcdef/basic.ics"
OTHER = "https://example.org/feeds/work.ics?token=s3cr3t-t0ken"


def ics(*events: str) -> bytes:
    """An iCalendar file with the events (the lines between BEGIN:VEVENT and END:VEVENT)."""
    body = "".join(f"BEGIN:VEVENT\n{event.strip()}\nEND:VEVENT\n" for event in events)
    text = f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//\n{body}END:VCALENDAR\n"
    return text.replace("\n", "\r\n").encode()


def event(title: str, start: str, end: str | None = None, uid: str | None = None) -> str:
    """A timed event in Taipei; `start` and `end` are times like 20261020T093000."""
    lines = [f"UID:{uid or title}", f"DTSTART;TZID=Asia/Taipei:{start}", f"SUMMARY:{title}"]
    if end:
        lines.insert(2, f"DTEND;TZID=Asia/Taipei:{end}")
    return "\n".join(lines)


class FakeNet:
    """What a download gets: a body or a FetchError for each address, and a record of the asks."""

    def __init__(self) -> None:
        self.pages: dict[str, bytes | Exception] = {}
        self.asked: list[str] = []

    async def __call__(self, address: str) -> bytes:
        self.asked.append(address)
        page = self.pages.get(address, FetchError("network"))
        if isinstance(page, Exception):
            raise page
        return page


class Clock:
    """A clock the test moves."""

    def __init__(self, now_ms: int = NOON) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, seconds: float) -> None:
        self.now_ms += round(seconds * 1000)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def net() -> FakeNet:
    return FakeNet()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def vault() -> MemoryAddressVault:
    return MemoryAddressVault()


@pytest.fixture
def service(conn, vault, net, clock) -> CalendarService:
    return CalendarService(conn, vault, fetch=net, now_ms=clock, tz=TPE)


@pytest.fixture
def router(service) -> Router:
    r = Router()
    service.register(r)
    return r
