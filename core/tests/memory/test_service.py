"""Tests for the memory as a service: the RPC methods of the settings page, the tools of the
assistant, and when the reflection pass and the consolidation run."""

import asyncio
import logging

import pytest

from raphael_core.brain.openrouter import OpenRouterError
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.settings import SettingsStore
from raphael_core.memory.reflection import Action
from raphael_core.memory.service import CONSOLIDATE_EVERY_MS, REFLECT_AFTER_TURNS, MemoryService
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router

from .conftest import FakeModel, add_turn

SECRET_WORDS = "主人喜歡烏龍茶"


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def keys():
    store = MemoryKeyStore()
    store.set("sk-or-v1-test")
    return store


@pytest.fixture
async def memory(conn, tmp_path, model, keys, clock):
    service = MemoryService(
        conn, tmp_path / "memory", model, keys, now_ms=clock.ms, clock=clock.mono, now=clock.now,
        quiet_s=0.05, start_delay_s=0.0,
    )  # fmt: skip
    yield service
    await service.aclose()


@pytest.fixture
def rpc(memory):
    router = Router()
    memory.register(router)
    return router


def memory_item(name="user-tea", **fields):
    values = {
        "op": "ADD", "name": name, "type": "user", "description": SECRET_WORDS, "body": "烏龍茶。",
        "expires": "", "origin": "said",
    }  # fmt: skip
    return {**values, **fields}


# --- the RPC methods ------------------------------------------------------------------------------


async def test_the_six_methods_are_registered(rpc):
    for method in ("memory.list", "memory.get", "memory.update", "memory.delete", "memory.clear", "memory.summary_clear"):
        assert method in rpc.methods(), method


async def test_list_get_update_and_delete(rpc, memory):
    memory.store.write("user-tea", type="user", description=SECRET_WORDS, body="烏龍茶。", origin="inferred")
    listed = await rpc.call("memory.list", {})
    assert listed["summary"] is None
    assert listed["memories"] == [
        {"name": "user-tea", "description": SECRET_WORDS, "type": "user", "origin": "inferred",
         "updated": "2026-10-22T21:40:00+08:00", "expires": None}
    ]  # fmt: skip
    got = await rpc.call("memory.get", {"name": "user-tea"})
    assert got["body"] == "烏龍茶。" and got["turns"] == []
    updated = await rpc.call("memory.update", {"name": "user-tea", "body": "主人只喝無糖烏龍茶。"})
    assert updated["body"] == "主人只喝無糖烏龍茶。" and updated["origin"] == "said"  # the owner edited it
    assert updated["description"] == SECRET_WORDS
    renamed = await rpc.call("memory.update", {"name": "user-tea", "body": "x", "description": "主人只喝茶"})
    assert renamed["description"] == "主人只喝茶"
    assert await rpc.call("memory.delete", {"name": "user-tea"}) == {"deleted": True}
    assert await rpc.call("memory.delete", {"name": "user-tea"}) == {"deleted": False}
    assert not (memory.store.root / "user-tea.md").exists()


async def test_clear_deletes_every_memory_and_summary_clear_the_summary(rpc, memory):
    memory.store.write("user-a", type="user", description="一", body="x")
    memory.store.write("user-b", type="user", description="二", body="x")
    memory.conversation._save("Context:\n...", 3)
    assert (await rpc.call("memory.list", {}))["summary"]["text"] == "Context:\n..."
    assert await rpc.call("memory.clear", {}) == {"deleted": 2}
    assert await rpc.call("memory.summary_clear", {}) == {"cleared": True}
    assert await rpc.call("memory.list", {}) == {"memories": [], "summary": None}


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("memory.get", {}),
        ("memory.get", {"name": "../raphael"}),
        ("memory.get", {"name": "user-none"}),
        ("memory.delete", {"name": 3}),
        ("memory.update", {"name": "user-tea"}),
        ("memory.update", {"name": "user-tea", "body": 5}),
        ("memory.update", {"name": "user-tea", "body": "x", "description": 5}),
        ("memory.update", {"name": "user-none", "body": "x"}),
        ("memory.update", {"name": "user-tea", "body": "Wi-Fi 密碼是 hunter22"}),
        ("memory.update", {"name": "user-tea", "body": "長" * 1201}),
    ],
)
async def test_the_methods_check_their_params(rpc, memory, method, params):
    memory.store.write("user-tea", type="user", description=SECRET_WORDS, body="烏龍茶。")
    with pytest.raises(RpcError) as error:
        await rpc.call(method, params)
    assert error.value.code == INVALID_PARAMS
    assert "hunter22" not in str(error.value)
    assert memory.store.get("user-tea").body == "烏龍茶。"  # a refused edit changes nothing


# --- the tools of the assistant ------------------------------------------------------------------


