"""Provider diagnostics become bounded quota metadata, never public diagnostic text."""

from datetime import datetime, timezone
import pytest
from .test_service import make_rig  # noqa: F401

from raphael_core.agent.quota import Quotas, parse_quota

NOW = int(datetime(2026, 10, 24, 4, tzinfo=timezone.utc).timestamp() * 1000)


def test_codex_rate_limit_windows_use_the_latest_exhausted_reset():
    assert parse_quota({"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": NOW // 1000 + 3600},
                                       "secondary": {"usedPercent": 100, "resetsAt": NOW // 1000 + 7200}}}, NOW) == {
        "exhausted": True, "reset_at_ms": NOW + 7200000,
    }


def test_claude_and_agy_diagnostics_keep_only_reset_metadata():
    assert parse_quota({"error": "Usage limit reached. Resets at 2026-10-24T13:58:00+08:00"}, NOW) == {
        "exhausted": True, "reset_at_ms": NOW + 7080000,
    }
    assert parse_quota({"error": "RESOURCE_EXHAUSTED quota, retry in 30 minutes"}, NOW)["reset_at_ms"] == NOW + 1800000
    assert parse_quota({"error": "network timeout"}, NOW) is None


def test_quota_persists_expires_and_clears_on_recovery(conn):
    now = [NOW]
    events = []
    store = Quotas(conn, lambda: now[0], lambda method, params: events.append((method, params)))
    store.record("codex", {"exhausted": True, "reset_at_ms": NOW + 1000})
    assert Quotas(conn, lambda: now[0]).list() == [{"provider": "codex", "reset_at_ms": NOW + 1000}]
    now[0] += 1000
    assert store.list() == []
    store.record("claude", {"exhausted": True, "reset_at_ms": None})
    store.record("claude", {"exhausted": False, "reset_at_ms": None})
    assert store.list() == []
    assert events[-1][0] == "quota.changed"


@pytest.mark.parametrize(("provider", "packet"), [
    ("codex", {"method": "account/rateLimits/updated", "params": {"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": NOW // 1000 + 3600}}}}),
    ("claude", {"type": "result", "is_error": True, "subtype": "error_during_execution", "result": "Usage limit reached; retry in 1 hour"}),
    ("gemini", {"event": "result", "result": {"status": "ERROR", "error": "RESOURCE_EXHAUSTED; retry in 1 hour"}}),
])
async def test_cli_ingress_records_only_quota_metadata(monkeypatch, provider, packet):
    import asyncio
    import json
    from types import SimpleNamespace
    from raphael_core.agent.cli.drivers import Driver

    monkeypatch.setattr("raphael_core.agent.cli.drivers.wall_clock_ms", lambda: NOW)
    recorded = []
    driver = Driver(provider, ["fake"], quota=lambda *value: recorded.append(value))
    stream = asyncio.StreamReader()
    stream.feed_data(json.dumps(packet).encode() + b"\n")
    driver.child = SimpleNamespace(process=SimpleNamespace(stdout=stream))
    assert await driver._read() == packet
    assert recorded == [(provider, {"exhausted": True, "reset_at_ms": NOW + 3600000})]
    assert driver.exhausted


def test_native_resume_ids_are_isolated_and_legacy_sessions_go_only_to_main(conn):
    from raphael_core.agent.cli.loop import Sessions
    from raphael_core.agent.conversations import Conversations
    conversations = Conversations(conn)
    main = conversations.main(0)
    Sessions(conn)
    with conn:
        conn.execute("INSERT INTO agent_cli_sessions VALUES (?, ?, ?)", (0, "cli:codex", "old-session"))
    sessions = Sessions(conn)
    other = conversations.create(0)["id"]
    assert sessions.get(0, "cli:codex", main) == "old-session"
    assert sessions.get(0, "cli:codex", other) is None
    sessions.put(0, "cli:codex", "new-session", other)
    assert sessions.get(0, "cli:codex", main) == "old-session"
    assert sessions.get(0, "cli:codex", other) == "new-session"
    assert sessions.get(0, "cli:codex") == "old-session"
    assert conn.execute("SELECT count(*) FROM agent_cli_sessions").fetchone()[0] == 0


def test_late_native_session_cannot_restore_a_deleted_conversation(conn):
    from raphael_core.agent.cli.loop import Sessions
    sessions = Sessions(conn)
    conversation = sessions.conversations.create(0)["id"]
    with conn:
        conn.execute("DELETE FROM conversations WHERE id=?", (conversation,))
    sessions.put(0, "cli:codex", "late-session", conversation)
    assert sessions.get(0, "cli:codex", conversation) is None


def test_native_sessions_reject_another_members_conversation(conn):
    from raphael_core.agent.cli.loop import Sessions
    sessions = Sessions(conn)
    conversation = sessions.conversations.create(0)["id"]
    sessions.put(1, "cli:codex", "wrong-member", conversation)
    assert sessions.get(0, "cli:codex", conversation) is None
    sessions.put(0, "cli:codex", "right-member", conversation)
    assert sessions.get(1, "cli:codex", conversation) is None


async def test_usage_allowances_include_persisted_exhaustion_without_provider_prose(make_rig, monkeypatch):
    rig = make_rig()
    async def allowances(_brains):
        return {"brains": [{"id":"codex", "name":"Codex CLI", "windows":[]}]}
    monkeypatch.setattr("raphael_core.agent.cli.allowances.read_allowances", allowances)
    rig.service.quotas.record("claude", {"exhausted":True})
    result = await rig.call("usage.allowances", {})
    assert result["quotas"] == [{"provider":"claude", "reset_at_ms":None}]
    assert result["brains"][0]["id"] == "codex"
