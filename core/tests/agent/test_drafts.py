"""The email draft of a private chat (P08c): a version and its digest, edited by the owner, sent once with the digest of
the version on the card, discarded with no effect, handed to a colleague without being sent, and refused wherever it
is not the owner's own chat.

The mail app is a real connection over an in-memory server that notes what it was asked to send; the model is a fake.
No test sends a mail or touches the network."""

import json

import pytest
from mcp.server.mcpserver import MCPServer

from raphael_core.agent.drafts import (
    DraftError,
    DraftStore,
    EmailDrafts,
    draft_digest,
    is_mail_sender,
    mail_arguments,
)
from raphael_core.agent.gate import ToolGate
from raphael_core.agent.history import AgentHistory
from raphael_core.agent.ledger import Ledger
from raphael_core.agent.plugins import PluginTool
from raphael_core.agent.policy import Confirmations, Tier
from raphael_core.agent.safety import DENY, SafetyGuard, SafetyJudge, SafetyRules, narrow_scope
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn, Decision
from raphael_core.brain.spend import SpendTracker
from raphael_core.protocol import RpcError
from raphael_core.router import Router

from .conftest import FakeOpenRouter, decision, noul
from .test_plugins import calls, plugins  # noqa: F401
from .test_service import make_rig  # noqa: F401

SENT: list[dict] = []
FAILING: list[bool] = []
NAMES = {0: "拉斐爾", 1: "佳佳", 2: "焦媽媽"}


def mail_server(*, copies=True):
    server = MCPServer("mail")
    if copies:
        @server.tool()
        def send_email(to: str, subject: str, body: str, cc: str = "") -> str:
            """Send an email."""
            if FAILING:
                raise RuntimeError("the mail app is down")
            SENT.append({"to": to, "cc": cc, "subject": subject, "body": body})
            return "sent"
    else:
        @server.tool()
        def send_email(to: str, subject: str, body: str) -> str:
            """Send an email."""
            if FAILING:
                raise RuntimeError("the mail app is down")
            SENT.append({"to": to, "subject": subject, "body": body})
            return "sent"

    @server.tool()
    def create_draft(to: str, subject: str, body: str) -> str:
        """Keep an email as a draft in the mailbox. Sends nothing."""
        return "kept"

    @server.tool()
    def read_inbox() -> str:
        """Read the inbox."""
        return "nothing"

    return server


async def connect_mail(plugins, name="Mail", **kwargs):
    plugins.servers.by_name[name] = mail_server(**kwargs)
    added = await plugins.rpc.call("integrations.add", {"name": name, "transport": "stdio", "command": "unused"})
    server_id = added["server"]["id"]
    await plugins.rpc.call("integrations.set_enabled", {"id": server_id, "enabled": True})
    await plugins.rpc.call("integrations.refresh", {"id": server_id})
    return server_id


@pytest.fixture(autouse=True)
def sent():
    SENT.clear()
    yield SENT
    SENT.clear()


def tool(name, **properties):
    schema = {"type": "object", "properties": {key: {"type": kind} for key, kind in properties.items()}}
    return PluginTool(f"mcp:s/{name}", "Mail", name, "", schema, Tier.T1)


# --- which tool sends mail, and what it is given --------------------------------------------------------------


def test_a_tool_that_sends_mail_is_found_by_its_name_and_its_input():
    assert is_mail_sender(tool("send_email", to="string", subject="string", body="string"))
    assert is_mail_sender(tool("gmail_send", to="string", subject="string", body="string"))
    assert is_mail_sender(tool("send", recipient_email="string", subject="string", body="string"))
    assert not is_mail_sender(tool("gmail_create_draft", to="string", subject="string", body="string"))  # keeps, sends nothing
    assert not is_mail_sender(tool("search", q="string"))
    assert not is_mail_sender(tool("send_email", to="string", subject="string"))  # no body: not mail
    assert not is_mail_sender(tool("write", to="string", subject="string", body="string"))  # does not say it sends


