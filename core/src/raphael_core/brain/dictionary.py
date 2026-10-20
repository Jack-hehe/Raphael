"""The personal dictionary: words Raphael should spell right, and the ways they get misheard.

An entry is a word and the forms speech-to-text tends to write for it. Every form is a replacement
rule: `replacements()` turns it into the word, in the transcript and again in the cleaned text. What
the user adds is taken as it is.

What the brain learns from a correction the user made by hand is not (`learn_pair`). Such a pair
is often no recognition error (a synonym, a word the user changed their mind about), and a
replacement rule rewrites right text everywhere. So the corrected word becomes a term of the
dictionary at once, a hint for the speech-to-text engine and the cleanup model, and the pair is
kept as evidence with a count. The form stays a hint in the cleanup prompt (`hint_forms`), where
the model reads it in context. It becomes a replacement rule only when the same pair is seen a
second time, its form is distinctive and sounds like the word (`mishearing.may_become_rule`), and
the rule leaves the replacements working as they did. When the user changes a learned rule's text
back, that is evidence against it: the rule is withdrawn and the pair is not learned again, but the
word stays.
"""

# Postponed annotations: DictionaryStore has a method called `list`, which would otherwise hide the
# builtin in the `list[...]` annotations of the methods defined after it.
from __future__ import annotations

import enum
import json
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from .mishearing import may_become_rule
from .textnorm import apply_replacements, replacement_key

# "learned": added by itself, from a correction the user made to a pasted dictation.
_SOURCES = ("manual", "suggested", "learned")
# What the evidence about a (word, form) pair says. "pending": seen, and not a rule (yet), so the
# form is a hint. "rule": the pair became a rule by learning. "blocked": the user changed the rule's
# text back, so the pair is not learned again.
_PENDING, _RULE, _BLOCKED = "pending", "rule", "blocked"
# A pair that is fit for it becomes a replacement rule when it is seen for the second time.
RULE_AFTER_SIGHTINGS = 2


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass
class Entry:
    id: int
    word: str
    misheard: list[str]  # spellings speech-to-text tends to produce for the word: its rules
    source: str  # "manual", "suggested" or "learned"


class Learned(enum.StrEnum):
    """What `DictionaryStore.learn_pair` did with one correction."""

    NOTHING = "nothing"  # refused, or known already
    TERM = "term"  # the word is a new term of the dictionary; the pair is a hint
    PENDING = "pending"  # the pair is a hint: evidence that is not a rule (yet)
    RULE = "rule"  # the pair is a replacement rule now
    REVERTED = "reverted"  # a rule the brain learned was withdrawn, and the pair is blocked


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


def _rules(entries: Sequence[Entry]) -> list[tuple[str, str]]:
    """`(misheard, word)` for every form of the entries, the longest form first. Forms of equal
    length keep the order of the entries."""
    pairs = [(form, entry.word) for entry in entries for form in entry.misheard]
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


def _keeps_replacements_working(
    entries: Sequence[Entry], known: Entry | None, word: str, form: str
) -> bool:
    """Whether the replacements still behave with `form` added as a rule for `word`.

    The whole pipeline is run with the new rule set: `form` must come out as `word`, and `word` must
    come out as itself. A rule that fails this would write another word in place of `word` (with
    "cloud" turning into "Claude", the rule "clod storage" for "cloud storage" would produce "Claude
    storage"), or change it each time the rules are applied. `known` is the entry of `word`, if it
    has one.
    """
    if known is None:
        grown = [Entry(0, word, [form], "learned"), *entries]  # a new word is the newest
    else:
        grown = [
            replace(entry, misheard=[*entry.misheard, form]) if entry.id == known.id else entry
            for entry in entries
        ]
    rules = _rules(grown)
    return apply_replacements(form, rules) == word and apply_replacements(word, rules) == word


