"""Tests for the one continuous conversation: what the model reads (the summary, then the latest
turns), when the oldest turns are folded into the summary, and which turns never are yet."""

import pytest

from raphael_core.agent.history import Turn
from raphael_core.agent.ledger import Ledger
from raphael_core.brain.openrouter import OpenRouterError
from raphael_core.brain.spend import SpendTracker
from raphael_core.memory import conversation as conversation_module
from raphael_core.memory.conversation import (
    FOLD_AT_TURNS,
    KEEP_OWNER_TURNS,
    KEEP_TURNS,
    QUICK_TURNS,
    SUMMARY_MAX_TOKENS,
    Conversation,
    asks_to_remember,
    cap_summary,
    is_correction,
    plan_fold,
    turn_text,
)
from raphael_core.memory.terms import estimate_tokens

from .conftest import FakeModel, add_turn


def turn(n, question=None, *, status="done", route="chat", text="好。", detail=None):
    return Turn(
        id=n, created_at_ms=n, session_id="s", question=question or f"q{n}", route=route,
        prefix="答", text=text, detail=detail, status=status,
    )  # fmt: skip


def ids(turns):
    return [t.id for t in turns]


# --- when to fold, and what -----------------------------------------------------------------------


def test_nothing_is_folded_until_the_turns_pass_thirty_or_twelve_thousand_tokens():
    assert plan_fold([turn(n) for n in range(1, FOLD_AT_TURNS + 1)], set()) is None
    folded, through = plan_fold([turn(n) for n in range(1, FOLD_AT_TURNS + 2)], set())
    # Thirty-one turns: the newest sixteen stay as they were said.
    assert ids(folded) == list(range(1, FOLD_AT_TURNS + 2 - KEEP_TURNS))
    assert through == FOLD_AT_TURNS + 1 - KEEP_TURNS


def test_long_turns_are_folded_by_their_tokens_down_to_six_thousand():
    long = "字" * 1500  # about 1,500 tokens a turn
    turns = [turn(n, long) for n in range(1, 10)]  # about 13,500 tokens
    folded, through = plan_fold(turns, set())
    # Kept: the newest that make 6,000 tokens (four turns), and never fewer than the last four.
    assert through == 5 and ids(folded) == [1, 2, 3, 4, 5]


def test_the_owner_s_last_four_turns_are_always_kept():
    huge = "字" * 13_000
    turns = [turn(n, huge) for n in range(1, 6)]
    folded, through = plan_fold(turns, set())
    assert through == 1 and len(turns) - through == KEEP_OWNER_TURNS


def test_the_fold_stops_before_a_protected_turn():
    turns = [turn(n) for n in range(1, 40)]
    folded, through = plan_fold(turns, {5})
    assert through == 4 and ids(folded) == [1, 2, 3, 4]
    assert plan_fold(turns, {1}) is None  # nothing before it to fold


def test_turns_that_say_nothing_are_covered_but_not_given_to_the_model():
    turns = [turn(n, status="denied" if n % 2 else "done") for n in range(1, 70)]
    folded, through = plan_fold(turns, set())
    assert all(t.status == "done" for t in folded) and through > ids(folded)[-1] - 2


# --- which turns are protected ---------------------------------------------------------------------


@pytest.fixture
def conversation(conn, clock):
    reflected = {"through": 0}
    made = Conversation(
        conn, FakeModel(), now_ms=clock.ms, clock=clock.mono,
        reflected_through=lambda: reflected["through"],
    )  # fmt: skip
    made.reflected = reflected
    return made


def test_a_question_waiting_for_a_yes_is_protected(conversation, history):
    waiting = add_turn(history, "要刪除嗎", status="confirm")
    done = add_turn(history, "好")
    assert conversation.protected(history.after(0)) == {waiting}
    assert done not in conversation.protected(history.after(0))


def test_a_correction_is_protected_until_the_reflection_pass_has_read_it(conversation, history):
    correction = add_turn(history, "不對，我住高雄不是台北")
    assert conversation.protected(history.after(0)) == {correction}
    conversation.reflected["through"] = correction
    assert conversation.protected(history.after(0)) == set()


