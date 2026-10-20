"""The plugins as RPC methods (`integrations.*`), and the hub the assistant calls tools through.

The window manages the plugins with:

- `integrations.list` the servers: name, how to reach it, whether it is on, how it is doing, how many
  tools it has and how many may be used;
- `integrations.add {name, transport, command, args, env, url, headers}` connect a server. The
  values of `env` and `headers` are secrets: they go to the vault at once and are never sent back,
  logged or kept anywhere else; the list shows only their names;
- `integrations.set_enabled {id, enabled}` and `integrations.remove {id}` (which forgets the
  server's secrets too);
- `integrations.refresh {id}` connect, read the tool list, keep it; `integrations.tools {id}` the
  tools kept, each with the tier the user gave it, if any;
- `integrations.set_tier {id, tool, tier}` the user's decision about one tool: "T0" to "T3", or null
  to take the tool back (a tool with no tier is blocked: never offered to a model, never run);
- `integrations.audit {limit}` the latest calls.

The assistant reaches the tools through `PluginHub` (`agent.plugins`): `offered`, `tier_of`,
`call` and `record_unrun`. Every call goes through `call`, which checks again that the tool is
still the one the user gave its tier to, refuses a call that needs a yes without one, and writes
the audit whatever happens.

Nothing here logs a name, a command, an address, a value, an argument or a result.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..agent.plugins import MAX_ARGUMENT_CHARS, PluginResult, PluginTool, tool_key
from ..agent.policy import PLUGIN_PREFIX, Tier
from ..protocol import INTERNAL_ERROR, INVALID_PARAMS, RpcError
from ..router import Router
from .audit import LIST_DEFAULT, AuditLog
from .client import CALL_TOTAL_S, McpConnector, McpFailure
from .config import (
    TIERS,
    ConfigStore,
    Grant,
    ServerConfig,
    ToolRecord,
    parse_new_server,
)
from .vault import SecretTooLong, SecretVault, secret_name

logger = logging.getLogger(__name__)

# What a server's tool is suggested to be when the server calls it read-only. A suggestion for the
# screen only: the user's choice decides.
SUGGESTED_FOR_READ_ONLY = "T0"


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _invalid(message: str) -> RpcError:
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


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

    def register(self, router: Router) -> None:
        methods = {
            "integrations.list": self._list,
            "integrations.add": self._add,
            "integrations.set_enabled": self._set_enabled,
            "integrations.remove": self._remove,
            "integrations.refresh": self._refresh,
            "integrations.tools": self._tools,
            "integrations.set_tier": self._set_tier,
            "integrations.audit": self._audit_list,
        }
        for method, handler in methods.items():
            router.register(method, handler)

    # --- views ----------------------------------------------------------------------------------

    def _view(self, server: ServerConfig) -> dict[str, Any]:
        """A server as the window sees it: nothing secret, only the names of what is."""
        allowed = sum(1 for tool in server.tools if server.granted_tier(tool) is not None)
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
            "status": status,
            "error": failure,
            "tool_count": len(server.tools),
            "allowed_count": allowed,
            "checked_at_ms": server.tools_checked_at_ms,
        }

    @staticmethod
    def _tool_view(server: ServerConfig, tool: ToolRecord) -> dict[str, Any]:
        tier = server.granted_tier(tool)
        changed = tier is None and tool.name in server.grants
        return {
            "name": tool.name,
            "description": tool.description,
            "tier": tier,
            "changed": changed,  # had a tier, but the server describes the tool differently now
            "suggested": SUGGESTED_FOR_READ_ONLY if tool.read_only_hint is True else None,
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
            logger.warning("Could not save the plugins: %s", type(exc).__name__)
            raise RpcError(INTERNAL_ERROR, "Could not save the plugins") from None

    # --- the methods of the window --------------------------------------------------------------

    async def _list(self, params: dict) -> dict:
        return {"servers": [self._view(server) for server in self._config.servers()]}

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
            logger.warning("Could not save the plugins: %s", type(exc).__name__)
            raise RpcError(INTERNAL_ERROR, "Could not save the plugins") from None
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

    async def _remove(self, params: dict) -> dict:
        server = self._server(params)
        removed = self._config.remove(server.id)
        if removed is None:
            return {"removed": False}
        self._failure.pop(server.id, None)
        self._locks.pop(server.id, None)
        self._forget(
            [secret_name(server.id, "env", key) for key in server.env]
            + [secret_name(server.id, "header", key) for key in server.headers]
        )
        return {"removed": True}

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
        return {"server": self._view(server), "tools": self._tool_views(server)}

    def _tool_views(self, server: ServerConfig) -> list[dict[str, Any]]:
        return [self._tool_view(server, tool) for tool in server.tools]

    async def _tools(self, params: dict) -> dict:
        return {"tools": self._tool_views(self._server(params))}

    async def _set_tier(self, params: dict) -> dict:
        server = self._server(params)
        name = _str(params, "tool")
        tier = params.get("tier")
        if tier is not None and tier not in TIERS:
            raise _invalid("tier must be T0, T1, T2, T3 or null")
        tool = server.tool(name)
        if tool is None:
            raise _invalid("There is no such tool")
        if tier is None:
            server.grants.pop(name, None)
        else:
            # Tied to the tool as it is now: the one the user is looking at.
            server.grants[name] = Grant(tier, tool.fingerprint)
        self._save()
        return {"tool": self._tool_view(server, tool)}

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
                }
                for entry in self._audit.recent(limit)
            ]
        }

    # --- the hub the assistant calls tools through ----------------------------------------------

    def _usable(self, key: str) -> tuple[ServerConfig, ToolRecord, Tier] | None:
        """The server, the tool and the tier of a tool that may be used now: its plugin is on, it
        is a tool the plugin has, and the user gave it a tier as it is described now."""
        parsed = _parse_key(key)
        if parsed is None:
            return None
        server = self._config.get(parsed[0])
        if server is None or not server.enabled:
            return None
        tool = server.tool(parsed[1])
        if tool is None:
            return None
        tier = server.granted_tier(tool)
        return None if tier is None else (server, tool, Tier[tier])

    def offered(self) -> list[PluginTool]:
        tools = []
        for server in self._config.servers():
            if not server.enabled:
                continue
            for tool in server.tools:
                tier = server.granted_tier(tool)
                if tier is not None:
                    tools.append(
                        PluginTool(
                            key=tool_key(server.id, tool.name),
                            server=server.name,
                            name=tool.name,
                            description=tool.description,
                            schema=tool.input_schema,
                            tier=Tier[tier],
                        )
                    )
        return tools

    def tier_of(self, key: str) -> Tier | None:
        usable = self._usable(key)
        return None if usable is None else usable[2]

    def _names(self, key: str) -> tuple[str, str]:
        """What the audit calls a tool: its server's name and its own, as far as they are known."""
        parsed = _parse_key(key)
        server = self._config.get(parsed[0]) if parsed else None
        if parsed is None or server is None:
            return ("?", "?")
        return (server.name, parsed[1])

    def record_unrun(self, key: str, *, outcome: str) -> None:
        server_name, tool_name = self._names(key)
        usable = self._usable(key)
        self._audit.add(
            server=server_name,
            tool=tool_name,
            tier=None if usable is None else usable[2].name,
            approver="none",
            outcome=outcome,
        )

    async def call(self, key: str, arguments: Mapping[str, Any], *, approver: str) -> PluginResult:
        server_name, tool_name = self._names(key)
        usable = self._usable(key)

        def end(outcome: str, result: PluginResult, tier: Tier | None) -> PluginResult:
            self._audit.add(
                server=server_name,
                tool=tool_name,
                tier=None if tier is None else tier.name,
                approver=approver if outcome not in ("blocked",) else "none",
                outcome=outcome,
            )
            return result

        if usable is None:
            return end("blocked", PluginResult(False, "blocked"), None)
        server, tool, tier = usable
        # A call that needs a yes is made with one. The assistant checks this at its own door
        # (`ToolRegistry.check`); here it cannot be forgotten.
        if (tier >= Tier.T2 and approver != "user") or approver not in ("auto", "user"):
            return end("blocked", PluginResult(False, "blocked"), tier)
        if not isinstance(arguments, Mapping) or len(
            json.dumps(arguments, ensure_ascii=False, default=str)
        ) > MAX_ARGUMENT_CHARS:
            return end("blocked", PluginResult(False, "blocked"), tier)
        try:
            outcome = await self._connector.call_tool(
                server,
                tool.name,
                dict(arguments),
                expected=tool.fingerprint,
                timeout_s=self._call_timeout_s,
            )
        except McpFailure as failure:
            if failure.fresh is not None and self._config.get(server.id) is server:
                # The server describes the tool differently now: the user decides again.
                server.tools = [failure.fresh if t.name == tool.name else t for t in server.tools]
                try:
                    self._config.save()
                except OSError as exc:
                    logger.warning("Could not save the plugins: %s", type(exc).__name__)
            if failure.kind in ("changed", "gone"):
                return end("blocked", PluginResult(False, failure.kind), tier)
            self._failure[server.id] = failure.kind
            result = PluginResult(False, failure.kind)
            return end("timeout" if failure.kind == "timeout" else "error", result, tier)
        self._failure.pop(server.id, None)
        if outcome.is_error:
            result = PluginResult(False, "tool_error", outcome.text, outcome.truncated)
            return end("error", result, tier)
        return end("ok", PluginResult(True, None, outcome.text, outcome.truncated), tier)
