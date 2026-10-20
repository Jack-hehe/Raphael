"""The `calendar.*` methods (calendars/service.py).

The download is replaced by `FakeNet`, the vault by the in-memory one, the clock and the zone are
injected: no network, no real vault, and no test depends on today or on the machine's zone.
"""

import asyncio
import logging
from datetime import datetime

import pytest

from raphael_core.calendars.fetch import FetchError
from raphael_core.calendars.service import CalendarService, MAX_CALENDARS
from raphael_core.protocol import INTERNAL_ERROR, INVALID_PARAMS, RpcError
from raphael_core.router import Router

from .conftest import GOOGLE, OTHER, TPE, event, ics

STANDUP = event("Standup", "20261020T093000", "20261020T100000")
LUNCH = event("Lunch with Mei", "20261020T120000", "20261020T130000")
TOMORROW = event("Tomorrow", "20261021T090000", "20261021T100000")
HOLIDAY = "UID:h\nDTSTART;VALUE=DATE:20261020\nDTEND;VALUE=DATE:20261021\nSUMMARY:Holiday"


def ms(h, m=0, day=20):
    return int(datetime(2026, 10, day, h, m, tzinfo=TPE).timestamp() * 1000)


async def add(router, label="Personal", url=GOOGLE):
    return await router.call("calendar.add", {"label": label, "url": url})


# --- adding, listing, removing ----------------------------------------------------------------


async def test_without_calendars_the_list_and_today_are_empty(router):
    assert await router.call("calendar.list") == {"calendars": []}
    assert await router.call("calendar.today") == {"date": "2026-10-20", "calendars": 0, "events": [], "failed": []}


async def test_adding_a_calendar_reads_it_and_stores_it(router, net, vault):
    net.pages[GOOGLE] = ics(STANDUP, TOMORROW)
    added = await add(router)
    assert added == {
        "ok": True,
        "calendar": {"id": 1, "label": "Personal", "host": "calendar.google.com"},
        "events_today": 1,
    }
    assert vault.get(1) == GOOGLE
    assert await router.call("calendar.list") == {"calendars": [added["calendar"]]}
    assert net.asked == [GOOGLE]


async def test_the_page_is_never_given_the_address_back(router, net):
    net.pages[GOOGLE] = ics(STANDUP)
    net.pages[OTHER] = ics(LUNCH)
    answers = [await add(router, "A", GOOGLE), await add(router, "B", OTHER)]
    answers.append(await router.call("calendar.list"))
    answers.append(await router.call("calendar.today"))
    answers.append(await router.call("calendar.test", {"id": 1}))
    for answer in answers:
        text = str(answer)
        assert "private-0123456789abcdef" not in text
        assert "s3cr3t-t0ken" not in text
        assert "/calendar/ical" not in text
    assert [c["host"] for c in answers[2]["calendars"]] == ["calendar.google.com", "example.org"]


async def test_the_address_is_in_the_vault_and_not_in_the_database(router, net, conn, vault):
    net.pages[GOOGLE] = ics(STANDUP)
    await add(router)
    assert vault.get(1) == GOOGLE
    dump = "\n".join(conn.iterdump())
    assert "private-0123456789abcdef" not in dump
    assert "calendar.google.com" not in dump
    assert "Personal" in dump  # the label is not a secret


async def test_an_address_typed_as_webcal_is_stored_as_https(router, net, vault):
    net.pages["https://example.org/a.ics"] = ics(STANDUP)
    assert (await add(router, url="webcal://example.org/a.ics"))["ok"] is True
    assert vault.get(1) == "https://example.org/a.ics"


async def test_a_label_is_trimmed_and_may_not_be_empty_or_long(router, net):
    net.pages[GOOGLE] = ics(STANDUP)
    assert (await add(router, label="  Work   stuff "))["calendar"]["label"] == "Work stuff"
    for label in ("", "   ", "x" * 41):
        with pytest.raises(RpcError) as bad:
            await add(router, label=label, url=OTHER)
        assert bad.value.code == INVALID_PARAMS
    with pytest.raises(RpcError) as bad:
        await router.call("calendar.add", {"label": 5, "url": OTHER})
    assert bad.value.code == INVALID_PARAMS
    with pytest.raises(RpcError) as bad:
        await router.call("calendar.add", {"label": "A"})
    assert bad.value.code == INVALID_PARAMS
    assert OTHER not in bad.value.message


