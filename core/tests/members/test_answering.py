"""Tests for the members of the team in the assistant: a member answers in its own thread as itself,
with its own tools and memory, never on the desktop; Raphael asks a member, or adds one, with the
team tools; and what one assistant was told stays in its thread."""

import pytest

from agent.conftest import FakeActions, FakeOpenRouter, completion, decision, tool_plan
from agent.test_service import Rig
from raphael_core.agent.history import AgentHistory
from raphael_core.agent.team import team_tools_for
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.members.service import MembersService
from raphael_core.memory.service import MemoryService
from raphael_core.protocol import RpcError

from .conftest import FakeBackground, QuietModel


class Team:
    def __init__(self, conn, tmp_path):
        keys = MemoryKeyStore()
        keys.set("sk-or-v1-test")
        self.memory = MemoryService(
            conn, tmp_path / "memory", QuietModel(), keys, quiet_s=3600.0, start_delay_s=3600.0
        )
        self.background = FakeBackground(conn)
        self.members = MembersService(
            conn, tmp_path, background=self.background, memory=self.memory
        )
        self.memory.member_exists = self.members.member_exists


@pytest.fixture
def client():
    return FakeOpenRouter()


@pytest.fixture
async def setup(conn, tmp_path, client):
    team = Team(conn, tmp_path)
    rig = Rig(conn, client, FakeActions(), team=team.members, memory=team.memory)
    yield rig, team
    await rig.aclose()
    await team.memory.aclose()


def member(team, name="小行程", **fields):
    return team.members.store.create_member(
        name=name, role=fields.pop("role", "管行程"),
        instructions=fields.pop("instructions", "PRIVATE-INSTRUCTIONS 用條列回覆"), **fields,
    )  # fmt: skip


async def typed(rig, text, member_id, *, source="app", language="zh-TW"):
    return await rig.call(
        "agent.ask",
        {"session_id": "s1", "text": text, "ui_language": language, "source": source, "member_id": member_id},
    )


def said(text, *, detail="", memory=None, search=""):
    data = {"text": text, "detail": detail}
    if memory is not None:
        data.update(memory=memory, need={"read": [], "search": search})
    return completion(data)


def raphael_said(text, prefix="答"):
    return completion({"prefix": prefix, "text": text, "detail": "", "memory": [], "need": {"read": [], "search": ""}})


# --- a member in its own thread ---------------------------------------------------------------------


async def test_a_member_answers_in_its_thread_as_itself_in_plain_sentences(setup, client, conn):
    rig, team = setup
    one = member(team)
    client.decisions.append(decision("chat"))
    client.replies.append(said("好的。\n明天早上九點開會。", memory=[]))
    result = await typed(rig, "明天有什麼行程", one.id)
    assert (result["prefix"], result["text"]) == ("", "好的。\n明天早上九點開會。")
    call = client.json_calls[0]
    assert "You are 小行程" in call["system"] and "PRIVATE-INSTRUCTIONS" in call["system"]
    assert "Your role: 管行程." in call["system"]
    assert "You are Raphael" not in call["system"] and "主人" not in call["system"].split("Memory")[0]
    assert "prefix" not in call["schema"]["properties"]
    history = AgentHistory(conn)
    (turn,) = history.list(member_id=one.id)
    assert (turn.member_id, turn.prefix, turn.source) == (one.id, "", "app")
    assert history.list(member_id=0) == []
    listed = (await rig.call("agent.history.list", {"member_id": one.id}))["turns"]
    assert [t["id"] for t in listed] == [turn.id] and listed[0]["member_id"] == one.id
    assert (await rig.call("agent.history.list", {}))["turns"] == []


async def test_a_member_has_its_own_model_when_it_has_one(setup, client):
    rig, team = setup
    one = member(team, model="own/model")
    client.decisions.append(decision("chat"))
    client.replies.append(said("好。", memory=[]))
    await typed(rig, "你好", one.id)
    assert client.json_calls[0]["model"] == "own/model"


async def test_the_voice_and_the_chat_apps_always_go_to_raphael(setup):
    rig, team = setup
    one = member(team)
    for source in ("voice", "telegram"):
        with pytest.raises(RpcError):
            await typed(rig, "你好", one.id, source=source)
    with pytest.raises(RpcError):
        await typed(rig, "你好", 42)
    with pytest.raises(RpcError):
        await rig.call("agent.history.list", {"member_id": 42})


