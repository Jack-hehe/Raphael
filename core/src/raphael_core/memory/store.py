"""Raphael's long-term memory: Markdown files on this PC, one memory to a file, and their index.

The folder is `paths.memory_dir()` (`%LOCALAPPDATA%\\Raphael\\memory` for the release: machine-local,
never roaming). Each memory is `<name>.md`, where the name is an ASCII slug that starts with the
memory's type (`preference-answer-length`), with a frontmatter and a body:

    ---
    name: preference-answer-length
    description: "回答保持兩三句，除非主人要求詳細"
    type: preference
    created: 2026-10-22T21:40:00+08:00
    updated: 2026-10-22T21:40:00+08:00
    origin: said
    member: 0
    turns: [1234, 1240]
    ---
    主人偏好簡短的回答……

- `type` is one of `TYPES`: who the user is (`user`), how they want Raphael to answer or act
  (`preference`), people they mention (`person`), plans and open loops (`ongoing`, which must carry
  an `expires` date) and where things are (`reference`, never a credential).
- `origin` is `said` when the user said it, `inferred` when the reflection pass deduced it.
- `member` is the assistant whose conversation it came from (0 is Raphael): with `origin` and
  `updated` it is a shared note's provenance, who was told it and when. A note of an assistant's
  own memory needs none, and a write that names none keeps the one the note had.
- `turns` are the ids of the turns (`agent_turns`) it came from, for "why do you remember that?".
- `description` is one line, and it is the memory's line in the index.
- `created` and `updated` are stamped here, never by a model.

The index (`index`, also written to `MEMORY.md` for whoever opens the folder) is made from the
frontmatter by this code, never by a model, so it can never drift from the files: one line a
memory, `- [type] description (MM/DD) → name` (`(MM/DD, member N)` for a note with a `member`), by
type and then in the order the memories were made,
the `ongoing` ones whose date has passed left out. It is the same text until a memory changes, so
the prompt that carries it stays the same from one turn to the next (and the provider's prompt
cache keeps it). It is held to `INDEX_MAX_LINES` lines and `INDEX_MAX_CHARS` characters.

The limits are checked on every write and a write that breaks one is refused with a reason a model
can act on (`MemoryRefused`), as Hermes does, rather than cut short in silence. Every write is also
checked by `scan.refusal`: no secret and nothing that steers a model is ever written.

Nothing here logs a memory's name, description or body.
"""

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

from . import scan
from .terms import tokens

logger = logging.getLogger(__name__)

TYPES = ("user", "preference", "person", "ongoing", "reference")
ORIGINS = ("said", "inferred")
MAX_BODY_CHARS = 1200
MAX_DESCRIPTION_CHARS = 80
MAX_MEMORIES = 80
INDEX_MAX_LINES = 80
INDEX_MAX_CHARS = 6000
NAME_MAX_CHARS = 64
# The most turn ids a memory keeps: the latest ones.
MAX_TURNS = 20
INDEX_FILE = "MEMORY.md"
_NAME = re.compile(rf"(?:{'|'.join(TYPES)})-[a-z0-9]+(?:-[a-z0-9]+)*")
_SLUG_PART = re.compile(r"[^a-z0-9]+")


