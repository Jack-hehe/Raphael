"""The permission card of a typed private chat (P08c): the question the gate asks, said in the thread for
that exact action, with 允許一次, 一律允許 and 拒絕, bound to its digest, and the owner's rules around it.

The plugins are real ones over in-memory servers and the model is a fake; the card follows the production
`agent.confirm`. No test spends money or touches the network."""

import json

import pytest

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.permissions import PermissionCards, qualifies
from raphael_core.agent.plugins import PlannedCall
from raphael_core.agent.safety import ASK, DENY, SEEDS, SafetyGuard, SafetyJudge, SafetyRules, narrow_scope
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn, Decision
from raphael_core.brain.settings import SettingsStore

from .conftest import FakeOpenRouter, decision, noul, tool_plan
from .test_plugins import calls, plugins  # noqa: F401
from .test_service import make_rig  # noqa: F401


def chat(text, *tool_calls):
    return ChatTurn(text, [ChatToolCall(str(n), name, args, json.dumps(args)) for n, (name, args) in enumerate(tool_calls)],
                    "fake/model", 0.3, 10, 10, 1)


def finished(text="好了。"):
    return chat(json.dumps({"prefix": "Answer", "text": text, "detail": ""}))


async def typed(rig, text="Save an example", member_id=0, session="live"):
    return await rig.call("agent.ask", {"session_id": session, "text": text, "ui_language": "en", "source": "app",
                                        "member_id": member_id})


class Judge(FakeOpenRouter):
    """Jev, who says no rule falls under an action unless a test has directed him otherwise."""

    async def decide(self, **kwargs):
        if self.decisions:
            return await super().decide(**kwargs)
        self.decide_calls.append(kwargs)
        return Decision(answers={key: noul(0.0) for key in kwargs["questions"]}, cost_usd=0, latency_ms=1)


class Owner:
    """A rig whose assistant has the permission cards and the owner's rules, with a judge a test directs."""

    def __init__(self, conn, plugins, make_rig):
        self.conn, self.plugins = conn, plugins
        self.rules = SafetyRules(conn)
        self.judge = Judge()
        self.gate = ToolGate(plugins.hub, conn=conn, safety=SafetyGuard(self.rules, SafetyJudge(self.judge)))
        self.cards = PermissionCards(conn)
        self.rig = make_rig(plugins=plugins.hub, gate=self.gate, permissions=self.cards)

    def judged(self, *matches):
        """The judge says the rules `matches` (by id) fall under the next action, and no other."""
        self.judge.decisions.append(lambda **kwargs: Decision(
            answers={key: noul(0.93 if int(key.removeprefix("rule_")) in matches else 0.0) for key in kwargs["questions"]},
            cost_usd=0, latency_ms=1))

    def cards_of(self, turn_id):
        return self.cards.store.for_turns([turn_id]).get(turn_id, [])

    async def view(self, turn_id):
        turns = (await self.rig.call("agent.history.list", {"limit": 50}))["turns"]
        return next(turn for turn in turns if turn["id"] == turn_id)

    async def confirm(self, asked, approved=True, **extra):
        return await self.rig.call("agent.confirm", {"id": asked["confirm"]["id"], "approved": approved, **extra})


@pytest.fixture
def owner(conn, plugins, make_rig):
    return Owner(conn, plugins, make_rig)


def script(client, text="記下牛奶", tool="Notes__note", arguments=None):
    client.decisions.append(decision("answer"))
    client.chats = [chat("I will do it.", (tool, arguments or {"text": text})), finished()]


# --- the card of a question ----------------------------------------------------------------------------


async def test_a_tool_that_asks_shows_a_card_for_that_exact_action_in_the_chat_that_asked(owner, plugins, calls, client):
    await plugins.connect(note="T2")
    script(client)
    asked = await typed(owner.rig)
    confirm = asked["confirm"]
    assert asked["status"] == "confirm" and calls == []
    [card] = owner.cards_of(asked["turn_id"])
    assert (card.id, card.source, card.kind, card.service, card.tool, card.state) == (
        confirm["id"], "ask", "tool", "Notes", "note", "pending")
    assert card.summary == "Allow Notes to run note (text: 記下牛奶)?" and card.reason == ""
    assert card.digest == confirm["digest"] and len(card.digest) == 64
    # Nothing the owner wrote asks, so 一律允許 can take effect: the question carries the three answers.
    assert card.always and (confirm["yes"], confirm["no"], confirm["always"]) == ("Allow once", "Deny", "Always allow")
    view = await owner.view(asked["turn_id"])
    assert view["permissions"] == [card.view()] and view["status"] == "confirm"


