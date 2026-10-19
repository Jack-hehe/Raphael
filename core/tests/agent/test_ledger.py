"""Tests for the ledger: every paid call of a request, written to the spend and added up."""

import logging

import pytest

from raphael_core.agent import ledger as ledger_module
from raphael_core.agent.ledger import PURPOSE, Ledger
from raphael_core.brain.spend import SpendTracker


@pytest.fixture
def spend(conn):
    return SpendTracker(conn, now_ms=lambda: 1_000)


def rows(conn):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT purpose, model, cost_usd, prompt_tokens, completion_tokens FROM spend"
            " ORDER BY id"
        )
    ]


def test_the_spend_of_the_assistant_has_its_own_purpose():
    assert PURPOSE == "agent"


def test_each_paid_call_is_one_row_of_the_spend_and_part_of_the_total(conn, spend):
    ledger = Ledger(spend)
    ledger.add(model="typesafe/jev-1.14", cost_usd=0.00004)
    ledger.add(model="openai/gpt-6-luna", cost_usd=0.001, prompt_tokens=800, completion_tokens=60)
    assert rows(conn) == [
        ("agent", "typesafe/jev-1.14", 0.00004, 0, 0),
        ("agent", "openai/gpt-6-luna", 0.001, 800, 60),
    ]
    assert ledger.total == pytest.approx(0.00104)
    assert spend.month_total() == pytest.approx(0.00104)


def test_a_ledger_starts_at_nothing_and_each_request_has_its_own(conn, spend):
    first, second = Ledger(spend), Ledger(spend)
    assert first.total == 0.0
    first.add(model="m", cost_usd=1.0)
    assert second.total == 0.0 and first.total == 1.0


def test_a_call_that_cost_nothing_is_still_written_down(conn, spend):
    Ledger(spend).add(model="m", cost_usd=0.0)
    assert rows(conn) == [("agent", "m", 0.0, 0, 0)]


@pytest.mark.parametrize("cost", [-1.0, float("nan"), float("inf")])
def test_a_cost_the_spend_refuses_counts_as_nothing_and_is_logged_by_its_type(
    conn, spend, caplog, cost
):
    ledger = Ledger(spend)
    with caplog.at_level(logging.WARNING, logger=ledger_module.logger.name):
        ledger.add(model="m", cost_usd=cost)
    assert rows(conn) == [] and ledger.total == 0.0
    assert "ValueError" in caplog.text


def test_a_database_that_fails_does_not_lose_the_request(conn, spend, caplog):
    conn.close()
    ledger = Ledger(spend)
    with caplog.at_level(logging.WARNING, logger=ledger_module.logger.name):
        ledger.add(model="m", cost_usd=0.5)  # not raised
    assert ledger.total == 0.5  # it was paid for all the same
    assert "ProgrammingError" in caplog.text
