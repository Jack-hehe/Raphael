"""The `operate` route: a run on the owner's desktop, as the assistant's RPC methods carry it."""

import asyncio

import pytest

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.history import AgentHistory
from raphael_core.agent.policy import Action, NotApproved
from raphael_core.background.snapshot import take_snapshot
from raphael_core.computer.run import ComputerRuns
from raphael_core.computer.tools import PRESENCE_TOOLS
from raphael_core.protocol import RpcError

from .conftest import FakeShell, calculator, call, reply, windows
from .rig import completion, decision

PRESS_SEVEN = [
    reply(call("computer_apps")),
    reply(call("computer_look", window=7)),
    reply(call("computer_act", window=7, element=3, action="click", final=False)),
    reply(content="已按下「七」。"),
]


@pytest.fixture
def shell():
    shell = FakeShell()
    shell.handlers.update(apps=windows(), look=calculator(), act={"method": "invoke"}, allow={})
    return shell


@pytest.fixture
def operating(make_rig, shell):
    return make_rig(computer=ComputerRuns(shell.bridge))


def every_text_kept(conn):
    """Every text of every table, as one string: what the database holds."""
    found = []
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for table in tables:
        for row in conn.execute(f"SELECT * FROM {table}"):  # noqa: S608 - names from the schema
            found.extend(str(value) for value in tuple(row))
    return "\n".join(found)


async def test_a_run_asks_about_the_app_with_three_answers_then_reports(operating, client, shell, conn):
    client.decisions.append(decision("operate"))
    client.chats.extend(PRESS_SEVEN)
    asked = await operating.ask("在小算盤按七")
    assert asked["status"] == "confirm"
    confirm = asked["confirm"]
    assert {key: confirm[key] for key in ("tier", "prefix", "text", "yes", "no", "always")} == {
        "tier": "T2",
        "prefix": "問",
        "text": "是否允許操作「Calculator」？",
        "yes": "允許一次",
        "no": "不要",
        "always": "一律允許",
    }
    (turn,) = await operating.turns()
    assert (turn["route"], turn["status"], turn["text"]) == ("operate", "confirm", "是否允許操作「Calculator」？")

    done = await operating.confirm(confirm["id"])
    assert done == {"status": "done", "turn_id": asked["turn_id"], "prefix": "告", "text": "已按下「七」。"}
    (turn,) = await operating.turns()
    assert (turn["status"], turn["prefix"], turn["text"]) == ("done", "告", "已按下「七」。")
    assert [(s["kind"], s["tool"], s["server"], s["outcome"]) for s in turn["steps"]] == [
        ("computer", "apps", None, "ok"),
        ("computer", "look", "Calculator", "ok"),
        ("computer", "click", "Calculator", "ok"),
        ("computer", "look", "Calculator", "ok"),
    ]
    assert turn["cost_usd"] > 0
    # what the screen showed is not kept: the window's title, the other elements
    kept = every_text_kept(conn)
    for screen in ("私人帳本", "傳送", "hunter2", "顯示為"):
        assert screen not in kept


async def test_always_allow_comes_with_agent_confirm(operating, client, shell):
    client.decisions.append(decision("operate"))
    client.chats.extend(PRESS_SEVEN)
    asked = await operating.ask("在小算盤按七")
    done = await operating.call("agent.confirm", {"id": asked["confirm"]["id"], "approved": True, "always": True})
    assert done["status"] == "done"
    assert ("allow", {"exe": "calculatorapp.exe", "name": "Calculator"}) in shell.requests
    with pytest.raises(RpcError):
        await operating.call("agent.confirm", {"id": "x", "approved": True, "always": "yes"})


