"""Signing in to a plugin with a login in the browser (OAuth), and keeping what that gives.

The SDK (`mcp.client.auth`) does the protocol: it finds the server's authorization server, registers
Raphael there (a public client, RFC 7591), sends the user to the login with PKCE and a `state`, swaps
the code for tokens and refreshes them. This module supplies what it needs around that:

- `VaultTokenStorage` keeps the tokens and the client registration of one server in Windows
  Credential Manager (`vault`), cut over several credentials when they are long. They are never in a
  file, a log, the database or anything that goes to the window.
- `CallbackListener` is the one-shot listener on this computer that the login ends at: a random port
  on 127.0.0.1, one request, a `state` that has to match, a time limit, and a small page that says the
  tab can be closed.
- `OAuthAccess` is what a job signs in with. A *login* (the user pressed 「加入」 or 「重新登入」) opens
  the browser and waits for the user. Every other job is *silent*: it uses the stored tokens, refreshes
  them when they ran out, and when that cannot be done it fails with the kind `auth`. It never opens
  a browser by itself, so a dictation, a voice turn or a task in the background can never pop up a
  login.
- `revoke` tells the server to forget a token (RFC 7009), best effort, when the plugin is removed.

The SDK takes over a second to import, so it is imported when it is first needed. Nothing here logs a
token, a code, a state, an address or a client registration.
"""

import asyncio
import contextlib
import functools
import html
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from .client import USER_AGENT, McpFailure
from .config import checked_url
from .vault import SecretVault, delete_long, load_long, secret_name, store_long

if TYPE_CHECKING:
    import httpx2
    from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthToken

logger = logging.getLogger(__name__)

# How long a person is given to finish the login in the browser.
LOGIN_TIMEOUT_S = 300.0
# How long one connection to the listener may take to send its request.
_REQUEST_READ_S = 5.0
_MAX_REQUEST_BYTES = 8192
CLIENT_NAME = "Raphael"

OpenBrowser = Callable[[str], Awaitable[None]]


async def open_in_browser(url: str) -> None:
    """Open the login page of the server in the default browser. https only (the address came from
    the server's own metadata, and a browser follows any scheme it is given)."""
    import webbrowser

    if urlsplit(url).scheme != "https":
        raise McpFailure("protocol")
    if not await asyncio.to_thread(webbrowser.open, url):
        raise McpFailure("error")


# --- what is kept ----------------------------------------------------------------------------


class VaultTokenStorage:
    """The SDK's `TokenStorage`, over the vault: the tokens and the client registration of one
    server. `expires_at` is when the access token that was read, or last written, runs out (the SDK
    keeps only a lifetime, which means nothing to a job that starts later)."""

    def __init__(self, vault: SecretVault, server_id: str) -> None:
        self._vault = vault
        self._tokens_name = secret_name(server_id, "oauth", "tokens")
        self._client_name = secret_name(server_id, "oauth", "client")
        self.expires_at: float | None = None

    def has_tokens(self) -> bool:
        return load_long(self._vault, self._tokens_name) is not None

    def forget(self) -> None:
        delete_long(self._vault, self._tokens_name)
        delete_long(self._vault, self._client_name)
        self.expires_at = None

    async def get_tokens(self) -> "OAuthToken | None":
        from mcp.shared.auth import OAuthToken

        text = load_long(self._vault, self._tokens_name)
        if text is None:
            return None
        try:
            record = json.loads(text)
            expires_at = record.get("expires_at")
            self.expires_at = float(expires_at) if isinstance(expires_at, int | float) else None
            return OAuthToken.model_validate(record["token"])
        except (ValueError, KeyError, TypeError, AttributeError):
            return None  # not what was written: as if there were none, and the login is asked again

    async def set_tokens(self, tokens: "OAuthToken") -> None:
        self.expires_at = None if tokens.expires_in is None else time.time() + tokens.expires_in
        record = {"token": json.loads(tokens.model_dump_json(exclude_none=True)), "expires_at": self.expires_at}
        store_long(self._vault, self._tokens_name, json.dumps(record))

    async def get_client_info(self) -> "OAuthClientInformationFull | None":
        from mcp.shared.auth import OAuthClientInformationFull

        text = load_long(self._vault, self._client_name)
        if text is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate_json(text)
        except ValueError:
            return None

    async def set_client_info(self, client_info: "OAuthClientInformationFull") -> None:
        store_long(self._vault, self._client_name, client_info.model_dump_json(exclude_none=True))

    def read_for_revocation(self) -> tuple[str | None, str | None, str | None]:
        """`(refresh token, access token, client id)` as kept, for `revoke`; None for what is not."""
        try:
            token = json.loads(load_long(self._vault, self._tokens_name) or "null")["token"]
            client = json.loads(load_long(self._vault, self._client_name) or "null")
            return token.get("refresh_token"), token.get("access_token"), client.get("client_id")
        except (ValueError, KeyError, TypeError, AttributeError):
            return None, None, None


