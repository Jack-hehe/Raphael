"""Usage analytics through the read-only RPC, with real synthetic database rows."""

from datetime import UTC, datetime, timedelta, timezone
import sys

import pytest

from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.providers.registry import ProviderRegistry
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router


NOW = int(datetime(2026, 10, 23, 12, tzinfo=UTC).timestamp() * 1000)


def router_for(conn, **kwargs):
    router = Router()
    Brain(conn, object(), MemoryKeyStore(), now_ms=lambda: NOW,
          tz=timezone(timedelta(hours=8)), **kwargs).register(router)
    return router


def spend(conn, at, cost, model="vendor/model", purpose="agent"):
    conn.execute(
        "INSERT INTO spend (created_at_ms,purpose,model,cost_usd,prompt_tokens,completion_tokens)"
        " VALUES (?,?,?,?,1,2)", (at, purpose, model, cost),
    )


async def test_local_period_includes_midnight_and_excludes_next_midnight(conn):
    # Oct 17 00:00 in Taipei; the seven-day period ends Oct 23 inclusive.
    start = int(datetime(2026, 10, 16, 16, tzinfo=UTC).timestamp() * 1000)
    spend(conn, start - 1, 99)
    spend(conn, start, 0.25)
    spend(conn, start + 7 * 86400000 - 1, 0.75, purpose="cleanup")
    spend(conn, start + 7 * 86400000, 99)
    result = await router_for(conn).call("usage.analytics", {"range": "7d"})
    assert result["days"] == [f"2026-10-{d}" for d in range(17, 24)]
    assert result["cost_by"]["purpose"]["total"] == 1
    agent = result["cost_by"]["purpose"]["series"][1]
    assert agent == {"key": "agent", "label": "agent", "values": [0.25, 0, 0, 0, 0, 0, 0],
                     "total": 0.25, "share": 0.25}
    assert result["messages_by"]["source"]["total"] == 0
    assert result["dictation"]["dictations"] == [0] * 7


async def test_top_five_other_and_historical_provider_identity(conn):
    for i in range(7):
        spend(conn, NOW, 7 - i, model=f"@deleted:model:{i}", purpose=f"purpose{i}")
    spend(conn, NOW, 2, model="plain/model")
    result = await router_for(conn).call("usage.analytics", {"range": "30d", "tz": "Asia/Taipei"})
    purpose = result["cost_by"]["purpose"]
    assert purpose["total"] == 30
    assert len(purpose["series"]) == 6
    assert purpose["series"][-1]["key"] == "__other__"
    assert purpose["series"][-1]["total"] == 5
    assert sum(s["share"] for s in purpose["series"]) == pytest.approx(1)
    assert len(purpose["series"][-1]["values"]) == 30
    providers = result["cost_by"]["provider"]["series"]
    assert [(s["key"], s["total"]) for s in providers] == [("deleted", 28), ("openrouter", 2)]
    assert providers[1]["label"] == "OpenRouter"
    assert result["cost_by"]["model"]["series"][0]["key"] == "@deleted:model:0"


async def test_timezone_uses_dst_calendar_days_and_explicit_end_day(conn):
    # The New York fallback day is 25 hours; both 01:30 occurrences belong to Nov 1.
    for hour in (5, 6):
        spend(conn, int(datetime(2026, 11, 1, hour, 30, tzinfo=UTC).timestamp() * 1000), 1)
    spend(conn, int(datetime(2026, 11, 2, 4, 30, tzinfo=UTC).timestamp() * 1000), 1)
    spend(conn, int(datetime(2026, 11, 2, 5, tzinfo=UTC).timestamp() * 1000), 100)
    result = await router_for(conn).call("usage.analytics", {
        "range": "7d", "end_day": "2026-11-01", "tz": "America/New_York",
    })
    assert result["days"][0] == "2026-10-26"
    assert result["cost_by"]["purpose"]["total"] == 3
    assert result["cost_by"]["purpose"]["series"][0]["values"][-1] == 3


