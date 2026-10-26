"""An automatic Google sign-in and a recorded Gmail and Calendar, for Guardian and offline tests.

Only the outside world is replaced: the owner in the browser and Google's servers. The core's own
sign-in runs as it does for the owner (a loopback listener, PKCE, the `state` it checks, the code
exchanged at the token endpoint), and its Gmail and Calendar calls cross the connector's real HTTP
boundary (`connectors.http.PrivateHttp`). The fixture checks what Google would: the code it issued,
once, for that redirect, with the verifier of that challenge, and a bearer token it issued. Each API
call becomes a test-mode receipt of digests, never values; nothing is sent anywhere.

`mode` is what the owner does in the browser: "approve", "deny", "hold" (never finishes) or
"forged" (a callback with another `state`). `scopes` is what the owner grants.
"""

import asyncio
import base64
import contextlib
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx

from ..connectors.google import AUTH, CALENDAR, GMAIL, REVOKE, TOKEN, scopes_for
from .guardian_registry import digest

CLIENT_ID = "guardian-desktop.apps.googleusercontent.invalid"
GRANTS = {
    "read": scopes_for("read", "read"),
    "calendar-only": scopes_for("read", "read")[1:],
}


class GoogleFixture:
    def __init__(self, nonce: str, *, mode: str = "approve", scopes: str = "read") -> None:
        self.nonce, self.mode, self.scopes = nonce, mode, scopes
        self.mail_code = "MC-" + secrets.token_hex(4)
        self.receipts: list[dict] = []
        self.sign_ins: list[dict] = []
        self.opened = 0
        # Every value that must never leave the sign-in: the URL and its parts, codes and tokens.
        self.secrets: set[str] = set()
        self._codes: dict[str, tuple[str, str]] = {}
        self._access: set[str] = set()
        self._refresh: set[str] = set()
        self._callbacks: list[str] = []
        self.transport = httpx.MockTransport(self._respond)

    # --- the owner in the browser -----------------------------------------------------------------

    async def browser(self, url: str) -> None:
        self.opened += 1
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        state, challenge, redirect = query.get("state", ""), query.get("code_challenge", ""), query.get("redirect_uri", "")
        self.secrets.update(value for value in (url, state, challenge) if value)
        self.sign_ins.append({"url_digest": digest(url), "authorization_endpoint": url.split("?", 1)[0] == AUTH,
                              "client": query.get("client_id") == CLIENT_ID, "pkce": query.get("code_challenge_method") == "S256",
                              "state_present": bool(state), "loopback": redirect.startswith("http://127.0.0.1:")})
        if self.mode == "hold":
            return
        if self.mode == "deny":
            callback = redirect + "?" + urlencode({"state": state, "error": "access_denied"})
        else:
            code = secrets.token_urlsafe(24)
            self.secrets.add(code)
            self._codes[code] = (challenge, redirect)
            callback = redirect + "?" + urlencode({"state": "forged-" + secrets.token_hex(8) if self.mode == "forged" else state,
                                                   "code": code})
        self._callbacks.append(callback)
        await self._open(callback)

    async def replay(self) -> int | None:
        """Send the last callback again, as a reloaded tab would: its HTTP status, or None when
        nothing listens there any more."""
        return await self._open(self._callbacks[-1]) if self._callbacks else None

    @staticmethod
    async def _open(url: str) -> int | None:
        """The browser tab's request, made outside the core's HTTP clients and their logs, as a real
        browser's is."""
        parts = urlsplit(url)
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(parts.hostname, parts.port), 5)
        except (OSError, TimeoutError):
            return None
        try:
            writer.write(f"GET {parts.path}?{parts.query} HTTP/1.1\r\nHost: {parts.netloc}\r\n"
                         "Connection: close\r\n\r\n".encode("ascii"))
            await writer.drain()
            status = await asyncio.wait_for(reader.readline(), 5)
            return int(status.split()[1]) if status else None
        except (OSError, TimeoutError, ValueError, IndexError):
            return None
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    # --- Google's servers -------------------------------------------------------------------------

    def _respond(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?", 1)[0]
        if url == TOKEN:
            return self._token(parse_qs(request.content.decode()))
        if url == REVOKE:
            return httpx.Response(200)
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        if bearer not in self._access:
            return httpx.Response(401, json={"error": "invalid_token"})
        params = dict(request.url.params)
        if url == GMAIL + "/messages":
            self._receipt("gmail_search", params)
            found = self.nonce in params.get("q", "")
            return httpx.Response(200, json={"messages": [{"id": "msg-" + self.nonce, "threadId": "thr-" + self.nonce}]
                                             if found else [], "resultSizeEstimate": int(found)})
        if url == GMAIL + "/messages/msg-" + self.nonce:
            self._receipt("gmail_get_message", {"id": "msg-" + self.nonce, **params})
            return httpx.Response(200, json={"id": "msg-" + self.nonce, "snippet": f"本次確認碼是 {self.mail_code}。",
                                             "payload": {"headers": [{"name": "Subject", "value": f"Guardian 測試信 {self.nonce}"},
                                                                     {"name": "From", "value": "guardian@example.invalid"}]}})
        if url.startswith(CALENDAR):
            self._receipt("calendar_list_events", params)
            return httpx.Response(200, json={"timeZone": "Asia/Taipei", "items": []})
        self._receipt("unexpected:" + request.method, {"path": request.url.path})
        return httpx.Response(404, json={"error": "not_found"})

    def _token(self, form: dict[str, list[str]]) -> httpx.Response:
        grant = form.get("grant_type", [""])[0]
        if form.get("client_id") != [CLIENT_ID]:
            return httpx.Response(401, json={"error": "invalid_client"})
        if grant == "authorization_code":
            issued = self._codes.pop(form.get("code", [""])[0], None)
            verifier = form.get("code_verifier", [""])[0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            if issued is None or issued != (challenge, form.get("redirect_uri", [""])[0]):
                return httpx.Response(400, json={"error": "invalid_grant"})
            refresh = secrets.token_urlsafe(24)
            self._refresh.add(refresh)
            self.secrets.update((verifier, refresh))
        elif grant == "refresh_token" and form.get("refresh_token", [""])[0] in self._refresh:
            pass
        else:
            return httpx.Response(400, json={"error": "invalid_grant"})
        access = secrets.token_urlsafe(24)
        self._access.add(access)
        self.secrets.add(access)
        body = {"access_token": access, "expires_in": 3600, "token_type": "Bearer", "scope": " ".join(GRANTS[self.scopes])}
        if grant == "authorization_code":
            body["refresh_token"] = refresh
        return httpx.Response(200, json=body)

    def expire_access(self) -> None:
        """Google stops taking the tokens it issued: the next call needs a new sign-in."""
        self._access.clear()
        self._refresh.clear()

    def _receipt(self, operation: str, params: dict) -> None:
        self.receipts.append({"test_mode": True, "operation": operation, "argument_digest": digest(params),
                              "receipt_id": uuid4().hex, "received_at": time.monotonic()})

    def reads(self) -> list[str]:
        return [receipt["operation"] for receipt in self.receipts if receipt["operation"] in ("gmail_search", "gmail_get_message")]
