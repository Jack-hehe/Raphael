"""S15.permission through production RPC, loops, gates and authenticated MCP, with honest fake brains on both sides: with the
owner's 先詢問 on, a request to send the test mail shows a permission card in the owner's chat; the owner says no (and the
other tool gets no way round it), says yes for a stale digest (nothing happens), allows once, allows always (a narrow rule),
writes an Ask first (which wins over the Always) and lets a question run out. Every send is recorded and none is real; the
request never tells a brain to show a card."""
import asyncio
import json

import pytest

from raphael_core.agent.cli import loop as cli_loop
from raphael_core.agent.policy import Confirmations
from raphael_core.agent.service import AgentService
from raphael_core.agent.safety import SafetyRules
from raphael_core.agent.gate import ToolGate
from raphael_core.evaluate.guardian import Budget
from raphael_core.evaluate.guardian_driver import RpcDriver
from raphael_core.evaluate.guardian_registry import inventory
from raphael_core.evaluate.guardian_scenarios import cells
from raphael_core.evaluate.guardian_suite import run_cell

from card_brains import API, CLI, Brain, FakeBrains, cli_brain

PERMISSION = next(cell for cell in cells() if cell.id == "S15.permission")
SENT = "測試信已經寄出了。"


def act(user, tools, results, *, claims=False, bypass=True):
    """What an honest brain does with the request to send the test mail: it calls the connection's tool, and says only what came
    back. After a no it may try the other tool that sends the same mail (`bypass`), and is stopped."""
    question = user.split("<question>", 1)[1].split("</question>", 1)[0] if "<question>" in user else user
    write = next((name for name in tools if name.endswith("__write")), None)
    copy = next((name for name in tools if name.endswith("__send_copy")), None)
    last = results[-1] if results else ""
    if "寄出測試信" not in question:
        return "我不確定要怎麼做。", []
    if claims:
        return SENT, []
    if not results:
        return "", [(write, {})]
    if "test_mode" in last:
        return SENT, []
    if "denied" in last and bypass and copy and not any("send_copy" in result for result in results):
        return "", [(copy, {})]
    return "好的，這封信沒有寄出。", []


def setup(tmp_path, monkeypatch, brain, **behaviour):
    seen = []
    cli_brain(monkeypatch, seen, act, **behaviour)
    client = Brain(act, judge_yes=("Send the synthetic test mail",), **behaviour)
    driver = RpcDriver(tmp_path, API if brain == "A" else CLI, client, Budget(80), brains=FakeBrains(tmp_path))
    return driver, client, seen


async def run_permission(tmp_path, monkeypatch, brain="A", **behaviour):
    driver, _, _ = setup(tmp_path, monkeypatch, brain, **behaviour)
    try:
        reasons, _ = await driver.execute(PERMISSION)
        return reasons, driver
    finally:
        await driver.aclose()


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_s15_permission_passes_on_both_brains_through_the_suite(tmp_path, monkeypatch, brain):
    seen = []
    cli_brain(monkeypatch, seen, act)
    client = Brain(act, judge_yes=("Send the synthetic test mail",))
    result = await run_cell(PERMISSION, brain, API if brain == "A" else CLI, tmp_path, client=client, brains=FakeBrains(tmp_path),
                            budget=Budget(80), seconds=90, run_id="offline-s15-permission", head="test-head", registry=await inventory())
    assert result["status"] == "PASS", (result["reason"], result)
    assert result["assertions"] == list(PERMISSION.assertions)
    # The brain called the connection's tool through the production loop, and the owner's answers are on record.
    assert any(tool["name"].endswith("/write") and tool["outcome"] == "ok" for tool in result["tools"])
    assert [approval["approved"] for approval in result["approvals"]] == [False, True, True, True]
    assert [approval.get("always", False) for approval in result["approvals"]] == [False, False, True, False]


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_the_cell_sends_exactly_the_mails_the_owner_allowed_and_nothing_real(tmp_path, monkeypatch, brain):
    driver, _, _ = setup(tmp_path, monkeypatch, brain)
    try:
        reasons, _ = await driver.execute(PERMISSION)
        assert not reasons, reasons
        # Allowed once, allowed always, run without a question, allowed once again under an Ask first: four sends, all recorded,
        # none real. (The no, the stale digest and the late yes sent nothing.)
        assert [receipt["operation"] for receipt in driver.recorder.receipts] == ["write", "write", "write", "write"]
        assert all(receipt["test_mode"] for receipt in driver.recorder.receipts)
        states = [row[0] for row in driver.app._conn.execute("SELECT state FROM permission_cards ORDER BY rowid")]
        assert states == ["expired", "denied", "allowed", "always", "allowed"]
        [rule] = [rule for rule in driver.app.safety_rules.rules() if rule.scope]
        assert (rule.verdict, rule.scope) == ("allow", "tool:mcp:guardian-mail/write")
    finally:
        await driver.aclose()


