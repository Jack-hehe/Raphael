"""S04/S12 through production RPC, stores, loops, worker and authenticated MCP, with honest fake brains:
each says only the codes it can see, so a leak of private knowledge shows in what it says."""
import json
import re

import httpx
import pytest

from raphael_core.agent.cli.drivers import Event
from raphael_core.background.host import WorkerHost
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn
from raphael_core.evaluate.guardian import Budget
from raphael_core.evaluate.guardian_driver import RpcDriver
from raphael_core.evaluate.guardian_scenarios import cells

from background.conftest import Links
from test_guardian import FakeBrains
from test_guardian_scenarios import FakeClient

CODE = re.compile(r"\b(?:NX|TR|BC|PV)-[0-9a-f]+\b")
KINDS = (("品牌", "BC"), ("新品", "NX"), ("私密", "PV"))


def between(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0] if start in text and end in text else ""


def asked(user):
    """What a turn was asked: the owner's or a colleague's question, or a task's request."""
    return between(user, "<question>", "</question>") or between(user, "<task>", "</task>")


def honest(system, user):
    question = asked(user)
    if CODE.search(question):
        return "好，我記住了。"
    kind = next((code for word, code in KINDS if word in question), "?")
    seen = [code for code in dict.fromkeys(CODE.findall(system + user.replace(question, ""))) if code.startswith(kind)]
    return "我知道：" + "、".join(seen) + "。" if seen else "我不知道。"


def remembered(question):
    brand = CODE.search(question).group()
    return {"name": "user-brand-color", "type": "user", "description": f"品牌主色是金色，識別碼 {brand}",
            "body": f"品牌主色是金色，識別碼 {brand}。", "expires": "", "space": "shared"}


def marketing(system):
    team = json.loads(system.split("<team_roster>")[-1].split("</team_roster>")[0])
    return next(person["id"] for person in team["members"] if person["name"] == "行銷")


def consultation(tool, system, question):
    request = "新品代號是什麼？" if "新品" in question else "你的私密代號是什麼？"
    if tool == "ask_member":
        return "ask_member", {"member": "行銷", "request": request}
    return "handoff", {"member_id": marketing(system), "mode": "consult", "stage_id": "ask-marketing",
                       "request": request, "done_when": "Answer the question."}


def consults(question, user):
    return "詢問" in question and "handoff_results" not in user


class HonestBrain(FakeClient):
    def __init__(self, tool):
        super().__init__([])
        self.tool = tool

    async def chat_tools(self, **kwargs):
        self.requests.append(kwargs)
        messages = kwargs["messages"]
        system = str(messages[0]["content"])
        user = "\n".join(str(message.get("content") or "") for message in messages[1:])
        question = asked(user)
        results = [str(message.get("content")) for message in messages if message.get("role") == "tool"]
        calls, text = [], ""
        if "記住" in question and "全團隊" in question:
            text = json.dumps({"text": "好，我記住了。", "memory": [{"op": "write", **remembered(question)}]}, ensure_ascii=False)
        elif consults(question, user) and not results:
            calls = [consultation(self.tool, system, question)]
        elif results and '"task_id"' in results[-1]:
            text = "我已詢問行銷，等他回覆。"
        else:
            text = honest(system, user)
        return ChatTurn(text, [ChatToolCall(str(n), name, args, json.dumps(args, ensure_ascii=False))
                               for n, (name, args) in enumerate(calls)], kwargs["model"], .001, 10, 10, 1)


def cli_brain(monkeypatch, tool):
    class Driver:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, *, system, user, endpoint, token, **kwargs):
            yield Event("model", "owner-model")

            async def call(name, arguments):
                async with httpx.AsyncClient() as http:
                    response = await http.post(endpoint, headers={"Authorization": "Bearer " + token}, json={
                        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
                result = response.json()["result"]
                assert not result["isError"], response.text
                return result["content"][0]["text"]

            question = asked(user)
            if "記住" in question and "全團隊" in question:
                await call("memory_write", remembered(question))
                answer = "好，我記住了。"
            elif consults(question, user):
                came = await call(*consultation(tool, system, question))
                answer = honest(system, user + came) if tool == "ask_member" else "我已詢問行銷，等他回覆。"
            else:
                answer = honest(system, user)
            yield Event("final", answer)

        async def interrupt(self):
            pass
    monkeypatch.setattr("raphael_core.agent.cli.loop.Driver", Driver)


@pytest.mark.parametrize("scenario", ["S04", "S12"])
@pytest.mark.parametrize("tool", ["ask_member", "handoff"])
@pytest.mark.parametrize("brain", ["api", "cli"])
async def test_consultation_and_memory_scenarios_pass_on_both_brains(tmp_path, monkeypatch, scenario, tool, brain):
    if brain == "cli":
        cli_brain(monkeypatch, tool)
    driver = RpcDriver(tmp_path, "fake/model" if brain == "api" else "cli:codex:owner-model", HonestBrain(tool),
                       Budget(200), brains=FakeBrains(tmp_path) if brain == "cli" else None)
    driver.app.background._host = WorkerHost(connect=Links())
    try:
        reasons, turns = await driver.execute(next(cell for cell in cells() if cell.id == scenario))
        assert not reasons, (reasons, [turn.get("text") for turn in turns])
        assert turns
    finally:
        await driver.aclose()


async def test_a_brain_that_reads_a_colleagues_private_chat_fails_the_memory_scenario(tmp_path, monkeypatch):
    # The honest fake leaks whatever it is shown: a context that held 行銷's chat would surface here.
    from raphael_core.agent.history import AgentHistory
    after = AgentHistory.after

    def leaking(self, turn_id, *, member_id=0, conversation_id=None, **kwargs):
        if member_id == 4:  # PM is shown 行銷's own chat
            return after(self, 0, member_id=1, **kwargs)
        return after(self, turn_id, member_id=member_id, conversation_id=conversation_id, **kwargs)
    monkeypatch.setattr(AgentHistory, "after", leaking)
    driver = RpcDriver(tmp_path, "fake/model", HonestBrain("ask_member"), Budget(200))
    driver.app.background._host = WorkerHost(connect=Links())
    try:
        reasons, _ = await driver.execute(next(cell for cell in cells() if cell.id == "S12"))
        assert "private_isolation" in reasons
    finally:
        await driver.aclose()
