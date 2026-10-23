"""Fixtures for the members' tests: the members' service on the suite's database `conn`, with a
background worker that notes what it is handed, a real memory and a local folder of its own."""

from datetime import datetime, timedelta, timezone

import pytest

from raphael_core.background.tasks import Refused, TaskStore
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.members.service import MembersService
from raphael_core.memory.service import MemoryService
from raphael_core.router import Router as RpcRouter

TZ = timezone(timedelta(hours=8))
# 2026-10-23 08:59:00 in Taipei: a minute before a routine at nine.
NOW_MS = int(datetime(2026, 10, 23, 8, 59, tzinfo=TZ).timestamp() * 1000)


class Wall:
    def __init__(self, now=NOW_MS):
        self.now = now

    def __call__(self):
        return self.now


class FakeBackground:
    """The background worker as the members' service uses it: what it was handed, and the tasks
    written down as the real one writes them (so that a turn can be tied to one)."""

    def __init__(self, conn):
        self.store = TaskStore(conn)
        self.submitted = []
        self.attached = []
        self.forgotten = []
        self.refuse = None

    def submit(self, *, session_id, text, language, model, member_id=0, routine_id=None, **_):
        if self.refuse:
            raise Refused(self.refuse)
        self.submitted.append(
            {"session_id": session_id, "text": text, "language": language, "model": model,
             "member_id": member_id, "routine_id": routine_id}
        )  # fmt: skip
        return self.store.create(
            session_id=session_id, language=language, text=text, member_id=member_id,
            routine_id=routine_id,
        )  # fmt: skip

    def attach_turn(self, task_id, turn_id):
        self.attached.append((task_id, turn_id))
        self.store.attach_turn(task_id, turn_id)

    def forget_member(self, member_id):
        self.forgotten.append(member_id)


class QuietModel:
    async def complete_json(self, **kwargs):
        raise AssertionError("the memory's model was asked")


class Rig:
    def __init__(self, conn, tmp_path):
        self.conn = conn
        self.local = tmp_path / "local"
        self.wall = Wall()
        self.notes = []
        keys = MemoryKeyStore()
        keys.set("sk-or-v1-test")
        self.memory = MemoryService(
            conn, self.local / "memory", QuietModel(), keys, now_ms=self.wall,
            quiet_s=3600.0, start_delay_s=3600.0,
            notify=lambda method, params: self.notes.append((method, params)),
        )  # fmt: skip
        self.background = FakeBackground(conn)
        self.language = "zh-TW"
        self.service = MembersService(
            conn,
            self.local,
            background=self.background,
            memory=self.memory,
            language=lambda: self.language,
            notify=lambda method, params: self.notes.append((method, params)),
            connections=lambda: [{"id": "srv-1", "name": "GitHub", "enabled": True}],
            now_ms=self.wall,
            tz=TZ,
        )
        self.memory.member_exists = self.service.member_exists
        self.rpc = RpcRouter()
        self.service.register(self.rpc)
        self.memory.register(self.rpc)

    async def call(self, method, params=None):
        return await self.rpc.call(method, params or {})

    async def close(self):
        await self.service.aclose()
        await self.memory.aclose()


@pytest.fixture
async def rig(conn, tmp_path):
    made = Rig(conn, tmp_path)
    yield made
    await made.close()
