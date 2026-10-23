"""Tests for the two spaces of the memory: the shared one every assistant reads and writes (macro
knowledge about the owner), and each assistant's own; the move of the notes about the owner at the
upgrade, the RPC methods, and the reflection and the consolidation of each space."""

import pytest

from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.memory.reflection import Action, about_the_owner, apply_merge
from raphael_core.memory.service import MemoryService
from raphael_core.memory.store import MemoryIndex, MemoryStore
from raphael_core.protocol import RpcError
from raphael_core.router import Router

from .conftest import FakeModel

A, B = 7, 8  # two members of the team


@pytest.fixture
def keys():
    store = MemoryKeyStore()
    store.set("sk-or-v1-test")
    return store


@pytest.fixture
def make(conn, tmp_path, keys, clock):
    made = []

    def build(model=None):
        service = MemoryService(
            conn, tmp_path / "memory", model or FakeModel(), keys, now_ms=clock.ms,
            clock=clock.mono, now=clock.now, quiet_s=3600.0, start_delay_s=3600.0,
        )  # fmt: skip
        made.append(service)
        return service

    yield build


@pytest.fixture
def memory(make):
    return make()


@pytest.fixture
def members(conn):
    for member_id, name in ((A, "小行程"), (B, "小研究")):
        conn.execute(
            "INSERT INTO members (id, name, position, created_at_ms) VALUES (?, ?, ?, 1)",
            (member_id, name, member_id),
        )
    conn.commit()


def write(store, name, kind="user", text="主人住在台北"):
    store.write(name, type=kind, description=text, body=text)


def said(name, *, space, kind="user", text="主人住在台北", op="ADD"):
    return Action(op=op, name=name, type=kind, description=text, body=text, origin="said", space=space)


# --- the move at the upgrade -------------------------------------------------------------------


def test_the_notes_about_the_owner_move_to_the_shared_memory_once_as_they_are(make, tmp_path, clock):
    root = tmp_path / "memory"
    old = MemoryStore(root, now=clock.now)
    write(old, "user-city")
    write(old, "user-name", text="主人叫小明")
    write(old, "preference-short", kind="preference", text="回答要短")
    # A note edited by hand keeps its bytes.
    edited = root / "user-name.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "手改的一行\n", encoding="utf-8")
    hand = edited.read_bytes()
    # The shared memory already has a note of that name: that one stays, and so does Raphael's.
    taken = MemoryStore(root / "shared", now=clock.now)
    write(taken, "user-city", text="主人住在高雄")

    memory = make()
    assert [m.name for m in memory.shared.all()] == ["user-city", "user-name"]
    assert memory.shared.get("user-city").description == "主人住在高雄"
    assert (root / "shared" / "user-name.md").read_bytes() == hand
    assert sorted(m.name for m in memory.store.all()) == ["preference-short", "user-city"]
    assert "user-name" in (root / "shared" / "MEMORY.md").read_text(encoding="utf-8")

    # Once: a note about the owner written to Raphael's own later stays there.
    write(memory.store, "user-job", text="主人是工程師")
    again = make()
    assert again.store.get("user-job") is not None and again.shared.get("user-job") is None


# --- reading and writing -----------------------------------------------------------------------


def test_the_prompt_has_the_shared_index_first_then_the_own(memory, members):
    write(memory.shared, "user-city")
    write(memory.store_for(A), "preference-format", kind="preference", text="行程用條列")
    index = memory.index(A)
    assert isinstance(index, MemoryIndex)
    assert "主人住在台北" in index.shared and "行程用條列" in index.own
    text = str(index)
    assert text.index("<shared_memory>") < text.index("<own_memory>")
    assert "行程用條列" not in str(memory.index(0))


def test_a_note_goes_to_the_space_it_names(memory, members):
    applied = memory.apply_said(
        [said("user-city", space="shared"), said("preference-format", space="own", kind="preference", text="行程用條列")],
        5,
        A,
    )
    assert sorted(applied.written) == ["preference-format", "user-city"]
    assert memory.shared.get("user-city").turns == (5,)
    assert memory.store_for(A).get("preference-format") is not None
    assert memory.store.all() == []  # Raphael's own is untouched


@pytest.mark.parametrize(
    "action",
    [
        said("ongoing-trip", space="shared", kind="ongoing", text="下週去東京"),  # a plan is one thread's
        said("user-plan", space="shared", text="我們決定下週三交稿"),  # what was decided
        said("preference-meeting", space="shared", kind="preference", text="We discussed the budget"),
    ],
)
def test_a_note_that_is_not_about_the_owner_at_large_stays_with_its_assistant(memory, members, action):
    if action.type == "ongoing":
        action = Action(**{**action.__dict__, "expires": "2026-11-01"})
    assert not about_the_owner(action)
    applied = memory.apply_said([action], 1, A)
    assert applied.written == [action.name]
    assert memory.shared.all() == []
    assert memory.store_for(A).get(action.name) is not None


def test_a_note_is_deleted_from_the_space_it_names(memory, members):
    write(memory.shared, "user-city")
    write(memory.store_for(A), "user-city", text="主人在高雄出差")
    memory.apply_said([said("user-city", space="shared", op="DELETE")], 1, A)
    assert memory.shared.get("user-city") is None
    assert memory.store_for(A).get("user-city") is not None


