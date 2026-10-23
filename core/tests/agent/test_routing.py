"""Tests for the router: Jev's questions and answers, the language model's fallback, and how the
two are put together. The OpenRouter client is a fake that answers as told."""

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from raphael_core.agent import routing
from raphael_core.agent.commands import COMMAND_NAMES, COMMANDS, NONE
from raphael_core.agent.failure import AgentFailure
from raphael_core.agent.ledger import Ledger
from raphael_core.agent.routing import (
    CONFIDENCE_FLOOR,
    JEV_MODEL,
    ROUTE_CRITERIA,
    ROUTES,
    OpenAnswer,
    Router,
    RouteDecision,
    build_questions,
    build_state,
    parse_answers,
)
from raphael_core.brain.openrouter import CLEANUP_FALLBACKS, Decision, OpenRouterError
from raphael_core.brain.spend import SpendTracker

from .conftest import answers, classification, completion, decision


@pytest.fixture
def ledger(conn):
    return Ledger(SpendTracker(conn, now_ms=lambda: 1_000))


@pytest.fixture
def router(client):
    return Router(client)


def spend_rows(conn):
    return [tuple(r) for r in conn.execute("SELECT purpose, model, cost_usd FROM spend")]


# --- the questions ---------------------------------------------------------------------------------


def test_the_seven_routes_are_these():
    assert ROUTES == ("command", "answer", "operate", "delegate", "discuss", "chat", "unclear")
    assert JEV_MODEL == "typesafe/jev-1.14"
    assert CONFIDENCE_FLOOR == 0.55


def test_the_criteria_of_the_routes_are_these():
    assert ROUTE_CRITERIA == {
        "command": "A one-step action on this computer that a fixed command covers: open any "
        "website, app or Windows settings page (or search on a site), change the volume, pause or "
        "play media, lock the screen.",
        "answer": "A question Raphael can answer in a few sentences, possibly after one quick "
        "lookup (weather, time, a web search, a definition, a calculation), including how a "
        "background task is going or what it did (state.background lists them).",
        "operate": "A task done by using a desktop app on this computer's screen: clicking, typing "
        "or choosing inside an app such as Calculator, Excel, File Explorer or the Settings app (for "
        "example: compute something in Calculator, type a line into a document, turn on a switch in "
        "Settings), which no fixed command covers.",
        "delegate": "A multi-step task, or one that needs a connected service such as email, "
        "calendar, Notion or GitHub, or that will take more than a few seconds, and that is not done "
        "by operating an app on the screen. Not a question about a task that was already handed over.",
        "discuss": "The speaker wants to think something through together or plan, an "
        "open-ended conversation rather than one answer.",
        "chat": "Small talk, a greeting or thanks, needing no action and no facts.",
        "unclear": "Too garbled or incomplete to act on; Raphael should ask what was meant.",
    }
    assert tuple(ROUTE_CRITERIA) == ROUTES


def test_one_request_asks_the_route_the_command_and_three_things_that_may_be_needed():
    questions = build_questions()
    assert list(questions) == ["route", "command", "needs_weather", "needs_search", "needs_time"]
    route = questions["route"]
    assert route["type"] == "choice" and route["criteria"] == ROUTE_CRITERIA
    command = questions["command"]
    assert command["type"] == "choice"
    assert list(command["criteria"]) == [*COMMAND_NAMES, NONE]
    for name, spec in COMMANDS.items():
        assert command["criteria"][name] == spec.guidance
    assert command["criteria"][NONE]
    for name in ("needs_weather", "needs_search", "needs_time"):
        question = questions[name]
        assert question["type"] == "noul"
        assert list(question["criteria"]) == ["true", "false"]
        assert all(isinstance(text, str) and text for text in question["criteria"].values())


def test_every_question_says_where_the_request_is():
    for question in build_questions().values():
        assert "state.utterance" in question["instructions"]


def test_the_questions_do_not_change_between_requests():
    assert build_questions() == build_questions()
    assert build_questions() is not build_questions()  # a copy: nobody can change the originals


