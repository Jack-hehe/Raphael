"""The policy: what the assistant may do at once, and what it must be told yes to first.

Every tool and command has a tier:

- T0 reads something and changes nothing (the weather, a web search, the time).
- T1 changes this computer in a way that is easy to undo (a page opened, the volume, a media key,
  the screen locked).
- T2 changes state that cannot simply be put back (the clipboard overwritten).
- T3 cannot be undone at all.

T0 and T1 run at once. T2 and T3 are never run on the strength of a spoken request alone: the
assistant asks, the overlay shows a yes and a no, and the action runs only when the answer is yes,
within thirty seconds, for that one question. `ToolRegistry.check` is the single door the runner
goes through: it refuses a T2 or T3 action that has no `Approval`, and an `Approval` can only come
from `Confirmations.resolve`, for the action that was asked about, once.
"""

import enum
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

# The names of the tools of plugins begin with this (see ToolRegistry).
PLUGIN_PREFIX = "mcp:"
# How long a question waits for its answer; after this a yes is too late.
CONFIRM_TIMEOUT_S = 30.0
CONFIRM_TIMEOUT_MS = round(CONFIRM_TIMEOUT_S * 1000)
# Confirmations that ran out are remembered (so that a late yes is told it was late, in the
# language it was asked in), but not for ever.
_EXPIRED_MEMORY = 64


class Tier(enum.IntEnum):
    T0 = 0  # reads, changes nothing
    T1 = 1  # changes this computer, easily undone
    T2 = 2  # changes state that cannot simply be put back: asks first
    T3 = 3  # cannot be undone: asks first


class UnknownTool(KeyError):
    """A tool or command nobody registered. It has no tier, so it cannot run."""


class NotApproved(Exception):
    """An action at T2 or above was about to run without a yes for it."""


@dataclass(frozen=True, eq=False)
class Action:
    """A tool or command to run, with its arguments. Compared by identity: an approval is for this
    action, not for one that looks the same."""

    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)


_MINT = object()


class Approval:
    """A yes, for one action. Only `Confirmations.resolve` makes one. It lets its action run once."""

    __slots__ = ("action", "pending_id", "spent")

    def __init__(self, mint: object, pending_id: str, action: Action) -> None:
        if mint is not _MINT:
            raise TypeError("An Approval comes from Confirmations.resolve only")
        self.pending_id = pending_id
        self.action = action
        self.spent = False


class ToolRegistry:
    """The tier of each tool, and the check an action passes before it runs.

    The tools of the plugins the user connected (names that begin with "mcp:") are not registered:
    their tier is the user's, can change at any time and can be taken back, so `plugin_tier` is
    asked each time, and returns None for a tool that has no tier now. Such a tool is unknown, like
    any other without one. A name that begins with "mcp:" can never be registered here, so no
    built-in tool can be shadowed by a plugin's, nor the other way round.
    """

    def __init__(
        self,
        tiers: Mapping[str, Tier] | None = None,
        *,
        plugin_tier: Callable[[str], Tier | None] | None = None,
        ask_first: Callable[[str, Tier], bool] | None = None,
    ) -> None:
        self._tiers: dict[str, Tier] = {}
        self._plugin_tier = plugin_tier
        self._ask_first = ask_first
        for name, tier in (tiers or {}).items():
            self.register(name, tier)

    def register(self, name: str, tier: Tier) -> None:
        """Give a tool its tier. ValueError if it has one already: registering a tool again could
        only make it safer on paper."""
        if name in self._tiers:
            raise ValueError(f"The tool {name!r} is registered already")
        if name.startswith(PLUGIN_PREFIX):
            raise ValueError(f"The name {name!r} is for the tools of plugins")
        self._tiers[name] = Tier(tier)

    def tier(self, name: str) -> Tier:
        if name.startswith(PLUGIN_PREFIX):
            tier = self._plugin_tier(name) if self._plugin_tier is not None else None
            if tier is None:
                raise UnknownTool(name)
            return Tier(tier)
        try:
            return self._tiers[name]
        except KeyError:
            raise UnknownTool(name) from None

    def needs_confirmation(self, name: str) -> bool:
        tier = self.tier(name)
        return tier >= Tier.T2 or bool(self._ask_first and self._ask_first(name, tier))

    def check(self, action: Action, approval: Approval | None) -> None:
        """Let `action` through: UnknownTool if it has no tier, NotApproved if it is at T2 or above
        and `approval` is not a yes for this very action that has not been used. The approval is
        spent by the check."""
        if not self.needs_confirmation(action.tool):
            return
        if not isinstance(approval, Approval) or approval.action is not action or approval.spent:
            raise NotApproved(f"{action.tool} needs a yes")
        approval.spent = True


