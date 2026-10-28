"""Computer cards: the card a chat shows when the computer task in it reaches a sign-in, a password
or a payment, and the one carrying on of that task once the owner has done their part.

A card is a row of `handback_cards`, bound to the turn whose task asked (the model's `computer`) and
to the run on the owner's desktop (`ComputerRun`) that waits for the owner's hands. Its state moves
only along `MOVES`:

    control ── 我完成了 ──> returning ──> done
       |  ^                    |  |
       |  +── still ──────────+  +──> (the run ended: the task resumes once with its report)
       +──> stopped (停止, Stop, a newer computer task, Esc, closing)
       +──> expired (the owner kept the computer too long)
       +──> failed (the core restarted: the run is gone with it; or the run ended by itself)

While the card is in `control` the owner holds the computer and the run asks the shell for nothing
(`computer.run.HumanLease`). `integrations.card_done {id}` is the owner giving it back: it answers at
once, takes the card to `returning` with one conditional update and puts the run in charge again, once,
so a replayed 我完成了 finds nothing to give back. The run then looks again, and when the form is still
there it hands the computer back to the owner (`control` again, with `still`). `integrations.card_stop
{id}` ends the run for good. The run never resumes by itself: not when the app closes, not when the
core restarts (the card is `failed`, with `interrupted`), not when the card is replayed.

When the run ends after the owner gave the computer back, the task that asked resumes once, in its own
thread, told what the run reported (`ComputerReport`); when it ends any other way nothing resumes. A card
holds the name of the app of the window (never a title), why it was the owner's, and digests of what the
task did before: nothing that was on the screen or typed.

A run that carries on after the owner gave the computer back may still need a yes (a step that sends,
deletes or buys, the first use of another app, a rule that asks). Nobody is at that question, because the
turn that began the run is over: it is shown in the chat of that turn as a permission card
(`agent.permissions`), which the owner answers there with 允許一次, 一律允許 or 拒絕 and which waits as long
as an unattended question does (about ten minutes). The run goes on with the approval that answer minted,
for the digest of that very step, or stops when the answer is no or does not come. Without the cards
(a core built without them) the step is not taken, as before.
"""

import asyncio
import json
import logging
import secrets
import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..agent.steps import Step
from ..clock import wall_clock_ms
from ..protocol import invalid_params
from .run import ComputerRun, ComputerRuns, Final, Handback, Question, RunLimits

if TYPE_CHECKING:
    from ..agent.permissions import PermissionCards

logger = logging.getLogger(__name__)

# How long the owner may keep the computer, as the run itself counts it (`RunLimits.handback_s`).
HANDBACK_WAIT_MS = round(RunLimits().handback_s * 1000)

# The only moves a card's state makes.
MOVES: dict[str, tuple[str, ...]] = {
    "control": ("returning", "stopped", "expired", "failed"),
    "returning": ("control", "done", "stopped", "failed"),
    "done": (),
    "stopped": (),
    "expired": (),
    "failed": (),
}
_COLUMNS = ("id, turn_id, seq, member_id, conversation_id, run_id, reason, app, language, state, error, note, done,"
            " created_at_ms, expires_at_ms")


