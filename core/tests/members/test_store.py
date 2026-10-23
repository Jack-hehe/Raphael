"""Tests for the members' tables: groups, members, routines and the unread marks."""

import pytest

from raphael_core.agent.speaker import RAPHAEL_ID
from raphael_core.members.schedule import Schedule
from raphael_core.members.store import (
    INSTRUCTIONS_MAX,
    MAX_ROUTINES,
    MemberError,
    MemberStore,
    Source,
)


@pytest.fixture
def store(conn):
    return MemberStore(conn, now_ms=lambda: 5_000)


def kind_of(call):
    with pytest.raises(MemberError) as caught:
        call()
    return caught.value.kind


# --- groups ----------------------------------------------------------------------------------------


def test_groups_are_made_in_order_renamed_moved_and_given_sources(store):
    work = store.create_group(" 工作  專案 ")
    home = store.create_group("家")
    assert (work.name, work.position, home.position) == ("工作 專案", 0, 1)
    moved = store.update_group(
        home.id,
        position=0,
        sources=[
            {"kind": "github", "ref": "https://github.com/octo/repo.git"},
            {"kind": "github", "ref": "octo/repo"},  # the same one again: kept once
            {"kind": "notion", "ref": "0123456789ABCDEF0123456789abcdef"},
            {"kind": "notion", "ref": "https://www.notion.so/team/Plan-0123456789abcdef"},
        ],
    )
    assert moved.sources == (
        Source("github", "octo/repo"),
        Source("notion", "0123456789abcdef0123456789abcdef"),
        Source("notion", "https://www.notion.so/team/Plan-0123456789abcdef"),
    )
    assert [g.id for g in store.groups()] == [home.id, work.id]  # by position, then id
    assert store.update_group(work.id, name="Work").name == "Work"
    assert store.group_by_name(" work ").id == work.id


@pytest.mark.parametrize(
    "sources",
    [
        "octo/repo",
        [{"kind": "gitlab", "ref": "a/b"}],
        [{"kind": "github", "ref": "not a repo"}],
        [{"kind": "github", "ref": ""}],
        [{"kind": "notion", "ref": "https://evil.example/page"}],
        [{"kind": "notion", "ref": "1234"}],
        [{"kind": "github", "ref": f"o/r{n}"} for n in range(11)],
    ],
)
def test_a_source_that_is_not_a_repository_or_a_notion_page_is_refused(store, sources):
    group = store.create_group("g")
    assert kind_of(lambda: store.update_group(group.id, sources=sources)) == "sources"
    assert store.group(group.id).sources == ()


def test_a_group_name_is_checked(store):
    assert kind_of(lambda: store.create_group("")) == "name"
    assert kind_of(lambda: store.create_group("x" * 25)) == "name"
    assert kind_of(lambda: store.create_group("a\x00b")) == "name"
    assert kind_of(lambda: store.update_group(99, name="x")) == "not_found"


def test_deleting_a_group_moves_its_members_to_no_group(store):
    group = store.create_group("g")
    member = store.create_member(name="小行程", group_id=group.id)
    assert store.delete_group(group.id) is True
    assert store.member(member.id).group_id is None
    assert store.delete_group(group.id) is False


# --- members ---------------------------------------------------------------------------------------


def test_a_member_is_made_with_what_the_owner_wrote_and_read_back(store):
    group = store.create_group("g")
    member = store.create_member(
        name="  小行程 ",
        role="管行程",
        instructions="  每天早上提醒我。\n\n用條列。  ",
        group_id=group.id,
        avatar="preset:chibi-01",
        tools=["web_search", "server-1", "web_search"],
        model="openai/gpt-6-luna",
    )
    assert member.id > RAPHAEL_ID
    assert (member.name, member.role, member.instructions) == ("小行程", "管行程", "每天早上提醒我。\n\n用條列。")
    assert (member.group_id, member.avatar, member.model) == (group.id, "preset:chibi-01", "openai/gpt-6-luna")
    assert member.tools == ("web_search", "server-1")
    assert member.created_at_ms == 5_000
    assert store.by_name("小行程").id == member.id
    speaker = member.speaker()
    assert (speaker.id, speaker.name, speaker.tools) == (member.id, "小行程", frozenset({"web_search", "server-1"}))


