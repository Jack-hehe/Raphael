"""The calendars as RPC methods: `calendar.list`, `calendar.add`, `calendar.remove`,
`calendar.test` and `calendar.today`.

A calendar is an iCalendar subscription address (Google Calendar's "secret address in iCal format",
for one) and a label the user gave it. Read-only: nothing is ever written to a calendar.

- The address is a secret. It goes to the vault (vault.py) and nowhere else, and the page never gets
  it back: `calendar.list` gives the label and the host (`calendar.google.com`), nothing of the path
  or the query. The page does send it once, in `calendar.add` and in `calendar.test` with a `url`,
  as it sends the OpenRouter key to `secrets.set_openrouter_key`.
- `calendar.add` fetches the address and reads it before it stores anything, and answers
  `{"ok": false, "reason": ...}` when that does not work (a reason is a word: url, not_found, denied,
  http, timeout, network, too_large, not_calendar, duplicate or limit), as the key check does.
- `calendar.today` gives the events of today in this computer's zone from every calendar, sorted,
  and the calendars that could not be read. A calendar is downloaded at most every
  `CACHE_TTL_S`; one that cannot be reached is answered from the last reading for up to an hour.
- Nothing here logs an address, a label, a title or the message of a failure. A failure is told by
  its kind. Every handler is sealed so that no exception message (httpx puts addresses in some of
  them) reaches the router's log or the page.
"""

import asyncio
import functools
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, tzinfo
from typing import Any

from ..protocol import INTERNAL_ERROR, INVALID_PARAMS, RpcError
from ..router import Router
from .fetch import FetchError, clean_url, fetch_ics, host_of
from .ics import CalendarFormatError, Occurrence, SystemZone, events_on
from .store import CalendarRow, CalendarStore
from .vault import AddressVault

logger = logging.getLogger(__name__)

# How long a download is kept before the calendar is read again, and for how long it still stands
# in for one that cannot be reached.
CACHE_TTL_S = 180
STALE_TTL_S = 3600
MAX_CALENDARS = 10
LABEL_MAX_CHARS = 40

NOT_CALENDAR = "not_calendar"
DUPLICATE = "duplicate"
LIMIT = "limit"
MISSING = "missing"  # the label is here, the address is not (the vault lost it)
UNAVAILABLE = "unavailable"  # the vault could not be read

Fetch = Callable[[str], Awaitable[bytes]]


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _invalid(message: str) -> RpcError:
    """An INVALID_PARAMS error. `message` names a param, never a value: it may be an address."""
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


def _str(params: dict, name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str):
        raise _invalid(f"{name} must be a string")
    return value


def _int(params: dict, name: str) -> int:
    value = params.get(name)
    if type(value) is not int:
        raise _invalid(f"{name} must be a whole number")
    return value


def _label(params: dict) -> str:
    label = " ".join(_str(params, "label").split())
    if not 1 <= len(label) <= LABEL_MAX_CHARS:
        raise _invalid(f"label must be 1 to {LABEL_MAX_CHARS} characters")
    return label


