"""The plugins as RPC methods (`integrations.*`), and the hub the assistant calls tools through.

The window manages the plugins with:

- `integrations.list` the servers: name, how to reach it, whether it is on, how it is doing, how many
  tools it has and how many are on;
- `integrations.catalog` the store (`catalog`): every service that can be added with one login, and
  the server each is added as, if it is;
- `integrations.connect {catalog_id}` add a service of the store, or `{id}` sign in to a server again:
  opens the browser for the login and waits for the user (up to five minutes, which is why it is not
  a job like the others). It answers `{connected: true, server, tools}`, or `{connected: false, error}`
  with the kind of what went wrong (`denied` for a login the user refused, `cancelled`, `timeout`...);
  `integrations.connect_cancel` ends the wait. The tokens go to the vault and nowhere else;
- `integrations.add {name, transport, command, args, env, url, headers}` connect a server of the
  user's own. The values of `env` and `headers` are secrets: they go to the vault at once and are
  never sent back, logged or kept anywhere else; the list shows only their names;
- `integrations.set_enabled {id, enabled}` the whole service on or off; `integrations.disconnect {id}`
  revokes the login if the server takes revocations, forgets the secrets and removes the server;
- `integrations.refresh {id}` connect, read the tool list, keep it; `integrations.tools {id}` the
  tools kept, each on or off and a `kind` (`write` or `read`, for the screen only);
- `integrations.set_tool_enabled {id, tool, enabled}` the user's decision about one tool. Every tool
  is on until the user turns it off, new ones and ones described differently later included;
  `integrations.set_tools_enabled {id, tools, enabled}` does the same for a list of tools at once,
  all or none, with one write of the file;
- `integrations.audit {limit}` the latest calls;
- `toolsets.list` the servers of the 「連接」 tab with their tools (`toolsets.py`).

The assistant reaches the tools through `PluginHub` (`agent.plugins`): `offered`, `tier_of`,
`call` and `record_unrun`, by way of the one gate (`agent.gate`). A tool that is on has the tier
that runs at once (`ON_TIER`), one that is off has none, so it is never offered and never run. Every
call goes through `call`, which checks again that the tool is on and writes the audit whatever
happens.

Nothing here logs a name, a command, an address, a value, an argument or a result.
"""

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..agent.plugins import MAX_ARGUMENT_CHARS, PluginResult, PluginTool, clean_arguments, tool_key
from ..agent.policy import PLUGIN_PREFIX, Tier
from ..protocol import INTERNAL_ERROR, INVALID_PARAMS, RpcError
from ..router import Router
from . import catalog
from .audit import LIST_DEFAULT, AuditLog
from .client import CALL_TOTAL_S, LOGIN_TIMEOUT_S, LoginResult, McpConnector, McpFailure
from .config import (
    MAX_NAME_CHARS,
    ConfigStore,
    ServerConfig,
    ToolRecord,
    parse_new_server,
)
from .oauth import OpenBrowser, VaultTokenStorage, open_in_browser, revoke
from .toolsets import tool_kind
from .vault import SecretTooLong, SecretVault, secret_name

if TYPE_CHECKING:
    from .pool import McpPool

logger = logging.getLogger(__name__)

# The tier of a tool that is on: the one the gate lets through without a yes. A tool that is off has
# none. (The tiers T2 and T3, which ask first, are for Raphael's own commands.)
ON_TIER = Tier.T1


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _invalid(message: str) -> RpcError:
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


def _not_saved(exc: OSError) -> RpcError:
    """The error for a change that could not be written to the file. Only the type is logged and
    nothing of the message goes on: it holds the file's path."""
    logger.warning("Could not save the plugins: %s", type(exc).__name__)
    return RpcError(INTERNAL_ERROR, "Could not save the plugins")


