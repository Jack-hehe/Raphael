"""Tests for the owner's safety rules: the table and its RPC methods, the rules of the first run, the
judge (Jev, faked) and how its verdicts combine with what is built in.

No test spends money or touches the network.
"""

import asyncio
import logging

import pytest

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.ledger import Ledger
from raphael_core.agent.safety import (
    ALLOW,
    ASK,
    DENY,
    MAX_RULES,
    PASS,
    RUN,
    SEEDS,
    Proposed,
    Rule,
    SafetyGuard,
    SafetyJudge,
    SafetyRules,
    Screening,
    asked_with,
    combine,
    narrow_scope,
    needs_yes,
    summarize,
)
from raphael_core.brain.openrouter import Decision, OpenRouterError
from raphael_core.brain.spend import SpendTracker
from raphael_core.protocol import INVALID_PARAMS, NOT_FOUND, RpcError
from raphael_core.router import Router as RpcRouter

from .conftest import FakeOpenRouter, noul

PRIVATE_RULE = "把PRIVATE-RULE寄出去"
PRIVATE_ARGUMENT = "PRIVATE-ARGUMENT-to-bob@example.com"


@pytest.fixture
def rules(conn):
    return SafetyRules(conn)


@pytest.fixture
def rpc(rules):
    router = RpcRouter()
    rules.register(router)
    return router


@pytest.fixture
def ledger(conn):
    return Ledger(SpendTracker(conn))


def judged(*matches, cost=0.00004, **answers):
    """Jev's answers to the rules: the ids in `matches` say yes, every other id named in `answers`
    as it is given there."""
    found = {f"rule_{rule_id}": noul(0.93) for rule_id in matches}
    found.update({key: noul(value) for key, value in answers.items()})
    return Decision(answers=found, cost_usd=cost, latency_ms=500)


# --- the table and its methods -------------------------------------------------------------------


async def test_the_four_methods_are_registered(rpc):
    assert sorted(rpc.methods()) == [
        "safety.rules.create",
        "safety.rules.delete",
        "safety.rules.list",
        "safety.rules.update",
    ]


async def test_a_rule_is_created_listed_changed_and_deleted(rpc):
    await rpc.call("safety.rules.list", {"ui_language": "en"})  # the first run's three
    made = (await rpc.call("safety.rules.create", {"action": "  在網路上\n留言 ", "verdict": "deny"}))["rule"]
    assert made == {"id": 4, "action": "在網路上 留言", "verdict": "deny", "position": 4}
    changed = (await rpc.call("safety.rules.update", {"id": 4, "verdict": "ask"}))["rule"]
    assert changed["verdict"] == "ask" and changed["action"] == "在網路上 留言"
    renamed = (await rpc.call("safety.rules.update", {"id": 4, "action": "發文"}))["rule"]
    assert renamed["action"] == "發文" and renamed["verdict"] == "ask"
    listed = (await rpc.call("safety.rules.list"))["rules"]
    assert [r["action"] for r in listed] == [*SEEDS["en"], "發文"]
    assert await rpc.call("safety.rules.delete", {"id": 4}) == {"deleted": True}
    assert await rpc.call("safety.rules.delete", {"id": 4}) == {"deleted": False}
    assert len((await rpc.call("safety.rules.list"))["rules"]) == 3


@pytest.mark.parametrize(
    "params, kind",
    [
        ({"action": "", "verdict": "ask"}, "empty"),
        ({"action": "   ", "verdict": "ask"}, "empty"),
        ({"action": "x" * 81, "verdict": "ask"}, "too_long"),
        ({"action": "刪除檔案", "verdict": "deny"}, "duplicate"),
    ],
)
async def test_a_rule_that_cannot_be_kept_is_refused_with_its_kind(rpc, params, kind):
    await rpc.call("safety.rules.list", {"ui_language": "zh-TW"})
    with pytest.raises(RpcError) as error:
        await rpc.call("safety.rules.create", params)
    assert error.value.code == INVALID_PARAMS and error.value.data == {"kind": kind}


