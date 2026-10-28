"""The members of the owner's team, their groups and their routines, in the database.

- `member_groups`: a group of the member column, with a name (at most GROUP_NAME_MAX characters), a
  place (`position`) and the places its members look for progress (`sources`, "看的地方"): a GitHub
  repository (`owner/repo`) or a Notion page (its id or its address), at most MAX_SOURCES.
- `members`: a member, with a name (at most NAME_MAX characters, one no other member has and none
  of Raphael's), a role (ROLE_MAX), its character and speaking style (PERSONALITY_MAX), the owner's
  instructions for its work (INSTRUCTIONS_MAX), a picture
  (`avatar`: "", "preset:<file stem>" or "file:<stored file name>"), a group or none (未分組), a model
  ("" for the answer model), a place, when it was made, and the tools and connections it may use
  (`tools`: a list of ids, or None for every one that is on).
- `routines`: what a member does on a schedule (`schedule`, see `schedule.py`), with a title
  (TITLE_MAX) and the prompt it runs with (PROMPT_MAX), whether it is on, and when it last ran and
  runs next. Raphael has routines too, as member 0.
- `member_unread`: the threads (Raphael's as 0) with news the owner has not seen.

Raphael himself is not a row: he is member 0 everywhere, cannot be renamed or deleted, and has
every tool. The primary contact is the member whose `primary_contact` is 1 (at most one, by the table's
own index), or Raphael while none is: choosing another never touches the chief of staff, who is Raphael
whoever the primary is, and a member that is hidden or deleted stops being it. A name, a role, a personality,
an instruction, a title and a prompt are what the owner wrote, so
nothing here logs one; a refusal says which field was wrong, never what was in it.

Use it on the thread that owns `conn` (the event loop).
"""

import json
import re
import sqlite3
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any

from ..agent.speaker import Speaker, is_raphael_name
from ..clock import wall_clock_ms
from .schedule import Schedule, ScheduleError, parse_schedule

NAME_MAX = 16
ROLE_MAX = 24
PERSONALITY_MAX = 2000
INSTRUCTIONS_MAX = 2000
GROUP_NAME_MAX = 24
TITLE_MAX = 40
PROMPT_MAX = 1000
MODEL_MAX = 200
MAX_SOURCES = 10
SOURCE_MAX = 300
MAX_TOOLS = 200
TOOL_ID_MAX = 200
MAX_MEMBERS = 100
MAX_GROUPS = 40
MAX_ROUTINES = 20  # of one member
SOURCE_KINDS = ("github", "notion")

_GITHUB_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
_GITHUB_URL = re.compile(r"https://(?:www\.)?github\.com/([^/?#]+/[^/?#]+?)(?:\.git)?(?:[/?#].*)?")
_NOTION_ID = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_NOTION_URL = re.compile(r"https://(?:www\.)?(?:notion\.so|[a-z0-9-]+\.notion\.site)/\S*")
_AVATAR = re.compile(r"(?:preset|file):[A-Za-z0-9._ -]{1,120}")