def test_the_state_is_the_utterance():
    assert build_state("打開 YouTube") == {"utterance": "打開 YouTube"}


def test_the_state_has_one_line_about_the_background_when_there_is_something_to_say():
    assert build_state("好了嗎", "1 running (step 4)") == {
        "utterance": "好了嗎",
        "background": "1 running (step 4)",
    }
    assert build_state("好了嗎", "") == {"utterance": "好了嗎"}
    assert build_state("好了嗎", None) == {"utterance": "好了嗎"}


async def test_jev_is_told_about_the_background_in_the_state_and_asked_nothing_more(
    router, client, ledger
):
    client.decisions.append(decision("answer"))
    await router.classify("寄出去了嗎", "m", ledger, background="1 done 3 min ago")
    [call] = client.decide_calls
    assert call["state"] == {"utterance": "寄出去了嗎", "background": "1 done 3 min ago"}
    assert list(call["questions"]) == list(build_questions())  # no new question, no new call


async def test_the_language_model_reads_the_background_too_as_data(router, client, ledger):
    client.decisions.extend([Decision(answers={}, cost_usd=0.0, latency_ms=1)])
    client.replies.append(completion(classification(route="answer")))
    await router.classify("寄出去了嗎", "m", ledger, background="1 done 3 min ago")
    assert "<background>\n1 done 3 min ago\n</background>" in client.json_calls[0]["user"]
    client.replies.append(completion(classification(route="answer")))
    await router.ask_luna("寄出去了嗎", "m", ledger)  # nothing to say: nothing is added
    assert "background" not in client.json_calls[1]["user"]


# --- reading Jev's answers --------------------------------------------------------------------------


def test_jev_answers_become_a_decision():
    result = parse_answers(
        answers("command", "open", weather=0.1, search=0.2, time=0.3), CONFIDENCE_FLOOR
    )
    assert result == RouteDecision(
        route="command",
        command="open",
        needs_weather=False,
        needs_search=False,
        needs_time=False,
        source="jev",
        confidence=0.93,
    )


@pytest.mark.parametrize(
    ("value", "needed"), [(0.99, True), (0.5, True), (0.4999, False), (0.0, False), (1, True)]
)
def test_a_noul_answer_is_true_from_one_half(value, needed):
    result = parse_answers(answers("answer", weather=value, search=value, time=value), 0.55)
    assert (result.needs_weather, result.needs_search, result.needs_time) == (needed,) * 3


def test_the_command_is_only_kept_when_the_route_is_a_command():
    assert parse_answers(answers("answer", "open"), 0.55).command == NONE
    assert parse_answers(answers("command", "open"), 0.55).command == "open"


@pytest.mark.parametrize("route", ROUTES)
def test_every_route_is_read(route):
    assert parse_answers(answers(route), 0.55).route == route


def test_a_route_jev_is_not_sure_of_is_no_decision():
    assert parse_answers(answers("answer", route_confidence=0.54), 0.55) is None
    assert parse_answers(answers("answer", route_confidence=0.55), 0.55) is not None


def test_a_command_jev_is_not_sure_of_is_no_decision_but_none_is_always_fine():
    assert parse_answers(answers("command", "volume_up", command_confidence=0.3), 0.55) is None
    unsure_none = parse_answers(answers("command", NONE, command_confidence=0.3), 0.55)
    assert unsure_none.route == "command" and unsure_none.command == NONE
    # and the command does not matter for a request that is no command
    assert parse_answers(answers("chat", "volume_up", command_confidence=0.1), 0.55) is not None


def test_an_answer_without_its_type_is_read_by_its_keys():
    given = answers("command", "open")
    for answer in given.values():
        del answer["type"]
    result = parse_answers(given, 0.55)
    assert (result.route, result.command) == ("command", "open")


def test_a_plain_true_or_false_is_a_noul_answer_too():
    given = answers("answer")
    given["needs_weather"] = {"type": "noul", "noul": True}
    given["needs_time"] = {"noul": False}
    result = parse_answers(given, 0.55)
    assert (result.needs_weather, result.needs_time) == (True, False)


