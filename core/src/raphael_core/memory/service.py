"""The memory of Raphael and of the members of the owner's team as one service: the files
(`store`), each conversation's summary (`conversation`), the reflection pass and the weekly
consolidation (`reflection`), the tools the assistants reach them by, and the RPC methods of the
settings page and of a member's 記憶 tab.

Two spaces. Every assistant (Raphael, and each member of the team) has a memory of its own, and
they all share one more:

- the shared memory (共同記憶), `memory\\shared\\` with its own `MEMORY.md`: what every assistant
  should know about the owner, and only that (who they are, their stable preferences, the people in
  their life, their life and work, how they like to be spoken to). Every assistant reads it and may
  write to it; never what was decided or discussed with one of them, a task, a plan made in one
  thread, or what a tool or the web said (`reflection.about_the_owner` holds to it);
- each assistant's own: Raphael's is the memory folder itself (`memory\\`), a member's
  `memory\\members\\<id>\\`. Only that assistant reads and writes it: its role, what its routines
  found, how the owner wants it to work.

The conversations are private to each assistant: its prompt, its summary, its search of the past
(`history_search`) and its reflection pass read only its own thread. The one text two of them share
is an exchange of `ask_member`, which is written in the threads of those two.

The prompt carries both indexes, the shared one first (`store.MemoryIndex`), each within the limits
of an index. A write chooses its space (`space`); a note read by name is looked for in the
assistant's own memory first, then in the shared one.

Upgrading: the notes of the type `user` in Raphael's folder were about the owner, so they move to
the shared memory once (`_share_the_owner`), as their files are, edits by hand included; a note
whose name the shared memory already has stays where it is. The move is marked as done in the
database, so it is made once.

The RPC methods (the main window only; the shell checks their params before they come here). Each
takes `space` ("own", the default, or "shared") and `member_id` (whose own: 0, Raphael, by default):

- `memory.list {space?, member_id?}` -> `{memories: [{name, description, type, origin, updated,
  expires}], summary: {text, updated_at_ms} | null}` (the summary is the member's conversation's;
  none for the shared space)
- `memory.get {name, space?, member_id?}` -> the memory, with its body and the turns it came from
- `memory.update {name, body, description?, space?, member_id?}` -> the memory as it is now. An edit
  by the user is a memory the user stated (`origin: said`); it passes the same checks as any other
  write.
- `memory.delete {name, space?, member_id?}` -> `{deleted}`, `memory.clear {space?, member_id?}` ->
  `{deleted}` (every memory of that space)
- `memory.move {name, to_space, member_id?}` -> `{moved}`: a note moved between the shared memory and
  an assistant's own, as it is; never over a note of the same name
- `memory.summary_clear {member_id?}` -> `{cleared}`: the conversation's summary is forgotten

The tools of the assistant (`agent.gate` knows them, and their tiers): `memory_read` and
`history_search` only read (T0); `memory_write` and `memory_delete` change the memory (T1, which
runs at once, with no yes asked for). The fast tier writes only from the owner's own turn
(`apply_said`, see `agent.service`); the background worker may read and search, never write, since
it reads what services return.

The reflection pass runs in the background on the cheap model (the `agent_model` setting), for each
assistant over its own turns (a watermark each): after REFLECT_AFTER_TURNS answered turns it has not
read, or once the conversation has been quiet for QUIET_S with any such turn, and once soon after
the app starts when one is waiting. The weekly consolidation runs with it when the last one is a
week old, for each space by itself. A timer that finds nothing due yet, and every pass when it ends,
arms the timer again for the next time something will be (the quiet time, the pause of RETRY_S
after a failure, the week), so nothing waits for the owner's next turn. Both need the key and room
under the month's limit, and both are written to the spend with the purpose "memory".

Nothing here logs a memory, a question or an answer.
"""

import asyncio
import secrets
import logging
import shutil
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, tzinfo
from pathlib import Path