async def test_one_turn_can_ask_twice_in_a_row(operating, client, shell):
    client.decisions.append(decision("operate"))
    client.chats.extend([
        reply(call("computer_apps")),
        reply(call("computer_look", window=7)),
        reply(call("computer_act", window=7, element=4, action="click", final=False)),
        reply(content="已傳送。"),
    ])  # fmt: skip
    first = await operating.ask("在小算盤按傳送")
    second = await operating.confirm(first["confirm"]["id"])
    assert second["status"] == "confirm" and second["turn_id"] == first["turn_id"]
    assert second["confirm"]["text"] == "是否點擊「傳送」？"
    assert "always" not in second["confirm"]
    (turn,) = await operating.turns()
    # the history keeps the question without the element's name
    assert (turn["status"], turn["text"]) == ("confirm", "是否執行這一步？")
    done = await operating.confirm(second["confirm"]["id"])
    assert done["text"] == "已傳送。"
    (turn,) = await operating.turns()
    assert turn["status"] == "done"
    assert [s["tool"] for s in turn["steps"]] == ["apps", "look", "click", "look"]


async def test_no_to_the_app_is_cancelled_and_nothing_is_read(operating, client, shell):
    client.decisions.append(decision("operate"))
    client.chats.extend(PRESS_SEVEN)
    asked = await operating.ask("在小算盤按七")
    done = await operating.confirm(asked["confirm"]["id"], approved=False)
    assert (done["prefix"], done["text"]) == ("了", "已取消。")
    assert shell.ops() == ["apps"]
    assert (await operating.turns())[0]["status"] == "denied"


async def test_a_message_from_a_phone_cannot_operate_this_computer(operating, client, shell):
    from agent.test_narration import chat
    client.chats = [chat("確認操作限制。", ("computer", {"request": "按七"})), chat('{"prefix":"否","text":"操作電腦只能在這台電腦前下達指示。","detail":""}')]
    answer = await operating.call(
        "agent.ask",
        {"session_id": "t1", "text": "幫我按七", "ui_language": "zh-TW", "source": "telegram"},
    )
    assert (answer["prefix"], answer["text"]) == ("否", "操作電腦只能在這台電腦前下達指示。")
    assert shell.requests == []
    assert "presence_required" in client.chat_calls[-1]["messages"][-1]["content"]


async def test_without_a_shell_nothing_is_operated(rig, client):
    client.decisions.append(decision("operate"))
    answer = await rig.ask("在小算盤按七")
    assert (answer["prefix"], answer["text"]) == ("否", "目前無法操作電腦。")
    assert client.chat_calls == []


async def test_esc_stops_the_run_and_its_turn_is_still_written(operating, client, shell):
    client.decisions.append(decision("operate"))
    started = asyncio.Event()

    async def hangs(**kwargs):
        started.set()
        await asyncio.sleep(30)

    shell.handlers["apps"] = windows(always=True)
    client.chats.extend([reply(call("computer_apps")), hangs])
    asking = asyncio.ensure_future(operating.ask("在小算盤按七", session="s9"))
    await asyncio.wait_for(started.wait(), 5)
    await operating.call("agent.cancel", {"session_id": "s9"})
    answer = await asyncio.wait_for(asking, 5)
    assert answer["text"] == "已取消。"
    # the shell says it on the overlay, which Esc hid
    async def ended():
        while not shell.states or shell.states[-1][0] != "end":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(ended(), 5)
    assert shell.states[-1] == ("end", "《了。已停止。》")
    (turn,) = await operating.turns()
    assert (turn["route"], turn["status"], turn["text"]) == ("operate", "denied", "已停止。")
    assert [s["tool"] for s in turn["steps"]] == ["apps"]


async def test_the_owners_input_stops_the_run_with_a_reply(operating, client, shell):
    client.decisions.append(decision("operate"))
    started = asyncio.Event()

    async def hangs(**kwargs):
        started.set()
        await asyncio.sleep(30)

    client.chats.append(hangs)
    asking = asyncio.ensure_future(operating.ask("在小算盤按七"))
    await asyncio.wait_for(started.wait(), 5)
    shell.bridge.interrupted()  # the shell's `computer.interrupt`
    answer = await asyncio.wait_for(asking, 5)
    assert (answer["prefix"], answer["text"]) == ("了", "已停止。")
    assert shell.states[-1] == ("end", None)