def test_the_digest_covers_the_app_and_every_field_the_owner_sees():
    base = draft_digest("mcp:s/send", "a@b.co", "c@d.co", "Hi", "Body")
    for changed in (("mcp:t/send", "a@b.co", "c@d.co", "Hi", "Body"), ("mcp:s/send", "x@b.co", "c@d.co", "Hi", "Body"),
                    ("mcp:s/send", "a@b.co", "", "Hi", "Body"), ("mcp:s/send", "a@b.co", "c@d.co", "Hello", "Body"),
                    ("mcp:s/send", "a@b.co", "c@d.co", "Hi", "Body.")):
        assert draft_digest(*changed) != base
    assert draft_digest("mcp:s/send", "a@b.co", "c@d.co", "Hi", "Body") == base


def test_a_version_is_given_to_the_tool_whole_or_not_at_all():
    send = tool("send_email", to="string", cc="string", subject="string", body="string")
    assert mail_arguments(send, "a@b.co, e@f.co", "c@d.co", "Hi", "Body") == {
        "to": "a@b.co, e@f.co", "cc": "c@d.co", "subject": "Hi", "body": "Body"}
    assert mail_arguments(send, "a@b.co", "", "Hi", "Body") == {"to": "a@b.co", "subject": "Hi", "body": "Body"}
    without_copies = tool("send_email", to="string", subject="string", body="string")
    with pytest.raises(DraftError) as error:
        mail_arguments(without_copies, "a@b.co", "c@d.co", "Hi", "Body")  # a copy is never dropped without a word
    assert error.value.kind == "cc_unsupported"
    for bad in ("", "not an address", "a@b"):
        with pytest.raises(DraftError) as error:
            mail_arguments(send, bad, "", "Hi", "Body")
        assert error.value.kind == "invalid_recipient"
    with pytest.raises(DraftError) as error:
        mail_arguments(send, "a@b.co", "", "Hi", "  ")
    assert error.value.kind == "empty"


# --- the store: versions, one claim, one discard ----------------------------------------------------------------


@pytest.fixture
def store(conn):
    return DraftStore(conn, lambda: 5)


def turn_in(conn, member_id=0):
    return AgentHistory(conn).add(session_id="s", question="q", route="answer", prefix="", text="", status="done",
                                  source="app", member_id=member_id)


def make(store, conn, **overrides):
    fields = dict(author_id=0, turn_id=turn_in(conn), conversation_id=None, language="zh-TW", send_via="mcp:s/send_email",
                  to="a@b.co", cc="", subject="回信", body="謝謝你的來信。", offer_to=None)
    fields.update(overrides)
    return store.create(**fields)


def test_a_draft_starts_as_version_one_with_the_digest_of_what_the_owner_sees(store, conn):
    draft = make(store, conn)
    assert (draft.version, draft.state, draft.holder_id, draft.offer_state) == (1, "draft", 0, "")
    assert draft.digest == draft_digest("mcp:s/send_email", "a@b.co", "", "回信", "謝謝你的來信。")
    assert store.versions(draft.id) == [{"version": 1, "digest": draft.digest, "by_owner": False}]


def test_every_edit_is_a_new_version_and_an_edit_on_an_old_digest_is_refused(store, conn):
    draft = make(store, conn)
    second = store.revise(draft, send_via=draft.send_via, to="x@y.co", cc="", subject="回信", body=draft.body, by_owner=True)
    assert (second.version, second.digest != draft.digest, second.to) == (2, True, "x@y.co")
    stale = store.revise(draft, send_via=draft.send_via, to="z@y.co", cc="", subject="回信", body=draft.body, by_owner=True,
                         base_digest=draft.digest)
    assert stale is None and store.get(draft.id).version == 2  # the card of version 1 is not the draft any more
    assert [v["by_owner"] for v in store.versions(draft.id)] == [False, True]


def test_the_one_claim_goes_to_the_digest_of_the_current_version_and_to_one_taker(store, conn):
    draft = make(store, conn)
    second = store.revise(draft, send_via=draft.send_via, to="x@y.co", cc="", subject="回信", body=draft.body, by_owner=True)
    assert not store.claim_send(draft.id, draft.digest)  # an old digest never sends
    assert store.claim_send(draft.id, second.digest)
    assert not store.claim_send(draft.id, second.digest)  # nor a second click
    assert store.get(draft.id).state == "sending" and not store.discard(draft.id)  # a send under way is not discarded
    store.finish(draft.id, second.digest, ok=True)
    after = store.get(draft.id)
    assert (after.state, after.sent_digest) == ("sent", second.digest)
    assert store.revise(after, send_via="", to="", cc="", subject="", body="", by_owner=True) is None  # a sent draft is not edited
    assert not store.claim_send(draft.id, second.digest)


