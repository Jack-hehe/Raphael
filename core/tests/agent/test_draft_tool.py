"""The member's side of an email draft (P08c): the tool `email_draft`, which writes the mail for the owner to send and cannot
send it, the draft-first preference, and the chats that are never shown a draft.

The model is a fake and the mail app is a real connection over an in-memory server; no test sends a mail."""

import json
from dataclasses import replace

import pytest

from raphael_core.agent.drafts import EmailDrafts
from raphael_core.agent.gate import ToolGate
from raphael_core.agent.harness_tools import HarnessToolError
from raphael_core.agent.permissions import PermissionCards
from raphael_core.agent.safety import SafetyGuard, SafetyJudge, SafetyRules
from raphael_core.agent.service import _Ask
from raphael_core.agent.speaker import RAPHAEL
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn, Decision

from .conftest import decision, noul
from .test_drafts import SENT, Judge, connect_mail, sent  # noqa: F401
from .test_plugins import calls, plugins  # noqa: F401
from .test_service import make_rig  # noqa: F401


def chat(text, *tool_calls):
    return ChatTurn(text, [ChatToolCall(str(n), name, args, json.dumps(args)) for n, (name, args) in enumerate(tool_calls)],
                    "fake/model", 0.3, 10, 10, 1)


def finished(text="好了。"):
    return chat(json.dumps({"prefix": "Answer", "text": text, "detail": ""}))


async def typed(rig, text="Reply to the mail", session="live"):
    return await rig.call("agent.ask", {"session_id": session, "text": text, "ui_language": "en", "source": "app"})


MAIL = {"to": "a@b.co", "subject": "Re: lunch", "body": "Thursday works."}


class Office:
    def __init__(self, conn, plugins, make_rig):
        self.conn = conn
        self.rules = SafetyRules(conn)
        self.judge = Judge()
        self.gate = ToolGate(plugins.hub, conn=conn, safety=SafetyGuard(self.rules, SafetyJudge(self.judge)))
        self.cards = PermissionCards(conn)
        self.drafts = EmailDrafts(conn, plugins.hub, self.gate)
        self.rig = make_rig(plugins=plugins.hub, gate=self.gate, permissions=self.cards, drafts=self.drafts)

    async def turn(self):
        turns = (await self.rig.call("agent.history.list", {"limit": 20}))["turns"]
        return turns[0]


@pytest.fixture
async def office(conn, plugins, make_rig):
    await connect_mail(plugins)
    return Office(conn, plugins, make_rig)


async def test_a_member_writes_the_mail_as_a_draft_and_cannot_send_it(office, client):
    client.decisions.append(decision("answer"))
    client.chats = [chat("I will write it.", ("email_draft", {**MAIL, "cc": "c@d.co"})), finished("The draft is waiting for you.")]
    result = await typed(office.rig)
    assert result["status"] == "done" and SENT == []
    turn = await office.turn()
    [card] = turn["drafts"]
    assert (card["mode"], card["state"], card["version"]) == ("card", "draft", 1)
    assert (card["to"], card["cc"], card["subject"], card["body"]) == ("a@b.co", "c@d.co", "Re: lunch", "Thursday works.")
    assert card["from"]["name"] == "Mail" and card["digest"]
    # The model was told what it cannot do and what to say, and was given no way to send.
    told = [m["content"] for m in client.chat_calls[1]["messages"] if m["role"] == "tool"][-1]
    assert "cannot send it" in told and "never say the mail was sent" in told
    assert "email_draft" in [tool["function"]["name"] for tool in client.chat_calls[0]["tools"]]


async def test_a_member_that_sends_mail_by_itself_is_told_to_write_a_draft(office, client, plugins):
    client.decisions.append(decision("answer"))
    client.chats = [chat("Sending.", ("Mail__send_email", MAIL)), chat("Writing a draft.", ("email_draft", MAIL)), finished()]
    await typed(office.rig)
    assert SENT == []  # the owner's draft-first preference: not sent, whatever the rules say
    told = [m["content"] for m in client.chat_calls[1]["messages"] if m["role"] == "tool"][-1]
    assert "Do not send this mail yourself" in told and "email_draft" in told
    [card] = (await office.turn())["drafts"]
    assert card["state"] == "draft"
    assert ("send_email", "blocked", "blocked") in [
        (step["tool"], step["outcome"], step["reason"]) for step in (await office.turn())["steps"]]


async def test_a_mail_app_that_only_keeps_drafts_is_not_a_way_to_send(office, client):
    client.decisions.append(decision("answer"))
    client.chats = [chat("Keeping it.", ("Mail__create_draft", MAIL)), finished()]
    await typed(office.rig)
    assert SENT == [] and "drafts" not in await office.turn()  # (a tool that sends nothing is none of the draft-first's)


async def test_without_draft_first_a_direct_send_is_a_call_like_any_other_through_the_gate(office, client, monkeypatch):
    monkeypatch.setattr(office.rig.service, "_speaker", lambda member_id: replace(RAPHAEL, draft_first=False))
    client.decisions.append(decision("answer"))
    client.chats = [chat("Sending.", ("Mail__send_email", MAIL)), finished("Sent.")]
    result = await typed(office.rig)
    assert result["status"] == "done" and SENT == [{"to": "a@b.co", "cc": "", "subject": "Re: lunch", "body": "Thursday works."}]