class DictionaryStore:
    def __init__(
        self, conn: sqlite3.Connection, now_ms: Callable[[], int] = _wall_clock_ms
    ) -> None:
        self._conn = conn
        self._now_ms = now_ms

    def add(self, word: str, misheard: Sequence[str] = (), source: str = "manual") -> Entry:
        """Add a word, or merge `misheard` into the entry it already has and return that entry.

        An existing entry keeps its source. The forms are replacement rules as given: they are not
        judged as a learned pair is, and any evidence the brain had gathered about the same pair
        (a hint, or a rule the user rejected) is dropped. Raises ValueError for an empty word or an
        unknown source, and TypeError when `misheard` is one string instead of a list.
        """
        word = _clean_word(word)
        forms = _clean_forms(misheard)
        if source not in _SOURCES:
            raise ValueError(f"Unknown source {source!r}: expected one of {_SOURCES}")
        with self._conn:
            self._conn.executemany(
                "DELETE FROM dictionary_evidence WHERE word_key = ? AND form_key = ?",
                [(replacement_key(word), replacement_key(form)) for form in forms],
            )
            return self._put(word, forms, source)

    def _put(self, word: str, forms: list[str], source: str) -> Entry:
        """Add the word, or merge `forms` into its entry; part of a transaction."""
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
        """Delete an entry, and the hints the brain had about its word. A learned word that is
        deleted is dismissed too: the user did not want it, so it is not learned again. A pair the
        user rejected stays blocked."""
        with self._conn:
            row = self._conn.execute(
                "SELECT word, source FROM dictionary WHERE id = ?", (id,)
            ).fetchone()
            cursor = self._conn.execute("DELETE FROM dictionary WHERE id = ?", (id,))
            if row is not None:
                self._conn.execute(
                    "DELETE FROM dictionary_evidence WHERE word_key = ? AND status != ?",
                    (replacement_key(row["word"]), _BLOCKED),
                )
            if row is not None and row["source"] == "learned":
                self._conn.execute(
                    "INSERT OR IGNORE INTO dismissed_suggestions (word) VALUES (?)", (row["word"],)
                )
        return cursor.rowcount > 0

    # --- learning -------------------------------------------------------------------------------

    def learn_pair(self, word: str, misheard: str) -> Learned:
        """Learn from a correction: the user replaced `misheard` with `word` in a pasted dictation.
        Says what it did (see the module's docstring).

        A new word is added as "learned"; a known word is left as it is. The pair is kept as
        evidence with a count. On the second sighting it becomes a replacement rule, added to the
        word's entry, if its form is distinctive, the two sound alike and the rule keeps the
        replacements working (`mishearing.may_become_rule`, `_keeps_replacements_working`);
        otherwise it stays a hint, however often it is seen.

        Nothing is learned when either side is blank, the two differ only in letter case, the word
        was dismissed (in any letter case), the pair was blocked by a revert, `misheard` is itself
        a word of the dictionary (the user changed a known word into another), `word` is a form of
        some entry (the two would swap the words back and forth), or `misheard` is a form of
        another word. When `misheard` is a word of the dictionary and `word` a form the brain
        learned for it, the user has changed what a rule or a hint wrote back to the original: the
        form is dropped from the word's rules or hints and the pair is blocked. The word itself
        stays, and is not dismissed.
        """
        word, misheard = word.strip(), misheard.strip()
        if not word or not misheard or word.casefold() == misheard.casefold():
            return Learned.NOTHING
        if word.casefold() in {w.casefold() for w in self.dismissed()}:
            return Learned.NOTHING
        entries = self.list()
        word_key, form_key = replacement_key(word), replacement_key(misheard)
        by_key: dict[str, Entry] = {}
        for entry in entries:
            by_key.setdefault(replacement_key(entry.word), entry)

        undone = self._evidence(form_key, word_key)
        if undone is not None and undone["status"] != _BLOCKED and form_key in by_key:
            self._withdraw(by_key[form_key], word_key)
            return Learned.REVERTED
        own = self._evidence(word_key, form_key)
        if own is not None and own["status"] == _BLOCKED:
            return Learned.NOTHING
        if form_key in by_key:
            return Learned.NOTHING
        owners = self._form_owners(entries)
        # `word` is a form: the two would swap. `misheard` is the form of another word: one form
        # for two words.
        if word_key in owners or owners.get(form_key, word_key) != word_key:
            return Learned.NOTHING
        known = by_key.get(word_key)
        if known is not None and form_key in {replacement_key(form) for form in known.misheard}:
            return Learned.NOTHING  # a rule already, learned or typed

        spelled = word if known is None else known.word
        # The form is kept as it was first seen: rules ignore its ASCII letter case.
        form = misheard if own is None else own["form"]
        sightings = (0 if own is None else own["count"]) + 1
        rule = (
            sightings >= RULE_AFTER_SIGHTINGS
            and may_become_rule(spelled, form)
            and _keeps_replacements_working(entries, known, spelled, form)
        )
        with self._conn:
            self._put(spelled, [form] if rule else [], "learned")
            self._set_evidence(word_key, form_key, form, sightings, _RULE if rule else _PENDING)
        if rule:
            return Learned.RULE
        return Learned.TERM if known is None else Learned.PENDING

    def _evidence(self, word_key: str, form_key: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT form, count, status FROM dictionary_evidence"
            " WHERE word_key = ? AND form_key = ?",
            (word_key, form_key),
        ).fetchone()

    def _set_evidence(
        self, word_key: str, form_key: str, form: str, count: int, status: str
    ) -> None:
        """Record the evidence about a pair; part of a transaction."""
        self._conn.execute(
            "INSERT INTO dictionary_evidence (word_key, form_key, form, count, status,"
            " updated_at_ms) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(word_key, form_key) DO UPDATE SET form = excluded.form,"
            " count = excluded.count, status = excluded.status,"
            " updated_at_ms = excluded.updated_at_ms",
            (word_key, form_key, form, count, status, self._now_ms()),
        )

    def _form_owners(self, entries: Sequence[Entry]) -> dict[str, str]:
        """The key of the word that each form belongs to, a rule or a hint."""
        owners: dict[str, str] = {}
        for entry in entries:
            for form in entry.misheard:
                owners.setdefault(replacement_key(form), replacement_key(entry.word))
        rows = self._conn.execute(
            "SELECT word_key, form_key FROM dictionary_evidence WHERE status = ?", (_PENDING,)
        )
        for row in rows:
            owners.setdefault(row["form_key"], row["word_key"])
        return owners

    def _withdraw(self, entry: Entry, form_key: str) -> None:
        """Drop the form of `entry` that has this key from its rules, if it is one, and block the
        pair."""
        kept = [form for form in entry.misheard if replacement_key(form) != form_key]
        with self._conn:
            if kept != entry.misheard:
                self._conn.execute(
                    "UPDATE dictionary SET misheard = ? WHERE id = ?",
                    (json.dumps(kept, ensure_ascii=False), entry.id),
                )
            self._conn.execute(
                "UPDATE dictionary_evidence SET status = ?, updated_at_ms = ?"
                " WHERE word_key = ? AND form_key = ?",
                (_BLOCKED, self._now_ms(), replacement_key(entry.word), form_key),
            )

    # --- what the brain reads -------------------------------------------------------------------

    def hint_words(self) -> list[str]:
        """The words for the hints, in order of priority: the ones the user chose first, then the
        ones learned from corrections, each group newest first. A list of hints is cut short at the
        end, so what the user chose outlasts what was learned."""
        entries = sorted(self.list(), key=lambda entry: entry.source == "learned")
        return [entry.word for entry in entries]

    def hint_forms(self) -> list[tuple[str, str]]:
        """`(misheard, word)` for every form that the model should know a word is heard as: the
        replacement rules (`replacements()`) and then the hints, the pairs that are only evidence
        so far, the newest first. For the cleanup prompt, not for replacing."""
        entries = self.list()
        words = {replacement_key(entry.word): entry.word for entry in entries}
        known = {replacement_key(form) for entry in entries for form in entry.misheard}
        rows = self._conn.execute(
            "SELECT word_key, form_key, form FROM dictionary_evidence WHERE status = ?"
            " ORDER BY updated_at_ms DESC, rowid DESC",
            (_PENDING,),
        )
        hints = [
            (row["form"], words[row["word_key"]])
            for row in rows
            if row["word_key"] in words and row["form_key"] not in known
        ]
        return _rules(entries) + hints

    def replacements(self) -> list[tuple[str, str]]:
        """`(misheard, word)` for every misheard form, the longest form first.

        Applied in this order, a long form gets its chance before a shorter one inside it
        ("Lafia 先生" before "Lafia"). Forms of equal length keep the order of `list()`.
        """
        return _rules(self.list())

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