from ..agent.history import AgentHistory, Turn
from ..agent.attribution import labelled_lines, turn_speakers
from ..agent.ledger import Ledger
from ..agent.tools import ToolResult
from ..brain.providers.base import ModelProvider
from ..brain.providers.registry import ProviderRouter, for_purpose
from ..brain.secrets import KeyStore
from ..brain.settings import DEFAULTS, SettingsStore
from ..brain.spend import SpendTracker
from ..clock import wall_clock_ms
from ..protocol import invalid_params
from ..router import Router as RpcRouter
from .conversation import SUMMARY_PURPOSE, Conversation
from .reflection import MAX_TURNS, SPACES, Action, Applied, Reflector, apply_spaced
from .store import MemoryIndex, MemoryRefused, MemoryStore, move_note, valid_name

logger = logging.getLogger(__name__)

PURPOSE = SUMMARY_PURPOSE  # "memory", in the spend
QUIET_S = 25 * 60.0
REFLECT_AFTER_TURNS = 10
CONSOLIDATE_EVERY_MS = 7 * 86_400_000
# How long after the app starts the memory looks for turns it has not read.
START_DELAY_S = 60.0
# A pass that failed is not tried again for this long.
RETRY_S = 15 * 60.0
_REFLECTED = "reflected_through"
_CONSOLIDATED = "consolidated_at_ms"
# Set once the notes about the owner moved from Raphael's folder to the shared one.
_SHARED_MOVED = "shared_moved"
# What a search gives of a turn, in characters, at most.
SNIPPET_CHARS = 150
MAX_READ = 3
# The folders, in the memory folder: the members' own, and the shared memory.
MEMBERS_DIR = "members"
SHARED_DIR = "shared"
# The type of Raphael's notes that moves to the shared memory at the upgrade: who the owner is.
_MOVED_TYPE = "user"


