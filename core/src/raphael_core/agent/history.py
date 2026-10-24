"""The voice assistant's history: what it was asked and what it said, one row for each turn.

A turn is written when the assistant has its answer, or when it has asked for a yes or a no
(`status` "confirm"), and the row is completed when that is answered (`resolve`). The app's page of
conversations shows them, and they are the one continuous conversation the model reads: the
latest turns as they were said, the earlier ones through a summary (`memory.conversation`).

A turn also keeps the names and the types of the files the owner handed Raphael with the request
(`attachments`, so that the conversation view can show which), never the files themselves, which
stay in memory for the turn.

Each turn keeps where it came from (`source`: "voice", or a chat app such as "telegram") and the
model that answered, and its steps (`agent_steps`, `steps.Step`): what the fast tier did for it, so
the conversation view can show what happened between the question and the answer. The steps go with
their turns when the history is cleared.

Every turn is also in the search (`agent_turns_fts`, `search`): its question and its answer as
words and pairs of CJK characters (`memory.terms`), so that Raphael can look up what was said long
ago, a word of two Chinese characters too.

Each turn is in the thread of one member of the owner's team (`member_id`, raphael_core.members):
0 is Raphael, whose thread every turn was in before there were members, and the rest are the members
the owner made. Every reading here is of one thread (Raphael's, unless another is named), so each
member has a conversation, a summary and a search of its own. Two kinds of turn are not the owner's
own words: an exchange (`route` "exchange", `source` "member"), where one member asked another
something through `ask_member` and the other answered (it is written in both threads, `peer_id`
naming the other one), and a routine's run (`route` and `source` "routine"), whose `question` is the
routine's title and whose answer is what the run reported.

The conversation is not pruned: unlike the dictations, which go after the retention setting, the
turns stay until the user clears them (`clear`). Nothing here logs a question or an answer.
"""

# Postponed annotations: AgentHistory has a method called `list`, which would otherwise hide the
# builtin in the annotations of the methods defined after it.
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ..clock import wall_clock_ms
from ..protocol import RpcError
from ..memory.terms import QueryWord, fts_quote, index_terms, query_words
from .steps import Step
from .quotes import quote_from_wire

# Where a turn came from when nobody says: the hotkey and the overlay.
VOICE = "voice"


@dataclass
class Turn:
    id: int | None
    created_at_ms: int
    session_id: str
    question: str  # what the user said, after the dictionary's replacements
    route: str  # command, answer, delegate, discuss, chat or unclear
    prefix: str  # how Raphael answered, in the language the user reads
    text: str  # the short line of the overlay; empty for a command that was done
    detail: str | None  # the longer text of an answer, if it had one
    # "done", "confirm" (waiting for a yes or a no), "denied", "expired" or "error".
    status: str
    cost_usd: float = 0.0
    source: str = VOICE  # "voice", or the chat app the request came from
    model: str | None = None  # the model that answered, when one did
    # The files handed with the request, `[{"name", "mime"}]`; None when there were none.
    attachments: list[dict[str, str]] | None = None
    member_id: int = 0  # whose thread it is in: 0 for Raphael
    peer_id: int | None = None  # for an exchange, the member on the other side
    messages: list[str] = field(default_factory=list)
    quote: dict[str, str] | None = None


_COLUMNS = (
    "id, created_at_ms, session_id, question, route, prefix, text, detail, status, cost_usd,"
    " source, model, attachments, member_id, peer_id, quote"
)
_STEP_COLUMNS = "turn_id, kind, tool, server, outcome, ms, reason, at_ms, detail"
_TURN_COLUMNS = ", ".join(f"t.{name.strip()}" for name in _COLUMNS.split(","))
# The most turns a search returns to the model (the conversation page asks for more).
SEARCH_MAX = 10


def _attachments(saved: str | None) -> list[dict[str, str]] | None:
    """The files of a turn as saved (JSON), or None when it had none or what is saved is damaged."""
    if not saved:
        return None
    try:
        items = json.loads(saved)
    except ValueError:
        return None
    if not isinstance(items, list):
        return None
    kept = [
        {"name": item["name"], "mime": item["mime"]}
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("mime"), str)
    ]
    return kept or None


