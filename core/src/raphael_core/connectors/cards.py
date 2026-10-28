"""Connector cards: the card a chat shows in its own conversation when the task in it needs an app
that is not connected (Gmail, Google Calendar), and the one resume of that task once it is.

A card is a row of `connect_cards`, bound to the turn whose task asked for the app (the model's
`connect_app`). Its state moves only along `MOVES`:

    pending ── Connect ──> authorizing ──> connected
       ^                       |  |
       |                       |  +──> failed (denied, timeout, scope, state, not_configured, ...)
       +── Connect again <─────+─────> cancelled (the owner's Cancel)
    pending, failed or cancelled ── CARD_WAIT_MS ──> expired

`integrations.card_connect {id}` answers at once and signs in in the background: through the owner's
own Google authorization when it is configured (a loopback callback with PKCE and a `state`), else
through Composio; with neither, the card fails with `not_configured` and no browser opens. Connected
means the app's read tools can be called now, so a sign-in that granted less (a partial consent) is
`scope`, never connected. `integrations.card_cancel {id}` ends a sign-in under way.

When every card of a turn is connected, that turn's task resumes, once (`resume`, which the agent
supplies): the claim is one conditional update, so a replayed callback or a second Connect finds
nothing to resume. A card holds no URL, code, `state` or token, and none is logged; the window is
told of a change by `agent.turn`.
"""

import asyncio
import json
import logging
import secrets
import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from ..clock import wall_clock_ms
from ..protocol import RpcError, invalid_params
from .apps import APPS, App
from .composio import KEY
from .google import PREFIX
from .service import GOOGLE_SERVER

logger = logging.getLogger(__name__)

# How long a card waits for the owner, as a question of the background does.
CARD_WAIT_MS = 24 * 3600 * 1000

# The only moves a card's state makes.
MOVES: dict[str, tuple[str, ...]] = {
    "pending": ("authorizing", "expired"),
    "failed": ("authorizing", "expired"),
    "cancelled": ("authorizing", "expired"),
    "authorizing": ("connected", "failed", "cancelled"),
    "connected": (),
    "expired": (),
}
# What a sign-in that did not connect says, by what the provider called it. Anything else is "error".
_FAILURES = {
    "denied": "denied", "access_denied": "denied", "timeout": "timeout", "expired": "timeout",
    "state": "state", "missing_scopes": "scope", "scope": "scope", "not_configured": "not_configured",
    "missing_key": "not_configured", "needs_own_oauth_app": "not_configured",
}
_COLUMNS = ("id, turn_id, member_id, conversation_id, app, language, reconnect, state, error, tool_count, done,"
            " created_at_ms, expires_at_ms, resumed_at_ms")


@dataclass(frozen=True)
class Card:
    id: str
    turn_id: int
    member_id: int
    conversation_id: int | None
    app: str
    language: str
    reconnect: bool
    state: str
    error: str
    tool_count: int | None
    done: tuple[tuple[str, str], ...]  # (tool key, argument digest) of what the task did before it asked
    created_at_ms: int
    expires_at_ms: int
    resumed: bool

    def view(self) -> dict:
        """The card as the window draws it."""
        return {"id": self.id, "app": self.app, "name": APPS[self.app].name, "state": self.state,
                "error": self.error or None, "reconnect": self.reconnect, "tool_count": self.tool_count,
                "expires_at_ms": self.expires_at_ms, "resumed": self.resumed}


def _card(row: sqlite3.Row) -> Card:
    return Card(row["id"], row["turn_id"], row["member_id"], row["conversation_id"], row["app"], row["language"],
                bool(row["reconnect"]), row["state"], row["error"], row["tool_count"],
                tuple((key, digest) for key, digest in json.loads(row["done"])),
                row["created_at_ms"], row["expires_at_ms"], row["resumed_at_ms"] is not None)