def _snippet(text: str, words: Sequence[str], *, preserve_label: bool = False) -> str:
    """`text` cut to SNIPPET_CHARS around the first of `words` it holds."""
    text = "\n".join(" ".join(line.split()) for line in (text or "").splitlines())
    if len(text) <= SNIPPET_CHARS:
        return text
    folded = text.casefold()
    at = min((folded.find(w) for w in words if w and folded.find(w) >= 0), default=0)
    start = max(0, at - SNIPPET_CHARS // 3)
    prefix = ""
    if preserve_label and start:
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", line_start)
        if line_end < 0:
            line_end = len(text)
        colon = text.find(": ", line_start)
        # Exchanges already carry explicit line labels. Keep the label of the first
        # excerpted line even when the matching words are deep inside its content.
        if line_start <= colon < min(line_start + 80, line_end):
            if start <= colon + 2:
                start = line_start
            else:
                prefix = text[line_start:colon + 2]
    cut = text[start : start + SNIPPET_CHARS]
    return prefix + ("…" if start else "") + cut + ("…" if start + SNIPPET_CHARS < len(text) else "")


def _speaker_snippet(speaker: str, text: str, words: Sequence[str], *, exchange: bool = False) -> str:
    """Truncate content before adding labels so the snippet cannot cut its speaker away."""
    return labelled_lines(speaker, _snippet(text, words, preserve_label=exchange))


def _watermark(member_id: int) -> str:
    """The name of the state that says up to which turn the reflection pass of `member_id` read."""
    return _REFLECTED if member_id == 0 else f"{_REFLECTED}:{int(member_id)}"


class MemoryService:
    """The memory. `conn` is the open database (used on the event loop only), `root` the memory
    folder, `client` the OpenRouter client, `keys` where the key lives. `now_ms` is the wall clock,
    `tz` the time zone of the spend's months, `clock` a monotonic clock in seconds and `now` the
    local time. `quiet_s` and `start_delay_s` are for the tests."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        root: Path,
        client: ModelProvider,
        keys: KeyStore,
        *,
        now_ms: Callable[[], int] = wall_clock_ms,
        tz: tzinfo | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        quiet_s: float = QUIET_S,
        start_delay_s: float = START_DELAY_S,
        notify: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._notify = notify
        self._conn = conn
        self._keys = keys
        self._now_ms = now_ms
        self._clock = clock
        self._quiet_s = quiet_s
        self._start_delay_s = start_delay_s
        self._client = for_purpose(client, "memory")
        self._now = now
        self.store = MemoryStore(root, now=now, on_change=lambda: self._changed(0))  # Raphael's own
        self.shared = MemoryStore(root / SHARED_DIR, now=now, on_change=lambda: self._changed(0, "shared"))
        self._history = AgentHistory(conn, now_ms)
        self._settings = SettingsStore(conn)
        self._spend = SpendTracker(conn, now_ms, tz)
        self._reflector = Reflector(client)
        self.conversation = Conversation(
            conn, client, now_ms=now_ms, clock=clock, reflected_through=self.reflected_through,
            on_change=lambda: self._changed(0),
        )
        # The members' memories and conversations, made when first needed.
        self._stores: dict[int, MemoryStore] = {}
        self._conversations: dict[int, Conversation] = {}
        # Whether a member is there: the app sets it to the members' own answer, so that the RPC
        # methods make no folder for an id that is nobody's. Until then any id goes.
        self.member_exists: Callable[[int], bool] = lambda member_id: True
        self._timer: asyncio.TimerHandle | None = None
        self._running: asyncio.Task[None] | None = None
        self._failed_at: float | None = None
        # How many times the conversation was cleared: a pass that began at another count is stale.
        self._turn_clears = 0
        self._share_the_owner()

    def _changed(self, member_id: int, space: str = "own") -> None:
        """Only the affected space and owner leave the core, never a note's contents."""
        if self._notify is not None:
            self._notify("memory.changed", {"member_id": member_id, "space": space})

    def register(self, router: RpcRouter) -> None:
        methods = {
            "memory.create": self._create,
            "memory.list": self._list,
            "memory.get": self._get,
            "memory.update": self._update,
            "memory.delete": self._delete,
            "memory.clear": self._clear,
            "memory.move": self._move,
            "memory.summary_clear": self._summary_clear,
        }
        for method, handler in methods.items():
            router.register(method, handler)

    # --- the state ---------------------------------------------------------------------------------

    def _state(self, key: str) -> int:
        try:
            row = self._conn.execute("SELECT value FROM memory_state WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error:
            return 0
        return 0 if row is None else int(row[0])

    def _set_state(self, key: str, value: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO memory_state (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def reflected_through(self, member_id: int = 0) -> int:
        """The last turn of the thread of `member_id` its reflection pass has read (0: none)."""
        return self._state(_watermark(member_id))

    def _share_the_owner(self) -> None:
        """Once, at the upgrade to the shared memory: Raphael's notes about who the owner is (type
        `user`) move to the shared memory, as their files are. A note the shared memory already has
        a note of the same name for stays where it is. A move that fails is tried again at the next
        start; the mark is set only once every note was dealt with."""
        if self._state(_SHARED_MOVED):
            return
        moved = 0
        try:
            for memory in self.store.all():
                if memory.type == _MOVED_TYPE and move_note(memory.name, self.store, self.shared):
                    moved += 1
            self._set_state(_SHARED_MOVED, 1)
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Could not move the notes about the owner: %s", type(exc).__name__)
            return
        if moved:
            logger.info("Moved %d notes about the owner to the shared memory", moved)

    # --- the spaces --------------------------------------------------------------------------------

    def store_for(self, member_id: int) -> MemoryStore:
        """The own memory of `member_id`: Raphael's for 0, else the member's folder in his."""
        if member_id == 0:
            return self.store
        store = self._stores.get(member_id)
        if store is None:
            store = MemoryStore(self.store.root / MEMBERS_DIR / str(int(member_id)), now=self._now,
                                on_change=lambda: self._changed(member_id))
            self._stores[member_id] = store
        return store

    def space(self, space: str, member_id: int = 0) -> MemoryStore:
        """The memory `space` names: the shared one, or the own of `member_id`."""
        return self.shared if space == "shared" else self.store_for(member_id)

    def conversation_for(self, member_id: int) -> Conversation:
        """The conversation of `member_id`: Raphael's for 0."""
        if member_id == 0:
            return self.conversation
        conversation = self._conversations.get(member_id)
        if conversation is None:
            conversation = Conversation(
                self._conn,
                self._client,
                now_ms=self._now_ms,
                clock=self._clock,
                reflected_through=lambda: self.reflected_through(member_id),
                member_id=member_id,
                on_change=lambda: self._changed(member_id),
            )
            self._conversations[member_id] = conversation
        return conversation

    def member_conversations(self) -> list[int]:
        """The members whose conversation has been read since the core started."""
        return list(self._conversations)

    def reader_for(self, member_id: int) -> "MemberMemory":
        """What a background task of `member_id` may do with the memory: read and search."""
        return MemberMemory(self, member_id)

    def _members(self) -> list[int]:
        """The ids of the members of the team (Raphael not among them)."""
        try:
            return [int(row[0]) for row in self._conn.execute("SELECT id FROM members ORDER BY id")]
        except sqlite3.Error:
            return []

    def _agents(self) -> list[int]:
        return [0, *self._members()]

    def _name_of(self, member_id: int) -> str:
        """What the reflection pass calls the assistant: Raphael, or the member by its name."""
        if member_id == 0:
            return "Raphael"
        try:
            row = self._conn.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()
        except sqlite3.Error:
            row = None
        return "the assistant" if row is None else str(row[0])

    async def forget_member(self, member_id: int) -> None:
        """A member was deleted: its own memory folder goes, with its conversation's summary and
        its reflection's watermark. What it wrote to the shared memory stays: it is the owner's."""
        if member_id == 0:
            return
        conversation = self._conversations.pop(member_id, None)
        if conversation is not None:
            await conversation.aclose()
        self._stores.pop(member_id, None)
        try:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM conversation_summaries WHERE member_id = ?", (member_id,)
                )
                self._conn.execute(
                    "DELETE FROM memory_state WHERE key = ?", (_watermark(member_id),)
                )
        except sqlite3.Error as exc:
            logger.warning("Could not delete a member's summary: %s", type(exc).__name__)
        folder = self.store.root / MEMBERS_DIR / str(int(member_id))
        try:
            await asyncio.to_thread(shutil.rmtree, folder, ignore_errors=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not delete a member's memory: %s", type(exc).__name__)
        self._changed(member_id)

    # --- for the assistant -------------------------------------------------------------------------

    def index(self, member_id: int = 0) -> MemoryIndex:
        """What `member_id` reads of the memory for the prompt: the index of the shared memory and
        of its own. An index that cannot be read is empty."""
        indexes = []
        for store in (self.shared, self.store_for(member_id)):
            try:
                indexes.append(store.index())
            except OSError as exc:
                logger.warning("Could not read the memory: %s", type(exc).__name__)
                indexes.append("")
        return MemoryIndex(shared=indexes[0], own=indexes[1])

    def apply_said(self, actions: Sequence[Action], turn_id: int, member_id: int = 0) -> Applied:
        """Write what the owner asked to be remembered (or forgotten) in the turn `turn_id`: the
        memories are ones the owner stated. Each goes to the space it names (the shared memory, or
        the own of `member_id`) and is checked like any other write; one for the shared memory that
        is not about the owner at large stays in the own (`reflection.apply_spaced`)."""
        said = [replace(a, origin="said") for a in actions]
        try:
            return apply_spaced(
                said, self.store_for(member_id), self.shared, turns=[turn_id] if turn_id else []
            )
        except OSError as exc:
            logger.warning("Could not write to the memory: %s", type(exc).__name__)
            return Applied(refused=["io"] * len(said))

    def note_turn(self, names: Sequence[str], turn_id: int, member_id: int = 0) -> None:
        """Add the turn `turn_id` to the turns the memories `names` came from (it was written
        after them), in whichever space each is."""
        if not turn_id:
            return
        own = self.store_for(member_id)
        for name in names:
            try:
                (own if own.get(name) is not None else self.shared).add_turn(name, turn_id)
            except OSError as exc:
                logger.warning("Could not note a memory's turn: %s", type(exc).__name__)

    def forget_member_turns(self, member_id: int) -> None:
        """Invalidate in-flight reflection while preserving every member's notes."""
        self._turn_clears += 1
        with self._conn:
            self._conn.execute("DELETE FROM memory_state WHERE key = ?", (_watermark(member_id),))

    def forget_turns(self) -> None:
        """The conversations were cleared: every reflection pass starts again from the first turn
        (the ids of the turns start again too). A pass that waits for the model meanwhile read
        turns that are gone: it applies nothing and leaves the watermark at 0."""
        self._turn_clears += 1
        with self._conn:
            self._conn.execute(
                "DELETE FROM memory_state WHERE key = ? OR key LIKE ?",
                (_REFLECTED, f"{_REFLECTED}:%"),
            )

    def read(self, names: Sequence[str], member_id: int = 0) -> ToolResult:
        """`memory_read`: the memories of `names` (at most MAX_READ), each with its body, when it
        was last updated and its space, looked for in the own memory of `member_id`, then in the
        shared one; the names that are not memories are listed as missing."""
        found, missing = [], []
        own = self.store_for(member_id)
        for name in list(dict.fromkeys(names))[:MAX_READ]:
            space, memory = "own", own.get(name) if valid_name(name) else None
            if memory is None and valid_name(name):
                space, memory = "shared", self.shared.get(name)
            if memory is None:
                missing.append(name)
                continue
            found.append(
                {
                    "name": memory.name,
                    "description": memory.description,
                    "body": memory.body,
                    "updated": memory.updated[:10],
                    "origin": memory.origin,
                    "space": space,
                }
            )
        return ToolResult("memory_read", True, {"memories": found, "missing": missing})

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        exclude: Sequence[int] = (),
        member_id: int = 0,
    ) -> ToolResult:
        """`history_search`: the past turns of the thread of `member_id` (and of no other) that
        hold the words of `query`, best first, each as its date, the question and the answer, cut
        short. No model call. The turns of `exclude` (the ones in the context already) are left out
        by the search itself, before its limit. A result that holds a run on the owner's desktop
        (`operate`), an exchange with another member or a routine's run is untrusted: the answer
        there tells what the screen showed, or what a member or a service said, so the reply that
        reads it writes no note."""
        try:
            turns = self._history.search(query, limit=limit, exclude=exclude, member_id=member_id)
        except sqlite3.Error as exc:
            logger.warning("Could not search the conversation: %s", type(exc).__name__)
            return ToolResult("history_search", False, None, "error")
        words = [w.casefold() for w in query.split()]
        found = []
        screen = False
        for turn in turns:
            screen = screen or turn.route in ("operate", "exchange", "routine")
            when = datetime.fromtimestamp(turn.created_at_ms / 1000).strftime("%Y-%m-%d %H:%M")
            answer = turn.detail if turn.detail and len(turn.detail) > len(turn.text) else turn.text
            question_label, answer_label = turn_speakers(turn)
            found.append(
                {
                    "turn": turn.id,
                    "when": when,
                    "question": _speaker_snippet(question_label, turn.question, words, exchange=turn.route == "exchange"),
                    "answer": _speaker_snippet(answer_label, answer or turn.prefix, words, exchange=turn.route == "exchange"),
                }
            )
            if len(found) >= limit:
                break
        return ToolResult("history_search", True, {"turns": found}, untrusted=screen)

    # --- the background ----------------------------------------------------------------------------

    def _model(self) -> str:
        model = self._settings.get("agent_model")
        return model.strip() if isinstance(model, str) and model.strip() else DEFAULTS["agent_model"]

    def may_spend(self) -> bool:
        """Whether a call of the memory's own may be made: there is a key."""
        try:
            if isinstance(self._client, ProviderRouter):
                return self._client.available(self._model())
            if not (self._keys.get() or "").strip():
                return False
            return True
        except Exception as exc:  # the key store's message could hold the key
            logger.warning("The memory could not check the key or the limit: %s", type(exc).__name__)
            return False

    def ledger(self) -> Ledger:
        return Ledger(self._spend, PURPOSE)

    def after_turn(self, member_id: int = 0) -> None:
        """A turn was written in the thread of `member_id`: fold its conversation if it is time,
        and run the reflection pass now if enough turns wait for one, or once the conversation has
        been quiet for a while."""
        if self.may_spend():
            self.conversation_for(member_id).schedule_fold(self._model(), self.ledger)
        if self._waiting(limit=REFLECT_AFTER_TURNS, member_id=member_id) >= REFLECT_AFTER_TURNS:
            self._spawn()
        else:
            self._arm(self._quiet_s)

    def start(self) -> None:
        """The app is up: look for turns the reflection pass has not read, soon."""
        self._arm(self._start_delay_s)

    def _arm(self, delay_s: float) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = loop.call_later(delay_s + 1.0, self._tick)

    def _tick(self) -> None:
        """The timer fired: run what is due, or arm the timer for when something will be. Without
        the second, a turn that waits at startup but is not quiet yet, or a pass that failed, would
        wait for the owner's next turn, however long that is."""
        self._timer = None
        if self.reflection_due() or self.consolidation_due():
            self._spawn()
        else:
            self._arm_next()

    def _arm_next(self) -> None:
        """Arm the timer for the next time something is due (`_next_due_s`), if anything ever is."""
        delay = self._next_due_s()
        if delay is not None:
            self._arm(delay)

    def _next_due_s(self) -> float | None:
        """In how many seconds a pass will next be due: the end of the pause after a failure, else
        the soonest of the reflection passes (now when REFLECT_AFTER_TURNS turns of one assistant
        wait, or when its conversation will have been quiet for QUIET_S) and the weekly
        consolidation. None when nothing waits: the owner's next turn arms the timer again."""
        if self._failed_at is not None:
            left = RETRY_S - (self._clock() - self._failed_at)
            if left > 0:
                return left
        delays: list[float] = []
        for member_id in self._agents():
            try:
                unread = self._unread(member_id=member_id)
                if len(unread) >= REFLECT_AFTER_TURNS:
                    delays.append(0.0)
                elif unread:
                    last = self._last_turn_ms(unread, member_id)
                    quiet_ms = last + self._quiet_s * 1000 - self._now_ms()
                    delays.append(max(0.0, quiet_ms / 1000))
            except sqlite3.Error:
                pass
        if self._any_memories():
            weekly_ms = self._state(_CONSOLIDATED) + CONSOLIDATE_EVERY_MS - self._now_ms()
            delays.append(max(0.0, weekly_ms / 1000))
        return min(delays) if delays else None

    def _last_turn_ms(self, unread: Sequence[Turn], member_id: int = 0) -> int:
        """When the latest turn of the thread was made, whatever its status (its last sound)."""
        latest = self._history.after(0, limit=1, member_id=member_id)
        return latest[-1].created_at_ms if latest else unread[-1].created_at_ms

    def _waiting(self, limit: int = MAX_TURNS, member_id: int = 0) -> int:
        try:
            return len(self._unread(limit, member_id))
        except sqlite3.Error:
            return 0

    def _unread(self, limit: int = MAX_TURNS, member_id: int = 0) -> list[Turn]:
        """The answered turns of the thread of `member_id` its reflection pass has not read, the
        latest `limit` of them. An exchange with another member and a routine's run are not the
        owner's own words: the pass never reads them."""
        turns = self._history.after(
            self.reflected_through(member_id), limit=limit * 3, member_id=member_id
        )
        own = [t for t in turns if t.status == "done" and t.route not in ("exchange", "routine")]
        return own[-limit:]

    def _due(self, member_id: int) -> bool:
        try:
            unread = self._unread(member_id=member_id)
        except sqlite3.Error:
            return False
        if len(unread) >= REFLECT_AFTER_TURNS:
            return True
        if not unread:
            return False
        return self._now_ms() - self._last_turn_ms(unread, member_id) >= self._quiet_s * 1000

    def reflection_due(self) -> bool:
        """Whether a reflection pass should run now: for an assistant, REFLECT_AFTER_TURNS turns
        wait, or one does and its last turn is QUIET_S old."""
        return any(self._due(member_id) for member_id in self._agents())

    def _spaces(self) -> list[tuple[MemoryStore, bool]]:
        """Every space there is to consolidate, with whether it is the shared one."""
        stores = [(self.store, False), (self.shared, True)]
        stores += [(self.store_for(member_id), False) for member_id in self._members()]
        return stores

    def _any_memories(self) -> bool:
        return any(store.all() for store, _ in self._spaces())

    def consolidation_due(self) -> bool:
        if not self._any_memories():
            return False
        return self._now_ms() - self._state(_CONSOLIDATED) >= CONSOLIDATE_EVERY_MS

    def _spawn(self) -> None:
        """Run what is due in the background, unless a pass runs already (it arms the timer when
        it ends) or the pause after a failure is not over (the timer is armed for its end)."""
        if self._running is not None and not self._running.done():
            return
        if self._failed_at is not None and self._clock() - self._failed_at < RETRY_S:
            self._arm_next()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._running = loop.create_task(self.run_due())

    async def run_due(self) -> None:
        """The reflection passes and the consolidation, whichever are due, one after the other;
        then the timer is armed for whatever is due next (the rest of a long backlog, the pause
        after a failure, the next quiet time or week). Without a key or room under the limit
        nothing runs, and the timer looks again no sooner than RETRY_S."""
        if not self.may_spend():
            delay = self._next_due_s()
            if delay is not None:
                self._arm(max(delay, RETRY_S))
            return
        try:
            for member_id in self._agents():
                if self._due(member_id):
                    applied = await self.reflect(member_id)
                    if applied.failed:
                        break
            if self.consolidation_due():
                await self.consolidate()
        except Exception as exc:  # the background must not break the conversation
            self._failed_at = self._clock()
            logger.warning("The memory's background pass failed: %s", type(exc).__name__)
        self._arm_next()

    async def reflect(self, member_id: int = 0) -> Applied:
        """One reflection pass over the turns of `member_id` it has not read, into its own memory
        and the shared one. The watermark moves past them when the model answered, whatever it
        made of them; a model that failed leaves them for the next pass, which waits RETRY_S. When
        the conversation was cleared while the model thought, nothing is applied and the
        watermark stays where the clear put it."""
        turns = self._unread(member_id=member_id)
        if not turns:
            return Applied()
        clears = self._turn_clears

        def current() -> bool:
            return self._turn_clears == clears

        applied = await self._reflector.reflect(
            self.store_for(member_id),
            turns,
            model=self._model(),
            ledger=self.ledger(),
            current=current,
            shared=self.shared,
            agent=self._name_of(member_id),
        )
        if applied.failed:
            self._failed_at = self._clock()
        elif current():
            through = max(turns[-1].id or 0, self.reflected_through(member_id))
            self._set_state(_watermark(member_id), through)
        return applied

    async def consolidate(self) -> Applied:
        """The weekly tidy of every space, each by itself (the shared one never deletes a note);
        the next one is a week away once the model has answered for each."""
        total = Applied()
        for store, shared in self._spaces():
            if not store.all():
                continue
            applied = await self._reflector.consolidate(
                store, model=self._model(), ledger=self.ledger(), shared=shared
            )
            total.written += applied.written
            total.deleted += applied.deleted
            total.refused += applied.refused
            total.failed = total.failed or applied.failed
        if total.failed:
            self._failed_at = self._clock()
        else:
            self._set_state(_CONSOLIDATED, self._now_ms())
        return total

    async def wait_idle(self) -> None:
        """Wait for the background pass and the folds that run, if any (the tests)."""
        if self._running is not None:
            await asyncio.gather(self._running, return_exceptions=True)
        await self.conversation.wait_for_fold()
        for conversation in list(self._conversations.values()):
            await conversation.wait_for_fold()

    async def aclose(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._running is not None and not self._running.done():
            self._running.cancel()
            await asyncio.gather(self._running, return_exceptions=True)
        await self.conversation.aclose()
        for conversation in list(self._conversations.values()):
            await conversation.aclose()

    # --- the RPC methods ---------------------------------------------------------------------------

    @staticmethod
    def _name(params: dict) -> str:
        name = params.get("name")
        if not valid_name(name):
            raise invalid_params("name must be the name of a memory")
        return name

    def _member(self, params: dict) -> int:
        """The assistant whose own memory a method is about: `member_id`, 0 (Raphael) by default.
        One that is not there is refused."""
        member_id = params.get("member_id", 0)
        if member_id is None:
            return 0
        if type(member_id) is not int or member_id < 0:
            raise invalid_params("member_id must be a whole number of 0 or more")
        if member_id and not self.member_exists(member_id):
            raise invalid_params("there is no such member")
        return member_id

    @staticmethod
    def _space_of(params: dict, key: str = "space") -> str:
        space = params.get(key, "own")
        if space is None:
            return "own"
        if space not in SPACES:
            raise invalid_params(f"{key} must be shared or own")
        return space

    def _store(self, params: dict) -> MemoryStore:
        return self.space(self._space_of(params), self._member(params))

    async def _create(self, params: dict) -> dict:
        if self._space_of(params) != "own":
            raise invalid_params("new notes belong to the member's own space")
        member_id = self._member(params)
        body = params.get("body")
        description = params.get("description")
        if not isinstance(body, str) or not body.strip():
            raise invalid_params("body must be nonempty text")
        if description is None:
            description = " ".join(body.split())[:80]
        if not isinstance(description, str):
            raise invalid_params("description must be text")
        name = "reference-" + secrets.token_hex(8)
        try:
            memory = self.store_for(member_id).write(name, type="reference", description=description,
                                                      body=body, origin="said")
        except MemoryRefused as refused:
            raise invalid_params(f"{refused.kind}: {refused}") from None
        return {"note": memory.view()}

    async def _list(self, params: dict) -> dict:
        space = self._space_of(params)
        member_id = self._member(params)
        memories = [
            {
                "name": m.name,
                "description": m.description,
                "type": m.type,
                "origin": m.origin,
                "updated": m.updated,
                "expires": m.expires,
            }
            for m in self.space(space, member_id).all()
        ]
        shown = None
        if space == "own":
            summary = self.conversation_for(member_id).summary()
            if summary.text:
                shown = {"text": summary.text, "updated_at_ms": summary.updated_at_ms}
        return {"memories": memories, "summary": shown}

    async def _get(self, params: dict) -> dict:
        memory = self._store(params).get(self._name(params))
        if memory is None:
            raise invalid_params("no memory has that name")
        return memory.view()

    async def _update(self, params: dict) -> dict:
        store = self._store(params)
        name = self._name(params)
        body = params.get("body")
        if not isinstance(body, str):
            raise invalid_params("body must be a string")
        description = params.get("description")
        if description is not None and not isinstance(description, str):
            raise invalid_params("description must be a string")
        old = store.get(name)
        if old is None:
            raise invalid_params("no memory has that name")
        try:
            memory = store.write(
                name,
                type=old.type,
                description=old.description if description is None else description,
                body=body,
                expires=old.expires,
                origin="said",
            )
        except MemoryRefused as refused:
            raise invalid_params(f"{refused.kind}: {refused}") from None
        return memory.view()

    async def _delete(self, params: dict) -> dict:
        return {"deleted": self._store(params).delete(self._name(params))}

    async def _clear(self, params: dict) -> dict:
        return {"deleted": self._store(params).clear()}

    async def _move(self, params: dict) -> dict:
        """Move a note between the shared memory and the own of `member_id`, as it is."""
        name = self._name(params)
        target = self._space_of(params, "to_space")
        member_id = self._member(params)
        source = "own" if target == "shared" else "shared"
        moving = self.space(source, member_id)
        if moving.get(name) is None:
            raise invalid_params("no memory has that name")
        if self.space(target, member_id).get(name) is not None:
            raise invalid_params("taken: the other space has a memory of that name")
        try:
            moved = move_note(name, moving, self.space(target, member_id))
        except OSError as exc:
            logger.warning("Could not move a memory: %s", type(exc).__name__)
            raise invalid_params("io: the memory could not be moved") from None
        return {"moved": moved}

    async def _summary_clear(self, params: dict) -> dict:
        return {"cleared": self.conversation_for(self._member(params)).clear_summary()}


@dataclass(frozen=True)
class MemberMemory:
    """The memory as a background task of one assistant sees it (`background.run.MemoryReader`):
    the shared index and its own, its notes read and its own thread searched; never a write."""

    memory: MemoryService
    member_id: int

    def index(self) -> MemoryIndex:
        return self.memory.index(self.member_id)

    def read(self, names: Sequence[str]) -> ToolResult:
        return self.memory.read(names, self.member_id)

    def search(self, query: str, *, limit: int = 5, exclude: Sequence[int] = ()) -> ToolResult:
        return self.memory.search(query, limit=limit, exclude=exclude, member_id=self.member_id)