def _to_turn(row: sqlite3.Row) -> Turn:
    data = dict(row)
    data["attachments"] = _attachments(data.get("attachments"))
    try:
        data["quote"] = quote_from_wire(json.loads(data["quote"])) if data.get("quote") else None
    except (ValueError, TypeError, RpcError):
        # Damaged saved context must not prevent reading the rest of a thread.
        data["quote"] = None
    return Turn(**data)


def _filters(
    since_ms: int | None, exclude: Sequence[int], member_id: int = 0
) -> tuple[str, list[object]]:
    """The conditions of a search besides its words, as SQL to add to its WHERE, and their params:
    the turns of the thread of `member_id`, made from `since_ms` on, and none of the ids of
    `exclude`."""
    where, params = " AND t.member_id = ?", [member_id]
    if since_ms is not None:
        where += " AND t.created_at_ms >= ?"
        params.append(since_ms)
    ids = sorted({turn for turn in exclude if type(turn) is int})
    if ids:
        where += f" AND t.id NOT IN ({', '.join('?' * len(ids))})"
        params += ids
    return where, params


class AgentHistory:
    """The `agent_turns` table. Use it on the thread that owns `conn`."""

    def __init__(
        self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms

    def add(
        self,
        *,
        session_id: str,
        question: str,
        route: str,
        prefix: str,
        text: str,
        status: str,
        detail: str | None = None,
        cost_usd: float = 0.0,
        source: str = VOICE,
        model: str | None = None,
        steps: Sequence[Step] = (),
        attachments: Sequence[Mapping[str, str]] = (),
        member_id: int = 0,
        peer_id: int | None = None,
        quote: dict[str, str] | None = None,
    ) -> int:
        """Store a new turn, made now, in the thread of `member_id`, with the steps taken for it,
        and return its id. `attachments` are the names and the types of the files handed with the
        request; `peer_id` is the other member of an exchange."""
        files = [{"name": item["name"], "mime": item["mime"]} for item in attachments]
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO agent_turns (created_at_ms, session_id, question, route, prefix,"
                " text, detail, status, cost_usd, source, model, attachments, member_id, peer_id, quote)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._now_ms(),
                    session_id,
                    question,
                    route,
                    prefix,
                    text,
                    detail,
                    status,
                    cost_usd,
                    source,
                    model,
                    json.dumps(files, ensure_ascii=False) if files else None,
                    member_id,
                    peer_id,
                    json.dumps(quote, ensure_ascii=False) if quote else None,
                ),
            )
            turn_id = cursor.lastrowid
            assert turn_id is not None
            self._insert_steps(turn_id, steps)
            self._index(turn_id, question, text, detail)
        return turn_id

    def _index(self, turn_id: int, question: str, text: str, detail: str | None) -> None:
        """Put a turn into the search, in place of what it held for it."""
        saved = self._conn.execute(
            "SELECT text FROM agent_messages WHERE turn_id = ? ORDER BY seq", (turn_id,)
        ).fetchall()
        terms = index_terms(question, text, (detail or "") + "\n" + "\n".join(row[0] for row in saved))
        self._conn.execute("DELETE FROM agent_turns_fts WHERE rowid = ?", (turn_id,))
        self._conn.execute(
            "INSERT INTO agent_turns_fts (rowid, pairs, chars) VALUES (?, ?, ?)",
            (turn_id, terms.pairs, terms.chars),
        )

    def add_steps(self, turn_id: int, steps: Sequence[Step]) -> None:
        """Add steps to a turn that is already stored: the call made after a yes, say. They come
        after the ones it has."""
        with self._conn:
            self._insert_steps(turn_id, steps)

    def _insert_steps(self, turn_id: int, steps: Sequence[Step]) -> None:
        for step in steps:
            self._conn.execute(
                "INSERT INTO agent_steps (turn_id, n, at_ms, kind, tool, server, outcome, ms,"
                " reason, detail) VALUES (?, (SELECT COALESCE(MAX(n), 0) + 1 FROM agent_steps"
                " WHERE turn_id = ?), ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn_id,
                    turn_id,
                    step.at_ms or self._now_ms(),
                    step.kind,
                    step.tool,
                    step.server,
                    step.outcome,
                    max(int(step.ms), 0),
                    step.reason,
                    step.detail,
                ),
            )

    def steps(self, turn_ids: Iterable[int]) -> dict[int, list[Step]]:
        """The steps of each of `turn_ids` that has any, in the order they were taken."""
        ids = sorted({int(turn_id) for turn_id in turn_ids})
        found: dict[int, list[Step]] = {}
        # SQLite takes at most 999 parameters in old builds: a page of turns is far fewer.
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ", ".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT {_STEP_COLUMNS} FROM agent_steps WHERE turn_id IN ({marks})"
                " ORDER BY turn_id, n",
                chunk,
            )
            for row in rows:
                data = dict(row)
                found.setdefault(data.pop("turn_id"), []).append(Step(**data))
        return found

    def get(self, turn_id: int) -> Turn | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM agent_turns WHERE id = ?", (turn_id,)
        ).fetchone()
        return None if row is None else _to_turn(row)

    def add_message(self, turn_id: int, kind: str, text: str) -> None:
        """Commit one visible line immediately; cleared turns are never resurrected."""
        if kind not in ("progress", "reply", "event", "thinking", "media"):
            raise ValueError("unknown message kind")
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO agent_messages (turn_id, seq, kind, text, at_ms)"
                " SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ? FROM agent_messages"
                " WHERE turn_id = ? HAVING EXISTS (SELECT 1 FROM agent_turns WHERE id = ?)",
                (turn_id, kind, text, self._now_ms(), turn_id, turn_id),
            )
            if cursor.rowcount:
                turn = self.get(turn_id)
                if turn:
                    self._index(turn_id, turn.question, turn.text, turn.detail)

    def _with_messages(self, turns: list[Turn]) -> list[Turn]:
        saved = self.messages(turn.id for turn in turns if turn.id is not None)
        for turn in turns:
            turn.messages = [m["text"] for m in saved.get(turn.id, [])]
        return turns

    def messages(self, turn_ids: Iterable[int]) -> dict[int, list[dict]]:
        """The visible messages of each turn, in insertion order."""
        found: dict[int, list[dict]] = {}
        ids = sorted(set(turn_ids))
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            rows = self._conn.execute(
                "SELECT turn_id, seq, kind, text, at_ms FROM agent_messages"
                f" WHERE turn_id IN ({','.join('?' * len(chunk))}) ORDER BY turn_id, seq",
                chunk,
            )
            for row in rows:
                data = dict(row)
                found.setdefault(data.pop("turn_id"), []).append(data)
        return found

    def resolve(
        self,
        turn_id: int,
        *,
        prefix: str,
        text: str,
        status: str,
        detail: str | None = None,
        cost_usd: float = 0.0,
        model: str | None = None,
        route: str | None = None,
    ) -> bool:
        """Give a turn its outcome: what Raphael said in the end, how it ended, and the cost of
        getting there on top of what the turn cost so far (and the model that worded it, if one
        did). False if the turn is gone (the history was cleared meanwhile)."""
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE agent_turns SET prefix = ?, text = ?, detail = ?, status = ?,"
                " cost_usd = cost_usd + ?, model = COALESCE(?, model),"
                " route = COALESCE(?, route) WHERE id = ?",
                (prefix, text, detail, status, cost_usd, model, route, turn_id),
            )
            if cursor.rowcount:
                row = self._conn.execute(
                    "SELECT question FROM agent_turns WHERE id = ?", (turn_id,)
                ).fetchone()
                self._index(turn_id, row[0], text, detail)
        return cursor.rowcount > 0

    def list(
        self, limit: int = 50, before_id: int | None = None, *, member_id: int = 0
    ) -> list[Turn]:
        """The thread of `member_id`, newest first. `before_id` keeps the turns made before that
        one: the cursor for the next page."""
        conditions = " WHERE member_id = ?" + ("" if before_id is None else " AND id < ?")
        params: list[object] = [member_id] if before_id is None else [member_id, before_id]
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM agent_turns{conditions} ORDER BY id DESC LIMIT ?",
            [*params, limit],
        )
        return [_to_turn(row) for row in rows]

    def recent(self, count: int, *, member_id: int = 0) -> list[Turn]:
        """The last `count` turns of the thread of `member_id` that were answered, oldest first:
        the context of what is asked next. A turn that is waiting for a yes or a no, was refused
        or failed says nothing."""
        if count <= 0:
            return []
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM agent_turns WHERE status = 'done' AND member_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (member_id, count),
        )
        return self._with_messages([_to_turn(row) for row in reversed(rows.fetchall())])

    def after(
        self, turn_id: int, *, limit: int | None = None, member_id: int = 0
    ) -> list[Turn]:
        """The turns of the thread of `member_id` made after the turn `turn_id`, whatever their
        status, oldest first; with a `limit`, only the newest that many of them."""
        if limit is None:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM agent_turns WHERE id > ? AND member_id = ? ORDER BY id",
                (turn_id, member_id),
            ).fetchall()
            return self._with_messages([_to_turn(row) for row in rows])
        if limit <= 0:
            return []
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM agent_turns WHERE id > ? AND member_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (turn_id, member_id, limit),
        ).fetchall()
        return self._with_messages([_to_turn(row) for row in reversed(rows)])

    def last_id(self) -> int:
        """The id of the latest turn of any thread; 0 when there is none."""
        (found,) = self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM agent_turns").fetchone()
        return int(found)

    def latest_by_member(self) -> dict[int, Turn]:
        """The latest turn of each thread that has one, by member: what the member column shows
        under each name."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM agent_turns WHERE id IN"
            " (SELECT MAX(id) FROM agent_turns GROUP BY member_id)"
        ).fetchall()
        return {turn.member_id: turn for turn in map(_to_turn, rows)}

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        since_ms: int | None = None,
        exclude: Sequence[int] = (),
        most: int = SEARCH_MAX,
        member_id: int = 0,
    ) -> list[Turn]:
        """The answered turns that hold the words of `query`, best first (BM25, then the newest),
        at most `limit` and `most` (SEARCH_MAX for the model, more for the conversation page). Each
        word is looked for as it is written, a word of two Chinese characters too (`memory.terms`),
        and a turn that holds any of them is found. A word that is a long run of Chinese and is not
        found as it is written is looked for by its pairs of characters. `since_ms` keeps the turns made from then on. The turns of `exclude` (the
        ones the model already reads) are left out by the query itself, before the limit, so that
        the older turns behind them are found however many of them match. Only the thread of
        `member_id` is searched."""
        words = query_words(query)
        limit = max(1, min(limit, most))
        if not words:
            return []
        where, params = _filters(since_ms, exclude, member_id)
        found = self._find(words, limit, where, params)
        if not found and any(len(word.pairs) > 1 for word in words):
            pairs = dict.fromkeys(pair for word in words for pair in word.pairs)
            found = self._find([QueryWord(pair, (pair,)) for pair in pairs], limit, where, params)
        return found

    def _find(
        self, words: Sequence[QueryWord], limit: int, where: str, params: Sequence[object]
    ) -> list[Turn]:
        if not self._has_fts():
            return self._search_like(words, limit, where, params)
        match = " OR ".join(
            f"chars : {fts_quote(word.char)}"
            if word.char
            else "pairs : " + fts_quote(" ".join(word.pairs))
            for word in words
        )
        return self._match(match, limit, where, params)

    def _match(self, match: str, limit: int, where: str, params: Sequence[object]) -> list[Turn]:
        rows = self._conn.execute(
            f"SELECT {_TURN_COLUMNS} FROM agent_turns_fts JOIN agent_turns t"
            " ON t.id = agent_turns_fts.rowid"
            f" WHERE agent_turns_fts MATCH ? AND t.status = 'done'{where}"
            " ORDER BY bm25(agent_turns_fts), t.id DESC LIMIT ?",
            [match, *params, limit],
        ).fetchall()
        return [_to_turn(row) for row in rows]

    def _search_like(
        self, words: Sequence[QueryWord], limit: int, where: str, params: Sequence[object]
    ) -> list[Turn]:
        """The search where this SQLite has no FTS5: the same terms, read with LIKE, newest first.
        The table holds a few thousand turns a year, so a scan takes milliseconds."""
        conditions: list[str] = []
        terms: list[object] = []
        for word in words:
            if word.char:
                conditions.append("f.chars LIKE ?")
                terms.append(f"%{word.char}%")
            else:
                conditions.append("(' ' || f.pairs || ' ') LIKE ?")
                terms.append(f"% {' '.join(word.pairs)} %")
        rows = self._conn.execute(
            f"SELECT {_TURN_COLUMNS} FROM agent_turns_fts f JOIN agent_turns t ON t.id = f.rowid"
            f" WHERE ({' OR '.join(conditions)}) AND t.status = 'done'{where}"
            " ORDER BY t.id DESC LIMIT ?",
            [*terms, *params, limit],
        ).fetchall()
        return [_to_turn(row) for row in rows]

    def _has_fts(self) -> bool:
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'agent_turns_fts'"
        ).fetchone()
        return row is not None and "fts5" in (row[0] or "").lower()

    def clear(self) -> int:
        """Delete every turn of every thread, with its steps and its place in the search; return
        how many went."""
        with self._conn:
            self._conn.execute("DELETE FROM agent_messages")
            self._conn.execute("DELETE FROM agent_steps")
            self._conn.execute("DELETE FROM agent_turns_fts")
            cursor = self._conn.execute("DELETE FROM agent_turns")
        return cursor.rowcount

    def for_session(self, session_id: str) -> list[Turn]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM agent_turns WHERE session_id = ?", (session_id,)
        ).fetchall()
        return [_to_turn(row) for row in rows]

    def delete(self, turn_id: int) -> bool:
        """Remove a single turn and its public lines, steps and search terms."""
        with self._conn:
            self._conn.execute("UPDATE worker_tasks SET turn_id = NULL WHERE turn_id = ?", (turn_id,))
            self._conn.execute("DELETE FROM agent_messages WHERE turn_id = ?", (turn_id,))
            self._conn.execute("DELETE FROM agent_steps WHERE turn_id = ?", (turn_id,))
            self._conn.execute("DELETE FROM agent_turns_fts WHERE rowid = ?", (turn_id,))
            cursor = self._conn.execute("DELETE FROM agent_turns WHERE id = ?", (turn_id,))
        return cursor.rowcount > 0

    def clear_member(self, member_id: int) -> int:
        """Delete the thread of `member_id` and the exchanges of the other threads with it, with
        their steps and their place in the search; return how many turns went."""
        both = (member_id, member_id)
        ids = "SELECT id FROM agent_turns WHERE member_id = ? OR peer_id = ?"
        with self._conn:
            self._conn.execute(f"DELETE FROM agent_messages WHERE turn_id IN ({ids})", both)
            self._conn.execute(f"DELETE FROM agent_steps WHERE turn_id IN ({ids})", both)
            self._conn.execute(f"DELETE FROM agent_turns_fts WHERE rowid IN ({ids})", both)
            cursor = self._conn.execute(
                "DELETE FROM agent_turns WHERE member_id = ? OR peer_id = ?", both
            )
        return cursor.rowcount

    def expire_pending(self) -> int:
        """Mark the turns that still wait for a yes or a no as expired: the confirmation lived in
        a process that is gone. Returns how many there were."""
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE agent_turns SET status = 'expired' WHERE status IN ('confirm', 'running')"
            )
        return cursor.rowcount