@pytest.mark.parametrize(
    "mangle",
    [
        lambda a: a.pop("route"),
        lambda a: a.pop("command"),
        lambda a: a.pop("needs_weather"),
        lambda a: a.update(route="chat"),
        lambda a: a.update(route={"type": "choice", "choice": "dance", "confidence": 0.9}),
        lambda a: a.update(route={"type": "choice", "choice": 5, "confidence": 0.9}),
        lambda a: a.update(route={"type": "noul", "noul": 0.9}),
        lambda a: a.update(route={"type": "choice", "choice": "chat"}),
        lambda a: a.update(route={"type": "choice", "choice": "chat", "confidence": "high"}),
        lambda a: a.update(route={"type": "choice", "choice": "chat", "confidence": True}),
        lambda a: a.update(command={"type": "choice", "choice": "format_c", "confidence": 0.9}),
        lambda a: a.update(needs_time={"type": "noul"}),
        lambda a: a.update(needs_time={"type": "noul", "noul": "yes"}),
        lambda a: a.update(needs_time="true"),
        lambda a: a.update(needs_search={"type": "choice", "choice": "true", "confidence": 1}),
    ],
)
def test_answers_that_are_not_what_was_asked_for_are_no_decision(mangle):
    given = answers("command", "open")
    mangle(given)
    assert parse_answers(given, 0.55) is None


@pytest.mark.parametrize("given", [None, [], "x", 5])
def test_something_that_is_not_answers_is_no_decision(given):
    assert parse_answers(given, 0.55) is None


# --- asking Jev -------------------------------------------------------------------------------------


async def test_jev_is_asked_for_everything_in_one_request(router, client, ledger):
    client.decisions.append(decision("command", "open"))
    outcome = await router.ask_jev("打開 YouTube", ledger)
    call = client.decide_calls[0]
    assert call["model"] == "typesafe/jev-1.14"
    assert call["state"] == {"utterance": "打開 YouTube"}
    assert call["questions"] == build_questions()
    assert 0 < call["timeout_s"] <= 2.5
    assert outcome.decision.route == "command" and outcome.decision.command == "open"
    assert outcome.failure is None
    assert len(client.decide_calls) == 1


async def test_what_jev_cost_is_written_to_the_spend(router, client, ledger, conn):
    client.decisions.append(decision(cost=0.00004))
    await router.ask_jev("你好", ledger)
    assert spend_rows(conn) == [("agent", "typesafe/jev-1.14", 0.00004)]


async def test_an_answer_jev_was_unsure_of_was_still_paid_for(router, client, ledger, conn):
    client.decisions.append(decision("answer", route_confidence=0.4))
    outcome = await router.ask_jev("那個", ledger)
    assert outcome.decision is None and outcome.failure == "unsure"
    assert spend_rows(conn) == [("agent", "typesafe/jev-1.14", 0.00004)]


async def test_answers_that_make_no_sense_are_a_bad_response_but_were_paid_for(
    router, client, ledger, conn
):
    client.decisions.append(Decision(answers={"route": 5}, cost_usd=0.00004, latency_ms=1))
    outcome = await router.ask_jev("你好", ledger)
    assert outcome.decision is None and outcome.failure == "bad_response"
    assert len(spend_rows(conn)) == 1


@pytest.mark.parametrize(
    "first",
    [OpenRouterError(0, "timeout", "slow"), OpenRouterError(503, "server", "down")],
    ids=["timeout", "5xx"],
)
async def test_a_timeout_or_a_server_error_is_tried_once_more(router, client, ledger, conn, first):
    client.decisions.extend([first, decision("chat")])
    outcome = await router.ask_jev("你好", ledger)
    assert outcome.decision.route == "chat" and outcome.failure is None
    assert len(client.decide_calls) == 2
    assert len(spend_rows(conn)) == 1  # only the answer that came was paid for


