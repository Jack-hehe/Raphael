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
- `call_plugin` checks, then calls through the hub, which checks the tool once more (it is still
  on, of a plugin that is on) and writes the audit whatever happens, with the task that made the
  call; a call the gate refuses is made as a refused one, so it is written down too;
- `record_builtin` writes the audit of one of Raphael's own lookups (the weather, a web search,
  the time, the memory) for a task;
- `screen` asks the owner's safety rules (`safety`) about an action that changes something, before
  it is made: a plugin's tool that its server does not say only reads (`reads_only`), a step on the
  desktop, the creation of a member (`create_member`; never a command for this computer: see
  `safety`). Its verdict adds to the tiers and never
  takes from them:
  a rule may refuse an action or have it asked about, and a rule that allows an action waives only
  the questions of the rules (`safety.needs_yes`), but for the narrow rule the owner's own click on
  「一律允許」 made for one tool (`always_allow`, `waived`): that tool no longer asks for the tier T2 or
  for the global 先詢問 switch, and a rule that asks (Ask first) or forbids (Never) still wins over it.
  Without rules (or without a guard, as in most tests) every action passes it, with no call.

The memory's tools are Raphael's own too: reading a note (`memory_read`) and searching the past
turns (`history_search`) only read, so they are T0; writing and deleting a note (`memory_write`,
`memory_delete`) change only Raphael's own memory, which the user sees and can undo in the settings,
so they are T1: they run at once, with no yes asked for. So are the tools that reach the owner's team
(`team`): `ask_member`, which has another member answer, and `create_member`, which adds one at the
owner's request (and which the safety rules judge first, like a plugin's tool that changes something).

