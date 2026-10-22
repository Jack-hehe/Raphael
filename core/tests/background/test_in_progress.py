"""Tests for a call that was under way when the core ended (it leaves a step that says so), the
tool calls of every earlier attempt of a rerun, and telling the app after it is listening."""

from raphael_core.agent.persona import phrase

from .conftest import TASK_TEXT, call, report, turn, until


async def test_a_plugin_call_is_a_step_from_the_moment_it_is_sent(make_rig, calls):
    rig = make_rig()
    await rig.plugins.connect(slow="T0")
    rig.client.replies = [turn("", [call("Notes__slow", {}, "c1")])]
    task_id = rig.submit()
    await until(lambda: "slow" in calls)
    tools = [s for s in rig.steps(task_id) if s.kind == "tool"]
    assert [(s.tool, s.server, s.outcome) for s in tools] == [("slow", "Notes", "in_progress")]
    # the conversation view is not shown a step that has not ended
    rig.service.attach_turn(task_id, 99)
    _, shown = rig.service.tasks_for_turns([99])[99]
    assert [s["outcome"] for s in shown if s["kind"] == "tool"] == []
    await rig.rpc.call("worker.cancel", {})
    await rig.wait_for_reports()
    tools = [s for s in rig.steps(task_id) if s.kind == "tool"]
    assert [s.outcome for s in tools] == ["cancelled"]  # one step, not two


async def test_a_call_that_ends_updates_its_step(make_rig):
    rig = make_rig()
    await rig.plugins.connect(look="T0")
    rig.client.replies = [turn("", [call("Notes__look", {"topic": "a"}, "c1")]), report("ok")]
    task_id = rig.submit()
    await rig.wait_for_reports()
    tools = [s for s in rig.steps(task_id) if s.kind == "tool"]
    assert [(s.tool, s.outcome) for s in tools] == [("look", "ok")]


async def test_a_call_left_in_progress_by_a_crash_is_cut_off_when_the_core_starts_again(
    make_rig,
):
    first = make_rig()
    task_id = first.submit()
    first.service._queue.clear()
    store = first.service._store
    store.start(task_id)
    store.add_step(task_id, kind="tool", tool="send_email", server="Gmail", outcome="in_progress")
    second = make_rig()  # the process was killed: nothing ended that step
    assert [s.outcome for s in second.steps(task_id)] == ["cancelled"]
    second.client.replies = [report("Redone.")]
    await second.rpc.call("worker.rerun", {"task_id": task_id})
    await second.wait_for_reports()
    user = second.client.chat_calls[-1]["messages"][1]["content"]
    assert "Gmail / send_email: cut off, may have run" in user


async def test_a_rerun_of_a_rerun_still_knows_what_the_first_attempt_did(make_rig):
    rig = make_rig()
    store = rig.service._store
    first = store.create(session_id="s1", language="en", text=TASK_TEXT)
    store.add_step(first, kind="tool", tool="send_email", server="Gmail", outcome="ok")
    store.add_step(first, kind="tool", tool="search", server="Notion", outcome="cancelled")
    store.finish(first, "cancelled", prefix="告", text="x", detail=None, reason="cancelled")
    second = store.create(session_id="s1", language="en", text=TASK_TEXT, rerun_of=first)
    store.add_step(
        second, kind="tool", tool="search", server="Notion", outcome="error", reason="tool_error"
    )
    store.finish(second, "failed", prefix="告", text="x", detail=None, reason="failed")
    rig.client.replies = [report("Third time.")]
    result = await rig.rpc.call("worker.rerun", {"task_id": second})
    await rig.wait_for_reports()
    block = rig.client.chat_calls[0]["messages"][1]["content"]
    assert "Gmail / send_email: ok" in block
    assert "Notion / search: cut off, may have run" in block
    assert "Notion / search: failed" in block
    assert block.index("Gmail / send_email") < block.index("Notion / search: failed")
    assert rig.task(result["task_id"]).rerun_of == second