def test_read_gives_the_notes_and_names_the_missing(memory):
    memory.store.write("user-tea", type="user", description=SECRET_WORDS, body="烏龍茶。")
    result = memory.read(["user-tea", "user-none", "../x", "user-tea"])
    assert result.ok and result.name == "memory_read"
    assert result.data == {
        "memories": [{"name": "user-tea", "description": SECRET_WORDS, "body": "烏龍茶。", "updated": "2026-10-22", "origin": "said", "space": "own"}],
        "missing": ["user-none", "../x"],
    }  # fmt: skip


def test_search_gives_the_turns_with_their_dates_cut_short(memory, history):
    old = add_turn(history, "上次的會議在哪裡", "在三樓的會議室。")
    add_turn(history, "晚餐吃什麼", "拉麵。")
    long = add_turn(history, "長的會議", "會議" + "很長" * 200)
    result = memory.search("會議")
    assert result.ok and result.name == "history_search"
    found = {t["turn"]: t for t in result.data["turns"]}
    assert set(found) == {old, long}
    assert found[old]["when"].startswith("2026-10-22") and found[old]["answer"] == "在三樓的會議室。"
    assert len(found[long]["answer"]) <= 152
    assert memory.search("會議", exclude=[old]).data["turns"][0]["turn"] == long


def test_a_search_that_finds_a_run_on_the_desktop_is_untrusted(memory, history):
    # Raphael's line of a run tells what the screen showed: the reply that reads it writes no note.
    add_turn(history, "會議在哪裡", "在三樓。")
    assert not memory.search("會議").untrusted
    add_turn(history, "打開會議通知", "畫面寫著會議改到五樓。", route="operate")
    found = memory.search("會議")
    assert found.ok and len(found.data["turns"]) == 2 and found.untrusted


def test_search_finds_an_older_turn_behind_the_recent_ones_it_leaves_out(memory, history):
    # The turns already in the context are left out; twelve of them match best (the newest come
    # first), more than the most a search gives, and the older match must still be found.
    old = add_turn(history, "上次的會議在哪裡", "在三樓。")
    recent = [add_turn(history, f"會議 {n}", "好。") for n in range(12)]
    found = memory.search("會議", exclude=recent).data["turns"]
    assert [t["turn"] for t in found] == [old]


def test_what_the_owner_said_is_written_as_said_and_its_turn_is_noted(memory):
    applied = memory.apply_said([Action("ADD", "user-tea", "user", SECRET_WORDS, "烏龍茶。", None, "inferred")], 0)
    assert applied.written == ["user-tea"] and memory.store.get("user-tea").origin == "said"
    memory.note_turn(applied.written, 42)
    assert memory.store.get("user-tea").turns == (42,)


# --- when the background runs --------------------------------------------------------------------


async def test_the_pass_runs_after_ten_turns_and_moves_its_watermark(memory, history, model, conn):
    model.replies.append({"actions": [memory_item()]})
    ids = [add_turn(history, f"q{n}") for n in range(REFLECT_AFTER_TURNS)]
    memory.after_turn()
    await memory.wait_idle()
    assert memory.store.get("user-tea") is not None
    assert memory.reflected_through() == ids[-1]
    assert [tuple(r) for r in conn.execute("SELECT purpose FROM spend")] == [("memory",)]
    assert memory.reflection_due() is False


async def test_the_pass_runs_once_the_conversation_is_quiet(memory, history, model, clock):
    add_turn(history, "我喜歡烏龍茶")
    model.replies.append({"actions": [memory_item()]})
    memory.after_turn()  # arms the quiet timer
    assert memory.reflection_due() is False
    clock.advance(seconds=1)
    assert memory.reflection_due() is True
    for _ in range(200):
        await asyncio.sleep(0.01)
        if memory.store.get("user-tea") is not None:
            break
    await memory.wait_idle()
    assert memory.store.get("user-tea") is not None


async def test_a_pass_that_failed_leaves_the_turns_for_later(memory, history, model, clock):
    add_turn(history, "q")
    clock.advance(seconds=1)
    model.replies.append({"bad": 1})
    model.replies.append({"bad": 2})
    await memory.run_due()
    assert memory.reflected_through() == 0
    memory._spawn()
    assert memory._running is None  # waits before it tries again


def armed_in(memory):
    """In how many seconds the background's timer fires, or None when none is armed."""
    if memory._timer is None:
        return None
    return memory._timer.when() - asyncio.get_running_loop().time()