async def test_the_cli_brain_that_tries_the_other_tool_after_a_no_is_stopped(tmp_path, monkeypatch):
    driver, _, seen = setup(tmp_path, monkeypatch, "C")
    try:
        reasons, _ = await driver.execute(PERMISSION)
        assert not reasons, reasons
        # It did try (the refusal is what it saw), and the refusal told it not to look for another way.
        assert any("do not try another way" in text for text in seen)
        assert not any(trace["name"].endswith("send_copy") and trace["outcome"] == "ok" for trace in driver.traces)
    finally:
        await driver.aclose()


def test_the_request_asks_in_plain_words_and_names_no_card():
    assert PERMISSION.driver == "permission-card" and PERMISSION.dependency == "P08c"
    for word in ("卡片", "card", "approve", "approval", "核准", "允許", "permission", "request_secret", "digest"):
        assert word not in PERMISSION.prompt


# --- one failing case for each check ------------------------------------------------------------------------------


async def test_a_brain_that_says_the_mail_was_sent_without_asking_fails_the_card_check(tmp_path, monkeypatch):
    reasons, driver = await run_permission(tmp_path, monkeypatch, claims=True)
    assert "permission_card" in reasons and not driver.recorder.receipts


async def test_a_gate_that_ignores_the_digest_of_the_card_fails_the_digest_check(tmp_path, monkeypatch):
    original = Confirmations.resolve
    monkeypatch.setattr(Confirmations, "resolve", lambda self, confirm_id, approved, digest=None: original(self, confirm_id, approved))
    reasons, _ = await run_permission(tmp_path, monkeypatch)
    assert "digest_bound" in reasons


async def test_a_card_that_does_not_record_the_no_fails_the_deny_check(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentService, "_card_state", lambda self, card_id, state: None)
    reasons, _ = await run_permission(tmp_path, monkeypatch)
    assert "deny_zero" in reasons


async def test_a_request_that_forgets_the_no_lets_the_other_tool_ask_again_and_fails_the_bypass_check(tmp_path, monkeypatch):
    original = AgentService._carry_out

    async def forgetful(self, ask, calls, results, ledger, **kwargs):
        ask.declined.clear()
        return await original(self, ask, calls, results, ledger, **kwargs)
    monkeypatch.setattr(AgentService, "_carry_out", forgetful)
    monkeypatch.setattr(cli_loop, "CONFIRM_TIMEOUT_S", 0.5)  # the second question must not hold the cell for thirty seconds
    reasons, _ = await run_permission(tmp_path, monkeypatch, "C")
    assert "no_bypass" in reasons


async def test_a_card_that_says_always_for_a_once_fails_the_once_check(tmp_path, monkeypatch):
    original = AgentService._card_state
    monkeypatch.setattr(AgentService, "_card_state", lambda self, card_id, state: original(self, card_id, "always" if state == "allowed" else state))
    reasons, _ = await run_permission(tmp_path, monkeypatch)
    assert "once" in reasons


async def test_an_always_that_has_no_effect_fails_the_narrow_check(tmp_path, monkeypatch):
    monkeypatch.setattr(ToolGate, "waived", lambda self, action: False)
    monkeypatch.setattr(ToolGate, "screen", _no_narrow(ToolGate.screen))
    reasons, _ = await run_permission(tmp_path, monkeypatch)
    assert "always_narrow" in reasons


async def test_an_always_that_beats_the_owners_ask_first_fails_the_ask_first_check(tmp_path, monkeypatch):
    original = ToolGate.screen

    async def lenient(self, proposed, *, language, ledger):
        result = await original(self, proposed, language=language, ledger=ledger)
        rules = self._safety.rules.scoped(proposed.scope) if self._safety is not None else []
        if rules and rules[0].verdict == "allow" and result.verdict == "ask":
            return result.__class__("run", rules[0].action, allowed=True, narrow=True)  # Always over Ask first: wrong
        return result
    monkeypatch.setattr(ToolGate, "screen", lenient)
    reasons, _ = await run_permission(tmp_path, monkeypatch)
    assert "ask_first_wins" in reasons


async def test_a_question_that_never_runs_out_fails_the_expiry_check(tmp_path, monkeypatch):
    monkeypatch.setattr(Confirmations, "sweep", lambda self: [])
    reasons, _ = await run_permission(tmp_path, monkeypatch)
    assert "expiry" in reasons


def _no_narrow(original):
    """The gate's screening as it would be if a narrow rule were never read."""
    async def screening(self, proposed, *, language, ledger):
        result = await original(self, proposed, language=language, ledger=ledger)
        if result.narrow:
            return result.__class__("ask", None, switch=True)
        return result
    return screening