async def test_two_failures_in_a_row_are_a_failure_after_two_tries_and_no_more(
    router, client, ledger
):
    client.decisions.extend([OpenRouterError(503, "server", "down")] * 2)
    outcome = await router.ask_jev("你好", ledger)
    assert (outcome.decision, outcome.failure) == (None, "server")
    assert len(client.decide_calls) == 2


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (OpenRouterError(401, "auth", "no"), "auth"),
        (OpenRouterError(402, "credits", "broke"), "credits"),
        (OpenRouterError(429, "rate_limit", "slow"), "rate_limit"),
        (OpenRouterError(0, "network", "offline"), "network"),
        (OpenRouterError(400, "bad_request", "no"), "bad_request"),
        (OpenRouterError(200, "bad_response", "html"), "bad_response"),
    ],
)
async def test_any_other_failure_is_not_tried_again(router, client, ledger, error, failure):
    client.decisions.append(error)
    outcome = await router.ask_jev("你好", ledger)
    assert (outcome.decision, outcome.failure) == (None, failure)
    assert len(client.decide_calls) == 1


class Hang:
    """A Jev that never answers, and the router's clock. Each call is given up by the router at its
    deadline (the hang is cancelled), and the clock then moves on by the whole time the call was
    given. So what the router leaves for the next try is exact, which timing the hangs is not: the
    clock of asyncio on Windows ticks every 15.6 ms, and a timer may fire before its time."""

    def __init__(self):
        self.now = 1_000.0
        self.given_up = 0

    def clock(self):
        return self.now

    async def __call__(self, **kwargs):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.given_up += 1
            self.now += kwargs["timeout_s"]
            raise


@pytest.fixture
def waits(monkeypatch):
    """The time limit the router puts on each thing it awaits (asyncio.wait_for), in order."""
    limits = []

    def wait_for(awaitable, timeout):
        limits.append(timeout)
        return asyncio.wait_for(awaitable, timeout)

    monkeypatch.setattr(routing, "asyncio", SimpleNamespace(wait_for=wait_for))
    return limits


async def test_a_jev_that_hangs_is_given_up_at_the_deadline_and_asked_again_in_what_is_left(
    client, ledger, waits
):
    hang = Hang()
    router = Router(client, jev_total_s=0.6, jev_first_s=0.2, clock=hang.clock)
    client.decisions.extend([hang, decision("chat")])
    outcome = await router.ask_jev("你好", ledger)
    assert outcome.decision.route == "chat" and hang.given_up == 1
    # The first try has its share, the second what is left of 0.6 s; each is waited for as long as
    # it is told it may take.
    budgets = [call["timeout_s"] for call in client.decide_calls]
    assert budgets == pytest.approx([0.2, 0.4]) and waits == budgets


async def test_two_hangs_use_up_the_whole_budget_and_no_more(client, ledger, waits):
    hang = Hang()
    router = Router(client, jev_total_s=0.5, jev_first_s=0.2, clock=hang.clock)
    client.decisions.extend([hang, hang])
    began = time.perf_counter()
    outcome = await router.ask_jev("你好", ledger)
    assert (outcome.decision, outcome.failure) == (None, "timeout")
    assert hang.given_up == 2  # each was given up at its deadline
    budgets = [call["timeout_s"] for call in client.decide_calls]
    assert budgets == pytest.approx([0.2, 0.3]) and waits == budgets  # 0.5 s in all
    assert time.perf_counter() - began < 5.0  # not the 30 s of a hang


async def test_a_retry_with_too_little_time_left_is_not_made(client, ledger):
    async def hang(**kwargs):
        await asyncio.sleep(30)

    router = Router(client, jev_total_s=0.3, jev_first_s=0.28)
    client.decisions.extend([hang, decision("chat")])
    outcome = await router.ask_jev("你好", ledger)
    assert (outcome.decision, outcome.failure) == (None, "timeout")
    assert len(client.decide_calls) == 1  # 0.02 s is no time to ask