@pytest.mark.parametrize("params", [
    {}, {"range": "1d"}, {"range": False}, {"range": []},
    {"range": "7d", "end_day": "2026-02-30"}, {"range": "7d", "end_day": "20261023"},
    {"range": "7d", "end_day": None}, {"range": "7d", "end_day": "0001-01-01"},
    {"range": "7d", "end_day": "9999-12-31"}, {"range": "7d", "tz": 8},
    {"range": "7d", "tz": "not/a-zone"}, {"range": "7d", "tz": None},
])
async def test_invalid_periods_are_invalid_params(conn, params):
    with pytest.raises(RpcError) as error:
        await router_for(conn).call("usage.analytics", params)
    assert error.value.code == INVALID_PARAMS


def turn(conn, member=0, model="plain/model", source="app", cost=1):
    return conn.execute(
        "INSERT INTO agent_turns (created_at_ms,session_id,question,route,prefix,text,status,"
        "member_id,model,source,cost_usd) VALUES (?,'s','private','chat','','private','ok',?,?,?,?)",
        (NOW, member, model, source, cost),
    ).lastrowid


async def test_members_turn_counts_sources_and_models_without_double_counting_cost(conn):
    conn.execute("INSERT INTO members (id,name,role,position,created_at_ms) VALUES (1,'Ada','Writer',0,?)", (NOW,))
    turn(conn, 1, "@p:model:v1", "telegram", 3)
    turn(conn, 1, "plain/model", "routine", 1)
    turn(conn, 0, None, "voice", 0)
    turn(conn, 17, "plain/model", "member", 2)
    spend(conn, NOW, 6)
    result = await router_for(conn).call("usage.analytics", {"range": "7d"})
    assert result["cost_by"]["purpose"]["total"] == 6
    assert result["messages_by"]["model"]["total"] == 4
    assert {s["key"]: s["total"] for s in result["messages_by"]["source"]["series"]} == {
        "telegram": 1, "routine": 1, "voice": 1, "member": 1,
    }
    raphael, ada, removed = result["members"]
    assert raphael["id"] == 0
    assert ada["name"] == "Ada" and ada["role"] == "Writer"
    assert ada["turns"] == 2 and ada["cost_usd"] == 4
    assert ada["share"] == pytest.approx(2 / 3)
    assert ada["models"][0] == {"model": "@p:model:v1", "turns": 1, "cost_usd": 3}
    assert removed["id"] == 17 and removed["deleted"] is True
    assert "private" not in str(result)


async def test_tools_count_real_steps_by_own_day_exclude_models_and_keep_connection_names(conn):
    tid = turn(conn)
    task = conn.execute("INSERT INTO worker_tasks (created_at_ms,state,session_id,language,text)"
                        " VALUES (?,'done','s','en','private')", (NOW,)).lastrowid
    calls = [("tool", "web_search", "Raphael"), ("tool", "web_read", "Raphael"),
             ("tool", "query", "Notion"), ("computer", "click", "Notepad"),
             ("tool", "routine_list", "Raphael"), ("tool", "ask_member", "Raphael"),
             ("tool", "memory_read", "Raphael"), ("tool", "read_attachment", "Raphael"),
             ("model", None, None), ("tool", "weather", "Raphael")]
    for i, (kind, tool, server) in enumerate(calls):
        conn.execute("INSERT INTO worker_steps (task_id,n,at_ms,kind,tool,server,outcome)"
                     " VALUES (?,?,?,?,?,?,'ok')", (task, i, NOW, kind, tool, server))
    conn.execute("INSERT INTO agent_steps (turn_id,n,at_ms,kind,tool,server,outcome)"
                 " VALUES (?,0,?,'tool','query','Notion','error')", (tid, NOW - 86400000))
    conn.execute("INSERT INTO agent_steps (turn_id,n,at_ms,kind,tool,server,outcome)"
                 " VALUES (?,1,?,'tool','query','Notion','ok')", (tid, NOW + 86400000))
    result = await router_for(conn).call("usage.analytics", {"range": "7d"})
    assert result["tools"]["total"] == 10
    assert len(result["tools"]["series"]) == 6
    notion = next(s for s in result["tools"]["series"] if s["key"] == "connection:Notion")
    assert notion["label"] == "Notion" and notion["values"][-2:] == [1, 1]


