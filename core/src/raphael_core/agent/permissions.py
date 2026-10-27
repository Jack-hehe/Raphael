"""Permission cards: the card a typed private chat shows for an action that needs the owner's yes, in
that chat and nowhere else, and what the owner's three answers on it do.

The gate decides that an action needs a yes (`gate`, `safety`: the owner's rules, the tiers, the global
switch, the steps on the desktop that send, delete or buy). The question itself is the one every path
already has, `policy.Confirmations`: it mints the one `Approval`, for the digest of the action asked
about, and only a yes in time for that question does. A card is the owner's view of that question, kept
in the history of the chat that holds it:

    pending ── 允許一次 ──> allowed         pending ── 一律允許 ──> always (a narrow rule was kept)
       |                                      |
       +── 拒絕 ──> denied   +── time ──> expired   +── Stop, a newer request ──> cancelled

A card names the service and the tool, says the exact action in plain words (`summary`, the question
as it is asked), says which of the owner's rules had it asked (`reason`) and carries the digest of the
action. It offers 一律允許 (`always`) only where that can take effect (`ToolGate.always_allow`); where a
rule asks first, Ask first has the last word over Always and the card does not pretend otherwise.

There are two kinds of cards by who delivers the answer:

- `ask`: the question of the fast tier or of the run on the desktop while the turn that asked is still
  there. Its answer is `agent.confirm`, as it always was (the shell holds the question, and the card
  follows what comes of it); the card only keeps the history of it.
- `run`: a question of the run on the desktop that carries on after the owner gave the computer back
  (`computer.handback`). Nobody is at that question: the card is its only door, `integrations.card_allow`,
  `integrations.card_always` and `integrations.card_deny {id, digest?}`, and it waits as long as an
  unattended question does (`policy.UNATTENDED_WAIT_S`, about ten minutes). The answer goes to the run
  that waits on that very question with the digest of the card, so a card that is not the question any
  more answers nothing, a replay finds the card answered, and a core that started again has no run:
  the card is expired.

Only a private chat typed at this computer is shown a card. A room, an exchange between members, a quick
question, a spoken request and a message from a phone never get one, and never carry its payload.
Nothing here logs an action, its arguments or the words of a rule.
"""

import logging
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..clock import wall_clock_ms
from ..protocol import invalid_params
from .policy import UNATTENDED_WAIT_MS

if TYPE_CHECKING:
    from ..computer.run import ComputerRuns

logger = logging.getLogger(__name__)

# The only moves a card's state makes: from pending, and only once.
STATES = ("pending", "allowed", "always", "denied", "expired", "cancelled")
_COLUMNS = ("id, turn_id, member_id, conversation_id, source, kind, service, tool, summary, reason, digest, always,"
            " language, state, created_at_ms, expires_at_ms")


@dataclass(frozen=True)
class PermissionCard:
    id: str
    turn_id: int
    member_id: int
    conversation_id: int | None
    source: str  # "ask" (answered with agent.confirm) or "run" (answered through the card)
    kind: str  # "tool" (a connection's tool), "app" (the first use of an app) or "step" (a step that sends, deletes, buys)
    service: str
    tool: str
    summary: str
    reason: str
    digest: str
    always: bool
    language: str
    state: str
    created_at_ms: int
    expires_at_ms: int

    def view(self) -> dict:
        """The card as the window draws it."""
        return {"id": self.id, "source": self.source, "kind": self.kind, "service": self.service, "tool": self.tool,
                "summary": self.summary, "reason": self.reason or None, "digest": self.digest, "always": self.always,
                "state": self.state, "expires_at_ms": self.expires_at_ms}


def _card(row: sqlite3.Row) -> PermissionCard:
    return PermissionCard(row["id"], row["turn_id"], row["member_id"], row["conversation_id"], row["source"],
                          row["kind"], row["service"], row["tool"], row["summary"], row["reason"], row["digest"],
                          bool(row["always"]), row["language"], row["state"], row["created_at_ms"], row["expires_at_ms"])


