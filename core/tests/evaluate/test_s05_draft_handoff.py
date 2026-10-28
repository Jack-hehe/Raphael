"""S05.draft-handoff through production RPC, loops, gates and authenticated MCP, with honest fake brains on both sides: the
owner asks 信件助理 to write a reply and give it to 行銷 to send; the draft is an editable card in the owner's chat with a
choice card for the hand-over; the owner says no and discards one draft (nothing is sent, and it can not be sent afterwards),
says yes to the other (the same version, editable, in 行銷's chat; nothing is sent), edits it (a new version; the old digest
sends nothing) and presses 傳送 once. Every send is recorded and none is real; the request never tells a brain to show a card."""
import re

import pytest

from raphael_core.agent import drafts as drafts_module
from raphael_core.agent.drafts import DraftStore, EmailDrafts
from raphael_core.evaluate.guardian import Budget
from raphael_core.evaluate.guardian_driver import RpcDriver
from raphael_core.evaluate.guardian_registry import inventory
from raphael_core.evaluate.guardian_scenarios import cells
from raphael_core.evaluate.guardian_suite import run_cell

from card_brains import API, CLI, Brain, FakeBrains, cli_brain, question_of

DRAFT = next(cell for cell in cells() if cell.id == "S05.draft-handoff")
ADDRESS = re.compile(r"[\w.-]+@example\.com")
SUBJECT = re.compile(r"「([^」]+)」")
# What a request must not say: the owner asks in plain words, and the brain finds the tool that does it.
CARD_WORDS = ("卡片", "card", "email_draft", "草稿", "digest", "傳送", "approve")


def act(user, tools, results, *, claims=False):
    """What an honest brain does with the request to write a reply and give it to 行銷: it writes the draft with the tool that
    does that (offering it to 行銷), and says only what came back. It never says the mail was sent."""
    question = question_of(user)
    if "擬一封回信" not in question:
        return "我不確定要怎麼做。", []
    if claims:
        return "回信已經擬好，也已經寄出了。", []
    last = results[-1] if results else ""
    if not results:
        return "", [("email_draft", {"to": ADDRESS.search(question).group(), "subject": SUBJECT.search(question).group(1),
                                      "body": "您好，週四下午三點可以開會，期待與您見面。", "hand_to": "行銷"})]
    if "shown" in last:
        return "草稿放在這個對話裡了，請看一下，需要的話可以修改。", []
    return "我沒有辦法寫出這封草稿。", []


def setup(tmp_path, monkeypatch, brain, **behaviour):
    seen = []
    cli_brain(monkeypatch, seen, act, **behaviour)
    client = Brain(act, **behaviour)
    driver = RpcDriver(tmp_path, API if brain == "A" else CLI, client, Budget(80), brains=FakeBrains(tmp_path))
    return driver, client, seen


async def run_draft(tmp_path, monkeypatch, brain="A", **behaviour):
    driver, _, _ = setup(tmp_path, monkeypatch, brain, **behaviour)
    try:
        reasons, _ = await driver.execute(DRAFT)
        return reasons, driver
    finally:
        await driver.aclose()


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_s05_draft_handoff_passes_on_both_brains_through_the_suite(tmp_path, monkeypatch, brain):
    seen = []
    cli_brain(monkeypatch, seen, act)
    client = Brain(act)
    result = await run_cell(DRAFT, brain, API if brain == "A" else CLI, tmp_path, client=client, brains=FakeBrains(tmp_path),
                            budget=Budget(80), seconds=90, run_id="offline-s05-draft", head="test-head", registry=await inventory())
    assert result["status"] == "PASS", (result["reason"], result)
    assert result["assertions"] == list(DRAFT.assertions)
    # The brain wrote the drafts through the production loop; the only send is the owner's.
    assert any(tool["tool_key"] == "raphael/core/email_draft" and tool["outcome"] == "ok" for tool in result["tools"])


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_the_cell_sends_exactly_one_mail_with_the_owners_words_and_nothing_real(tmp_path, monkeypatch, brain):
    driver, _, _ = setup(tmp_path, monkeypatch, brain)
    try:
        reasons, _ = await driver.execute(DRAFT)
        assert not reasons, reasons
        [receipt] = driver.recorder.receipts
        assert receipt["operation"] == "send_email" and receipt["test_mode"]
        rows = driver.app._conn.execute("SELECT state, version, holder_id, author_id, offer_state, error FROM email_drafts"
                                        " ORDER BY created_at_ms, rowid").fetchall()
        # The first draft was discarded (its hand-over refused), the second was handed over, edited once and sent once.
        assert [(row["state"], row["version"], row["holder_id"] != row["author_id"]) for row in rows] == [
            ("discarded", 1, False), ("sent", 2, True)]
        assert all(row["error"] == "" for row in rows)
        versions = driver.app._conn.execute("SELECT version, by_owner FROM email_draft_versions ORDER BY draft_id, version").fetchall()
        assert sorted((row["version"], row["by_owner"]) for row in versions) == [(1, 0), (1, 0), (2, 1)]
        # The carrier of the handed draft is one turn in 行銷's own chat, and says nothing to anybody.
        carriers = driver.app._conn.execute("SELECT member_id, source, route, scope FROM agent_turns WHERE route='draft'").fetchall()
        assert [tuple(row) for row in carriers] == [(rows[1]["holder_id"], "draft", "draft", "private")]
    finally:
        await driver.aclose()