class CardStore:
    """The cards, in the database. A sign-in that was under way when the core last ended is over:
    such a card is `failed` with `interrupted`, from which it can be connected again."""

    def __init__(self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms,
                 wait_ms: int = CARD_WAIT_MS) -> None:
        self._conn, self._now_ms, self._wait_ms = conn, now_ms, wait_ms
        with conn:
            conn.execute("UPDATE connect_cards SET state='failed', error='interrupted', updated_at_ms=?"
                         " WHERE state='authorizing'", (now_ms(),))

    def open(self, *, turn_id: int, member_id: int, conversation_id: int | None, app: str, language: str,
             reconnect: bool, done: Iterable[tuple[str, str]] = ()) -> Card:
        """The card of `app` for `turn_id`: a new one, or the one the turn has (a task asks once per app)."""
        now = self._now_ms()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO connect_cards (id, turn_id, member_id, conversation_id, app, language, reconnect,"
                " state, done, created_at_ms, updated_at_ms, expires_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (secrets.token_hex(8), turn_id, member_id, conversation_id, app, language, int(reconnect),
                 json.dumps(sorted(set(done))), now, now, now + self._wait_ms))
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM connect_cards WHERE turn_id=? AND app=?",
                                 (turn_id, app)).fetchone()
        return _card(row)

    def expire_due(self) -> None:
        """Cards whose time ran out while they waited for the owner are expired."""
        now = self._now_ms()
        with self._conn:
            self._conn.execute("UPDATE connect_cards SET state='expired', updated_at_ms=?"
                               " WHERE state IN ('pending','failed','cancelled') AND expires_at_ms<=?", (now, now))

    def get(self, card_id: str) -> Card | None:
        self.expire_due()
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM connect_cards WHERE id=?", (card_id,)).fetchone()
        return None if row is None else _card(row)

    def for_turns(self, turn_ids: Iterable[int]) -> dict[int, list[Card]]:
        self.expire_due()
        ids = sorted({int(turn_id) for turn_id in turn_ids})
        found: dict[int, list[Card]] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = self._conn.execute(f"SELECT {_COLUMNS} FROM connect_cards WHERE turn_id IN ({','.join('?' * len(chunk))})"
                                      " ORDER BY created_at_ms, app", chunk)
            for card in map(_card, rows):
                found.setdefault(card.turn_id, []).append(card)
        return found

    def move(self, card_id: str, to: str, *, error: str = "", tool_count: int | None = None) -> Card | None:
        """Move a card to `to` from a state that may move there; None (and nothing changed) otherwise."""
        sources = [state for state, targets in MOVES.items() if to in targets]
        with self._conn:
            moved = self._conn.execute(
                f"UPDATE connect_cards SET state=?, error=?, tool_count=COALESCE(?, tool_count), updated_at_ms=?"
                f" WHERE id=? AND state IN ({','.join('?' * len(sources))})",
                (to, error, tool_count, self._now_ms(), card_id, *sources)).rowcount
        return self.get(card_id) if moved else None

    def claim_resume(self, turn_id: int) -> bool:
        """True once, when every card of the turn is connected and its task has not resumed yet."""
        with self._conn:
            return self._conn.execute(
                "UPDATE connect_cards SET resumed_at_ms=? WHERE turn_id=? AND resumed_at_ms IS NULL"
                " AND NOT EXISTS (SELECT 1 FROM connect_cards WHERE turn_id=? AND state!='connected')",
                (self._now_ms(), turn_id, turn_id)).rowcount > 0