async def test_what_jev_was_asked_is_not_logged(router, client, ledger, caplog):
    client.decisions.append(OpenRouterError(503, "server", "秘密 message"))
    client.decisions.append(OpenRouterError(503, "server", "秘密 message"))
    with caplog.at_level(logging.DEBUG, logger=routing.logger.name):
        await router.ask_jev("我的秘密問題", ledger)
    assert "秘密" not in caplog.text


# --- asking the language model ----------------------------------------------------------------------


async def test_the_language_model_is_asked_for_the_same_outputs_as_json(router, client, ledger):
    client.replies.append(completion(classification(route="command", command="volume_up")))
    result = await router.ask_luna("音量調大", "openai/gpt-6-luna", ledger)
    call = client.json_calls[0]
    assert call["model"] == "openai/gpt-6-luna"
    assert call["fallbacks"] == CLEANUP_FALLBACKS
    assert call["schema_name"] == "raphael_route"
    assert call["reasoning_effort"] == "none"
    assert call["user"] == "<utterance>\n音量調大\n</utterance>"
    schema = call["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["route"]["enum"] == list(ROUTES)
    assert schema["properties"]["command"]["enum"] == [*COMMAND_NAMES, NONE]
    for flag in ("needs_weather", "needs_search", "needs_time"):
        assert schema["properties"][flag] == {"type": "boolean"}
    assert schema["properties"]["target"] == {"type": "string"}
    assert result == RouteDecision(
        route="command", command="volume_up", source="luna", confidence=None
    )


async def test_the_system_prompt_of_the_fallback_has_the_same_criteria(router, client, ledger):
    client.replies.append(completion(classification()))
    await router.ask_luna("你好", "m", ledger)
    system = client.json_calls[0]["system"]
    for text in ROUTE_CRITERIA.values():
        assert text in system
    for command in COMMANDS.values():
        assert command.name in system and command.guidance in system
    assert "weather" in system and "web" in system and "time" in system
    assert "<utterance>" in system  # told that the request is inside the tags, as data


async def test_the_language_model_decides_what_is_needed_and_which_app(router, client, ledger):
    client.replies.append(
        completion(
            classification(
                route="answer", needs_weather=True, needs_search=True, needs_time=True
            )
        )
    )
    result = await router.ask_luna("明天要帶傘嗎", "m", ledger)
    assert (result.needs_weather, result.needs_search, result.needs_time) == (True, True, True)
    client.replies.append(
        completion(classification(route="command", command="open", target="  小算盤 "))
    )
    result = await router.ask_luna("打開計算機", "m", ledger)
    assert result.command == "open" and result.target == "小算盤"


async def test_the_target_is_only_kept_for_open(router, client, ledger):
    client.replies.append(completion(classification(route="chat", target="Spotify")))
    assert (await router.ask_luna("hi", "m", ledger)).target is None
    client.replies.append(
        completion(classification(route="command", command="lock_screen", target="Spotify"))
    )
    assert (await router.ask_luna("lock it", "m", ledger)).target is None


async def test_the_command_is_only_kept_for_a_command_route(router, client, ledger):
    client.replies.append(completion(classification(route="chat", command="lock_screen")))
    assert (await router.ask_luna("hi", "m", ledger)).command == NONE


@pytest.mark.parametrize(
    "data",
    [
        classification(route="dance"),
        classification(route=None),
        {"command": "none"},
        {},
    ],
)
async def test_a_classification_without_a_route_is_a_bad_response(router, client, ledger, data):
    client.replies.append(completion(data))
    with pytest.raises(AgentFailure) as failure:
        await router.ask_luna("hi", "m", ledger)
    assert failure.value.kind == "error"


async def test_a_command_the_registry_lacks_is_none_and_flags_that_are_no_booleans_are_off(
    router, client, ledger
):
    client.replies.append(
        completion(
            classification(
                route="command", command="format_c", needs_weather="yes", needs_time=1, target=5
            )
        )
    )
    result = await router.ask_luna("x", "m", ledger)
    assert result.command == NONE
    assert (result.needs_weather, result.needs_search, result.needs_time) == (False, False, False)
    assert result.target is None


async def test_what_the_language_model_cost_is_written_to_the_spend(router, client, ledger, conn):
    client.replies.append(completion(classification(), cost=0.0003, model="openai/gpt-6-luna"))
    await router.ask_luna("hi", "m", ledger)
    assert spend_rows(conn) == [("agent", "openai/gpt-6-luna", 0.0003)]


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (OpenRouterError(401, "auth", "no"), "auth"),
        (OpenRouterError(402, "credits", "broke"), "credits"),
        (OpenRouterError(0, "network", "offline"), "offline"),
        (OpenRouterError(0, "timeout", "slow"), "offline"),
        (OpenRouterError(503, "server", "down"), "offline"),
        (OpenRouterError(400, "bad_request", "no"), "error"),
        (RuntimeError("boom"), "error"),
    ],
)
async def test_a_failing_language_model_is_a_failure_of_a_kind(
    router, client, ledger, error, kind
):
    client.replies.append(error)
    with pytest.raises(AgentFailure) as failure:
        await router.ask_luna("hi", "m", ledger)
    assert failure.value.kind == kind