async def test_a_bad_verdict_or_id_is_refused(rpc):
    for params in ({"action": "x", "verdict": "maybe"}, {"action": 5, "verdict": "ask"}):
        with pytest.raises(RpcError) as error:
            await rpc.call("safety.rules.create", params)
        assert error.value.code == INVALID_PARAMS
    with pytest.raises(RpcError) as error:
        await rpc.call("safety.rules.update", {"id": 99, "verdict": "ask"})
    assert error.value.code == NOT_FOUND
    with pytest.raises(RpcError) as error:
        await rpc.call("safety.rules.delete", {"id": "1"})
    assert error.value.code == INVALID_PARAMS


async def test_a_rule_cannot_be_renamed_to_one_that_is_there(rpc):
    await rpc.call("safety.rules.list", {"ui_language": "zh-TW"})
    with pytest.raises(RpcError) as error:
        await rpc.call("safety.rules.update", {"id": 1, "action": "刪除檔案"})
    assert error.value.data == {"kind": "duplicate"}
    # ... but it may keep its own words with another case or spacing
    assert (await rpc.call("safety.rules.update", {"id": 1, "action": "付款結帳 "}))["rule"]["id"] == 1


async def test_there_are_at_most_so_many_rules(rules):
    rules.ensure_seeded("en")
    for n in range(MAX_RULES - 3):
        rules.create(f"rule {n}", ASK)
    with pytest.raises(RpcError) as error:
        rules.create("one more", ASK)
    assert error.value.data == {"kind": "too_many"}


# --- the rules of the first run ------------------------------------------------------------------


@pytest.mark.parametrize("language", ["zh-TW", "en", "ja"])
async def test_the_first_run_has_three_rules_that_ask_in_the_language_of_the_app(rpc, language):
    listed = (await rpc.call("safety.rules.list", {"ui_language": language}))["rules"]
    assert [(r["action"], r["verdict"]) for r in listed] == [(a, "ask") for a in SEEDS[language]]
    assert SEEDS["zh-TW"] == ("付款結帳", "刪除檔案", "寄信或傳訊息給別人")


async def test_the_rules_are_seeded_once_and_never_put_back(rpc):
    await rpc.call("safety.rules.list", {"ui_language": "zh-TW"})
    for rule_id in (1, 2, 3):
        await rpc.call("safety.rules.delete", {"id": rule_id})
    assert (await rpc.call("safety.rules.list", {"ui_language": "en"}))["rules"] == []


async def test_a_rule_the_owner_wrote_first_keeps_the_seeds_away(rules):
    rules.create("發文", DENY)
    rules.ensure_seeded("zh-TW")
    assert [r.action for r in rules.rules()] == ["發文"]


async def test_an_unknown_language_seeds_in_the_one_raphael_falls_back_to(rules):
    rules.ensure_seeded("fr")
    assert [r.action for r in rules.rules()] == list(SEEDS["en"])


# --- the judge -----------------------------------------------------------------------------------


async def test_the_judge_asks_one_question_for_each_rule_and_pays_for_it(rules, ledger):
    client = FakeOpenRouter()
    rules.create("寄信給別人", ASK)
    rules.create("刪除檔案", DENY)
    client.decisions.append(judged(1, rule_2=0.1))
    judge = SafetyJudge(client)
    matched = await judge.matching(
        Proposed("Gmail / send", "Send an email.", summarize({"to": "bob"})), rules.rules(), ledger
    )
    assert matched == frozenset({1})
    sent = client.decide_calls[0]
    assert sent["model"] == "typesafe/jev-1.14"
    assert sorted(sent["questions"]) == ["rule_1", "rule_2"]
    assert all(q["type"] == "noul" for q in sent["questions"].values())
    assert "寄信給別人" in sent["questions"]["rule_1"]["instructions"]
    assert sent["state"] == {
        "action": {"tool": "Gmail / send", "description": "Send an email.", "arguments": '{"to":"bob"}'}
    }
    assert ledger.total == pytest.approx(0.00004)


async def test_no_rule_matching_is_an_empty_set(rules, ledger):
    client = FakeOpenRouter()
    rules.create("付款", ASK)
    client.decisions.append(judged(rule_1=0.2))
    assert await SafetyJudge(client).matching(Proposed("x"), rules.rules(), ledger) == frozenset()