async def test_allowing_once_runs_the_call_once_and_the_card_says_so(owner, plugins, calls, client):
    await plugins.connect(note="T2")
    script(client)
    asked = await typed(owner.rig)
    answered = await owner.confirm(asked, True, digest=asked["confirm"]["digest"])
    assert answered["text"] == "好了。" and calls == ["note"]
    assert owner.cards_of(asked["turn_id"])[0].state == "allowed"
    assert [rule for rule in owner.rules.rules() if rule.scope] == []  # a once is a once: no rule was made
    # A replay of the very same yes runs nothing, and the card stays what it was.
    again = await owner.confirm(asked, True)
    assert again["turn_id"] == 0 and again["text"] != "好了。"
    assert calls == ["note"] and owner.cards_of(asked["turn_id"])[0].state == "allowed"


async def test_an_answer_for_another_digest_changes_nothing_and_the_question_still_waits(owner, plugins, calls, client):
    await plugins.connect(note="T2")
    script(client)
    asked = await typed(owner.rig)
    stale = await owner.confirm(asked, True, digest="0" * 64)
    assert stale["turn_id"] == 0 and calls == []
    assert owner.cards_of(asked["turn_id"])[0].state == "pending" and (await owner.view(asked["turn_id"]))["status"] == "confirm"
    proper = await owner.confirm(asked, True, digest=asked["confirm"]["digest"])
    assert proper["text"] == "好了。" and calls == ["note"]


async def test_inputs_changed_after_the_question_never_use_the_yes(owner, plugins, calls, client):
    await plugins.connect(note="T2")
    script(client)
    asked = await typed(owner.rig)
    followup = owner.rig.service._followups[asked["confirm"]["id"]]
    followup.call.arguments["text"] = "something nobody was asked about"  # changed after the question
    await owner.confirm(asked, True)
    assert calls == []  # the call was not made: the yes was for another digest
    assert plugins.audit()[-1][3:] == ("none", "blocked")


async def test_no_answer_in_thirty_seconds_expires_the_card_and_a_late_yes_runs_nothing(owner, plugins, calls, client):
    await plugins.connect(note="T2")
    script(client)
    asked = await typed(owner.rig)
    owner.rig.clock.now += 31
    late = await owner.confirm(asked, True)
    assert late["text"] != "好了。" and calls == []
    assert owner.cards_of(asked["turn_id"])[0].state == "expired"


async def test_a_no_runs_nothing_and_the_card_is_denied(owner, plugins, calls, client):
    await plugins.connect(note="T2")
    script(client)
    asked = await typed(owner.rig)
    await owner.confirm(asked, False)
    assert calls == [] and owner.cards_of(asked["turn_id"])[0].state == "denied"
    assert (await owner.view(asked["turn_id"]))["status"] == "denied"


async def test_a_core_that_started_again_has_no_question_so_the_old_card_is_expired(owner, plugins, calls, client, conn):
    await plugins.connect(note="T2")
    script(client)
    asked = await typed(owner.rig)
    restarted = PermissionCards(conn)
    assert restarted.store.get(asked["confirm"]["id"]).state == "expired" and calls == []


# --- Always ----------------------------------------------------------------------------------------------


async def test_always_keeps_a_narrow_rule_for_that_tool_and_the_next_request_asks_nothing(owner, plugins, calls, client):
    server_id = await plugins.connect(note="T2")
    script(client)
    asked = await typed(owner.rig)
    await owner.confirm(asked, True, always=True)
    assert calls == ["note"] and owner.cards_of(asked["turn_id"])[0].state == "always"
    [rule] = [rule for rule in owner.rules.rules() if rule.scope]
    assert (rule.verdict, rule.scope) == ("allow", narrow_scope(f"mcp:{server_id}/note"))
    # A new request for the same tool is made at once: no question, no card.
    script(client, "記下雞蛋")
    second = await typed(owner.rig, session="live-2")
    assert second["status"] == "done" and calls == ["note", "note"]
    assert owner.cards_of(second["turn_id"]) == []


async def test_always_covers_one_tool_and_the_others_still_ask(owner, plugins, calls, client):
    await plugins.connect(note="T2", wipe="T2")
    script(client)
    asked = await typed(owner.rig)
    await owner.confirm(asked, True, always=True)
    script(client, tool="Notes__wipe", arguments={})
    other = await typed(owner.rig, "Wipe the notes", session="live-2")
    assert other["status"] == "confirm" and calls == ["note"]  # a narrow rule is for one tool


async def test_where_a_rule_asks_the_card_offers_no_always_and_makes_none(owner, plugins, calls, client):
    await plugins.connect(note="T1")
    owner.rules.ensure_seeded("en")
    asking = [rule for rule in owner.rules.rules() if rule.action == SEEDS["en"][2]][0]  # send an email or a message...
    owner.judged(asking.id)
    script(client)
    asked = await typed(owner.rig)
    [card] = owner.cards_of(asked["turn_id"])
    assert card.state == "pending" and not card.always and SEEDS["en"][2] in card.reason
    assert "always" not in asked["confirm"] and asked["confirm"]["yes"] == "Yes"
    # Pressing Always anyway (a client that was not told it was offered) makes no rule.
    await owner.confirm(asked, True, always=True)
    assert calls == ["note"] and [rule for rule in owner.rules.rules() if rule.scope] == []
    assert owner.cards_of(asked["turn_id"])[0].state == "allowed"


