"""Downloading a calendar (calendars/fetch.py). No network: httpx's own mock transport answers."""

import logging

import httpx
import pytest

from raphael_core.calendars import fetch
from raphael_core.calendars.fetch import FetchError, clean_url, fetch_ics, host_of

from .conftest import GOOGLE, OTHER, ics


def serving(handler):
    return httpx.MockTransport(handler)


# --- the address ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed, wanted",
    [
        (GOOGLE, GOOGLE),
        ("  " + OTHER + "\n", OTHER),
        ("http://example.org/a.ics", "http://example.org/a.ics"),
        ("webcal://example.org/a.ics", "https://example.org/a.ics"),
        ("WEBCAL://example.org/a.ics", "https://example.org/a.ics"),
        ("webcals://example.org/a.ics", "https://example.org/a.ics"),
        ("https://example.org:8443/a.ics", "https://example.org:8443/a.ics"),
    ],
)
def test_an_address_is_taken_as_an_https_or_http_address(typed, wanted):
    assert clean_url(typed) == wanted


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "   ",
        "example.org/a.ics",
        "ftp://example.org/a.ics",
        "file:///C:/calendar.ics",
        "javascript:alert(1)",
        "https://",
        "https:///a.ics",
        "https://exa mple.org/a.ics",
        "https://example.org/a\nb.ics",
        "https://example.org:notaport/a.ics",
        "https://example.org/" + "a" * fetch.URL_MAX_CHARS,
    ],
)
def test_what_is_not_such_an_address_is_refused_with_the_word_url(typed):
    with pytest.raises(FetchError) as refused:
        clean_url(typed)
    assert refused.value.kind == fetch.URL
    assert not typed.strip() or typed.strip() not in str(refused.value)  # it says nothing of the address


def test_only_the_host_of_an_address_is_shown():
    assert host_of(GOOGLE) == "calendar.google.com"
    assert host_of("https://User:Pass@Example.org:8443/a/b.ics?token=x#y") == "example.org"
    assert host_of("not an address") == ""
    assert host_of("https://[::1") == ""


# --- the download -----------------------------------------------------------------------------


async def test_the_body_is_returned_and_the_calendar_is_asked_for_as_one():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=ics("UID:a\nDTSTART:20261020T010000Z"))

    body = await fetch_ics(GOOGLE, transport=serving(handler))
    assert b"BEGIN:VCALENDAR" in body
    assert "text/calendar" in seen[0].headers["accept"]
    assert str(seen[0].url) == GOOGLE


async def test_a_redirect_is_followed():
    def handler(request):
        if request.url.path == "/old.ics":
            return httpx.Response(301, headers={"location": "https://example.org/new.ics"})
        return httpx.Response(200, content=b"BEGIN:VCALENDAR")

    assert await fetch_ics("https://example.org/old.ics", transport=serving(handler)) == b"BEGIN:VCALENDAR"


async def test_redirects_that_never_end_are_a_network_failure():
    def handler(request):
        return httpx.Response(302, headers={"location": str(request.url) + "x"})

    with pytest.raises(FetchError) as failed:
        await fetch_ics(OTHER, transport=serving(handler))
    assert failed.value.kind == fetch.NETWORK


@pytest.mark.parametrize(
    "status, kind",
    [(404, fetch.NOT_FOUND), (410, fetch.NOT_FOUND), (401, fetch.DENIED), (403, fetch.DENIED), (500, fetch.HTTP), (429, fetch.HTTP)],
)
async def test_an_error_status_is_told_by_its_kind(status, kind):
    with pytest.raises(FetchError) as failed:
        await fetch_ics(GOOGLE, transport=serving(lambda request: httpx.Response(status)))
    assert failed.value.kind == kind


@pytest.mark.parametrize(
    "error, kind",
    [
        (httpx.ReadTimeout("timed out"), fetch.TIMEOUT),
        (httpx.ConnectTimeout("timed out"), fetch.TIMEOUT),
        (httpx.ConnectError("name not found"), fetch.NETWORK),
        (httpx.RemoteProtocolError("closed"), fetch.NETWORK),
        (UnicodeError("label too long"), fetch.NETWORK),
    ],
)
async def test_a_failure_of_the_connection_is_told_by_its_kind(error, kind):
    def handler(request):
        raise error

    with pytest.raises(FetchError) as failed:
        await fetch_ics(GOOGLE, transport=serving(handler))
    assert failed.value.kind == kind


async def test_a_file_that_is_too_large_is_refused_before_it_is_all_read(monkeypatch):
    monkeypatch.setattr(fetch, "MAX_BYTES", 1000)
    chunks = []

    async def stream():
        for _ in range(100):
            chunks.append(1)
            yield b"x" * 100

    with pytest.raises(FetchError) as failed:
        await fetch_ics(GOOGLE, transport=serving(lambda request: httpx.Response(200, content=stream())))
    assert failed.value.kind == fetch.TOO_LARGE
    assert len(chunks) < 100


async def test_an_exception_of_a_download_has_no_trace_of_the_address():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(FetchError) as failed:
        await fetch_ics(GOOGLE, transport=serving(handler))
    assert "private-0123456789abcdef" not in repr(failed.value) + str(failed.value)
    assert failed.value.__cause__ is None
    assert failed.value.__suppress_context__


# --- the log ----------------------------------------------------------------------------------


async def test_httpx_logs_the_address_of_a_request_but_not_one_made_here(caplog):
    caplog.set_level(logging.INFO)
    handler = lambda request: httpx.Response(200, content=b"BEGIN:VCALENDAR")  # noqa: E731
    async with httpx.AsyncClient(transport=serving(handler)) as client:
        await client.get(GOOGLE)
    assert "private-0123456789abcdef" in caplog.text  # this is what the filter is for
    caplog.clear()

    await fetch_ics(GOOGLE, transport=serving(handler))
    assert "private-0123456789abcdef" not in caplog.text
    assert "calendar.google.com" not in caplog.text


async def test_the_log_is_open_again_after_a_download(caplog):
    caplog.set_level(logging.INFO)
    handler = lambda request: httpx.Response(200, content=b"x")  # noqa: E731
    await fetch_ics(GOOGLE, transport=serving(handler))
    async with httpx.AsyncClient(transport=serving(handler)) as client:
        await client.get("https://example.org/other")
    assert "https://example.org/other" in caplog.text


async def test_even_a_failed_or_redirected_download_leaves_the_address_out_of_the_log(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        if request.url.path.endswith("basic.ics"):
            return httpx.Response(302, headers={"location": OTHER})
        return httpx.Response(404)

    with pytest.raises(FetchError):
        await fetch_ics(GOOGLE, transport=serving(handler))
    assert "private-0123456789abcdef" not in caplog.text
    assert "s3cr3t-t0ken" not in caplog.text