def test_a_send_that_was_under_way_when_the_core_ended_is_never_sent_again(store, conn):
    draft = make(store, conn)
    assert store.claim_send(draft.id, draft.digest)
    restarted = DraftStore(conn, lambda: 9)
    after = restarted.get(draft.id)
    assert (after.state, after.error) == ("failed", "interrupted")
    assert not restarted.claim_send(draft.id, draft.digest)  # an unknown outcome is not tried again by itself
    again = restarted.revise(after, send_via=after.send_via, to=after.to, cc="", subject="回信！", body=after.body, by_owner=True)
    assert again.state == "draft" and again.version == 2  # the owner's edit is a new version, which they can send


def test_discarding_is_once_and_only_for_a_draft_that_is_open(store, conn):
    draft = make(store, conn)
    assert store.discard(draft.id) and not store.discard(draft.id)
    assert store.get(draft.id).state == "discarded" and not store.claim_send(draft.id, draft.digest)


def test_the_offer_to_a_colleague_is_answered_once(store, conn):
    draft = make(store, conn, offer_to=2)
    assert (draft.offer_state, draft.offer_to) == ("pending", 2)
    carrier = turn_in(conn, member_id=2)
    assert store.answer_offer(draft.id, True, turn_id=carrier, conversation_id=7)
    assert not store.answer_offer(draft.id, True, turn_id=carrier, conversation_id=7)
    moved = store.get(draft.id)
    assert (moved.holder_id, moved.turn_id, moved.origin_turn_id != moved.turn_id, moved.offer_state) == (2, carrier, True, "")
    declined = make(store, conn, offer_to=2)
    assert store.answer_offer(declined.id, False, turn_id=None, conversation_id=None)
    assert (store.get(declined.id).holder_id, store.get(declined.id).offer_state) == (0, "declined")


# --- the card's methods against the gate and a mail app ---------------------------------------------------------------


class Judge(FakeOpenRouter):
    async def decide(self, **kwargs):
        if self.decisions:
            return await super().decide(**kwargs)
        self.decide_calls.append(kwargs)
        return Decision(answers={key: noul(0.0) for key in kwargs["questions"]}, cost_usd=0, latency_ms=1)


class Desk:
    def __init__(self, conn, plugins):
        self.conn, self.plugins = conn, plugins
        self.rules, self.judge = SafetyRules(conn), Judge()
        self.gate = ToolGate(plugins.hub, conn=conn, safety=SafetyGuard(self.rules, SafetyJudge(self.judge)))
        self.confirmations = Confirmations()
        self.events = []
        self.drafts = EmailDrafts(conn, plugins.hub, self.gate, notify=lambda method, params: self.events.append((method, params)))
        self.drafts.approve = self.approve
        self.drafts.ledger = lambda: Ledger(SpendTracker(conn))
        self.drafts.names = lambda member_id: NAMES.get(member_id, "")
        self.drafts.colleague = lambda name: next((member for member, known in NAMES.items() if known == name), None)
        self.router = Router()
        self.drafts.register(self.router)
        self.approvals = 0
        for member_id in (1, 2):
            conn.execute("INSERT OR IGNORE INTO members(id, name, position, created_at_ms) VALUES (?, ?, ?, 1)",
                         (member_id, NAMES[member_id], member_id))

    def approve(self, action, turn_id, language):
        self.approvals += 1
        pending = self.confirmations.open("card", turn_id, action, Tier.T2, language)
        return self.confirmations.resolve(pending.id, True, pending.digest).approval

    def write(self, *, to="a@b.co", cc="", subject="回信", body="謝謝你的來信。", via=None, hand_to=None, revise=False, author=0, turn=None):
        turn = turn or turn_in(self.conn, author)
        draft, offered = self.drafts.write(author_id=author, turn_id=turn, conversation_id=None, language="zh-TW", to=to, cc=cc,
                                           subject=subject, body=body, via=via, hand_to=hand_to, revise=revise)
        return draft, offered, turn

    async def call(self, method, **params):
        return await self.router.call("integrations." + method, params)


@pytest.fixture
async def desk(conn, plugins):
    await connect_mail(plugins)
    return Desk(conn, plugins)


