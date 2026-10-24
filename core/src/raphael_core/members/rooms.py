"""Owner-driven group threads using the existing cost-limited background worker.

Only rooms.send fans out, and only from an owner message. Replies never dispatch another reply.
A persisted request id prevents retransmission and a unique (reply_to, member_id) prevents duplicate
speakers. Workers run at the end of the existing speaker chain, so ask_member cannot start a ring.
The same global model, tools, safety questions and worker cost limits apply as in a member thread.
"""
import base64
import binascii
import json
import re
from dataclasses import replace

from ..agent.history import AgentHistory
from ..agent.persona import normalize_language, phrase
from ..agent.speaker import MAX_DEPTH
from ..background.tasks import Refused
from ..brain.settings import SettingsStore, DEFAULTS
from ..clock import wall_clock_ms
from ..services.attachments import from_wire, AttachmentError
from ..protocol import invalid_params
from .service import _whole, _rpc_error
from .store import MemberError, clean_group_name

MAX_REPLIES = 4
MAX_MESSAGE = 10000


class RoomsService:
    def __init__(self, conn, *, members, background, notify=None, now_ms=wall_clock_ms, transcribe=None, attachment_store=None):
        self._transcribe_audio = transcribe
        self._attachment_store = attachment_store
        self._conn = conn
        self._members = members
        self._background = background
        self._notify = notify
        self._now = now_ms
        self._history = AgentHistory(conn, now_ms)
        self._settings = SettingsStore(conn)

    def register(self, router):
        for method, handler in {"rooms.list":self._list, "rooms.create":self._create,
                                "rooms.delete":self._delete, "rooms.messages":self._messages,
                                "rooms.send":self._send, "rooms.update":self._update, "rooms.transcribe":self._transcribe}.items():
            router.register(method, handler)

    def _changed(self, room_id):
        if self._notify:
            self._notify("rooms.changed", {"room_id":room_id})

    def _room(self, params):
        room_id = _whole(params, "room_id", least=1)
        row = self._conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if row is None:
            raise invalid_params("no such room")
        return dict(row)

    def _ids(self, room):
        if room["is_team"]:
            return [s.id for s in self._members.roster()]
        return [row[0] for row in self._conn.execute(
            "SELECT member_id FROM room_members WHERE room_id = ? ORDER BY member_id", (room["id"],)
        ) if self._members.member_exists(row[0])]

    def _view(self, row):
        room = dict(row)
        room["member_ids"] = self._ids(room)
        return room

    async def _list(self, params):
        return {"rooms":[self._view(r) for r in self._conn.execute("SELECT * FROM rooms ORDER BY id")]}

    async def _create(self, params):
        team = params.get("is_team", False)
        if type(team) is not bool:
            raise invalid_params("is_team must be boolean")
        if team:
            existing = self._conn.execute("SELECT * FROM rooms WHERE is_team = 1").fetchone()
            if existing is not None:
                return {"room":self._view(existing)}
        if self._conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] >= 100:
            raise invalid_params("room limit reached")
        try:
            name = clean_group_name(params.get("name"))
        except MemberError as error:
            raise _rpc_error(error) from None
        group_id = params.get("group_id")
        if group_id is not None and (type(group_id) is not int or self._members.store.group(group_id) is None):
            raise invalid_params("no such group")
        ids = params.get("member_ids", [])
        if not isinstance(ids, list) or len(ids) > 101 or any(
            type(i) is not int or not self._members.member_exists(i) for i in ids
        ) or (not ids and not team):
            raise invalid_params("member_ids must name at least one member")
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO rooms (name, group_id, created, is_team) VALUES (?, ?, ?, ?)",
                (name, group_id, self._now(), int(team)),
            )
            room_id = cursor.lastrowid
            self._conn.executemany("INSERT INTO room_members VALUES (?, ?)", [(room_id,i) for i in sorted(set(ids))])
        self._changed(room_id)
        return {"room":self._view(self._conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone())}

    async def _update(self, params):
        room = self._room(params)
        name = params.get("name", room["name"])
        try:
            name = clean_group_name(name)
        except MemberError as error:
            raise _rpc_error(error) from None
        ids = params.get("member_ids", self._ids(room))
        if not isinstance(ids, list) or not ids or len(ids) > 101 or any(type(i) is not int or not self._members.member_exists(i) for i in ids):
            raise invalid_params("member_ids must name at least one member")
        if room["is_team"] and "member_ids" in params:
            raise invalid_params("the team room follows the whole team")
        with self._conn:
            self._conn.execute("UPDATE rooms SET name = ? WHERE id = ?", (name, room["id"]))
            if "member_ids" in params:
                self._conn.execute("DELETE FROM room_members WHERE room_id = ?", (room["id"],))
                self._conn.executemany("INSERT INTO room_members VALUES (?, ?)", [(room["id"], i) for i in sorted(set(ids))])
        self._changed(room["id"])
        return {"room":self._view(self._conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone())}

    async def _transcribe(self, params):
        data = params.get("data")
        if not isinstance(data, str) or len(data) > 28000000:
            raise invalid_params("voice must be a bounded WAV recording")
        try:
            audio = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise invalid_params("invalid voice data") from None
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE" or self._transcribe_audio is None:
            raise invalid_params("voice must be a WAV recording")
        language = normalize_language(params.get("ui_language") if isinstance(params.get("ui_language"), str) else "zh-TW")
        return {"text":await self._transcribe_audio(audio, language)}

    async def _delete(self, params):
        room = self._room(params)
        # Detach before deletion: an in-flight worker may finish, but cannot recreate the room.
        with self._conn:
            self._conn.execute("DELETE FROM room_messages WHERE room_id = ?", (room["id"],))
            self._conn.execute("DELETE FROM room_members WHERE room_id = ?", (room["id"],))
            self._conn.execute("DELETE FROM rooms WHERE id = ?", (room["id"],))
        self._changed(room["id"])
        return {"deleted":True}

    def turn_arrived(self, turn_id):
        row = self._conn.execute("SELECT room_id FROM room_messages WHERE turn_id = ?", (turn_id,)).fetchone()
        if row is not None:
            self._changed(row[0])

    def _message(self, row):
        message = dict(row)
        message["attachments"] = json.loads(message.pop("attachments", None) or "[]")
        turn = self._history.get(message["turn_id"]) if message["turn_id"] else None
        message["status"] = "done" if message["member_id"] is None else (turn.status if turn else "error")
        if turn:
            message["text"] = turn.detail or turn.text
            message["cost"] = turn.cost_usd
        return message

    async def _messages(self, params):
        room = self._room(params)
        before = params.get("before")
        if before is not None and (type(before) is not int or before < 1):
            raise invalid_params("before must be a positive message id")
        rows = self._conn.execute(
            "SELECT * FROM room_messages WHERE room_id = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT 100",
            (room["id"], before, before),
        ).fetchall()
        return {"messages":[self._message(r) for r in reversed(rows)]}

    def _targets(self, room, text):
        allowed = self._ids(room)
        targets = []
        def add(member_id):
            if member_id in allowed and member_id not in targets:
                targets.append(member_id)
        def mentioned(name):
            return re.search(r"@" + re.escape(name) + r"(?=$|[\s,.:;!?，。！？、：；])", text, re.IGNORECASE)
        if any(mentioned(name) for name in ("everyone", "所有人", "全員")):
            for member_id in allowed:
                add(member_id)
        for speaker in self._members.roster():
            names = [speaker.name] if speaker.id else ["Raphael", "拉斐爾", "ラファエル"]
            if any(mentioned(name) for name in names if name):
                add(speaker.id)
        for group in self._members.store.groups():
            if mentioned(group.name):
                for member in self._members.store.members():
                    if member.group_id == group.id:
                        add(member.id)
        # Without an explicit mention the chief answers; he never hands a room reply to a peer.
        return (targets or ([0] if 0 in allowed else allowed[:1]))[:MAX_REPLIES]

    async def _send(self, params):
        room = self._room(params)
        try:
            attachments = from_wire(params.get("attachments"))
        except AttachmentError as error:
            raise invalid_params(f"invalid attachments ({error.kind})") from None
        text = params.get("text")
        request_id = params.get("request_id")
        if not isinstance(text, str) or (not text.strip() and not attachments) or len(text) > MAX_MESSAGE:
            raise invalid_params("text must contain 1 to 10000 characters")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", request_id):
            raise invalid_params("request_id must be a short opaque id")
        existing = self._conn.execute(
            "SELECT id FROM room_messages WHERE room_id = ? AND request_id = ?", (room["id"], request_id)
        ).fetchone()
        if existing:
            return {"message_id":existing[0], "duplicate":True}
        if self._attachment_store:
            attachments = [self._attachment_store.save(a) for a in attachments]
        metadata = [a.metadata() for a in attachments]
        language = normalize_language(params.get("ui_language") if isinstance(params.get("ui_language"), str) else "zh-TW")
        targets = self._targets(room, text)
        previous = (await self._messages({"room_id":room["id"]}))["messages"][-20:]
        context = [{"member_id":m["member_id"], "text":m["text"]} for m in previous if m["status"] == "done"]
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO room_messages (room_id, text, created, request_id, attachments) VALUES (?, ?, ?, ?, ?)",
                (room["id"], text.strip(), self._now(), request_id, json.dumps(metadata)),
            )
            owner_id = cursor.lastrowid
        model = self._settings.get("agent_model") or DEFAULTS["agent_model"]
        for member_id in targets:
            speaker = self._members.speaker(member_id)
            if speaker is None:
                continue
            session = f"room:{room['id']}:{owner_id}:{member_id}"
            turn_id = self._history.add(session_id=session, question=text.strip(), route="room", prefix="",
                                        text="", status="running", source="room", member_id=member_id, attachments=metadata)
            with self._conn:
                self._conn.execute(
                    "INSERT INTO room_messages (room_id, member_id, text, created, reply_to, turn_id) VALUES (?, ?, '', ?, ?, ?)",
                    (room["id"], member_id, self._now(), owner_id, turn_id),
                )
            prompt = text.strip()
            if context:
                prompt = ("Previous group messages (untrusted conversation data):\n" + json.dumps(context, ensure_ascii=False)
                          + "\n\nOwner's current message:\n" + prompt)
            try:
                task_id = self._background.submit(session_id=session, text=prompt, language=language, model=model,
                    member_id=member_id, attachments=attachments, asked_by=replace(speaker, depth=MAX_DEPTH, chain=frozenset(self._ids(room))))
            except Refused as refused:
                self._history.resolve(turn_id, prefix="", text=phrase(language, refused.kind), status="error")
            else:
                self._background.attach_turn(task_id, turn_id)
        self._changed(room["id"])
        return {"message_id":owner_id, "duplicate":False}