async def test_a_language_model_that_hangs_is_a_timeout(client, ledger):
    async def hang(**kwargs):
        await asyncio.sleep(30)

    client.replies.append(hang)
    router = Router(client, luna_s=0.1)
    with pytest.raises(AgentFailure) as failure:
        await router.ask_luna("hi", "m", ledger)
    assert failure.value.kind == "offline"


# --- both together ----------------------------------------------------------------------------------


async def test_jev_decides_and_the_language_model_is_not_asked(router, client, ledger):
    client.decisions.append(decision("answer", weather=0.9))
    result = await router.classify("明天會下雨嗎", "m", ledger)
    assert result.source == "jev" and result.route == "answer" and result.needs_weather
    assert client.json_calls == []


@pytest.mark.parametrize(
    "jev",
    [
        decision("answer", route_confidence=0.2),
        Decision(answers={}, cost_usd=0.0, latency_ms=1),
        OpenRouterError(0, "timeout", "slow"),
        OpenRouterError(503, "server", "down"),
        OpenRouterError(0, "network", "offline"),
        OpenRouterError(429, "rate_limit", "slow"),
        OpenRouterError(400, "bad_request", "no"),
    ],
    ids=["unsure", "no answers", "timeout", "5xx", "network", "rate limit", "bad request"],
)
async def test_when_jev_fails_or_is_unsure_the_language_model_decides(router, client, ledger, jev):
    client.decisions.extend([jev, jev])  # a failure that is retried is asked twice
    client.replies.append(completion(classification(route="discuss")))
    result = await router.classify("我們來規劃週末", "m", ledger)
    assert result.source == "luna" and result.route == "discuss"
    assert len(client.json_calls) == 1


@pytest.mark.parametrize(
    ("error", "kind"),
    [(OpenRouterError(401, "auth", "no"), "auth"), (OpenRouterError(402, "credits", "x"), "credits")],
)
async def test_a_key_or_a_balance_jev_cannot_use_is_not_tried_again_with_the_language_model(
    router, client, ledger, error, kind
):
    client.decisions.append(error)
    with pytest.raises(AgentFailure) as failure:
        await router.classify("你好", "m", ledger)
    assert failure.value.kind == kind
    assert client.json_calls == []


async def test_when_both_fail_the_failure_is_the_language_models(router, client, ledger):
    client.decisions.extend([OpenRouterError(0, "network", "offline")])
    client.replies.append(OpenRouterError(0, "network", "offline"))
    with pytest.raises(AgentFailure) as failure:
        await router.classify("你好", "m", ledger)
    assert failure.value.kind == "offline"


async def test_the_model_given_is_the_one_the_language_model_is_asked(router, client, ledger):
    client.decisions.append(OpenRouterError(0, "network", "offline"))
    client.replies.append(completion(classification()))
    await router.classify("你好", "anthropic/claude-haiku-5.5", ledger)
    assert client.json_calls[0]["model"] == "anthropic/claude-haiku-5.5"


# --- finding what to open ---------------------------------------------------------------------------