async def test_no_request_tells_a_brain_to_show_a_card_or_name_a_tool(tmp_path, monkeypatch):
    driver, client, seen = setup(tmp_path, monkeypatch, "A")
    try:
        await driver.execute(DRAFT)
        asked = [question_of(str(request["messages"][1]["content"])) for request in client.requests]
        assert any("擬一封回信" in text for text in asked)
        for text in [*asked, DRAFT.prompt]:
            for word in CARD_WORDS:
                assert word not in text, (word, text)
    finally:
        await driver.aclose()


def test_s05_has_a_driver_for_the_draft_and_stays_open_for_gmail_s_own_half():
    by_id = {cell.id: cell for cell in cells()}
    assert by_id["S05.draft-handoff"].driver == "draft-handoff" and by_id["S05.draft-handoff"].dependency == "P08c"
    # Gmail's own draft and send (P08d) has no driver yet, so S05 itself cannot pass on the generic half alone.
    assert by_id["S05.gmail"].driver == "" and by_id["S05.gmail"].dependency == "P08d"


# --- one failing case for each check ------------------------------------------------------------------------------


async def test_a_brain_that_says_the_reply_was_sent_without_a_draft_fails_the_editable_email_check(tmp_path, monkeypatch):
    reasons, driver = await run_draft(tmp_path, monkeypatch, claims=True)
    assert "editable_email" in reasons and not driver.recorder.receipts


async def test_a_digest_that_does_not_change_with_the_words_fails_the_version_bound_check(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts_module, "draft_digest", lambda *fields: "f" * 64)
    reasons, _ = await run_draft(tmp_path, monkeypatch)
    assert "version_bound_send" in reasons


async def test_a_discard_that_leaves_the_draft_open_fails_the_cancel_check(tmp_path, monkeypatch):
    monkeypatch.setattr(DraftStore, "discard", lambda self, draft_id: False)
    reasons, _ = await run_draft(tmp_path, monkeypatch)
    assert "cancel_zero_send" in reasons


async def test_a_send_that_forgets_it_sent_fails_the_one_receipt_check(tmp_path, monkeypatch):
    original = DraftStore.finish

    def forgetful(self, draft_id, digest, *, ok, error=""):
        original(self, draft_id, digest, ok=ok, error=error)
        with self._conn:
            self._conn.execute("UPDATE email_drafts SET state='draft' WHERE id=?", (draft_id,))  # open for a second send
    monkeypatch.setattr(DraftStore, "finish", forgetful)
    reasons, driver = await run_draft(tmp_path, monkeypatch)
    assert "one_receipt" in reasons and len(driver.recorder.receipts) == 2


async def test_a_hand_over_that_sends_fails_the_hand_over_check(tmp_path, monkeypatch):
    original = EmailDrafts._handover

    async def sends(self, params):
        result = await original(self, params)
        if result.get("handed"):
            draft = self.store.get(params["id"])
            await self._send({"id": draft.id, "digest": draft.digest})  # the hand-over sends: wrong
        return result
    monkeypatch.setattr(EmailDrafts, "_handover", sends)
    reasons, _ = await run_draft(tmp_path, monkeypatch)
    assert "handoff_never_sends" in reasons
