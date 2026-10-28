"""The members of the owner's team as RPC methods, with their pictures, their routines' scheduler and
the team as the assistant sees it.

The RPC methods (the main window only, through the shell's allow-list):

- `members.list {ui_language?}` -> `{members, groups}`. `members` is Raphael first (`id` 0, his name
  in the language, his picture when the owner chose one: `avatar_url`, a data URL, and
  `avatar_crop`), then every member in its place: `{id, name, role, personality, instructions, avatar,
  avatar_url?, group_id, model, tools, position, created, unread, waiting, preview, last_at}`.
  `preview` is the last line of its thread (one line, cut) and `last_at` when it was said; `unread`
  says the thread has news the owner has not seen (`members.read` clears it), and `waiting` that a
  question waits for the owner there. `groups` is `[{id, name, position, sources}]`.
- `members.create {name, role?, personality?, instructions?, group_id?, avatar?, tools?, model?, ui_language?}` ->
  `{member, routine_offer}`. `routine_offer` is the routine offered to a member of a group that
  has places to look (`sources`): `{title, prompt, schedule}` for 「每天早上的進度」 at 09:00, or
  null. It is offered, not made: `routines.create` makes it.
- `members.update {id, name?, role?, personality?, instructions?, group_id?, avatar?, tools?, model?, position?}`
  -> `{member}`. `avatar` here is "" or "preset:<stem>"; a picture of the owner's own comes by
  `members.set_avatar`. Raphael (0) is not changed here: his picture is the app's to change.
- `members.delete {id}` -> `{deleted}`: the member, its conversation (and the exchanges of other
  threads with it), its memory folder, its routines, its tasks and its picture. The app asks first.
- `members.set_primary {id}` -> `{primary_id}`: the owner's primary contact, the Bot that new work is brought
  to and that hands it to the colleague it belongs to. Raphael (0) until the owner chooses another; exactly one
  at a time. The roster says who it is to every model and to the window (`members.list`'s `team_context`,
  `primary_id`), which is the one place it is read from. It is Raphael again when the member is hidden or
  deleted. Raphael stays the chief of staff, in every room, whoever it is, and only the choice changes: no
  room, task or conversation is touched.
- `members.set_avatar {id, name, mime, data}` -> `{member}`: a picture of the owner's own, in
  base64, checked as the images of a request are (`services.attachments`), copied into
  `<local>\\avatars\\members\\` under a name of its own: the path it came from is never known here.
- `members.presets {}` -> `{presets: [{avatar, name, url}]}`: the pictures in
  `<local>\\avatars\\presets\\` (`.webp` and `.png`), if that folder is there. They are the owner's
  own files and never part of the app.
- `members.read {id}` -> `{}`; `members.viewing {id | null}` -> `{}`: which thread is on screen now
  (null for none), so that what arrives there is not news.
- `members.tool_choices {}` -> `{tools, connections}`: Raphael's own tools a member may be given, by
  id, and the connections, `{id, name, enabled}`.
- `groups.create {name}` -> `{group}`, `groups.update {id, name?, position?, sources?}` -> `{group}`,
  `groups.delete {id}` -> `{deleted}` (its members go to 未分組).
- `routines.list {member_id}` -> `{routines}`, `routines.create {member_id, title, prompt, schedule,
  enabled?}` -> `{routine}`, `routines.update {id, title?, prompt?, schedule?, enabled?}` ->
  `{routine}`, `routines.delete {id}` -> `{deleted}`, `routines.template {member_id, ui_language?}`
  -> `{offer}`. A routine is `{id, member_id, title, prompt, schedule, enabled, last_run, next_run}`,
  the times in ms (or null).

A refusal of what the owner wrote is a bad request whose `data.kind` names the field
(`store.MemberError`), never what was in it.

The routines. A scheduler ticks every TICK_S while the core runs (`start`). A routine that is due is
moved to its next time first and only then run, so it runs at most once each time it is due, however
the run goes. One that was due while the core was not running runs once if it was due within the
last CATCH_UP_MS (12 hours), and not at all if longer ago. A run is a background task of the
routine's member (`BackgroundService.submit`, `routine_id`), with the routine's prompt: it is written
as a turn of that thread (`route` and `source` "routine", the routine's title as its `question`) and
completed with the report. It never uses the desktop (the worker has no tool for it) and never asks
the owner at the overlay; every run counts toward the month's limit, as every task does.

Unread. A turn written or completed in a thread that is not on screen (`members.viewing`) marks it
unread, unless it is Raphael's reply heard on the voice overlay.

Waiting. A thread is waiting while a question for a yes waits in it: a turn with the status
"confirm" (a question of the fast tier), or a question that a background task of that member waits
on (`add_waiting_source`, which the app gives `BackgroundService.waiting_members`), answered in the
app alone.

Nothing here logs a name, a role, an instruction, a routine's title or prompt, or a result.
"""

import asyncio
import base64
import binascii
import logging
import re
import json
import secrets
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from typing import Any

from ..agent.history import AgentHistory, Turn
from ..agent.persona import normalize_language, phrase
from ..agent.speaker import BUILTIN_TOOL_IDS, RAPHAEL_NAMES, Speaker, is_raphael_name
from ..background.tasks import Refused
from ..brain.settings import DEFAULTS, SettingsStore
from ..clock import wall_clock_ms
from ..protocol import INVALID_PARAMS, RpcError, invalid_params
from ..router import Router
from ..services.attachments import IMAGE_TYPES, MAX_IMAGE_BYTES, AttachmentError, checked, sniff_ok
from .capabilities import MemberCapabilities, download_skills
from .schedule import Schedule, next_run
from .store import (
    PROMPT_MAX,
    PROFILE_FIELDS,
    NAME_MAX,
    Group,
    Member,
    MemberError,
    MemberStore,
    Routine,
    Source,
    clean_schedule,
)

logger = logging.getLogger(__name__)

TICK_S = 30.0
# A routine due while the core was not running runs once if it was due within this long.
CATCH_UP_MS = 12 * 3_600_000
# What the member column shows of the last line of a thread, at most.
PREVIEW_CHARS = 80
# The folders of the pictures, in the app's local folder.
AVATARS_DIR = "avatars"
PRESETS_DIR = "presets"
MEMBERS_DIR = "members"
# Raphael's own picture, kept by the shell (app/src-tauri/src/avatar.rs): read here, never written.
RAPHAEL_AVATAR_DIR = "avatar"
PRESET_SUFFIXES = (".webp", ".png")
MAX_PRESETS = 60
_PRESET_STEM = re.compile(r"[A-Za-z0-9._ -]{1,120}")
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
# The template of the routine offered to a member of a group with places to look.
TEMPLATE_TIME = "09:00"
_TEMPLATE_TITLE = {"zh-TW": "每天早上的進度", "en": "Morning progress", "ja": "毎朝の進捗"}
_TEMPLATE_PROMPT = {
    "zh-TW": "用已連接的 GitHub 與 Notion 工具，查看下列地方從昨天到現在的進度，整理成一份簡短的進度報告：",
    "en": (
        "Using the connected GitHub and Notion tools, check the progress since yesterday at the "
        "places below and write a short progress report:"
    ),
    "ja": "接続済みの GitHub と Notion のツールで、以下の場所の昨日からの進捗を確認し、短い進捗報告にまとめてください：",
}
_SOURCE_LABEL = {"github": "GitHub", "notion": "Notion"}
_COLON = {"zh-TW": "：", "en": ": ", "ja": "："}
_ASCII_WORD = re.compile(r"[A-Za-z0-9 ._-]+")