class MemoryRefused(Exception):
    """A memory that may not be written or read as asked. `kind` says why in one word
    ("bad_name", "bad_type", "bad_description", "too_long", "bad_expires", "secret", "injection",
    "full", "not_found"); `str()` says it in a sentence a model can act on. Neither ever holds the
    memory's text."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Memory:
    name: str
    description: str
    type: str
    body: str
    created: str  # ISO 8601 with the offset
    updated: str
    origin: str = "said"
    expires: str | None = None  # YYYY-MM-DD
    turns: tuple[int, ...] = field(default=())
    member: int | None = None

    def expired(self, today: date) -> bool:
        """Whether its date has passed (only a memory with an `expires` date ever has)."""
        if not self.expires:
            return False
        try:
            return date.fromisoformat(self.expires) < today
        except ValueError:
            return False

    def view(self) -> dict:
        """The memory as the settings page and the RPC methods show it."""
        return {
            "name": self.name,
            "description": self.description,
            "type": self.type,
            "body": self.body,
            "created": self.created,
            "updated": self.updated,
            "origin": self.origin,
            "expires": self.expires,
            "turns": list(self.turns),
            "member": self.member,
        }


def valid_name(name: object) -> bool:
    return isinstance(name, str) and len(name) <= NAME_MAX_CHARS and bool(_NAME.fullmatch(name))


def make_name(kind: str, hint: str | None, description: str = "") -> str:
    """A name for a new memory of type `kind`: `hint` made into an ASCII slug that starts with the
    type, or, when nothing of it is left (it was all Chinese, say), the type and a short hash of the
    description."""
    slug = _SLUG_PART.sub("-", (hint or "").lower()).strip("-")
    for prefix in (f"{kind}-", *(f"{other}-" for other in TYPES)):
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
            break
    if not slug:
        slug = hashlib.sha1(description.encode("utf-8")).hexdigest()[:8]
    name = f"{kind}-{slug}"[:NAME_MAX_CHARS].rstrip("-")
    return name if valid_name(name) else f"{kind}-{hashlib.sha1(name.encode()).hexdigest()[:8]}"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _iso(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def _index_line(memory: Memory) -> str:
    day = memory.updated[5:10].replace("-", "/") if len(memory.updated) >= 10 else ""
    told = "" if memory.member is None else f", member {memory.member}"
    return f"- [{memory.type}] {memory.description} ({day}{told}) → {memory.name}"


def index_of(memories: Iterable[Memory], today: date) -> list[str]:
    """The lines of the index of `memories`: by type, in the order they were made, the expired
    ones left out. Not cut to the limits."""
    order = {kind: n for n, kind in enumerate(TYPES)}
    kept = [m for m in memories if not m.expired(today)]
    kept.sort(key=lambda m: (order.get(m.type, len(TYPES)), m.created, m.name))
    return [_index_line(m) for m in kept]


def _fits(lines: Sequence[str]) -> bool:
    return len(lines) <= INDEX_MAX_LINES and len("\n".join(lines)) <= INDEX_MAX_CHARS


# --- the file format ---------------------------------------------------------------------------------


def render(memory: Memory) -> str:
    """A memory as the text of its file."""
    lines = [
        "---",
        f"name: {memory.name}",
        f"description: {json.dumps(memory.description, ensure_ascii=False)}",
        f"type: {memory.type}",
        f"created: {memory.created}",
        f"updated: {memory.updated}",
    ]
    if memory.expires:
        lines.append(f"expires: {memory.expires}")
    lines.append(f"origin: {memory.origin}")
    if memory.member is not None:
        lines.append(f"member: {memory.member}")
    lines.append(f"turns: {json.dumps(list(memory.turns))}")
    lines.append("---")
    return "\n".join(lines) + "\n" + memory.body.strip() + "\n"


def parse(text: str, name: str) -> Memory | None:
    """The memory in the text of the file `<name>.md`, or None when it is not one: no frontmatter,
    a name that is not the file's, a type that is not one of TYPES. A file edited by hand is read
    as far as it can be; what cannot be read of it is left at its default."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()

    def text_of(key: str) -> str:
        value = fields.get(key, "")
        if value.startswith('"'):
            try:
                loaded = json.loads(value)
            except ValueError:
                return value.strip('"')
            return loaded if isinstance(loaded, str) else ""
        return value

    if fields.get("name", name) != name or fields.get("type") not in TYPES:
        return None
    turns: tuple[int, ...] = ()
    try:
        loaded = json.loads(fields.get("turns", "[]"))
        if isinstance(loaded, list):
            turns = tuple(t for t in loaded if type(t) is int)
    except ValueError:
        pass
    origin = fields.get("origin", "said")
    member = fields.get("member", "")
    return Memory(
        name=name,
        description=_one_line(text_of("description")),
        type=fields["type"],
        body="\n".join(lines[end + 1 :]).strip(),
        created=fields.get("created", ""),
        updated=fields.get("updated", fields.get("created", "")),
        origin=origin if origin in ORIGINS else "said",
        expires=fields.get("expires") or None,
        turns=turns,
        member=int(member) if member.isascii() and member.isdigit() else None,
    )


