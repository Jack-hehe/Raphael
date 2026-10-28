"""Secret cards: the card a chat shows in its own conversation when the task in it needs a key or a
token that a connection declares and the vault holds no value for, and the one resume of that task
once the owner has saved it.

A card is a row of `secret_cards`, bound to the turn whose task asked (the model's `request_secret`)
and to one secret a connection of the owner's own declares (an environment variable or a header):
the model names the connection, never a vault name, so a card can fill only a slot the owner already
gave a connection. Its state moves only along `MOVES`:

    pending ── save ──> saved ── resume ──> resuming ──> resumed
       |                  ^                      |
       +──> failed ───────+ (the vault refused;  +──> saved again, with resume_failed (retry)
            save again)
    pending, failed or saved ── Stop ──> cancelled;  pending, failed or saved ── CARD_WAIT_MS ──> expired

`integrations.card_save {id, value}` is the one way a value leaves the window: straight to the
connection's secret in the vault (`IntegrationsService.fill_secret`), and nowhere else. The value is
never put in a row, a notification, a reply, an exception or a log, and the model learns only that
the key is saved. "Saved" and "resumed" are different things: when every card of the turn is saved,
that turn's task resumes once (`resume`, which the agent supplies) with a conditional update, so a
replayed save finds nothing to do (and does not touch the vault); a resume that failed leaves the
card saved, and `integrations.card_resume {id}` carries the task on without asking for the key
again. A cancelled or expired card never resumes anything; a resume under way when the core ended
is over: the card is saved, with `interrupted`.
"""

import asyncio
import json
import logging
import re
import secrets
import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from ..clock import wall_clock_ms
from ..integrations.service import NoSuchSecret
from ..integrations.vault import SecretTooLong
from ..protocol import invalid_params
from .cards import CARD_WAIT_MS

logger = logging.getLogger(__name__)

