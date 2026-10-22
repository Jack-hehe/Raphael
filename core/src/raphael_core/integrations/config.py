"""The plugins the user connected, and what they decided about each tool: `integrations.json`.

The file is in the data directory and holds, for each server, its name, how to reach it (a command
and arguments, or a URL), whether it is on, the names (never the values) of its environment
variables and headers, the list of tools it reported last time, and the names of the tools the
user turned off. It never holds a secret: the values are in `vault` (a login's tokens too). Nothing
here logs a name, a command, an address or a value.

A tool is on unless the user turned it off, and only the user does: what the server says about
itself (its annotations, its description) groups the tools on the screen and decides nothing. A tool
that is new, or that the server describes differently later, is on like the rest; one that is off
is never offered to a model and never run. The first format of the file (version 1) held a tier for
each tool, tied to a fingerprint: reading it, everything is on but a tool that was
blocked on purpose.
"""

import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

FILE_NAME = "integrations.json"
FORMAT_VERSION = 2

# What the first format of the file held for a tool the user blocked (read once, to migrate).
_OLD_BLOCK = "block"
TRANSPORTS = ("stdio", "http")
# How a server is signed in to: none (headers, if any), or a login in the browser (OAuth).
AUTH_KINDS = ("none", "oauth", "device")

MAX_SERVERS = 20
MAX_NAME_CHARS = 40
MAX_COMMAND_CHARS = 260
MAX_ARGS = 32
MAX_ARG_CHARS = 500
MAX_PAIRS = 16
MAX_URL_CHARS = 2048
# What is kept of a tool's description: it is read by the user and by a model, and no more than
# this is worth either's time.
MAX_DESCRIPTION_CHARS = 1000
# A schema longer than this (as JSON) is kept as "an object" only: it would fill a prompt.
MAX_SCHEMA_CHARS = 16000
# What is kept of the title a server gives a tool (for the screen only).
MAX_TITLE_CHARS = 80

_TOOL_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,128}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_HEADER_NAME = re.compile(r"[A-Za-z0-9\-]{1,64}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_OPEN_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": True}


@dataclass
class ToolRecord:
    """A tool as its server described it when the list was last read."""

    name: str
    description: str
    input_schema: dict[str, Any]
    fingerprint: str
    # What the server claims about the tool. Only ever used to group the tools on the screen.
    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    # The human name the server gives the tool, if it does. For the screen only.
    title: str | None = None


@dataclass
class ServerConfig:
    id: str
    name: str
    transport: str  # "stdio" or "http"
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    enabled: bool = True
    env: list[str] = field(default_factory=list)  # names; the values are in the vault
    headers: list[str] = field(default_factory=list)  # names; the values are in the vault
    tools: list[ToolRecord] = field(default_factory=list)
    tools_checked_at_ms: int | None = None
    disabled_tools: set[str] = field(default_factory=set)  # the names of the tools turned off
    auth: str = "none"  # "oauth": a login in the browser, "device": a code typed at the service; tokens are in the vault
    catalog_id: str = ""  # the entry of the store this was added from, if it was
    revocation_url: str = ""  # where the login's token is revoked when the server is disconnected

    def tool(self, name: str) -> ToolRecord | None:
        return next((tool for tool in self.tools if tool.name == name), None)

    def tool_on(self, tool: ToolRecord) -> bool:
        """Whether `tool` may be used: it is on unless the user turned it off."""
        return tool.name not in self.disabled_tools