class MemberError(ValueError):
    """A change that cannot be made. `kind` says why in one word ("name", "taken", "role",
    "instructions", "group", "model", "tools", "avatar", "title", "prompt", "schedule", "sources",
    "full", "not_found", "raphael", "primary", "hidden"), and `str()` names the field: never what the owner wrote."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Source:
    kind: str  # "github" or "notion"
    ref: str  # "owner/repo", or a Notion page's id or address

    def to_json(self) -> dict[str, str]:
        return {"kind": self.kind, "ref": self.ref}


@dataclass(frozen=True)
class Group:
    id: int
    name: str
    position: int
    sources: tuple[Source, ...] = ()


@dataclass(frozen=True)
class Member:
    id: int
    name: str
    role: str
    instructions: str
    avatar: str
    group_id: int | None
    model: str
    position: int
    created_at_ms: int
    tools: tuple[str, ...] | None = None
    personality: str = ""

    blurb: str = ""
    reply_style: str = "full"
    voice: str = ""
    read_aloud: bool = False
    notify: bool = False
    ask_before_members: bool = False
    pinned: bool = False
    hidden: bool = False
    # Writes mail as a draft for the owner to edit and send, unless they turned it off (`agent.drafts`).
    draft_first: bool = True

    def speaker(self) -> Speaker:
        """The member as the assistant takes it (`agent.speaker.Speaker`)."""
        return Speaker(
            id=self.id,
            name=self.name,
            role=self.role,
            instructions=self.instructions,
            personality=self.personality,
            model="",
            blurb=self.blurb,
            reply_style=self.reply_style,
            ask_before_members=self.ask_before_members,
            draft_first=self.draft_first,
            tools=None if self.tools is None else frozenset(self.tools),
        )


@dataclass(frozen=True)
class Routine:
    id: int
    member_id: int
    title: str
    prompt: str
    schedule: Schedule
    enabled: bool
    last_run_ms: int | None
    next_run_ms: int | None
    created_at_ms: int


# --- checking what the owner wrote -----------------------------------------------------------------


def _text(value: Any, field: str, limit: int, *, blank: bool = True, one_line: bool = True) -> str:
    """`value` as text the owner wrote: no control character (but a line break, in a text that may
    have several lines), the spaces around it gone, at most `limit` characters."""
    if not isinstance(value, str):
        raise MemberError(field, f"{field} must be text")
    if one_line:
        value = " ".join(value.split())
    else:
        value = value.replace("\r\n", "\n").strip()
    if any(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in value):
        raise MemberError(field, f"{field} has a character that cannot be shown")
    if not blank and not value:
        raise MemberError(field, f"{field} must not be empty")
    if len(value) > limit:
        raise MemberError(field, f"{field} must be at most {limit} characters")
    return value


def clean_name(value: Any) -> str:
    return _text(value, "name", NAME_MAX, blank=False)


def clean_group_name(value: Any) -> str:
    return _text(value, "name", GROUP_NAME_MAX, blank=False)


def clean_role(value: Any) -> str:
    return _text(value, "role", ROLE_MAX)


def clean_instructions(value: Any) -> str:
    return _text(value, "instructions", INSTRUCTIONS_MAX, one_line=False)


def clean_personality(value: Any) -> str:
    return _text(value, "personality", PERSONALITY_MAX, one_line=False)


def clean_title(value: Any) -> str:
    return _text(value, "title", TITLE_MAX, blank=False)


def clean_prompt(value: Any) -> str:
    return _text(value, "prompt", PROMPT_MAX, blank=False, one_line=False)


def clean_model(value: Any) -> str:
    return _text(value, "model", MODEL_MAX)


def clean_schedule(value: Any) -> Schedule:
    try:
        return parse_schedule(value)
    except ScheduleError as error:
        raise MemberError("schedule", str(error)) from None


def clean_avatar(value: Any) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or not _AVATAR.fullmatch(value) or ".." in value:
        raise MemberError("avatar", 'avatar must be "", "preset:<name>" or "file:<name>"')
    return value


def clean_tools(value: Any) -> tuple[str, ...] | None:
    """The tools a member may use: None for every one that is on, or a list of ids (Raphael's own,
    by BUILTIN_TOOL_IDS, connections by their ids, and plugin tools by their keys)."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > MAX_TOOLS:
        raise MemberError("tools", f"tools must be null or a list of at most {MAX_TOOLS} ids")
    ids: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > TOOL_ID_MAX:
            raise MemberError("tools", "each tool must be an id")
        if any(unicodedata.category(char) == "Cc" for char in item):
            raise MemberError("tools", "each tool must be an id")
        if item.strip() not in ids:
            ids.append(item.strip())
    return tuple(ids)