async def test_a_draft_is_shown_as_a_card_with_the_app_that_will_send_it(desk, conn):
    draft, offered, turn = desk.write(cc="c@d.co")
    [card] = desk.drafts.views([turn])[turn]
    assert (card["mode"], card["state"], card["version"], card["digest"]) == ("card", "draft", 1, draft.digest)
    assert (card["to"], card["cc"], card["subject"], card["body"]) == ("a@b.co", "c@d.co", "回信", "謝謝你的來信。")
    assert card["from"]["name"] == "Mail" and card["from"]["key"].endswith("/send_email") and card["senders"] == [card["from"]]
    assert card["offer"] is None and offered == "" and card["error"] is None
    assert SENT == []  # forming a draft sends nothing


async def test_sending_sends_the_version_on_the_card_once_through_the_gate(desk, plugins):
    draft, _, turn = desk.write(cc="c@d.co")
    result = await desk.call("draft_send", id=draft.id, digest=draft.digest)
    assert result["sent"] and result["draft"]["state"] == "sent"
    assert SENT == [{"to": "a@b.co", "cc": "c@d.co", "subject": "回信", "body": "謝謝你的來信。"}]
    assert plugins.audit()[-1][1:] == ("send_email", None, "user", "ok")
    # A second click, a replay and a second card send nothing.
    for _ in range(2):
        again = await desk.call("draft_send", id=draft.id, digest=draft.digest)
        assert not again["sent"] and again["draft"]["state"] == "sent"
    assert len(SENT) == 1 and desk.approvals == 1


async def test_an_edit_makes_a_new_version_and_the_old_digest_never_sends(desk):
    draft, _, turn = desk.write()
    saved = await desk.call("draft_save", id=draft.id, digest=draft.digest, to="x@y.co", subject="新的主旨", body="改過的內文。")
    new = saved["draft"]
    assert saved["saved"] and (new["version"], new["to"], new["subject"]) == (2, "x@y.co", "新的主旨") and new["digest"] != draft.digest
    stale = await desk.call("draft_send", id=draft.id, digest=draft.digest)  # the card of version 1
    assert not stale["sent"] and SENT == []
    again = await desk.call("draft_save", id=draft.id, digest=draft.digest, to="z@y.co")  # an edit on version 1
    assert not again["saved"] and again["draft"]["to"] == "x@y.co"
    sent = await desk.call("draft_send", id=draft.id, digest=new["digest"])
    assert sent["sent"] and SENT == [{"to": "x@y.co", "cc": "", "subject": "新的主旨", "body": "改過的內文。"}]


async def test_an_edit_that_changes_nothing_makes_no_version(desk):
    draft, _, _ = desk.write()
    same = await desk.call("draft_save", id=draft.id, digest=draft.digest, to=draft.to, subject=draft.subject, body=draft.body)
    assert not same["saved"] and same["draft"]["version"] == 1


async def test_choosing_another_app_to_send_with_is_an_edit_of_the_card(desk, plugins):
    await connect_mail(plugins, name="Other")
    draft, _, _ = desk.write(via="Mail")
    options = (await desk.call("draft_save", id=draft.id, digest=draft.digest))["draft"]["senders"]
    other = next(sender for sender in options if sender["name"] == "Other")
    saved = await desk.call("draft_save", id=draft.id, digest=draft.digest, via=other["key"])
    assert saved["saved"] and saved["draft"]["from"]["name"] == "Other" and saved["draft"]["digest"] != draft.digest
    with pytest.raises(RpcError):
        await desk.call("draft_save", id=draft.id, digest=saved["draft"]["digest"], via="mcp:nobody/send_email")


async def test_discarding_changes_nothing_outside(desk, plugins):
    draft, _, _ = desk.write()
    before = plugins.audit()
    done = await desk.call("draft_discard", id=draft.id)
    assert done["discarded"] and done["draft"]["state"] == "discarded"
    assert SENT == [] and plugins.audit() == before and desk.approvals == 0  # no call, no gate, no approval, no receipt
    later = await desk.call("draft_send", id=draft.id, digest=draft.digest)
    assert not later["sent"] and SENT == []
    assert not (await desk.call("draft_discard", id=draft.id))["discarded"]
    assert not (await desk.call("draft_save", id=draft.id, digest=draft.digest, subject="x"))["saved"]


