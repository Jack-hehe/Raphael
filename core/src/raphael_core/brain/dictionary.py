"""The personal dictionary: words Raphael should spell right, and the ways they get misheard."""

# Postponed annotations: DictionaryStore has a method called `list`, which would otherwise hide the
# builtin in the `list[...]` annotations of the methods defined after it.
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# "learned": added by itself, from a correction the user made to a pasted dictation.
_SOURCES = ("manual", "suggested", "learned")


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass
class Entry:
    id: int
    word: str
    misheard: list[str]  # spellings speech-to-text tends to produce for the word
    source: str  # "manual", "suggested" or "learned"


def _clean_word(word: str) -> str:
    word = word.strip()
    if not word:
        raise ValueError("The word is empty")
    return word


def _clean_forms(misheard: Sequence[str]) -> list[str]:
    """The forms stripped, without blanks or repeats. A blank one would match everywhere."""
    if isinstance(misheard, str):
        # Iterating a string gives characters, and each would become a replacement rule.
        raise TypeError("misheard must be a list of strings, not a single string")
    forms: list[str] = []
    for form in misheard:
        form = form.strip()
        if form and form not in forms:
            forms.append(form)
    return forms


def _to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"], word=row["word"], misheard=json.loads(row["misheard"]), source=row["source"]
    )


class DictionaryStore:
    def __init__(
        self, conn: sqlite3.Connection, now_ms: Callable[[], int] = _wall_clock_ms
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms

    def add(self, word: str, misheard: Sequence[str] = (), source: str = "manual") -> Entry:
        """Add a word, or merge `misheard` into the entry it already has and return that entry.

        An existing entry keeps its source. Raises ValueError for an empty word or an unknown
        source, and TypeError when `misheard` is one string instead of a list.
        """
        word = _clean_word(word)
        forms = _clean_forms(misheard)
        if source not in _SOURCES:
            raise ValueError(f"Unknown source {source!r}: expected one of {_SOURCES}")
        with self._conn:
            row = self._conn.execute(
                "SELECT id, word, misheard, source FROM dictionary WHERE word = ?", (word,)
            ).fetchone()
            if row is None:
                cursor = self._conn.execute(
                    "INSERT INTO dictionary (word, misheard, source, created_at_ms)"
                    " VALUES (?, ?, ?, ?)",
                    (word, json.dumps(forms, ensure_ascii=False), source, self._now_ms()),
                )
                return Entry(id=cursor.lastrowid, word=word, misheard=forms, source=source)
            entry = _to_entry(row)
            merged = entry.misheard + [form for form in forms if form not in entry.misheard]
            if merged != entry.misheard:
                self._conn.execute(
                    "UPDATE dictionary SET misheard = ? WHERE id = ?",
                    (json.dumps(merged, ensure_ascii=False), entry.id),
                )
                entry.misheard = merged
            return entry

    def list(self) -> list[Entry]:
        """Newest first."""
        rows = self._conn.execute(
            "SELECT id, word, misheard, source FROM dictionary ORDER BY created_at_ms DESC, id DESC"
        )
        return [_to_entry(row) for row in rows]

    def delete(self, id: int) -> bool:
        """Delete an entry. A learned word that is deleted is dismissed too: the user did not want
        it, so it is not learned again."""
        with self._conn:
            row = self._conn.execute(
                "SELECT word, source FROM dictionary WHERE id = ?", (id,)
            ).fetchone()
            cursor = self._conn.execute("DELETE FROM dictionary WHERE id = ?", (id,))
            if row is not None and row["source"] == "learned":
                self._conn.execute(
                    "INSERT OR IGNORE INTO dismissed_suggestions (word) VALUES (?)", (row["word"],)
                )
        return cursor.rowcount > 0

    def learn(self, word: str, misheard: str) -> bool:
        """Learn from a correction: the user replaced `misheard` with `word` in a pasted dictation.

        A new word is added as "learned" with `misheard` as its form; a known word gets the form
        appended. Returns whether the dictionary changed. Nothing is learned when either side is
        blank, the two differ only in letter case, the word was dismissed (in any letter case),
        `misheard` is itself a word of the dictionary (the user changed a known word into another),
        or `word` is a misheard form of some entry (the two rules would swap the words back and
        forth).
        """
        word, misheard = word.strip(), misheard.strip()
        if not word or not misheard or word.casefold() == misheard.casefold():
            return False
        if word.casefold() in {w.casefold() for w in self.dismissed()}:
            return False
        entries = self.list()
        if any(entry.word == misheard for entry in entries):
            return False
        if any(word in entry.misheard for entry in entries):
            return False
        known = next((entry for entry in entries if entry.word == word), None)
        if known is not None and misheard in known.misheard:
            return False
        self.add(word, [misheard], source="learned")
        return True

    def hint_words(self) -> list[str]:
        """The words, newest first."""
        return [entry.word for entry in self.list()]

    def replacements(self) -> list[tuple[str, str]]:
        """`(misheard, word)` for every misheard form, the longest form first.

        Applied in this order, a long form gets its chance before a shorter one inside it
        ("Lafia 先生" before "Lafia"). Forms of equal length keep the order of `list()`.
        """
        pairs = [(form, entry.word) for entry in self.list() for form in entry.misheard]
        return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)

    def dismiss(self, word: str) -> None:
        """Remember that the user does not want `word` suggested again."""
        word = _clean_word(word)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO dismissed_suggestions (word) VALUES (?)", (word,)
            )

    def dismissed(self) -> set[str]:
        rows = self._conn.execute("SELECT word FROM dismissed_suggestions")
        return {row["word"] for row in rows}
