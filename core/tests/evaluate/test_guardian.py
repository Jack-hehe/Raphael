"""Offline fake brains exercise the production API loop and authenticated MCP loop."""

import json
from types import SimpleNamespace

import httpx
import pytest

from raphael_core.agent.cli.loop import Sessions
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn
from raphael_core.evaluate.guardian import Budget, Recorder, SCENARIOS, attachments, cell, check_reply, run
from raphael_core.worker.loop import Stop


class FakeClient:
    def __init__(self, turns):
        self.turns, self.requests = iter(turns), []

    async def chat_tools(self, **kwargs):
        self.requests.append(kwargs)
        content, calls = next(self.turns)
        return ChatTurn(content, [ChatToolCall(str(i), name, args, json.dumps(args)) for i, (name, args) in enumerate(calls)], kwargs["model"], .001, 10, 10, 1)


class FakeBrains:
    def __init__(self, home, installed=True):
        self.home, self.environ, self.installed = home, {}, installed

    def command(self, name):
        return ["never-spawn-real-CLI"] if self.installed else None

    def logged_in(self, name):
        return self.installed

    def available(self, name, *, command=None):
        return self.installed

    def with_effort(self, model, effort):
        return f"{model.partition('?effort=')[0]}?effort={effort}"

    async def aclose(self):
        pass


async def api_cell(conn, tmp_path, scenario, turns, member=0, budget=None):
    client = FakeClient(turns)
    result = await cell("fake/model", member, scenario, brains=FakeBrains(tmp_path), client=client, sessions=Sessions(conn), conn=conn, budget=budget or Budget(20))
    return result, client


async def test_api_records_two_creates_without_side_effects(conn, tmp_path):
    before = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    result, client = await api_cell(conn, tmp_path, SCENARIOS[0], [
        ('{"progress":"我會建立兩位成員。"}', [("member_create", {"name": "青禾", "role": "研究員"}), ("member_create", {"name": "明月", "role": "編輯"})]),
        ("已建立青禾與明月。", []),
    ])
    assert result["status"] == "PASS", result
    assert result["tools"] == ["member_create", "member_create"]
    assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == before
    assert all(request["fallbacks"] == [] for request in client.requests)
    assert result["cost_usd"] == .002


@pytest.mark.parametrize("approved", [True, False])
async def test_approval_pauses_and_resumes_without_deleting(conn, tmp_path, approved):
    scenario = next(s for s in SCENARIOS if s.name == ("approval-yes" if approved else "approval-no"))
    result, client = await api_cell(conn, tmp_path, scenario, [("", [("member_delete", {"id": 7})]), ("已確認。" if approved else "已拒絕，未刪除。", [])])
    assert result["status"] == "PASS", result
    assert result["approvals"] == [{"tool": "member_delete", "language": "zh-TW", "choices": ["YES", "NO"], "executed": False, "decision": "YES" if approved else "NO"}]
    assert client.requests[-1]["messages"][-1]["role"] == "tool"


async def test_missing_calls_and_empty_or_english_replies_fail(conn, tmp_path):
    result, _ = await api_cell(conn, tmp_path, SCENARIOS[1], [("Done", [])])
    assert result["status"] == "FAIL"
    assert "missing_tool:routine_create" in result["reason"]
    assert "reply_not_zh_TW" in result["reason"]
    assert "raw_json_reply" in check_reply('{"text":"完成"}', SCENARIOS[1])
    assert "reply_not_traditional_Taiwan" in check_reply("已经完成。", SCENARIOS[1])


async def test_html_reaches_member_without_tool_selection(conn, tmp_path):
    result, client = await api_cell(conn, tmp_path, SCENARIOS[3], [("標題是「守護星光」。", [])], member=1)
    assert result["status"] == "PASS", result
    assert "守護星光" in client.requests[0]["messages"][1]["content"]
    offered = {tool["function"]["name"] for tool in client.requests[0]["tools"]}
    assert "member_create" not in offered and "member_delete" not in offered


async def test_turn_cap_blocks_before_next_api_request(conn, tmp_path):
    result, client = await api_cell(conn, tmp_path, SCENARIOS[1], [("", [("routine_create", {"title": "寫作", "prompt": "練習", "schedule": {"kind": "daily", "time": "09:00"}})])], budget=Budget(1))
    assert result["status"] == "FAIL"
    assert len(client.requests) == 1
    assert result["reason"] == "total_turn_cap"