async def test_dictation_keeps_capture_dates_and_recovered_estimates(conn):
    conn.execute("INSERT INTO dictation_daily (day,dictations,words,estimated,estimated_words,estimated_dictations)"
                 " VALUES ('2026-10-23',3,42,1,12,1)")
    result = await router_for(conn).call("usage.analytics", {"range": "7d", "tz": "UTC"})
    assert result["dictation"]["dictations"][-1] == 3
    assert result["dictation"]["words"][-1] == 42
    assert result["dictation"]["estimated"][-1] is True
    assert result["dictation"]["estimated_words"] == 12
    assert result["dictation"]["estimated_dictations"] == 1
    assert result["dictation"]["day_basis"] == "capture_local"


async def test_provider_display_name_from_registry_without_secret_access(conn):
    import json
    conn.execute("INSERT INTO settings (key,value) VALUES ('providers.config.v1',?)", (json.dumps({
        "p": {"id": "p", "name": "My gateway", "kind": "openai", "enabled": False},
    }),))
    providers = ProviderRegistry(conn, object(), MemoryKeyStore())
    spend(conn, NOW, 0.125, "@p:llama:latest")
    result = await router_for(conn, providers=providers).call("usage.analytics", {"range": "7d"})
    assert result["cost_by"]["provider"]["series"][0]["label"] == "My gateway"


async def test_zero_cost_turns_have_finite_shares_and_empty_periods_are_zero_filled(conn):
    turn(conn, cost=0)
    router = router_for(conn)
    result = await router.call("usage.analytics", {"range": "7d"})
    assert result["members"][0]["share"] == 0
    empty = await router.call("usage.analytics", {"range": "30d", "end_day": "2026-08-01"})
    assert empty["members"] == []
    assert empty["tools"] == {"total": 0, "series": []}
    assert empty["dictation"]["words"] == [0] * 30


async def test_requested_timezone_controls_today_and_the_default_end_day(conn):
    # It is midnight October 23 at UTC-12 at this injected clock.
    result = await router_for(conn).call("usage.analytics", {"range": "7d", "tz": "Etc/GMT+12"})
    assert result["end_day"] == "2026-10-23"
    # UTC+14 is already October 24, unlike the injected Taipei default.
    east = await router_for(conn).call("usage.analytics", {"range": "7d", "tz": "Pacific/Kiritimati"})
    assert east["today_day"] == "2026-10-24"
    assert east["end_day"] == "2026-10-24"


async def test_every_tool_category_is_attributed_even_when_not_in_top_five(conn):
    tid = turn(conn)
    groups = {"web_search": "web_search", "web_read": "web_read", "routine_create": "routines",
              "ask_member": "ask_member", "history_search": "memory", "read_attachment": "files",
              "weather": "other", "computer": "computer"}
    for tool, expected in groups.items():
        conn.execute("DELETE FROM agent_steps")
        conn.execute("INSERT INTO agent_steps (turn_id,n,at_ms,kind,tool,server,outcome)"
                     " VALUES (?,0,?,'tool',?,'Raphael','ok')", (tid, NOW, tool))
        result = await router_for(conn).call("usage.analytics", {"range": "7d"})
        assert result["tools"]["series"][0]["key"] == expected
        assert result["tools"]["total"] == 1


async def test_message_models_are_limited_to_five_with_all_remaining_turns_preserved(conn):
    for i in range(7):
        for _ in range(i + 1):
            turn(conn, model=f"model{i}", cost=0)
    result = await router_for(conn).call("usage.analytics", {"range": "7d"})
    series = result["messages_by"]["model"]["series"]
    assert result["messages_by"]["model"]["total"] == 28
    assert [s["key"] for s in series] == ["model6", "model5", "model4", "model3", "model2", "__other__"]
    assert series[-1]["total"] == 3


@pytest.mark.skipif(sys.platform != "win32", reason="Windows local-time range")
async def test_unsupported_windows_local_dates_are_invalid_params_not_internal_errors(conn):
    router = Router()
    Brain(conn, object(), MemoryKeyStore(), now_ms=lambda: NOW).register(router)
    with pytest.raises(RpcError) as error:
        await router.call("usage.analytics", {"range": "7d", "end_day": "0002-01-01"})
    assert error.value.code == INVALID_PARAMS
    # Explicit IANA data can represent this date even when the system cannot.
    result = await router.call("usage.analytics", {"range": "7d", "end_day": "0002-01-01", "tz": "UTC"})
    assert result["end_day"] == "0002-01-01"