@pytest.mark.parametrize(
    "typed, reason",
    [("not an address", "url"), ("ftp://example.org/a.ics", "url"), ("", "url")],
)
async def test_what_is_not_an_address_is_not_even_fetched(router, net, typed, reason):
    assert await add(router, url=typed) == {"ok": False, "reason": reason}
    assert net.asked == []
    assert (await router.call("calendar.list"))["calendars"] == []


@pytest.mark.parametrize("reason", ["not_found", "denied", "http", "timeout", "network", "too_large"])
async def test_a_calendar_that_cannot_be_fetched_is_not_stored(router, net, vault, reason):
    net.pages[GOOGLE] = FetchError(reason)
    assert await add(router) == {"ok": False, "reason": reason}
    assert (await router.call("calendar.list"))["calendars"] == []
    assert vault.get(1) is None


async def test_an_address_that_gives_something_other_than_a_calendar_is_not_stored(router, net, vault):
    net.pages[GOOGLE] = b"<html><body>Sign in to continue</body></html>"
    assert await add(router) == {"ok": False, "reason": "not_calendar"}
    assert (await router.call("calendar.list"))["calendars"] == []
    assert vault.get(1) is None


async def test_the_same_address_twice_is_refused(router, net):
    net.pages[GOOGLE] = ics(STANDUP)
    await add(router, "One")
    assert await add(router, "Two") == {"ok": False, "reason": "duplicate"}
    assert len((await router.call("calendar.list"))["calendars"]) == 1


async def test_there_is_a_limit_to_the_calendars(router, net):
    for n in range(MAX_CALENDARS):
        address = f"https://example.org/{n}.ics"
        net.pages[address] = ics(STANDUP)
        assert (await add(router, f"C{n}", address))["ok"] is True
    net.pages[GOOGLE] = ics(STANDUP)
    assert await add(router) == {"ok": False, "reason": "limit"}


async def test_removing_a_calendar_removes_its_address_and_its_events(router, net, vault):
    net.pages[GOOGLE] = ics(STANDUP)
    await add(router)
    assert len((await router.call("calendar.today"))["events"]) == 1
    assert await router.call("calendar.remove", {"id": 1}) == {"removed": True}
    assert vault.get(1) is None
    assert await router.call("calendar.list") == {"calendars": []}
    assert (await router.call("calendar.today"))["events"] == []
    assert await router.call("calendar.remove", {"id": 1}) == {"removed": False}


async def test_a_calendar_added_after_another_was_removed_does_not_get_its_events(router, net, vault):
    net.pages[GOOGLE] = ics(STANDUP)
    net.pages[OTHER] = ics(LUNCH)
    await add(router, "One", GOOGLE)
    await router.call("calendar.today")
    await router.call("calendar.remove", {"id": 1})
    assert (await add(router, "Two", OTHER))["calendar"]["id"] == 1  # SQLite hands the id out again
    assert [e["title"] for e in (await router.call("calendar.today"))["events"]] == ["Lunch with Mei"]


async def test_a_vault_that_cannot_store_leaves_nothing_behind(conn, net, clock):
    class BrokenVault:
        def get(self, calendar_id):
            return None

        def set(self, calendar_id, address):
            raise OSError(f"the vault says no to {address}")

        def clear(self, calendar_id):
            pass

    service = CalendarService(conn, BrokenVault(), fetch=net, now_ms=clock, tz=TPE)
    router = Router()
    service.register(router)
    net.pages[GOOGLE] = ics(STANDUP)
    with pytest.raises(RpcError) as failed:
        await add(router)
    assert failed.value.code == INTERNAL_ERROR
    assert "private-0123456789abcdef" not in failed.value.message
    assert (await router.call("calendar.list"))["calendars"] == []


async def test_a_vault_that_cannot_remove_keeps_the_calendar(router, net, vault, monkeypatch):
    net.pages[GOOGLE] = ics(STANDUP)
    await add(router)

    def refuse(calendar_id):
        raise OSError(f"no: {GOOGLE}")

    monkeypatch.setattr(vault, "clear", refuse)
    with pytest.raises(RpcError) as failed:
        await router.call("calendar.remove", {"id": 1})
    assert failed.value.code == INTERNAL_ERROR
    assert GOOGLE not in failed.value.message
    assert len((await router.call("calendar.list"))["calendars"]) == 1


async def test_a_calendar_whose_address_is_lost_is_listed_without_a_host(router, net, vault, clock):
    net.pages[GOOGLE] = ics(STANDUP)
    await add(router)
    vault.clear(1)
    clock.advance(600)
    assert await router.call("calendar.list") == {"calendars": [{"id": 1, "label": "Personal", "host": None}]}
    today = await router.call("calendar.today")
    assert today["failed"] == [{"id": 1, "label": "Personal", "reason": "missing"}]
    assert today["calendars"] == 1
    assert await router.call("calendar.test", {"id": 1}) == {"ok": False, "reason": "missing"}
    assert await router.call("calendar.remove", {"id": 1}) == {"removed": True}


