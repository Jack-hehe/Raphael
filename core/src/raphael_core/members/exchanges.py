"""Bounded room exchanges: route, serialize, follow explicit handoffs, optionally wrap up.

All member turns use the background worker. Only this orchestration state is new;
personas, tool approvals, timeouts and member capability checks stay in that worker.
"""
import asyncio
import json
import re
from dataclasses import dataclass

from ..agent.ledger import Ledger
from ..brain.spend import SpendTracker

MAX_TURNS = 6
MAX_HOPS = 3
MAX_CHIME_INS = 2
MIN_CONFIDENCE = .6


def is_pass(text):
    return not text.strip() or bool(re.fullmatch(r"\(?\s*pass\s*\)?\.?", text.strip(), re.IGNORECASE))


@dataclass(frozen=True)
class Exchange:
    origin_message_id: int
    room_id: int
    state: str
    language: str
    pending: str | None
    active_turn: int | None
    turns_taken: int
    spoken: str
    hops: int
    chime_ins: int
    cost: float
    stop_requested: int
    wrapup_sent: int
    reason: str


class RoomExchanges:
    def __init__(self, rooms, decision=None):
        self.rooms, self.conn, self.decision = rooms, rooms._conn, decision
        self.locks, self.tasks, self.attachments = {}, set(), {}
        self.spend = SpendTracker(self.conn, now_ms=rooms._now)
        self.closed = False
        with self.conn:
            self.conn.execute("UPDATE room_exchanges SET state = 'stopped', reason = 'restart', pending = '[]' "
                              "WHERE state IN ('queued','running')")

    def get(self, origin):
        row = self.conn.execute("SELECT * FROM room_exchanges WHERE origin_message_id = ?", (origin,)).fetchone()
        return Exchange(**dict(row)) if row else None

    def latest(self, room_id):
        row = self.conn.execute("SELECT * FROM room_exchanges WHERE room_id = ? ORDER BY origin_message_id DESC LIMIT 1", (room_id,)).fetchone()
        return dict(row) if row else None

    def update(self, origin, **fields):
        with self.conn:
            self.conn.execute("UPDATE room_exchanges SET " + ", ".join(f"{key} = ?" for key in fields) +
                              " WHERE origin_message_id = ?", (*fields.values(), origin))

    def fallback(self, room):
        ids = self.rooms._ids(room)
        choice = room["default_responder"]
        if choice == "everyone":
            return ids
        if choice.startswith("member:") and int(choice[7:]) in ids:
            return [int(choice[7:])]
        if 0 in ids:
            return [0]
        def rank(i):
            role = self.rooms._members.speaker(i).role.upper()
            return next((n for n, words in enumerate((("CEO", "執行長"), ("COO", "CTO", "營運長", "技術長"),
                                                     ("PM", "專案經理", "產品經理"))) if any(w in role for w in words)), 3)
        return [min(ids, key=rank)] if ids else []

    def transcript(self, room):
        people = {s.id:s for s in self.rooms._members.roster()}
        result = []
        for row in self.conn.execute("SELECT * FROM room_messages WHERE room_id = ? ORDER BY id", (room["id"],)):
            m = self.rooms._message(row)
            if m["status"] != "done" or (m["member_id"] is not None and is_pass(m["text"])):
                continue
            person = people.get(m["member_id"])
            result.append({"id":m["id"], "member_id":m["member_id"], "name":person.name if person else "Owner",
                           "role":person.role if person else "Owner", "text":m["text"], "attachments":m["attachments"]})
        return result

    def last_reply(self, origin):
        for row in self.conn.execute("SELECT * FROM room_messages WHERE origin_message_id = ? AND member_id IS NOT NULL ORDER BY id DESC", (origin,)):
            message = self.rooms._message(row)
            if message["status"] == "done" and not is_pass(message["text"]):
                return row["id"], row["member_id"]
        return None

    async def decide(self, room, exchange, phase, candidates):
        if not self.decision or not candidates or self.rooms._settings.get("agent_model").startswith("cli:"):
            return None
        if self.spend.is_over(self.rooms._settings.get("monthly_limit_usd")):
            return None
        ledger = Ledger(self.spend)
        try:
            answer = await self.decision.room_responder(
                transcript=self.transcript(room),
                members=[{"id":s.id, "name":s.name, "role":s.role, "personality":s.personality}
                         for s in self.rooms._members.roster() if s.id in candidates],
                phase=phase, model=self.rooms._settings.get("agent_model"), ledger=ledger,
            )
            confidence = answer.get("confidence", 0)
            choice = answer.get("choice")
            if type(confidence) in (int, float) and MIN_CONFIDENCE <= confidence <= 1 and (
                    choice in ("everyone", "none") or type(choice) is int and choice in candidates):
                return choice
        except Exception:
            # No key, a timeout or an unavailable router follows the saved room default.
            return None
        finally:
            current = self.get(exchange.origin_message_id)
            if current:
                self.update(current.origin_message_id, cost=current.cost + ledger.total)

    async def start(self, room, origin, language, attachments, recipient=None):
        # A new owner message takes priority after the current speaker, without replaying old work.
        with self.conn:
            self.conn.execute("UPDATE room_exchanges SET stop_requested = 1, pending = '[]', reason = 'interjection' "
                              "WHERE room_id = ? AND state IN ('queued','running')", (room["id"],))
            self.conn.execute("INSERT INTO room_exchanges (origin_message_id, room_id, state, language, pending) "
                              "VALUES (?, ?, 'queued', ?, ?)",
                              (origin, room["id"], language, json.dumps([{"member":recipient,"reply_to_member":recipient,
                               "reply_to":origin,"handoff":False}]) if recipient is not None else None))
        self.attachments[origin] = attachments
        await self.drive(room["id"])

    def arrived(self, turn_id):
        row = self.conn.execute("SELECT room_id FROM room_messages WHERE turn_id = ? AND origin_message_id IS NOT NULL", (turn_id,)).fetchone()
        if row and not self.closed:
            task = asyncio.create_task(self.drive(row[0]))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def wait_idle(self):
        while self.tasks:
            await asyncio.gather(*list(self.tasks))

    async def close(self):
        self.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def stop(self, room_id):
        with self.conn:
            self.conn.execute("UPDATE room_exchanges SET stop_requested = 1, pending = '[]', reason = 'owner' "
                              "WHERE room_id = ? AND state IN ('queued','running')", (room_id,))

    async def drive(self, room_id):
        async with self.locks.setdefault(room_id, asyncio.Lock()):
            while not self.closed:
                row = self.conn.execute("SELECT * FROM room_exchanges WHERE room_id = ? AND state IN ('queued','running') "
                                        "ORDER BY origin_message_id LIMIT 1", (room_id,)).fetchone()
                if row is None:
                    return
                exchange = Exchange(**dict(row))
                room_row = self.conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
                if room_row is None:
                    return
                room = dict(room_row)
                if exchange.active_turn:
                    turn = self.rooms._history.get(exchange.active_turn)
                    if turn and turn.status == "running":
                        return
                    message = self.conn.execute("SELECT * FROM room_messages WHERE turn_id = ?", (exchange.active_turn,)).fetchone()
                    if not message or not turn:
                        self.update(exchange.origin_message_id, state="stopped", active_turn=None, reason="missing_turn")
                        self.attachments.pop(exchange.origin_message_id, None)
                        continue
                    pending = json.loads(exchange.pending or "[]")
                    spoken = set(json.loads(exchange.spoken)) | {message["member_id"]}
                    with self.conn:
                        self.conn.execute("UPDATE room_messages SET exchange_settled = 1 WHERE id = ?", (message["id"],))
                    self.update(exchange.origin_message_id, active_turn=None, cost=exchange.cost + turn.cost_usd,
                                spoken=json.dumps(sorted(spoken)))
                    exchange = self.get(exchange.origin_message_id)
                    if turn.status != "done":
                        self.update(exchange.origin_message_id, state="stopped", reason="worker")
                        self.attachments.pop(exchange.origin_message_id, None)
                        self.rooms._changed(room_id)
                        continue
                    if message["kind"] == "wrapup":
                        self.update(exchange.origin_message_id, state="stopped" if exchange.stop_requested else "done")
                        self.attachments.pop(exchange.origin_message_id, None)
                        self.rooms._changed(room_id)
                        continue
                    if not is_pass(turn.detail or turn.text) and exchange.hops < MAX_HOPS:
                        handoffs = [i for i in self.rooms._targets(room, turn.detail or turn.text)
                                    if i != message["member_id"]]
                        pending = [{"member":i,"reply_to_member":message["member_id"],"reply_to":message["id"],"handoff":True}
                                   for i in handoffs] + pending
                    self.update(exchange.origin_message_id, pending=json.dumps(pending))
                exchange = self.get(exchange.origin_message_id)
                cap = float(self.rooms._settings.get("worker_cost_cap_usd"))
                if exchange.stop_requested or exchange.cost >= cap or self.spend.is_over(self.rooms._settings.get("monthly_limit_usd")):
                    self.update(exchange.origin_message_id, state="stopped", pending="[]",
                                reason=exchange.reason if exchange.stop_requested else "cost")
                    self.attachments.pop(exchange.origin_message_id, None)
                    self.rooms._changed(room_id)
                    continue
                ids = self.rooms._ids(room)
                spoken = set(json.loads(exchange.spoken))
                pending = json.loads(exchange.pending) if exchange.pending is not None else None
                if pending is None:
                    owner = self.conn.execute("SELECT text FROM room_messages WHERE id = ?", (exchange.origin_message_id,)).fetchone()[0]
                    targets = self.rooms._targets(room, owner)
                    if not targets:
                        choice = await self.decide(room, exchange, "route", ids)
                        targets = ids if choice == "everyone" else [choice] if type(choice) is int else self.fallback(room)
                    pending = [{"member":i,"reply_to_member":None,"reply_to":exchange.origin_message_id,"handoff":False} for i in targets]
                pending = [p for p in pending if p["member"] in ids and
                           (p["member"] not in spoken or p["handoff"]) and (not p["handoff"] or exchange.hops < MAX_HOPS)]
                if not pending and exchange.chime_ins < MAX_CHIME_INS and exchange.turns_taken < MAX_TURNS - 1:
                    candidates = [i for i in ids if i not in spoken]
                    choice = await self.decide(room, exchange, "chime", candidates)
                    if type(choice) is int and choice in candidates:
                        last = self.last_reply(exchange.origin_message_id)
                        pending = [{"member":choice,"reply_to_member":last[1] if last else None,
                                    "reply_to":last[0] if last else exchange.origin_message_id,"handoff":False}]
                        self.update(exchange.origin_message_id, chime_ins=exchange.chime_ins + 1)
                    else:
                        self.update(exchange.origin_message_id, chime_ins=MAX_CHIME_INS)
                # Any owner interjection/stop received during a routing call wins before submission.
                exchange = self.get(exchange.origin_message_id)
                if not exchange or exchange.stop_requested or exchange.cost >= cap:
                    continue
                wrapup = (exchange.turns_taken >= 3 and 0 in ids and not exchange.wrapup_sent and
                          (not pending or exchange.turns_taken == MAX_TURNS - 1))
                if wrapup:
                    last = self.last_reply(exchange.origin_message_id)
                    pending = [{"member":0,"reply_to_member":last[1] if last else None,
                                "reply_to":last[0] if last else exchange.origin_message_id,"handoff":False}]
                if not pending or exchange.turns_taken >= MAX_TURNS:
                    self.update(exchange.origin_message_id, state="done", pending="[]", reason="turns" if pending else "")
                    self.attachments.pop(exchange.origin_message_id, None)
                    self.rooms._changed(room_id)
                    continue
                target, remaining = pending[0], pending[1:]
                if target["member"] not in self.rooms._ids(room):
                    self.update(exchange.origin_message_id, state="stopped", pending="[]", reason="membership")
                    self.attachments.pop(exchange.origin_message_id, None)
                    self.rooms._changed(room_id)
                    continue
                self.update(exchange.origin_message_id, state="running", pending=json.dumps(remaining),
                            turns_taken=exchange.turns_taken + 1,
                            hops=exchange.hops + int(target["handoff"]), wrapup_sent=int(wrapup) or exchange.wrapup_sent)
                turn = self.rooms._submit_exchange_turn(room, exchange, target, "wrapup" if wrapup else "message",
                                                        self.attachments.get(exchange.origin_message_id, ()))
                self.update(exchange.origin_message_id, active_turn=turn)
                self.rooms._changed(room_id)
                # Refused submissions resolve immediately and must not leave a stuck exchange.
                if self.rooms._history.get(turn).status != "running":
                    continue
                return