# --- the end of the login --------------------------------------------------------------------

_PAGE = {
    "en": ("You are signed in", "You can close this tab and go back to Raphael."),
    "zh": ("登入完成", "你可以關閉這個分頁，回到拉斐爾。"),
    "ja": ("ログインが完了しました", "このタブを閉じて、Raphael に戻ってください。"),
}
_PAGE_FAILED = {
    "en": ("Not signed in", "The login was not finished. You can close this tab and try again in Raphael."),
    "zh": ("沒有登入", "登入沒有完成。你可以關閉這個分頁，回到拉斐爾再試一次。"),
    "ja": ("ログインできませんでした", "ログインが完了しませんでした。このタブを閉じて、Raphael でもう一度お試しください。"),
}


def _page(accept_language: str, *, ok: bool) -> bytes:
    lang = "zh" if accept_language.lower().startswith("zh") else "ja" if accept_language.lower().startswith("ja") else "en"
    title, text = (_PAGE if ok else _PAGE_FAILED)[lang]
    body = (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><style>'
        "body{font:16px/1.6 system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;margin:0;"
        "background:#f7f9fd;color:#1b2942}main{text-align:center;padding:24px}h1{font-size:20px;margin:0 0 8px}"
        "p{margin:0;color:#465873}@media(prefers-color-scheme:dark){body{background:#182133;color:#edf3fd}"
        "p{color:#c0cee3}}</style></head><body><main>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p></main></body></html>"
    )
    return body.encode("utf-8")


class CallbackListener:
    """The address the login ends at: `http://127.0.0.1:<port>/callback`, on a port the system picks.

    It answers requests for `/callback` only, from a `Host` that is this very address (a page on
    the web cannot reach it through a name it controls). A request whose `state` is not the one the
    login was started with is turned away and the wait goes on; the first one that is right ends the
    wait, and the listener takes no more. `wait` ends with `TimeoutError` after `timeout_s`.
    """

    def __init__(self, *, timeout_s: float = LOGIN_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s
        self._server: asyncio.Server | None = None
        self._port = 0
        self._state: str | None = None
        self._done: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self._port}/callback"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self._port = self._server.sockets[0].getsockname()[1]

    def expect(self, state: str) -> None:
        """The `state` the login was started with."""
        self._state = state

    async def wait(self) -> "AuthorizationCodeResult":
        from mcp.shared.auth import AuthorizationCodeResult

        async with asyncio.timeout(self._timeout_s):
            code, state, issuer = await self._done
        return AuthorizationCodeResult(code=code, state=state, iss=issuer)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        if not self._done.done():
            self._done.cancel()
        elif not self._done.cancelled():
            self._done.exception()  # read, so that an unread failure is not reported later

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                async with asyncio.timeout(_REQUEST_READ_S):
                    head = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                return
            if len(head) > _MAX_REQUEST_BYTES:
                return
            status, body = self._answer(head)
            payload = (
                f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
            ).encode() + body
            writer.write(payload)
            with contextlib.suppress(ConnectionError):
                await writer.drain()
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    def _answer(self, head: bytes) -> tuple[str, bytes]:
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        language = headers.get("accept-language", "")
        if len(parts) != 3 or parts[0] != "GET":
            return "405 Method Not Allowed", b""
        target = urlsplit(parts[1])
        if target.path != "/callback" or headers.get("host") != f"127.0.0.1:{self._port}":
            return "404 Not Found", b""
        query = {key: values[0] for key, values in parse_qs(target.query).items()}
        state = query.get("state")
        if self._state is None or state != self._state or self._done.done():
            return "400 Bad Request", _page(language, ok=False)
        if "error" in query:
            denied = query["error"] == "access_denied"
            self._done.set_exception(McpFailure("denied" if denied else "auth"))
            return "200 OK", _page(language, ok=False)
        code = query.get("code")
        if not code:
            return "400 Bad Request", _page(language, ok=False)
        self._done.set_result((code, state, query.get("iss")))
        return "200 OK", _page(language, ok=True)