def _str(params: dict, name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value:
        raise _invalid(f"{name} must be a non-empty string")
    return value


def _parse_key(key: str) -> tuple[str, str] | None:
    """`(server id, tool)` of the name a plugin tool has in the policy."""
    rest = key.removeprefix(PLUGIN_PREFIX) if key.startswith(PLUGIN_PREFIX) else None
    if rest is None or "/" not in rest:
        return None
    server_id, tool = rest.split("/", 1)
    return (server_id, tool) if server_id and tool else None


class IntegrationsService:
    """The plugins. `conn` is the open database (used on the event loop only, for the audit),
    `directory` holds `integrations.json`, `vault` the secrets. `connector` talks to the servers
    (tests give one with servers of their own); `call_timeout_s` is how long a call of the
    assistant may take, connecting included."""

    def __init__(
        self,
        conn: Any,
        directory: Path,
        vault: SecretVault,
        *,
        connector: McpConnector | None = None,
        now_ms: Callable[[], int] = _wall_clock_ms,
        call_timeout_s: float = CALL_TOTAL_S,
        open_browser: OpenBrowser = open_in_browser,
        login_timeout_s: float = LOGIN_TIMEOUT_S,
    ) -> None:
        self._config = ConfigStore(directory)
        self._vault = vault
        self._connector = connector if connector is not None else McpConnector(vault)
        self._audit = AuditLog(conn, now_ms)
        self._now_ms = now_ms
        self._call_timeout_s = call_timeout_s
        # server id -> the kind of failure of the latest job with it; a server not here is fine.
        self._failure: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._open_browser = open_browser
        self._login_timeout_s = login_timeout_s
        # The login that is under way (one at a time: there is one browser and one user).
        self._login: asyncio.Task | None = None
        self._login_for = ""  # the id of the server it signs in to

    def register(self, router: Router) -> None:
        methods = {
            "integrations.list": self._list,
            "integrations.catalog": self._catalog,
            "integrations.connect": self._connect,
            "integrations.connect_cancel": self._connect_cancel,
            "integrations.add": self._add,
            "integrations.set_enabled": self._set_enabled,
            "integrations.disconnect": self._disconnect,
            "integrations.refresh": self._refresh,
            "integrations.tools": self._tools,
            "integrations.set_tool_enabled": self._set_tool_enabled,
            "integrations.set_tools_enabled": self._set_tools_enabled,
            "integrations.audit": self._audit_list,
            "toolsets.list": self._toolsets,
        }
        for method, handler in methods.items():
            router.register(method, handler)

    # --- views ----------------------------------------------------------------------------------

    def _view(self, server: ServerConfig) -> dict[str, Any]:
        """A server as the window sees it: nothing secret, only the names of what is."""
        failure = self._failure.get(server.id)
        if not server.enabled:
            status = "disabled"
        elif failure is not None:
            status = "error"
        elif server.tools_checked_at_ms is None:
            status = "unchecked"
        else:
            status = "ready"
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
            "auth": server.auth,
            "catalog_id": server.catalog_id or None,
            "status": status,
            "error": failure,
            "tool_count": len(server.tools),
            "enabled_count": sum(1 for tool in server.tools if server.tool_on(tool)),
            "write_count": sum(1 for tool in server.tools if tool_kind(tool) == "write"),
            "read_count": sum(1 for tool in server.tools if tool_kind(tool) == "read"),
            "checked_at_ms": server.tools_checked_at_ms,
        }

    @staticmethod
    def _tool_view(server: ServerConfig, tool: ToolRecord) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "enabled": server.tool_on(tool),
            "title": tool.title,
            "kind": tool_kind(tool),  # how the screen groups it; decides nothing
        }

    def _server(self, params: dict) -> ServerConfig:
        server = self._config.get(_str(params, "id"))
        if server is None:
            raise _invalid("There is no such plugin")
        return server

    def _save(self) -> None:
        try:
            self._config.save()
        except OSError as exc:
            raise _not_saved(exc) from None

    # --- the methods of the window --------------------------------------------------------------

    async def _list(self, params: dict) -> dict:
        return {"servers": [self._view(server) for server in self._config.servers()]}

    async def _catalog(self, params: dict) -> dict:
        added = {s.catalog_id: s.id for s in self._config.servers() if s.catalog_id}
        return {"services": [{**catalog.view(entry), "server_id": added.get(entry.id)} for entry in catalog.SERVICES]}

    def _unused_name(self, name: str) -> str:
        taken = {s.name.casefold() for s in self._config.servers()}
        candidate, n = name, 1
        while candidate.casefold() in taken:
            n += 1
            candidate = f"{name[: MAX_NAME_CHARS - 4]} {n}"
        return candidate

    async def _connect(self, params: dict) -> dict:
        """Sign in to a service of the store, or to a server again. Waits for the user in the
        browser, so it can take minutes; `connect_cancel` ends it."""
        if self._login is not None and not self._login.done():
            raise _invalid("A login is under way already")
        ident = params.get("id")
        if ident is not None:
            server = self._server(params)
            if server.auth != "oauth":
                raise _invalid("This plugin does not use a login")
            new = False
        else:
            entry = catalog.find(_str(params, "catalog_id"))
            if entry is None:
                raise _invalid("There is no such service in the store")
            if any(s.catalog_id == entry.id for s in self._config.servers()):
                raise _invalid("This service is added already")
            server = ServerConfig(
                id=secrets.token_hex(6),
                name=self._unused_name(entry.name),
                transport="http",
                url=entry.url,
                auth=entry.auth,
                catalog_id=entry.id,
            )
            new = True
        self._connector.invalidate(server.id)  # a login again: the old one is not to be used
        if server.auth == "none":
            # A public server: there is no login, so the browser stays shut and the tools are read at once.
            job = self._join_public(server)
        else:
            job = self._connector.login(server, self._open_browser, timeout_s=self._login_timeout_s)
        self._login = asyncio.create_task(job)
        self._login_for = server.id
        try:
            result = await self._login
        except McpFailure as failure:
            return self._login_failed(server, new, failure.kind)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():  # the core is closing, not the user cancelling
                self._login.cancel()
                self._wipe_login(server, new)
                raise
            return self._login_failed(server, new, "cancelled")
        if not new and self._config.get(server.id) is not server:
            return {"connected": False, "error": "cancelled"}  # removed while the browser was open
        server.tools = result.tools
        server.tools_checked_at_ms = self._now_ms()
        server.revocation_url = result.revocation_url
        try:
            if new:
                self._config.add(server)
            else:
                self._config.save()
        except (ValueError, OSError) as exc:
            self._wipe_login(server, True)
            if isinstance(exc, ValueError):  # too many plugins
                raise _invalid(str(exc)) from None
            raise _not_saved(exc) from None
        self._failure.pop(server.id, None)
        self._connector.invalidate(server.id)  # the connections of before used the old login
        return {"connected": True, "server": self._view(server), "tools": self._tool_views(server)}

    async def _join_public(self, server: ServerConfig) -> LoginResult:
        return LoginResult(await self._connector.list_tools(server))

    def _wipe_login(self, server: ServerConfig, new: bool) -> None:
        """Take what a login that did not finish left in the vault (a server added by it goes whole;
        one that was there keeps its settings but no tokens)."""
        try:
            VaultTokenStorage(self._vault, server.id).forget()
        except Exception as exc:
            logger.warning("Could not remove a plugin's login: %s", type(exc).__name__)
        if not new:
            self._failure[server.id] = "auth"

    def _login_failed(self, server: ServerConfig, new: bool, kind: str) -> dict:
        self._wipe_login(server, new)
        return {"connected": False, "error": kind}

    async def _connect_cancel(self, params: dict) -> dict:
        task = self._login
        if task is None or task.done():
            return {"cancelled": False}
        task.cancel()
        return {"cancelled": True}

    async def _add(self, params: dict) -> dict:
        try:
            new = parse_new_server(params, [s.name for s in self._config.servers()])
        except ValueError as exc:  # says what is wrong, never a value
            raise _invalid(str(exc)) from None
        server = new.config
        stored: list[str] = []
        try:
            for key, value in new.env.items():
                self._vault.set(secret_name(server.id, "env", key), value)
                stored.append(secret_name(server.id, "env", key))
            for key, value in new.headers.items():
                self._vault.set(secret_name(server.id, "header", key), value)
                stored.append(secret_name(server.id, "header", key))
            self._config.add(server)
        except SecretTooLong as exc:
            self._forget(stored)
            raise _invalid(str(exc)) from None
        except ValueError as exc:  # too many plugins
            self._forget(stored)
            raise _invalid(str(exc)) from None
        except OSError as exc:
            self._forget(stored)
            raise _not_saved(exc) from None
        except Exception as exc:  # the vault: its message could hold anything
            self._forget(stored)
            logger.warning("Could not store a plugin's secrets: %s", type(exc).__name__)
            raise RpcError(INTERNAL_ERROR, "Could not store the secrets") from None
        return {"server": self._view(server)}

    def _forget(self, names: list[str]) -> None:
        """Take secrets out of the vault again (best effort: a failure is logged by its type)."""
        for name in names:
            try:
                self._vault.delete(name)
            except Exception as exc:
                logger.warning("Could not remove a plugin's secret: %s", type(exc).__name__)

    async def _set_enabled(self, params: dict) -> dict:
        server = self._server(params)
        enabled = params.get("enabled")
        if type(enabled) is not bool:
            raise _invalid("enabled must be true or false")
        server.enabled = enabled
        self._save()
        return {"server": self._view(server)}

    async def _disconnect(self, params: dict) -> dict:
        server = self._server(params)
        if self._login is not None and not self._login.done() and self._login_for == server.id:
            self._login.cancel()
        try:
            removed = self._config.remove(server.id)
        except OSError as exc:
            # The file still has the server, so it is still connected and keeps its secrets.
            raise _not_saved(exc) from None
        if removed is None:
            return {"removed": False, "revoked": False}
        self._failure.pop(server.id, None)
        self._locks.pop(server.id, None)
        self._connector.invalidate(server.id)
        revoked = False
        if server.auth == "oauth":
            storage = VaultTokenStorage(self._vault, server.id)
            revoked = await revoke(storage, server.revocation_url) if server.revocation_url else False
            try:
                storage.forget()
            except Exception as exc:
                logger.warning("Could not remove a plugin's login: %s", type(exc).__name__)
        self._forget(
            [secret_name(server.id, "env", key) for key in server.env]
            + [secret_name(server.id, "header", key) for key in server.headers]
        )
        return {"removed": True, "revoked": revoked}

    async def _refresh(self, params: dict) -> dict:
        server = self._server(params)
        lock = self._locks.setdefault(server.id, asyncio.Lock())
        async with lock:
            try:
                tools = await self._connector.list_tools(server)
            except McpFailure as failure:
                self._failure[server.id] = failure.kind
            else:
                self._failure.pop(server.id, None)
                if self._config.get(server.id) is server:  # not removed while it was away
                    server.tools = tools
                    server.tools_checked_at_ms = self._now_ms()
                    self._save()
                    # A connection kept open knows the tools of before.
                    self._connector.invalidate(server.id)
        return {"server": self._view(server), "tools": self._tool_views(server)}

    def kind_of(self, server_name: str, tool_name: str) -> str:
        """`"read"` for a tool that only looks, `"write"` for one that changes something or that is
        not known (the safe guess)."""
        for server in self._config.servers():
            if server.name == server_name:
                for tool in server.tools:
                    if tool.name == tool_name:
                        return tool_kind(tool)
        return "write"

    def _tool_views(self, server: ServerConfig) -> list[dict[str, Any]]:
        return [self._tool_view(server, tool) for tool in server.tools]

    async def _tools(self, params: dict) -> dict:
        return {"tools": self._tool_views(self._server(params))}

    async def _set_tool_enabled(self, params: dict) -> dict:
        server = self._server(params)
        name = _str(params, "tool")
        enabled = params.get("enabled")
        if type(enabled) is not bool:
            raise _invalid("enabled must be true or false")
        tool = server.tool(name)
        if tool is None:
            raise _invalid("There is no such tool")
        (server.disabled_tools.discard if enabled else server.disabled_tools.add)(name)
        self._save()
        return {"tool": self._tool_view(server, tool)}

    async def _set_tools_enabled(self, params: dict) -> dict:
        """A whole group switched at once. The list is checked in full before anything changes, so a
        bad name leaves every tool as it was."""
        server = self._server(params)
        names = params.get("tools")
        enabled = params.get("enabled")
        if type(enabled) is not bool:
            raise _invalid("enabled must be true or false")
        if not isinstance(names, list) or not names:
            raise _invalid("tools must be a list of names")
        tools = []
        for name in names:
            if not isinstance(name, str) or not name:
                raise _invalid("tools must be a list of names")
            tool = server.tool(name)
            if tool is None:
                raise _invalid("There is no such tool")
            tools.append(tool)
        for tool in tools:
            (server.disabled_tools.discard if enabled else server.disabled_tools.add)(tool.name)
        self._save()
        return {"tools": [self._tool_view(server, tool) for tool in tools]}

    def _group(self, server: ServerConfig) -> dict[str, Any]:
        return {**self._view(server), "tools": self._tool_views(server)}

    async def _toolsets(self, params: dict) -> dict:
        return {"groups": [self._group(server) for server in self._config.servers()]}

    async def _audit_list(self, params: dict) -> dict:
        limit = params.get("limit", LIST_DEFAULT)
        if type(limit) is not int:
            raise _invalid("limit must be a whole number")
        return {
            "entries": [
                {
                    "id": entry.id,
                    "at_ms": entry.at_ms,
                    "server": entry.server,
                    "tool": entry.tool,
                    "tier": entry.tier,
                    "approver": entry.approver,
                    "outcome": entry.outcome,
                    "task_id": entry.task_id,
                }
                for entry in self._audit.recent(limit)
            ]
        }

    # --- the hub the assistant calls tools through ----------------------------------------------

    def _tier_for(self, tool: ToolRecord) -> Tier:
        """The tier a tool that is on has. Always `ON_TIER`; a test gives a hub whose tools ask."""
        return ON_TIER

    def _usable(self, key: str) -> tuple[ServerConfig, ToolRecord] | None:
        """The server and the tool of a tool that may be used now: its plugin is on, it is a tool the
        plugin has, and the user did not turn it off."""
        parsed = _parse_key(key)
        if parsed is None:
            return None
        server = self._config.get(parsed[0])
        if server is None or not server.enabled:
            return None
        tool = server.tool(parsed[1])
        if tool is None or not server.tool_on(tool):
            return None
        return server, tool

    def offered(self) -> list[PluginTool]:
        tools = []
        for server in self._config.servers():
            if not server.enabled:
                continue
            for tool in server.tools:
                if server.tool_on(tool):
                    tools.append(
                        PluginTool(
                            key=tool_key(server.id, tool.name),
                            server=server.name,
                            name=tool.name,
                            description=tool.description,
                            schema=tool.input_schema,
                            tier=self._tier_for(tool),
                        )
                    )
        return tools

    def tier_of(self, key: str) -> Tier | None:
        usable = self._usable(key)
        return None if usable is None else self._tier_for(usable[1])

    def _names(self, key: str) -> tuple[str, str]:
        """What the audit calls a tool: its server's name and its own, as far as they are known."""
        parsed = _parse_key(key)
        server = self._config.get(parsed[0]) if parsed else None
        if parsed is None or server is None:
            return ("?", "?")
        return (server.name, parsed[1])

    def record_unrun(self, key: str, *, outcome: str, task_id: int | None = None) -> None:
        server_name, tool_name = self._names(key)
        self._audit.add(
            server=server_name,
            tool=tool_name,
            tier=None,
            approver="none",
            outcome=outcome,
            task_id=task_id,
        )

    async def call(
        self,
        key: str,
        arguments: Mapping[str, Any],
        *,
        approver: str,
        task_id: int | None = None,
        pool: "McpPool | None" = None,
    ) -> PluginResult:
        server_name, tool_name = self._names(key)
        usable = self._usable(key)

        def end(outcome: str, result: PluginResult) -> PluginResult:
            self._audit.add(
                server=server_name,
                tool=tool_name,
                tier=None,  # a plugin's tool has no tier: it is on or off
                approver=approver if outcome not in ("blocked",) else "none",
                outcome=outcome,
                task_id=task_id,
            )
            return result

        if usable is None:
            return end("blocked", PluginResult(False, "blocked"))
        server, tool = usable
        # A call is made with the gate's word for it (`ToolGate.call_plugin`): "none" is a call the
        # gate refused, which is written down and not made.
        if approver not in ("auto", "user"):
            return end("blocked", PluginResult(False, "blocked"))
        if not isinstance(arguments, Mapping) or len(
            json.dumps(arguments, ensure_ascii=False, default=str)
        ) > MAX_ARGUMENT_CHARS:
            return end("blocked", PluginResult(False, "blocked"))
        # A pool is passed on only when there is one: a connector of a test may not know it.
        kept = {} if pool is None else {"pool": pool}
        try:
            outcome = await self._connector.call_tool(
                server,
                tool.name,
                clean_arguments(tool.input_schema, arguments),
                expected=tool.fingerprint,
                timeout_s=self._call_timeout_s,
                **kept,
            )
        except asyncio.CancelledError:
            # Esc, or the core closing, ended the call part way: whether it was done is not known.
            end("error", PluginResult(False, "cancelled"))
            raise
        except McpFailure as failure:
            if failure.kind == "gone":
                return end("blocked", PluginResult(False, failure.kind))
            self._failure[server.id] = failure.kind
            result = PluginResult(False, failure.kind)
            return end("timeout" if failure.kind == "timeout" else "error", result)
        self._failure.pop(server.id, None)
        if outcome.fresh is not None and self._config.get(server.id) is server:
            # The server describes the tool differently now: it is kept as it is (and stays on).
            server.tools = [outcome.fresh if t.name == tool.name else t for t in server.tools]
            try:
                self._config.save()
            except OSError as exc:
                logger.warning("Could not save the plugins: %s", type(exc).__name__)
        if outcome.is_error:
            result = PluginResult(False, "tool_error", outcome.text, outcome.truncated)
            return end("error", result)
        return end("ok", PluginResult(True, None, outcome.text, outcome.truncated))