def _rpc_error(error: MemberError) -> RpcError:
    """A refusal of the store as a bad request whose data names the field."""
    return RpcError(INVALID_PARAMS, f"Invalid params: {error}", {"kind": error.kind})


def _whole(params: dict, name: str, *, least: int = 0) -> int:
    value = params.get(name)
    if type(value) is not int or value < least:
        raise invalid_params(f"{name} must be a whole number of {least} or more")
    return value


def _line(text: str) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= PREVIEW_CHARS else one[: PREVIEW_CHARS - 1] + "…"


def _preview(turn: Turn) -> str:
    """The last line of a thread as the member column shows it: what was answered, or what was
    asked while there is no answer yet (a routine's title, while it runs)."""
    return _line(turn.text or turn.question)


def routine_view(routine: Routine) -> dict[str, Any]:
    return {
        "id": routine.id,
        "member_id": routine.member_id,
        "title": routine.title,
        "prompt": routine.prompt,
        "schedule": routine.schedule.to_json(),
        "enabled": routine.enabled,
        "last_run": routine.last_run_ms,
        "next_run": routine.next_run_ms,
    }


def group_view(group: Group) -> dict[str, Any]:
    return {
        "id": group.id,
        "name": group.name,
        "position": group.position,
        "sources": [source.to_json() for source in group.sources],
    }


def routine_template(sources: Sequence[Source], language: str) -> dict[str, Any] | None:
    """The routine offered to a member of a group with places to look: every morning at 09:00, a
    progress report read from those places through the connected GitHub and Notion tools. None
    when the group has no place."""
    if not sources:
        return None
    prompt = _TEMPLATE_PROMPT[language]
    for source in sources:
        line = f"\n- {_SOURCE_LABEL[source.kind]}{_COLON[language]}{source.ref}"
        if len(prompt) + len(line) > PROMPT_MAX:
            break
        prompt += line
    return {
        "title": _TEMPLATE_TITLE[language],
        "prompt": prompt,
        "schedule": {"kind": "daily", "time": TEMPLATE_TIME},
    }


@dataclass
class _Picture:
    key: tuple[int, int]  # the file's mtime and size when it was read
    url: str


