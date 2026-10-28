"""Private Desktop OAuth with loopback + PKCE and a small scoped API surface."""

import asyncio
import base64
import contextlib
import hashlib
import secrets
import time
from email.message import EmailMessage
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .http import ConnectorFailure, PrivateHttp

PREFIX = "connectors:google:"
SCOPE = "https://www.googleapis.com/auth/"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
REVOKE = "https://oauth2.googleapis.com/revoke"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars"


def scopes_for(gmail: str, calendar: str) -> list[str]:
    if gmail not in ("read", "write") or calendar not in ("read", "write"):
        raise ValueError("Scope presets must be read or write")
    return [SCOPE + ("gmail.readonly" if gmail == "read" else "gmail.modify"),
            SCOPE + ("calendar.events.readonly" if calendar == "read" else "calendar.events")]


class Loopback:
    """Take exactly one callback request on a random IPv4 loopback port, then close."""

    def __init__(self, state: str, timeout: float = 120) -> None:
        self.state = state
        self.timeout = timeout
        self.done = asyncio.get_running_loop().create_future()
        self.server = None
        self.redirect_uri = ""
        self.writers = set()
        self.claimed = False

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0, limit=8192)
        self.redirect_uri = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/callback"
        return self

    async def __aexit__(self, *args):
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.writers):
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if not self.done.done():
            self.done.cancel()
        elif not self.done.cancelled():
            self.done.exception()

    async def wait(self) -> str:
        try:
            async with asyncio.timeout(self.timeout):
                return await self.done
        except TimeoutError:
            raise ConnectorFailure("timeout") from None

    async def _serve(self, reader, writer):
        self.writers.add(writer)
        try:
            if self.claimed:
                return
            self.claimed = True
            self.server.close()
            async with asyncio.timeout(5):
                head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            lines = head.split("\r\n")
            method, target, _ = lines[0].split(" ")
            headers = dict(line.lower().split(":", 1) for line in lines[1:] if ":" in line)
            parsed = urlsplit(target)
            query = parse_qs(parsed.query)
            expected_host = urlsplit(self.redirect_uri).netloc
            if (method != "GET" or parsed.path != "/callback" or parsed.netloc
                    or headers.get("host", "").strip() != expected_host
                    or len(query.get("state", [])) != 1
                    or not secrets.compare_digest(query["state"][0], self.state)):
                raise ConnectorFailure("state")
            if "error" in query:
                raise ConnectorFailure("denied")
            if len(query.get("code", [])) != 1:
                raise ConnectorFailure("protocol")
            self.done.set_result(query["code"][0])
            body = b"Sign-in complete. You can close this tab and return to Raphael."
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\n"
                         b"Connection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        except Exception as error:
            if not self.done.done():
                self.done.set_exception(error if isinstance(error, ConnectorFailure) else ConnectorFailure("protocol"))
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            self.writers.discard(writer)


class GoogleClient:
    def __init__(self, vault, *, transport=None, now=time.monotonic, listener=Loopback) -> None:
        self.vault = vault
        self.http = PrivateHttp(transport=transport)
        self.now = now
        self.listener = listener
        self._access = ""
        self._expires = 0.0
        self.scopes: list[str] | None = None
        self._refresh_lock = asyncio.Lock()

    def forget_access(self) -> None:
        self._access, self._expires, self.scopes = "", 0, None

    def _client(self) -> dict:
        client_id = self.vault.get(PREFIX + "client_id")
        if not client_id:
            raise ConnectorFailure("not_configured")
        data = {"client_id": client_id}
        client_secret = self.vault.get(PREFIX + "client_secret")
        if client_secret:
            data["client_secret"] = client_secret
        return data

    def _accept(self, data: dict, *, require_refresh: bool = False) -> None:
        access = data.get("access_token")
        expires = data.get("expires_in")
        if (not isinstance(access, str) or not access or type(expires) not in (int, float)
                or expires <= 0 or data.get("token_type", "Bearer").lower() != "bearer"):
            raise ConnectorFailure("protocol")
        refresh = data.get("refresh_token")
        if require_refresh and (not isinstance(refresh, str) or not refresh):
            raise ConnectorFailure("missing_refresh_token")
        if refresh:
            self.vault.set(PREFIX + "refresh_token", refresh)
        self._access = access
        self._expires = self.now() + max(0, expires - 60)
        if isinstance(data.get("scope"), str):
            self.scopes = data["scope"].split()

    async def connect(self, scopes: list[str], open_browser, *, timeout: float = 120) -> list[str]:
        client = self._client()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        async with self.listener(state, timeout) as listener:
            params = {"client_id": client["client_id"], "redirect_uri": listener.redirect_uri,
                      "response_type": "code", "scope": " ".join(scopes), "state": state,
                      "code_challenge": challenge, "code_challenge_method": "S256",
                      "access_type": "offline", "prompt": "consent"}
            await open_browser(AUTH + "?" + urlencode(params))
            code = await listener.wait()
            data = await self.http.json("POST", TOKEN, data={**client, "grant_type": "authorization_code",
                                       "code": code, "code_verifier": verifier, "redirect_uri": listener.redirect_uri})
        # Missing granted scopes is not evidence of a grant. Fail closed until re-consent.
        if not isinstance(data.get("scope"), str):
            raise ConnectorFailure("missing_scopes")
        self._accept(data, require_refresh=True)
        return self.scopes or []

    async def access_token(self) -> str:
        async with self._refresh_lock:
            if self._access and self.now() < self._expires:
                return self._access
            refresh = self.vault.get(PREFIX + "refresh_token")
            if not refresh:
                raise ConnectorFailure("reconnect")
            try:
                data = await self.http.json("POST", TOKEN, data={**self._client(), "grant_type": "refresh_token",
                                                               "refresh_token": refresh})
                self._accept(data)
            except ConnectorFailure:
                self.forget_access()
                raise
            return self._access

    async def revoke(self) -> None:
        token = self.vault.get(PREFIX + "refresh_token")
        if token:
            response = await self.http.response("POST", REVOKE, data={"token": token})
            if response.status_code != 200:
                raise ConnectorFailure("revocation_failed", response.status_code)
        self.forget_access()
        for name in ("client_id", "client_secret", "refresh_token"):
            self.vault.delete(PREFIX + name)

    async def call(self, tool: str, args: dict) -> dict:
        token = await self.access_token()
        method, url, options = api_request(tool, args)
        return await self.http.json(method, url, headers={"Authorization": "Bearer " + token}, **options)


