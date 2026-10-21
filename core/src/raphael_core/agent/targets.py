"""What opening taught Raphael: the names of web pages that opened, and the address each opened.

When the language model tells Raphael that "reddit" is `https://www.reddit.com/` and the page
opened, the name and the address are kept (the `open_targets` table), so that the next "open reddit"
needs no model, not even a network. Nothing else is kept: not what was said around the name, not a
page that was a search (what was searched for is what was said), not an address that did not open.

Like the history it is cleared with the user's clear, and pruned with the retention setting. Nothing
here logs a name or an address.
"""

import sqlite3
import time
from collections.abc import Callable

from .opening import validate_url

# The names kept; when there are more, the one used longest ago goes.
MAX_TARGETS = 500
_MAX_NAME_CHARS = 60


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


class OpenTargets:
    """The `open_targets` table. Use it on the thread that owns `conn`."""

    def __init__(
        self, conn: sqlite3.Connection, now_ms: Callable[[], int] = _wall_clock_ms
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms

    def get(self, name_key: str) -> str | None:
        """The address `name_key` (a name as `opening.clean_target` leaves it) opened before, if it
        may still be opened."""
        row = self._conn.execute(
            "SELECT url FROM open_targets WHERE name_key = ?", (name_key,)
        ).fetchone()
        return None if row is None else validate_url(row["url"])

    def learn(self, name_key: str, url: str) -> None:
        """Keep that `name_key` opened `url`. Not kept: an empty or long name, a page that may not
        be opened, a search or a fragment."""
        name_key = name_key.strip()
        if not name_key or len(name_key) > _MAX_NAME_CHARS or validate_url(url) is None:
            return
        if "?" in url or "#" in url:
            return
        now = self._now_ms()
        with self._conn:
            self._conn.execute(
                "INSERT INTO open_targets (name_key, url, uses, last_used_ms) VALUES (?, ?, 1, ?)"
                " ON CONFLICT(name_key) DO UPDATE SET url = excluded.url, uses = uses + 1,"
                " last_used_ms = excluded.last_used_ms",
                (name_key, url, now),
            )
            self._conn.execute(
                "DELETE FROM open_targets WHERE name_key IN (SELECT name_key FROM open_targets"
                " ORDER BY last_used_ms DESC, name_key LIMIT -1 OFFSET ?)",
                (MAX_TARGETS,),
            )

    def clear(self) -> int:
        """Forget every name; return how many went."""
        with self._conn:
            return self._conn.execute("DELETE FROM open_targets").rowcount

    def prune(self, older_than_ms: int) -> int:
        """Forget the names not used since `older_than_ms`; return how many went."""
        with self._conn:
            return self._conn.execute(
                "DELETE FROM open_targets WHERE last_used_ms < ?", (older_than_ms,)
            ).rowcount