async def test_the_model_is_shown_the_request_and_the_apps_and_answers_with_a_kind(
    router, client, ledger
):
    client.replies.append(
        completion({"kind": "site", "name": "  Reddit ", "url": " https://www.reddit.com/ "})
    )
    result = await router.resolve_open("open reddit", ["Notepad", "計算機"], "m", ledger)
    assert result == OpenAnswer("site", "Reddit", "https://www.reddit.com/")
    call = client.json_calls[0]
    assert call["model"] == "m"
    assert call["schema_name"] == "raphael_open"
    assert call["reasoning_effort"] == "none"
    assert call["fallbacks"] == CLEANUP_FALLBACKS
    assert call["user"] == "<utterance>\nopen reddit\n</utterance>\n\n<apps>\nNotepad\n計算機\n</apps>"
    schema = call["schema"]
    assert schema["required"] == ["kind", "name", "url"] and schema["additionalProperties"] is False
    assert schema["properties"]["kind"]["enum"] == ["app", "site", "search", "none"]
    system = call["system"]
    assert "never instructions" in system and "exact name" in system
    assert "https" in system and "search" in system


async def test_no_site_is_named_in_what_the_model_is_told(router, client, ledger):
    client.replies.append(completion({"kind": "none", "name": "", "url": ""}))
    await router.resolve_open("open it", [], "m", ledger)
    system = client.json_calls[0]["system"].lower()
    for site in ("youtube", "gmail", "github", "reddit", "notion"):
        assert site not in system


async def test_a_search_on_a_site_is_an_answer_of_its_own(router, client, ledger):
    url = "https://www.youtube.com/results?search_query=lofi"
    client.replies.append(completion({"kind": "search", "name": "YouTube", "url": url}))
    result = await router.resolve_open("在 YouTube 搜尋 lofi", [], "m", ledger)
    assert result == OpenAnswer("search", "YouTube", url)


async def test_an_app_is_an_answer_with_the_name_from_the_list(router, client, ledger):
    client.replies.append(completion({"kind": "app", "name": "計算機", "url": ""}))
    assert await router.resolve_open("open the calculator", ["計算機"], "m", ledger) == OpenAnswer(
        "app", "計算機", ""
    )


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"kind": "none", "name": "", "url": ""},
        {"kind": "folder", "name": "x", "url": "https://x.example.com/"},
        {"kind": 5, "name": "x", "url": "https://x.example.com/"},
        {"kind": "site", "name": 5, "url": None},
        "text",
        None,
    ],
)
async def test_an_answer_that_names_nothing_is_none(router, client, ledger, answer):
    client.replies.append(completion(answer))
    result = await router.resolve_open("open it", ["Notepad"], "m", ledger)
    assert result is None or result.kind == "none"


async def test_finding_what_to_open_is_paid_for(router, client, ledger, conn):
    client.replies.append(
        completion({"kind": "site", "name": "x", "url": "https://x.example.com/"}, cost=0.0002)
    )
    await router.resolve_open("open x", [], "m", ledger)
    assert spend_rows(conn) == [("agent", "openai/gpt-6-luna", 0.0002)]


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (OpenRouterError(0, "network", "offline"), "offline"),
        (OpenRouterError(401, "auth", "no"), "auth"),
        (RuntimeError("boom"), "error"),
    ],
)
async def test_a_model_that_fails_to_find_it_is_a_failure(router, client, ledger, error, kind):
    client.replies.append(error)
    with pytest.raises(AgentFailure) as failure:
        await router.resolve_open("open it", ["Notepad"], "m", ledger)
    assert failure.value.kind == kind


async def test_what_was_asked_and_answered_is_not_logged(router, client, ledger, caplog):
    caplog.set_level(logging.DEBUG)
    client.replies.append(
        completion({"kind": "site", "name": "秘密站", "url": "https://secret.example.com/"})
    )
    await router.resolve_open("打開秘密站", [], "m", ledger)
    assert "秘密" not in caplog.text and "secret.example" not in caplog.text
