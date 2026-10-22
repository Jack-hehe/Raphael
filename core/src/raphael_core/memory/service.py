"""Raphael's memory as one service: the files (`store`), the conversation's summary
(`conversation`), the reflection pass and the weekly consolidation (`reflection`), the tools the
assistant reaches them by, and the RPC methods of the settings page.

The RPC methods (the main window only; the shell checks their params before they come here):

- `memory.list {}` -> `{memories: [{name, description, type, origin, updated, expires}],
  summary: {text, updated_at_ms} | null}`
- `memory.get {name}` -> the memory, with its body and the turns it came from
- `memory.update {name, body, description?}` -> the memory as it is now. An edit by the user is a
  memory the user stated (`origin: said`); it passes the same checks as any other write.
- `memory.delete {name}` -> `{deleted}`, `memory.clear {}` -> `{deleted}` (every memory)
- `memory.summary_clear {}` -> `{cleared}`: the conversation's summary is forgotten

The tools of the assistant (`agent.gate` knows them, and their tiers): `memory_read` and
`history_search` only read (T0); `memory_write` and `memory_delete` change the memory (T1, which
runs at once, with no yes asked for). The fast tier writes only from the owner's own turn
(`apply_said`, see `agent.service`); the background worker may read and search, never write, since
it reads what services return.

The reflection pass runs in the background on the cheap model (the `agent_model` setting): after
REFLECT_AFTER_TURNS answered turns it has not read, or once the conversation has been quiet for
QUIET_S with any such turn, and once soon after the app starts when one is waiting. The weekly
consolidation runs with it when the last one is a week old. Both need the key and room under the
month's limit, and both are written to the spend with the purpose "memory". The turns are not
deleted by age any more, so nothing has to be reflected on in a hurry before a prune.

Nothing here logs a memory, a question or an answer.
"""

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from datetime import datetime, tzinfo
from pathlib import Path

from ..agent.history import AgentHistory, Turn
from ..agent.ledger import Ledger
from ..agent.tools import ToolResult
from ..brain.openrouter import OpenRouterClient
from ..brain.secrets import KeyStore
from ..brain.settings import DEFAULTS, SettingsStore
from ..brain.spend import SpendTracker
from ..clock import wall_clock_ms
from ..protocol import invalid_params
from ..router import Router as RpcRouter
from .conversation import SUMMARY_PURPOSE, Conversation
from .reflection import MAX_TURNS, Action, Applied, Reflector, apply_actions
from .store import MemoryRefused, MemoryStore, valid_name

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
# What a search gives of a turn, in characters, at most.
SNIPPET_CHARS = 150
MAX_READ = 3


