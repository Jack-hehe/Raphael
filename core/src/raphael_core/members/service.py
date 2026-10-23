"""The members of the owner's team as RPC methods, with their pictures, their routines' scheduler and
the team as the assistant sees it.

The RPC methods (the main window only, through the shell's allow-list):

- `members.list {ui_language?}` -> `{members, groups}`. `members` is Raphael first (`id` 0, his name
  in the language, his picture when the owner chose one: `avatar_url`, a data URL, and
  `avatar_crop`), then every member in its place: `{id, name, role, instructions, avatar,
  avatar_url?, group_id, model, tools, position, created, unread, waiting, preview, last_at}`.
  `preview` is the last line of its thread (one line, cut) and `last_at` when it was said; `unread`
  says the thread has news the owner has not seen (`members.read` clears it), and `waiting` that a
  question waits for the owner there. `groups` is `[{id, name, position, sources}]`.
- `members.create {name, role?, instructions?, group_id?, avatar?, tools?, model?, ui_language?}` ->
  `{member, routine_offer}`. `routine_offer` is the routine offered to a member of a group that
  has places to look (`sources`): `{title, prompt, schedule}` for 「每天早上的進度」 at 09:00, or
  null. It is offered, not made: `routines.create` makes it.
- `members.update {id, name?, role?, instructions?, group_id?, avatar?, tools?, model?, position?}`
  -> `{member}`. `avatar` here is "" or "preset:<stem>"; a picture of the owner's own comes by
  `members.set_avatar`. Raphael (0) is not changed here: his picture is the app's to change.
- `members.delete {id}` -> `{deleted}`: the member, its conversation (and the exchanges of other
  threads with it), its memory folder, its routines, its tasks and its picture. The app asks first.
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
unread, unless the owner heard it said (`source` "voice").

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
from ..agent.speaker import BUILTIN_TOOL_IDS, RAPHAEL, RAPHAEL_NAMES, Speaker, is_raphael_name
from ..background.tasks import Refused
from ..brain.settings import DEFAULTS, SettingsStore
from ..clock import wall_clock_ms
from ..protocol import INVALID_PARAMS, RpcError, invalid_params
from ..router import Router
from ..services.attachments import IMAGE_TYPES, MAX_IMAGE_BYTES, AttachmentError, checked, sniff_ok
from .schedule import Schedule, next_run
from .store import (
    PROMPT_MAX,
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
            "members.list": self._list,
            "members.create": self._create,
            "members.update": self._update,
            "members.delete": self._delete,
            "members.set_avatar": self._set_avatar,
            "members.presets": self._presets,
            "members.read": self._read,
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
        }
        for method, handler in methods.items():
            router.register(method, handler)

    # --- the team, as the assistant sees it (agent.team.Team) ----------------------------------------

    def speaker(self, member_id: int) -> Speaker | None:
        return self.store.speaker(member_id)

    def find(self, name: str) -> Speaker | None:
        if not isinstance(name, str) or not name.strip():
            return None
        if is_raphael_name(name):
            return RAPHAEL
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

    def create(self, *, name: Any, role: Any, instructions: Any, group: Any) -> Speaker:
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
            name=name, role=role or "", instructions=instructions or "", group_id=group_id
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
            speaker = self.store.speaker(member_id)
        except sqlite3.Error as exc:
            logger.warning("Could not read a member: %s", type(exc).__name__)
            return None
        return None if speaker is None else speaker.name

    # --- unread and waiting --------------------------------------------------------------------------

    def turn_arrived(self, turn_id: int) -> None:
        """A turn was written or completed: its thread is unread, unless it is on screen or the
        owner heard the turn said (`source` "voice")."""
        try:
            turn = self._history.get(turn_id)
            if turn is None or turn.source == "voice" or turn.member_id == self._viewing:
                return
            was_unread = turn.member_id in self.store.unread()
            self.store.mark_unread(turn.member_id)
            if not was_unread:
                self._changed()
        except sqlite3.Error as exc:
            logger.warning("Could not mark a thread unread: %s", type(exc).__name__)

    def add_waiting_source(self, source: Callable[[], Iterable[int]]) -> None:
        """Add a source of threads with a question waiting for the owner: the members it names
        are `waiting` too. The safety rules' questions from the background come in here."""
        self._waiting_sources.append(source)

    def waiting_members(self) -> set[int]:
        """The members (Raphael as 0) in whose thread a question waits for the owner."""
        waiting = {
            int(row[0])
            for row in self._conn.execute(
                "SELECT DISTINCT member_id FROM agent_turns WHERE status = 'confirm'"
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
        gone (the app shows the first character of the name)."""
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
            "id": member.id,
            "name": member.name,
            "role": member.role,
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
            "id": 0,
            "name": RAPHAEL_NAMES[language],
            "role": "",
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

    async def _list(self, params: dict) -> dict:
        language = self._ui_language(params)
        unread = self.store.unread()
        waiting = self.waiting_members()
        latest = self._history.latest_by_member()
        members = [self._raphael_view(language, unread, waiting, latest)]
        members += [self._member_view(m, unread, waiting, latest) for m in self.store.members()]
        return {"members": members, "groups": [group_view(g) for g in self.store.groups()]}

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
                group_id=params.get("group_id"),
                avatar=avatar if avatar is not None else "",
                tools=params.get("tools"),
                model=params.get("model", "") or "",
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
            for name in ("name", "role", "instructions", "group_id", "avatar", "tools", "model", "position")
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
        self._remove_picture(member.avatar)
        deleted = self.store.delete_member(member_id)
        if self._viewing == member_id:
            self._viewing = None
        if deleted:
            self._changed()
            self._changed("routines.changed", member_id)
        logger.info("A member was deleted")
        return {"deleted": deleted}

    async def _set_avatar(self, params: dict) -> dict:
        member_id = self._member_id(params)
        name, mime, data = params.get("name"), params.get("mime"), params.get("data")
        if not isinstance(name, str) or not isinstance(mime, str) or not isinstance(data, str):
            raise invalid_params("name, mime and data must be strings")
        if len(data) > (MAX_IMAGE_BYTES * 4) // 3 + 8:
            raise RpcError(INVALID_PARAMS, "Invalid params: avatar (size)", {"kind": "size"})
        try:
            raw = base64.b64decode(data, validate=True)
            picture = checked(name, mime, raw)
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

    def _next(self, schedule: Schedule) -> int:
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
        """The same routine API, scoped to the calling speaker; tool data cannot select a peer."""
        handlers = {"routine_list": self._routines_list, "routine_create": self._routine_create,
                    "routine_update": self._routine_update, "routine_delete": self._routine_delete}
        if name not in handlers:
            raise invalid_params("unknown routine tool")
        params = {**arguments, "member_id": member_id}
        if name in ("routine_update", "routine_delete"):
            routine_id = self._routine_id(params)
            if self.store.routine(routine_id).member_id != member_id:
                raise invalid_params("routine belongs to another speaker")
        return await handlers[name](params)

    async def _routines_list(self, params: dict) -> dict:
        member_id = self._routine_member(params)
        return {"routines": [routine_view(r) for r in self.store.routines(member_id)]}

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
        model = model.strip() if isinstance(model, str) and model.strip() else DEFAULTS["agent_model"]
        try:
            task_id = self._background.submit(
                session_id=session_id,
                text=routine.prompt,
                language=language,
                model=speaker.model_or(model),
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
        ticking, self._ticking = self._ticking, None
        if ticking is not None and not ticking.done():
            ticking.cancel()
            await asyncio.gather(ticking, return_exceptions=True)