def fingerprint(name: str, description: str, schema: Any) -> str:
    """A short hash of what makes a tool the tool the user looked at."""
    canonical = json.dumps(
        {"name": name, "description": description, "schema": schema},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def tool_record(
    name: Any,
    description: Any,
    schema: Any,
    read_only_hint: Any = None,
    destructive_hint: Any = None,
    title: Any = None,
) -> ToolRecord | None:
    """A tool as it is kept, from what a server reported; None for one whose name cannot be kept
    (a name is shown, audited and sent to a model, so it must be plain)."""
    if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
        return None
    text = description.strip() if isinstance(description, str) else ""
    text = text[:MAX_DESCRIPTION_CHARS]
    if not isinstance(schema, dict):
        schema = dict(_OPEN_SCHEMA)
    elif len(json.dumps(schema, ensure_ascii=False)) > MAX_SCHEMA_CHARS:
        schema = dict(_OPEN_SCHEMA)
    return ToolRecord(
        name=name,
        description=text,
        input_schema=schema,
        fingerprint=fingerprint(name, text, schema),
        read_only_hint=read_only_hint if isinstance(read_only_hint, bool) else None,
        destructive_hint=destructive_hint if isinstance(destructive_hint, bool) else None,
        title=(title.strip()[:MAX_TITLE_CHARS] or None) if isinstance(title, str) else None,
    )


# --- what the user types ---------------------------------------------------------------------


@dataclass
class NewServer:
    """A server the user asks to add, checked: the secrets apart from the rest."""

    config: ServerConfig
    env: dict[str, str]
    headers: dict[str, str]


def _text(params: dict, name: str, *, limit: int) -> str:
    value = params.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    if len(value) > limit:
        raise ValueError(f"{name} must be at most {limit} characters")
    if _CONTROL.search(value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _pairs(params: dict, name: str, key_pattern: re.Pattern[str], kind: str) -> dict[str, str]:
    """The `{name, value}` pairs the user typed, as a dict. Both are required; a pair with no value
    would be no secret at all."""
    raw = params.get(name)
    if raw is None:
        return {}
    if not isinstance(raw, list) or len(raw) > MAX_PAIRS:
        raise ValueError(f"{name} must be a list of at most {MAX_PAIRS} pairs")
    pairs: dict[str, str] = {}
    for item in raw:
        key = item.get("name") if isinstance(item, dict) else None
        value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(key, str) or not key_pattern.fullmatch(key.strip()):
            raise ValueError(f"A {kind} name is not valid")
        key = key.strip()
        if not isinstance(value, str) or not value:
            raise ValueError(f"The {kind} {key} needs a value")
        if _CONTROL.search(value):
            raise ValueError(f"The {kind} {key} must not contain control characters")
        folded = key.casefold() if kind == "header" else key
        if any((k.casefold() if kind == "header" else k) == folded for k in pairs):
            raise ValueError(f"The {kind} {key} is given twice")
        pairs[key] = value
    return pairs


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def checked_url(url: str) -> str:
    """`url` if a plugin may be reached there: https, or http on this computer only; no credentials
    in the address (they would be stored in the clear: a token goes in a header)."""
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("url must be an http or https address")
    if parts.username is not None or parts.password is not None:
        raise ValueError("url must not contain a user name or password: use a header")
    if parts.fragment:
        raise ValueError("url must not contain a fragment")
    if parts.scheme == "http" and not _is_loopback(host):
        raise ValueError("url must be https unless it is on this computer")
    return url


def parse_new_server(params: dict, taken_names: list[str]) -> NewServer:
    """The server the user asked to add. ValueError, saying what is wrong, when they gave
    something a server cannot be made from (it never names a value: a value may be a secret).
    `taken_names` are the names of the servers there are."""
    name = _text(params, "name", limit=MAX_NAME_CHARS)
    if any(name.casefold() == other.casefold() for other in taken_names):
        raise ValueError("A plugin with this name exists already")
    transport = params.get("transport")
    if transport not in TRANSPORTS:
        raise ValueError("transport must be stdio or http")
    config = ServerConfig(id=secrets.token_hex(6), name=name, transport=transport)
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    if transport == "stdio":
        config.command = _text(params, "command", limit=MAX_COMMAND_CHARS)
        args = params.get("args", [])
        if not isinstance(args, list) or len(args) > MAX_ARGS:
            raise ValueError(f"args must be a list of at most {MAX_ARGS} items")
        for arg in args:
            if not isinstance(arg, str) or len(arg) > MAX_ARG_CHARS or "\x00" in arg:
                raise ValueError("An argument is not valid")
        config.args = list(args)
        env = _pairs(params, "env", _ENV_NAME, "environment variable")
        config.env = list(env)
    else:
        config.url = checked_url(_text(params, "url", limit=MAX_URL_CHARS))
        headers = _pairs(params, "headers", _HEADER_NAME, "header")
        config.headers = list(headers)
    return NewServer(config, env, headers)


# --- the file --------------------------------------------------------------------------------


def _tool_to_json(tool: ToolRecord) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
        "fingerprint": tool.fingerprint,
        "read_only_hint": tool.read_only_hint,
        "destructive_hint": tool.destructive_hint,
        "title": tool.title,
    }


def _server_to_json(server: ServerConfig) -> dict[str, Any]:
    return {
        "id": server.id,
        "name": server.name,
        "transport": server.transport,
        "command": server.command,
        "args": server.args,
        "url": server.url,
        "enabled": server.enabled,
        "env": server.env,
        "headers": server.headers,
        "tools": [_tool_to_json(tool) for tool in server.tools],
        "tools_checked_at_ms": server.tools_checked_at_ms,
        "disabled_tools": sorted(server.disabled_tools),
        "auth": server.auth,
        "catalog_id": server.catalog_id,
        "revocation_url": server.revocation_url,
    }


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _server_from_json(raw: Any) -> ServerConfig | None:
    """A server as the file has it, or None for an entry that is not one (it is dropped: a hand
    edit can break the file, and a server that cannot be read must not block the others)."""
    if not isinstance(raw, dict):
        return None
    ident, name, transport = raw.get("id"), raw.get("name"), raw.get("transport")
    if not (isinstance(ident, str) and ident and isinstance(name, str) and name):
        return None
    if transport not in TRANSPORTS:
        return None
    server = ServerConfig(
        id=ident,
        name=name,
        transport=transport,
        command=raw["command"] if isinstance(raw.get("command"), str) else "",
        args=_strings(raw.get("args")),
        url=raw["url"] if isinstance(raw.get("url"), str) else "",
        enabled=raw.get("enabled") is not False,
        env=_strings(raw.get("env")),
        headers=_strings(raw.get("headers")),
        auth=raw["auth"] if raw.get("auth") in AUTH_KINDS else "none",
        catalog_id=raw["catalog_id"] if isinstance(raw.get("catalog_id"), str) else "",
        revocation_url=raw["revocation_url"] if isinstance(raw.get("revocation_url"), str) else "",
    )
    checked = raw.get("tools_checked_at_ms")
    server.tools_checked_at_ms = checked if type(checked) is int else None
    for item in raw.get("tools", []) if isinstance(raw.get("tools"), list) else []:
        if not isinstance(item, dict):
            continue
        # Rebuilt, so that the fingerprint is always the one of what is kept.
        tool = tool_record(
            item.get("name"),
            item.get("description"),
            item.get("input_schema"),
            item.get("read_only_hint"),
            item.get("destructive_hint"),
            item.get("title"),
        )
        if tool is not None:
            server.tools.append(tool)
    if isinstance(raw.get("disabled_tools"), list):
        server.disabled_tools = set(_strings(raw["disabled_tools"]))
    else:
        # The first format: everything is on but a tool the user blocked on purpose.
        grants = raw.get("grants") if isinstance(raw.get("grants"), dict) else {}
        for tool in server.tools:
            grant = grants.get(tool.name)
            if isinstance(grant, dict) and grant.get("tier") == _OLD_BLOCK:
                server.disabled_tools.add(tool.name)
    return server


class ConfigStore:
    """The servers, in `integrations.json` under `directory`. Used on the event loop only.

    The file is read once, when it is first needed, and rewritten whole after each change: to a
    file beside it that then replaces it, so that a crash leaves the old file or the new one. A
    file that cannot be read is set aside (as `integrations.json.unreadable`) and the plugins start
    again from none; the secrets of the lost servers stay in the vault until the user removes the
    server again.
    """

    def __init__(self, directory: Path) -> None:
        self._path = directory / FILE_NAME
        self._servers: list[ServerConfig] | None = None

    def _load(self) -> list[ServerConfig]:
        if self._servers is not None:
            return self._servers
        servers: list[ServerConfig] = []
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = None
        except OSError as exc:
            logger.warning("Could not read the plugins file: %s", type(exc).__name__)
            text = None
        if text is not None:
            try:
                data = json.loads(text)
                raw_servers = data.get("servers") if isinstance(data, dict) else None
                if not isinstance(raw_servers, list):
                    raise ValueError("no servers")
                for raw in raw_servers:
                    server = _server_from_json(raw)
                    if server is not None and all(server.id != other.id for other in servers):
                        servers.append(server)
            except (ValueError, AttributeError) as exc:
                servers = []
                kind = type(exc).__name__
                try:
                    os.replace(self._path, self._path.with_name(FILE_NAME + ".unreadable"))
                except OSError as error:
                    logger.warning(
                        "The plugins file is not valid (%s) and could not be set aside (%s): the"
                        " next change replaces it",
                        kind,
                        type(error).__name__,
                    )
                else:
                    logger.warning("The plugins file is not valid (%s); it was set aside", kind)
        self._servers = servers
        return servers

    def servers(self) -> list[ServerConfig]:
        return list(self._load())

    def get(self, server_id: str) -> ServerConfig | None:
        return next((server for server in self._load() if server.id == server_id), None)

    def add(self, server: ServerConfig) -> None:
        servers = self._load()
        if len(servers) >= MAX_SERVERS:
            raise ValueError(f"At most {MAX_SERVERS} plugins can be connected")
        servers.append(server)
        self.save()

    def remove(self, server_id: str) -> ServerConfig | None:
        servers = self._load()
        gone = self.get(server_id)
        if gone is not None:
            servers.remove(gone)
            self.save()
        return gone

    def save(self) -> None:
        """Write the file. OSError reaches the caller: a change that was not kept must not look
        as if it was, so the servers are read from the file again, as it is, next time."""
        data = {"version": FORMAT_VERSION, "servers": [_server_to_json(s) for s in self._load()]}
        temporary = self._path.with_name(FILE_NAME + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            self._servers = None
            raise
