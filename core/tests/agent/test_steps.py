"""Tests for the reasons a step keeps: one word out of a fixed few, never a message."""

import pytest

from raphael_core.agent.steps import REASONS, reason_of


def test_a_step_that_went_well_has_no_reason():
    assert reason_of("ok", "auth") is None


@pytest.mark.parametrize(
    ("outcome", "kind", "reason"),
    [
        ("error", "auth", "auth"),  # a plugin that wants a new sign-in
        ("error", "bad_arguments", "bad_arguments"),
        ("error", "tool_error", "tool_error"),
        ("error", "not_found", "not_found"),  # the weather of a city nobody knows
        ("error", "offline", "network"),  # the model's word for it
        ("error", "http_error", "network"),  # the lookups'
        ("error", "bad_response", "protocol"),
        ("blocked", "changed", "blocked"),
        ("timeout", None, "timeout"),
        ("blocked", None, "blocked"),
        ("error", None, "error"),
        ("error", "Calendar 'Dinner with Mia' was not found", "error"),  # a message is no kind
    ],
)
def test_the_kind_of_a_failure_becomes_one_of_the_reasons(outcome, kind, reason):
    assert reason_of(outcome, kind) == reason
    assert reason in REASONS