def _snippet(text: str, words: Sequence[str]) -> str:
    """`text` cut to SNIPPET_CHARS around the first of `words` it holds."""
    text = " ".join((text or "").split())
    if len(text) <= SNIPPET_CHARS:
        return text
    folded = text.casefold()
    at = min((folded.find(w) for w in words if w and folded.find(w) >= 0), default=0)
    start = max(0, at - SNIPPET_CHARS // 3)
    cut = text[start : start + SNIPPET_CHARS]
    return ("…" if start else "") + cut + ("…" if start + SNIPPET_CHARS < len(text) else "")


class MemoryService:
    """The memory. `conn` is the open database (used on the event loop only), `root` the memory
    folder, `client` the OpenRouter client, `keys` where the key lives. `now_ms` is the wall clock,
    `tz` the time zone of the spend's months, `clock` a monotonic clock in seconds and `now` the
    local time. `quiet_s` and `start_delay_s` are for the tests."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        root: Path,
        client: OpenRouterClient,
        keys: KeyStore,
        *,
        now_ms: Callable[[], int] = wall_clock_ms,
        tz: tzinfo | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        quiet_s: float = QUIET_S,
        start_delay_s: float = START_DELAY_S,
    ) -> None:
        self._conn = conn
        self._keys = keys
        self._now_ms = now_ms
        self._clock = clock
        self._quiet_s = quiet_s
        self._start_delay_s = start_delay_s
        self.store = MemoryStore(root, now=now)
        self._history = AgentHistory(conn, now_ms)
        self._settings = SettingsStore(conn)
        self._spend = SpendTracker(conn, now_ms, tz)
        self._reflector = Reflector(client)
        self.conversation = Conversation(
            conn, client, now_ms=now_ms, clock=clock, reflected_through=self.reflected_through
        )
        self._timer: asyncio.TimerHandle | None = None
        self._running: asyncio.Task[None] | None = None
        self._failed_at: float | None = None

    def register(self, router: RpcRouter) -> None:
        methods = {
            "memory.list": self._list,
            "memory.get": self._get,
            "memory.update": self._update,
            "memory.delete": self._delete,
            "memory.clear": self._clear,
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

    def reflected_through(self) -> int:
        """The last turn the reflection pass has read (0: none)."""
        return self._state(_REFLECTED)

    # --- for the assistant -------------------------------------------------------------------------

    def index(self) -> str:
        """The index of the memory, for the prompt: empty when the folder cannot be read."""
        try:
            return self.store.index()
        except OSError as exc:
            logger.warning("Could not read the memory: %s", type(exc).__name__)
            return ""

    def apply_said(self, actions: Sequence[Action], turn_id: int) -> Applied:
        """Write what the owner asked to be remembered (or forgotten) in the turn `turn_id`: the
        memories are ones the owner stated. Each is checked like any other write."""
        said = [
            Action(
                op=a.op,
                name=a.name,
                type=a.type,
                description=a.description,
                body=a.body,
                expires=a.expires,
                origin="said",
            )
            for a in actions
        ]
        try:
            return apply_actions(said, self.store, turns=[turn_id] if turn_id else [])
        except OSError as exc:
            logger.warning("Could not write to the memory: %s", type(exc).__name__)
            return Applied(refused=["io"] * len(said))

    def note_turn(self, names: Sequence[str], turn_id: int) -> None:
        """Add the turn `turn_id` to the turns the memories `names` came from (it was written
        after them)."""
        if not turn_id:
            return
        for name in names:
            try:
                self.store.add_turn(name, turn_id)
            except OSError as exc:
                logger.warning("Could not note a memory's turn: %s", type(exc).__name__)

    def forget_turns(self) -> None:
        """The conversation was cleared: the reflection pass starts again from the first turn
        (the ids of the turns start again too)."""
        self._set_state(_REFLECTED, 0)

    def read(self, names: Sequence[str]) -> ToolResult:
        """`memory_read`: the memories of `names` (at most MAX_READ), each with its body and when it
        was last updated; the names that are not memories are listed as missing."""
        found, missing = [], []
        for name in list(dict.fromkeys(names))[:MAX_READ]:
            memory = self.store.get(name) if valid_name(name) else None
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
                }
            )
        return ToolResult("memory_read", True, {"memories": found, "missing": missing})

    def search(self, query: str, *, limit: int = 5, exclude: Sequence[int] = ()) -> ToolResult:
        """`history_search`: the past turns that hold the words of `query`, best first, each as its
        date, the question and Raphael's answer, cut short. No model call."""
        try:
            turns = self._history.search(query, limit=limit + len(exclude))
        except sqlite3.Error as exc:
            logger.warning("Could not search the conversation: %s", type(exc).__name__)
            return ToolResult("history_search", False, None, "error")
        words = [w.casefold() for w in query.split()]
        skip = set(exclude)
        found = []
        for turn in turns:
            if turn.id in skip:
                continue
            when = datetime.fromtimestamp(turn.created_at_ms / 1000).strftime("%Y-%m-%d %H:%M")
            answer = turn.detail if turn.detail and len(turn.detail) > len(turn.text) else turn.text
            found.append(
                {
                    "turn": turn.id,
                    "when": when,
                    "question": _snippet(turn.question, words),
                    "answer": _snippet(answer or turn.prefix, words),
                }
            )
            if len(found) >= limit:
                break
        return ToolResult("history_search", True, {"turns": found})

    # --- the background ----------------------------------------------------------------------------

    def _model(self) -> str:
        model = self._settings.get("agent_model")
        return model.strip() if isinstance(model, str) and model.strip() else DEFAULTS["agent_model"]

    def may_spend(self) -> bool:
        """Whether a call of the memory's own may be made: there is a key and room under the
        month's limit."""
        try:
            if not (self._keys.get() or "").strip():
                return False
            return not self._spend.is_over(self._settings.get("monthly_limit_usd"))
        except Exception as exc:  # the key store's message could hold the key
            logger.warning("The memory could not check the key or the limit: %s", type(exc).__name__)
            return False

    def ledger(self) -> Ledger:
        return Ledger(self._spend, PURPOSE)

    def after_turn(self) -> None:
        """A turn was written: fold the conversation if it is time, and run the reflection pass now
        if enough turns wait for it, or once the conversation has been quiet for a while."""
        if self.may_spend():
            self.conversation.schedule_fold(self._model(), self.ledger)
        if self._waiting(limit=REFLECT_AFTER_TURNS) >= REFLECT_AFTER_TURNS:
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
        self._timer = None
        if self.reflection_due() or self.consolidation_due():
            self._spawn()

    def _waiting(self, limit: int = MAX_TURNS) -> int:
        try:
            return len(self._unread(limit))
        except sqlite3.Error:
            return 0

    def _unread(self, limit: int = MAX_TURNS) -> list[Turn]:
        """The answered turns the reflection pass has not read, the latest `limit` of them."""
        turns = self._history.after(self.reflected_through(), limit=limit * 3)
        return [turn for turn in turns if turn.status == "done"][-limit:]

    def reflection_due(self) -> bool:
        """Whether the reflection pass should run now: REFLECT_AFTER_TURNS turns wait, or one does
        and the last turn is QUIET_S old."""
        try:
            unread = self._unread()
        except sqlite3.Error:
            return False
        if len(unread) >= REFLECT_AFTER_TURNS:
            return True
        if not unread:
            return False
        latest = self._history.after(0, limit=1)
        last_ms = latest[-1].created_at_ms if latest else unread[-1].created_at_ms
        return self._now_ms() - last_ms >= self._quiet_s * 1000

    def consolidation_due(self) -> bool:
        if not self.store.all():
            return False
        return self._now_ms() - self._state(_CONSOLIDATED) >= CONSOLIDATE_EVERY_MS

    def _spawn(self) -> None:
        if self._running is not None and not self._running.done():
            return
        if self._failed_at is not None and self._clock() - self._failed_at < RETRY_S:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._running = loop.create_task(self.run_due())

    async def run_due(self) -> None:
        """The reflection pass and the consolidation, whichever is due, one after the other."""
        if not self.may_spend():
            return
        try:
            if self.reflection_due():
                await self.reflect()
            if self.consolidation_due():
                await self.consolidate()
        except Exception as exc:  # the background must not break the conversation
            self._failed_at = self._clock()
            logger.warning("The memory's background pass failed: %s", type(exc).__name__)

    async def reflect(self) -> Applied:
        """One reflection pass over the turns it has not read. The watermark moves past them
        when the model answered, whatever it made of them; a model that failed leaves them for the
        next pass, which waits RETRY_S."""
        turns = self._unread()
        if not turns:
            return Applied()
        applied = await self._reflector.reflect(
            self.store, turns, model=self._model(), ledger=self.ledger()
        )
        if applied.failed:
            self._failed_at = self._clock()
        else:
            self._set_state(_REFLECTED, max(turns[-1].id or 0, self.reflected_through()))
        return applied

    async def consolidate(self) -> Applied:
        """The weekly tidy; the next one is a week away once the model has answered."""
        applied = await self._reflector.consolidate(
            self.store, model=self._model(), ledger=self.ledger()
        )
        if applied.failed:
            self._failed_at = self._clock()
        else:
            self._set_state(_CONSOLIDATED, self._now_ms())
        return applied

    async def wait_idle(self) -> None:
        """Wait for the background pass and the fold that run, if any (the tests)."""
        if self._running is not None:
            await asyncio.gather(self._running, return_exceptions=True)
        await self.conversation.wait_for_fold()

    async def aclose(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._running is not None and not self._running.done():
            self._running.cancel()
            await asyncio.gather(self._running, return_exceptions=True)
        await self.conversation.aclose()

    # --- the RPC methods ---------------------------------------------------------------------------

    @staticmethod
    def _name(params: dict) -> str:
        name = params.get("name")
        if not valid_name(name):
            raise invalid_params("name must be the name of a memory")
        return name

    async def _list(self, params: dict) -> dict:
        memories = [
            {
                "name": m.name,
                "description": m.description,
                "type": m.type,
                "origin": m.origin,
                "updated": m.updated,
                "expires": m.expires,
            }
            for m in self.store.all()
        ]
        summary = self.conversation.summary()
        shown = (
            {"text": summary.text, "updated_at_ms": summary.updated_at_ms}
            if summary.text
            else None
        )
        return {"memories": memories, "summary": shown}

    async def _get(self, params: dict) -> dict:
        memory = self.store.get(self._name(params))
        if memory is None:
            raise invalid_params("no memory has that name")
        return memory.view()

    async def _update(self, params: dict) -> dict:
        name = self._name(params)
        body = params.get("body")
        if not isinstance(body, str):
            raise invalid_params("body must be a string")
        description = params.get("description")
        if description is not None and not isinstance(description, str):
            raise invalid_params("description must be a string")
        old = self.store.get(name)
        if old is None:
            raise invalid_params("no memory has that name")
        try:
            memory = self.store.write(
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
        return {"deleted": self.store.delete(self._name(params))}

    async def _clear(self, params: dict) -> dict:
        return {"deleted": self.store.clear()}

    async def _summary_clear(self, params: dict) -> dict:
        return {"cleared": self.conversation.clear_summary()}

