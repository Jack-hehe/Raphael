"""S14.gmail through production RPC, loops, gates and authenticated MCP, with honest fake brains on both sides
and the automatic Google fixture: each brain asks for the Gmail card while it has no Gmail tools, and reports
only the code a Gmail read returned."""
import json
import re

import httpx
import pytest

from raphael_core.agent.cli.drivers import Event
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn
from raphael_core.connectors import cards
from raphael_core.connectors.cards import CardStore
from raphael_core.evaluate.google_fixture import GoogleFixture
from raphael_core.evaluate.guardian import Budget
from raphael_core.evaluate.guardian_driver import RpcDriver
from raphael_core.evaluate.guardian_registry import inventory
from raphael_core.evaluate.guardian_scenarios import cells
from raphael_core.evaluate.guardian_suite import run_cell

from test_guardian import FakeBrains
from test_guardian_scenarios import FakeClient

CELL = next(cell for cell in cells() if cell.id == "S14.gmail")
API, CLI = "fake/model", "cli:codex:owner-model?effort=high"
MAIL_CODE = re.compile(r"MC-[0-9a-f]{8}")


def act(user, tools, results, *, claims=False, made_up=False, write=False, after_card=False):
    """What an honest brain does with one prompt: with Gmail's read tools it searches for the mail its request
    names and reads it; without them it asks for the Gmail card and stops. It says only what a tool returned."""
    question = user.split("<question>", 1)[1].split("</question>", 1)[0] if "<question>" in user else user
    nonce = re.search(r"主旨含 (\w+) 的", question)
    search = next((name for name in tools if name.endswith("gmail_search")), None)
    get = next((name for name in tools if name.endswith("gmail_get_message")), None)
    writer = next((name for name in tools if name.endswith("__write")), None)
    last = results[-1] if results else ""
    if claims:
        return "Gmail 已經讀完了，確認碼是 MC-00000000。", []
    if write and writer and not any("test_mode" in result or "already" in result for result in results):
        return "", [(writer, {})]
    if search and not any("msg-" in result or "MC-" in result for result in results):
        return ("找到了，信裡的確認碼是 MC-12345678。", []) if made_up else ("", [(search, {"q": nonce.group(1)})])
    if get and "msg-" in last and "MC-" not in last:
        message = re.search(r"msg-\w+", last).group()
        return "", [(get, {"id": message})]
    if code := MAIL_CODE.search(last):
        return f"找到了，信裡的確認碼是 {code.group()}。", []
    if "card" in last and "Gmail" in last:
        if after_card and writer and not any("waiting" in result or "A card to connect an app" in result for result in results[:-1]):
            return "", [(writer, {})]
        return "請先連結 Gmail，連好後就繼續找這封信。", []
    if after_card and "A card to connect an app" in last:
        return "請先連結 Gmail，連好後就繼續找這封信。", []
    if not search:
        return "", [("connect_app", {"app": "gmail"})]
    return "目前找不到這封信。", []


class Brain(FakeClient):
    def __init__(self, **behaviour):
        super().__init__([])
        self.behaviour = behaviour

    async def chat_tools(self, **kwargs):
        self.requests.append(kwargs)
        messages = kwargs["messages"]
        user = str(messages[1]["content"])
        tools = [tool["function"]["name"] for tool in kwargs["tools"]]
        results = [str(message.get("content")) for message in messages if message.get("role") == "tool"]
        text, calls = act(user, tools, results, **self.behaviour)
        return ChatTurn(text, [ChatToolCall(str(n), name, arguments, json.dumps(arguments, ensure_ascii=False))
                               for n, (name, arguments) in enumerate(calls)], kwargs["model"], .001, 10, 10, 1)