@pytest.mark.parametrize(
    "answer",
    [
        OpenRouterError(500, "server", "down"),
        OpenRouterError(401, "auth", "no"),
        RuntimeError("boom"),
        Decision(answers={}, cost_usd=0.0, latency_ms=1),  # it answered no rule
        Decision(answers={"rule_1": {"type": "choice"}}, cost_usd=0.0, latency_ms=1),
    ],
)
async def test_a_judge_that_fails_says_nothing_and_the_rules_ask(rules, ledger, answer):
    client = FakeOpenRouter()
    rules.create("付款", ALLOW)
    client.decisions.append(answer)
    guard = SafetyGuard(rules, SafetyJudge(client))
    screening = await guard.screen(Proposed("x"), language="zh-TW", ledger=ledger)
    assert screening == Screening(ASK, None, unchecked=True)


async def test_a_judge_that_takes_too_long_counts_as_one_that_failed(rules, ledger):
    client = FakeOpenRouter()
    rules.create("付款", ALLOW)

    async def slow(**kwargs):
        await asyncio.sleep(1.0)

    client.decisions.append(slow)
    judge = SafetyJudge(client, timeout_s=0.05)
    assert await judge.matching(Proposed("x"), rules.rules(), ledger) is None


async def test_without_rules_nothing_is_asked_and_everything_passes(rules, ledger):
    client = FakeOpenRouter()
    rules.ensure_seeded("en")
    for rule in rules.rules():
        rules.delete(rule.id)
    guard = SafetyGuard(rules, SafetyJudge(client))
    assert await guard.screen(Proposed("x"), language="en", ledger=ledger) == PASS
    assert client.decide_calls == []


async def test_the_first_screening_seeds_the_rules_in_the_language_of_the_request(rules, ledger):
    client = FakeOpenRouter()
    client.decisions.append(judged(rule_1=0.0, rule_2=0.0, rule_3=0.0))
    guard = SafetyGuard(rules, SafetyJudge(client))
    assert await guard.screen(Proposed("x"), language="ja", ledger=ledger) == PASS
    assert [r.action for r in rules.rules()] == list(SEEDS["ja"])


async def test_a_gate_without_a_guard_passes_everything_with_no_call(conn, ledger):
    gate = ToolGate(None, conn=conn)
    assert await gate.screen(Proposed("x"), language="en", ledger=ledger) == PASS
    assert gate.reads_only("Notes", "look") is False  # no hub: every tool changes things


async def test_nothing_of_an_action_or_a_rule_is_logged(rules, ledger, caplog):
    client = FakeOpenRouter()
    rules.create(PRIVATE_RULE, DENY)
    client.decisions.append(judged(1))
    client.decisions.append(OpenRouterError(500, "server", PRIVATE_ARGUMENT))
    guard = SafetyGuard(rules, SafetyJudge(client))
    proposed = Proposed("Gmail / send", "Send it.", summarize({"to": PRIVATE_ARGUMENT}))
    with caplog.at_level(logging.DEBUG):
        assert (await guard.screen(proposed, language="zh-TW", ledger=ledger)).verdict == DENY
        assert (await guard.screen(proposed, language="zh-TW", ledger=ledger)).unchecked
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Safety check: verdict=deny" in logged
    for private in (PRIVATE_RULE, PRIVATE_ARGUMENT, "Gmail", "Send it"):
        assert private not in logged


# --- combining ------------------------------------------------------------------------------------


RULES = [Rule(1, "寄信", ALLOW, 1), Rule(2, "寄信給別人", ASK, 2), Rule(3, "寄給老闆", DENY, 3)]


def test_deny_wins_over_ask_and_allow():
    assert combine(RULES, frozenset({1, 2, 3})) == Screening(DENY, "寄給老闆")


def test_ask_wins_over_allow():
    assert combine(RULES, frozenset({1, 2})) == Screening(ASK, "寄信給別人")


def test_allow_alone_runs_and_says_so():
    assert combine(RULES, frozenset({1})) == Screening(RUN, "寄信", allowed=True)


def test_no_match_is_as_without_rules():
    assert combine(RULES, frozenset()) == PASS


def test_a_failed_judge_asks():
    assert combine(RULES, None) == Screening(ASK, None, unchecked=True)


def test_allow_never_waives_a_built_in_question():
    allowed = combine(RULES, frozenset({1}))
    assert needs_yes(True, allowed) is True  # a T2 tool, a step that sends: asked anyway
    assert needs_yes(False, allowed) is False
    assert needs_yes(False, combine(RULES, frozenset({2}))) is True
    assert needs_yes(False, PASS) is False


