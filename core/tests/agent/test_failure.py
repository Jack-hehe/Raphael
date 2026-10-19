"""Tests for how a failure of OpenRouter or the network becomes a reason Raphael can give."""

import asyncio

import pytest

from raphael_core.agent.failure import KINDS, AgentFailure, failure_kind
from raphael_core.agent.persona import PHRASES
from raphael_core.brain.openrouter import OpenRouterError


def test_every_kind_of_failure_has_a_phrase_in_every_language():
    assert KINDS == ("no_key", "auth", "budget", "credits", "offline", "error")
    for phrases in PHRASES.values():
        for kind in KINDS:
            assert getattr(phrases, kind)


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (OpenRouterError(0, "auth", "No OpenRouter key"), "auth"),
        (OpenRouterError(401, "auth", "bad key"), "auth"),
        (OpenRouterError(402, "credits", "broke"), "credits"),
        (OpenRouterError(429, "rate_limit", "slow down"), "offline"),
        (OpenRouterError(0, "network", "no route"), "offline"),
        (OpenRouterError(0, "timeout", "slow"), "offline"),
        (OpenRouterError(503, "server", "down"), "offline"),
        (OpenRouterError(400, "bad_request", "no"), "error"),
        (OpenRouterError(200, "bad_response", "html"), "error"),
        (TimeoutError(), "offline"),
        (asyncio.TimeoutError(), "offline"),
        (RuntimeError("boom"), "error"),
        (KeyError("x"), "error"),
    ],
)
def test_an_error_is_the_reason_it_amounts_to(error, kind):
    assert failure_kind(error) == kind


def test_a_failure_carries_its_kind_and_nothing_else():
    failure = AgentFailure("budget")
    assert failure.kind == "budget" and str(failure) == "budget"


def test_a_failure_of_an_unknown_kind_is_an_error():
    assert AgentFailure("whatever").kind == "error"