# The only moves a card's state makes.
MOVES: dict[str, tuple[str, ...]] = {
    "pending": ("saved", "failed", "cancelled", "expired"),
    "failed": ("saved", "cancelled", "expired"),
    "saved": ("resuming", "cancelled", "expired"),
    "resuming": ("resumed", "saved"),
    "resumed": (),
    "cancelled": (),
    "expired": (),
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_COLUMNS = ("id, turn_id, member_id, conversation_id, server_id, kind, secret, name, language, state, error,"
            " attempts, done, created_at_ms, expires_at_ms, resumed_at_ms")


@dataclass(frozen=True)
class SecretCard:
    id: str
    turn_id: int
    member_id: int
    conversation_id: int | None
    server_id: str
    kind: str
    secret: str
    name: str
    language: str
    state: str
    error: str
    attempts: int
    done: tuple[tuple[str, str], ...]  # (tool key, argument digest) of what the task did before it asked
    created_at_ms: int
    expires_at_ms: int
    resumed: bool

    def view(self) -> dict:
        """The card as the window draws it: the connection and the secret it declares, never a value."""
        return {"id": self.id, "name": self.name, "secret": self.secret, "state": self.state,
                "error": self.error or None, "expires_at_ms": self.expires_at_ms, "resumed": self.resumed}


def _card(row: sqlite3.Row) -> SecretCard:
    return SecretCard(row["id"], row["turn_id"], row["member_id"], row["conversation_id"], row["server_id"], row["kind"],
                      row["secret"], row["name"], row["language"], row["state"], row["error"], row["attempts"],
                      tuple((key, digest) for key, digest in json.loads(row["done"])),
                      row["created_at_ms"], row["expires_at_ms"], row["resumed_at_ms"] is not None)


class SecretCardStore:
    """The cards, in the database. A resume that was under way when the core last ended is over: its
    cards are `saved` with `interrupted`, from which the task can be carried on again."""

    def __init__(self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms,
                 wait_ms: int = CARD_WAIT_MS) -> None:
        self._conn, self._now_ms, self._wait_ms = conn, now_ms, wait_ms
        with conn:
            conn.execute("UPDATE secret_cards SET state='saved', error='interrupted', updated_at_ms=?"
                         " WHERE state='resuming'", (now_ms(),))

    def open(self, *, turn_id: int, member_id: int, conversation_id: int | None, server_id: str, kind: str,
             secret: str, name: str, language: str, done: Iterable[tuple[str, str]] = ()) -> SecretCard:
        """The card of one secret for `turn_id`: a new one, or the one the turn has (a task asks once)."""
        now = self._now_ms()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO secret_cards (id, turn_id, member_id, conversation_id, server_id, kind, secret,"
                " name, language, state, done, created_at_ms, updated_at_ms, expires_at_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (secrets.token_hex(8), turn_id, member_id, conversation_id, server_id, kind, secret, name, language,
                 json.dumps(sorted(set(done))), now, now, now + self._wait_ms))
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM secret_cards WHERE turn_id=? AND server_id=? AND kind=? AND secret=?",
            (turn_id, server_id, kind, secret)).fetchone()
        return _card(row)

    def expire_due(self) -> None:
        """Cards whose time ran out while they waited for the owner (or for the resume) are expired."""
        now = self._now_ms()
        with self._conn:
            self._conn.execute("UPDATE secret_cards SET state='expired', updated_at_ms=?"
                               " WHERE state IN ('pending','failed','saved') AND expires_at_ms<=?", (now, now))

    def get(self, card_id: str) -> SecretCard | None:
        self.expire_due()
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM secret_cards WHERE id=?", (card_id,)).fetchone()
        return None if row is None else _card(row)

    def for_turns(self, turn_ids: Iterable[int]) -> dict[int, list[SecretCard]]:
        self.expire_due()
        ids = sorted({int(turn_id) for turn_id in turn_ids})
        found: dict[int, list[SecretCard]] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM secret_cards WHERE turn_id IN ({','.join('?' * len(chunk))})"
                " ORDER BY created_at_ms, rowid", chunk)
            for card in map(_card, rows):
                found.setdefault(card.turn_id, []).append(card)
        return found

    def move(self, card_id: str, to: str, *, error: str = "") -> SecretCard | None:
        """Move a card to `to` from a state that may move there; None (and nothing changed) otherwise."""
        sources = [state for state, targets in MOVES.items() if to in targets]
        with self._conn:
            moved = self._conn.execute(
                f"UPDATE secret_cards SET state=?, error=?, updated_at_ms=? WHERE id=?"
                f" AND state IN ({','.join('?' * len(sources))})",
                (to, error, self._now_ms(), card_id, *sources)).rowcount
        return self.get(card_id) if moved else None

    def claim_resume(self, turn_id: int) -> bool:
        """True once per attempt, when every card of the turn is saved and none is resuming or done:
        the cards all move to `resuming` in one update, so a replayed save or retry finds nothing."""
        self.expire_due()
        with self._conn:
            return self._conn.execute(
                "UPDATE secret_cards SET state='resuming', error='', attempts=attempts+1, updated_at_ms=?"
                " WHERE turn_id=? AND state='saved'"
                " AND NOT EXISTS (SELECT 1 FROM secret_cards WHERE turn_id=? AND state!='saved')",
                (self._now_ms(), turn_id, turn_id)).rowcount > 0

    def attempt(self, turn_id: int) -> int:
        """How many times the task of the turn was carried on (the latest claim's number)."""
        row = self._conn.execute("SELECT MAX(attempts) FROM secret_cards WHERE turn_id=?", (turn_id,)).fetchone()
        return int(row[0] or 0)

    def resume_finished(self, turn_id: int, ok: bool, made: Iterable[tuple[str, str]] = ()) -> None:
        """The task of the turn went on (`resumed`), or did not: the cards are saved again, with
        `resume_failed` and what the attempt did that changes something, which is not done twice."""
        now = self._now_ms()
        with self._conn:
            if ok:
                self._conn.execute("UPDATE secret_cards SET state='resumed', error='', resumed_at_ms=?, updated_at_ms=?"
                                   " WHERE turn_id=? AND state='resuming'", (now, now, turn_id))
                return
            rows = self._conn.execute("SELECT id, done FROM secret_cards WHERE turn_id=? AND state='resuming'",
                                      (turn_id,)).fetchall()
            for row in rows:
                done = sorted({*(tuple(item) for item in json.loads(row["done"])), *map(tuple, made)})
                self._conn.execute("UPDATE secret_cards SET state='saved', error='resume_failed', done=?, updated_at_ms=?"
                                   " WHERE id=?", (json.dumps(done), now, row["id"]))

    def cancel_session(self, session_id: str) -> list[SecretCard]:
        """Stop: the cards of the turns of this session that still wait are cancelled and never resume
        anything. The cards that changed."""
        with self._conn:
            ids = [row["id"] for row in self._conn.execute(
                "SELECT c.id FROM secret_cards c JOIN agent_turns t ON t.id=c.turn_id"
                " WHERE t.session_id=? AND c.state IN ('pending','failed','saved')", (session_id,))]
            if ids:
                self._conn.execute(
                    f"UPDATE secret_cards SET state='cancelled', updated_at_ms=? WHERE id IN ({','.join('?' * len(ids))})",
                    (self._now_ms(), *ids))
        return [card for card in (self.get(card_id) for card_id in ids) if card is not None]