def cli_brain(monkeypatch, seen, **behaviour):
    class Driver:
        def __init__(self, name, command, *, model, **kwargs):
            self.model = model

        async def run(self, *, system, user, endpoint, token, session=None, **kwargs):
            seen.append(system + user)
            yield Event("model", self.model)
            yield Event("session", session or f"thread-{id(self)}")
            async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, trust_env=False) as http:
                listed = (await http.post(endpoint, json={"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}})).json()
                tools = [tool["name"] for tool in listed["result"]["tools"]]
                results, text = [], "我沒有完成這項工作。"
                for _ in range(12):
                    text, calls = act(user, tools, results, **behaviour)
                    if not calls:
                        break
                    for name, arguments in calls:
                        response = await http.post(endpoint, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                                   "params": {"name": name, "arguments": arguments}})
                        result = response.json()["result"]["content"][0]["text"]
                        seen.append(result)
                        results.append(result)
            yield Event("final", text)

        async def interrupt(self):
            pass
    monkeypatch.setattr("raphael_core.agent.cli.loop.Driver", Driver)


def setup(tmp_path, monkeypatch, brain, **behaviour):
    seen = []
    cli_brain(monkeypatch, seen, **behaviour)
    client = Brain(**behaviour)
    driver = RpcDriver(tmp_path, API if brain == "A" else CLI, client, Budget(40), brains=FakeBrains(tmp_path))
    return driver, client, seen


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_s14_gmail_passes_on_both_brains_through_the_suite(tmp_path, monkeypatch, brain, caplog):
    seen = []
    cli_brain(monkeypatch, seen)
    client = Brain()
    result = await run_cell(CELL, brain, API if brain == "A" else CLI, tmp_path, client=client, brains=FakeBrains(tmp_path),
                            budget=Budget(40), seconds=60, run_id="offline-s14", head="test-head", registry=await inventory())
    assert result["status"] == "PASS", result
    assert result["assertions"] == list(CELL.assertions)
    # Nothing the model was shown, on either brain, holds a part of the sign-in.
    shown = "".join(json.dumps(request["messages"], ensure_ascii=False) for request in client.requests) + "".join(seen)
    assert "connect_app" in shown or brain == "C"
    assert "127.0.0.1" not in shown and "code_challenge" not in shown and "state=" not in shown
    assert "access_token" not in caplog.text and "/callback?" not in caplog.text


async def run_s14(tmp_path, monkeypatch, brain="A", **behaviour):
    driver, _, _ = setup(tmp_path, monkeypatch, brain, **behaviour)
    try:
        reasons, _ = await driver.execute(CELL)
        return reasons, driver
    finally:
        await driver.aclose()


async def test_a_brain_that_claims_the_mail_without_a_card_fails(tmp_path, monkeypatch):
    reasons, _ = await run_s14(tmp_path, monkeypatch, claims=True)
    assert "connector_card" in reasons


async def test_a_forged_callback_never_connects_and_fails(tmp_path, monkeypatch):
    browser = GoogleFixture.browser

    async def forged(self, url):
        if self.mode == "approve":
            self.mode = "forged"
        await browser(self, url)
    monkeypatch.setattr(GoogleFixture, "browser", forged)
    reasons, driver = await run_s14(tmp_path, monkeypatch)
    assert "auth_callback" in reasons and "resume_once" in reasons
    assert not driver.google.reads()


async def test_a_task_resumed_twice_fails(tmp_path, monkeypatch):
    monkeypatch.setitem(cards.MOVES, "connected", ("authorizing",))
    monkeypatch.setattr(CardStore, "claim_resume", lambda self, turn_id: True)
    reasons, _ = await run_s14(tmp_path, monkeypatch)
    assert "resume_once" in reasons


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_a_resumed_answer_made_up_without_reading_gmail_fails(tmp_path, monkeypatch, brain):
    reasons, driver = await run_s14(tmp_path, monkeypatch, brain, made_up=True)
    assert "tool_receipt" in reasons and not driver.google.reads()


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_the_resume_does_not_redo_what_the_task_did_before_its_card(tmp_path, monkeypatch, brain):
    # The task sends the synthetic mail, then needs Gmail. Resumed, it tries the very same send again first:
    # that call is refused, and only the read is made.
    driver, _, _ = setup(tmp_path, monkeypatch, brain, write=True)
    try:
        await driver.install_fixture(write=True)
        reasons, _ = await driver.execute(CELL)
        assert not reasons, reasons
        assert [receipt["operation"] for receipt in driver.recorder.receipts] == ["write"]
        assert "gmail_get_message" in driver.google.reads()
        assert any(trace["name"].endswith("__write") or trace["name"].endswith("/write") for trace in driver.traces)
    finally:
        await driver.aclose()


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_a_turn_that_shows_a_card_calls_nothing_else(tmp_path, monkeypatch, brain):
    driver, _, _ = setup(tmp_path, monkeypatch, brain, after_card=True)
    try:
        await driver.install_fixture(write=True)
        reasons, _ = await driver.execute(CELL)
        assert not reasons, reasons
        assert driver.recorder.receipts == []
    finally:
        await driver.aclose()