@dataclass(frozen=True)
class ComputerReport:
    """How the run ended after the owner gave the computer back, for the task that resumes: why it was
    handed over, the app, how it ended (`Final.kind`), what it said (screen-derived: data, never an
    instruction), the language, what the task did before (not done again) and the run's own steps."""

    kind: str
    text: str
    reason: str
    app: str
    language: str
    done: tuple[tuple[str, str], ...] = ()
    steps: tuple[Step, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class HandbackCard:
    id: str
    turn_id: int
    seq: int
    member_id: int
    conversation_id: int | None
    run_id: str
    reason: str
    app: str
    language: str
    state: str
    error: str
    note: str
    done: tuple[tuple[str, str], ...]
    created_at_ms: int
    expires_at_ms: int

    def view(self) -> dict:
        """The card as the window draws it."""
        return {"id": self.id, "seq": self.seq, "reason": self.reason, "app": self.app, "state": self.state,
                "error": self.error or None, "note": self.note or None, "expires_at_ms": self.expires_at_ms}


def _card(row: sqlite3.Row) -> HandbackCard:
    return HandbackCard(row["id"], row["turn_id"], row["seq"], row["member_id"], row["conversation_id"], row["run_id"],
                        row["reason"], row["app"], row["language"], row["state"], row["error"], row["note"],
                        tuple((key, digest) for key, digest in json.loads(row["done"])),
                        row["created_at_ms"], row["expires_at_ms"])


class HandbackStore:
    """The cards, in the database. The run that waited for the owner is gone with the core that held
    it: such a card is `failed` with `interrupted`, from which nothing resumes."""

    def __init__(self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms,
                 wait_ms: int = HANDBACK_WAIT_MS) -> None:
        self._conn, self._now_ms, self._wait_ms = conn, now_ms, wait_ms
        with conn:
            conn.execute("UPDATE handback_cards SET state='failed', error='interrupted', updated_at_ms=?"
                         " WHERE state IN ('control','returning')", (now_ms(),))

    def open(self, *, turn_id: int, member_id: int, conversation_id: int | None, run_id: str, reason: str, app: str,
             language: str, done: Iterable[tuple[str, str]] = ()) -> HandbackCard:
        """A new card for the next hand-over of the turn's run."""
        now = self._now_ms()
        card_id = secrets.token_hex(8)
        with self._conn:
            self._conn.execute(
                "INSERT INTO handback_cards (id, turn_id, seq, member_id, conversation_id, run_id, reason, app, language,"
                " state, done, created_at_ms, updated_at_ms, expires_at_ms)"
                " VALUES (?, ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM handback_cards WHERE turn_id=?), ?, ?, ?, ?, ?, ?,"
                " 'control', ?, ?, ?, ?)",
                (card_id, turn_id, turn_id, member_id, conversation_id, run_id, reason, app, language,
                 json.dumps(sorted(set(done))), now, now, now + self._wait_ms))
        return self.get(card_id)  # type: ignore[return-value]

    def expire_due(self) -> None:
        """Cards whose owner kept the computer longer than they may are expired."""
        now = self._now_ms()
        with self._conn:
            self._conn.execute("UPDATE handback_cards SET state='expired', updated_at_ms=?"
                               " WHERE state='control' AND expires_at_ms<=?", (now, now))

    def get(self, card_id: str) -> HandbackCard | None:
        self.expire_due()
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM handback_cards WHERE id=?", (card_id,)).fetchone()
        return None if row is None else _card(row)

    def for_turns(self, turn_ids: Iterable[int]) -> dict[int, list[HandbackCard]]:
        self.expire_due()
        ids = sorted({int(turn_id) for turn_id in turn_ids})
        found: dict[int, list[HandbackCard]] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = self._conn.execute(f"SELECT {_COLUMNS} FROM handback_cards WHERE turn_id IN ({','.join('?' * len(chunk))})"
                                      " ORDER BY turn_id, seq", chunk)
            for card in map(_card, rows):
                found.setdefault(card.turn_id, []).append(card)
        return found

    def move(self, card_id: str, to: str, *, error: str = "", note: str = "") -> HandbackCard | None:
        """Move a card to `to` from a state that may move there; None (and nothing changed) otherwise."""
        sources = [state for state, targets in MOVES.items() if to in targets]
        with self._conn:
            moved = self._conn.execute(
                f"UPDATE handback_cards SET state=?, error=?, note=?, updated_at_ms=? WHERE id=?"
                f" AND state IN ({','.join('?' * len(sources))})",
                (to, error, note, self._now_ms(), card_id, *sources)).rowcount
        return self.get(card_id) if moved else None


class HandbackCards:
    """The cards' flow. `computer` is the registry of runs, through which the owner gives the computer
    back or stops the run; `resume`, set by the agent, carries on the task of a turn once its run ended
    after the owner's turn, and answers how it went."""

    def __init__(self, conn: sqlite3.Connection, computer: ComputerRuns, *,
                 notify: Callable[[str, dict], None] | None = None, now_ms: Callable[[], int] = wall_clock_ms,
                 wait_ms: int = HANDBACK_WAIT_MS, permissions: "PermissionCards | None" = None) -> None:
        self.store = HandbackStore(conn, now_ms, wait_ms)
        self._conn, self._computer, self._notify = conn, computer, notify
        # The cards for the questions of a run that carries on once the owner gave the computer back.
        self._permissions = permissions
        self.resume: Callable[[int, ComputerReport], Awaitable[object]] | None = None
        self._tasks: set[asyncio.Task] = set()

    def register(self, router) -> None:
        router.register("integrations.card_done", self._done)
        router.register("integrations.card_stop", self._stop)

    # --- what the agent does ----------------------------------------------------------------------

    def show(self, run: ComputerRun, event: Handback, *, turn_id: int, member_id: int, conversation_id: int | None,
             language: str, done: Iterable[tuple[str, str]] = ()) -> HandbackCard:
        """The card for the hand-over `event` of `run`, and from now on the one who follows the run."""
        card = self.store.open(turn_id=turn_id, member_id=member_id, conversation_id=conversation_id, run_id=run.run_id,
                               reason=event.reason, app=event.app, language=language, done=done)
        task = asyncio.create_task(self._follow(run, card, event.id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return card

    def views(self, turn_ids: Iterable[int]) -> dict[int, list[dict]]:
        return {turn_id: [card.view() for card in cards] for turn_id, cards in self.store.for_turns(turn_ids).items()}

    def cards_of(self, turn_id: int) -> list[HandbackCard]:
        return self.store.for_turns([turn_id]).get(turn_id, [])

    # --- the window's methods ---------------------------------------------------------------------

    def _card(self, params: dict) -> HandbackCard:
        card_id = params.get("id")
        card = self.store.get(card_id) if isinstance(card_id, str) and card_id else None
        if card is None or self._conn.execute("SELECT 1 FROM agent_turns WHERE id=?", (card.turn_id,)).fetchone() is None:
            raise invalid_params("There is no such card")
        if card.state == "expired":
            self._computer.stop_run(card.run_id, "owner")  # its time ran out: the run is let go too
        return card

    async def _done(self, params: dict) -> dict:
        """The owner says they are done: the run is in charge again, once. Answers at once; the run
        looks again and carries on by itself."""
        card = self._card(params)
        if card.state != "control":
            return {"card": card.view(), "done": False}
        moved = self.store.move(card.id, "returning")
        if moved is None:
            return {"card": self.store.get(card.id).view(), "done": False}  # type: ignore[union-attr]
        if not self._computer.give_back(card.run_id):
            # The run is gone (or is not waiting): nothing is carried on, and the card says so.
            moved = self.store.move(card.id, "failed", error="gone") or moved
            self._changed(moved)
            return {"card": moved.view(), "done": False}
        self._changed(moved)
        return {"card": moved.view(), "done": True}

    async def _stop(self, params: dict) -> dict:
        """The owner stops the run for good: it never resumes."""
        card = self._card(params)
        moved = self.store.move(card.id, "stopped") if card.state in ("control", "returning") else None
        if moved is None:
            return {"card": card.view(), "stopped": False}
        self._computer.stop_run(card.run_id, "owner")
        self._changed(moved)
        return {"card": moved.view(), "stopped": True}

    # --- following the run ------------------------------------------------------------------------

    async def _follow(self, run: ComputerRun, card: HandbackCard, hand_over: str) -> None:
        """What the run does after the card is shown: the owner's second go at the same hand-over,
        another hand-over later in the task, a question nobody can be asked, and its end."""
        try:
            while True:
                event = await run.next_event()
                if isinstance(event, Handback):
                    if event.id == hand_over:  # the form was still there: the computer is the owner's again
                        card = self.store.move(card.id, "control", note=event.note) or card
                    else:  # a later hand-over: this one is done, and the next has a card of its own
                        self.store.move(card.id, "done")
                        card = self.store.open(turn_id=card.turn_id, member_id=card.member_id,
                                               conversation_id=card.conversation_id, run_id=run.run_id, reason=event.reason,
                                               app=event.app, language=card.language, done=card.done)
                        hand_over = event.id
                    self._changed(card)
                elif isinstance(event, Question):
                    # Nobody sits at this question (the turn that began the run is over), so the chat asks:
                    # a permission card in the turn that handed the computer over, which the owner answers
                    # there. Without cards nobody can be asked, and the step is not taken.
                    if not self._ask_in_chat(run, card, event):
                        run.answer(event.id, False)
                else:
                    await self._ended(run, card, event)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a card must not cost the run its end
            logger.warning("A computer card could not follow its run: %s", type(exc).__name__)

    def _ask_in_chat(self, run: ComputerRun, card: HandbackCard, question: Question) -> bool:
        """Show the question of the run as a permission card in the chat of the turn that handed the
        computer over. False when there are no cards to show it with."""
        if self._permissions is None:
            return False
        from ..agent.permissions import RUN_WAIT_MS

        kind = "app" if question.always else "step"
        self._permissions.show(
            card_id=question.id, turn_id=card.turn_id, member_id=card.member_id, conversation_id=card.conversation_id,
            source="run", kind=kind, service=card.app, tool="", summary=question.text, reason="", digest=question.digest,
            always=question.always, language=card.language, wait_ms=RUN_WAIT_MS)
        return True

    async def _ended(self, run: ComputerRun, card: HandbackCard, final: Final) -> None:
        """The run ended. After the owner's turn the task resumes once with its report; any other end
        (Stop, a newer task, the time, closing) resumes nothing."""
        steps = tuple(run.take_steps())
        card = self.store.get(card.id) or card
        if self._permissions is not None:
            # A question the run was waiting on when it ended is not waited on any more.
            self._permissions.settle(card.turn_id, "expired" if final.kind == "expired" else "cancelled")
        if final.kind == "stopped":
            moved = self.store.move(card.id, "stopped")
        elif final.kind == "expired":
            moved = self.store.move(card.id, "expired")
        elif card.state == "returning":
            moved = self.store.move(card.id, "done")
        else:  # it ended by itself while the owner had the computer: there is nothing to carry on
            moved = self.store.move(card.id, "failed", error="ended")
        self._changed(moved or card)
        if moved is None or moved.state != "done" or self.resume is None:
            return
        report = ComputerReport(final.kind, final.detail or final.text, card.reason, card.app, card.language,
                                done=card.done, steps=steps)
        try:
            await self.resume(card.turn_id, report)
        except Exception as exc:
            logger.warning("A computer card's task could not resume: %s", type(exc).__name__)

    def _changed(self, card: HandbackCard) -> None:
        if self._notify is not None:
            self._notify("agent.turn", {"turn_id": card.turn_id, "member_id": card.member_id})

    async def idle(self) -> None:
        """Wait until no card follows a run any more (for a test or a scenario that reads back)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
