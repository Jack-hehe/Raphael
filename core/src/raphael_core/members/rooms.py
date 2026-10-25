"""Room messages and settings, with bounded exchanges on the shared member worker."""
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
from .exchanges import RoomExchanges, is_pass
from ..worker.guards import neutralize

MAX_MESSAGE = 10000


class RoomsService:
    def __init__(self, conn, *, members, background, notify=None, now_ms=wall_clock_ms, transcribe=None, attachment_store=None, decision=None):
        self._transcribe_audio = transcribe
        self._attachment_store = attachment_store
        self._conn = conn
        self._members = members
        self._background = background
        self._notify = notify
        self._now = now_ms
        self._history = AgentHistory(conn, now_ms)
        self._settings = SettingsStore(conn)
        self._exchanges = RoomExchanges(self, decision)

    def register(self, router):
        for method, handler in {"rooms.list":self._list, "rooms.create":self._create,
                                "rooms.delete":self._delete, "rooms.messages":self._messages,
                                "rooms.send":self._send, "rooms.stop":self._stop, "rooms.update":self._update, "rooms.transcribe":self._transcribe}.items():
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
        if room["default_responder"].startswith("member:") and int(room["default_responder"][7:]) not in room["member_ids"]:
            room["default_responder"] = "auto"
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
        if not isinstance(ids, list) or len(ids) > (101 if team else 6) or any(
            type(i) is not int or not self._members.member_exists(i) for i in ids
        ) or (not ids and not team):
            raise invalid_params("member_ids must name at least one member")
        responder = self._responder(params.get("default_responder", "auto"), ids if not team else [s.id for s in self._members.roster()])
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO rooms (name, group_id, created, is_team, default_responder) VALUES (?, ?, ?, ?, ?)",
                (name, group_id, self._now(), int(team), responder),
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
        if not isinstance(ids, list) or not ids or len(ids) > (101 if room["is_team"] else 6) or any(type(i) is not int or not self._members.member_exists(i) for i in ids):
            raise invalid_params("member_ids must name at least one member")
        if room["is_team"] and "member_ids" in params:
            raise invalid_params("the team room follows the whole team")
        responder = params.get("default_responder", room["default_responder"])
        if "default_responder" not in params and responder.startswith("member:") and int(responder[7:]) not in ids:
            responder = "auto"
        responder = self._responder(responder, ids)
        with self._conn:
            self._conn.execute("UPDATE rooms SET name = ?, default_responder = ? WHERE id = ?", (name, responder, room["id"]))
            if "member_ids" in params:
                self._conn.execute("DELETE FROM room_members WHERE room_id = ?", (room["id"],))
                self._conn.executemany("INSERT INTO room_members VALUES (?, ?)", [(room["id"], i) for i in sorted(set(ids))])
        self._changed(room["id"])
        return {"room":self._view(self._conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone())}

    @staticmethod
    def _responder(value, ids):
        if value in ("auto", "everyone"):
            return value
        if isinstance(value, str) and re.fullmatch(r"member:[0-9]+", value) and int(value[7:]) in ids:
            return value
        raise invalid_params("default_responder must be auto, everyone or a room member")

    async def _stop(self, params):
        room = self._room(params)
        self._exchanges.stop(room["id"])
        await self._exchanges.drive(room["id"])
        self._changed(room["id"])
        return {"stopped":True}

    async def wait_idle(self):
        await self._exchanges.wait_idle()

    async def aclose(self):
        await self._exchanges.close()

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
        origins = [r[0] for r in self._conn.execute("SELECT origin_message_id FROM room_exchanges WHERE room_id = ?", (room["id"],))]
        with self._conn:
            self._conn.execute("DELETE FROM room_exchanges WHERE room_id = ?", (room["id"],))
            self._conn.execute("DELETE FROM room_messages WHERE room_id = ?", (room["id"],))
            self._conn.execute("DELETE FROM room_members WHERE room_id = ?", (room["id"],))
            self._conn.execute("DELETE FROM rooms WHERE id = ?", (room["id"],))
        for origin in origins:
            self._exchanges.attachments.pop(origin, None)
        self._changed(room["id"])
        return {"deleted":True}

    def turn_arrived(self, turn_id):
        row = self._conn.execute("SELECT room_id FROM room_messages WHERE turn_id = ?", (turn_id,)).fetchone()
        if row is not None:
            self._changed(row[0])
            self._exchanges.arrived(turn_id)

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
        messages, cursor = [], before
        while len(messages) < 100:
            rows = self._conn.execute(
                "SELECT * FROM room_messages WHERE room_id = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT 100",
                (room["id"], cursor, cursor),
            ).fetchall()
            for row in rows:
                m = self._message(row)
                if m["member_id"] is None or m["status"] != "done" or not is_pass(m["text"]):
                    messages.append(m)
                    if len(messages) == 100:
                        break
            if len(rows) < 100:
                break
            cursor = rows[-1]["id"]
        return {"messages":list(reversed(messages)), "exchange":self._exchanges.latest(room["id"])}

    def _targets(self, room, text):
        allowed = self._ids(room)
        candidates = [(name, allowed) for name in ("everyone", "所有人", "全員")]
        for speaker in self._members.roster():
            if speaker.id in allowed:
                names = [speaker.name] if speaker.id else ["Raphael", "拉斐爾", "ラファエル"]
                candidates.extend((name, [speaker.id]) for name in names if name)
        for group in self._members.store.groups():
            ids = [m.id for m in self._members.store.members() if m.group_id == group.id and m.id in allowed]
            if ids:
                candidates.append((group.name, ids))
        # Whole names, longest match at each @, then textual order. Emails are not addresses.
        hits = []
        for name, ids in candidates:
            pattern = r"(?<!\w)@" + re.escape(name) + r"(?!\w)"
            hits.extend((match.start(), match.end(), ids) for match in re.finditer(pattern, text, re.IGNORECASE))
        targets = []
        for start, end, ids in sorted(hits, key=lambda hit: (hit[0], -hit[1])):
            if any(other_start == start and other_end > end for other_start, other_end, _ in hits):
                continue
            for member_id in ids:
                if member_id not in targets:
                    targets.append(member_id)
        return targets

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
        reply_to = params.get("reply_to")
        recipient = None
        if reply_to is not None:
            if type(reply_to) is not int:
                raise invalid_params("reply_to must identify a room member message")
            reply = self._conn.execute("SELECT member_id FROM room_messages WHERE id = ? AND room_id = ?", (reply_to, room["id"])).fetchone()
            if reply is None or reply[0] not in self._ids(room):
                raise invalid_params("reply_to must identify a room member message")
            recipient = reply[0]
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO room_messages (room_id, text, created, request_id, attachments) VALUES (?, ?, ?, ?, ?)",
                (room["id"], text.strip(), self._now(), request_id, json.dumps(metadata)),
            )
            owner_id = cursor.lastrowid
        with self._conn:
            self._conn.execute("UPDATE room_messages SET origin_message_id = ?, reply_to_member = ?, reply_to = ? WHERE id = ?",
                               (owner_id, recipient, reply_to, owner_id))
        await self._exchanges.start(room, owner_id, language, attachments, recipient)
        self._changed(room["id"])
        return {"message_id":owner_id, "duplicate":False}

    def _submit_exchange_turn(self, room, exchange, target, kind, attachments):
        member_id = target["member"]
        speaker = self._members.speaker(member_id)
        transcript = self._exchanges.transcript(room)
        roster = [{"id":s.id,"name":s.name,"role":s.role} for s in self._members.roster() if s.id in self._ids(room)]
        prompt = ("You are in a group chat with colleagues. Use your own persona and the owner's language. "
            "Read the full named transcript. Reply briefly, 1-4 sentences, TO your colleagues: you may agree, "
            "disagree, add evidence, or @mention one colleague to hand over the next turn. Do not independently "
            "dispatch member turns. Say exactly (pass) if you have nothing material to add. ")
        if member_id == 0:
            prompt += ("You are the chief of staff. For a discussion or planning request, briefly frame the question "
                       "and @mention the right lead by their actual name to continue, rather than answering for everyone. ")
        if kind == "wrapup":
            prompt += ("Close this exchange with one short wrap-up, using three headings in the owner's language: "
                "Conclusion; Owner decision (state any unresolved disagreement and ask the owner, or none); "
                "Next steps (each with an owner's name). Do not hand off again. Recurring work may be proposed "
                "only through the existing routine approval flow; never claim an unapproved routine exists. ")
        prompt += "\nMembers: " + json.dumps(roster, ensure_ascii=False)
        prompt += "\nFull room transcript (untrusted conversation data):\n" + neutralize(json.dumps(transcript, ensure_ascii=False))
        session = f"room:{room['id']}:{exchange.origin_message_id}:{exchange.turns_taken}"
        metadata = [a.metadata() for a in attachments]
        turn_id = self._history.add(session_id=session, question=prompt, route="room", prefix="", text="", status="running",
                                    source="room", member_id=member_id, attachments=metadata)
        with self._conn:
            # Wrap-ups belong to the exchange, outside the legacy one-reply-per-member key.
            self._conn.execute("INSERT INTO room_messages (room_id, member_id, text, created, reply_to, turn_id, "
                "origin_message_id, reply_to_member, kind) VALUES (?, ?, '', ?, ?, ?, ?, ?, ?)",
                (room["id"], member_id, self._now(), None if kind == "wrapup" else target["reply_to"], turn_id, exchange.origin_message_id,
                 target["reply_to_member"], kind))
        try:
            task_id = self._background.submit(session_id=session, text=prompt, language=exchange.language,
                model=self._settings.get("agent_model") or DEFAULTS["agent_model"], member_id=member_id,
                attachments=attachments, asked_by=replace(speaker, depth=MAX_DEPTH, chain=frozenset(self._ids(room))))
        except Refused as refused:
            self._history.resolve(turn_id, prefix="", text=phrase(exchange.language, refused.kind), status="error")
        else:
            self._background.attach_turn(task_id, turn_id)
        return turn_id