# --- the store ---------------------------------------------------------------------------------------


class MemoryStore:
    """The memory folder `root`. Made on the first write; reading a folder that is not there finds
    no memories. `now` is the local time with its offset. Use it on one thread (the event loop)."""

    def __init__(self, root: Path, *, now: Callable[[], datetime] | None = None,
                 on_change: Callable[[], None] | None = None) -> None:
        self._on_change = on_change
        self.root = root
        self._now = now or (lambda: datetime.now().astimezone())
        self._cache_key: tuple | None = None
        self._cache: list[Memory] = []
        self._index_key: tuple | None = None
        self._index = ""
        # What changed, in the order it did (`mark`, `changed_since`): a count of the changes, the
        # count at which each name was last written or deleted, and the count of the last clear.
        self._changes = 0
        self._changed: dict[str, int] = {}
        self._cleared = 0

    # --- what changed ------------------------------------------------------------------------------

    def mark(self) -> int:
        """A mark of the memory as it is now, for `changed_since`. A pass of the model takes one
        before it reads the memory, and applies nothing to a memory that changed after it (the
        owner edited, deleted or cleared it in the settings meanwhile, or a turn wrote it)."""
        return self._changes

    def changed_since(self, name: str, mark: int) -> bool:
        """Whether the memory `name` was written or deleted after `mark`, or every memory was
        cleared after it. Only the changes made through this store are counted (a file edited by
        hand is not)."""
        return self._cleared > mark or self._changed.get(name, 0) > mark

    def _count_change(self, name: str | None) -> None:
        """Count a change of the memory `name`, or of every memory (None: a clear)."""
        self._changes += 1
        if name is None:
            self._cleared = self._changes
        else:
            self._changed[name] = self._changes

    # --- reading -----------------------------------------------------------------------------------

    def _files(self) -> list[Path]:
        try:
            entries = list(os.scandir(self.root))
        except OSError:  # no folder yet, or one that cannot be read
            return []
        return sorted(
            Path(entry.path)
            for entry in entries
            if entry.is_file()
            and entry.name.endswith(".md")
            and entry.name.lower() != INDEX_FILE.lower()
        )

    def _signature(self, files: Sequence[Path]) -> tuple:
        signature = []
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    def all(self) -> list[Memory]:
        """Every memory there is, the expired ones too, by type and in the order they were made.
        Read again from the folder only when a file changed (or was added or removed)."""
        files = self._files()
        key = self._signature(files)
        if key == self._cache_key:
            return list(self._cache)
        found: list[Memory] = []
        for path in files:
            name = path.stem
            if not valid_name(name):
                continue
            try:
                memory = parse(path.read_text(encoding="utf-8"), name)
            except (OSError, UnicodeDecodeError):
                memory = None
            if memory is not None:
                found.append(memory)
        order = {kind: n for n, kind in enumerate(TYPES)}
        found.sort(key=lambda m: (order[m.type], m.created, m.name))
        self._cache_key, self._cache = key, found
        return list(found)

    def get(self, name: str) -> Memory | None:
        if not valid_name(name):
            return None
        return next((m for m in self.all() if m.name == name), None)

    def today(self) -> date:
        return self._now().date()

    def index(self) -> str:
        """The index, as the prompt carries it: the same text for as long as no memory changes
        (and the day does not pass a memory's date). Cut to the limits when files edited by hand
        went past them."""
        memories = self.all()
        today = self.today()
        key = (self._cache_key, today)
        if key == self._index_key:
            return self._index
        lines = index_of(memories, today)
        while lines and not _fits(lines):
            lines.pop()
        self._index_key, self._index = key, "\n".join(lines)
        return self._index

    def fill(self) -> float:
        """How full the memory is, from 0 to 1: the larger of its share of the most memories and
        its index's share of the most lines and characters."""
        memories = self.all()
        lines = index_of(memories, self.today())
        return max(
            len(memories) / MAX_MEMORIES,
            len(lines) / INDEX_MAX_LINES,
            len("\n".join(lines)) / INDEX_MAX_CHARS,
        )

    def related(self, text: str, limit: int = 5, *, exclude: Iterable[str] = ()) -> list[Memory]:
        """The memories that share the most words (and pairs of CJK characters) with `text`, best
        first; none that shares nothing."""
        wanted = set(tokens(text))
        if not wanted:
            return []
        skip = set(exclude)
        scored = []
        for memory in self.all():
            if memory.name in skip:
                continue
            terms = set(tokens(f"{memory.name.replace('-', ' ')} {memory.description} {memory.body}"))
            score = len(wanted & terms)
            if score:
                scored.append((score, memory.updated, memory))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [memory for _, _, memory in scored[:limit]]

    # --- writing -----------------------------------------------------------------------------------

    def check(
        self,
        *,
        name: str,
        type: str,
        description: str,
        body: str,
        expires: str | None,
    ) -> tuple[str, str, str | None]:
        """The description, the body and the date of a memory as they would be written, or
        MemoryRefused. Nothing is written."""
        if not valid_name(name):
            raise MemoryRefused(
                "bad_name",
                "The name must be the type and an ASCII slug, such as preference-answer-length.",
            )
        if type not in TYPES:
            raise MemoryRefused("bad_type", f"The type must be one of {', '.join(TYPES)}.")
        description = _one_line(description)
        if not description:
            raise MemoryRefused("bad_description", "A memory needs a one-line description.")
        if len(description) > MAX_DESCRIPTION_CHARS:
            raise MemoryRefused(
                "bad_description",
                f"The description must be at most {MAX_DESCRIPTION_CHARS} characters.",
            )
        body = body.replace("\r\n", "\n").strip()
        if not body:
            body = description
        if len(body) > MAX_BODY_CHARS:
            raise MemoryRefused(
                "too_long", f"The body must be at most {MAX_BODY_CHARS} characters: shorten it."
            )
        expires = (expires or "").strip() or None
        if expires is not None:
            try:
                expires = date.fromisoformat(expires).isoformat()
            except ValueError:
                raise MemoryRefused("bad_expires", "expires must be a date, YYYY-MM-DD.") from None
        if type == "ongoing" and expires is None:
            raise MemoryRefused(
                "bad_expires", "An ongoing memory needs an expires date (YYYY-MM-DD)."
            )
        refused = scan.refusal(description, body)
        if refused is not None:
            kind = refused.split(":", 1)[0]
            raise MemoryRefused(
                kind,
                "A memory must never hold a password, a key, a token, a code or a card number."
                if kind == "secret"
                else "A memory must be a plain fact about the user, not instructions or links.",
            )
        return description, body, expires

    def write(
        self,
        name: str,
        *,
        type: str,
        description: str,
        body: str,
        expires: str | None = None,
        origin: str = "said",
        turns: Sequence[int] = (),
        leaving: Iterable[str] = (),
        member: int | None = None,
    ) -> Memory:
        """Create the memory `name`, or replace it. Its `created` stays what it was, `updated` is
        now, and the turns it came from are added to the ones it had; `member`, when given, is the
        assistant it was told to now, else it keeps the one it had. MemoryRefused when it breaks
        a limit or holds something it may not. `leaving` are memories about to be deleted (the
        ones a merge folds into this one): the index is checked without them."""
        description, body, expires = self.check(
            name=name, type=type, description=description, body=body, expires=expires
        )
        existing = {m.name: m for m in self.all()}
        old = existing.get(name)
        if old is None and len(existing) >= MAX_MEMORIES:
            raise MemoryRefused(
                "full",
                f"The memory is full ({MAX_MEMORIES} memories): update or delete one first.",
            )
        now = _iso(self._now())
        kept_turns = list(old.turns) if old is not None else []
        for turn in turns:
            if _is_turn_id(turn) and turn not in kept_turns:
                kept_turns.append(turn)
        memory = Memory(
            name=name,
            description=description,
            type=type,
            body=body,
            created=old.created if old is not None and old.created else now,
            updated=now,
            origin=origin if origin in ORIGINS else "said",
            expires=expires,
            turns=tuple(kept_turns[-MAX_TURNS:]),
            member=member if member is not None else (old.member if old is not None else None),
        )
        existing[name] = memory
        gone = set(leaving) - {name}
        kept = [m for m in existing.values() if m.name not in gone]
        if not _fits(index_of(kept, self.today())):
            raise MemoryRefused(
                "full",
                "The index of memories is full: merge or delete memories, or shorten descriptions.",
            )
        self._write_file(self.root / f"{name}.md", render(memory))
        self._count_change(name)
        self._after_change()
        return memory

    def add_turn(self, name: str, turn_id: int) -> None:
        """Add `turn_id` to the turns the memory `name` came from, leaving the rest of it (its
        `updated` too, so that its line in the index stays the same)."""
        memory = self.get(name)
        if memory is None or turn_id in memory.turns or not _is_turn_id(turn_id):
            return
        turns = (*memory.turns, turn_id)[-MAX_TURNS:]
        self._write_file(self.root / f"{name}.md", render(replace(memory, turns=turns)))
        self._cache_key = None
        if self._on_change is not None:
            self._on_change()

    def delete(self, name: str) -> bool:
        """Delete the memory `name`; whether there was one."""
        if not valid_name(name):
            return False
        try:
            (self.root / f"{name}.md").unlink()
        except FileNotFoundError:
            return False
        self._count_change(name)
        self._after_change()
        return True

    def clear(self) -> int:
        """Delete every memory; how many went."""
        self._count_change(None)
        deleted = 0
        for path in self._files():
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("Could not delete a memory: %s", type(exc).__name__)
        self._after_change()
        return deleted

    def drop_expired(self) -> list[str]:
        """Delete the memories whose date has passed; their names."""
        today = self.today()
        gone = [m.name for m in self.all() if m.expired(today)]
        for name in gone:
            self.delete(name)
        return gone

    def _write_file(self, path: Path, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_text(text, encoding="utf-8", newline="\n")
        os.replace(part, path)

    def _after_change(self) -> None:
        """Write the index to `MEMORY.md`, for whoever opens the folder. The prompt reads `index`."""
        self._cache_key = None
        if not self.root.exists():
            return
        try:
            index = self.index()
            header = "# Raphael's memory (made from the files; edits here are not kept)\n\n"
            self._write_file(self.root / INDEX_FILE, header + (index + "\n" if index else ""))
        except OSError as exc:
            logger.warning("Could not write the index of the memory: %s", type(exc).__name__)
        if self._on_change is not None:
            self._on_change()


def move_note(name: str, source: MemoryStore, target: MemoryStore) -> bool:
    """Move the memory `name` from `source` to `target` as its file is, byte for byte (an edit by
    hand is kept); whether it moved. Not when `source` has no such memory or `target` already has
    one of that name: nothing is ever overwritten. Both indexes are written again, and the move
    counts as a change of the memory in both (`changed_since`), so a pass of the model that read
    either before it applies nothing to it."""
    if not valid_name(name) or source.get(name) is None or target.get(name) is not None:
        return False
    target.root.mkdir(parents=True, exist_ok=True)
    os.replace(source.root / f"{name}.md", target.root / f"{name}.md")
    for store in (source, target):
        store._count_change(name)
        store._after_change()
    return True


@dataclass(frozen=True)
class MemoryIndex:
    """What an assistant reads of the memory: the index of the shared memory, which every assistant
    of the owner's reads and writes (who the owner is), and the index of its own (its own work).
    The shared one comes first: it changes less often than an assistant's own work, which keeps
    the start of the prompt the same for the provider's cache."""

    shared: str = ""
    own: str = ""

    def __bool__(self) -> bool:
        return bool(self.shared or self.own)

    def __str__(self) -> str:
        """Both indexes, each in its tags, for a prompt that shows them as data."""
        return (
            f"<shared_memory>\n{self.shared or '(no notes yet)'}\n</shared_memory>\n"
            f"<own_memory>\n{self.own or '(no notes yet)'}\n</own_memory>"
        )


def _is_turn_id(turn: object) -> bool:
    """Whether `turn` is a turn id: a whole number above zero (not a bool)."""
    return type(turn) is int and turn > 0

