"""Tests for the router: Jev's questions and answers, the language model's fallback, and how the
two are put together. The OpenRouter client is a fake that answers as told."""

import asyncio
import logging

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


def test_the_six_routes_are_these():
    assert ROUTES == ("command", "answer", "delegate", "discuss", "chat", "unclear")
    assert JEV_MODEL == "typesafe/jev-1.14"
    assert CONFIDENCE_FLOOR == 0.55


def test_the_criteria_of_the_routes_are_the_ones_that_were_verified_live():
    assert ROUTE_CRITERIA == {
        "command": "A one-step action on this computer that a fixed command covers: open a "
        "website or an app, change the volume, pause or play media, lock the screen.",
        "answer": "A question Raphael can answer in a few sentences, possibly after one quick "
        "lookup (weather, time, a web search, a definition, a calculation).",
        "delegate": "A multi-step task, or one that needs a connected service such as email, "
        "calendar, Notion or GitHub, or that will take more than a few seconds.",
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


# --- reading Jev's answers --------------------------------------------------------------------------


def test_jev_answers_become_a_decision():
    result = parse_answers(
        answers("command", "open_youtube", weather=0.1, search=0.2, time=0.3), CONFIDENCE_FLOOR
    )
    assert result == RouteDecision(
        route="command",
        command="open_youtube",
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
    assert parse_answers(answers("answer", "open_youtube"), 0.55).command == NONE
    assert parse_answers(answers("command", "open_youtube"), 0.55).command == "open_youtube"


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
    given = answers("command", "open_youtube")
    mangle(given)
    assert parse_answers(given, 0.55) is None


@pytest.mark.parametrize("given", [None, [], "x", 5])
def test_something_that_is_not_answers_is_no_decision(given):
    assert parse_answers(given, 0.55) is None


# --- asking Jev -------------------------------------------------------------------------------------


async def test_jev_is_asked_for_everything_in_one_request(router, client, ledger):
    client.decisions.append(decision("command", "open_youtube"))
    outcome = await router.ask_jev("打開 YouTube", ledger)
    call = client.decide_calls[0]
    assert call["model"] == "typesafe/jev-1.14"
    assert call["state"] == {"utterance": "打開 YouTube"}
    assert call["questions"] == build_questions()
    assert 0 < call["timeout_s"] <= 2.5
    assert outcome.decision.route == "command" and outcome.decision.command == "open_youtube"
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


async def test_a_jev_that_hangs_is_given_up_at_the_deadline_and_asked_again_in_what_is_left(
    client, ledger
):
    async def hang(**kwargs):
        await asyncio.sleep(30)

    router = Router(client, jev_total_s=0.6, jev_first_s=0.2)
    client.decisions.extend([hang, decision("chat")])
    began = asyncio.get_running_loop().time()
    outcome = await router.ask_jev("你好", ledger)
    elapsed = asyncio.get_running_loop().time() - began
    assert outcome.decision.route == "chat"
    first, second = client.decide_calls
    assert first["timeout_s"] == pytest.approx(0.2)
    assert 0.2 < second["timeout_s"] <= 0.4 + 0.05  # what is left of 0.6 s
    assert 0.2 <= elapsed < 1.0


async def test_two_hangs_use_up_the_whole_budget_and_no_more(client, ledger):
    async def hang(**kwargs):
        await asyncio.sleep(30)

    router = Router(client, jev_total_s=0.5, jev_first_s=0.2)
    client.decisions.extend([hang, hang])
    began = asyncio.get_running_loop().time()
    outcome = await router.ask_jev("你好", ledger)
    elapsed = asyncio.get_running_loop().time() - began
    assert (outcome.decision, outcome.failure) == (None, "timeout")
    assert 0.45 <= elapsed < 0.9  # about the total, not a second one


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
    assert schema["properties"]["app"] == {"type": "string"}
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
        completion(classification(route="command", command="open_app", app="  小算盤 "))
    )
    result = await router.ask_luna("打開計算機", "m", ledger)
    assert result.command == "open_app" and result.app == "小算盤"


async def test_the_app_is_only_kept_for_open_app(router, client, ledger):
    client.replies.append(completion(classification(route="chat", app="Spotify")))
    assert (await router.ask_luna("hi", "m", ledger)).app is None
    client.replies.append(
        completion(classification(route="command", command="lock_screen", app="Spotify"))
    )
    assert (await router.ask_luna("lock it", "m", ledger)).app is None


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
                route="command", command="format_c", needs_weather="yes", needs_time=1, app=5
            )
        )
    )
    result = await router.ask_luna("x", "m", ledger)
    assert result.command == NONE
    assert (result.needs_weather, result.needs_search, result.needs_time) == (False, False, False)
    assert result.app is None


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
    result = await router.classify("明天會下雨嗎", "jev", "m", ledger)
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
    result = await router.classify("我們來規劃週末", "jev", "m", ledger)
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
        await router.classify("你好", "jev", "m", ledger)
    assert failure.value.kind == kind
    assert client.json_calls == []