@pytest.mark.parametrize(("route", "command"), [("operate", "none"), ("command", "open")])
async def test_a_member_never_acts_on_the_desktop(setup, client, route, command):
    rig, team = setup
    one = member(team)
    client.decisions.append(decision(route, command))
    result = await typed(rig, "打開小算盤算 12 乘 34", one.id)
    assert (result["prefix"], result["text"]) == ("", "只有拉斐爾能操作這台電腦。")
    assert rig.actions.calls == []


async def test_a_member_looks_up_only_with_the_tools_it_was_given(setup, client):
    rig, team = setup
    one = member(team, tools=[])
    client.decisions.append(decision("answer", search=0.95, weather=0.95))
    client.replies.append(said("我沒有可以查的工具。", memory=[]))
    await typed(rig, "今天台北天氣和新聞", one.id)
    assert client.text_calls == []  # no web search
    assert rig.meteo.requests == []  # no weather


async def test_a_member_task_goes_to_the_background_as_the_members(setup, client):
    rig, team = setup
    one = member(team, model="own/model")
    client.decisions.append(decision("delegate"))
    result = await typed(rig, "整理這週的會議", one.id)
    assert result["prefix"] == ""
    assert rig.service._background.members == [one.id]


# --- the team tools in Raphael's answers ------------------------------------------------------------


async def test_raphael_asks_a_member_and_the_exchange_is_in_both_threads(setup, client, conn):
    rig, team = setup
    one = member(team)
    client.decisions.append(decision("answer"))  # Raphael's request
    client.plans.append(tool_plan(("Raphael__ask_member", {"member": "小行程", "request": "主人明天有什麼行程？"})))
    client.decisions.append(decision("answer"))  # the member's flags
    client.replies.append(said("明天九點開會。", memory=[]))  # the member's answer
    client.replies.append(raphael_said("小行程說明天九點開會。"))
    result = await rig.ask("問小行程我明天有什麼行程")
    assert (result["prefix"], result["text"]) == ("答", "小行程說明天九點開會。")
    plan = client.plan_calls[0]
    assert "Raphael__ask_member" in {t["function"]["name"] for t in plan["tools"]}
    member_call, raphael_call = client.json_calls
    assert "You are 小行程" in member_call["system"]
    assert "明天九點開會" in raphael_call["user"]  # the answer came back as data
    history = AgentHistory(conn)
    (theirs,) = history.list(member_id=one.id)
    assert (theirs.route, theirs.source, theirs.peer_id, theirs.question, theirs.text) == (
        "exchange", "member", 0, "主人明天有什麼行程？", "明天九點開會。",
    )  # fmt: skip
    mine = history.list(member_id=0)
    assert [t.route for t in mine] == ["answer", "exchange"]
    assert (mine[1].peer_id, mine[1].text) == (one.id, "明天九點開會。")
    steps = (await rig.turns())[0]["steps"]
    assert ("ask_member", "ok") in [(s["tool"], s["outcome"]) for s in steps]


async def test_a_question_about_something_else_offers_no_team_tool(setup, client):
    rig, team = setup
    member(team)
    client.decisions.append(decision("answer"))
    client.replies.append(raphael_said("現在是下午兩點。"))
    await rig.ask("現在幾點")
    assert client.plan_calls == []  # nothing to pick: no team tool was offered


async def test_raphael_adds_a_member_when_the_owner_asks(setup, client):
    rig, team = setup
    client.decisions.append(decision("command"))  # the router took it for a command it has not
    client.plans.append(
        tool_plan(("Raphael__create_member", {"name": "小行程", "role": "管行程", "instructions": "每天早上提醒我", "group": "生活"}))
    )
    result = await rig.ask("幫我加一個管行程的成員")
    assert (result["prefix"], result["text"]) == ("了", "已建立「小行程」。")
    made = team.members.store.by_name("小行程")
    assert made.role == "管行程" and team.members.store.group(made.group_id).name == "生活"
    assert client.json_calls == []  # nothing more to word


class _Voice:
    """Raphael's voice, as a note of what he was told to say and how often he was stopped."""

    def __init__(self):
        self.said = []

    def say(self, prefix, text):
        self.said.append((prefix, text))

    def stop(self):
        pass


@pytest.fixture
async def voiced(conn, tmp_path, client):
    team = Team(conn, tmp_path)
    voice = _Voice()
    rig = Rig(conn, client, FakeActions(), team=team.members, memory=team.memory, speaker=voice)
    yield rig, team, voice
    await rig.aclose()
    await team.memory.aclose()


