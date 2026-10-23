"""The built-in page reader's fetch: one web page from this computer, made safe to ask for.

A page is asked for because of an address someone wrote, and the one who wrote it may not be the
owner: a page read earlier, a search result. So the fetch goes only where any stranger on the
internet could go, and takes nothing of the owner's with it:

- only `http` and `https`, and no address with a user name or a password in it;
- the host is resolved here, and every address it resolves to must be a public one: never this
  computer (loopback), the local network (private, link-local, shared), multicast, or an address
  that is reserved or unspecified. The request is then sent to the address that was checked, with
  the host's name as the `Host` header and as the name TLS checks the certificate against, so a
  name that resolves to a public address when it is checked and to a private one when it is used
  (DNS rebinding) is no way round it;
- a redirect is not followed by the HTTP client: each one is checked like the first address, at
  most MAX_REDIRECTS of them;
- MAX_SECONDS for the whole of it, redirects included, and MAX_BYTES of body, counted as it comes
  in (decompressed: a small gzip that unpacks to gigabytes stops at the cap);
- only a page (`text/html`), plain text (`text/plain`) or a PDF (`application/pdf`, which the
  caller reads as a PDF);
- no cookies (each fetch has an HTTP client of its own, emptied of what a redirect set), no
  credentials, no proxy or `.netrc` from the environment (`trust_env=False`).

`resolver` and `transport` are for tests; by default the system's resolver and httpx's transport.
Nothing here logs an address or a byte of a page.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from .common import ServiceError

MAX_REDIRECTS = 5
MAX_SECONDS = 10.0
MAX_BYTES = 3 * 1024 * 1024
PAGE_TYPES = ("text/html", "text/plain")
PDF_TYPE = "application/pdf"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
# A browser's words for what it takes; some sites refuse a client that says nothing.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Raphael/0.2 (page reader)",
    "Accept": "text/html,text/plain;q=0.9,application/pdf;q=0.8,*/*;q=0.1",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8,ja;q=0.7",
}

# host, port -> the addresses the host resolves to.
Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolver(host: str, port: int) -> list[str]:
    """The addresses `host` resolves to, by the system's resolver, each once and in its order."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public(address: str) -> bool:
    """Whether `address` (an IPv4 or IPv6 address, as text) is one anyone on the internet may be
    sent to: not this computer, not a local network, not multicast, reserved or unspecified. An
    IPv6 address that carries an IPv4 one (mapped, 6to4, Teredo) is judged by that one too."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        carried = [ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None]
        return all(inner is None or is_public(str(inner)) for inner in carried)
    return True


@dataclass(frozen=True)
class Fetched:
    """What a fetch brought back: the address it ended at, the type, the charset the server named
    (or None), and the body."""

    url: str
    content_type: str
    charset: str | None
    body: bytes


def checked_url(url: str) -> httpx.URL:
    """`url` as an address the reader may ask for, or ServiceError("bad_url")."""
    try:
        parsed = httpx.URL(url.strip())
    except (httpx.InvalidURL, TypeError, ValueError):
        raise ServiceError("bad_url") from None
    if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo:
        raise ServiceError("bad_url")
    return parsed


def _content_type(header: str) -> tuple[str, str | None]:
    kind, _, rest = header.partition(";")
    charset = None
    for part in rest.split(";"):
        name, _, value = part.strip().partition("=")
        if name.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip("\"'")
    return kind.strip().lower(), charset


class PageFetcher:
    """Fetches one page at a time, safely (see the module). `resolver` resolves a host; `transport`
    is httpx's (a test's fake); the limits are the module's unless a test says otherwise."""

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        max_seconds: float = MAX_SECONDS,
        max_bytes: int = MAX_BYTES,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._max_seconds = max_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    async def fetch(self, url: str) -> Fetched:
        """The page at `url`, or ServiceError of a kind (`common.ServiceError`)."""
        try:
            return await asyncio.wait_for(self._fetch(checked_url(url)), self._max_seconds)
        except TimeoutError:
            raise ServiceError("timeout") from None

    async def _fetch(self, url: httpx.URL) -> Fetched:
        async with httpx.AsyncClient(
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
            timeout=self._max_seconds,
        ) as client:
            for _ in range(self._max_redirects + 1):
                client.cookies.clear()  # a cookie a redirect set is not sent on
                fetched, location = await self._hop(client, url)
                if fetched is not None:
                    return fetched
                assert location is not None
                url = checked_url(str(url.join(location)))
            raise ServiceError("redirects")

    async def _address(self, url: httpx.URL) -> str:
        """The address to send the request for `url` to: the host itself when it is an address,
        else the first it resolves to. ServiceError("blocked_address") if any is not public, and
        "network" when the name resolves to nothing."""
        host = url.host
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            port = url.port or (443 if url.scheme == "https" else 80)
            try:
                addresses = await self._resolver(host, port)
            except OSError:
                raise ServiceError("network") from None
        if not addresses:
            raise ServiceError("network")
        if not all(is_public(address) for address in addresses):
            raise ServiceError("blocked_address")
        return addresses[0]

    async def _hop(
        self, client: httpx.AsyncClient, url: httpx.URL
    ) -> tuple[Fetched | None, str | None]:
        """One request: the page, or (None, the address a redirect points to)."""
        address = await self._address(url)
        default_port = 443 if url.scheme == "https" else 80
        host_header = url.host if url.port in (None, default_port) else f"{url.host}:{url.port}"
        headers = {**_HEADERS, "Host": host_header}
        extensions = {"sni_hostname": url.host} if url.scheme == "https" else {}
        pinned = url.copy_with(host=address)
        try:
            async with client.stream("GET", pinned, headers=headers, extensions=extensions) as reply:
                if reply.status_code in _REDIRECTS:
                    location = reply.headers.get("location")
                    if not location:
                        raise ServiceError("http_error")
                    return None, location
                if reply.status_code >= 400:
                    raise ServiceError("http_error")
                kind, charset = _content_type(reply.headers.get("content-type", ""))
                if kind not in (*PAGE_TYPES, PDF_TYPE):
                    raise ServiceError("unsupported_type")
                declared = reply.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self._max_bytes:
                    raise ServiceError("too_large")
                body = bytearray()
                async for chunk in reply.aiter_bytes():
                    body += chunk
                    if len(body) > self._max_bytes:
                        raise ServiceError("too_large")
        except httpx.TimeoutException:
            raise ServiceError("timeout") from None
        except (httpx.TransportError, httpx.DecodingError):
            raise ServiceError("network") from None
        return Fetched(str(url), kind, charset, bytes(body)), None


def decoded(fetched: Fetched) -> str:
    """The text of a page or of plain text: by the charset the server named, else the one the page
    names in its first bytes, else UTF-8 (anything that does not decode becomes U+FFFD)."""
    charset = fetched.charset
    if charset is None and fetched.content_type == "text/html":
        head = fetched.body[:2048].decode("ascii", "ignore").lower()
        at = head.find("charset=")
        if at >= 0:
            name = head[at + len("charset=") :].lstrip("\"' ")
            length = next((i for i, c in enumerate(name) if not (c.isalnum() or c in "-_")), len(name))
            charset = name[: min(length, 40)] or None
    for name in (charset, "utf-8"):
        if not name:
            continue
        try:
            return fetched.body.decode(name, "replace")
        except LookupError:
            continue
    return fetched.body.decode("utf-8", "replace")