async def test_handing_a_draft_over_moves_the_same_version_to_the_colleague_and_sends_nothing(desk, conn):
    draft, offered, turn = desk.write(hand_to="焦媽媽", author=1)
    assert offered == "焦媽媽"
    [asking] = desk.drafts.views([turn])[turn]
    assert asking["offer"] == {"member_id": 2, "name": "焦媽媽"} and asking["state"] == "draft" and asking["author_name"] == ""
    no = await desk.call("draft_handover", id=draft.id, choice="no")
    assert not no["handed"] and no["draft"]["offer"] is None and desk.drafts.store.get(draft.id).holder_id == 1
    # (a new offer, and this time the owner says yes)
    draft, _, turn = desk.write(hand_to="焦媽媽", author=1, subject="第二封")
    yes = await desk.call("draft_handover", id=draft.id, choice="yes")
    assert yes["handed"] and SENT == [] and desk.approvals == 0
    moved = desk.drafts.store.get(draft.id)
    assert (moved.holder_id, moved.author_id, moved.version, moved.digest, moved.state) == (2, 1, 1, draft.digest, "draft")
    carrier = conn.execute("SELECT member_id, source, route, scope FROM agent_turns WHERE id=?", (moved.turn_id,)).fetchone()
    assert tuple(carrier) == (2, "draft", "draft", "private")
    # The first chat keeps one centred line; the card, editable and the same version, is in the colleague's chat.
    assert desk.drafts.views([turn])[turn] == [{"id": draft.id, "mode": "handed", "state": "draft", "holder_id": 2, "holder_name": "焦媽媽"}]
    [card] = desk.drafts.views([moved.turn_id])[moved.turn_id]
    assert (card["mode"], card["version"], card["digest"], card["holder_id"]) == ("card", 1, draft.digest, 2)
    assert card["author_name"] == "佳佳"  # the colleague's card says whose draft it is (the author's own, above, says nothing)
    # Answering the choice again changes nothing, and it never sends.
    assert not (await desk.call("draft_handover", id=draft.id, choice="yes"))["handed"]
    assert SENT == []
    # The colleague's owner edits and sends it, once, with the digest of the version on the card.
    edited = (await desk.call("draft_save", id=draft.id, digest=draft.digest, body="由焦媽媽寄出。"))["draft"]
    assert (await desk.call("draft_send", id=draft.id, digest=edited["digest"]))["sent"] and len(SENT) == 1
    assert desk.drafts.views([turn])[turn][0]["state"] == "sent"


async def test_a_draft_is_only_handed_to_a_colleague_who_is_one(desk):
    from raphael_core.agent.drafts import DraftError as Error

    with pytest.raises(Error) as unknown:
        desk.write(hand_to="沒有這個人", author=1)
    assert unknown.value.kind == "unknown_member"
    with pytest.raises(Error):
        desk.write(hand_to="佳佳", author=1)  # not to itself


async def test_a_never_that_forbids_sending_refuses_it_and_the_draft_stays_open(desk, plugins):
    draft, _, _ = desk.write()
    server = next(tool for tool in plugins.hub.offered() if tool.name == "send_email")
    rule = desk.rules.always(narrow_scope(server.key), "Mail：send_email", "zh-TW")
    desk.rules.update(rule.id, verdict=DENY)
    result = await desk.call("draft_send", id=draft.id, digest=draft.digest)
    assert not result["sent"] and result["draft"]["state"] == "draft" and result["draft"]["error"] == "forbidden"
    assert SENT == [] and desk.approvals == 0
    desk.rules.delete(rule.id)
    assert (await desk.call("draft_send", id=draft.id, digest=draft.digest))["sent"] and len(SENT) == 1


async def test_ask_first_is_answered_by_the_click_on_that_very_version(desk, plugins):
    desk.rules.ensure_seeded("zh-TW")
    asking = next(rule for rule in desk.rules.rules() if rule.action == "寄信或傳訊息給別人")
    desk.judge.decisions.append(lambda **kwargs: Decision(
        answers={key: noul(0.93 if key == f"rule_{asking.id}" else 0.0) for key in kwargs["questions"]}, cost_usd=0, latency_ms=1))
    draft, _, _ = desk.write()
    result = await desk.call("draft_send", id=draft.id, digest=draft.digest)
    assert result["sent"] and desk.approvals == 1 and len(SENT) == 1  # the owner pressed 傳送: nobody asks again