async def test_a_custom_service_called_raphael_is_still_told_to_the_next_attempt(make_rig):
    rig = make_rig()
    store = rig.service._store
    first = store.create(session_id="s1", language="en", text=TASK_TEXT)
    store.add_step(first, kind="tool", tool="send_email", server="Raphael", outcome="ok")
    store.add_step(first, kind="tool", tool="weather", server="Raphael", outcome="ok", tier="T0")
    store.finish(first, "failed", prefix="告", text="x", detail=None, reason="failed")
    second = store.create(session_id="s1", language="en", text=TASK_TEXT, rerun_of=first)
    lines = rig.service._earlier_attempt(rig.task(second))
    assert lines == ["Raphael / send_email: ok"]


async def test_a_loop_in_the_chain_of_reruns_ends(make_rig):
    rig = make_rig()
    store = rig.service._store
    a = store.create(session_id="s1", language="en", text="t")
    b = store.create(session_id="s1", language="en", text="t", rerun_of=a)
    rig.service._store._conn.execute("UPDATE worker_tasks SET rerun_of = ? WHERE id = ?", (b, a))
    task = rig.task(b)
    assert rig.service._earlier_attempt(task) == []


# --- telling the app once it listens ------------------------------------------------------------


async def test_the_report_of_an_interrupted_task_is_said_when_the_shell_asks_not_at_hello(
    make_rig, conn
):
    first = make_rig()
    task_id = first.submit()
    first.service._queue.clear()
    second = make_rig()
    assert second.reports() == []
    assert await second.rpc.call("worker.announce_pending", {}) == {}
    assert [r["task_id"] for r in second.reports()] == [task_id]
    assert second.reports()[0]["text"] == phrase("en", "worker_interrupted", steps="0")
    await second.rpc.call("worker.announce_pending", {})
    assert len(second.reports()) == 1



async def test_a_rerun_may_repeat_a_lookup_but_not_a_call_that_changed_something(make_rig):
    rig = make_rig()
    await rig.plugins.connect(look="T0", note="T0")
    store = rig.service._store
    first = store.create(session_id="s1", language="en", text=TASK_TEXT)
    store.add_step(first, kind="tool", tool="look", server="Notes", outcome="ok")
    store.add_step(first, kind="tool", tool="note", server="Notes", outcome="ok")
    store.add_step(first, kind="tool", tool="mystery", server="Elsewhere", outcome="ok")
    store.finish(first, "interrupted", prefix="告", text="x", detail=None, reason="closing")
    rig.client.replies = [report("Again.")]
    result = await rig.rpc.call("worker.rerun", {"task_id": first})
    await rig.wait_for_reports()
    user = rig.client.chat_calls[0]["messages"][1]["content"]
    block = user.split("<previous_attempt>")[1].split("</previous_attempt>")[0]
    assert "Notes / look: ok [read-only]" in block
    assert "Notes / note: ok\n" in block + "\n"  # a call that changed something has no such mark
    assert "Elsewhere / mystery: ok\n" in block + "\n"  # a tool not known counts as one that changes
    assert "read-only" in block.split("Notes / look")[0]  # the model is told what the mark means
    assert rig.task(result["task_id"]).rerun_of == first


async def test_a_failure_after_the_call_was_sent_is_not_known_to_have_failed(make_rig):
    rig = make_rig()
    store = rig.service._store
    first = store.create(session_id="s1", language="en", text=TASK_TEXT)
    for tool, outcome, reason in [
        ("a_refused", "error", "tool_error"),
        ("b_login", "error", "auth"),
        ("c_network", "error", "network"),
        ("d_timeout", "timeout", "timeout"),
        ("e_gone", "error", "gone"),
        ("f_protocol", "error", "protocol"),
        ("g_unknown", "error", None),
        ("h_blocked", "blocked", "blocked"),
    ]:
        store.add_step(first, kind="tool", tool=tool, server="Svc", outcome=outcome, reason=reason)
    store.finish(first, "failed", prefix="告", text="x", detail=None, reason="failed")
    second = store.create(session_id="s1", language="en", text=TASK_TEXT, rerun_of=first)
    lines = {line.split(":")[0].split(" / ")[1]: line for line in rig.service._earlier_attempt(rig.task(second))}
    for sure in ("a_refused", "b_login", "h_blocked"):
        assert lines[sure].endswith(": failed"), lines[sure]
    for unsure in ("c_network", "d_timeout", "e_gone", "f_protocol", "g_unknown"):
        assert "unknown whether it ran" in lines[unsure] or "may have run" in lines[unsure], lines[unsure]