class PermissionCardStore:
    """The cards, in the database. The questions they follow live in memory only: a card that was
    waiting when the core last ended is over, and is expired."""

    def __init__(self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms) -> None:
        self._conn, self._now_ms = conn, now_ms
        with conn:
            conn.execute("UPDATE permission_cards SET state='expired', updated_at_ms=? WHERE state='pending'", (now_ms(),))

    def open(self, *, card_id: str, turn_id: int, member_id: int, conversation_id: int | None, source: str, kind: str,
             service: str, tool: str, summary: str, reason: str, digest: str, always: bool, language: str,
             wait_ms: int) -> PermissionCard:
        now = self._now_ms()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO permission_cards (id, turn_id, member_id, conversation_id, source, kind, service,"
                " tool, summary, reason, digest, always, language, state, created_at_ms, updated_at_ms, expires_at_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (card_id, turn_id, member_id, conversation_id, source, kind, service, tool, summary, reason, digest,
                 int(always), language, now, now, now + wait_ms))
        return self.get(card_id)  # type: ignore[return-value]

    def expire_due(self) -> None:
        """Cards whose time ran out, or whose turn is over without an answer (the question was
        dropped, the turn was retracted), are expired."""
        now = self._now_ms()
        with self._conn:
            self._conn.execute(
                "UPDATE permission_cards SET state='expired', updated_at_ms=? WHERE state='pending' AND (expires_at_ms<=?"
                " OR turn_id NOT IN (SELECT id FROM agent_turns WHERE status='confirm') AND source='ask')", (now, now))

    def get(self, card_id: str) -> PermissionCard | None:
        self.expire_due()
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM permission_cards WHERE id=?", (card_id,)).fetchone()
        return None if row is None else _card(row)

    def for_turns(self, turn_ids: Iterable[int]) -> dict[int, list[PermissionCard]]:
        self.expire_due()
        ids = sorted({int(turn_id) for turn_id in turn_ids})
        found: dict[int, list[PermissionCard]] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM permission_cards WHERE turn_id IN ({','.join('?' * len(chunk))})"
                " ORDER BY created_at_ms, rowid", chunk)
            for card in map(_card, rows):
                found.setdefault(card.turn_id, []).append(card)
        return found

    def move(self, card_id: str, to: str) -> PermissionCard | None:
        """Take a pending card to `to`; None (and nothing changed) when it is not pending: a card is
        answered once."""
        if to not in STATES or to == "pending":
            raise ValueError("not a state a card moves to")
        with self._conn:
            moved = self._conn.execute(
                "UPDATE permission_cards SET state=?, updated_at_ms=? WHERE id=? AND state='pending'",
                (to, self._now_ms(), card_id)).rowcount
        return self.get(card_id) if moved else None

    def settle_turn(self, turn_id: int, to: str) -> list[PermissionCard]:
        """The cards of a turn that still wait, taken to `to` (the turn is over). The cards that changed."""
        with self._conn:
            ids = [row["id"] for row in self._conn.execute(
                "SELECT id FROM permission_cards WHERE turn_id=? AND state='pending'", (turn_id,))]
            for card_id in ids:
                self._conn.execute("UPDATE permission_cards SET state=?, updated_at_ms=? WHERE id=? AND state='pending'",
                                   (to, self._now_ms(), card_id))
        return [card for card in (self.get(card_id) for card_id in ids) if card is not None]


def qualifies(*, source: str, quick: bool, nested: bool, scope: str) -> bool:
    """Whether a request is one whose chat is shown a card: typed in the app at this computer, in a
    private chat, not a quick question and not a request one member handed another."""
    return source in ("app", "connector") and not quick and not nested and scope == "private"