async def test_an_always_made_earlier_never_beats_an_ask_first_the_owner_wrote_later(owner, plugins, calls, client):
    await plugins.connect(note="T2")
    script(client)
    asked = await typed(owner.rig)
    await owner.confirm(asked, True, always=True)
    owner.rules.ensure_seeded("en")
    asking = owner.rules.create("Save notes", ASK)
    owner.judged(asking.id)
    script(client, "記下雞蛋")
    second = await typed(owner.rig, session="live-2")
    assert second["status"] == "confirm"  # Ask first has the last word over Always
    [card] = owner.cards_of(second["turn_id"])
    assert not card.always and "Save notes" in card.reason and calls == ["note"]


async def test_a_never_refuses_without_a_card(owner, plugins, calls, client):
    await plugins.connect(note="T1")
    owner.rules.ensure_seeded("en")
    never = owner.rules.create("Save notes", DENY)
    owner.judged(never.id)
    script(client)
    result = await typed(owner.rig)
    assert result["status"] == "done" and calls == []
    assert owner.cards.store.for_turns([result["turn_id"]]) == {}


async def test_the_global_switch_offers_always_and_always_waives_it_for_that_tool_alone(owner, plugins, calls, client, conn):
    await plugins.connect(note="T1", wipe="T1")
    SettingsStore(conn).set("action_mode", "ask")
    owner.rules.ensure_seeded("en")
    for rule in owner.rules.rules():
        owner.rules.delete(rule.id)
    script(client)
    asked = await typed(owner.rig)
    [card] = owner.cards_of(asked["turn_id"])
    assert card.always and card.reason == ""  # nothing the owner wrote asks: only the switch
    await owner.confirm(asked, True, always=True)
    script(client, "again")
    assert (await typed(owner.rig, session="live-2"))["status"] == "done"  # the switch is waived for that tool
    script(client, tool="Notes__wipe", arguments={})
    assert (await typed(owner.rig, "Wipe the notes", session="live-3"))["status"] == "confirm"  # ... and not for another


# --- no way round a no -----------------------------------------------------------------------------------


async def test_a_no_is_remembered_by_the_request_so_that_no_other_way_to_the_same_end_is_asked_about(owner, plugins, calls, client):
    server_id = await plugins.connect(note="T2", wipe="T2")
    script(client)
    asked = await typed(owner.rig)
    followup = owner.rig.service._followups[asked["confirm"]["id"]]
    await owner.confirm(asked, False)
    assert followup.ask.declined == [(f"mcp:{server_id}/note", None)] and calls == []
    # What a continuing turn (a CLI brain's) meets next in that request: the tool again, or another one.
    service, ask = owner.rig.service, followup.ask
    same = await service._carry_out(ask, [followup.call], [], ask.ledger)
    assert [(result.ok, result.error) for result in same] == [(False, "declined")] and same[0].instruction
    other_tool = next(tool for tool in plugins.hub.offered() if tool.name == "wipe")
    other = await service._carry_out(ask, [PlannedCall(other_tool, {})], [], ask.ledger)
    assert [(result.ok, result.error) for result in other] == [(False, "declined")]
    assert calls == [] and owner.cards.store.for_turns([asked["turn_id"]])[asked["turn_id"]][0].state == "denied"


# --- who is shown a card ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "quick", "nested", "scope", "shown"),
    [
        ("app", False, False, "private", True),
        ("connector", False, False, "private", True),  # a task resumed in the same thread
        ("voice", False, False, "private", False),  # a spoken request: the overlay asks
        ("telegram", False, False, "private", False),  # a message from a phone
        ("app", True, False, "private", False),  # a quick question
        ("app", False, True, "private", False),  # a request one member handed another
        ("app", False, False, "room", False),  # a room
        ("member", False, False, "exchange", False),
    ],
)
def test_only_a_private_chat_typed_at_this_computer_is_shown_a_card(source, quick, nested, scope, shown):
    assert qualifies(source=source, quick=quick, nested=nested, scope=scope) is shown


async def test_a_spoken_request_is_asked_by_the_overlay_and_has_no_card(owner, plugins, calls, client):
    await plugins.connect(note="T2")
    client.decisions.append(decision("answer"))
    client.plans.append(tool_plan(("Notes__note", {"text": "x"})))
    result = await owner.rig.ask("記下牛奶")
    assert result["status"] == "confirm" and owner.cards.store.for_turns([result["turn_id"]]) == {}
    assert "digest" not in result["confirm"] and "always" not in result["confirm"]
