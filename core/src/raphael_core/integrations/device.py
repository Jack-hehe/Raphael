"""Signing in to a plugin with the device flow (RFC 8628), which GitHub's own MCP server needs.

The server takes no dynamic client registration, and a login with a secret would put the secret in
a program anyone can open. The device flow has none: Raphael asks the service for a short code, the
user types it once at the service's own page and agrees, and Raphael collects the token meanwhile by
asking the token endpoint at the interval the service gave.

- `device_login` does the whole of it and answers the access token. `on_code` is told the code as
  soon as it is issued, for the window to show. A cancellation ends the wait at once.
- `BearerAuth` is the `auth` of the HTTP client of a job: the stored token as a bearer. It is used
  instead of the SDK's `OAuthClientProvider`, which would look for a registration endpoint that
  GitHub does not have. A 401 is the failure `auth` (the token was taken back): the window offers
  the login again.

Nothing here logs a token, a device code or a user code.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx2

from .catalog import DeviceLogin
from .client import USER_AGENT, McpFailure
from .oauth import VaultTokenStorage

logger = logging.getLogger(__name__)

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
# RFC 8628: the interval is five seconds when the service does not say, and `slow_down` adds five.
DEFAULT_INTERVAL_S = 5
SLOW_DOWN_S = 5
DEFAULT_EXPIRES_S = 900
# The longest a code is waited for, whatever the service says: the call of the window to the core
# waits a little longer than this (core_process.rs `timeout_for`), so the core ends the login first.
MAX_EXPIRES_S = 900
TOTAL_S = MAX_EXPIRES_S + 30
REQUEST_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class DeviceCode:
    """What the user needs: the code to type, and the page to type it at."""

    user_code: str
    verification_uri: str
    expires_in: int

    def __repr__(self) -> str:  # a stray log line must not carry the code
        return "DeviceCode()"


OnCode = Callable[[DeviceCode], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


def _number(value: Any, default: int) -> int:
    return value if type(value) is int and 0 < value <= 3600 else default


async def _post(http: httpx2.AsyncClient, url: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        reply = await http.post(url, data=data)
        body = reply.json()
    except httpx2.TimeoutException:
        raise McpFailure("timeout") from None
    except httpx2.HTTPError:
        raise McpFailure("network") from None
    except ValueError:
        raise McpFailure("protocol") from None
    if not isinstance(body, dict):
        raise McpFailure("protocol")
    return body


async def device_login(
    login: DeviceLogin,
    on_code: OnCode,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    sleep: Sleep = asyncio.sleep,
) -> str:
    """Ask for a code, hand it to `on_code`, wait for the user and answer the access token.
    McpFailure of a kind: `denied` (the user refused), `timeout` (the code ran out), `auth` (the
    service turned the app away), `network`, `protocol`."""
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    async with httpx2.AsyncClient(
        headers=headers, timeout=REQUEST_TIMEOUT_S, follow_redirects=False, transport=transport
    ) as http:
        issued = await _post(http, login.device_endpoint, {"client_id": login.client_id, "scope": " ".join(login.scopes)})
        device_code, user_code, uri = issued.get("device_code"), issued.get("user_code"), issued.get("verification_uri")
        if not (isinstance(device_code, str) and device_code and isinstance(user_code, str) and user_code):
            raise McpFailure("auth" if "error" in issued else "protocol")
        if not isinstance(uri, str) or urlsplit(uri).scheme != "https":
            raise McpFailure("protocol")  # the window opens this address: https only
        expires_in = min(_number(issued.get("expires_in"), DEFAULT_EXPIRES_S), MAX_EXPIRES_S)
        interval = _number(issued.get("interval"), DEFAULT_INTERVAL_S)
        await on_code(DeviceCode(user_code, uri, expires_in))
        waited = 0
        while True:
            if waited + interval > expires_in:
                raise McpFailure("timeout")
            await sleep(interval)
            waited += interval
            answer = await _post(
                http,
                login.token_endpoint,
                {"client_id": login.client_id, "device_code": device_code, "grant_type": GRANT_TYPE},
            )
            token = answer.get("access_token")
            if isinstance(token, str) and token:
                return token
            error = answer.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += SLOW_DOWN_S
                continue
            if error == "expired_token":
                raise McpFailure("timeout")
            if error == "access_denied":
                raise McpFailure("denied")
            raise McpFailure("auth")


class BearerAuth(httpx2.Auth):
    """The stored token as a bearer on every request of a job. A 401 ends the job with `auth`."""

    def __init__(self, storage: VaultTokenStorage) -> None:
        self._storage = storage

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        tokens = await self._storage.get_tokens()
        if tokens is None:
            raise McpFailure("auth")
        request.headers["Authorization"] = f"Bearer {tokens.access_token}"
        response = yield request
        if response.status_code == 401:
            raise McpFailure("auth")