class _Failed(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class ConnectorCards:
    """The cards' flow. `router` calls the connectors' own sign-in methods (their lock and their
    value-free failures included); `connectors` says which tools can be called now; `resume`, set by
    the agent, carries on the task of a turn once its cards are connected."""

    def __init__(self, conn: sqlite3.Connection, router, connectors, *,
                 notify: Callable[[str, dict], None] | None = None, now_ms: Callable[[], int] = wall_clock_ms,
                 wait_ms: int = CARD_WAIT_MS) -> None:
        self.store = CardStore(conn, now_ms, wait_ms)
        self._conn, self._router, self._connectors, self._notify = conn, router, connectors, notify
        self.resume: Callable[[int], Awaitable[None]] | None = None
        self._login: tuple[str, asyncio.Task] | None = None
        self._owner_cancelled = False
        self._tasks: set[asyncio.Task] = set()

    def register(self, router) -> None:
        router.register("integrations.card_connect", self._connect)
        router.register("integrations.card_cancel", self._cancel)

    # --- what the agent asks ----------------------------------------------------------------------

    def ready_tools(self, app: App) -> list[str]:
        """The tools of `app` that can be called now, through either provider; none while its read
        tools cannot, or when the connections cannot be read (unknown is never connected)."""
        try:
            google = [name for name in app.google_tools if self._connectors.tool_available(GOOGLE_SERVER, name)]
            if app.reads and set(app.reads) <= set(google):
                return google
            # The same test the count of the owner's apps makes (`ConnectorsService.ready_tools_of`).
            entry = self._connectors.store.connectors.get("composio:" + app.composio)
            if entry is not None and (ready := self._connectors.ready_tools_of(entry)):
                return ready
        except Exception as exc:
            logger.warning("Could not read an app's connection: %s", type(exc).__name__)
        return []

    def _expired(self, app: App) -> bool:
        """Whether the app was connected and its sign-in has run out."""
        try:
            entries = self._connectors.store.connectors
            return any(entry is not None and entry.status == "EXPIRED"
                       for entry in (entries.get("google"), entries.get("composio:" + app.composio)))
        except Exception:
            return False

    def show(self, *, turn_id: int, member_id: int, conversation_id: int | None, app: App, language: str,
             done: Iterable[tuple[str, str]] = ()) -> Card | None:
        """The card a task asks for, or None when the app is connected already."""
        if self.ready_tools(app):
            return None
        return self.store.open(turn_id=turn_id, member_id=member_id, conversation_id=conversation_id,
                               app=app.id, language=language, reconnect=self._expired(app), done=done)

    def views(self, turn_ids: Iterable[int]) -> dict[int, list[dict]]:
        return {turn_id: [card.view() for card in cards] for turn_id, cards in self.store.for_turns(turn_ids).items()}

    def cards_of(self, turn_id: int) -> list[Card]:
        return self.store.for_turns([turn_id]).get(turn_id, [])

    # --- the window's methods ---------------------------------------------------------------------

    def _card(self, params: dict) -> Card:
        card_id = params.get("id")
        card = self.store.get(card_id) if isinstance(card_id, str) and card_id else None
        if card is None or self._conn.execute("SELECT 1 FROM agent_turns WHERE id=?", (card.turn_id,)).fetchone() is None:
            raise invalid_params("There is no such card")
        return card

    async def _connect(self, params: dict) -> dict:
        """Start the sign-in of a card that waits (a connected or expired card stays as it is)."""
        card = self._card(params)
        if self._login is not None and not self._login[1].done():
            return {"card": card.view(), "started": False}
        moved = self.store.move(card.id, "authorizing")
        if moved is None:
            return {"card": card.view(), "started": False}
        self._owner_cancelled = False
        task = asyncio.create_task(self._sign_in(moved))
        self._login = (moved.id, task)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._changed(moved)
        return {"card": moved.view(), "started": True}

    async def _cancel(self, params: dict) -> dict:
        card = self._card(params)
        if self._login is None or self._login[0] != card.id or self._login[1].done():
            return {"cancelled": False}
        self._owner_cancelled = True
        self._login[1].cancel()
        return {"cancelled": True}

    def _provider(self, app: App) -> tuple[str, dict] | None:
        """The sign-in to use: the owner's own Google authorization first, then Composio."""
        vault = self._connectors.vault
        if "google" in self._connectors.store.connectors and vault.get(PREFIX + "client_id"):
            return "connectors.google.connect", {}
        if vault.get(KEY):
            return "connectors.connect", {"toolkit": app.composio}
        return None

    async def _sign_in(self, card: Card) -> None:
        app = APPS[card.app]
        try:
            try:
                if not self.ready_tools(app):
                    provider = self._provider(app)
                    if provider is None:
                        raise _Failed("not_configured")
                    result = await self._router.call(*provider)
                    if not result.get("connected"):
                        raise _Failed(_FAILURES.get(str(result.get("error")), "error"))
                tools = self.ready_tools(app)
                if not tools:
                    raise _Failed("scope")
                ended = self.store.move(card.id, "connected", tool_count=len(tools))
            except _Failed as failure:
                ended = self.store.move(card.id, "failed", error=failure.kind)
            except RpcError:
                ended = self.store.move(card.id, "failed", error="error")
            except asyncio.CancelledError:
                if not self._owner_cancelled:
                    self.store.move(card.id, "failed", error="interrupted")
                    raise
                ended = self.store.move(card.id, "cancelled")
        finally:
            if self._login is not None and self._login[0] == card.id:
                self._login = None
            self._changed(self.store.get(card.id) or card)
        if ended is not None and ended.state == "connected" and self.store.claim_resume(ended.turn_id) and self.resume:
            try:
                await self.resume(ended.turn_id)
            except Exception as exc:
                logger.warning("A connected card's task could not resume: %s", type(exc).__name__)

    def _changed(self, card: Card) -> None:
        if self._notify is not None:
            self._notify("agent.turn", {"turn_id": card.turn_id, "member_id": card.member_id})

    async def idle(self) -> None:
        """Wait until no sign-in and no resume is under way (for a test or a scenario that reads back)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