# --- what a job signs in with ----------------------------------------------------------------


@functools.cache
def _provider_type() -> type:
    from mcp.client.auth.oauth2 import OAuthClientProvider

    class Provider(OAuthClientProvider):
        """The SDK's provider, told when the stored access token runs out. It reads the tokens
        from storage when a job starts, and without this would take an expired one for good."""

        async def _initialize(self) -> None:
            await super()._initialize()
            if self.context.current_tokens is not None:
                self.context.token_expiry_time = getattr(self.context.storage, "expires_at", None)

        async def _auth_flow(self, request):  # type: ignore[no-untyped-def]
            """The requests of the login itself (discovery, registration, token, refresh) are built
            by the SDK without the client's headers, so they say who is asking here."""
            flow = super()._auth_flow(request)
            try:
                outgoing = await flow.__anext__()
                while True:
                    outgoing.headers.setdefault("User-Agent", USER_AGENT)
                    response = yield outgoing
                    outgoing = await flow.asend(response)
            except StopAsyncIteration:
                return
            finally:
                await flow.aclose()

    return Provider


def _metadata(redirect_uri: str) -> Any:
    from mcp.shared.auth import OAuthClientMetadata

    return OAuthClientMetadata(
        client_name=CLIENT_NAME,
        redirect_uris=[redirect_uri],  # type: ignore[list-item]
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


async def _no_browser(_url: str) -> None:
    raise McpFailure("auth")


async def _no_callback() -> Any:
    raise McpFailure("auth")


@dataclass
class OAuthAccess:
    """How one job signs in to a server: with the stored tokens, or, when `open_browser` is given,
    with a login in the browser first. After a login, `revocation_url` is where the server takes
    a token back."""

    storage: VaultTokenStorage
    open_browser: OpenBrowser | None = None
    login_timeout_s: float = LOGIN_TIMEOUT_S
    revocation_url: str = ""

    @asynccontextmanager
    async def auth(self, server_url: str) -> AsyncIterator["httpx2.Auth"]:
        """The `auth` of the job's HTTP client."""
        if self.open_browser is None:
            # A placeholder address: it would be registered only by a login, which this is not.
            yield _provider_type()(
                server_url=server_url,
                client_metadata=_metadata("http://127.0.0.1:1/callback"),
                storage=self.storage,
                redirect_handler=_no_browser,
                callback_handler=_no_callback,
            )
            return
        listener = CallbackListener(timeout_s=self.login_timeout_s)
        await listener.start()
        open_browser = self.open_browser

        async def redirect(url: str) -> None:
            state = parse_qs(urlsplit(url).query).get("state", [""])[0]
            listener.expect(state)
            await open_browser(url)

        provider = _provider_type()(
            server_url=server_url,
            client_metadata=_metadata(listener.redirect_uri),
            storage=self.storage,
            redirect_handler=redirect,
            callback_handler=listener.wait,
        )
        try:
            yield provider
        finally:
            await listener.close()
            metadata = getattr(provider.context, "oauth_metadata", None)
            endpoint = getattr(metadata, "revocation_endpoint", None)
            if endpoint is not None:
                with contextlib.suppress(ValueError):
                    self.revocation_url = checked_url(str(endpoint))


# --- taking the tokens back ------------------------------------------------------------------


async def revoke(storage: VaultTokenStorage, revocation_url: str, *, timeout_s: float = 10.0) -> bool:
    """Ask the server to forget the tokens (RFC 7009): the refresh token, which ends the grant, and
    the access token. Best effort; True if the server took a request. Never raises."""
    import httpx2  # not httpx: this one's log lines are silenced, and name the address

    refresh, access, client_id = storage.read_for_revocation()
    if not revocation_url or client_id is None or (refresh is None and access is None):
        return False
    try:
        checked_url(revocation_url)
        done = False
        async with httpx2.AsyncClient(timeout=timeout_s, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as http:
            for token, hint in ((refresh, "refresh_token"), (access, "access_token")):
                if token is None:
                    continue
                reply = await http.post(
                    revocation_url, data={"token": token, "token_type_hint": hint, "client_id": client_id}
                )
                done = done or reply.is_success
        return done
    except Exception as exc:  # the message of an HTTP error holds the address
        logger.warning("Could not revoke a plugin's token: %s", type(exc).__name__)
        return False
