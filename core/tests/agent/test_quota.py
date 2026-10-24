"""Provider diagnostics become bounded quota metadata, never public diagnostic text."""

from datetime import datetime, timezone
import pytest
from .test_service import make_rig  # noqa: F401

from raphael_core.agent.quota import Quotas, parse_quota

NOW = int(datetime(2026, 10, 24, 4, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.mark.parametrize(("status", "utilization", "remaining", "exhausted"), [
    ("allowed", None, None, False),
    ("allowed_warning", .83, 17, False),
    ("rejected", 1, 0, True),
])
def test_claude_rate_limit_events_are_not_errors(status, utilization, remaining, exhausted):
    info = {"status": status, "resetsAt": NOW // 1000 + 3600, "rateLimitType": "five_hour"}
    if utilization is not None:
        info["utilization"] = utilization
    data = parse_quota({"type": "rate_limit_event", "rate_limit_info": info, "session_id": "private"}, NOW)
    assert data == {"exhausted": exhausted, "reset_at_ms": NOW + 3600000 if exhausted else None,
                    "windows": [{"period": "fiveHour", "remaining_percent": remaining,
                                 "resets_at": NOW // 1000 + 3600, "status": status}]}


async def test_real_run_events_persist_allowance_windows_without_private_text(conn, tmp_path, monkeypatch):
    import json
    import sys
    from raphael_core.agent.cli.drivers import Driver

    monkeypatch.setattr("raphael_core.agent.cli.drivers.wall_clock_ms", lambda: NOW)
    script = tmp_path / "claude.py"
    packets = [
        {"type": "rate_limit_event", "session_id": "PRIVATE", "rate_limit_info": {
            "status": "allowed_warning", "rateLimitType": "five_hour", "utilization": .83,
            "resetsAt": NOW // 1000 + 3600, "diagnostic": "PRIVATE"}},
        {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "seven_day", "resetsAt": NOW // 1000 + 7200}},
        {"type": "result", "subtype": "success", "result": "Answer"},
    ]
    script.write_text("import sys\nsys.stdin.readline()\n" + "\n".join(f"print({json.dumps(packet)!r}, flush=True)" for packet in packets), encoding="utf-8")
    store = Quotas(conn, lambda: NOW)
    driver = Driver("claude", [sys.executable, str(script)], quota=store.record)
    events = [event async for event in driver.run(user="Question", system="Persona", endpoint="http://127.0.0.1:1/mcp",
                                                 token="ephemeral", permission=None)]
    assert events[-1].kind == "final"
    assert store.list() == []
    assert Quotas(conn, lambda: NOW + 1000).allowances("claude") == [
        {"period": "fiveHour", "remaining_percent": 17, "resets_at": NOW // 1000 + 3600,
         "status": "allowed_warning", "updated_at_ms": NOW},
        {"period": "weekly", "remaining_percent": None, "resets_at": NOW // 1000 + 7200,
         "status": "allowed", "updated_at_ms": NOW},
    ]


def test_codex_rate_limit_windows_use_the_latest_exhausted_reset():
    assert parse_quota({"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": NOW // 1000 + 3600},
                                       "secondary": {"usedPercent": 100, "resetsAt": NOW // 1000 + 7200}}}, NOW) == {
        "exhausted": True, "reset_at_ms": NOW + 7200000,
    }


def test_codex_run_windows_keep_remaining_values_for_query_failure():
    data = parse_quota({"method": "account/rateLimits/updated", "params": {"rateLimits": {
        "primary": {"usedPercent": 24, "windowDurationMins": 300, "resetsAt": NOW // 1000 + 3600}}}}, NOW)
    assert data["windows"] == [{"period": "fiveHour", "remaining_percent": 76,
                                "resets_at": NOW // 1000 + 3600, "status": "allowed"}]


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


def test_healthy_short_window_and_success_do_not_clear_rejected_weekly_window(conn):
    store = Quotas(conn, lambda: NOW)
    for period, status in (("seven_day", "rejected"), ("five_hour", "allowed")):
        store.record("claude", parse_quota({"type": "rate_limit_event", "rate_limit_info": {
            "status": status, "rateLimitType": period, "resetsAt": NOW // 1000 + 3600}}, NOW))
    store.record("claude", {"exhausted": False})
    assert store.list() == [{"provider": "claude", "reset_at_ms": NOW + 3600000}]
    store.record("claude", parse_quota({"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "rateLimitType": "seven_day"}}, NOW))
    assert store.list() == []


@pytest.mark.parametrize("value", [True, -1, 2, float("nan"), float("inf"), "0.5"])
def test_invalid_claude_utilization_is_unknown(value):
    data = parse_quota({"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "rateLimitType": "five_hour", "utilization": value}}, NOW)
    assert data["windows"][0]["remaining_percent"] is None


def test_unknown_or_overage_events_do_not_claim_subscription_exhaustion():
    for info in ({"status": "rejected", "rateLimitType": "overage"}, {}, None):
        assert parse_quota({"type": "rate_limit_event", "rate_limit_info": info}, NOW) is None


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


async def test_usage_allowances_prefers_live_then_timestamped_reports(make_rig, monkeypatch):
    rig = make_rig()
    async def allowances(_brains):
        return {"brains": [{"id": name, "name": label, "windows": windows} for name, label, windows in [
            ("codex", "Codex", [{"period": "fiveHour", "remaining_percent": 76, "resets_at": None}]),
            ("claude", "Claude", []), ("gemini", "Antigravity", []),
        ]]}
    monkeypatch.setattr("raphael_core.agent.cli.allowances.read_allowances", allowances)
    rig.service.quotas.record("claude", parse_quota({"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "rateLimitType": "seven_day", "utilization": .4}}, NOW))
    result = await rig.call("usage.allowances", {})
    assert [row["source"] for row in result["brains"]] == ["live", "reported", "unavailable"]
    assert result["brains"][1]["windows"][0]["remaining_percent"] == 60
    assert result["brains"][1]["windows"][0]["updated_at_ms"] > 0
