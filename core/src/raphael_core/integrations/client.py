"""Talking to an MCP server: connect when there is something to do, list its tools, call one.

A connection is made for each job and closed when the job is done (a stdio server is started for
it and stopped after it; an HTTP server is called with a request client of its own). There is
nothing to keep alive, and so nothing that can be left running when the app closes. The price is
the time a server takes to start, which is the reason the lists of tools are cached
(`config.ToolRecord`) and the assistant plans from the cache.

- Every job has a time limit that covers the connection too. A stdio server is started with the
  environment the user gave it on top of the few variables a program needs (the SDK's list), not
  with the core's own, and its error output is thrown away: it is the server's, and could hold
  anything.
- What a tool returns is cut to `MAX_RESULT_CHARS` before it goes anywhere, and only its text is
  kept: an image or a file is replaced by a note that it was left out.
- Before a tool is called its server is asked for the tool list once more, and the call goes
  ahead only if the tool is still the one the user gave its tier to (`config.fingerprint`).
- A failure is a kind (`McpFailure`), never a message: the message of an exception could hold an
  address with a token in it, or what the server printed.

The SDK (`mcp`) takes over a second to import, so it is imported when the first job needs it and
not before. Nothing here logs a command, an address, a value, an argument or a result.
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ServerConfig, ToolRecord, tool_record
from .vault import SecretVault, secret_name

logger = logging.getLogger(__name__)

# The most a job may take, connecting included. Starting a server that is fetched first (npx) can
# take most of the refresh's time; a call the assistant makes waits for much less, since someone is
# listening.
REFRESH_TOTAL_S = 30.0
CALL_TOTAL_S = 15.0
# What of a tool's result is passed on, in characters.
MAX_RESULT_CHARS = 8000
# The most tools read from one server, and the most pages of the list that are asked for.
MAX_TOOLS = 200
MAX_PAGES = 10

KINDS = (
    "not_found",
    "timeout",
    "auth",
    "network",
    "protocol",
    "missing_secret",
    "changed",
    "gone",
    "error",
)


class McpFailure(Exception):
    """A job with a server that did not work out. `kind` is one of KINDS and says why in one word;
    `fresh` is the tool as the server describes it now, for a tool that `changed`."""

    def __init__(self, kind: str, fresh: ToolRecord | None = None) -> None:
        super().__init__(kind)
        self.kind = kind if kind in KINDS else "error"
        self.fresh = fresh


@dataclass
class Secrets:
    """The secret values of one server, read from the vault for one job."""

    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # a stray print or log line must not carry the values
        return f"Secrets(env={sorted(self.env)}, headers={sorted(self.headers)})"


@dataclass
class CallOutcome:
    """What a tool answered: its text (cut to MAX_RESULT_CHARS), and whether it said it failed."""

    text: str
    is_error: bool
    truncated: bool = False


# Opens a client for a job: an `mcp.Client`, or anything with its `list_tools` and `call_tool`.
Opener = Callable[[ServerConfig, Secrets], AbstractAsyncContextManager[Any]]


@asynccontextmanager
async def open_client(server: ServerConfig, secrets: Secrets) -> Any:
    """A client of the server, connected, for the length of the `async with`."""
    from mcp import Client

    if server.transport == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=server.command,
            args=list(server.args),
            env=dict(secrets.env) or None,
            # Where a person's own programs start, not in the app's install folder.
            cwd=Path.home(),
        )
        with open(os.devnull, "w", encoding="utf-8") as quiet:
            async with Client(stdio_client(params, errlog=quiet)) as client:
                yield client
        return

    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    timeout = httpx2.Timeout(15.0, read=CALL_TOTAL_S * 2)
    async with httpx2.AsyncClient(headers=dict(secrets.headers), timeout=timeout) as http:
        async with Client(streamable_http_client(server.url, http_client=http)) as client:
            yield client


def _leaves(error: BaseException) -> list[BaseException]:
    """The exceptions inside an exception group (the SDK's task groups wrap what went wrong), or
    the exception itself."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaves(inner)]
    return [error]


def failure_kind(error: BaseException) -> str:
    """The kind of failure `error` amounts to (one of KINDS)."""
    for leaf in _leaves(error):
        if isinstance(leaf, McpFailure):
            return leaf.kind
        if isinstance(leaf, TimeoutError):
            return "timeout"
        if isinstance(leaf, FileNotFoundError | NotADirectoryError | PermissionError):
            return "not_found"  # the command is not there, or may not be run
        if getattr(leaf, "winerror", None) == 193:
            return "not_found"  # Windows: the file is not a program
        module = type(leaf).__module__
        if module.startswith("httpx2") or module.startswith("httpcore"):
            status = getattr(getattr(leaf, "response", None), "status_code", None)
            if status in (401, 403):
                return "auth"
            return "timeout" if "Timeout" in type(leaf).__name__ else "network"
        if module.startswith(("mcp", "pydantic", "anyio")):
            return "protocol"
    return "error"


def _failure(what: str, error: BaseException) -> McpFailure:
    """The McpFailure that `error` stands for. A failure of our own that the SDK's task groups
    wrapped is the one raised again, with what it carries; anything else is logged, by the types of
    what went wrong and nothing else."""
    own = next((leaf for leaf in _leaves(error) if isinstance(leaf, McpFailure)), None)
    if own is not None:
        return own
    logger.warning(
        "A plugin %s failed: %s", what, ", ".join(sorted({type(x).__name__ for x in _leaves(error)}))
    )
    return McpFailure(failure_kind(error))


class McpConnector:
    """Does the jobs. `opener` makes the client of a job (`open_client`, unless a test gives
    another); `vault` has the secrets."""

    def __init__(self, vault: SecretVault, opener: Opener = open_client) -> None:
        self._vault = vault
        self._opener = opener

    def _secrets(self, server: ServerConfig) -> Secrets:
        secrets = Secrets()
        try:
            for key in server.env:
                value = self._vault.get(secret_name(server.id, "env", key))
                if value is None:
                    raise McpFailure("missing_secret")
                secrets.env[key] = value
            for key in server.headers:
                value = self._vault.get(secret_name(server.id, "header", key))
                if value is None:
                    raise McpFailure("missing_secret")
                secrets.headers[key] = value
        except McpFailure:
            raise
        except Exception as exc:  # the vault is locked, say: its message could hold anything
            logger.warning("The vault failed to read a plugin's secrets: %s", type(exc).__name__)
            raise McpFailure("error") from None
        return secrets

    @staticmethod
    async def _list(client: Any) -> list[ToolRecord]:
        tools: list[ToolRecord] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            page = await client.list_tools(cursor=cursor)
            for item in page.tools:
                annotations = getattr(item, "annotations", None)
                record = tool_record(
                    item.name,
                    getattr(item, "description", None),
                    getattr(item, "input_schema", None),
                    getattr(annotations, "read_only_hint", None),
                )
                if record is not None and all(record.name != other.name for other in tools):
                    tools.append(record)
            cursor = page.next_cursor
            if cursor is None or len(tools) >= MAX_TOOLS:
                break
        return tools[:MAX_TOOLS]

    async def list_tools(
        self, server: ServerConfig, *, timeout_s: float = REFRESH_TOTAL_S
    ) -> list[ToolRecord]:
        """The tools `server` offers now. McpFailure (of a kind) when it cannot be had."""
        secrets = self._secrets(server)
        try:
            async with asyncio.timeout(timeout_s), self._opener(server, secrets) as client:
                return await self._list(client)
        except Exception as exc:
            raise _failure("tool list", exc) from None

    async def call_tool(
        self,
        server: ServerConfig,
        tool: str,
        arguments: dict[str, Any],
        *,
        expected: str,
        timeout_s: float = CALL_TOTAL_S,
    ) -> CallOutcome:
        """Call `tool` of `server`, if it is still the tool with fingerprint `expected`. McpFailure
        (of a kind) when it cannot be called; a tool that ran and said it failed is a CallOutcome
        with `is_error`."""
        secrets = self._secrets(server)
        try:
            async with asyncio.timeout(timeout_s), self._opener(server, secrets) as client:
                current = next((t for t in await self._list(client) if t.name == tool), None)
                if current is None:
                    raise McpFailure("gone")
                if current.fingerprint != expected:
                    raise McpFailure("changed", fresh=current)
                result = await client.call_tool(tool, arguments)
        except Exception as exc:
            raise _failure("tool call", exc) from None
        text, truncated = render_result(result)
        return CallOutcome(text=text, is_error=bool(getattr(result, "is_error", False)), truncated=truncated)


def render_result(result: Any) -> tuple[str, bool]:
    """The text of a tool's result, cut to MAX_RESULT_CHARS, and whether it was cut. Other kinds
    of content (an image, audio, a file) are named and left out."""
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(str(getattr(item, "text", "")))
        elif kind == "resource":
            text = getattr(getattr(item, "resource", None), "text", None)
            parts.append(text if isinstance(text, str) else "[a file was left out]")
        else:
            parts.append(f"[{kind or 'content'} was left out]")
    structured = getattr(result, "structured_content", None)
    if not parts and structured:
        with contextlib.suppress(TypeError, ValueError):
            parts.append(json.dumps(structured, ensure_ascii=False))
    text = "\n".join(parts).strip()
    if len(text) > MAX_RESULT_CHARS:
        return text[:MAX_RESULT_CHARS] + "…", True
    return text, False