class PermissionCards:
    """The cards' flow. `computer` is the registry of runs, to which the answer of a `run` card goes."""

    def __init__(self, conn: sqlite3.Connection, computer: "ComputerRuns | None" = None, *,
                 notify: Callable[[str, dict], None] | None = None, now_ms: Callable[[], int] = wall_clock_ms) -> None:
        self.store = PermissionCardStore(conn, now_ms)
        self._conn, self._computer, self._notify = conn, computer, notify

    def register(self, router) -> None:
        router.register("integrations.card_allow", self._allow)
        router.register("integrations.card_always", self._always)
        router.register("integrations.card_deny", self._deny)

    # --- what the agent does ----------------------------------------------------------------------

    def show(self, *, card_id: str, turn_id: int, member_id: int, conversation_id: int | None, source: str, kind: str,
             service: str, tool: str, summary: str, reason: str, digest: str, always: bool, language: str,
             wait_ms: int) -> PermissionCard:
        card = self.store.open(card_id=card_id, turn_id=turn_id, member_id=member_id, conversation_id=conversation_id,
                               source=source, kind=kind, service=service, tool=tool, summary=summary, reason=reason,
                               digest=digest, always=always, language=language, wait_ms=wait_ms)
        self._changed(card)
        return card

    def answered(self, card_id: str, state: str) -> PermissionCard | None:
        """The owner's answer (or the end of the question) as the history keeps it. Once: a card that
        no longer waits stays as it is."""
        moved = self.store.move(card_id, state)
        if moved is not None:
            self._changed(moved)
        return moved

    def settle(self, turn_id: int, state: str) -> None:
        for card in self.store.settle_turn(turn_id, state):
            self._changed(card)

    def views(self, turn_ids: Iterable[int]) -> dict[int, list[dict]]:
        return {turn_id: [card.view() for card in cards] for turn_id, cards in self.store.for_turns(turn_ids).items()}

    def get(self, card_id: str) -> PermissionCard | None:
        return self.store.get(card_id)

    # --- the window's methods (the cards a run waits on) --------------------------------------------

    def _card(self, params: dict) -> PermissionCard:
        card_id = params.get("id")
        card = self.store.get(card_id) if isinstance(card_id, str) and card_id else None
        if card is None or self._conn.execute("SELECT 1 FROM agent_turns WHERE id=?", (card.turn_id,)).fetchone() is None:
            raise invalid_params("There is no such card")
        digest = params.get("digest")
        if digest is not None and not isinstance(digest, str):
            raise invalid_params("digest must be text")
        return card

    async def _allow(self, params: dict) -> dict:
        return await self._answer(params, approved=True, always=False)

    async def _always(self, params: dict) -> dict:
        return await self._answer(params, approved=True, always=True)

    async def _deny(self, params: dict) -> dict:
        return await self._answer(params, approved=False, always=False)

    async def _answer(self, params: dict, *, approved: bool, always: bool) -> dict:
        """The owner's answer on a card the run waits on. It goes to the run that waits on that very
        question, with the card's digest; the card moves once, and a card that is not pending, or not the
        question of a run any more, answers nothing."""
        card = self._card(params)
        digest = params.get("digest")
        if card.state != "pending" or card.source != "run":
            return {"card": card.view(), "answered": False}
        if (digest is not None and digest != card.digest) or (always and not card.always):
            return {"card": card.view(), "answered": False}  # not this card's action, or no Always on it
        run = None if self._computer is None else self._computer.for_question(card.id)
        if run is None:
            gone = self.store.move(card.id, "expired") or card  # the run is over: nothing waits for this yes
            self._changed(gone)
            return {"card": gone.view(), "answered": False}
        # The card moves first (once: a replay finds it answered), and the run is then told, with the
        # digest of the question, which is the card's. Nothing awaits in between.
        moved = self.store.move(card.id, "denied" if not approved else "always" if always else "allowed")
        if moved is None:
            return {"card": (self.store.get(card.id) or card).view(), "answered": False}
        if not run.answer(card.id, approved, always, card.digest):  # cannot be: it waits on this very question
            logger.warning("A permission card's run did not take its answer")
        self._changed(moved)
        return {"card": moved.view(), "answered": True}

    def _changed(self, card: PermissionCard) -> None:
        if self._notify is not None:
            self._notify("agent.turn", {"turn_id": card.turn_id, "member_id": card.member_id})


# The wait of a card nobody sits at (the run carries on after the owner gave the computer back).
RUN_WAIT_MS = UNATTENDED_WAIT_MS