def test_a_member_never_reads_another_members_own_memory(memory, members):
    write(memory.store_for(A), "preference-secret", kind="preference", text="A 的工作筆記")
    write(memory.shared, "user-city")
    result = memory.read(["preference-secret", "user-city"], B)
    assert [m["name"] for m in result.data["memories"]] == ["user-city"]
    assert result.data["memories"][0]["space"] == "shared"
    assert result.data["missing"] == ["preference-secret"]
    assert "A 的工作筆記" not in str(memory.index(B))
    assert "A 的工作筆記" not in str(memory.reader_for(B).index())


# --- the RPC methods ---------------------------------------------------------------------------


@pytest.fixture
def rpc(memory):
    router = Router()
    memory.register(router)
    return router


async def test_the_methods_take_the_space_and_the_member(rpc, memory, members):
    write(memory.shared, "user-city")
    write(memory.store_for(A), "preference-format", kind="preference", text="行程用條列")
    shared = await rpc.call("memory.list", {"space": "shared"})
    assert [m["name"] for m in shared["memories"]] == ["user-city"] and shared["summary"] is None
    own = await rpc.call("memory.list", {"member_id": A})
    assert [m["name"] for m in own["memories"]] == ["preference-format"]
    assert (await rpc.call("memory.list", {}))["memories"] == []  # Raphael's own
    got = await rpc.call("memory.get", {"name": "user-city", "space": "shared"})
    assert got["description"] == "主人住在台北"
    updated = await rpc.call("memory.update", {"name": "preference-format", "member_id": A, "body": "行程一律用條列。"})
    assert updated["body"] == "行程一律用條列。"
    assert await rpc.call("memory.delete", {"name": "user-city", "space": "shared"}) == {"deleted": True}
    with pytest.raises(RpcError):
        await rpc.call("memory.list", {"space": "everyone"})


async def test_a_note_is_moved_between_the_shared_memory_and_an_assistants_own(rpc, memory, members):
    write(memory.store_for(A), "user-job", text="主人是工程師")
    assert await rpc.call("memory.move", {"name": "user-job", "to_space": "shared", "member_id": A}) == {"moved": True}
    assert memory.shared.get("user-job") is not None and memory.store_for(A).get("user-job") is None
    assert await rpc.call("memory.move", {"name": "user-job", "to_space": "own", "member_id": B}) == {"moved": True}
    assert memory.store_for(B).get("user-job") is not None
    write(memory.shared, "user-job", text="主人是設計師")
    with pytest.raises(RpcError):  # never over a note of the same name
        await rpc.call("memory.move", {"name": "user-job", "to_space": "shared", "member_id": B})
    with pytest.raises(RpcError):
        await rpc.call("memory.move", {"name": "user-none", "to_space": "shared"})


# --- reflection and consolidation, per space ------------------------------------------------------


def actions(*items):
    return {"actions": list(items)}


def item(name, *, space, kind="user", text="主人是工程師", op="ADD"):
    return {
        "op": op, "name": name, "type": kind, "description": text, "body": text, "expires": "",
        "origin": "inferred", "space": space,
    }  # fmt: skip


async def test_each_assistant_reflects_on_its_own_turns_into_its_own_memory_and_the_shared(
    make, members, history
):
    model = FakeModel(
        actions(
            item("user-job", space="shared"),
            item("preference-format", space="own", kind="preference", text="行程用條列"),
            # A note about a conversation is not shared: it is kept to the member.
            item("user-deadline", space="shared", text="我們決定下週三交稿"),
        )
    )
    memory = make(model)
    history.add(session_id="s", question="我是工程師，行程用條列", route="chat", prefix="", text="好", status="done", member_id=A)
    history.add(session_id="s", question="PRIVATE-B 的話", route="chat", prefix="", text="好", status="done", member_id=B)
    history.add(session_id="s", question="PRIVATE-RAPHAEL 的話", route="chat", prefix="答", text="好", status="done")
    applied = await memory.reflect(A)
    assert sorted(applied.written) == ["preference-format", "user-deadline", "user-job"]
    (call,) = model.calls
    assert "我是工程師" in call["user"]
    assert "PRIVATE-B" not in call["user"] and "PRIVATE-RAPHAEL" not in call["user"]
    assert "小行程" in call["system"]  # the pass is for this member
    assert [m.name for m in memory.shared.all()] == ["user-job"]
    assert sorted(m.name for m in memory.store_for(A).all()) == ["preference-format", "user-deadline"]
    assert memory.reflected_through(A) > 0 and memory.reflected_through(B) == 0
    assert memory.reflected_through(0) == 0


async def test_the_shared_consolidation_never_deletes_a_note(make):
    memory = make()
    write(memory.shared, "user-city")
    write(memory.shared, "user-home", text="主人家在台北")
    since = memory.shared.mark()
    merged = apply_merge(
        [Action("UPDATE", "user-city", "user", "主人住在台北", "主人住在台北的家", origin="said"),
         Action("DELETE", "user-home")],
        memory.shared,
        since=since,
        deletes=False,
    )  # fmt: skip
    assert merged.written == ["user-city"] and merged.deleted == [] and merged.refused == ["origin"]
    assert memory.shared.get("user-home") is not None


async def test_the_weekly_tidy_runs_for_each_space(make, members):
    model = FakeModel(actions(), actions(), actions())
    memory = make(model)
    write(memory.store, "user-a", text="一")
    write(memory.store, "user-b", text="二")
    write(memory.shared, "user-c", text="三")
    write(memory.shared, "user-d", text="四")
    write(memory.store_for(A), "user-e", text="五")
    write(memory.store_for(A), "user-f", text="六")
    await memory.consolidate()
    assert len(model.calls) == 3  # Raphael's own, the shared, the member's own
