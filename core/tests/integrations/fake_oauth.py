"""A service that signs people in, for the tests: an authorization server and an MCP server in one,
on a port of this computer, with the endpoints the SDK looks for (RFC 9728 and 8414 metadata,
dynamic client registration, authorize, token with PKCE, refresh, revoke) and an MCP endpoint that
answers only to a bearer token it handed out. No real network, no real login.

`browser` is the "browser" of a test: it does what a person does (opens the authorization address
and follows the redirect to the listener on this computer), so a test can run a whole login.
"""

import asyncio
import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2

TOOLS = [
    {
        "name": "search",
        "description": "Search the workspace.",
        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "create_page",
        "description": "Create a page.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}},
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "delete_page",
        "description": "Delete a page.",
        "inputSchema": {"type": "object"},
        "annotations": {"destructiveHint": True},
    },
]


class FakeService:
    def __init__(self, *, access_ttl: int = 3600, padding: int = 0, deny: bool = False) -> None:
        self.access_ttl = access_ttl
        self.padding = padding  # extra characters in each token, to make them long
        self.deny = deny
        self.server: asyncio.Server | None = None
        self.base = ""
        self.clients: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.access: set[str] = set()
        self.refresh: dict[str, str] = {}  # refresh token -> client id
        self.revoked: list[str] = []
        self.registrations = 0
        self.authorizations = 0
        self.refreshes = 0
        self.mcp_calls: list[str] = []  # the methods of the requests that were let in
        self.reject_refresh = False
        self.tools = list(TOOLS)
        self.agents: list[str] = []  # the User-Agent of every request

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    @property
    def url(self) -> str:
        return f"{self.base}/mcp"

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    # --- the browser of a test -----------------------------------------------------------------

    async def browser(self, url: str) -> None:
        async with httpx2.AsyncClient(follow_redirects=False, headers={"User-Agent": "Browser/1"}) as http:
            reply = await http.get(url)
            if reply.status_code in (301, 302, 303, 307):
                await http.get(reply.headers["location"])

    # --- http ----------------------------------------------------------------------------------

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ")
            headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:]) if k}
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            self.agents.append(headers.get("user-agent", ""))
            status, extra, payload = self.handle(method, target, headers, body)
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            head = f"HTTP/1.1 {status}\r\nContent-Length: {len(data)}\r\nConnection: close\r\n"
            for name, value in extra.items():
                head += f"{name}: {value}\r\n"
            writer.write(head.encode() + b"\r\n" + data)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    def handle(self, method: str, target: str, headers: dict, body: bytes):
        parts = urlsplit(target)
        path, query = parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}
        json_type = {"Content-Type": "application/json"}
        if path.startswith("/.well-known/oauth-protected-resource"):
            return "200 OK", json_type, {"resource": self.url, "authorization_servers": [self.base]}
        if path.startswith("/.well-known/oauth-authorization-server"):
            return "200 OK", json_type, {
                "issuer": self.base,
                "authorization_endpoint": f"{self.base}/authorize",
                "token_endpoint": f"{self.base}/token",
                "registration_endpoint": f"{self.base}/register",
                "revocation_endpoint": f"{self.base}/revoke",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["none"],
                "code_challenge_methods_supported": ["S256"],
            }
        if path == "/register" and method == "POST":
            self.registrations += 1
            asked = json.loads(body)
            client_id = f"client-{self.registrations}"
            self.clients[client_id] = asked
            return "201 Created", json_type, {**asked, "client_id": client_id, "token_endpoint_auth_method": "none"}
        if path == "/authorize":
            self.authorizations += 1
            client = self.clients.get(query.get("client_id", ""))
            if client is None or query.get("redirect_uri") not in client["redirect_uris"]:
                return "400 Bad Request", {}, b"bad client"
            if self.deny:
                back = {"error": "access_denied", "state": query["state"]}
            else:
                code = secrets.token_urlsafe(16)
                self.codes[code] = {
                    "challenge": query["code_challenge"],
                    "client": query["client_id"],
                    "redirect": query["redirect_uri"],
                }
                back = {"code": code, "state": query["state"]}
            return "302 Found", {"Location": f"{query['redirect_uri']}?{urlencode(back)}"}, b""
        if path == "/token" and method == "POST":
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            if form.get("grant_type") == "authorization_code":
                grant = self.codes.pop(form.get("code", ""), None)
                verifier = form.get("code_verifier", "")
                digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
                if grant is None or grant["challenge"] != digest or grant["redirect"] != form.get("redirect_uri"):
                    return "400 Bad Request", json_type, {"error": "invalid_grant"}
                return "200 OK", json_type, self._tokens(grant["client"])
            if form.get("grant_type") == "refresh_token":
                client = self.refresh.pop(form.get("refresh_token", ""), None)
                if client is None or self.reject_refresh:
                    return "400 Bad Request", json_type, {"error": "invalid_grant"}
                self.refreshes += 1
                return "200 OK", json_type, self._tokens(client)
            return "400 Bad Request", json_type, {"error": "unsupported_grant_type"}
        if path == "/revoke" and method == "POST":
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            self.revoked.append(form.get("token", ""))
            self.access.discard(form.get("token", ""))
            self.refresh.pop(form.get("token", ""), None)
            return "200 OK", {}, b""
        if path == "/mcp":
            return self.mcp(method, headers, body)
        return "404 Not Found", {}, b""

    def _tokens(self, client: str) -> dict:
        access = "at-" + secrets.token_urlsafe(8) + "x" * self.padding
        refresh = "rt-" + secrets.token_urlsafe(8) + "y" * self.padding
        self.access.add(access)
        self.refresh[refresh] = client
        return {"access_token": access, "token_type": "Bearer", "expires_in": self.access_ttl, "refresh_token": refresh}

    def expire_access_tokens(self) -> None:
        self.access.clear()

    def mcp(self, method: str, headers: dict, body: bytes):
        token = headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.access:
            challenge = f'Bearer resource_metadata="{self.base}/.well-known/oauth-protected-resource"'
            return "401 Unauthorized", {"WWW-Authenticate": challenge}, b""
        if method != "POST":
            return "405 Method Not Allowed", {}, b""
        message = json.loads(body)
        self.mcp_calls.append(message.get("method", "?"))
        if "id" not in message:  # a notification
            return "202 Accepted", {}, b""
        kind = message["method"]
        if kind == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"},
            }
        elif kind == "tools/list":
            result = {"tools": self.tools}
        elif kind == "tools/call":
            result = {"content": [{"type": "text", "text": f"did {message['params']['name']}"}], "isError": False}
        else:
            result = {}
        return "200 OK", {"Content-Type": "application/json"}, {"jsonrpc": "2.0", "id": message["id"], "result": result}