class MembersService:
    """The members (see the module). `conn` is the open database (used on the event loop only),
    `local_dir` the app's local folder (the pictures: `avatars\\`, and Raphael's own in `avatar\\`),
    `background` the background worker the routines run on, `memory` the memory (a member's
    folder and summary go with it). `language` is the language of the latest request (the
    assistant's), `notify` sends a notification to the app, `connections` lists the connections
    (`IntegrationsService.connections`). `now_ms` is the wall clock and `tz` the time zone of the
    routines' times (this PC's, by default)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        local_dir: Path,
        *,
        background: Any,
        memory: Any = None,
        language: Callable[[], str] = lambda: "zh-TW",
        notify: Callable[[str, dict], None] | None = None,
        connections: Callable[[], list[dict[str, Any]]] = lambda: [],
        now_ms: Callable[[], int] = wall_clock_ms,
        tz: tzinfo | None = None,
        tick_s: float = TICK_S,
    ) -> None:
        self.store = MemberStore(conn, now_ms)
        self.capabilities = MemberCapabilities(conn, local_dir / "member-skills", self.store)
        self._speech = None
        self._delivered: set[tuple[int, int, str]] = set()
        self._speech_tasks: set[asyncio.Task] = set()
        self._conn = conn
        self._history = AgentHistory(conn, now_ms)
        self._settings = SettingsStore(conn)
        self._background = background
        self._memory = memory
        self._language = language
        self._notify = notify
        self._connections = connections
        self._now_ms = now_ms
        self._tz = tz
        self._tick_s = tick_s
        self._avatars = local_dir / AVATARS_DIR
        self._raphael_avatar = local_dir / RAPHAEL_AVATAR_DIR
        self._pictures: dict[Path, _Picture] = {}
        self._viewing: int | None = None
        self._waiting_sources: list[Callable[[], Iterable[int]]] = []
        self._ticking: asyncio.Task[None] | None = None

    def register(self, router: Router) -> None:
        methods = {
            "members.skills.list": self._skills_list,
            "members.skills.read": self._skills_read,
            "members.skills.import": self._skills_import,
            "members.skills.update": self._skills_update,
            "members.skills.delete": self._skills_delete,
            "members.commands.list": self._commands_list,
            "members.commands.save": self._commands_save,
            "members.commands.delete": self._commands_delete,
            "members.duplicate": self._duplicate,
            "members.mark_all_read": self._mark_all_read,
            "members.voices": self._voices,
            "members.list": self._list,
            "members.create": self._create,
            "members.update": self._update,
            "members.delete": self._delete,
            "members.set_primary": self._set_primary,
            "members.set_avatar": self._set_avatar,
            "members.presets": self._presets,
            "members.read": self._read,
            "members.unread": self._unread,
            "members.viewing": self._viewing_now,
            "members.tool_choices": self._tool_choices,
            "groups.create": self._group_create,
            "groups.update": self._group_update,
            "groups.delete": self._group_delete,
            "routines.list": self._routines_list,
            "routines.create": self._routine_create,
            "routines.update": self._routine_update,
            "routines.delete": self._routine_delete,
            "routines.template": self._routine_template,
            "routines.runs": self._routine_runs,
        }
        for method, handler in methods.items():
            router.register(method, handler)

    # --- the team, as the assistant sees it (agent.team.Team) ----------------------------------------

    def speaker(self, member_id: int) -> Speaker | None:
        if member_id == 0:
            return Speaker(**self.first_bot_profile())
        return self.store.speaker(member_id)

    def first_bot_profile(self) -> dict:
        from ..brain.settings import SettingsStore

        return SettingsStore(self._conn).get("first_bot_profile")

    def find(self, name: str) -> Speaker | None:
        if not isinstance(name, str) or not name.strip():
            return None
        if is_raphael_name(name) or name.casefold() == self.first_bot_profile().get("name", "").casefold():
            return self.speaker(0)
        member = self.store.by_name(name)
        return None if member is None else member.speaker()

    def named_in(self, text: str) -> list[Speaker]:
        """The members whose names `text` holds: a name in Latin letters as a whole word, any
        other name of two characters or more as it is written."""
        folded = (text or "").casefold()
        if not folded:
            return []
        found = []
        for member in self.store.members():
            name = member.name.casefold()
            if _ASCII_WORD.fullmatch(name):
                hit = re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", folded)
            else:
                hit = len(name) >= 2 and name in folded
            if hit:
                found.append(member.speaker())
        return found

    def has_members(self) -> bool:
        return self._conn.execute("SELECT 1 FROM members LIMIT 1").fetchone() is not None

    def roster(self) -> list[Speaker]:
        return [self.speaker(0)] + [
            Speaker(id=member.id, name=member.name, role=member.role, blurb=member.blurb)
            for member in self.store.members()
        ]

    def primary_id(self) -> int:
        """The owner's primary contact (0 for Raphael): the one place the roster, the window and the prompts read it from."""
        return self.store.primary_id()

    def team_context(self, self_id=0, language="zh-TW", room_member_ids=None):
        from ..agent.team_context import snapshot
        return snapshot(self, self_id, language, room_member_ids).wire()

    def create(self, *, name: Any, role: Any, instructions: Any, group: Any, personality: Any = "") -> Speaker:
        """Add a member at the owner's request (`create_member`): in the group of that name, made
        when there is none, or in none. MemberError when it cannot be added."""
        group_id = None
        if isinstance(group, str) and group.strip():
            found = self.store.group_by_name(group)
            if found is None:
                found = self.store.create_group(group)
                self._changed()
            group_id = found.id
        member = self.store.create_member(
            name=name, role=role or "", instructions=instructions or "", group_id=group_id,
            personality=personality,
        )
        self._changed()
        return member.speaker()

    def _changed(self, method: str = "members.changed", member_id: int | None = None) -> None:
        """Announce committed data changes using identifiers only."""
        if self._notify is not None:
            self._notify(method, {} if member_id is None else {"member_id": member_id})

    def member_exists(self, member_id: int) -> bool:
        return self.store.exists(member_id)

    def name_of(self, member_id: int) -> str | None:
        """What the member `member_id` is called (for the overlay's notice of a question that waits
        in its thread); None for one that is not there."""
        try:
            speaker = self.speaker(member_id)
        except sqlite3.Error as exc:
            logger.warning("Could not read a member: %s", type(exc).__name__)
            return None
        return None if speaker is None else speaker.name

    # --- unread and waiting --------------------------------------------------------------------------

    def turn_arrived(self, turn_id: int) -> None:
        """A turn was written or completed: its thread is unread, unless it is on screen or the
        owner heard Raphael's turn said (`source` "voice", member 0)."""
        try:
            self._finish_run(turn_id)
            turn = self._history.get(turn_id)
            if turn is not None and turn.scope != "private":
                return
            if turn is not None and turn.member_id:
                self._deliver_turn(turn)
            if turn is None or (turn.source == "voice" and turn.member_id == 0) or turn.member_id == self._viewing:
                return
            was_unread = turn.member_id in self.store.unread()
            self.store.mark_unread(turn.member_id)
            if not was_unread:
                self._changed()
        except sqlite3.Error as exc:
            logger.warning("Could not mark a thread unread: %s", type(exc).__name__)

    def _deliver_turn(self, turn: Turn) -> None:
        kind = "needs_input" if turn.status == "confirm" else "finished" if turn.status == "done" else None
        if kind is None or turn.id is None or (turn.id, turn.created_at_ms, kind) in self._delivered:
            return
        self._delivered.add((turn.id, turn.created_at_ms, kind))
        # Bound the delivery cache without retaining any message contents.
        if len(self._delivered) > 2000:
            self._delivered = {(turn.id, turn.created_at_ms, kind)}
        member = self.store.member(turn.member_id)
        if member is None:
            return
        self._notice(member, kind)
        if kind == "finished" and member.read_aloud and turn.text and self._speech is not None:
            task = asyncio.get_running_loop().create_task(self._speech.say_member("\n".join(turn.messages) if turn.messages else turn.text, voice=member.voice, turn_id=turn.id))
            self._speech_tasks.add(task)
            task.add_done_callback(self._speech_tasks.discard)
            task.add_done_callback(lambda done: done.cancelled() or done.exception())

    def _notice(self, member: Member, kind: str) -> None:
        if member.notify and self._notify is not None:
            self._notify("member.notice", {"member_id": member.id, "name": member.name,
                                          "kind": kind, "notify": True})

    def needs_input(self, member_id: int) -> None:
        member = self.store.member(member_id)
        if member is not None:
            self._notice(member, "needs_input")

    def add_waiting_source(self, source: Callable[[], Iterable[int]]) -> None:
        """Add a source of threads with a question waiting for the owner: the members it names
        are `waiting` too. The safety rules' questions from the background come in here."""
        self._waiting_sources.append(source)

    def waiting_members(self) -> set[int]:
        """The members (Raphael as 0) in whose thread a question waits for the owner."""
        waiting = {
            int(row[0])
            for row in self._conn.execute(
                "SELECT DISTINCT member_id FROM agent_turns WHERE scope = 'private' AND status = 'confirm'"
            )
        }
        for source in self._waiting_sources:
            try:
                waiting |= {member for member in source() if type(member) is int}
            except Exception as exc:  # a source that breaks must not break the list
                logger.warning("A source of waiting questions failed: %s", type(exc).__name__)
        return waiting

    # --- the pictures --------------------------------------------------------------------------------

    def _data_url(self, path: Path) -> str | None:
        """The picture at `path` as a data URL, if it is one of the four kinds and not too large.
        Read again only when the file changed."""
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (stat.st_mtime_ns, stat.st_size)
        known = self._pictures.get(path)
        if known is not None and known.key == key:
            return known.url
        if stat.st_size > MAX_IMAGE_BYTES:
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        mime = next((kind for kind in IMAGE_TYPES if sniff_ok(kind, data)), None)
        if mime is None:
            return None
        url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        self._pictures[path] = _Picture(key, url)
        return url

    def _preset_files(self) -> list[Path]:
        folder = self._avatars / PRESETS_DIR
        try:
            files = sorted(
                path
                for path in folder.iterdir()
                if path.is_file()
                and path.suffix.lower() in PRESET_SUFFIXES
                and _PRESET_STEM.fullmatch(path.stem)
            )
        except OSError:  # no folder: no presets
            return []
        return files[:MAX_PRESETS]

    def _avatar_url(self, avatar: str) -> str | None:
        """The picture a member's `avatar` names, as a data URL; None for none, or one that is
        gone (the app shows the default silhouette)."""
        kind, _, name = avatar.partition(":")
        if kind == "file" and name:
            return self._data_url(self._avatars / MEMBERS_DIR / name)
        if kind == "preset" and name:
            for path in self._preset_files():
                if path.stem == name:
                    return self._data_url(path)
        return None

    def _raphael_picture(self) -> tuple[str, dict] | None:
        """Raphael's picture of the owner's own and its crop, as the shell keeps them; None while he
        is his emblem."""
        try:
            meta = json.loads((self._raphael_avatar / "picture.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        url = self._data_url(self._raphael_avatar / "picture")
        crop = meta.get("crop") if isinstance(meta, dict) else None
        if url is None or not isinstance(crop, dict):
            return None
        return url, {key: crop.get(key) for key in ("x", "y", "zoom")}

    def _remove_picture(self, avatar: str) -> None:
        kind, _, name = avatar.partition(":")
        if kind != "file" or not name:
            return
        path = self._avatars / MEMBERS_DIR / name
        self._pictures.pop(path, None)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not delete a member's picture: %s", type(exc).__name__)

    # --- views ---------------------------------------------------------------------------------------

    def _member_view(
        self, member: Member, unread: set[int], waiting: set[int], latest: dict[int, Turn]
    ) -> dict[str, Any]:
        last = latest.get(member.id)
        view: dict[str, Any] = {
            **{field: getattr(member, field) for field in PROFILE_FIELDS},
            "id": member.id,
            "name": member.name,
            "role": member.role,
            "personality": member.personality,
            "instructions": member.instructions,
            "avatar": member.avatar,
            "group_id": member.group_id,
            "model": member.model,
            "tools": None if member.tools is None else list(member.tools),
            "position": member.position,
            "created": member.created_at_ms,
            "unread": member.id in unread,
            "waiting": member.id in waiting,
            "preview": "" if last is None else _preview(last),
            "last_at": None if last is None else last.created_at_ms,
        }
        url = self._avatar_url(member.avatar) if member.avatar else None
        if url is not None:
            view["avatar_url"] = url
        return view

    def _raphael_view(
        self, language: str, unread: set[int], waiting: set[int], latest: dict[int, Turn]
    ) -> dict[str, Any]:
        last = latest.get(0)
        view: dict[str, Any] = {
            "blurb": "", "reply_style": "full", "voice": "", "read_aloud": False,
            "notify": False, "ask_before_members": False, "pinned": False, "hidden": False, "draft_first": True,
            "id": 0,
            "name": RAPHAEL_NAMES[language],
            "role": "",
            "personality": "",
            "instructions": "",
            "avatar": "",
            "group_id": None,
            "model": "",
            "tools": None,
            "position": -1,
            "created": 0,
            "unread": 0 in unread,
            "waiting": 0 in waiting,
            "preview": "" if last is None else _preview(last),
            "last_at": None if last is None else last.created_at_ms,
        }
        picture = self._raphael_picture()
        profile = self.first_bot_profile()
        view.update(profile)
        view["profile_ready"] = bool(profile)
        if picture is not None:
            view["avatar_url"], view["avatar_crop"] = picture
        return view

    def view_of(self, member: Member) -> dict[str, Any]:
        """One member as `members.list` shows it."""
        latest = {}
        last = self._history.list(1, member_id=member.id)
        if last:
            latest[member.id] = last[0]
        return self._member_view(member, self.store.unread(), self.waiting_members(), latest)

    def _ui_language(self, params: dict) -> str:
        language = params.get("ui_language")
        if language is None:
            return normalize_language(self._language())
        if not isinstance(language, str):
            raise invalid_params("ui_language must be a string")
        return normalize_language(language)

    # --- members.* -----------------------------------------------------------------------------------

    def expand_command(self, member_id: int, text: str) -> str:
        return self.capabilities.expand(member_id, text)

    def enabled_skills(self, member_id: int) -> list[dict]:
        return [] if member_id == 0 else [skill for skill in self.capabilities.skills(member_id) if skill["enabled"]]

    def skill_read(self, member_id: int, name: object) -> str | None:
        try:
            return self.capabilities.read_skill(member_id, name)
        except (OSError, MemberError):
            return None

    def _cap(self, method, *args):
        try:
            return method(*args)
        except MemberError as error:
            raise _rpc_error(error) from None

    def _cap_member(self, params: dict) -> int:
        member_id = _whole(params, "member_id", least=1)
        self._cap(self.capabilities.check, member_id)
        return member_id

    async def _skills_list(self, params: dict) -> dict:
        return {"skills": self._cap(self.capabilities.skills, self._cap_member(params))}

    async def _skills_import(self, params: dict) -> dict:
        member_id = self._cap_member(params)
        added = []
        try:
            documents = await download_skills(params.get("source"))
            for source, text in documents:
                added.append(self.capabilities.keep_skill(member_id, source, text))
        except MemberError as error:
            for skill in added:
                self.capabilities.delete_skill(member_id, skill["id"])
            raise _rpc_error(error) from None
        self._changed("members.skills.changed", member_id)
        self._changed()
        return {"skill": added[0], "skills": added}

    async def _skills_read(self, params: dict) -> dict:
        """A skill and its whole document, for the owner's page of it (the model reads one by `skill_read`)."""
        skill, text = self._cap(self.capabilities.document, self._cap_member(params), _whole(params, "id", least=1))
        return {"skill": skill, "text": text}

    async def _skills_update(self, params: dict) -> dict:
        """The skill switched on or off (`enabled`), its document edited (`text`), or both: the switch is
        checked first, so that a refused edit changes nothing."""
        member_id = self._cap_member(params)
        skill_id = _whole(params, "id", least=1)
        enabled = params.get("enabled")
        if "text" in params:
            if "enabled" in params and type(enabled) is not bool:
                raise _rpc_error(MemberError("enabled", "enabled must be true or false"))
            skill = self._cap(self.capabilities.rewrite_skill, member_id, skill_id, params["text"])
            if "enabled" in params:
                skill = self._cap(self.capabilities.update_skill, member_id, skill_id, enabled)
        else:
            skill = self._cap(self.capabilities.update_skill, member_id, skill_id, enabled)
        self._changed("members.skills.changed", member_id)
        self._changed()
        return {"skill": skill}

    async def _skills_delete(self, params: dict) -> dict:
        member_id = self._cap_member(params)
        deleted = self._cap(self.capabilities.delete_skill, member_id, _whole(params, "id", least=1))
        self._changed("members.skills.changed", member_id)
        self._changed()
        return {"deleted": deleted}

    async def _commands_list(self, params: dict) -> dict:
        return {"commands": self._cap(self.capabilities.commands, self._cap_member(params))}

    async def _commands_save(self, params: dict) -> dict:
        member_id = self._cap_member(params)
        command_id = _whole(params, "id", least=1) if "id" in params else None
        command = self._cap(self.capabilities.save_command, member_id, command_id,
                            params.get("name"), params.get("description", ""), params.get("template"))
        self._changed("members.commands.changed", member_id)
        self._changed()
        return {"command": command}

    async def _commands_delete(self, params: dict) -> dict:
        member_id = self._cap_member(params)
        deleted = self._cap(self.capabilities.delete_command, member_id, _whole(params, "id", least=1))
        self._changed("members.commands.changed", member_id)
        self._changed()
        return {"deleted": deleted}

    async def _mark_all_read(self, params: dict) -> dict:
        with self._conn:
            self._conn.execute("DELETE FROM member_unread")
        self._changed()
        return {}

    async def _duplicate(self, params: dict) -> dict:
        member_id = self._member_id(params)
        original = self.store.member(member_id)
        assert original is not None
        for number in range(2, 1000):
            suffix = f" ({number})"
            name = original.name[:NAME_MAX - len(suffix)] + suffix
            if self.store.by_name(name) is None:
                break
        fields = {field: getattr(original, field) for field in
                  ("role", "personality", "instructions", "group_id", "avatar", *PROFILE_FIELDS)}
        copy = None
        try:
            copy = self.store.create_member(name=name, **fields)
            for command in self.capabilities.commands(member_id):
                self.capabilities.save_command(copy.id, None, command["name"], command["description"], command["template"])
            for skill in self.capabilities.skills(member_id):
                text = self.capabilities._path(member_id, skill["id"]).read_text(encoding="utf-8")
                saved = self.capabilities.keep_skill(copy.id, skill["source"], text)
                self.capabilities.update_skill(copy.id, saved["id"], skill["enabled"])
        except (MemberError, OSError) as error:
            if copy is not None:
                for skill in self.capabilities.skills(copy.id):
                    self.capabilities.delete_skill(copy.id, skill["id"])
                self.store.delete_member(copy.id)
            raise _rpc_error(error if isinstance(error, MemberError) else MemberError("io", "could not copy skills")) from None
        self._changed()
        return {"member": self.view_of(copy)}

    def use_speech(self, speech) -> None:
        self._speech = speech

    async def _voices(self, params: dict) -> dict:
        voice = self._settings.get("tts_fish_voice")
        configured = bool(self._speech and self._speech.speaks())
        return {"voices": [{"id": voice, "name": "Fish Audio"}] if voice else [], "configured": configured}

    async def _list(self, params: dict) -> dict:
        language = self._ui_language(params)
        unread = self.store.unread()
        waiting = self.waiting_members()
        latest = self._history.latest_by_member()
        members = [self._raphael_view(language, unread, waiting, latest)]
        members += [self._member_view(m, unread, waiting, latest) for m in self.store.members()]
        return {"members": members, "groups": [group_view(g) for g in self.store.groups()],
                "team_context": self.team_context(0, language)}

    def _member_id(self, params: dict) -> int:
        """The `id` of a member that is there (never Raphael's: he is not changed here)."""
        member_id = _whole(params, "id")
        if member_id == 0:
            raise RpcError(
                INVALID_PARAMS, "Invalid params: Raphael is not changed here", {"kind": "raphael"}
            )
        if self.store.member(member_id) is None:
            raise RpcError(INVALID_PARAMS, "Invalid params: no such member", {"kind": "not_found"})
        return member_id

    async def _create(self, params: dict) -> dict:
        avatar = params.get("avatar", "")
        if isinstance(avatar, str) and avatar.startswith("file:"):
            raise RpcError(
                INVALID_PARAMS, "Invalid params: a picture comes by members.set_avatar",
                {"kind": "avatar"},
            )  # fmt: skip
        try:
            member = self.store.create_member(
                name=params.get("name"),
                role=params.get("role", ""),
                instructions=params.get("instructions", ""),
                personality=params.get("personality", ""),
                group_id=params.get("group_id"),
                avatar=avatar if avatar is not None else "",
                tools=params.get("tools"),
                model=params.get("model", "") or "",
                **{field: params[field] for field in PROFILE_FIELDS if field in params},
            )
        except MemberError as error:
            raise _rpc_error(error) from None
        logger.info("A member was added")
        group = None if member.group_id is None else self.store.group(member.group_id)
        offer = routine_template(group.sources if group else (), self._ui_language(params))
        self._changed()
        return {"member": self.view_of(member), "routine_offer": offer}

    async def _update(self, params: dict) -> dict:
        member_id = self._member_id(params)
        fields = {
            name: params[name]
            for name in ("name", "role", "personality", "instructions", "group_id", "avatar", "tools", "model", "position", *PROFILE_FIELDS)
            if name in params
        }
        avatar = fields.get("avatar")
        if isinstance(avatar, str) and avatar.startswith("file:"):
            raise RpcError(
                INVALID_PARAMS, "Invalid params: a picture comes by members.set_avatar",
                {"kind": "avatar"},
            )  # fmt: skip
        if fields.get("model", "") is None:
            fields["model"] = ""
        old = self.store.member(member_id)
        try:
            member = self.store.update_member(member_id, **fields)
        except MemberError as error:
            raise _rpc_error(error) from None
        if old is not None and "avatar" in fields and old.avatar != member.avatar:
            if not any(other.id != member_id and other.avatar == old.avatar for other in self.store.members()):
                self._remove_picture(old.avatar)
        self._changed()
        return {"member": self.view_of(member)}

    async def _delete(self, params: dict) -> dict:
        member_id = self._member_id(params)
        member = self.store.member(member_id)
        assert member is not None
        self._background.forget_member(member_id)
        try:
            self._history.clear_member(member_id)
        except sqlite3.Error as exc:
            logger.warning("Could not delete a member's conversation: %s", type(exc).__name__)
        if self._memory is not None:
            await self._memory.forget_member(member_id)
        for skill in self.capabilities.skills(member_id):
            self.capabilities.delete_skill(member_id, skill["id"])
        # Uploaded avatars can be shared by a duplicated profile.
        if not any(other.id != member_id and other.avatar == member.avatar for other in self.store.members()):
            self._remove_picture(member.avatar)
        deleted = self.store.delete_member(member_id)
        if self._viewing == member_id:
            self._viewing = None
        if deleted:
            self._changed()
            self._changed("routines.changed", member_id)
        logger.info("A member was deleted")
        return {"deleted": deleted}

    async def _set_primary(self, params: dict) -> dict:
        member_id = _whole(params, "id")
        try:
            changed = self.store.set_primary(member_id)
        except MemberError as error:
            raise _rpc_error(error) from None
        if changed:
            logger.info("The primary contact was changed")
            self._changed()
        return {"primary_id": member_id}

    async def _set_avatar(self, params: dict) -> dict:
        member_id = self._member_id(params)
        name, mime, data = params.get("name"), params.get("mime"), params.get("data")
        if not isinstance(name, str) or not isinstance(mime, str) or not isinstance(data, str):
            raise invalid_params("name, mime and data must be strings")
        if len(data) > (MAX_IMAGE_BYTES * 4) // 3 + 8:
            raise RpcError(INVALID_PARAMS, "Invalid params: avatar (size)", {"kind": "size"})
        try:
            raw = base64.b64decode(data, validate=True)
            picture = checked(name, mime, raw, max_bytes=MAX_IMAGE_BYTES)
        except (binascii.Error, ValueError) as error:
            kind = error.kind if isinstance(error, AttachmentError) else "data"
            raise RpcError(INVALID_PARAMS, f"Invalid params: avatar ({kind})", {"kind": kind}) from None
        if not picture.is_image:
            raise RpcError(INVALID_PARAMS, "Invalid params: avatar (type)", {"kind": "type"})
        folder = self._avatars / MEMBERS_DIR
        stored = f"{member_id}-{secrets.token_hex(6)}.{_EXTENSIONS[picture.mime]}"

        def write() -> None:
            folder.mkdir(parents=True, exist_ok=True)
            part = folder / f"{stored}.part"
            part.write_bytes(picture.data)
            part.replace(folder / stored)

        try:
            await asyncio.to_thread(write)
        except OSError as exc:
            logger.warning("Could not keep a member's picture: %s", type(exc).__name__)
            raise RpcError(INVALID_PARAMS, "Invalid params: avatar (io)", {"kind": "io"}) from None
        old = self.store.member(member_id)
        member = self.store.update_member(member_id, avatar=f"file:{stored}")
        if old is not None and old.avatar != member.avatar:
            if not any(other.id != member_id and other.avatar == old.avatar for other in self.store.members()):
                self._remove_picture(old.avatar)
        logger.info("A member's picture was set (%s, %d bytes)", picture.mime, len(picture.data))
        self._changed()
        return {"member": self.view_of(member)}

    async def _presets(self, params: dict) -> dict:
        presets = []
        for path in await asyncio.to_thread(self._preset_files):
            url = self._data_url(path)
            if url is not None:
                presets.append({"avatar": f"preset:{path.stem}", "name": path.stem, "url": url})
        return {"presets": presets}

    async def _unread(self, params: dict) -> dict:
        member_id = _whole(params, "id")
        if not self.store.exists(member_id):
            raise invalid_params("no such member")
        self.store.mark_unread(member_id)
        self._changed()
        return {}

    async def _read(self, params: dict) -> dict:
        member_id = _whole(params, "id")
        if not self.store.exists(member_id):
            raise RpcError(INVALID_PARAMS, "Invalid params: no such member", {"kind": "not_found"})
        if self.store.mark_read(member_id):
            self._changed()
        return {}

    async def _viewing_now(self, params: dict) -> dict:
        member_id = params.get("id")
        if member_id is not None:
            member_id = _whole(params, "id")
            if not self.store.exists(member_id):
                raise RpcError(
                    INVALID_PARAMS, "Invalid params: no such member", {"kind": "not_found"}
                )
            if self.store.mark_read(member_id):
                self._changed()
        self._viewing = member_id
        return {}

    async def _tool_choices(self, params: dict) -> dict:
        try:
            connections = self._connections()
        except Exception as exc:  # the list of connections must not break the panel
            logger.warning("Could not list the connections: %s", type(exc).__name__)
            connections = []
        return {"tools": list(BUILTIN_TOOL_IDS), "connections": connections}

    # --- groups.* ------------------------------------------------------------------------------------

    def _group_id(self, params: dict) -> int:
        group_id = _whole(params, "id", least=1)
        if self.store.group(group_id) is None:
            raise RpcError(INVALID_PARAMS, "Invalid params: no such group", {"kind": "not_found"})
        return group_id

    async def _group_create(self, params: dict) -> dict:
        try:
            group = self.store.create_group(params.get("name"))
        except MemberError as error:
            raise _rpc_error(error) from None
        self._changed()
        return {"group": group_view(group)}

    async def _group_update(self, params: dict) -> dict:
        group_id = self._group_id(params)
        try:
            group = self.store.update_group(
                group_id,
                name=params.get("name"),
                position=params.get("position"),
                sources=params.get("sources"),
            )
        except MemberError as error:
            raise _rpc_error(error) from None
        self._changed()
        return {"group": group_view(group)}

    async def _group_delete(self, params: dict) -> dict:
        deleted = self.store.delete_group(self._group_id(params))
        if deleted:
            self._changed()
        return {"deleted": deleted}

    # --- routines.* ----------------------------------------------------------------------------------

    def _next(self, schedule: Schedule) -> int | None:
        return next_run(schedule, self._now_ms(), tz=self._tz)

    def _routine_member(self, params: dict) -> int:
        member_id = _whole(params, "member_id")
        if not self.store.exists(member_id):
            raise RpcError(INVALID_PARAMS, "Invalid params: no such member", {"kind": "not_found"})
        return member_id

    def _routine_id(self, params: dict) -> int:
        routine_id = _whole(params, "id", least=1)
        if self.store.routine(routine_id) is None:
            raise RpcError(INVALID_PARAMS, "Invalid params: no such routine", {"kind": "not_found"})
        return routine_id

    async def routine_tool(self, member_id: int, name: str, arguments: dict) -> dict:
        """Raphael can select any member; peers stay within their own routines."""
        handlers = {"routine_list": self._routines_list, "routine_create": self._routine_create,
                    "routine_update": self._routine_update, "routine_delete": self._routine_delete}
        if name not in handlers:
            raise invalid_params("unknown routine tool")
        target = _whole(arguments, "member_id") if "member_id" in arguments else member_id
        if member_id != 0 and target != member_id:
            raise invalid_params("routine belongs to another speaker")
        params = {**arguments, "member_id": target}
        if name in ("routine_update", "routine_delete"):
            routine_id = self._routine_id(params)
            if self.store.routine(routine_id).member_id != target:
                raise invalid_params("routine belongs to another speaker")
        return await handlers[name](params)

    def admin_confirmation(self, name: str, arguments: dict, language: str, *, question: bool = True) -> str:
        from ..agent.team_admin import confirmation

        identifier = arguments.get("member_id", 0) if name.startswith("routine_") else arguments.get("member_id", arguments.get("id"))
        target = ""
        if type(identifier) is int:
            if name == "group_rename":
                group = self.store.group(identifier)
                target = group.name if group else ""
            else:
                target = self.name_of(identifier) or ""
        if name in ("routine_update", "routine_delete") and type(arguments.get("id")) is int:
            routine = self.store.routine(arguments["id"])
            if routine:
                target += f" / {routine.title}"
        if name in ("member_create", "create_member", "member_update"):
            current = self.store.member(identifier) if name == "member_update" and type(identifier) is int else None
            group = self.store.group(current.group_id) if current and current.group_id else None
            arguments = {"name": current.name if current else "", "role": current.role if current else "",
                         "blurb": current.blurb if current else "", "group": group.name if group else "", **arguments}
        return confirmation(name, arguments, language, target, question=question)

    async def admin_drafts(self, operations: list[dict]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Build structured member and group drafts for a pending team administration batch."""
        from ..agent.team_admin import GroupDraft, MemberDraft

        presets = (await self._presets({}))["presets"]
        available = [preset["avatar"] for preset in presets]
        used = {member.avatar for member in self.store.members() if member.avatar}
        existing_groups = {group.name.casefold() for group in self.store.groups()}
        drafts: list[dict[str, Any]] = []
        groups: list[dict[str, Any]] = []
        seen_groups: set[str] = set()
        for op in operations:
            name = op.get("name")
            arguments = dict(op.get("arguments") or {})
            if name in ("member_create", "create_member", "member_update"):
                is_update = name == "member_update"
                target_id = arguments.get("id") if is_update else None
                current = self.store.member(target_id) if is_update and type(target_id) is int else None
                if is_update and target_id == 0:
                    from types import SimpleNamespace
                    current = SimpleNamespace(name=RAPHAEL_NAMES[self._language()], role="", personality="", instructions="",
                                              blurb="", avatar="", group_id=None)
                    for field, value in self.first_bot_profile().items():
                        setattr(current, field, value)
                current_group = self.store.group(current.group_id) if current and current.group_id else None
                group_val = arguments.get("group", current_group.name if current_group else "")
                group_name = group_val.strip() if isinstance(group_val, str) else ""
                if "avatar_preset" in arguments:
                    raw_preset = arguments["avatar_preset"]
                    avatar_preset = (
                        ("preset:" + raw_preset if raw_preset and not raw_preset.startswith("preset:") else raw_preset)
                        if isinstance(raw_preset, str)
                        else ""
                    )
                elif is_update:
                    avatar_preset = current.avatar if current and current.avatar.startswith("preset:") else ""
                else:
                    avatar_preset = next((p for p in available if p not in used), "")
                if avatar_preset:
                    used.add(avatar_preset)
                draft = MemberDraft(
                    id=f"draft-{len(drafts) + 1}",
                    name=str(arguments.get("name", current.name if current else "") or ""),
                    role=str(arguments.get("role", current.role if current else "") or ""),
                    blurb=str(arguments.get("blurb", current.blurb if current else "") or ""),
                    personality=str(arguments.get("personality", current.personality if current else "") or ""),
                    instructions=str(arguments.get("instructions", current.instructions if current else "") or ""),
                    group=group_name,
                    avatar_preset=avatar_preset,
                    member_id=target_id if is_update and type(target_id) is int else None,
                )
                drafts.append(draft.to_json())
                if group_name and group_name.casefold() not in existing_groups and group_name.casefold() not in seen_groups:
                    seen_groups.add(group_name.casefold())
                    groups.append(GroupDraft(id=f"group-{len(groups) + 1}", name=group_name).to_json())
            elif name == "group_create":
                raw_gname = arguments.get("name")
                group_name = raw_gname.strip() if isinstance(raw_gname, str) else ""
                if group_name and group_name.casefold() not in seen_groups:
                    seen_groups.add(group_name.casefold())
                    groups.append(GroupDraft(id=f"group-{len(groups) + 1}", name=group_name).to_json())
            elif name == "group_rename":
                raw_gname = arguments.get("name")
                group_name = raw_gname.strip() if isinstance(raw_gname, str) else ""
                gid = arguments.get("id")
                groups.append(
                    GroupDraft(
                        id=f"group-{len(groups) + 1}",
                        name=group_name,
                        group_id=gid if type(gid) is int else None,
                    ).to_json()
                )
        return drafts, groups

    def plan_admin_batch(
        self,
        operations: list[dict],
        *,
        drafts: list[dict] | None = None,
        groups: list[dict] | None = None,
    ) -> list[dict]:
        """Resolve a batch into the operations to execute, applying any owner edits to drafts."""
        if drafts is None and groups is None:
            return list(operations)
        planned: list[dict] = []
        if groups is not None:
            if not isinstance(groups, list):
                raise invalid_params("groups must be a list")
            for item in groups:
                if not isinstance(item, dict):
                    raise invalid_params("invalid group draft")
                raw = dict(item)
                raw_id = raw.pop("id", None)
                gid = raw.pop("group_id", raw_id if type(raw_id) is int else None)
                if gid is not None:
                    raw["id"] = gid
                    planned.append({"name": "group_rename", "arguments": raw})
                else:
                    planned.append({"name": "group_create", "arguments": raw})
        else:
            had_member_ops = any(
                op["name"] in ("member_create", "create_member", "member_update") for op in operations
            )
            member_groups = {
                op["arguments"].get("group", "").strip().casefold()
                for op in operations
                if op["name"] in ("member_create", "create_member", "member_update")
                and isinstance(op["arguments"].get("group"), str)
            }
            for op in operations:
                if op["name"] == "group_rename" and (drafts or not had_member_ops):
                    planned.append(op)
                elif op["name"] == "group_create":
                    raw_name = op["arguments"].get("name")
                    gname = raw_name.strip().casefold() if isinstance(raw_name, str) else ""
                    if gname not in member_groups and (drafts or not had_member_ops):
                        planned.append(op)
        if drafts is not None:
            if not isinstance(drafts, list):
                raise invalid_params("drafts must be a list")
            creates = sum(
                1
                for item in drafts
                if isinstance(item, dict) and item.get("member_id") is None and type(item.get("id")) is not int
            )
            if creates > 10:
                raise invalid_params("member_create_limit")
            for item in drafts:
                if not isinstance(item, dict):
                    raise invalid_params("invalid member draft")
                raw = dict(item)
                raw_id = raw.pop("id", None)
                target_id = raw.pop("member_id", raw_id if type(raw_id) is int else None)
                if target_id is not None:
                    raw["id"] = target_id
                    planned.append({"name": "member_update", "arguments": raw})
                else:
                    raw.setdefault("role", "")
                    planned.append({"name": "member_create", "arguments": raw})
        else:
            planned.extend(
                op for op in operations if op["name"] in ("member_create", "create_member", "member_update")
            )
        return planned

    async def admin_batch(
        self,
        member_id: int,
        operations: list[dict],
        *,
        drafts: list[dict] | None = None,
        groups: list[dict] | None = None,
    ) -> list[dict]:
        """Apply the approved list atomically and publish only committed roster changes."""
        from ..agent.team_admin import BATCH_TOOLS

        if member_id != 0 or not operations or any(op["name"] not in BATCH_TOOLS for op in operations):
            raise invalid_params("invalid team administration batch")
        planned = self.plan_admin_batch(operations, drafts=drafts, groups=groups)
        allowed_targets = {op["arguments"].get("id") for op in operations if op["name"] == "member_update"}
        allowed_groups = {op["arguments"].get("id") for op in operations if op["name"] == "group_rename"}
        create_limit = sum(op["name"] in ("member_create", "create_member") for op in operations)
        if (sum(op["name"] == "member_create" for op in planned) > create_limit
                or any(op["arguments"].get("id") not in allowed_targets for op in planned if op["name"] == "member_update")
                or any(op["arguments"].get("id") not in allowed_groups for op in planned if op["name"] == "group_rename")):
            raise invalid_params("edited drafts exceed the proposed scope")
        if not planned:
            return []
        # Finish filesystem work before opening the transaction; its handlers must not yield.
        presets = (await self._presets({}))["presets"]
        notifications, pictures = [], []
        notify, remove_picture = self._notify, self._remove_picture
        self._notify = lambda method, data: notifications.append((method, data))
        self._remove_picture = pictures.append
        try:
            with self.store.transaction():
                results = [await self.admin_tool(member_id, "member_create" if op["name"] == "create_member" else op["name"], op["arguments"], presets=presets)
                           for op in planned]
        finally:
            self._notify, self._remove_picture = notify, remove_picture
        for picture in pictures:
            remove_picture(picture)
        if notify is not None:
            for method, data in notifications:
                notify(method, data)
        return results

    async def admin_tool(self, member_id: int, name: str, arguments: dict, *, presets: list[dict] | None = None) -> dict:
        """Adapt Raphael's registry to the same RPC handlers, defaults and notifications."""
        from ..agent.team_admin import DEFINITIONS

        if type(member_id) is not int or member_id != 0 or name not in DEFINITIONS:
            raise invalid_params("team administration requires Raphael")
        schema = DEFINITIONS[name]["parameters"]
        if (set(arguments) - set(schema["properties"]) or
                any(field not in arguments for field in schema["required"])):
            raise invalid_params("invalid team administration fields")
        if name == "team_overview":
            roster = await self._list({})
            groups = {group["id"]: group["name"] for group in roster["groups"]}
            members = []
            for item in roster["members"]:
                identifier = item["id"]
                members.append({**{field: item[field] for field in ("id", "name", "role", "blurb")},
                                "group": groups.get(item["group_id"]),
                                "skills_count": len(self.capabilities.skills(identifier)) if identifier else 0,
                                "commands_count": len(self.capabilities.commands(identifier)) if identifier else 0,
                                "routines": (await self._routines_list({"member_id": identifier}))["routines"]})
            return {"members": members, "groups": roster["groups"], "primary_id": roster["team_context"]["primary_id"],
                    "avatar_presets": [{"avatar": preset["avatar"], "name": preset["name"]}
                                       for preset in (await self._presets({}))["presets"]]}
        if name in ("member_create", "member_update"):
            params = dict(arguments)
            if name == "member_update" and params.get("id") == 0:
                from ..brain.settings import checked

                changes = {key: value for key, value in params.items() if key in ("name", "role", "personality", "instructions")}
                if any(params.get(key) for key in ("group", "avatar_preset", "blurb")):
                    raise invalid_params("the first Bot cannot be regrouped")
                profile = {"name": RAPHAEL_NAMES[self._language()], **self.first_bot_profile(), **changes}
                try:
                    key, profile = checked("first_bot_profile", profile)
                except (ValueError, TypeError):
                    raise invalid_params("invalid first Bot profile") from None
                if any(member.name.casefold() == profile["name"].casefold() for member in self.store.members()):
                    raise invalid_params("duplicate first Bot name")
                # Join the batch transaction: SettingsStore.set would commit it prematurely.
                with self.store.transaction():
                    self._conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                       (key, json.dumps(profile)))
                self._changed()
                return {"member": self._raphael_view(self._language(), set(), set(), {})}
            if name == "member_update":
                self._member_id(params)
            if presets is None:
                presets = (await self._presets({}))["presets"]
            available = {preset["avatar"] for preset in presets}
            if "avatar_preset" in params:
                avatar = params.pop("avatar_preset")
                if not isinstance(avatar, str):
                    raise invalid_params("invalid avatar preset")
                if avatar and not avatar.startswith("preset:"):
                    avatar = "preset:" + avatar
                if avatar and avatar not in available:
                    raise invalid_params("unavailable avatar preset")
                params["avatar"] = avatar
            elif name == "member_create":
                used = {member.avatar for member in self.store.members()}
                params["avatar"] = next((preset["avatar"] for preset in presets if preset["avatar"] not in used), "")
            created_group = None
            if "group" in params:
                group_name = params.pop("group")
                if not isinstance(group_name, str):
                    raise invalid_params("invalid group name")
                group_name = group_name.strip()
                group = self.store.group_by_name(group_name) if group_name else None
                if group_name and group is None:
                    created_group = (await self._group_create({"name": group_name}))["group"]
                params["group_id"] = group.id if group else created_group["id"] if created_group else None
            try:
                return await (self._create(params) if name == "member_create" else self._update(params))
            except RpcError:
                # Failed validation must not leave behind the automatically created group.
                if created_group:
                    await self._group_delete({"id": created_group["id"]})
                raise
        handlers = {"member_delete": self._delete, "group_create": self._group_create,
                    "group_rename": self._group_update, "member_skill_import": self._skills_import,
                    "member_command_save": self._commands_save}
        return await handlers[name](dict(arguments))

    async def _routines_list(self, params: dict) -> dict:
        member_id = self._routine_member(params) if "member_id" in params else None
        return {"routines": [routine_view(r) for r in self.store.routines(member_id)]}

    def _finish_run(self, turn_id: int) -> None:
        run = self._conn.execute(
            "SELECT id, routine_id FROM routine_runs WHERE turn_id = ? AND finished IS NULL", (turn_id,)
        ).fetchone()
        if run is None:
            return
        turn = self._history.get(turn_id)
        if turn is None or turn.status in ("running", "confirm"):
            return
        with self._conn:
            self._conn.execute(
                "UPDATE routine_runs SET finished = ?, status = ?, summary = ?, cost = ? WHERE id = ?",
                (self._now_ms(), turn.status, _line(turn.text or turn.detail or ""), turn.cost_usd, run["id"]),
            )
        routine = self.store.routine(run["routine_id"])
        if routine is not None:
            self._changed("routines.changed", routine.member_id)

    async def _routine_runs(self, params: dict) -> dict:
        routine_id = _whole(params, "routine_id", least=1)
        if self.store.routine(routine_id) is None:
            raise invalid_params("no such routine")
        for row in self._conn.execute(
            "SELECT turn_id FROM routine_runs WHERE routine_id = ? AND finished IS NULL", (routine_id,)
        ).fetchall():
            if row[0] is not None:
                self._finish_run(row[0])
        rows = self._conn.execute(
            "SELECT id, routine_id, scheduled, started, finished, status, turn_id, summary, cost"
            " FROM routine_runs WHERE routine_id = ? ORDER BY id DESC LIMIT 100", (routine_id,)
        )
        runs = [dict(row) for row in rows]
        total = self._conn.execute("SELECT COUNT(*) FROM routine_runs WHERE routine_id = ?", (routine_id,)).fetchone()[0]
        return {"runs": runs, "total": total}

    async def _routine_create(self, params: dict) -> dict:
        member_id = self._routine_member(params)
        try:
            schedule = clean_schedule(params.get("schedule"))
            routine = self.store.create_routine(
                member_id=member_id,
                title=params.get("title"),
                prompt=params.get("prompt"),
                schedule=schedule.to_json(),
                enabled=params.get("enabled", True),
                next_run_ms=self._next(schedule),
            )
        except MemberError as error:
            raise _rpc_error(error) from None
        self._changed("routines.changed", routine.member_id)
        return {"routine": routine_view(routine)}

    async def _routine_update(self, params: dict) -> dict:
        routine_id = self._routine_id(params)
        try:
            routine = self.store.update_routine(
                routine_id,
                title=params.get("title"),
                prompt=params.get("prompt"),
                schedule=params.get("schedule"),
                enabled=params.get("enabled"),
                next_run=self._next,
            )
        except MemberError as error:
            raise _rpc_error(error) from None
        self._changed("routines.changed", routine.member_id)
        return {"routine": routine_view(routine)}

    async def _routine_delete(self, params: dict) -> dict:
        routine = self.store.routine(self._routine_id(params))
        assert routine is not None
        deleted = self.store.delete_routine(routine.id)
        if deleted:
            self._changed("routines.changed", routine.member_id)
        return {"deleted": deleted}

    async def _routine_template(self, params: dict) -> dict:
        member_id = self._routine_member(params)
        member = self.store.member(member_id) if member_id else None
        group = None if member is None or member.group_id is None else self.store.group(member.group_id)
        return {"offer": routine_template(group.sources if group else (), self._ui_language(params))}

    # --- the scheduler -------------------------------------------------------------------------------

    def start(self) -> None:
        """Tick from now on, every TICK_S, the first time at once (a routine that was missed while
        the core was not running). Needs the running loop; does nothing without one, or twice."""
        if self._ticking is not None and not self._ticking.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._ticking = loop.create_task(self._tick_forever())

    async def _tick_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # one bad tick must not stop the routines for good
                logger.warning("The routines could not be run: %s", type(exc).__name__)
            await asyncio.sleep(self._tick_s)

    def tick(self, now_ms: int | None = None) -> list[int]:
        """Run the routines that are due now; the ids of those that were run. Each is moved to its
        next time before it runs. One due longer ago than CATCH_UP_MS is not run, only moved."""
        now = self._now_ms() if now_ms is None else now_ms
        ran: list[int] = []
        for routine in self.store.due(now):
            assert routine.next_run_ms is not None
            missed_ms = now - routine.next_run_ms
            catch_up = missed_ms <= CATCH_UP_MS
            following = next_run(routine.schedule, now, tz=self._tz)
            count = self._conn.execute(
                "SELECT COUNT(*) FROM routine_runs WHERE routine_id = ?", (routine.id,)
            ).fetchone()[0]
            if routine.schedule.max_runs is not None and count >= routine.schedule.max_runs:
                self.store.advance(routine.id, next_run_ms=None, ran_at_ms=None)
                self._changed("routines.changed", routine.member_id)
                continue
            if catch_up and routine.schedule.max_runs is not None and count + 1 >= routine.schedule.max_runs:
                following = None
            still_there = self.store.advance(
                routine.id, next_run_ms=following, ran_at_ms=now if catch_up else None
            )
            if not still_there:
                continue
            self._changed("routines.changed", routine.member_id)
            if not catch_up:
                logger.info("A routine was missed too long ago to run: %d", routine.id)
                continue
            self._run(routine)
            ran.append(routine.id)
        return ran

    def _run(self, routine: Routine) -> None:
        """A routine's run: a turn of its thread, and a background task of its member that
        completes it."""
        speaker = self.store.speaker(routine.member_id)
        if speaker is None:
            return
        language = normalize_language(self._language())
        session_id = f"routine:{routine.id}"
        turn_id = self._history.add(
            session_id=session_id,
            question=routine.title,
            route="routine",
            prefix="",
            text="",
            status="running",
            source="routine",
            member_id=routine.member_id,
        )
        model = self._settings.get("agent_model")
        with self._conn:
            self._conn.execute(
                "INSERT INTO routine_runs (routine_id, scheduled, started, status, turn_id) VALUES (?, ?, ?, 'running', ?)",
                (routine.id, routine.next_run_ms, self._now_ms(), turn_id),
            )
        model = model.strip() if isinstance(model, str) and model.strip() else DEFAULTS["agent_model"]
        try:
            task_id = self._background.submit(
                session_id=session_id,
                text=routine.prompt,
                language=language,
                model=model,
                member_id=routine.member_id,
                routine_id=routine.id,
            )
        except Refused as refused:
            self._history.resolve(
                turn_id, prefix="", text=phrase(language, refused.kind), status="error"
            )
            logger.info("A routine could not run: %s", refused.kind)
        else:
            self._background.attach_turn(task_id, turn_id)
            logger.info("A routine runs: %d (task %d)", routine.id, task_id)
        self.turn_arrived(turn_id)
        if self._notify is not None:
            self._notify("agent.turn", {"turn_id": turn_id, "member_id": routine.member_id})

    async def aclose(self) -> None:
        for task in self._speech_tasks:
            task.cancel()
        await asyncio.gather(*self._speech_tasks, return_exceptions=True)
        ticking, self._ticking = self._ticking, None
        if ticking is not None and not ticking.done():
            ticking.cancel()
            await asyncio.gather(ticking, return_exceptions=True)