def _mail(args: dict) -> dict:
    message = EmailMessage()
    try:
        message["To"], message["Subject"] = args["to"], args["subject"]
        message.set_content(args["body"])
    except (ValueError, TypeError, KeyError):
        raise ConnectorFailure("arguments") from None
    return {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")}


def api_request(tool: str, args: dict) -> tuple[str, str, dict]:
    if tool == "gmail_search":
        return "GET", GMAIL + "/messages", {"params": {k: v for k, v in args.items() if k in ("q", "pageToken", "maxResults")}}
    if tool == "gmail_get_message":
        return "GET", GMAIL + "/messages/" + quote(args["id"], safe=""), {"params": {"format": "full"}}
    if tool == "gmail_create_draft":
        return "POST", GMAIL + "/drafts", {"json": {"message": _mail(args)}}
    if tool == "gmail_send":
        return "POST", GMAIL + "/messages/send", {"json": _mail(args)}
    if tool == "gmail_modify_labels":
        return "POST", GMAIL + "/messages/" + quote(args["id"], safe="") + "/modify", {
            "json": {k: v for k, v in args.items() if k in ("addLabelIds", "removeLabelIds")}}
    calendar = quote(args.get("calendarId", "primary"), safe="")
    url = CALENDAR + "/" + calendar + "/events"
    if tool == "calendar_list_events":
        return "GET", url, {"params": {k: v for k, v in args.items() if k in (
            "q", "timeMin", "timeMax", "pageToken", "maxResults", "singleEvents", "orderBy")}}
    if tool == "calendar_create_event":
        return "POST", url, {"json": {k: v for k, v in args.items() if k in (
            "summary", "description", "start", "end", "attendees", "location")}}
    raise ConnectorFailure("blocked")


def _schema(properties: dict, required=()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TEXT = {"type": "string"}
MAIL = {"to": TEXT, "subject": TEXT, "body": TEXT}
TOOLS = {
    "gmail_search": ("Search or list Gmail messages.", _schema({"q": TEXT, "pageToken": TEXT, "maxResults": {"type": "integer", "minimum": 1, "maximum": 500}})),
    "gmail_get_message": ("Read a Gmail message.", _schema({"id": TEXT}, ["id"])),
    "gmail_create_draft": ("Create a Gmail draft without sending it.", _schema(MAIL, MAIL)),
    "gmail_send": ("Send email. Requires the owner's explicit approval.", _schema(MAIL, MAIL)),
    "gmail_modify_labels": ("Change message labels.", _schema({"id": TEXT, "addLabelIds": {"type": "array", "items": TEXT},
                                                              "removeLabelIds": {"type": "array", "items": TEXT}}, ["id"])),
    "calendar_list_events": ("List Google Calendar events.", _schema({"calendarId": TEXT, "q": TEXT, "timeMin": TEXT,
        "timeMax": TEXT, "pageToken": TEXT, "maxResults": {"type": "integer", "minimum": 1, "maximum": 2500},
        "singleEvents": {"type": "boolean"}, "orderBy": TEXT})),
    "calendar_create_event": ("Create a Google Calendar event. Requires the owner's explicit approval.", _schema({
        "calendarId": TEXT, "summary": TEXT, "description": TEXT, "location": TEXT,
        "start": {"type": "object"}, "end": {"type": "object"}, "attendees": {"type": "array", "items": {"type": "object"}}},
        ["summary", "start", "end"])),
}
READ_TOOLS = {"gmail_search", "gmail_get_message", "calendar_list_events"}


def permitted(tool: str, scopes: list[str], draft_only: bool) -> bool:
    if tool not in TOOLS or (tool == "gmail_send" and draft_only):
        return False
    if tool.startswith("gmail_"):
        return SCOPE + "gmail.modify" in scopes or (tool in READ_TOOLS and SCOPE + "gmail.readonly" in scopes)
    return SCOPE + "calendar.events" in scopes or (tool in READ_TOOLS and SCOPE + "calendar.events.readonly" in scopes)