def test_a_question_a_rule_asked_for_names_the_rule():
    asking = Screening(ASK, "寄信給別人")
    assert asked_with("zh-TW", asking, "是否執行此操作？") == "此操作符合安全規則「寄信給別人」。是否執行此操作？"
    assert asked_with("en", asking, "Execute this action?") == (
        "This falls under the safety rule 「寄信給別人」. Execute this action?"
    )
    unchecked = Screening(ASK, None, unchecked=True)
    assert asked_with("ja", unchecked, "実行しますか？") == "安全ルールを照合できませんでした。実行しますか？"
    assert asked_with("zh-TW", PASS, "是否執行此操作？") == "是否執行此操作？"


def test_the_summary_of_the_arguments_is_short():
    assert summarize({}) == "" and summarize(None) == ""
    long = summarize({"text": "字" * 2000})
    assert len(long) == 400 and long.endswith("…")


# --- the narrow rule of Always (P08c) -------------------------------------------------------------

SEND = "mcp:mail/send"
POST = "mcp:mail/post"


def test_always_makes_a_narrow_rule_once_in_the_owners_words_and_after_the_seeds(rules):
    first = rules.always(narrow_scope(SEND), "寄信服務：send", "zh-TW")
    assert (first.verdict, first.scope, first.action) == (ALLOW, "tool:mcp:mail/send", "寄信服務：send")
    assert [r.action for r in rules.rules()] == [*SEEDS["zh-TW"], "寄信服務：send"]  # the first run's three came first
    assert rules.always(narrow_scope(SEND), "another words", "zh-TW") == first  # the one that is there
    assert len(rules.rules()) == 4
    assert rules.always_allows(narrow_scope(SEND)) and not rules.always_allows(narrow_scope(POST))
    assert not rules.always_allows("")


async def test_a_narrow_rule_is_listed_like_any_other_without_its_scope(rpc, rules):
    rules.always(narrow_scope(SEND), "Mail：send", "en")
    listed = (await rpc.call("safety.rules.list", {"ui_language": "en"}))["rules"]
    assert listed[-1] == {"id": 4, "action": "Mail：send", "verdict": "allow", "position": 4}
    # ... and changed like any other: its words, its verdict, and deleted.
    changed = (await rpc.call("safety.rules.update", {"id": 4, "verdict": "ask"}))["rule"]
    assert changed["verdict"] == "ask"
    assert rules.scoped(narrow_scope(SEND))[0].verdict == ASK
    assert await rpc.call("safety.rules.delete", {"id": 4}) == {"deleted": True}
    assert rules.scoped(narrow_scope(SEND)) == []


def test_two_tools_that_go_by_the_same_words_get_two_rules(rules):
    one = rules.always(narrow_scope(SEND), "Mail：send", "en")
    other = rules.always(narrow_scope(POST), "Mail：send", "en")
    assert one.action == "Mail：send" and other.action == "Mail：send (2)" and one.scope != other.scope


def test_there_are_at_most_so_many_rules_for_always_as_for_any(rules):
    rules.ensure_seeded("en")
    for n in range(MAX_RULES - 3):
        rules.create(f"rule {n}", ASK)
    with pytest.raises(RpcError) as error:
        rules.always(narrow_scope(SEND), "Mail：send", "en")
    assert error.value.data == {"kind": "too_many"}


def test_the_owner_can_turn_a_narrow_rule_into_ask_first_or_never_in_settings(rules):
    rule = rules.always(narrow_scope(SEND), "Mail：send", "en")
    assert rules.always_allows(narrow_scope(SEND))
    rules.update(rule.id, verdict=ASK)
    assert not rules.always_allows(narrow_scope(SEND))  # Ask first: no Always any more
    assert rules.scoped(narrow_scope(SEND))[0].verdict == ASK
    rules.update(rule.id, verdict=DENY)
    assert rules.scoped(narrow_scope(SEND))[0].scope == narrow_scope(SEND)  # it stays that tool's


async def test_a_narrow_rule_is_matched_by_its_tool_with_no_call_to_the_judge(rules, ledger):
    client = FakeOpenRouter()
    rules.ensure_seeded("en")
    for rule in rules.rules():
        rules.delete(rule.id)
    rules.always(narrow_scope(SEND), "Mail：send", "en")
    guard = SafetyGuard(rules, SafetyJudge(client))
    sending = await guard.screen(Proposed("Mail / send", scope=narrow_scope(SEND)), language="en", ledger=ledger)
    assert sending == Screening(RUN, "Mail：send", allowed=True, narrow=True)
    other = await guard.screen(Proposed("Mail / post", scope=narrow_scope(POST)), language="en", ledger=ledger)
    assert other == PASS  # a narrow rule covers one tool and nothing else
    assert client.decide_calls == []  # nothing in the owner's words to judge


