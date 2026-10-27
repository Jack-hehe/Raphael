"""A recorded MCP server that answers only a caller holding the owner's key, for Guardian and offline tests.

Only the outside world is replaced: the server. The core's own path runs as it does for the owner: the
connection declares an Authorization header, the vault holds none until the owner has saved the key in
a card, and the connector (`integrations.client.McpConnector`, with this fixture as its opener) fails
the call with `missing_secret` until it does, then connects with the vault's value as the bearer. The
server checks what a real one would: the bearer is the key the owner typed, and nothing else opens it.
Each call becomes a receipt of digests, never values; nothing is sent anywhere.
"""

import secrets
from contextlib import asynccontextmanager
from types import SimpleNamespace

from ..integrations.client import McpFailure
from ..integrations.config import ServerConfig, tool_record
from .guardian_registry import digest

SERVER_ID = "guardian-weather"
SERVER_NAME = "天氣服務"
TOOL = "forecast"
SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}


class SecretServer:
    def __init__(self, nonce: str) -> None:
        # What the owner types into the card, and what only a caller holding it can learn.
        self.key = "gsk-" + secrets.token_hex(16)
        self.code = "FC-" + nonce[:8]
        self.receipts: list[dict] = []

    def config(self) -> ServerConfig:
        """The connection as the owner has it: on, declaring an Authorization header, its tools known,
        and no value for the header in the vault yet."""
        tool = tool_record(TOOL, "Tomorrow's forecast of a city, with the forecast code", SCHEMA, True, False)
        return ServerConfig(SERVER_ID, SERVER_NAME, "http", url="https://example.invalid/mcp", enabled=True,
                            headers=["Authorization"], tools=[tool], tools_checked_at_ms=1)

    def authorized(self, headers: dict[str, str]) -> bool:
        return headers.get("Authorization") == "Bearer " + self.key

    @asynccontextmanager
    async def opener(self, server, secrets):
        """The connection a job opens: refused with `auth` (as a 401 is) unless the bearer is the key."""
        if not self.authorized(secrets.headers):
            self.receipts.append({"operation": "connect", "authorized": False, "test_mode": True})
            raise McpFailure("auth")
        yield _Client(self)


class _Client:
    def __init__(self, server: SecretServer) -> None:
        self._server = server

    async def list_tools(self, cursor=None):
        tool = SimpleNamespace(name=TOOL, description="Tomorrow's forecast of a city, with the forecast code",
                               input_schema=SCHEMA, title=None,
                               annotations=SimpleNamespace(read_only_hint=True, destructive_hint=False, title=None))
        return SimpleNamespace(tools=[tool], next_cursor=None)

    async def call_tool(self, name, arguments):
        self._server.receipts.append({"operation": name, "authorized": True, "argument_digest": digest(arguments),
                                      "test_mode": True})
        text = f"明天的預報：多雲，24 度。預報代號 {self._server.code}"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], is_error=False, structured_content=None)