class _Unreadable(Exception):
    """A calendar that could not be read; `kind` says why, in a word."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass
class _Reading:
    """What a calendar had on a day when it was last read."""

    day: date
    at_ms: int
    events: list[Occurrence]


def _sealed(handler: Callable[[dict], Awaitable[Any]]) -> Callable[[dict], Awaitable[Any]]:
    """The handler, with every exception but an RpcError turned into one that says nothing of its
    cause: the message of an exception from a download or the vault may hold an address."""

    @functools.wraps(handler)
    async def sealed(params: dict) -> Any:
        try:
            return await handler(params)
        except (RpcError, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("A calendar call failed: %s", type(exc).__name__)
            raise RpcError(INTERNAL_ERROR, "The calendar call failed") from None

    return sealed


class CalendarService:
    """The calendars' stores and the methods that use them.

    `conn` is the open database, `vault` where the addresses live, `fetch` how an address is
    downloaded (a test passes its own), `now_ms` the clock and `tz` the zone of the days (this
    computer's, if None).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        vault: AddressVault,
        *,
        fetch: Fetch = fetch_ics,
        now_ms: Callable[[], int] = _wall_clock_ms,
        tz: tzinfo | None = None,
        cache_ttl_s: float = CACHE_TTL_S,
    ) -> None:
        self._store = CalendarStore(conn)
        self._vault = vault
        self._fetch = fetch
        self._now_ms = now_ms
        self._tz = tz
        self._ttl_ms = round(cache_ttl_s * 1000)
        self._cache: dict[int, _Reading] = {}
        # Counts the calendars added and removed, so that a reading that was under way when one
        # was cannot be filed under an id that now means another calendar.
        self._generation = 0

    def register(self, router: Router) -> None:
        """Put every method of the calendars on `router`."""
        methods = {
            "calendar.list": self._list,
            "calendar.add": self._add,
            "calendar.remove": self._remove,
            "calendar.test": self._test,
            "calendar.today": self._today,
        }
        for method, handler in methods.items():
            router.register(method, _sealed(handler))

    # --- helpers --------------------------------------------------------------------------------

    def _day(self) -> date:
        zone = SystemZone() if self._tz is None else self._tz
        return datetime.fromtimestamp(self._now_ms() / 1000, zone).date()

    def _address(self, calendar_id: int) -> str | None:
        """The address, or None when the vault has none or cannot be read."""
        try:
            return self._vault.get(calendar_id) or None
        except Exception as exc:
            logger.warning("The vault could not be read for a calendar: %s", type(exc).__name__)
            return None

    @staticmethod
    def _view(row: CalendarRow, address: str | None) -> dict:
        """What the page may know of a calendar: not the address, only its host."""
        return {"id": row.id, "label": row.label, "host": host_of(address) if address else None}

    async def _read(self, address: str, day: date) -> list[Occurrence]:
        """Download the calendar and read the events of `day` out of it. Raises _Unreadable."""
        try:
            data = await self._fetch(address)
        except FetchError as err:
            raise _Unreadable(err.kind) from None
        try:
            # a few MB of text take a second or two to read: not on the loop that hears the keys
            return await asyncio.to_thread(events_on, data, day, self._tz)
        except CalendarFormatError:
            raise _Unreadable(NOT_CALENDAR) from None
        except Exception as exc:
            # a bug in the reader, not in the calendar; it is still a calendar that cannot be read
            logger.warning("A calendar could not be read: %s", type(exc).__name__)
            raise _Unreadable(NOT_CALENDAR) from None

    async def _events_of(self, row: CalendarRow, day: date, force: bool) -> tuple[list[Occurrence], str | None]:
        """The events of a calendar on `day` and, when it could not be read, why."""
        now = self._now_ms()
        known = self._cache.get(row.id)
        if known is not None and known.day != day:
            known = None
        if known is not None and not force and now - known.at_ms < self._ttl_ms:
            return known.events, None
        address = self._address(row.id)
        if address is None:
            return [], MISSING
        generation = self._generation
        try:
            events = await self._read(address, day)
        except _Unreadable as err:
            if known is not None and now - known.at_ms < STALE_TTL_S * 1000:
                return known.events, None
            logger.warning("A calendar could not be read: %s", err.kind)
            return [], err.kind
        if generation == self._generation:
            self._cache[row.id] = _Reading(day, now, events)
        return events, None

    def _is_added(self, address: str) -> bool:
        return any(self._address(row.id) == address for row in self._store.list())

    # --- the methods ----------------------------------------------------------------------------

    async def _list(self, params: dict) -> dict:
        """The calendars, each with its label and the host of its address (None when the address
        is lost)."""
        return {"calendars": [self._view(row, self._address(row.id)) for row in self._store.list()]}

    async def _add(self, params: dict) -> dict:
        """Fetch the address, read it and, if it is a calendar, store it."""
        label = _label(params)
        try:
            address = clean_url(_str(params, "url"))
        except FetchError as err:
            return {"ok": False, "reason": err.kind}
        if self._store.count() >= MAX_CALENDARS:
            return {"ok": False, "reason": LIMIT}
        if self._is_added(address):
            return {"ok": False, "reason": DUPLICATE}
        day = self._day()
        try:
            events = await self._read(address, day)
        except _Unreadable as err:
            return {"ok": False, "reason": err.kind}
        now = self._now_ms()
        calendar_id = self._store.add(label, now)
        try:
            self._vault.set(calendar_id, address)
        except Exception as exc:
            self._store.remove(calendar_id)
            logger.warning("The vault failed to store a calendar address: %s", type(exc).__name__)
            raise RpcError(INTERNAL_ERROR, "Could not store the calendar address") from None
        self._generation += 1
        self._cache[calendar_id] = _Reading(day, now, events)
        return {"ok": True, "calendar": self._view(CalendarRow(calendar_id, label), address), "events_today": len(events)}

    async def _remove(self, params: dict) -> dict:
        calendar_id = _int(params, "id")
        if self._store.get(calendar_id) is None:
            return {"removed": False}
        try:
            self._vault.clear(calendar_id)
        except Exception as exc:
            logger.warning("The vault failed to remove a calendar address: %s", type(exc).__name__)
            raise RpcError(INTERNAL_ERROR, "Could not remove the calendar address") from None
        self._store.remove(calendar_id)
        self._cache.pop(calendar_id, None)
        self._generation += 1
        return {"removed": True}

    async def _test(self, params: dict) -> dict:
        """Fetch and read a calendar: a stored one (`id`) or an address not yet stored (`url`)."""
        day = self._day()
        if "id" in params:
            calendar_id = _int(params, "id")
            if self._store.get(calendar_id) is None:
                return {"ok": False, "reason": MISSING}
            address = self._address(calendar_id)
            if address is None:
                return {"ok": False, "reason": MISSING}
        else:
            try:
                address = clean_url(_str(params, "url"))
            except FetchError as err:
                return {"ok": False, "reason": err.kind}
            calendar_id = None
        try:
            events = await self._read(address, day)
        except _Unreadable as err:
            return {"ok": False, "reason": err.kind}
        if calendar_id is not None and self._store.get(calendar_id) is not None:
            self._cache[calendar_id] = _Reading(day, self._now_ms(), events)
        return {"ok": True, "events_today": len(events)}

    async def _today(self, params: dict) -> dict:
        """Today's events from every calendar, sorted, and the calendars that could not be read.
        `refresh` skips the cache."""
        refresh = params.get("refresh", False)
        if not isinstance(refresh, bool):
            raise _invalid("refresh must be true or false")
        day = self._day()
        rows = self._store.list()
        outcomes = await asyncio.gather(*(self._events_of(row, day, refresh) for row in rows))
        events: list[Occurrence] = []
        failed = []
        for row, (found, reason) in zip(rows, outcomes, strict=True):
            events += found
            if reason is not None:
                failed.append({"id": row.id, "label": row.label, "reason": reason})
        events.sort(key=lambda o: (not o.all_day, o.start_ms, o.end_ms, o.title))
        return {
            "date": day.isoformat(),
            "calendars": len(rows),
            "events": [asdict(event) for event in events],
            "failed": failed,
        }
