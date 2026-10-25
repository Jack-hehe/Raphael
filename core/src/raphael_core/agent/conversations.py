"""Named conversations, with a stable default entry for voice, routines and messaging.

Conversation IDs are never reused. A caller that omits an ID writes to the member's default;
creating another conversation does not silently redirect unattended work into that context.
"""

import sqlite3
from collections.abc import Callable

from ..clock import wall_clock_ms
from ..protocol import invalid_params

TITLE_MAX = 80


def short_title(question: str) -> str:
    return " ".join(question.split())[:TITLE_MAX]


class Conversations:
    def __init__(self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms):
        self.conn = conn
        self.now_ms = now_ms

    def get(self, conversation_id: int, member_id: int | None = None) -> dict:
        if type(conversation_id) is not int or conversation_id <= 0:
            raise invalid_params("conversation_id must be a positive whole number")
        row = self.conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None or (member_id is not None and row["member_id"] != member_id):
            raise invalid_params("there is no such conversation for this member")
        return dict(row)

    def main(self, member_id: int = 0) -> int:
        row = self.conn.execute(
            "SELECT id FROM conversations WHERE member_id = ? AND is_default = 1"
            " ORDER BY updated_at_ms DESC, id DESC LIMIT 1", (member_id,),
        ).fetchone()
        if row is not None:
            return row[0]
        return self.create(member_id, default=True)["id"]

    def resolve(self, member_id: int, conversation_id: int | None = None) -> int:
        return self.main(member_id) if conversation_id is None else self.get(conversation_id, member_id)["id"]

    def quick(self) -> int:
        """The single, lazy, persistent hotkey conversation; never the formal default."""
        row = self.conn.execute("SELECT id FROM conversations WHERE kind = 'quick'").fetchone()
        if row is not None:
            return row[0]
        now = self.now_ms()
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO conversations"
                " (member_id, title, created_at_ms, updated_at_ms, is_default, kind)"
                " VALUES (0, '', ?, ?, 0, 'quick')", (now, now),
            )
        return self.conn.execute("SELECT id FROM conversations WHERE kind = 'quick'").fetchone()[0]

    def create(self, member_id: int, *, default: bool = False) -> dict:
        now = self.now_ms()
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO conversations (member_id, title, created_at_ms, updated_at_ms, is_default)"
                " VALUES (?, '', ?, ?, ?)", (member_id, now, now, int(default)),
            )
        return self.get(cursor.lastrowid)

    def list(self, member_id: int, query: str = "") -> list[dict]:
        self.main(member_id)
        rows = self.conn.execute(
            "SELECT * FROM conversations WHERE member_id = ? ORDER BY (kind = 'quick') DESC, updated_at_ms DESC, id DESC", (member_id,),
        )
        return [dict(row) for row in rows if query.casefold() in row["title"].casefold()]

    def touch(self, conversation_id: int, question: str = "") -> None:
        self.conn.execute(
            "UPDATE conversations SET updated_at_ms = ?, title = CASE WHEN title = '' THEN ? ELSE title END WHERE id = ?",
            (self.now_ms(), short_title(question), conversation_id),
        )

    def rename(self, conversation_id: int, title: str) -> dict:
        if self.get(conversation_id)["kind"] == "quick":
            raise invalid_params("the quick conversation has a fixed localized title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > TITLE_MAX:
            raise invalid_params(f"title must be 1 to {TITLE_MAX} characters")
        with self.conn:
            self.conn.execute("UPDATE conversations SET title = ?, updated_at_ms = ? WHERE id = ?",
                              (title.strip(), self.now_ms(), conversation_id))
        return self.get(conversation_id)