# --- the questions waiting for a yes -------------------------------------------------------------


class Outcome(enum.Enum):
    APPROVED = "approved"  # a yes, in time
    DENIED = "denied"  # a no, in time
    EXPIRED = "expired"  # an answer, but too late
    UNKNOWN = "unknown"  # nobody asked this, or it is gone


@dataclass
class Pending:
    """A question that waits for its yes or no."""

    id: str
    session_id: str
    turn_id: int
    action: Action
    tier: Tier
    language: str  # the language it was asked in, which the answer is given in
    created_at: float


@dataclass
class Resolution:
    outcome: Outcome
    pending: Pending | None = None  # None when the outcome is UNKNOWN
    approval: Approval | None = None  # set when, and only when, the outcome is APPROVED


class Confirmations:
    """The questions that wait for a yes. `clock` is a monotonic clock in seconds.

    Used on the event loop only. A question lives CONFIRM_TIMEOUT_S; it is answered once, and
    taken out when it is answered, cancelled or has run out.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        timeout_s: float = CONFIRM_TIMEOUT_S,
    ) -> None:
        self._clock = clock
        self._timeout_s = timeout_s
        self._pending: dict[str, Pending] = {}
        self._expired: OrderedDict[str, Pending] = OrderedDict()

    @property
    def waiting(self) -> int:
        """How many questions wait for an answer (some may have run out; see `sweep`)."""
        return len(self._pending)

    def open(
        self, session_id: str, turn_id: int, action: Action, tier: Tier, language: str
    ) -> Pending:
        pending = Pending(
            id=str(uuid.uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            action=action,
            tier=tier,
            language=language,
            created_at=self._clock(),
        )
        self._pending[pending.id] = pending
        return pending

    def resolve(self, confirm_id: object, approved: bool) -> Resolution:
        """Answer a question. Only a yes that comes in time for a question that is still there is
        APPROVED; it comes with the approval for its action. The question is gone afterwards."""
        self.sweep()
        if not isinstance(confirm_id, str):
            return Resolution(Outcome.UNKNOWN)
        late = self._expired.get(confirm_id)
        if late is not None:
            return Resolution(Outcome.EXPIRED, late)
        pending = self._pending.pop(confirm_id, None)
        if pending is None:
            return Resolution(Outcome.UNKNOWN)
        if not approved:
            return Resolution(Outcome.DENIED, pending)
        return Resolution(Outcome.APPROVED, pending, Approval(_MINT, pending.id, pending.action))

    def sweep(self) -> list[Pending]:
        """Take out the questions that have run out, and return them: the oldest first."""
        now = self._clock()
        overdue = [p for p in self._pending.values() if now - p.created_at >= self._timeout_s]
        for pending in overdue:
            del self._pending[pending.id]
            self._expired[pending.id] = pending
        while len(self._expired) > _EXPIRED_MEMORY:
            self._expired.popitem(last=False)
        return overdue

    def cancel_session(self, session_id: str) -> list[Pending]:
        """Deny the questions of a session that was cancelled; return them. Their yes, if it still
        comes, finds nothing to approve."""
        return self.cancel_where(lambda pending: pending.session_id == session_id)

    def cancel_all(self) -> list[Pending]:
        """Deny every question that waits; return them."""
        return self.cancel_where(lambda pending: True)

    def cancel_where(self, replaced: Callable[[Pending], bool]) -> list[Pending]:
        """Deny the waiting questions that `replaced` picks; return them."""
        self.sweep()
        cancelled = [p for p in self._pending.values() if replaced(p)]
        for pending in cancelled:
            del self._pending[pending.id]
        return cancelled