async def test_a_members_reply_is_not_spoken(voiced, client):
    """Only Raphael's own replies are said aloud: what a member answers in its thread is written."""
    rig, team, voice = voiced
    one = member(team)
    client.decisions.append(decision("chat"))
    client.replies.append(said("明天早上九點開會。", memory=[]))
    result = await typed(rig, "明天有什麼行程", one.id)
    assert result["text"] == "明天早上九點開會。"
    assert voice.said == []
    # ... and Raphael's own, typed at this computer in his thread, is.
    client.decisions.append(decision("chat"))
    client.replies.append(raphael_said("台北。"))
    await typed(rig, "主人住哪裡", 0)
    assert voice.said == [("答", "台北。")]


async def test_an_exchange_between_members_is_never_spoken_only_raphaels_final_reply(voiced, client):
    rig, team, voice = voiced
    member(team, name="小研究")
    client.decisions.append(decision("answer"))  # Raphael's request
    client.plans.append(tool_plan(("Raphael__ask_member", {"member": "小研究", "request": "上季的數字"})))
    client.decisions.append(decision("answer"))  # the member's flags
    client.replies.append(said("PRIVATE-ANSWER 上季營收兩百萬。", memory=[]))  # the member, asked
    client.replies.append(raphael_said("小研究說上季營收兩百萬。"))  # Raphael, to the owner
    await typed(rig, "問小研究上季的數字", 0)
    assert voice.said == [("答", "小研究說上季營收兩百萬。")]


async def test_a_member_asks_raphael_who_answers_as_himself(setup, client, conn):
    rig, team = setup
    one = member(team)
    member(team, name="小研究")
    client.decisions.append(decision("answer"))  # the member's request
    client.plans.append(tool_plan(("Raphael__ask_member", {"member": "拉斐爾", "request": "主人住哪裡？"})))
    client.decisions.append(decision("answer"))  # Raphael's flags, asked
    client.replies.append(raphael_said("台北。"))  # Raphael, asked: nobody named, nothing to pick
    client.replies.append(said("主人住在台北。", memory=[]))
    result = await typed(rig, "問拉斐爾主人住哪裡", one.id)
    assert result["text"] == "主人住在台北。"
    tools = {t["function"]["name"] for t in client.plan_calls[0]["tools"]}
    assert tools == {"Raphael__ask_member"}  # no create_member for a member
    assert len(client.plan_calls) == 1
    assert "You are Raphael" in client.json_calls[0]["system"]
    (raphaels,) = AgentHistory(conn).list(member_id=0)
    assert (raphaels.route, raphaels.peer_id) == ("exchange", one.id)


async def test_a_member_asks_another_and_only_those_two_threads_hold_it(setup, client, conn):
    rig, team = setup
    a = member(team, name="小財務")
    b = member(team, name="小研究")
    c = member(team, name="小行程")
    client.decisions.append(decision("answer"))  # A's request
    client.plans.append(tool_plan(("Raphael__ask_member", {"member": "小研究", "request": "PRIVATE-REQUEST 上季的數字"})))
    client.decisions.append(decision("answer"))  # B's flags
    client.replies.append(said("PRIVATE-ANSWER 上季營收兩百萬。", memory=[]))  # B, as itself
    client.replies.append(said("小研究說上季營收兩百萬。", memory=[]))  # A
    result = await typed(rig, "問小研究上季的數字", a.id)
    assert result["text"] == "小研究說上季營收兩百萬。"
    b_call, a_call = client.json_calls
    assert "You are 小研究" in b_call["system"]
    assert "PRIVATE-ANSWER" in a_call["user"]  # the answer, as data
    history = AgentHistory(conn)
    (bs,) = history.list(member_id=b.id)
    assert (bs.route, bs.peer_id, bs.question) == ("exchange", a.id, "PRIVATE-REQUEST 上季的數字")
    assert [(t.route, t.peer_id) for t in history.list(member_id=a.id)] == [("answer", None), ("exchange", b.id)]
    # A third member, and Raphael, see nothing of it.
    assert history.list(member_id=c.id) == [] and history.list(member_id=0) == []
    assert team.memory.search("上季", member_id=c.id).data["turns"] == []
    assert (await rig.call("agent.history.search", {"query": "上季", "member_id": c.id}))["turns"] == []


