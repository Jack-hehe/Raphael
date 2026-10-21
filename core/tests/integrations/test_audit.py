"""Tests for the audit of the plugins' tool calls."""

import sqlite3

import pytest

from raphael_core.integrations import audit
from raphael_core.integrations.audit import AuditLog


def test_a_call_is_written_down_with_the_time_it_was_made(conn):
    log = AuditLog(conn, now_ms=lambda: 1_700_000_000_000)
    log.add(server="Notes", tool="look", tier="T0", approver="auto", outcome="ok")
    [entry] = log.recent()
    assert (entry.at_ms, entry.server, entry.tool) == (1_700_000_000_000, "Notes", "look")
    assert (entry.tier, entry.approver, entry.outcome) == ("T0", "auto", "ok")


def test_a_tool_with_no_tier_is_written_down_with_none(conn):
    log = AuditLog(conn)
    log.add(server="Notes", tool="wipe", tier=None, approver="none", outcome="blocked")
    assert log.recent()[0].tier is None


def test_the_audit_has_no_column_for_what_a_call_was_given_or_answered(conn):
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(tool_audit)")]
    assert columns == ["id", "at_ms", "server", "tool", "tier", "approver", "outcome"]


def test_the_newest_come_first_and_the_list_is_limited(conn):
    ticks = iter(range(1, 1000))
    log = AuditLog(conn, now_ms=lambda: next(ticks))
    for n in range(70):
        log.add(server="S", tool=f"t{n}", tier="T1", approver="auto", outcome="ok")
    entries = log.recent(50)
    assert [e.tool for e in entries][:3] == ["t69", "t68", "t67"]
    assert len(entries) == 50
    assert len(log.recent(1000)) == 70  # a limit is clamped, not obeyed to the letter
    assert len(log.recent(0)) == 1


def test_the_oldest_rows_go_as_new_ones_come_in(conn, monkeypatch):
    monkeypatch.setattr(audit, "KEEP_ROWS", 5)
    log = AuditLog(conn)
    for n in range(8):
        log.add(server="S", tool=f"t{n}", tier="T1", approver="auto", outcome="ok")
    assert [e.tool for e in log.recent()] == ["t7", "t6", "t5", "t4", "t3"]


@pytest.mark.parametrize(
    "approver, outcome", [("someone", "ok"), ("auto", "great"), ("", ""), (None, "ok")]
)
def test_an_approver_or_outcome_it_does_not_know_is_a_bug_and_is_refused(conn, approver, outcome):
    with pytest.raises(ValueError):
        AuditLog(conn).add(server="S", tool="t", tier="T0", approver=approver, outcome=outcome)


def test_a_database_that_fails_does_not_stop_the_call(conn, caplog):
    conn.execute("DROP TABLE tool_audit")
    with caplog.at_level("WARNING"):
        AuditLog(conn).add(server="S", tool="t", tier="T0", approver="auto", outcome="ok")
    assert "OperationalError" in caplog.text
    with pytest.raises(sqlite3.OperationalError):
        AuditLog(conn).recent()