def clean_source(value: Any) -> Source:
    """A place to look for progress: a GitHub repository as `owner/repo` (its address is taken
    too), or a Notion page by its id or its address."""
    if not isinstance(value, dict) or value.get("kind") not in SOURCE_KINDS:
        raise MemberError("sources", "a source is {kind: github or notion, ref}")
    ref = value.get("ref")
    if not isinstance(ref, str) or not ref.strip() or len(ref.strip()) > SOURCE_MAX:
        raise MemberError("sources", "a source needs a ref")
    ref = ref.strip()
    if value["kind"] == "github":
        url = _GITHUB_URL.fullmatch(ref)
        if url is not None:
            ref = url.group(1)
        if not _GITHUB_REPO.fullmatch(ref):
            raise MemberError("sources", "a GitHub source is owner/repo")
        return Source("github", ref)
    if _NOTION_ID.fullmatch(ref):
        return Source("notion", ref.replace("-", "").lower())
    if _NOTION_URL.fullmatch(ref):
        return Source("notion", ref)
    raise MemberError("sources", "a Notion source is a page id or a notion.so address")


def clean_sources(value: Any) -> tuple[Source, ...]:
    if not isinstance(value, list) or len(value) > MAX_SOURCES:
        raise MemberError("sources", f"sources must be a list of at most {MAX_SOURCES}")
    sources: list[Source] = []
    for item in value:
        source = clean_source(item)
        if source not in sources:
            sources.append(source)
    return tuple(sources)


def _check_position(value: Any) -> None:
    if type(value) is not int or value < 0:
        raise MemberError("position", "position must be a whole number of 0 or more")


def _sources_of(saved: str | None) -> tuple[Source, ...]:
    try:
        items = json.loads(saved or "[]")
    except ValueError:
        return ()
    if not isinstance(items, list):
        return ()
    sources = []
    for item in items:
        try:
            sources.append(clean_source(item))
        except MemberError:
            continue
    return tuple(sources)


def _tools_of(saved: str | None) -> tuple[str, ...] | None:
    if saved is None:
        return None
    try:
        return clean_tools(json.loads(saved))
    except (ValueError, MemberError):
        return None


def _boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise MemberError("boolean", "preference must be true or false")
    return value


def _reply_style(value: Any) -> str:
    if value not in ("full", "conversational"):
        raise MemberError("reply_style", "reply_style must be full or conversational")
    return value


def _voice(value: Any) -> str:
    if value != "" and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", value)):
        raise MemberError("voice", "voice must be a configured Fish voice id or empty")
    return value


PROFILE_FIELDS: dict[str, Callable[[Any], Any]] = {
    "blurb": lambda value: _text(value, "blurb", 200),
    "reply_style": _reply_style,
    "voice": _voice,
    **{name: _boolean for name in ("read_aloud", "notify", "ask_before_members", "pinned", "hidden", "draft_first")},
}

_MEMBER_COLUMNS = (
    "id, name, role, instructions, avatar, group_id, model, position, created_at_ms, tools, personality,"
    " blurb, reply_style, voice, read_aloud, notify, ask_before_members, pinned, hidden, draft_first"
)
_ROUTINE_COLUMNS = (
    "id, member_id, title, prompt, schedule, enabled, last_run_ms, next_run_ms, created_at_ms"
)
# What `update_member` changes, and how each is checked.
_MEMBER_FIELDS: dict[str, Callable[[Any], Any]] = {
    "role": clean_role,
    "instructions": clean_instructions,
    "personality": clean_personality,
    "avatar": clean_avatar,
    "model": clean_model,
    **PROFILE_FIELDS,
}


def _member(row: sqlite3.Row) -> Member:
    data = dict(row)
    data["tools"] = _tools_of(data.get("tools"))
    for field in ("read_aloud", "notify", "ask_before_members", "pinned", "hidden", "draft_first"):
        data[field] = bool(data[field])
    return Member(**data)


def _routine(row: sqlite3.Row) -> Routine | None:
    data = dict(row)
    try:
        data["schedule"] = parse_schedule(json.loads(data["schedule"]))
    except (ValueError, ScheduleError):
        return None
    data["enabled"] = bool(data["enabled"])
    return Routine(**data)


