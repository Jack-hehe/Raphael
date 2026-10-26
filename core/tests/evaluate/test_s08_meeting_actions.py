"""S08.meeting-actions through production RPC, rooms, worker and authenticated MCP: a short moderated meeting, then
the owner's 「現在執行」. The fake brains act only on what their prompts show."""
import asyncio
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
from test_p14_scenarios import asked

GO = "執行會議的每一項工作"
WRAPUP = "### 結論\n分工完成。\n### 待你決定\n無。\n### 下一步\n信件助理與 PM 各自回報。"
CELL = next(cell for cell in cells() if cell.id == "S08.meeting-actions")


def owners(user):
    """The two owners the meeting named, by the ids of the room's members list."""
    names = {person["name"]: person["id"] for person in json.loads(user.split("Members: ", 1)[1].split("\n", 1)[0])}
    return [{"member_id": names[name], "stage_id": stage, "request": request,
             "done_when": "Read the test mail and report its nonce."}
            for name, stage, request in (("信件助理", "mail-read", "用 Guardian 讀測試信並回報信中的 nonce。"),
                                         ("PM", "reply-points", "用 Guardian 讀同一封信，列出回覆重點並附上 nonce。"))]


def act(system, user, results, *, promise=False):
    """What a fake brain does with one prompt: its words and its calls."""
    if "Acceptance criteria" in system:
        if not results:
            return "", [("Guardian__read", {})]
        nonce = re.search(r"nonce: (\w+)", "\n".join(results))
        return f"已讀取信件，信中的 nonce 是 {nonce.group(1) if nonce else '?'}。", []
    if "Close this exchange" in user:
        return WRAPUP, []
    delegated = "Delegated work results" in user or "handoff_results" in user
    if GO in asked(user) and not results and not delegated:
        return ("好，明天再做。", []) if promise else ("", [("handoff", arguments) for arguments in owners(user)])
    return "收到。", []


class Brain(FakeClient):
    def __init__(self, promise=False):
        super().__init__([])
        self.promise = promise

    async def chat_tools(self, **kwargs):
        self.requests.append(kwargs)
        messages = kwargs["messages"]
        system = str(messages[0]["content"])
        user = "\n".join(str(message.get("content") or "") for message in messages[1:])
        results = [str(message.get("content")) for message in messages if message.get("role") == "tool"]
        text, calls = act(system, user, results, promise=self.promise)
        return ChatTurn(text, [ChatToolCall(str(n), name, arguments, json.dumps(arguments, ensure_ascii=False))
                               for n, (name, arguments) in enumerate(calls)], kwargs["model"], .001, 10, 10, 1)


def cli_brain(monkeypatch):
    class Driver:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, *, system, user, endpoint, token, **kwargs):
            yield Event("model", "owner-model")
            results = []
            while True:
                text, calls = act(system, user, results)
                if not calls:
                    break
                for name, arguments in calls:
                    async with httpx.AsyncClient() as http:
                        response = await http.post(endpoint, headers={"Authorization": "Bearer " + token}, json={
                            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
                    result = response.json()["result"]
                    assert not result["isError"], response.text
                    results.append(result["content"][0]["text"])
            yield Event("final", text)

        async def interrupt(self):
            pass
    monkeypatch.setattr("raphael_core.agent.cli.loop.Driver", Driver)


@pytest.mark.parametrize("brain", ["api", "cli"])
async def test_meeting_actions_start_at_once_and_report_back_to_the_room(tmp_path, monkeypatch, brain):
    if brain == "cli":
        cli_brain(monkeypatch)
    driver = RpcDriver(tmp_path, "fake/model" if brain == "api" else "cli:codex:owner-model", Brain(), Budget(200),
                       brains=FakeBrains(tmp_path) if brain == "cli" else None)
    driver.app.background._host = WorkerHost(connect=Links(), grace_s=.3)
    try:
        reasons, turns = await asyncio.wait_for(driver.execute(CELL), 30)
        assert not reasons, (reasons, [turn.get("text") for turn in turns])
        assert len(driver.recorder.receipts) == 2
    finally:
        await driver.aclose()


async def test_a_chief_that_promises_instead_of_dispatching_fails(tmp_path):
    # 「明天再做」 is no hand-off: no owner's work starts.
    driver = RpcDriver(tmp_path, "fake/model", Brain(promise=True), Budget(200))
    driver.app.background._host = WorkerHost(connect=Links(), grace_s=.3)
    try:
        reasons, _ = await asyncio.wait_for(driver.execute(CELL), 30)
        assert "one_task_per_owner" in reasons
    finally:
        await driver.aclose()