# --- testing ----------------------------------------------------------------------------------


async def test_a_stored_calendar_is_tested_by_fetching_it(router, net):
    net.pages[GOOGLE] = ics(STANDUP, LUNCH)
    await add(router)
    net.asked.clear()
    assert await router.call("calendar.test", {"id": 1}) == {"ok": True, "events_today": 2}
    assert net.asked == [GOOGLE]
    net.pages[GOOGLE] = FetchError("not_found")
    assert await router.call("calendar.test", {"id": 1}) == {"ok": False, "reason": "not_found"}


async def test_an_address_can_be_tested_before_it_is_stored(router, net, vault):
    net.pages[OTHER] = ics(HOLIDAY)
    assert await router.call("calendar.test", {"url": OTHER}) == {"ok": True, "events_today": 1}
    assert await router.call("calendar.test", {"url": "nonsense"}) == {"ok": False, "reason": "url"}
    net.pages[OTHER] = b"nope"
    assert await router.call("calendar.test", {"url": OTHER}) == {"ok": False, "reason": "not_calendar"}
    assert (await router.call("calendar.list"))["calendars"] == []
    assert vault.get(1) is None


async def test_testing_an_unknown_calendar_says_it_is_missing(router):
    assert await router.call("calendar.test", {"id": 99}) == {"ok": False, "reason": "missing"}
    with pytest.raises(RpcError) as bad:
        await router.call("calendar.test", {})
    assert bad.value.code == INVALID_PARAMS


# --- today ------------------------------------------------------------------------------------


async def test_today_merges_the_calendars_and_sorts_them(router, net):
    net.pages[GOOGLE] = ics(LUNCH, TOMORROW, HOLIDAY)
    net.pages[OTHER] = ics(STANDUP)
    await add(router, "Personal", GOOGLE)
    await add(router, "Work", OTHER)
    today = await router.call("calendar.today")
    assert today["date"] == "2026-10-20"
    assert today["calendars"] == 2
    assert today["failed"] == []
    assert [(e["title"], e["all_day"]) for e in today["events"]] == [
        ("Holiday", True),
        ("Standup", False),
        ("Lunch with Mei", False),
    ]
    standup = today["events"][1]
    assert (standup["start_ms"], standup["end_ms"]) == (ms(9, 30), ms(10))


async def test_the_day_is_the_day_in_the_zone_of_the_clock(router, net, clock):
    net.pages[GOOGLE] = ics(STANDUP, TOMORROW)
    await add(router)
    clock.now_ms = ms(23, 30)
    clock.advance(31 * 60)  # 00:01 on the 21st in Taipei
    today = await router.call("calendar.today")
    assert today["date"] == "2026-10-21"
    assert [e["title"] for e in today["events"]] == ["Tomorrow"]


async def test_a_calendar_that_fails_does_not_take_the_others_with_it(router, net, clock):
    net.pages[GOOGLE] = ics(STANDUP)
    net.pages[OTHER] = ics(LUNCH)
    await add(router, "Personal", GOOGLE)
    await add(router, "Work", OTHER)
    clock.advance(4000)  # past the cache, and past the time an old reading stands in
    net.pages[GOOGLE] = FetchError("timeout")
    today = await router.call("calendar.today")
    assert [e["title"] for e in today["events"]] == ["Lunch with Mei"]
    assert today["failed"] == [{"id": 1, "label": "Personal", "reason": "timeout"}]


async def test_a_calendar_is_not_downloaded_again_within_the_cache_time(router, net, clock):
    net.pages[GOOGLE] = ics(STANDUP)
    await add(router)
    net.asked.clear()
    await router.call("calendar.today")
    clock.advance(179)
    await router.call("calendar.today")
    assert net.asked == []  # the reading that `calendar.add` made served both
    clock.advance(2)
    net.pages[GOOGLE] = ics(STANDUP, LUNCH)
    assert len((await router.call("calendar.today"))["events"]) == 2
    assert net.asked == [GOOGLE]
    await router.call("calendar.today")
    assert net.asked == [GOOGLE]


async def test_refresh_downloads_again_at_once(router, net):
    net.pages[GOOGLE] = ics(STANDUP)
    await add(router)
    net.pages[GOOGLE] = ics(STANDUP, LUNCH)
    assert len((await router.call("calendar.today"))["events"]) == 1
    assert len((await router.call("calendar.today", {"refresh": True}))["events"]) == 2
    with pytest.raises(RpcError) as bad:
        await router.call("calendar.today", {"refresh": "yes"})
    assert bad.value.code == INVALID_PARAMS