async def test_a_version_that_cannot_be_carried_is_not_sent_and_says_why(desk, plugins):
    draft, _, _ = desk.write(to="not an address")
    result = await desk.call("draft_send", id=draft.id, digest=draft.digest)
    assert not result["sent"] and result["draft"]["error"] == "invalid_recipient" and result["draft"]["state"] == "draft"
    fixed = (await desk.call("draft_save", id=draft.id, digest=draft.digest, to="a@b.co"))["draft"]
    assert fixed["error"] is None and (await desk.call("draft_send", id=draft.id, digest=fixed["digest"]))["sent"]
    assert len(SENT) == 1


async def test_with_no_app_that_sends_mail_the_draft_waits_and_says_so(conn, plugins):
    desk = Desk(conn, plugins)  # nothing connected
    draft, _, turn = desk.write()
    assert draft.send_via == "" and desk.drafts.views([turn])[turn][0]["senders"] == []
    result = await desk.call("draft_send", id=draft.id, digest=draft.digest)
    assert not result["sent"] and result["draft"]["error"] == "no_sender" and SENT == []


async def test_a_mail_app_turned_off_after_the_draft_was_written_sends_nothing(desk, plugins):
    draft, _, _ = desk.write()
    server = next(tool for tool in plugins.hub.offered() if tool.name == "send_email")
    plugins.hub._config.get(server.key.removeprefix("mcp:").split("/")[0]).disabled_tools.add("send_email")
    result = await desk.call("draft_send", id=draft.id, digest=draft.digest)
    assert not result["sent"] and result["draft"]["error"] == "no_sender" and SENT == []


async def test_a_mail_app_that_fails_leaves_a_failed_draft_that_is_not_sent_again_by_itself(desk):
    draft, _, _ = desk.write()
    FAILING.append(True)
    try:
        result = await desk.call("draft_send", id=draft.id, digest=draft.digest)
    finally:
        FAILING.clear()
    assert not result["sent"] and result["draft"]["state"] == "failed" and result["draft"]["error"] == "tool_error"
    # Its outcome is not known to be "not sent": the same version is not sent again by a click or a replay ...
    again = await desk.call("draft_send", id=draft.id, digest=draft.digest)
    assert not again["sent"] and SENT == []
    # ... and the owner who edits it has a new version, which they can send.
    edited = (await desk.call("draft_save", id=draft.id, digest=draft.digest, body="再試一次。"))["draft"]
    assert edited["state"] == "draft" and edited["error"] is None
    assert (await desk.call("draft_send", id=draft.id, digest=edited["digest"]))["sent"] and len(SENT) == 1


async def test_unknown_drafts_and_bad_params_are_refused(desk):
    for method in ("draft_save", "draft_send", "draft_discard", "draft_handover"):
        for params in ({}, {"id": 3}, {"id": "missing"}):
            with pytest.raises(RpcError):
                await desk.call(method, **params)
    draft, _, _ = desk.write()
    with pytest.raises(RpcError):
        await desk.call("draft_send", id=draft.id)  # no digest: it would send nothing in particular
    with pytest.raises(RpcError):
        await desk.call("draft_handover", id=draft.id, choice="maybe")
    with pytest.raises(RpcError):
        await desk.call("draft_save", id=draft.id, digest=draft.digest, subject="x" * 400)


async def test_a_revision_by_the_member_is_a_new_version_on_the_card_in_the_newest_turn(desk):
    first, _, turn = desk.write(subject="回信")
    second, _, newer = desk.write(subject="回信（更短）", revise=True)
    assert second.id == first.id and second.version == 2 and second.turn_id == newer and second.digest != first.digest
    assert desk.drafts.views([turn]) == {}  # the card moved to the newest turn; the old one keeps nothing
    assert [v["by_owner"] for v in desk.drafts.store.versions(first.id)] == [False, False]
    unrelated, _, _ = desk.write(revise=False, subject="另一封")
    assert unrelated.id != first.id


async def test_the_drafts_of_a_deleted_turn_go_with_it(desk, conn):
    draft, _, turn = desk.write()
    AgentHistory(conn).delete(turn)
    assert desk.drafts.store.get(draft.id) is None
    assert conn.execute("SELECT COUNT(*) FROM email_draft_versions").fetchone()[0] == 0