def test_a_turn_whose_background_task_has_not_reported_is_protected(conversation, history, conn):
    handed = add_turn(history, "幫我整理信箱", route="delegate")
    reported = add_turn(history, "幫我查行程", route="delegate")
    for turn_id, state in ((handed, "running"), (reported, "done")):
        conn.execute(
            "INSERT INTO worker_tasks (created_at_ms, state, session_id, turn_id, language, text)"
            " VALUES (1, ?, 's', ?, 'zh-TW', 'x')",
            (state, turn_id),
        )
    assert conversation.protected(history.after(0)) == {handed}


def test_corrections_and_requests_to_remember_are_told_by_their_words():
    for text in ("不對，是三點", "我是說下週", "That's wrong, I meant Tuesday", "違う、火曜日です", "叫我 Jack 就好"):
        assert is_correction(text), text
    for text in ("今天天氣如何", "Tell me the time", "明日の予定は"):
        assert not is_correction(text), text
    for text in ("記住我住台北", "幫我記下 Amy 的生日", "remember that I prefer tea", "覚えておいて", "忘掉這件事"):
        assert asks_to_remember(text), text
    for text in ("今天天氣如何", "what is the weather", "記事本打開"):
        assert not asks_to_remember(text), text


# --- what the model reads --------------------------------------------------------------------------


def test_the_context_is_the_summary_and_the_answered_turns_after_it(conversation, history):
    for n in range(5):
        add_turn(history, f"q{n}")
    add_turn(history, "refused", status="denied")
    conversation._save("Ongoing:\n- 規劃週末", 2)
    context = conversation.context()
    assert context.summary == "Ongoing:\n- 規劃週末"
    assert [t.question for t in context.turns] == ["q2", "q3", "q4"]


def test_a_quick_route_reads_only_the_last_six_turns(conversation, history):
    for n in range(10):
        add_turn(history, f"q{n}")
    assert [t.question for t in conversation.context(quick=True).turns] == [f"q{n}" for n in range(4, 10)]
    assert len(conversation.context(quick=True).turns) == QUICK_TURNS
    assert len(conversation.context().turns) == 10


def test_the_context_holds_at_most_thirty_turns_however_far_the_fold_lags(conversation, history):
    for n in range(50):
        add_turn(history, f"q{n}")
    turns = conversation.context().turns
    assert len(turns) == FOLD_AT_TURNS and turns[-1].question == "q49"


def test_a_discussion_shows_the_start_of_its_longer_text():
    long = "長" * 500
    shown = turn_text(turn(1, route="discuss", detail=long), detail=True)
    assert "User: q1\nRaphael: 好。\n(Raphael, at more length: " in shown
    assert shown.endswith("…)") and shown.count("長") == 400
    assert turn_text(turn(1, detail="好。"), detail=True) == "User: q1\nRaphael: 好。"


# --- folding ----------------------------------------------------------------------------------------


def seed(history, count, **kwargs):
    return [add_turn(history, f"第 {n} 句", **kwargs) for n in range(count)]


async def test_a_fold_updates_the_summary_in_place_and_writes_the_spend(conversation, history, conn, clock):
    turn_ids = seed(history, 31)
    conversation._save("Ongoing:\n- 舊的摘要", 0)
    conversation._client.replies.append({"summary": "Ongoing:\n- 新的摘要"})
    ledger = Ledger(SpendTracker(conn, clock.ms), "memory")
    assert await conversation.fold("openai/gpt-6-luna", ledger) is True
    summary = conversation.summary()
    assert summary.text == "Ongoing:\n- 新的摘要"
    assert summary.through_turn_id == turn_ids[31 - KEEP_TURNS - 1]
    call = conversation._client.calls[0]
    assert "舊的摘要" in call["user"]  # updated, not written anew
    assert "第 0 句" in call["user"] and "第 30 句" not in call["user"]
    assert call["schema_name"] == "raphael_summary"
    assert [tuple(r) for r in conn.execute("SELECT purpose, cost_usd FROM spend")] == [("memory", 0.0004)]
    # The folded turns are still there, and still found.
    assert len(history.after(0)) == 31 and history.search("第")