def test_a_member_with_no_list_of_tools_has_every_tool_that_is_on(store):
    member = store.create_member(name="A")
    assert member.tools is None
    assert member.speaker().tools is None
    assert store.update_member(member.id, tools=[]).tools == ()
    assert store.update_member(member.id, tools=None).tools is None


def test_names_are_unique_whatever_the_case_and_none_is_raphaels(store):
    store.create_member(name="Ryo")
    assert kind_of(lambda: store.create_member(name="ryo")) == "taken"
    for name in ("拉斐爾", "Raphael", "raphael", "ラファエル"):
        assert kind_of(lambda name=name: store.create_member(name=name)) == "taken"
    other = store.create_member(name="Ken")
    assert kind_of(lambda: store.update_member(other.id, name="RYO")) == "taken"
    assert store.update_member(other.id, name="ken").name == "ken"  # its own name, cased anew


@pytest.mark.parametrize(
    ("fields", "kind"),
    [
        ({"name": ""}, "name"),
        ({"name": "x" * 17}, "name"),
        ({"name": "a", "role": "x" * 25}, "role"),
        ({"name": "a", "instructions": "x" * (INSTRUCTIONS_MAX + 1)}, "instructions"),
        ({"name": "a", "group_id": 42}, "group"),
        ({"name": "a", "group_id": "1"}, "group"),
        ({"name": "a", "avatar": "C:\\Users\\me\\a.png"}, "avatar"),
        ({"name": "a", "avatar": "file:../a.png"}, "avatar"),
        ({"name": "a", "tools": "web_search"}, "tools"),
        ({"name": "a", "tools": [5]}, "tools"),
        ({"name": "a", "model": 5}, "model"),
    ],
)
def test_what_a_member_cannot_be_is_refused(store, fields, kind):
    assert kind_of(lambda: store.create_member(**fields)) == kind
    assert store.members() == []


def test_a_member_is_updated_field_by_field(store):
    member = store.create_member(name="A", role="r", instructions="i")
    updated = store.update_member(member.id, role="新的", position=7)
    # A place past the end is the last place: the places are numbered again from 0.
    assert (updated.name, updated.role, updated.instructions, updated.position) == ("A", "新的", "i", 0)
    second = store.create_member(name="B")
    assert store.update_member(second.id, position=0).position == 0
    assert [m.name for m in store.members()] == ["B", "A"]
    assert kind_of(lambda: store.update_member(member.id, created_at_ms=1)) == "created_at_ms"
    assert kind_of(lambda: store.update_member(99, role="x")) == "not_found"


def test_deleting_a_member_takes_its_routines_and_its_unread_mark(store):
    member = store.create_member(name="A")
    store.create_routine(
        member_id=member.id, title="t", prompt="p", schedule={"kind": "every", "hours": 1},
        next_run_ms=10,
    )  # fmt: skip
    store.mark_unread(member.id)
    assert store.delete_member(member.id) is True
    assert store.routines(member.id) == []
    assert store.unread() == set()
    assert store.delete_member(member.id) is False


def test_the_speaker_of_zero_is_raphael_and_of_nobody_none(store):
    assert store.speaker(0).is_raphael
    assert store.speaker(42) is None
    assert store.exists(0) and not store.exists(42)


# --- routines --------------------------------------------------------------------------------------