async def test_cli_fake_brain_calls_real_authenticated_mcp(conn, tmp_path, monkeypatch):
    from raphael_core.agent.cli import loop as production
    from raphael_core.agent.cli.drivers import Event

    class FakeDriver:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, *, endpoint, token, **kwargs):
            async with httpx.AsyncClient() as http:
                headers = {"Authorization": "Bearer " + token}
                listing = await http.post(endpoint, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                names = {tool["name"] for tool in listing.json()["result"]["tools"]}
                assert "routine_list" in names and "member_create" not in names
                response = await http.post(endpoint, headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "routine_list", "arguments": {}}})
                assert response.status_code == 200
                assert "寫作" in response.text
            yield Event("final", "你有一項寫作任務。")

        async def interrupt(self):
            pass

    monkeypatch.setattr(production, "Driver", FakeDriver)
    result = await cell("cli:codex:gpt-6-luna?effort=low", 1, SCENARIOS[2], brains=FakeBrains(tmp_path), client=None, sessions=Sessions(conn), conn=conn, budget=Budget(1))
    assert result["status"] == "PASS", result
    assert result["tools"] == ["routine_list"]
    assert result["cost_usd"] is None


async def test_unavailable_brains_skip_and_no_pass_is_inconclusive(tmp_path):
    result = await run(["cli:codex"], tmp_path, max_turns=1, brains=FakeBrains(tmp_path, installed=False), client=FakeClient([]))
    assert result == 2
    report = json.loads(next(tmp_path.glob("run-*/report.json")).read_text(encoding="utf-8"))
    assert all(item["status"] == "SKIP" for item in report["results"])


def test_budget_is_hard_and_report_errors_never_include_provider_text():
    budget = Budget(1)
    budget.take()
    with pytest.raises(Stop, match="total_turn_cap"):
        budget.take()


@pytest.mark.parametrize("name,tool,args,text", [
    ("routine-create", "routine_create", {"title": "寫作", "prompt": "練習", "schedule": {"kind": "daily", "time": "09:00"}}, "已建立寫作任務。"),
    ("pdf", "read_attachment", {"index": 1}, "標題是星河筆記。"),
    ("image", "read_attachment", {"index": 1}, "圖片是紅色。"),
    ("open", "open", {"target_kind": "url", "target": "https://example.com/"}, "已開啟網頁。"),
    ("search", "web_search", {"query": "臺北今日新聞"}, "臺北今日有閱讀活動。"),
])
async def test_each_recorded_capability_on_both_speakers(conn, tmp_path, name, tool, args, text):
    scenario = next(s for s in SCENARIOS if s.name == name)
    for member in (0, 1):
        result, _ = await api_cell(conn, tmp_path, scenario, [("", [(tool, args)]), (text, [])], member=member)
        assert result["status"] == "PASS", result


@pytest.mark.parametrize("args", [{}, {"title": "寫作", "prompt": "練習", "schedule": {}}, {"title": "寫作", "prompt": "練習", "schedule": {"kind": "invalid"}}])
async def test_invalid_arguments_do_not_satisfy_scenario(conn, tmp_path, args):
    result, _ = await api_cell(conn, tmp_path, SCENARIOS[1], [("", [("routine_create", args)]), ("已完成。", [])])
    assert result["status"] == "FAIL" and "invalid_tool_arguments" in result["reason"]


async def test_provider_error_is_classified_without_secret_text(conn, tmp_path):
    class BrokenClient:
        async def chat_tools(self, **kwargs):
            raise RuntimeError("synthetic-secret-never-report")
    result = await cell("fake/model", 0, SCENARIOS[1], brains=FakeBrains(tmp_path), client=BrokenClient(), sessions=Sessions(conn), conn=conn, budget=Budget(5))
    assert result["status"] == "FAIL" and result["reason"] == "RuntimeError"
    assert "synthetic-secret" not in json.dumps(result)


async def test_api_memory_uses_the_production_reply_envelope(conn, tmp_path):
    scenario = next(s for s in SCENARIOS if s.name == "memory")
    reply = {"text": "已記住你的偏好。", "memory": [{"op": "write", "name": "morning-writing", "type": "user", "description": "寫作時間偏好", "body": "喜歡在早上練習寫作。", "expires": "", "space": "shared"}]}
    result, _ = await api_cell(conn, tmp_path, scenario, [(json.dumps(reply, ensure_ascii=False), [])])
    assert result["status"] == "PASS", result
    assert "english_sentence_in_zh_TW_reply" in check_reply("已完成。Everything is ready now.", scenario)


def test_synthetic_files_have_real_magic_and_inline_text():
    from io import BytesIO
    from pypdf import PdfReader
    from raphael_core.agent.attachment_inputs import text_context
    assert "守護星光" in text_context(attachments("html"))
    assert "晨光計畫" in text_context(attachments("text"))
    assert attachments("image")[0].data.startswith(b"\x89PNG")
    assert PdfReader(BytesIO(attachments("pdf")[0].data)).metadata.title == "星河筆記"