async def test_a_fold_that_fails_keeps_the_summary_and_waits_before_the_next_try(conversation, history, conn, clock):
    seed(history, 31)
    conversation._save("keep me", 0)
    conversation._client.replies.append(OpenRouterError(503, "server", "down"))
    ledger = Ledger(SpendTracker(conn, clock.ms), "memory")
    assert await conversation.fold("m", ledger) is False
    assert conversation.summary().text == "keep me" and conversation.summary().through_turn_id == 0
    conversation.schedule_fold("m", lambda: ledger)
    assert conversation._folding is None  # paused for a minute
    clock.advance(seconds=61)
    conversation._client.replies.append({"summary": "new"})
    conversation.schedule_fold("m", lambda: ledger)
    await conversation.wait_for_fold()
    assert conversation.summary().text == "new"


async def test_an_empty_summary_from_the_model_is_a_failure(conversation, history, conn, clock):
    seed(history, 31)
    conversation._client.replies.append({"summary": "  "})
    assert await conversation.fold("m", Ledger(SpendTracker(conn, clock.ms))) is False
    assert conversation.summary().text == ""


async def test_no_fold_is_made_when_it_is_not_time(conversation, history, conn, clock):
    seed(history, 5)
    assert await conversation.fold("m", Ledger(SpendTracker(conn, clock.ms))) is False
    assert conversation._client.calls == []


def test_the_summary_is_held_to_fifteen_hundred_tokens():
    text = "\n".join("這一行有二十個字這一行有二十個字這一行有二十" for _ in range(200))
    capped = cap_summary(text)
    assert estimate_tokens(capped) <= SUMMARY_MAX_TOKENS and capped.endswith("二十")
    assert cap_summary("short") == "short"
    assert estimate_tokens(cap_summary("字" * 5000)) <= SUMMARY_MAX_TOKENS


def test_the_summary_can_be_cleared(conversation):
    assert conversation.clear_summary() is False
    conversation._save("x", 3)
    assert conversation.clear_summary() is True
    assert conversation.summary().text == "" and conversation.summary().through_turn_id == 0


def test_the_thresholds_are_the_approved_ones():
    assert conversation_module.FOLD_AT_TOKENS == 12_000
    assert conversation_module.KEEP_TOKENS == 6_000
    assert SUMMARY_MAX_TOKENS == 1_500


async def test_a_long_backlog_is_folded_from_its_newest_turns(conversation, history, conn, clock):
    for n in range(300):
        add_turn(history, f"turn{n} " + "x" * 400)
    conversation._client.replies.append({"summary": "Context:\n..."})
    assert await conversation.fold("m", Ledger(SpendTracker(conn, clock.ms))) is True
    user = conversation._client.calls[0]["user"]
    assert "turn283 " in user and "turn0 " not in user
    assert estimate_tokens(user) < 20_000
    assert conversation.summary().through_turn_id == 300 - KEEP_TURNS


async def test_a_fold_that_was_waiting_when_the_conversation_was_cleared_writes_nothing(conversation, history, conn, clock):
    # The first fold (the summary covers no turn yet) waits for the model when the owner clears
    # the conversation: the ids of the turns start again from 1, and the summary's progress is 0
    # before and after, so only a count of the clears can tell that the fold is stale.
    seed(history, 31)

    def clear_while_waiting(**_):
        history.clear()
        conversation.clear_summary()
        seed(history, 3)  # the new conversation, from turn 1
        return {"summary": "Context:\n- what was cleared"}

    conversation._client.replies.append(clear_while_waiting)
    assert await conversation.fold("m", Ledger(SpendTracker(conn, clock.ms))) is False
    assert conversation.summary() == conversation_module.Summary("", 0, 0)
    assert [t.question for t in conversation.context().turns] == ["第 0 句", "第 1 句", "第 2 句"]
    # The call was made, and paid for: it is in the spend all the same.
    assert conn.execute("SELECT COUNT(*) FROM spend").fetchone()[0] == 1


async def test_clearing_the_summary_while_a_fold_waits_keeps_it_cleared(conversation, history, conn, clock):
    seed(history, 31)

    def clear_while_waiting(**_):
        conversation.clear_summary()
        return {"summary": "Context:\n- forgotten"}

    conversation._client.replies.append(clear_while_waiting)
    assert await conversation.fold("m", Ledger(SpendTracker(conn, clock.ms))) is False
    assert conversation.summary().text == ""