async def test_a_chain_is_at_most_two_deep_and_never_comes_back(setup):
    rig, team = setup
    a, b, c = (member(team, name=n).speaker() for n in ("小財務", "小研究", "小行程"))

    async def asks(speaker, whom):
        result = await rig.service.team_tool(
            speaker, "ask_member", {"member": whom, "request": "x"},
            language="zh-TW", ledger=None, session_id="s",
        )  # fmt: skip
        return result.error

    b_asked = b.asked_by(a)  # A asked B: B may ask one more
    assert b_asked.depth == 1 and b_asked.may_ask
    c_asked = c.asked_by(b_asked)  # B asked C: C asks nobody
    assert c_asked.depth == 2 and not c_asked.may_ask
    assert await asks(c_asked, "拉斐爾") == "blocked"
    assert await asks(b_asked, "小財務") == "blocked"  # A is in the chain: no cycle
    assert await asks(b_asked, "小研究") == "blocked"  # nobody asks itself
    assert await asks(a, "小財務") == "blocked"
    assert await asks(a, "nobody") == "not_found"
    created = await rig.service.team_tool(
        a, "create_member", {"name": "x", "role": "", "instructions": ""},
        language="zh-TW", ledger=None, session_id="s",
    )  # fmt: skip
    assert created.error == "blocked"  # adding a member is Raphael's, at the owner's request


async def test_the_fast_tier_offers_ask_member_only_for_someone_it_may_ask(setup):
    rig, team = setup
    a, b = (member(team, name=n).speaker() for n in ("小財務", "小研究"))
    assert team_tools_for(team.members, a, "問小研究") == ["ask_member"]
    assert team_tools_for(team.members, a, "問小財務") == []  # itself
    assert team_tools_for(team.members, b.asked_by(a), "問小財務") == []  # already in the chain
    assert team_tools_for(team.members, a, "今天天氣") == []
    assert team_tools_for(team.members, a, always=True) == ["ask_member"]


async def test_raphael_asked_for_the_desktop_says_the_owner_must_ask_him(setup, client, conn):
    rig, team = setup
    one = member(team)
    client.decisions.append(decision("answer"))
    client.plans.append(tool_plan(("Raphael__ask_member", {"member": "拉斐爾", "request": "幫我打開小算盤"})))
    client.decisions.append(decision("operate"))  # what Raphael was asked for
    client.replies.append(said("拉斐爾說要主人直接跟他說。", memory=[]))
    await typed(rig, "請拉斐爾打開小算盤", one.id)
    (raphaels,) = AgentHistory(conn).list(member_id=0)
    assert raphaels.text == "操作電腦需要直接向拉斐爾下達指示。"
    assert rig.actions.calls == []


# --- what one assistant was told stays in its thread ------------------------------------------------


async def test_a_decision_in_one_members_thread_never_reaches_another(setup, client, conn):
    rig, team = setup
    a = member(team, name="小財務")
    b = member(team, name="小研究")
    # In A's thread the owner decides something, and asks A to remember it for everyone.
    client.decisions.append(decision("chat"))
    note = {
        "op": "write", "name": "user-budget", "type": "user", "description": "我們決定預算是三萬",
        "body": "我們決定預算是三萬元。", "expires": "", "space": "shared",
    }  # fmt: skip
    client.replies.append(said("", memory=[note]))
    await typed(rig, "記住，我們決定預算是三萬", a.id)
    assert team.memory.shared.all() == []  # a decision is not macro knowledge: it stayed with A
    assert team.memory.store_for(a.id).get("user-budget") is not None
    # B is asked, and looks back in its past: none of it is there.
    client.decisions.append(decision("discuss"))
    client.replies.append(said("", memory=[], search="預算"))
    client.replies.append(said("我沒有關於預算的紀錄。", memory=[]))
    await typed(rig, "我們的預算是多少", b.id)
    first, again = client.json_calls[-2:]
    for call in (first, again):
        assert "三萬" not in call["system"] and "三萬" not in call["user"]
    assert "三萬" not in str(team.memory.index(b.id))
    # B's search finds B's own question, never A's turn.
    searched = team.memory.search("預算", member_id=b.id).data["turns"]
    assert searched and not any("三萬" in t["question"] + t["answer"] for t in searched)
    assert team.memory.conversation_for(b.id).summary().text == ""
    found = await rig.call("agent.history.search", {"query": "預算", "member_id": b.id})
    assert {t["member_id"] for t in found["turns"]} == {b.id}
    assert not any("三萬" in t["question"] for t in found["turns"])
    # A's own search finds it, in A's thread only.
    assert team.memory.search("預算", member_id=a.id).data["turns"]