async def test_without_draft_first_an_ask_first_of_the_owner_still_asks_and_the_answer_is_a_card(office, client, monkeypatch):
    """The preference never takes the owner's rules away: Ask first wins, and the question is a permission card."""
    monkeypatch.setattr(office.rig.service, "_speaker", lambda member_id: replace(RAPHAEL, draft_first=False))
    office.rules.ensure_seeded("en")
    asking = next(rule for rule in office.rules.rules() if rule.action == "Send an email or a message to someone else")
    office.judge.decisions.append(lambda **kwargs: Decision(
        answers={key: noul(0.93 if key == f"rule_{asking.id}" else 0.0) for key in kwargs["questions"]}, cost_usd=0, latency_ms=1))
    client.decisions.append(decision("answer"))
    client.chats = [chat("Sending.", ("Mail__send_email", MAIL)), finished("Sent.")]
    asked = await typed(office.rig)
    assert asked["status"] == "confirm" and SENT == []
    [card] = (await office.turn())["permissions"]
    assert (card["kind"], card["service"], card["tool"], card["state"], card["always"]) == ("tool", "Mail", "send_email", "pending", False)
    assert "Send an email or a message to someone else" in card["reason"]
    done = await office.rig.call("agent.confirm", {"id": asked["confirm"]["id"], "approved": True, "digest": card["digest"]})
    assert done["text"] == "Sent." and len(SENT) == 1


async def test_without_draft_first_a_no_to_the_question_sends_nothing(office, client, monkeypatch):
    monkeypatch.setattr(office.rig.service, "_speaker", lambda member_id: replace(RAPHAEL, draft_first=False))
    office.rules.ensure_seeded("en")
    asking = next(rule for rule in office.rules.rules() if rule.action == "Send an email or a message to someone else")
    office.judge.decisions.append(lambda **kwargs: Decision(
        answers={key: noul(0.93 if key == f"rule_{asking.id}" else 0.0) for key in kwargs["questions"]}, cost_usd=0, latency_ms=1))
    client.decisions.append(decision("answer"))
    client.chats = [chat("Sending.", ("Mail__send_email", MAIL))]
    asked = await typed(office.rig)
    await office.rig.call("agent.confirm", {"id": asked["confirm"]["id"], "approved": False})
    assert SENT == [] and (await office.turn())["permissions"][0]["state"] == "denied"


async def test_a_draft_for_a_colleague_nobody_knows_is_an_error_the_model_sees(office, client):
    client.decisions.append(decision("answer"))
    client.chats = [chat("Writing.", ("email_draft", {**MAIL, "hand_to": "沒有這個人"})), finished("I could not.")]
    await typed(office.rig)
    told = [m["content"] for m in client.chat_calls[1]["messages"] if m["role"] == "tool"][-1]
    assert "unknown_member" in told or "refused" in told
    assert "drafts" not in await office.turn()


async def test_a_draft_without_a_subject_or_body_is_a_bad_call(office, client):
    client.decisions.append(decision("answer"))
    client.chats = [chat("Writing.", ("email_draft", {"to": "a@b.co", "subject": "", "body": ""})), finished()]
    await typed(office.rig)
    assert "bad_arguments" in [m["content"] for m in client.chat_calls[1]["messages"] if m["role"] == "tool"][-1]
    assert "drafts" not in await office.turn()


async def test_a_revision_updates_the_draft_in_the_chat_instead_of_writing_another(office, client):
    client.decisions.append(decision("answer"))
    client.chats = [chat("Writing.", ("email_draft", MAIL)), finished("Done.")]
    await typed(office.rig)
    first = (await office.turn())["drafts"][0]
    client.decisions.append(decision("answer"))
    client.chats = [chat("Shortening.", ("email_draft", {**MAIL, "body": "Thursday.", "revise": True})), finished("Shortened.")]
    await typed(office.rig, "Make it shorter", session="live-2")
    turn = await office.turn()
    [card] = turn["drafts"]
    assert (card["id"], card["version"], card["body"]) == (first["id"], 2, "Thursday.") and card["digest"] != first["digest"]
    assert office.drafts.store.versions(card["id"])[0]["digest"] == first["digest"]


@pytest.mark.parametrize(
    ("source", "scope", "quick", "peer"),
    [
        ("voice", "private", False, None),  # a spoken request
        ("telegram", "private", False, None),  # a message from a phone
        ("app", "private", True, None),  # a quick question
        ("app", "private", False, 1),  # a request one member handed another
        ("member", "exchange", False, 1),
        ("app", "room", False, None),  # a room
    ],
)
async def test_a_room_an_exchange_a_quick_question_and_a_phone_are_never_shown_a_draft(office, source, scope, quick, peer):
    turn = office.conn.execute("INSERT INTO agent_turns (session_id, question, route, prefix, text, status, created_at_ms)"
                               " VALUES ('s', 'q', 'answer', '', '', 'done', 1)").lastrowid
    ask = _Ask(session_id="s", text="Write to Ann", language="en", source=source, scope=scope, quick=quick, peer=peer, turn_id=turn)
    with pytest.raises(HarnessToolError) as refused:
        office.rig.service._show_draft(ask, MAIL)
    assert str(refused.value) == "private_chat_only"
    assert office.conn.execute("SELECT COUNT(*) FROM email_drafts").fetchone()[0] == 0  # nothing was made, so nothing is carried
    assert office.drafts.views([turn]) == {}


async def test_a_refusal_in_a_room_tells_the_model_to_say_one_line_and_not_the_mail(office):
    from raphael_core.agent.service import _HARNESS_NOTES

    note = _HARNESS_NOTES["private_chat_only"]
    assert "one short sentence" in note and "Do not put the mail's text" in note