async def test_a_new_day_is_read_afresh(router, net, clock):
    net.pages[GOOGLE] = ics(STANDUP, TOMORROW)
    await add(router)
    net.asked.clear()
    clock.now_ms = ms(0, 5, day=21)  # within the cache time of the reading, but the next day
    assert [e["title"] for e in (await router.call("calendar.today"))["events"]] == ["Tomorrow"]
    assert net.asked == [GOOGLE]


async def test_a_calendar_that_cannot_be_reached_is_answered_from_its_last_reading_for_an_hour(router, net, clock):
    net.pages[GOOGLE] = ics(STANDUP)
    await add(router)
    net.pages[GOOGLE] = FetchError("network")
    clock.advance(600)
    today = await router.call("calendar.today")
    assert [e["title"] for e in today["events"]] == ["Standup"]
    assert today["failed"] == []
    clock.advance(3000)  # 3600 s since the reading
    today = await router.call("calendar.today")
    assert today["events"] == []
    assert today["failed"] == [{"id": 1, "label": "Personal", "reason": "network"}]


async def test_a_calendar_that_turns_into_html_is_reported(router, net, clock):
    net.pages[GOOGLE] = ics(STANDUP)
    await add(router)
    clock.advance(4000)
    net.pages[GOOGLE] = b"<html>captive portal</html>"
    assert (await router.call("calendar.today"))["failed"][0]["reason"] == "not_calendar"


async def test_a_calendar_removed_while_it_is_being_read_is_not_kept(conn, vault, clock):
    pages = {GOOGLE: ics(STANDUP), OTHER: ics(LUNCH)}
    gate = asyncio.Event()
    started = asyncio.Event()
    hold = []  # the address to hold up, once there is one

    async def slow(address):
        if address in hold:
            hold.clear()
            started.set()
            await gate.wait()
        return pages[address]

    service = CalendarService(conn, vault, fetch=slow, now_ms=clock, tz=TPE)
    router = Router()
    service.register(router)
    await add(router, "One", GOOGLE)
    clock.advance(600)
    hold.append(GOOGLE)
    reading = asyncio.create_task(router.call("calendar.today"))
    await started.wait()
    await router.call("calendar.remove", {"id": 1})
    assert (await add(router, "Two", OTHER))["calendar"]["id"] == 1  # the same id, another calendar
    gate.set()
    await reading
    # the reading of the first calendar, which finished last, must not stand for the second
    assert [e["title"] for e in (await router.call("calendar.today"))["events"]] == ["Lunch with Mei"]


# --- what is never told -----------------------------------------------------------------------


SECRETS = ("private-0123456789abcdef", "s3cr3t-t0ken", "calendar.google.com", "Standup", "Lunch with Mei", "Private label")


async def test_nothing_secret_reaches_the_log_through_a_whole_session(router, net, clock, caplog):
    caplog.set_level(logging.DEBUG)
    net.pages[GOOGLE] = ics(STANDUP, LUNCH, "UID:bad\nDTSTART:nonsense\nSUMMARY:Lunch with Mei")
    net.pages[OTHER] = FetchError("timeout")
    await add(router, "Private label", GOOGLE)
    await add(router, "Private label", OTHER)
    await router.call("calendar.test", {"id": 1})
    await router.call("calendar.test", {"url": OTHER})
    clock.advance(4000)
    net.pages[GOOGLE] = FetchError("network")
    await router.call("calendar.today")
    net.pages[GOOGLE] = b"<html>no</html>"
    await router.call("calendar.today", {"refresh": True})
    await router.call("calendar.remove", {"id": 1})
    for secret in SECRETS:
        assert secret not in caplog.text, secret


async def test_an_exception_from_inside_is_sealed(conn, vault, clock, caplog):
    async def leaky(address):
        raise RuntimeError(f"Request to {address} failed")

    service = CalendarService(conn, vault, fetch=leaky, now_ms=clock, tz=TPE)
    router = Router()
    service.register(router)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RpcError) as failed:
        await add(router)
    assert failed.value.code == INTERNAL_ERROR
    assert "private-0123456789abcdef" not in failed.value.message
    assert "private-0123456789abcdef" not in caplog.text
    assert failed.value.__cause__ is None


def test_the_methods_are_on_the_router(router):
    assert router.methods() == ["calendar.add", "calendar.list", "calendar.remove", "calendar.test", "calendar.today"]