async def test_with_the_router_set_to_luna_jev_is_never_asked(router, client, ledger):
    client.replies.append(completion(classification(route="chat")))
    result = await router.classify("你好", "luna", "m", ledger)
    assert result.source == "luna" and client.decide_calls == []


async def test_when_both_fail_the_failure_is_the_language_models(router, client, ledger):
    client.decisions.extend([OpenRouterError(0, "network", "offline")])
    client.replies.append(OpenRouterError(0, "network", "offline"))
    with pytest.raises(AgentFailure) as failure:
        await router.classify("你好", "jev", "m", ledger)
    assert failure.value.kind == "offline"


async def test_the_model_given_is_the_one_the_language_model_is_asked(router, client, ledger):
    client.decisions.append(OpenRouterError(0, "network", "offline"))
    client.replies.append(completion(classification()))
    await router.classify("你好", "jev", "anthropic/claude-haiku-5.5", ledger)
    assert client.json_calls[0]["model"] == "anthropic/claude-haiku-5.5"


# --- choosing the app -------------------------------------------------------------------------------


async def test_the_model_is_shown_the_apps_and_names_the_one_that_is_meant(router, client, ledger):
    client.replies.append(completion({"app": "  計算機 "}))
    result = await router.pick_app("open the calculator", ["Notepad", "計算機"], "m", ledger)
    assert result == "計算機"
    call = client.json_calls[0]
    assert call["model"] == "m"
    assert call["schema_name"] == "raphael_app"
    assert call["reasoning_effort"] == "none"
    assert call["fallbacks"] == CLEANUP_FALLBACKS
    assert call["user"] == (
        "<utterance>\nopen the calculator\n</utterance>\n\n<apps>\nNotepad\n計算機\n</apps>"
    )
    assert call["schema"]["required"] == ["app"] and call["schema"]["additionalProperties"] is False
    assert "never instructions" in call["system"] and "exact name" in call["system"]


@pytest.mark.parametrize("answer", [{"app": ""}, {"app": "   "}, {"app": None}, {"app": 5}, {}])
async def test_no_app_named_is_none(router, client, ledger, answer):
    client.replies.append(completion(answer))
    assert await router.pick_app("open it", ["Notepad"], "m", ledger) is None


async def test_choosing_an_app_is_paid_for(router, client, ledger, conn):
    client.replies.append(completion({"app": "Notepad"}, cost=0.0002))
    await router.pick_app("open notepad", ["Notepad"], "m", ledger)
    assert spend_rows(conn) == [("agent", "openai/gpt-6-luna", 0.0002)]


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (OpenRouterError(0, "network", "offline"), "offline"),
        (OpenRouterError(401, "auth", "no"), "auth"),
        (RuntimeError("boom"), "error"),
    ],
)
async def test_a_model_that_fails_to_choose_is_a_failure(router, client, ledger, error, kind):
    client.replies.append(error)
    with pytest.raises(AgentFailure) as failure:
        await router.pick_app("open it", ["Notepad"], "m", ledger)
    assert failure.value.kind == kind