class MemberStore:
    """The tables of the members (see the module). `now_ms` is the wall clock."""

    def __init__(self, conn: sqlite3.Connection, now_ms: Callable[[], int] = wall_clock_ms) -> None:
        self._conn = conn
        self._now_ms = now_ms
        self._transaction_depth = 0

    @contextmanager
    def transaction(self):
        """Compose existing writes without committing part of an approved batch."""
        outer = self._transaction_depth == 0
        self._transaction_depth += 1
        try:
            with self._conn if outer else nullcontext():
                yield
        finally:
            self._transaction_depth -= 1

    def _move(self, table: str, row_id: int, position: int) -> None:
        """Put the row `row_id` of `table` (groups or members) at `position` in the order of all of
        them (the last place, when that is past the end) and number the places again from 0."""
        ids = [
            int(row[0])
            for row in self._conn.execute(f"SELECT id FROM {table} ORDER BY position, id")
            if int(row[0]) != row_id
        ]
        ids.insert(min(position, len(ids)), row_id)
        with self.transaction():
            for place, each in enumerate(ids):
                self._conn.execute(f"UPDATE {table} SET position = ? WHERE id = ?", (place, each))

    # --- groups ------------------------------------------------------------------------------------

    def groups(self) -> list[Group]:
        rows = self._conn.execute(
            "SELECT id, name, position, sources FROM member_groups ORDER BY position, id"
        ).fetchall()
        return [Group(r["id"], r["name"], r["position"], _sources_of(r["sources"])) for r in rows]

    def group(self, group_id: int) -> Group | None:
        return next((group for group in self.groups() if group.id == group_id), None)

    def group_by_name(self, name: str) -> Group | None:
        folded = " ".join(name.split()).casefold()
        return next((g for g in self.groups() if g.name.casefold() == folded), None)

    def create_group(self, name: Any) -> Group:
        name = clean_group_name(name)
        if len(self.groups()) >= MAX_GROUPS:
            raise MemberError("full", f"there are {MAX_GROUPS} groups already")
        with self.transaction():
            (position,) = self._conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM member_groups"
            ).fetchone()
            cursor = self._conn.execute(
                "INSERT INTO member_groups (name, position, sources) VALUES (?, ?, '[]')",
                (name, position),
            )
        group = self.group(int(cursor.lastrowid or 0))
        assert group is not None
        return group

    def update_group(
        self,
        group_id: int,
        *,
        name: Any = None,
        position: Any = None,
        sources: Any = None,
    ) -> Group:
        group = self.group(group_id)
        if group is None:
            raise MemberError("not_found", "there is no such group")
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = clean_group_name(name)
        if position is not None:
            _check_position(position)
        if sources is not None:
            changes["sources"] = json.dumps(
                [source.to_json() for source in clean_sources(sources)], ensure_ascii=False
            )
        if changes:
            assignments = ", ".join(f"{column} = ?" for column in changes)
            with self.transaction():
                self._conn.execute(
                    f"UPDATE member_groups SET {assignments} WHERE id = ?",
                    [*changes.values(), group_id],
                )
        if position is not None:
            self._move("member_groups", group_id, position)
        updated = self.group(group_id)
        assert updated is not None
        return updated

    def delete_group(self, group_id: int) -> bool:
        """Delete a group; its members go to 未分組 (no group). Whether there was one."""
        with self.transaction():
            self._conn.execute("UPDATE members SET group_id = NULL WHERE group_id = ?", (group_id,))
            self._conn.execute("UPDATE rooms SET group_id = NULL WHERE group_id = ?", (group_id,))
            cursor = self._conn.execute("DELETE FROM member_groups WHERE id = ?", (group_id,))
        return cursor.rowcount > 0

    # --- members -----------------------------------------------------------------------------------

    def members(self) -> list[Member]:
        rows = self._conn.execute(
            f"SELECT {_MEMBER_COLUMNS} FROM members ORDER BY pinned DESC, position, id"
        ).fetchall()
        return [_member(row) for row in rows]

    def member(self, member_id: int) -> Member | None:
        row = self._conn.execute(
            f"SELECT {_MEMBER_COLUMNS} FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        return None if row is None else _member(row)

    def by_name(self, name: str) -> Member | None:
        """The member called `name`, whatever the case and the spaces around it."""
        row = self._conn.execute(
            f"SELECT {_MEMBER_COLUMNS} FROM members WHERE name = ? COLLATE NOCASE",
            (" ".join(name.split()),),
        ).fetchone()
        return None if row is None else _member(row)

    def _check_name(self, name: str, member_id: int | None) -> None:
        if is_raphael_name(name):
            raise MemberError("taken", "that name is Raphael's")
        other = self.by_name(name)
        if other is not None and other.id != member_id:
            raise MemberError("taken", "another member has that name")

    def _check_group(self, group_id: Any) -> int | None:
        if group_id is None:
            return None
        if type(group_id) is not int or self.group(group_id) is None:
            raise MemberError("group", "group_id must be the id of a group or null")
        return group_id

    def create_member(
        self,
        *,
        name: Any,
        role: Any = "",
        instructions: Any = "",
        personality: Any = "",
        group_id: Any = None,
        avatar: Any = "",
        tools: Any = None,
        model: Any = "",
        **preferences: Any,
    ) -> Member:
        name = clean_name(name)
        self._check_name(name, None)
        values = {
            "name": name,
            "role": clean_role(role),
            "instructions": clean_instructions(instructions),
            "personality": clean_personality(personality),
            "group_id": self._check_group(group_id),
            "avatar": clean_avatar(avatar),
            "model": clean_model(model),
        }
        for field, value in preferences.items():
            if field not in PROFILE_FIELDS:
                raise MemberError(field, "unknown member preference")
            values[field] = PROFILE_FIELDS[field](value)
        picked = clean_tools(tools)
        if len(self.members()) >= MAX_MEMBERS:
            raise MemberError("full", f"there are {MAX_MEMBERS} members already")
        with self.transaction():
            (position,) = self._conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM members"
            ).fetchone()
            values.update(position=position, created_at_ms=self._now_ms(),
                          tools=None if picked is None else json.dumps(list(picked), ensure_ascii=False))
            cursor = self._conn.execute(
                f"INSERT INTO members ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                list(values.values()),
            )
        member = self.member(int(cursor.lastrowid or 0))
        assert member is not None
        return member

    def update_member(self, member_id: int, **fields: Any) -> Member:
        """Change the fields given (`name`, `role`, `instructions`, `group_id`, `avatar`, `model`,
        `tools`, `position`); a field left out stays as it is. `group_id` None is 未分組, and
        `tools` None every tool that is on."""
        member = self.member(member_id)
        if member is None:
            raise MemberError("not_found", "there is no such member")
        changes: dict[str, Any] = {}
        for field, value in fields.items():
            if field == "name":
                changes["name"] = clean_name(value)
                self._check_name(changes["name"], member_id)
            elif field == "group_id":
                changes["group_id"] = self._check_group(value)
            elif field == "tools":
                picked = clean_tools(value)
                changes["tools"] = (
                    None if picked is None else json.dumps(list(picked), ensure_ascii=False)
                )
            elif field == "position":
                _check_position(value)
            elif field in _MEMBER_FIELDS:
                changes[field] = _MEMBER_FIELDS[field](value)
            else:
                raise MemberError(field, f"{field} cannot be changed")
        if changes.get("hidden"):
            # The owner sees who the primary contact is in the sidebar: a member that leaves it hands the role back to Raphael.
            changes["primary_contact"] = 0
        if changes:
            assignments = ", ".join(f"{column} = ?" for column in changes)
            with self.transaction():
                self._conn.execute(
                    f"UPDATE members SET {assignments} WHERE id = ?", [*changes.values(), member_id]
                )
        if "position" in fields:
            self._move("members", member_id, fields["position"])
        updated = self.member(member_id)
        assert updated is not None
        return updated

    def primary_id(self) -> int:
        """The member the owner chose as their primary contact: Raphael (0) until they choose another,
        and again once that one is hidden or deleted. The one definition of it, for the roster, the
        window and every prompt."""
        row = self._conn.execute("SELECT id FROM members WHERE primary_contact = 1").fetchone()
        return 0 if row is None else int(row[0])

    def set_primary(self, member_id: Any) -> bool:
        """Make `member_id` the primary contact (0 is Raphael), and nobody else: whether that changed
        anything. Only the flag moves. The chief of staff, the rooms, the tasks, the exchanges and
        the conversations are not touched, so nothing under way is run again, handed on or ended.
        MemberError when it is not a whole number, nobody, or a member hidden from the sidebar."""
        if type(member_id) is not int or member_id < 0:
            raise MemberError("primary", "primary must be the id of a member")
        if member_id:
            member = self.member(member_id)
            if member is None:
                raise MemberError("not_found", "there is no such member")
            if member.hidden:
                raise MemberError("hidden", "a hidden member cannot be the primary contact")
        if member_id == self.primary_id():
            return False
        with self.transaction():
            self._conn.execute("UPDATE members SET primary_contact = 0 WHERE primary_contact = 1")
            if member_id:
                self._conn.execute("UPDATE members SET primary_contact = 1 WHERE id = ?", (member_id,))
        return True

    def delete_member(self, member_id: int) -> bool:
        """Delete a member's row, its routines and its unread mark; whether there was one. Its
        conversation, memory and picture are the service's to delete."""
        with self.transaction():
            self._conn.execute("DELETE FROM routine_runs WHERE routine_id IN (SELECT id FROM routines WHERE member_id = ?)", (member_id,))
            self._conn.execute("DELETE FROM room_members WHERE member_id = ?", (member_id,))
            self._conn.execute("DELETE FROM member_skills WHERE member_id = ?", (member_id,))
            self._conn.execute("DELETE FROM member_commands WHERE member_id = ?", (member_id,))
            self._conn.execute("DELETE FROM routines WHERE member_id = ?", (member_id,))
            self._conn.execute("DELETE FROM member_unread WHERE member_id = ?", (member_id,))
            cursor = self._conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
        return cursor.rowcount > 0

    def exists(self, member_id: int) -> bool:
        """Whether `member_id` is someone: Raphael (0) or a member."""
        return member_id == 0 or self.member(member_id) is not None

    def speaker(self, member_id: int) -> Speaker | None:
        """The member `member_id` as the assistant takes it; Raphael for 0; None for nobody."""
        if member_id == 0:
            return Speaker()
        member = self.member(member_id)
        return None if member is None else member.speaker()

    # --- routines ----------------------------------------------------------------------------------

    def routines(self, member_id: int | None = None) -> list[Routine]:
        """The routines of `member_id`, or of everyone, in the order they were made."""
        if member_id is None:
            rows = self._conn.execute(f"SELECT {_ROUTINE_COLUMNS} FROM routines ORDER BY id")
        else:
            rows = self._conn.execute(
                f"SELECT {_ROUTINE_COLUMNS} FROM routines WHERE member_id = ? ORDER BY id",
                (member_id,),
            )
        return [routine for routine in map(_routine, rows.fetchall()) if routine is not None]

    def routine(self, routine_id: int) -> Routine | None:
        row = self._conn.execute(
            f"SELECT {_ROUTINE_COLUMNS} FROM routines WHERE id = ?", (routine_id,)
        ).fetchone()
        return None if row is None else _routine(row)

    def create_routine(
        self,
        *,
        member_id: int,
        title: Any,
        prompt: Any,
        schedule: Any,
        enabled: Any = True,
        next_run_ms: int | None,
    ) -> Routine:
        if not self.exists(member_id):
            raise MemberError("not_found", "there is no such member")
        if type(enabled) is not bool:
            raise MemberError("enabled", "enabled must be true or false")
        values = (clean_title(title), clean_prompt(prompt), clean_schedule(schedule))
        if len(self.routines(member_id)) >= MAX_ROUTINES:
            raise MemberError("full", f"a member has at most {MAX_ROUTINES} routines")
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO routines (member_id, title, prompt, schedule, enabled, next_run_ms,"
                " created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    member_id,
                    values[0],
                    values[1],
                    json.dumps(values[2].to_json()),
                    int(enabled),
                    next_run_ms if enabled else None,
                    self._now_ms(),
                ),
            )
        routine = self.routine(int(cursor.lastrowid or 0))
        assert routine is not None
        return routine

    def update_routine(
        self,
        routine_id: int,
        *,
        title: Any = None,
        prompt: Any = None,
        schedule: Any = None,
        enabled: Any = None,
        next_run: Callable[[Schedule], int | None],
    ) -> Routine:
        """Change the fields given. A routine whose schedule changed, or that was switched on, is
        next due when `next_run` says for its schedule as it is now; one switched off is due never."""
        routine = self.routine(routine_id)
        if routine is None:
            raise MemberError("not_found", "there is no such routine")
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = clean_title(title)
        if prompt is not None:
            changes["prompt"] = clean_prompt(prompt)
        plan = routine.schedule
        if schedule is not None:
            plan = clean_schedule(schedule)
            changes["schedule"] = json.dumps(plan.to_json())
        on = routine.enabled
        if enabled is not None:
            if type(enabled) is not bool:
                raise MemberError("enabled", "enabled must be true or false")
            on = enabled
            changes["enabled"] = int(on)
        if not on:
            changes["next_run_ms"] = None
        elif schedule is not None or not routine.enabled or routine.next_run_ms is None:
            changes["next_run_ms"] = next_run(plan)
        if changes:
            assignments = ", ".join(f"{column} = ?" for column in changes)
            with self._conn:
                self._conn.execute(
                    f"UPDATE routines SET {assignments} WHERE id = ?", [*changes.values(), routine_id]
                )
        updated = self.routine(routine_id)
        assert updated is not None
        return updated

    def delete_routine(self, routine_id: int) -> bool:
        with self._conn:
            self._conn.execute("DELETE FROM routine_runs WHERE routine_id = ?", (routine_id,))
            cursor = self._conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
        return cursor.rowcount > 0

    def due(self, now_ms: int) -> list[Routine]:
        """The routines that are on and due at `now_ms` (or were, and have not been dealt with)."""
        rows = self._conn.execute(
            f"SELECT {_ROUTINE_COLUMNS} FROM routines WHERE enabled = 1"
            " AND next_run_ms IS NOT NULL AND next_run_ms <= ? ORDER BY next_run_ms, id",
            (now_ms,),
        ).fetchall()
        return [routine for routine in map(_routine, rows) if routine is not None]

    def advance(self, routine_id: int, *, next_run_ms: int | None, ran_at_ms: int | None) -> bool:
        """Set when a routine is next due, and when it ran if it does now (`ran_at_ms`), before it
        is carried out: a routine is run at most once for each time it is due, whatever happens to
        the run. Whether the routine is still there."""
        with self._conn:
            if ran_at_ms is None:
                cursor = self._conn.execute(
                    "UPDATE routines SET next_run_ms = ? WHERE id = ?", (next_run_ms, routine_id)
                )
            else:
                cursor = self._conn.execute(
                    "UPDATE routines SET next_run_ms = ?, last_run_ms = ? WHERE id = ?",
                    (next_run_ms, ran_at_ms, routine_id),
                )
        return cursor.rowcount > 0

    # --- what the owner has not seen ---------------------------------------------------------------

    def unread(self) -> set[int]:
        return {int(row[0]) for row in self._conn.execute("SELECT member_id FROM member_unread")}

    def mark_unread(self, member_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO member_unread (member_id) VALUES (?)", (member_id,)
            )

    def mark_read(self, member_id: int) -> bool:
        """Whether it had been unread."""
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM member_unread WHERE member_id = ?", (member_id,)
            )
        return cursor.rowcount > 0
