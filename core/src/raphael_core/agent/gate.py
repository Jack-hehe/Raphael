"""The one gate every tool call goes through, whoever makes it.

Two things make tool calls: the fast tier (the assistant answering a request, `service`) and the
background worker (`raphael_core.background`). Neither reaches a tool any other way. The gate holds
the `ToolRegistry` (`policy`), so the tier of a tool is asked of it each time and a tool with no tier
is unknown and cannot run; and it holds the door to the plugins' tools (`plugins.PluginHub`):

- `tier_now` is the tier a tool has this moment, which is what a call is judged by, not the tier it
  had when it was offered or when its question was asked;
- `check` is `ToolRegistry.check`: a tool at T2 or above does not pass without the `Approval` that
  only a yes to that very action can make; and no command for this computer (opening anything, the
  volume, the lock...) passes for the worker, which runs while the user is away;
- `call_plugin` checks, then calls through the hub, which checks the tool once more (it is still the
  one the user gave the tier to) and writes the audit whatever happens, with the task that made the
  call; a call the gate refuses is made as a refused one, so it is written down too;
- `record_builtin` writes the audit of one of Raphael's own lookups (the weather, a web search,
  the time) for a task.

The audit holds a time, a tool, a tier, who approved and how it ended; never an argument or a result.
Nothing here logs a name, an argument or a result.
"""

import logging
import sqlite3
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from ..clock import wall_clock_ms
from ..integrations.audit import AuditLog
from .commands import COMMANDS
from .plugins import PluginHub, PluginResult
from .policy import Action, Approval, NotApproved, Tier, ToolRegistry, UnknownTool

if TYPE_CHECKING:
    from ..integrations.pool import McpPool

logger = logging.getLogger(__name__)

# Raphael's own lookups. They only read, so they are T0. The audit calls their "server" this.
WEB_TOOLS = ("weather", "web_search", "local_time")
BUILTIN_SERVER = "Raphael"


class ToolGate:
    """The registry of tiers and the door to the plugins' tools. Built once for the app and shared
    by the fast tier and the background worker.

    `plugins` is the hub of the tools the user connected (none: only Raphael's own exist). `conn` is
    the open database, for the audit of the built-in lookups; without it they are not audited.
    """

    def __init__(
        self,
        plugins: PluginHub | None = None,
        *,
        conn: sqlite3.Connection | None = None,
        now_ms: Callable[[], int] = wall_clock_ms,
    ) -> None:
        self._plugins = plugins
        self._registry = ToolRegistry(
            {name: command.tier for name, command in COMMANDS.items()},
            plugin_tier=None if plugins is None else plugins.tier_of,
        )
        for name in WEB_TOOLS:
            self._registry.register(name, Tier.T0)
        self._audit = None if conn is None else AuditLog(conn, now_ms)

    @property
    def plugins(self) -> PluginHub | None:
        return self._plugins

    # --- the registry ---------------------------------------------------------------------------

    def tier(self, name: str) -> Tier:
        """The tier of a tool or command now. UnknownTool if it has none."""
        return self._registry.tier(name)

    def tier_now(self, name: str) -> Tier | None:
        """The tier a tool has now, or None if it has none (a plugin's tool that was blocked, whose
        plugin was turned off or that the plugin describes differently since)."""
        try:
            return self._registry.tier(name)
        except UnknownTool:
            return None

    def needs_confirmation(self, name: str) -> bool:
        return self._registry.needs_confirmation(name)

    def check(self, action: Action, approval: Approval | None, *, worker: bool = False) -> None:
        """Let `action` through: UnknownTool if it has no tier, NotApproved if it is at T2 or above
        without a yes for this very action that has not been used, and NotApproved for a command
        for this computer (opening anything, the volume, the lock...) when `worker` is true: those
        are for the user at the keyboard, never for a task that runs while they are away."""
        if worker and action.tool in COMMANDS:
            raise NotApproved(action.tool)
        self._registry.check(action, approval)

    # --- the plugins' tools ---------------------------------------------------------------------

    async def call_plugin(
        self,
        key: str,
        arguments: Mapping[str, Any],
        *,
        approval: Approval | None = None,
        action: Action | None = None,
        task_id: int | None = None,
        pool: "McpPool | None" = None,
    ) -> PluginResult:
        """Call the tool `key` of a plugin through the door. `approval` is the yes for it, if it had
        to be asked about; a call that the door refuses is not made, and the hub writes down that it
        was refused. `pool` keeps the connection to the tool's server open for the calls after
        this one. Never raises for a failure of the call: the result says what came of it (a
        cancellation is the exception, as everywhere)."""
        plugins = self._plugins
        if plugins is None:
            return PluginResult(False, "blocked")
        action = action or Action(key, arguments)
        try:
            self._registry.check(action, approval)
            approver = "auto" if approval is None else "user"
        except (UnknownTool, NotApproved):
            approver = "none"
        kept = {} if pool is None else {"pool": pool}  # a hub of a test may not know it
        try:
            return await plugins.call(key, arguments, approver=approver, task_id=task_id, **kept)
        except Exception as exc:  # not the hub's own failures, which it reports in the outcome
            logger.warning("A plugin call failed unexpectedly: %s", type(exc).__name__)
            return PluginResult(False, "error")

    def record_unrun(self, key: str, *, outcome: str, task_id: int | None = None) -> None:
        """The audit of a call that was asked about and not made ("denied" or "expired")."""
        if self._plugins is None:
            return
        try:
            self._plugins.record_unrun(key, outcome=outcome, task_id=task_id)
        except Exception as exc:
            logger.warning("Could not write down a call that was not made: %s", type(exc).__name__)

    # --- Raphael's own lookups ------------------------------------------------------------------

    def record_builtin(self, name: str, *, outcome: str, task_id: int | None = None) -> None:
        """The audit of one of Raphael's own lookups, made for a task. `outcome` is "ok", "error",
        "timeout" or "blocked"; it needs no yes (T0), so the approver is "auto" unless it was
        refused."""
        if self._audit is None:
            return
        tier = self.tier_now(name)
        self._audit.add(
            server=BUILTIN_SERVER,
            tool=name,
            tier=None if tier is None else tier.name,
            approver="none" if outcome == "blocked" else "auto",
            outcome=outcome,
            task_id=task_id,
        )
