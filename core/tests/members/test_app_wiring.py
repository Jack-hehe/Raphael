"""Tests for the members in the whole core: the methods are on the app, a member made by RPC answers
a message typed in its thread with the fake LLM, and the pictures go to the local folder beside the
memory."""

from test_app import make_app  # noqa: F401  (the fixture)

MEMBER_METHODS = {
    "members.list", "members.create", "members.update", "members.delete", "members.set_avatar",
    "members.presets", "members.read", "members.viewing", "members.tool_choices", "groups.create",
    "groups.update", "groups.delete", "routines.list", "routines.create", "routines.update",
    "routines.delete", "routines.template", "memory.move",
}  # fmt: skip


async def test_the_members_methods_are_on_the_app(make_app):  # noqa: F811
    assert MEMBER_METHODS <= set(make_app().methods())


async def test_a_member_made_by_rpc_answers_in_its_thread(make_app):  # noqa: F811
    app = make_app()
    heard = []
    app.set_notifier(lambda method, params: heard.append((method, params)))
    member = (await app.call("members.create", {"name": "小行程", "role": "管行程"}))["member"]
    reply = await app.call(
        "agent.ask",
        {"session_id": "s1", "text": "你好", "ui_language": "zh-TW", "source": "app", "member_id": member["id"]},
    )
    assert reply["status"] == "done" and reply["prefix"] == ""
    turns = (await app.call("agent.history.list", {"member_id": member["id"]}))["turns"]
    assert [t["member_id"] for t in turns] == [member["id"]]
    assert (await app.call("agent.history.list", {}))["turns"] == []
    assert ("agent.turn", {"turn_id": turns[0]["id"], "member_id": member["id"]}) in heard
    # Nobody viewed the thread: it is news.
    listed = {m["id"]: m for m in (await app.call("members.list", {}))["members"]}
    assert listed[member["id"]]["unread"] is True
    assert await app.call("members.delete", {"id": member["id"]}) == {"deleted": True}


async def test_the_background_names_the_members_and_tells_which_threads_wait(make_app, monkeypatch):  # noqa: F811
    app = make_app()
    member = (await app.call("members.create", {"name": "進度官", "role": "看進度"}))["member"]
    # The overlay's notice of a question names the member whose thread holds it.
    assert app.background._notice("zh-TW", member["id"]) == "進度官 等你處理。"
    assert app.background._notice("zh-TW", 0) == "有一件事等你處理。"
    assert app.members.name_of(member["id"] + 1) is None
    # A question the background waits on makes that thread `waiting` in the member column.
    monkeypatch.setattr(app.background, "waiting_questions", lambda: [{"member_id": member["id"]}])
    listed = {m["id"]: m["waiting"] for m in (await app.call("members.list", {}))["members"]}
    assert listed == {0: False, member["id"]: True}