async def test_a_narrow_never_refuses_without_a_call(rules, ledger):
    client = FakeOpenRouter()
    rule = rules.always(narrow_scope(SEND), "Mail：send", "en")
    rules.update(rule.id, verdict=DENY)
    guard = SafetyGuard(rules, SafetyJudge(client))
    screening = await guard.screen(Proposed("Mail / send", scope=narrow_scope(SEND)), language="en", ledger=ledger)
    assert screening == Screening(DENY, "Mail：send")
    assert client.decide_calls == []


async def test_ask_first_in_the_owners_words_wins_over_a_narrow_always(rules, ledger):
    client = FakeOpenRouter()
    rules.ensure_seeded("en")
    rules.always(narrow_scope(SEND), "Mail：send", "en")
    client.decisions.append(judged(3, rule_1=0.0, rule_2=0.0))  # the seeded 「send an email...」 matches
    guard = SafetyGuard(rules, SafetyJudge(client))
    screening = await guard.screen(Proposed("Mail / send", scope=narrow_scope(SEND)), language="en", ledger=ledger)
    assert screening.verdict == ASK and screening.rule == SEEDS["en"][2] and not screening.narrow
    # The judge was asked about the owner's words only, never about the narrow rule.
    assert sorted(client.decide_calls[0]["questions"]) == ["rule_1", "rule_2", "rule_3"]


async def test_a_judge_that_fails_asks_even_when_a_narrow_always_matches(rules, ledger):
    client = FakeOpenRouter()
    rules.ensure_seeded("en")
    rules.always(narrow_scope(SEND), "Mail：send", "en")
    client.decisions.append(OpenRouterError(500, "server", "down"))
    guard = SafetyGuard(rules, SafetyJudge(client))
    screening = await guard.screen(Proposed("Mail / send", scope=narrow_scope(SEND)), language="en", ledger=ledger)
    assert screening == Screening(ASK, None, unchecked=True)  # an Ask first the judge could not read may be there


async def test_never_ask_and_always_against_an_ask_first_of_the_owner(rules, ledger):
    """The matrix of the rules: every verdict of one rule against an Ask first that also matches."""
    expected = {DENY: DENY, ASK: ASK, ALLOW: ASK}  # Never wins over Ask first; Ask first wins over Always
    for verdict, outcome in expected.items():
        client = FakeOpenRouter()
        rules.ensure_seeded("en")
        for rule in rules.rules():
            rules.delete(rule.id)
        asking = rules.create("send mail", ASK)
        other = rules.create("any message", verdict)
        client.decisions.append(judged(asking.id, other.id))
        guard = SafetyGuard(rules, SafetyJudge(client))
        screening = await guard.screen(Proposed("Mail / send"), language="en", ledger=ledger)
        assert screening.verdict == outcome, (verdict, screening)
        rules.delete(asking.id)
        rules.delete(other.id)


def test_a_narrow_always_waives_the_questions_of_the_tier_and_nothing_firm():
    narrow = Screening(RUN, "Mail：send", allowed=True, narrow=True)
    assert needs_yes(True, narrow) is False  # a T2 tool, or the global switch's question
    assert needs_yes(True, narrow, firm=True) is True  # T3, a step that sends, a hand-over
    assert needs_yes(False, narrow) is False
    assert needs_yes(True, Screening(RUN, "寄信", allowed=True)) is True  # the owner's words waive no built-in question
    assert needs_yes(False, Screening(ASK, "寄信")) is True and needs_yes(True, Screening(ASK, "寄信")) is True


def test_the_narrow_always_wins_over_a_worded_always_in_the_combination():
    rules_ = [Rule(1, "寄信", ALLOW, 1), Rule(2, "Mail：send", ALLOW, 2, scope=narrow_scope(SEND))]
    assert combine(rules_, frozenset({1, 2})) == Screening(RUN, "Mail：send", allowed=True, narrow=True)
    assert combine(rules_, frozenset({1})) == Screening(RUN, "寄信", allowed=True)
