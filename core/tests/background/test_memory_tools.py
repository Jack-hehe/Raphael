"""Tests for what a background task may do with Raphael's memory: read a note and search the past
turns, through the gate, and never write; and the index it is shown."""

import json

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.background.service import _prompts
from raphael_core.background.run import RunLimits
from raphael_core.background.snapshot import BUILTIN, take_snapshot
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.memory.service import MemoryService

from .test_run import Rig


class NoModel:
    async def complete_json(self, **kwargs):
        raise AssertionError("no model call is expected")


@pytest.fixture
def memory(conn, tmp_path):
    service = MemoryService(conn, tmp_path / "memory", NoModel(), MemoryKeyStore())
    service.store.write("person-amy", type="person", description="Amy 是同事", body="Amy 負責設計。")
    return service


@pytest.fixture
async def rig(conn, plugins, memory):
    made = Rig(conn, plugins, snapshot=take_snapshot(plugins.hub, memory=True), memory=memory)
    yield made
    await made.meteo.http.aclose()


def test_a_task_is_offered_the_memory_tools_that_read_and_none_that_write():
    names = [d["function"]["name"] for d in take_snapshot(None, memory=True).definitions]
    assert names == [
        "weather", "web_search", "local_time", "web_read", "memory_read", "history_search",
    ]  # fmt: skip
    assert "memory_write" not in names and "memory_delete" not in names
    assert "memory_read" not in [d["function"]["name"] for d in take_snapshot(None).definitions]
    refs = take_snapshot(None, memory=True).refs
    assert refs["memory_read"].kind == BUILTIN and refs["memory_read"].tier.name == "T0"


async def test_a_task_reads_a_note_through_the_gate_and_the_audit(rig):
    reply = await rig.tool("memory_read", {"names": ["person-amy"]})
    assert reply["ok"]
    assert json.loads(reply["content"])["memories"][0]["body"] == "Amy 負責設計。"
    assert rig.audit() == [("Raphael", "memory_read", "T0", "auto", "ok", rig.task_id)]


async def test_a_task_searches_the_past_turns(rig, conn):
    AgentHistory(conn).add(session_id="s", question="上次的會議", route="chat", prefix="答", text="三樓。", status="done")
    reply = await rig.tool("history_search", {"query": "會議"})
    assert reply["ok"] and "三樓。" in reply["content"]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [("memory_read", {}), ("memory_read", {"names": "person-amy"}), ("history_search", {"query": " "})],
)
async def test_a_memory_lookup_with_bad_arguments_fails_with_its_kind(rig, name, arguments):
    reply = await rig.tool(name, arguments)
    assert (reply["ok"], reply["kind"]) == (False, "bad_arguments")


async def test_a_task_may_not_write_a_note(rig, memory):
    reply = await rig.tool("memory_write", {"name": "user-x", "body": "x"})
    assert reply["ok"] is False
    assert [m.name for m in memory.store.all()] == ["person-amy"]


def test_the_task_is_shown_the_index_of_the_notes_as_data():
    moment = {"date": "2026-10-23", "time": "09:00", "weekday": "Friday", "utc_offset": "UTC+08:00"}
    _, user = _prompts("en", "整理 Amy 的設計稿", moment, [], RunLimits(), memory_index="- [person] Amy 是同事 (10/22) → person-amy")
    assert "<memory_index>\n- [person] Amy 是同事 (10/22) → person-amy\n</memory_index>" in user
    assert user.index("<memory_index>") < user.index("<task>")
    _, without = _prompts("en", "x", moment, [], RunLimits())
    assert "memory_index" not in without