class SecretCards:
    """The cards' flow. `integrations` knows which secrets a connection declares and keeps the value
    (`missing_secrets`, `fill_secret`); `resume`, set by the agent, carries on the task of a turn once
    its cards are saved and answers whether it went on (`.ok`) and what it did (`.made`)."""

    def __init__(self, conn: sqlite3.Connection, integrations, *, notify: Callable[[str, dict], None] | None = None,
                 now_ms: Callable[[], int] = wall_clock_ms, wait_ms: int = CARD_WAIT_MS) -> None:
        self.store = SecretCardStore(conn, now_ms, wait_ms)
        self._conn, self._integrations, self._notify = conn, integrations, notify
        self.resume: Callable[[int, int], Awaitable[object]] | None = None
        self._tasks: set[asyncio.Task] = set()

    def register(self, router) -> None:
        router.register("integrations.card_save", self._save)
        router.register("integrations.card_resume", self._retry)

    # --- what the agent asks ----------------------------------------------------------------------

    def show(self, *, turn_id: int, member_id: int, conversation_id: int | None, connection: str, secret: str | None,
             language: str, done: Iterable[tuple[str, str]] = ()) -> list[SecretCard]:
        """The cards of the secrets `connection` (its id or its name) declares and the vault holds no
        value for, or only of the one named; none when nothing is missing. LookupError when the owner
        has no such connection, or the connection declares no such secret."""
        found = self._integrations.missing_secrets(connection)
        if found is None:
            raise LookupError("connection")
        server, missing = found
        if secret is not None:
            named = [(kind, key) for kind, key in [("env", k) for k in server.env] + [("header", k) for k in server.headers]
                     if key == secret or key.casefold() == secret.strip().casefold()]
            if not named:
                raise LookupError("secret")
            missing = [slot for slot in missing if slot in named]
        return [self.store.open(turn_id=turn_id, member_id=member_id, conversation_id=conversation_id,
                                server_id=server.id, kind=kind, secret=key, name=server.name, language=language, done=done)
                for kind, key in missing]

    def views(self, turn_ids: Iterable[int]) -> dict[int, list[dict]]:
        return {turn_id: [card.view() for card in cards] for turn_id, cards in self.store.for_turns(turn_ids).items()}

    def cards_of(self, turn_id: int) -> list[SecretCard]:
        return self.store.for_turns([turn_id]).get(turn_id, [])

    def cancel_session(self, session_id: str) -> None:
        """Stop for the session of a turn whose cards still wait: they never resume."""
        for card in self.store.cancel_session(session_id):
            self._changed(card)

    # --- the window's methods ---------------------------------------------------------------------

    def _card(self, params: dict) -> SecretCard:
        card_id = params.get("id")
        card = self.store.get(card_id) if isinstance(card_id, str) and card_id else None
        if card is None or self._conn.execute("SELECT 1 FROM agent_turns WHERE id=?", (card.turn_id,)).fetchone() is None:
            raise invalid_params("There is no such card")
        return card

    async def _save(self, params: dict) -> dict:
        """Keep the value in the card's secret, straight in the vault. Nothing between the check of the
        card and its move to `saved` waits, so a replay cannot slip in; a card that no longer waits for
        a value (saved, cancelled, expired) does not touch the vault."""
        card = self._card(params)
        value = params.get("value")
        if not isinstance(value, str):
            raise invalid_params("value must be text")
        if card.state not in ("pending", "failed"):
            return {"card": card.view(), "saved": False}
        error = ""
        value = value.strip()
        if not value or _CONTROL.search(value):
            error = "invalid"
        else:
            try:
                self._integrations.fill_secret(card.server_id, card.kind, card.secret, value)
            except SecretTooLong:
                error = "too_long"
            except NoSuchSecret:
                error = "gone"
            except Exception as exc:  # the vault: its message could hold anything
                logger.warning("A card's secret could not be kept: %s", type(exc).__name__)
                error = "vault"
        value = ""
        if error:
            failed = self.store.move(card.id, "failed", error=error) or card
            self._changed(failed)
            return {"card": failed.view(), "saved": False}
        moved = self.store.move(card.id, "saved")
        started = self.store.claim_resume(card.turn_id)
        self._changed(moved or card)
        if started:
            self._start(card.turn_id)
        return {"card": (self.store.get(card.id) or card).view(), "saved": moved is not None}

    async def _retry(self, params: dict) -> dict:
        """Carry the task on again after a resume that failed, without asking for the key again."""
        card = self._card(params)
        started = card.state == "saved" and self.store.claim_resume(card.turn_id)
        if started:
            self._changed(card)
            self._start(card.turn_id)
        return {"card": (self.store.get(card.id) or card).view(), "resumed": bool(started)}

    def _start(self, turn_id: int) -> None:
        if self.resume is None:
            return
        task = asyncio.create_task(self._resume(turn_id, self.store.attempt(turn_id)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _resume(self, turn_id: int, attempt: int) -> None:
        ok, made = False, ()
        try:
            outcome = await self.resume(turn_id, attempt)
            ok, made = bool(getattr(outcome, "ok", False)), tuple(getattr(outcome, "made", ()))
        except Exception as exc:
            logger.warning("A saved card's task could not resume: %s", type(exc).__name__)
        finally:
            # A resume cut off (the core closing) is one that did not continue, like a failed one.
            self.store.resume_finished(turn_id, ok, made)
            for card in self.cards_of(turn_id)[:1]:
                self._changed(card)

    def _changed(self, card: SecretCard) -> None:
        if self._notify is not None:
            self._notify("agent.turn", {"turn_id": card.turn_id, "member_id": card.member_id})

    async def idle(self) -> None:
        """Wait until no resume is under way (for a test or a scenario that reads back)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