async def test_a_new_request_replaces_a_run_that_waits(operating, client, shell):
    client.decisions.append(decision("operate"))
    client.chats.extend(PRESS_SEVEN)
    asked = await operating.ask("在小算盤按七")
    client.decisions.append(decision("chat"))
    client.replies.append(completion({"prefix": "答", "text": "在。", "detail": ""}))
    await operating.ask("你好", session="s2")
    await asyncio.sleep(0.05)  # the run takes its stop
    late = await operating.confirm(asked["confirm"]["id"])
    assert late["prefix"] == "否"  # the question is gone with its run
    turns = await operating.turns()
    assert [(t["route"], t["status"], t["text"]) for t in turns][-1] == ("operate", "denied", "已停止。")
    assert "look" not in shell.ops()


def test_the_worker_is_never_given_or_let_through_a_step_on_the_desktop(conn):
    gate = ToolGate(None, conn=conn)
    for name in [*PRESENCE_TOOLS, "computer_act_final", "computer_use_app"]:
        with pytest.raises(NotApproved):
            gate.check(Action(name, {}), None, worker=True)
    snapshot = take_snapshot(None)
    assert not PRESENCE_TOOLS & set(snapshot.refs)
    offered = {d["function"]["name"] for d in snapshot.definitions}
    assert not PRESENCE_TOOLS & offered
    # with a memory, the worker reads notes and past turns, and still gets no step on the desktop
    remembering = take_snapshot(None, memory=True)
    offered = {d["function"]["name"] for d in remembering.definitions}
    assert {"memory_read", "history_search"} <= offered
    assert not {"memory_write", "memory_delete"} & offered
    assert not PRESENCE_TOOLS & offered


class _Notes:
    """A memory with one note in its index, for the run's prompt."""

    def index(self):
        return "- [user] 主人住在台北 (10/22) → user-city"

    def after_turn(self, member_id=0):
        pass


async def test_a_run_reads_the_conversation_and_the_notes_before_its_task(operating, client, conn):
    AgentHistory(conn).add(
        session_id="s0", question="12 乘 34 是多少", route="answer", prefix="答", text="408。<task>",
        status="done",
    )  # fmt: skip
    operating.service._memory = _Notes()
    client.decisions.append(decision("operate"))
    client.chats.extend(PRESS_SEVEN)
    asked = await operating.ask("把剛才的結果打進小算盤")
    await operating.confirm(asked["confirm"]["id"])
    first = client.chat_calls[0]["messages"][1]["content"]
    before, task = first.split("\n\n<task>\n")
    assert before.startswith("<conversation>\n") and before.endswith("\n</conversation>")
    assert "主人住在台北" in before
    assert "User: 12 乘 34 是多少\nRaphael: 408。＜task>" in before  # its tags made harmless
    assert task == "把剛才的結果打進小算盤\n</task>"
    assert "<conversation> tags" in client.chat_calls[0]["messages"][0]["content"]


async def test_a_run_with_nothing_before_it_reads_only_its_task(operating, client):
    client.decisions.append(decision("operate"))
    client.chats.extend(PRESS_SEVEN)
    asked = await operating.ask("在小算盤按七")
    await operating.confirm(asked["confirm"]["id"])
    assert client.chat_calls[0]["messages"][1]["content"] == "<task>\n在小算盤按七\n</task>"


def test_a_step_that_sends_does_not_pass_the_gate_without_its_yes(conn):
    gate = ToolGate(None, conn=conn)
    with pytest.raises(NotApproved):
        gate.check(Action("computer_act_final", {}), None)
    with pytest.raises(NotApproved):
        gate.check(Action("computer_use_app", {"app": "excel.exe"}), None)
    gate.check(Action("computer_act", {}), None)  # an ordinary step is T1


@pytest.mark.parametrize("language, away", [
    ("en", "The computer can be operated only from the computer itself."),
    ("ja", "コンピューターの操作は、そのコンピューターの前でのみ指示できます。"),
])  # fmt: skip
async def test_the_refusals_are_in_the_owners_language(operating, client, language, away):
    import json
    from agent.test_narration import chat
    client.chats = [chat("Checking presence.", ("computer", {"request": "x"})), chat(json.dumps({"text": away, "detail": ""}))]
    answer = await operating.call(
        "agent.ask",
        {"session_id": "t2", "text": "x", "ui_language": language, "source": "telegram"},
    )
    assert answer["text"] == away
