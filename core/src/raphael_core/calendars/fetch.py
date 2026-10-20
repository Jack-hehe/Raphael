"""Downloading a calendar by its address.

An address is a secret (Google's "secret address in iCal format" lets whoever has it read the whole
calendar), so nothing here puts one in a log or in an error: a failure is told by its kind alone.
httpx logs the address of every request it makes, at INFO, and that log goes to the app's own; the
requests made here are marked, and the log record of a marked request is dropped.
"""

import contextvars
import logging
from urllib.parse import urlsplit

import httpx

# How long one download may take (the connection and each wait for more of the file), and the
# largest file taken: a year of a busy calendar is a few MB.
FETCH_TIMEOUT_S = 8.0
MAX_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5
# The longest address kept. The vault holds 1280 characters of a value (2560 bytes, UTF-16).
URL_MAX_CHARS = 1000

# Why a download failed: the kind goes to the page, the message of the exception never does.
URL = "url"
NOT_FOUND = "not_found"
DENIED = "denied"
HTTP = "http"
TIMEOUT = "timeout"
NETWORK = "network"
TOO_LARGE = "too_large"

_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("calendar_fetch", default=False)


class _KeepAddressesOutOfTheLog(logging.Filter):
    """Drops what httpx logs while a calendar is being downloaded (and nothing else)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _quiet.get()


logging.getLogger("httpx").addFilter(_KeepAddressesOutOfTheLog())


class FetchError(Exception):
    """A download that failed. `kind` is one of the words above; there is no message."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def clean_url(text: str) -> str:
    """The address as it is fetched: `webcal://` is `https://`, and an address must be http or
    https, with a host, no spaces and not too long. Raises FetchError(URL) otherwise."""
    url = text.strip()
    scheme, separator, rest = url.partition("://")
    if separator and scheme.lower() in ("webcal", "webcals"):
        url = f"https://{rest}"
    if len(url) > URL_MAX_CHARS or any(char.isspace() or ord(char) < 0x20 for char in url):
        raise FetchError(URL)
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port  # a port that is not a number raises here
    except ValueError:
        raise FetchError(URL) from None
    if parts.scheme not in ("http", "https") or not host:
        raise FetchError(URL)
    return url


def host_of(url: str) -> str:
    """Only the host of an address: what the page may show of it (the path and query are the
    secret). Empty when there is none."""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


async def fetch_ics(
    url: str, *, timeout_s: float = FETCH_TIMEOUT_S, transport: httpx.AsyncBaseTransport | None = None
) -> bytes:
    """The body of `url`. Raises FetchError. `transport` is for tests."""
    token = _quiet.set(True)
    try:
        return await _download(url, timeout_s, transport)
    finally:
        _quiet.reset(token)


async def _download(url: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None) -> bytes:
    try:
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=timeout_s,
            headers={"Accept": "text/calendar, text/plain;q=0.8, */*;q=0.5", "User-Agent": "Raphael"},
        ) as client:
            async with client.stream("GET", url) as response:
                _check_status(response.status_code)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise FetchError(TOO_LARGE)
                return bytes(body)
    except FetchError:
        raise
    except httpx.TimeoutException:
        raise FetchError(TIMEOUT) from None
    except httpx.InvalidURL:
        raise FetchError(URL) from None
    except Exception:
        # Whatever else: a refused connection, a name that is not found, a failed certificate, too
        # many redirects, an address the idna codec refuses. None of it is told in words, which
        # could hold the address.
        raise FetchError(NETWORK) from None


def _check_status(status: int) -> None:
    if status < 400:
        return
    if status in (404, 410):
        raise FetchError(NOT_FOUND)
    if status in (401, 403):
        raise FetchError(DENIED)
    raise FetchError(HTTP)