def test_a_routine_is_made_updated_and_deleted(store):
    member = store.create_member(name="A")
    routine = store.create_routine(
        member_id=member.id,
        title="每天早上的進度",
        prompt="看進度。",
        schedule={"kind": "daily", "time": "09:00"},
        next_run_ms=100,
    )
    assert (routine.title, routine.schedule, routine.enabled, routine.next_run_ms) == (
        "每天早上的進度",
        Schedule("daily", time="09:00"),
        True,
        100,
    )
    # Switched off: never due; switched on again: due when the schedule says.
    off = store.update_routine(routine.id, enabled=False, next_run=lambda schedule: 999)
    assert (off.enabled, off.next_run_ms) == (False, None)
    on = store.update_routine(routine.id, enabled=True, next_run=lambda schedule: 555)
    assert (on.enabled, on.next_run_ms) == (True, 555)
    # A new schedule is due anew; a new title alone leaves the time it is due.
    later = store.update_routine(
        routine.id, schedule={"kind": "every", "hours": 2}, next_run=lambda schedule: 777
    )
    assert (later.schedule, later.next_run_ms) == (Schedule("every", hours=2), 777)
    assert store.update_routine(routine.id, title="T", next_run=lambda s: 1).next_run_ms == 777
    assert store.delete_routine(routine.id) is True
    assert store.routine(routine.id) is None


def test_raphael_has_routines_too_and_nobody_else_who_is_not_there(store):
    assert store.create_routine(
        member_id=0, title="t", prompt="p", schedule={"kind": "every", "hours": 1}, next_run_ms=1
    ).member_id == 0
    assert kind_of(
        lambda: store.create_routine(
            member_id=42, title="t", prompt="p", schedule={"kind": "every", "hours": 1},
            next_run_ms=1,
        )
    ) == "not_found"  # fmt: skip


@pytest.mark.parametrize(
    ("fields", "kind"),
    [
        ({"title": ""}, "title"),
        ({"title": "x" * 41}, "title"),
        ({"prompt": ""}, "prompt"),
        ({"prompt": "x" * 1001}, "prompt"),
        ({"schedule": {"kind": "daily", "time": "25:00"}}, "schedule"),
        ({"enabled": "yes"}, "enabled"),
    ],
)
def test_what_a_routine_cannot_be_is_refused(store, fields, kind):
    values = {"title": "t", "prompt": "p", "schedule": {"kind": "every", "hours": 1}, **fields}
    assert kind_of(lambda: store.create_routine(member_id=0, next_run_ms=1, **values)) == kind


def test_a_member_has_a_limited_number_of_routines(store):
    for n in range(MAX_ROUTINES):
        store.create_routine(
            member_id=0, title=f"t{n}", prompt="p", schedule={"kind": "every", "hours": 1},
            next_run_ms=1,
        )  # fmt: skip
    assert kind_of(
        lambda: store.create_routine(
            member_id=0, title="one more", prompt="p", schedule={"kind": "every", "hours": 1},
            next_run_ms=1,
        )
    ) == "full"  # fmt: skip


def test_the_routines_that_are_due_are_the_ones_that_are_on_and_past_their_time(store):
    def make(title, next_run_ms, enabled=True):
        return store.create_routine(
            member_id=0, title=title, prompt="p", schedule={"kind": "every", "hours": 1},
            next_run_ms=next_run_ms, enabled=enabled,
        )  # fmt: skip

    late = make("late", 50)
    make("later", 500)
    make("off", 10, enabled=False)
    now = make("now", 100)
    assert [r.id for r in store.due(100)] == [late.id, now.id]
    assert store.advance(late.id, next_run_ms=3_700, ran_at_ms=100) is True
    advanced = store.routine(late.id)
    assert (advanced.next_run_ms, advanced.last_run_ms) == (3_700, 100)
    assert [r.id for r in store.due(100)] == [now.id]
    assert store.advance(999, next_run_ms=1, ran_at_ms=None) is False


# --- unread ----------------------------------------------------------------------------------------


def test_a_thread_is_unread_until_it_is_read(store):
    store.mark_unread(0)
    store.mark_unread(0)
    store.mark_unread(3)
    assert store.unread() == {0, 3}
    assert store.mark_read(0) is True
    assert store.mark_read(0) is False
    assert store.unread() == {3}