The audit holds a time, a tool, a tier, who approved and how it ended; never an argument or a result.
Nothing here logs a name, an argument or a result.
"""

import logging
import sqlite3
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from ..clock import wall_clock_ms
from ..computer.tools import GATE_TIERS as COMPUTER_TIERS
from ..integrations.audit import AuditLog
from ..protocol import RpcError
from .commands import COMMANDS
from .harness_tools import TIERS as HARNESS_TIERS
from .plugins import PluginHub, PluginResult
from .ledger import Ledger
from .policy import Action, Approval, NotApproved, Tier, ToolRegistry, UnknownTool, action_digest
from .team import TEAM_TIER, TEAM_TOOLS
from .safety import ALLOW, PASS, Proposed, SafetyGuard, Screening, narrow_scope

if TYPE_CHECKING:
    from ..integrations.pool import McpPool

logger = logging.getLogger(__name__)

# Raphael's own lookups. They only read, so they are T0. The audit calls their "server" this.
# `web_read` reads a page (by the reader the owner chose, `raphael_core.services`).
WEB_TOOLS = ("weather", "web_search", "local_time", "web_read")
# Reading a file the owner handed over with the request (an image, a PDF): T0 too. A task is
# offered it only when it has files, and it reads only those, by their number: never a path.
ATTACHMENT_TOOLS = ("read_attachment",)
BUILTIN_SERVER = "Raphael"
# The memory's tools (raphael_core.memory), by tier: the ones that read, and the ones that change it.
MEMORY_READ_TOOLS = ("memory_read", "history_search")
MEMORY_WRITE_TOOLS = ("memory_write", "memory_delete")


class ToolGate:
    """The registry of tiers and the door to the plugins' tools. Built once for the app and shared
    by the fast tier and the background worker.

    `plugins` is the hub of the tools the user connected (none: only Raphael's own exist). `conn` is
    the open database, for the audit of the built-in lookups; without it they are not audited.
    `safety` is the owner's safety rules with their judge (none: no rule is read).
    """

    def __init__(
        self,
        plugins: PluginHub | None = None,
        *,
        conn: sqlite3.Connection | None = None,
        now_ms: Callable[[], int] = wall_clock_ms,
        safety: SafetyGuard | None = None,
    ) -> None:
        self._plugins = plugins
        self._safety = safety
        from ..brain.settings import SettingsStore
        self._settings = None if conn is None else SettingsStore(conn)
        self._registry = ToolRegistry(
            {name: command.tier for name, command in COMMANDS.items()},
            plugin_tier=None if plugins is None else plugins.tier_of,
            ask_first=lambda name, tier: self.ask_first and tier > Tier.T0 and name != "ask_member",
        )
        for name in (*WEB_TOOLS, *MEMORY_READ_TOOLS, *ATTACHMENT_TOOLS):
            self._registry.register(name, Tier.T0)
        # The steps of a run on the owner's desktop (`raphael_core.computer`).
        for name, tier in COMPUTER_TIERS.items():
            self._registry.register(name, tier)
        for name in MEMORY_WRITE_TOOLS:
            self._registry.register(name, Tier.T1)
        # The tools that reach the owner's team (`team`): asking a member, adding one.
        for name in TEAM_TOOLS:
            self._registry.register(name, TEAM_TIER)
        for name, tier in HARNESS_TIERS.items():
            self._registry.register(name, tier)
        self._registry.register("cli_permission", Tier.T2)
        self._registry.register("team_admin_batch", Tier.T2)
        self._audit = None if conn is None else AuditLog(conn, now_ms)

    @property
    def plugins(self) -> PluginHub | None:
        return self._plugins

    @property
    def ask_first(self) -> bool:
        return self._settings is not None and self._settings.get("action_mode") == "ask"

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
        without a yes for this very action that has not been used (or with a yes whose digest is no
        longer the action's), and NotApproved for a command
        for this computer (opening anything, the volume, the lock...) or a step of a run on the
        desktop when `worker` is true: those are for the user at the keyboard, never for a task
        that runs while they are away. A tool the owner said Always to (`waived`) needs no yes."""
        if worker and (action.tool in COMMANDS or action.tool in COMPUTER_TIERS):
            raise NotApproved(action.tool)
        self._door(action, approval)

    def check_for(self, action: Action, tool: str, arguments: Mapping[str, Any], approval: Approval | None) -> None:
        """`check`, for the call about to be made: `action` must be that very call, the tool and the
        inputs it is given now. A yes is for what was asked about, so a call whose inputs are not the
        action's (they were changed after the question) is not covered by it."""
        if action.tool != tool or action.digest != action_digest(tool, arguments):
            raise NotApproved(f"{tool} is not the action that was said yes to")
        self.check(action, approval)

    def _door(self, action: Action, approval: Approval | None) -> str:
        """The registry's check, and who said yes to the action: "user" for a yes of this very
        action or the owner's standing Always for its tool, "auto" for an action that needs none."""
        try:
            self._registry.check(action, approval)
        except NotApproved:
            if approval is None and self.waived(action):
                return "user"
            raise
        return "auto" if approval is None else "user"

    def waived(self, action: Action) -> bool:
        """Whether the owner said Always to this very tool (a narrow rule, `always_allow`) and it can be
        undone: its questions of the tier T2 and of the global 先詢問 switch are waived. The rules that ask
        and forbid are the screening's (`screen`), which every path asks before it makes a call that
        changes something."""
        tier = self.tier_now(action.tool)
        if self._safety is None or tier is None or tier >= Tier.T3:
            return False
        try:
            return self._safety.rules.always_allows(narrow_scope(action.tool))
        except sqlite3.Error:
            return False

    def always_allow(self, key: str, server: str, tool: str, language: str) -> bool:
        """The owner's 「一律允許」 on a card about the tool `key` (`tool` of `server` as they know them): a
        narrow rule of Always for that tool and no other. False when none could be made (no rules, too
        many of them, a rule that is not an Always is there already); never raises."""
        if self._safety is None:
            return False
        try:
            rule = self._safety.rules.always(narrow_scope(key), f"{server}：{tool}", language)
        except RpcError:
            return False
        except sqlite3.Error as exc:
            logger.warning("Could not keep an Always: %s", type(exc).__name__)
            return False
        return rule.verdict == ALLOW

    def check_admin_batch(self, action: Action, approval: Approval | None) -> None:
        """One single-use approval covers exactly the listed reversible admin operations."""
        from .team_admin import BATCH_TOOLS

        if action.tool != "team_admin_batch" or not action.args.get("operations"):
            raise NotApproved("team_admin_batch")
        for operation in action.args["operations"]:
            if operation["name"] not in BATCH_TOOLS or self.tier(operation["name"]) > Tier.T1:
                raise NotApproved(operation["name"])
        self.check(action, approval)

    # --- the owner's safety rules ----------------------------------------------------------------

    @property
    def safety(self) -> SafetyGuard | None:
        return self._safety

    async def screen(self, proposed: Proposed, *, language: str, ledger: Ledger) -> Screening:
        """What the owner's safety rules say of `proposed`, an action that changes something: PASS
        when there is no guard or no rule. Never raises but for a cancellation."""
        result = PASS if self._safety is None else await self._safety.screen(proposed, language=language, ledger=ledger)
        # Explicit denials win; a stored allow can never waive the owner's global quick switch, but for
        # the narrow rule the owner's own click made for this very tool (a rule that asks has won already).
        if self.ask_first and result.verdict != "deny":
            if result.narrow:
                return result
            asked = result.verdict == "ask"
            return Screening("ask", result.rule if asked else None, unchecked=result.unchecked, switch=not asked)
        return result

    def reads_only(self, server: str, tool: str) -> bool:
        """Whether the server of a plugin's tool says that the tool only reads (`readOnlyHint` and
        not destructive). Such a tool is never judged by the rules. A hub that cannot say, or a
        tool it does not know, changes things."""
        kind_of = getattr(self._plugins, "kind_of", None)
        if kind_of is None:
            return False
        try:
            return kind_of(server, tool) == "read"
        except Exception:
            return False

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
            if action.tool != key or action.digest != action_digest(key, arguments):
                raise NotApproved(key)  # the call is not the action that was said yes to
            approver = self._door(action, approval)
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
        if key in HARNESS_TIERS or key in COMMANDS:
            self.record_builtin(key, outcome=outcome, task_id=task_id, approver="none")
            return
        if self._plugins is None:
            return
        try:
            self._plugins.record_unrun(key, outcome=outcome, task_id=task_id)
        except Exception as exc:
            logger.warning("Could not write down a call that was not made: %s", type(exc).__name__)

    # --- Raphael's own lookups ------------------------------------------------------------------

    def record_builtin(self, name: str, *, outcome: str, task_id: int | None = None,
                       approver: str = "auto") -> None:
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
            approver="none" if outcome == "blocked" else approver,
            outcome=outcome,
            task_id=task_id,
        )