async def test_a_turn_waiting_at_startup_is_read_once_it_is_quiet_with_no_new_turn(memory, history, model, clock):
    # One turn waits, and it is not quiet yet when the startup's look comes: the look arms the
    # timer for when it will be, and the pass runs then, though the owner says nothing more.
    add_turn(history, "我喜歡烏龍茶")
    memory.start()
    assert armed_in(memory) is not None
    memory._tick()  # the startup's look: nothing is due yet
    assert memory._running is None
    assert armed_in(memory) == pytest.approx(0.05 + 1.0, abs=0.2)
    clock.advance(seconds=1)
    model.replies.append({"actions": [memory_item()]})
    memory._tick()  # the quiet time has come
    await memory.wait_idle()
    assert memory.store.get("user-tea") is not None and memory.reflection_due() is False


async def test_a_pass_that_failed_is_tried_again_after_its_pause(memory, history, model, clock):
    from raphael_core.memory.service import RETRY_S

    add_turn(history, "我喜歡烏龍茶")
    clock.advance(seconds=1)
    model.replies.append(OpenRouterError(503, "server", "down"))
    await memory.run_due()
    assert memory.reflected_through() == 0
    assert armed_in(memory) == pytest.approx(RETRY_S + 1.0, abs=1.0)
    memory._spawn()  # a turn asks for a pass meanwhile: it waits out the pause, armed for its end
    assert memory._running is None
    assert armed_in(memory) == pytest.approx(RETRY_S + 1.0, abs=1.0)
    clock.advance(seconds=RETRY_S)
    model.replies.append({"actions": [memory_item()]})
    memory._tick()
    await memory.wait_idle()
    assert memory.store.get("user-tea") is not None


async def test_without_a_key_the_background_looks_again_later_not_at_once(memory, history, model, keys, clock):
    from raphael_core.memory.service import RETRY_S

    add_turn(history, "q")
    clock.advance(seconds=1)
    keys.clear()
    await memory.run_due()
    assert model.calls == [] and armed_in(memory) >= RETRY_S


async def test_nothing_runs_without_a_key_or_over_the_limit(memory, history, model, keys, conn, clock):
    for n in range(REFLECT_AFTER_TURNS):
        add_turn(history, f"q{n}")
    keys.clear()
    await memory.run_due()
    keys.set("sk-or-v1-test")
    SettingsStore(conn).set("monthly_limit_usd", 0.0)
    await memory.run_due()
    assert model.calls == []


async def test_the_consolidation_runs_weekly(memory, model, clock):
    memory.store.write("user-a", type="user", description="一", body="x")
    memory.store.write("user-b", type="user", description="二", body="x")
    assert memory.consolidation_due() is True
    model.replies.append({"actions": []})
    await memory.run_due()
    assert memory.consolidation_due() is False
    clock.advance(milliseconds=CONSOLIDATE_EVERY_MS)
    assert memory.consolidation_due() is True


async def test_clearing_the_conversation_starts_the_pass_again_from_the_first_turn(memory, history, model):
    model.replies.append({"actions": []})
    for n in range(REFLECT_AFTER_TURNS):
        add_turn(history, f"q{n}")
    await memory.reflect()
    assert memory.reflected_through() > 0
    history.clear()
    memory.forget_turns()
    assert memory.reflected_through() == 0


async def test_a_pass_that_waited_while_the_conversation_was_cleared_moves_nothing(memory, history, model):
    for n in range(REFLECT_AFTER_TURNS):
        add_turn(history, f"q{n}")

    def clear_while_waiting(**_):
        history.clear()
        memory.forget_turns()
        add_turn(history, "新的對話")  # turn 1 again
        return {"actions": [memory_item()]}

    model.replies.append(clear_while_waiting)
    await memory.reflect()
    # The new conversation is still to be read, and nothing came of the one that was cleared.
    assert memory.reflected_through() == 0 and memory.store.all() == []


async def test_a_pass_that_waited_while_the_memory_was_cleared_writes_nothing(rpc, memory, history, model):
    memory.store.write("user-city", type="user", description="主人住在台北", body="x")
    for n in range(REFLECT_AFTER_TURNS):
        add_turn(history, f"q{n}")

    async def clear_while_waiting(**_):
        await rpc.call("memory.clear", {})
        return {"actions": [memory_item(), memory_item(name="user-city", op="UPDATE")]}

    model.replies.append(clear_while_waiting)
    applied = await memory.reflect()
    assert applied.changed == 0 and memory.store.all() == []


async def test_the_memory_logs_no_memory_and_no_turn(memory, history, model, caplog):
    for n in range(REFLECT_AFTER_TURNS):
        add_turn(history, f"秘密的問題 {n}")
    model.replies.append({"actions": [memory_item()]})
    with caplog.at_level(logging.DEBUG):
        await memory.reflect()
        memory.read(["user-tea"])
        memory.search("秘密")
    assert "秘密" not in caplog.text and SECRET_WORDS not in caplog.text and "烏龍" not in caplog.text
