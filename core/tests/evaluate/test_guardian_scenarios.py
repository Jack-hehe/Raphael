"""Offline transport fakes; production RPC, stores, loops, gates and MCP remain real."""

import json
import asyncio
from dataclasses import replace

import httpx
import pytest

from raphael_core.agent.cli.drivers import Event
from raphael_core.agent.plugins import tool_key
from raphael_core.brain.settings import SettingsStore
from raphael_core.evaluate.guardian import Budget
from raphael_core.evaluate.guardian_driver import RpcDriver
from raphael_core.evaluate.guardian_registry import coverage_guard, external_identity, inventory, load_manifest
from raphael_core.evaluate.guardian_scenarios import SCENARIOS, cells
from raphael_core.evaluate.guardian_suite import aggregate, exit_code, read_pair, run, run_cell
from raphael_core.integrations.config import ServerConfig, ToolRecord

from test_guardian import FakeBrains, FakeClient as GuardianFakeClient


class FakeClient(GuardianFakeClient):
    async def decide(self, **kwargs):
        from raphael_core.brain.openrouter import Decision
        return Decision(answers={key: {"type": "noul", "noul": 0.0} for key in kwargs["questions"]},
                        cost_usd=0, latency_ms=0)


def selected(identifier):
    return next(cell for cell in cells() if cell.id == identifier)


@pytest.mark.parametrize('identifier',['S03.immediate-start','S08.immediate-start','S08.completion'])
@pytest.mark.parametrize('brain',['api','cli'])
async def test_p03_immediate_start_uses_production_rpc_worker_and_mcp(tmp_path,monkeypatch,identifier,brain):
    from raphael_core.agent.cli import loop
    from raphael_core.background.host import WorkerHost
    from background.conftest import Links
    model='cli:codex:owner-model?effort=high' if brain=='cli' else 'fake/model'
    class Client(FakeClient):
        async def chat_tools(self,**kwargs):
            messages=kwargs['messages']
            child='Acceptance criteria' in messages[0]['content']
            if child:
                calls=[] if any(m['role']=='tool' for m in messages) else [('Guardian__read',{})]
            else:
                calls=[] if any(m['role']=='tool' for m in messages) or 'handoff_results' in str(messages) or 'Close this exchange' in messages[0]['content'] or 'Close this exchange' in str(messages) else [('handoff',arguments())]
            self.turns=iter([('已讀取信件。'+driver.nonce if not calls else '',calls)])
            return await super().chat_tools(**kwargs)
    def arguments():
        return {'member_id':2,'request':'Use Guardian to read the test mail now. 明早截止、明天中午提交。',
            'stage_id':'mail-triage','done_when':'Read the test mail and report its nonce.'}
    class Driver:
        def __init__(self,*args,**kwargs):
            pass
        async def run(self,*,endpoint,token,system,user,**kwargs):
            yield Event('model','owner-model')
            child='Acceptance criteria' in system
            if child or ('Delegated work results' not in user and 'Close this exchange' not in user):
                async with httpx.AsyncClient() as http:
                    response=await http.post(endpoint,headers={'Authorization':'Bearer '+token},json={
                        'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
                            'name':'Guardian__read' if child else 'handoff','arguments':{} if child else arguments()}})
                    assert not response.json()['result']['isError'],response.text
            yield Event('final','已讀取信件。'+driver.nonce)
        async def interrupt(self):
            pass
    monkeypatch.setattr(loop,'Driver',Driver)
    driver=RpcDriver(tmp_path,model,Client([]),Budget(40),brains=FakeBrains(tmp_path) if brain=='cli' else None)
    driver.app.background._host=WorkerHost(connect=Links(),grace_s=.3)
    try:
        reasons,turns=await asyncio.wait_for(driver.execute(selected(identifier)),10)
        assert not reasons,reasons
        # Live, a room cell's owner view held no model, so the Codex brain could never pass it.
        assert {turn.get('model') for turn in turns}=={model},turns
        assert len(driver.recorder.receipts)==1
        assert driver.app._conn.execute("SELECT COUNT(*) FROM worker_tasks WHERE mode='work'").fetchone()[0]==1
        if brain=='cli':
            # Live, a room-only cell verified no CLI model: its turns all ran as background tasks.
            assert set(driver.actual_cli_models)=={'owner-model'},driver.actual_cli_models
    finally:
        await driver.aclose()


async def test_room_work_done_without_a_handoff_fails_at_once(tmp_path):
    # Live, Raphael read the mail himself and the cell waited out its whole timeout before failing.
    from raphael_core.background.host import WorkerHost
    from background.conftest import Links
    class Client(FakeClient):
        async def chat_tools(self,**kwargs):
            messages=kwargs['messages']
            calls=[] if any(m['role']=='tool' for m in messages) or 'Close this exchange' in str(messages) else [('Guardian__read',{})]
            self.turns=iter([('已讀取信件。' if not calls else '',calls)])
            return await super().chat_tools(**kwargs)
    driver=RpcDriver(tmp_path,'fake/model',Client([]),Budget(40))
    driver.app.background._host=WorkerHost(connect=Links(),grace_s=.3)
    try:
        reasons,_=await asyncio.wait_for(driver.execute(selected('S08.immediate-start')),10)
        assert 'dispatch_receipt_or_owner' in reasons
    finally:
        await driver.aclose()


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_s01_edits_cancels_and_applies_once_on_both_production_brains(tmp_path, monkeypatch, brain):
    calls = [("group_create", {"name": "驗證團隊"}), *[("member_create", {
        "name": name, "role": name, "instructions": "Review evidence for " + name, "group": "驗證團隊"
    }) for name in ("行銷", "信件助理", "HiKari", "PM", "RD")]]
    client = FakeClient([("", calls), ("等待確認。", []), ("", calls), ("等待確認。", [])])
    if brain == "C":
        from raphael_core.agent.cli.drivers import Driver

        async def run(driver, **kwargs):
            async with httpx.AsyncClient(headers={"Authorization": "Bearer " + kwargs["token"]}, trust_env=False) as http:
                for index, (name, arguments) in enumerate(calls):
                    response = await http.post(kwargs["endpoint"], json={"jsonrpc": "2.0", "id": index,
                        "method": "tools/call", "params": {"name": name, "arguments": arguments}})
                    assert not response.json()["result"]["isError"]
            yield Event("model", "test-model")
            yield Event("final", "等待確認。")
        monkeypatch.setattr(Driver, "run", run)
    registry = await inventory()
    result = await run_cell(selected("S01"), brain, "cli:codex:test-model" if brain == "C" else "fake/model", tmp_path,
                            client=client, brains=FakeBrains(tmp_path), budget=Budget(30), seconds=20,
                            run_id="offline-s01", head="test-head", registry=registry)
    assert result["status"] == "PASS", result
    assert result["assertions"] == list(selected("S01").assertions)


def test_registry_contains_all_22_owner_contracts_and_operation_cells():
    assert [cell.id for cell in SCENARIOS] == [f"S{i:02}" for i in range(1, 23)]
    expanded = cells(catalog=[{"provider": "fixture", "id": "page-one", "auth_modes": ["oauth", "api-key"]},
                              {"provider": "fixture", "id": "page-two", "auth_modes": ["none"]}])
    identifiers = {cell.id for cell in expanded}
    assert len(identifiers) == len(expanded)
    assert {"S18.fixture.page-one.oauth", "S18.fixture.page-one.api-key", "S18.fixture.page-two.none",
            "S19.stdio.device.edited-draft", "S19.http.none.test-failure", "S20.revoke-in-flight",
            "S21.routines.run-now", "S21.skills.save-trigger", "S21.files.attachment-inventory"} <= identifiers


def test_pair_preserves_requested_codex_effort_without_adding_models(tmp_path):
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"codex": "cli:codex:owner-model?effort=high", "api": "provider/pinned"}))
    assert read_pair(config) == {"C": "cli:codex:owner-model?effort=high", "A": "provider/pinned"}
    config.write_text(json.dumps({"models": ["provider/pinned"]}))
    assert read_pair(config)["C"] is None
    config.write_text(json.dumps({"codex": "cli:claude:other", "api": "provider/pinned"}))
    with pytest.raises(ValueError):
        read_pair(config)


@pytest.mark.parametrize("statuses,status,code", [(["PASS", "PASS"], "PASS", 0),
                                                  (["PASS", "SKIP"], "SKIP", 2),
                                                  (["SKIP", "SKIP"], "SKIP", 2),
                                                  (["FAIL", "SKIP"], "FAIL", 1), ([], "FAIL", 1)])
def test_skip_never_rolls_up_to_pass(statuses, status, code):
    results = [{"status": value} for value in statuses]
    assert aggregate(results) == status and exit_code(results) == code


def test_empty_inventory_cannot_pass_coverage():
    assert coverage_guard([], {})["status"] == "FAIL"


@pytest.mark.parametrize("authorized", [False, True])
def test_real_smoke_refuses_without_owner_authorization_or_an_implemented_adapter(monkeypatch, capsys, authorized):
    from raphael_core.evaluate.guardian import main
    args = ["guardian", "--real-smoke", "fixture-mail"]
    if authorized:
        args.append("--owner-approved-real-smoke")
    monkeypatch.setattr("sys.argv", args)
    assert main() == 1
    assert ("adapter not implemented" if authorized else "requires --owner-approved-real-smoke") in capsys.readouterr().out


async def test_inventory_unions_sources_and_keeps_disabled_lazy_same_named_servers():
    servers = [ServerConfig(f"server-{i}", "Same display name", "http", enabled=i != 1,
                            tools=[ToolRecord(f"tool-{n}", "fixture", {"type": "object"}, str(n)) for n in range(45)])
               for i in range(2)]
    registered = await inventory(servers)
    assert len([tool for tool in registered if tool.origin == "plugin"]) == 90
    assert {external_identity("mcp", "server-0", "tool-44"), external_identity("mcp", "server-1", "tool-44")} <= {tool.tool_key for tool in registered}
    assert all(tool.api_names and tool.mcp_names for tool in registered if tool.origin == "plugin")
    assert next(tool for tool in registered if tool.tool_key == external_identity("mcp", "server-1", "tool-44")).enabled_state == "disabled"
    assert all(tool.schema_hash and tool.roles for tool in registered)
    empty = ServerConfig("empty-server", "Empty connector", "http", enabled=False)
    identifiers = {cell.id for cell in cells(registered, servers=[*servers, empty])}
    for server in ("server-0", "server-1", "empty-server"):
        assert {f"S20.connector/mcp/{server}.{operation}" for operation in
                ("connector-off", "connector-on", "login", "disconnect")} <= identifiers


async def test_every_registered_builtin_has_a_named_manifest_mapping():
    registered, mapping = await inventory(), load_manifest()
    report = coverage_guard(registered, mapping, scenario_ids={cell.id for cell in cells(registered)})
    assert not [error for error in report["errors"] if error["reason"] in {"missing_mapping", "unknown_scenario_cell", "mapping_without_registered_tool"}]
    assert report["status"] == "FAIL"  # Mapping alone is never acceptance.
    assert any(error["reason"] == "missing_surface" for error in report["errors"])


async def coverage_fixture():
    tool = next(tool for tool in await inventory() if tool.operation == "routine_create")
    mapping = {tool.tool_key: {"scenario_ids": ["S21.routines.create"], "expected_assertions": ["production_state"]}}
    calls = [{"tool_key": tool.tool_key, "schema_hash": tool.schema_hash, "brain": brain,
              "scenario_id": "S21.routines.create", "source": "production_loop", "gate_passed": True,
              "outcome": "ok", "cell_status": "PASS", "assertions": ["production_state"],
              "run_id": "run-current", "head": "current", "requested_model": "cli:codex:model" if brain == "C" else "provider/model"}
             for brain in ("C", "A")]
    return tool, mapping, calls


@pytest.mark.parametrize("fault", ["unmapped", "one-brain", "discovery", "denied", "old-schema", "old-head", "old-run", "driver-call", "no-assertion", "missing-surface", "new-page", "same-name-server", "wrong-model"])
async def test_coverage_guard_rejects_incomplete_or_stale_evidence(fault):
    tool, mapping, calls = await coverage_fixture()
    registry = [tool]
    kwargs = {"run_id": "run-current", "head": "current", "models": {"C": "cli:codex:model", "A": "provider/model"}}
    assert coverage_guard(registry, mapping, calls, **kwargs)["status"] == "PASS"
    if fault == "unmapped":
        mapping = {}
    elif fault == "one-brain":
        calls.pop()
    elif fault == "discovery":
        calls[0]["outcome"] = "listed"
    elif fault == "denied":
        calls[0]["gate_passed"] = False
    elif fault == "old-schema":
        calls[0]["schema_hash"] = "old"
    elif fault == "old-head":
        calls[0]["head"] = "old"
    elif fault == "old-run":
        calls[0]["run_id"] = "old"
    elif fault == "driver-call":
        calls[0]["source"] = "test_driver"
    elif fault == "no-assertion":
        calls[0]["assertions"] = []
    elif fault == "wrong-model":
        calls[0]["requested_model"] = "cli:codex:other"
    elif fault == "missing-surface":
        registry = [replace(tool, mcp_names=[])]
    else:
        registry.append(replace(tool, tool_key="mcp:new-server/same-name"))
    assert coverage_guard(registry, mapping, calls, **kwargs)["status"] == "FAIL"


@pytest.mark.parametrize("operation", ["create", "list", "edit", "pause", "resume", "delete"])
async def test_rpc_driver_uses_real_routine_mutation_and_readback(tmp_path, operation):
    args = {"title": "placeholder", "prompt": "練習寫作", "schedule": {"kind": "daily", "time": "08:00"}}
    tool = "routine_" + {"edit": "update", "pause": "update", "resume": "update"}.get(operation, operation)
    client = FakeClient([])
    driver = RpcDriver(tmp_path, "fake/model", client, Budget(20))
    args["title"] = "驗證寫作" + driver.nonce
    if operation != "create":
        args = {} if operation == "list" else {"id": 1}
    if operation == "edit":
        args["schedule"] = {"kind": "daily", "time": "09:00"}
    if operation in ("pause", "resume"):
        args["enabled"] = operation == "resume"
    client.turns = iter([("", [(tool, args)]), ("已完成。" if operation != "list" else "驗證寫作" + driver.nonce, [])])
    try:
        reasons, turns = await driver.execute(selected("S21.routines." + operation))
        assert not reasons, (reasons, turns)
        assert driver.traces[-1]["name"] == tool
        assert driver.traces[-1]["source"] == "production_loop"
        assert driver.recorder.receipts == []
    finally:
        await driver.aclose()


@pytest.mark.parametrize("kind", ["missing-connection", "missing-permission"])
async def test_failure_honesty_checks_observed_failure_and_owner_reply(tmp_path, kind):
    client = FakeClient([("", [("Guardian__read", {})]), ("需要重新登入並授予讀取權限。", [])])
    driver = RpcDriver(tmp_path, "fake/model", client, Budget(20))
    try:
        reasons, _ = await driver.execute(selected("S17." + kind))
        assert not reasons
        assert driver.recorder.receipts == []
        assert driver.traces[0]["outcome"] == ("auth" if kind == "missing-connection" else "denied")
    finally:
        await driver.aclose()


@pytest.mark.parametrize("approved", [True, False])
async def test_recording_executor_is_after_real_approval_gate(tmp_path, approved):
    client = FakeClient([("", [("Guardian__write", {})]), ("已完成。", [])])
    driver = RpcDriver(tmp_path, "fake/model", client, Budget(20))
    try:
        await driver.install_fixture(write=True)
        SettingsStore(driver.app._conn).set("action_mode", "ask")
        waiting = await driver.ask("請寄送測試信件，先讓我確認。")
        assert waiting["status"] == "confirm" and driver.recorder.receipts == []
        await driver.confirm(waiting, approved)
        assert len(driver.recorder.receipts) == int(approved)
        audit = (await driver.app.call("integrations.audit"))["entries"]
        assert any(entry["outcome"] == ("ok" if approved else "denied") for entry in audit)
        if approved:
            assert any(entry["approver"] == "user" for entry in audit)
        assert "test_mode" in json.dumps(driver.recorder.receipts) if approved else True
    finally:
        await driver.aclose()


async def test_expired_owner_approval_has_zero_recording_receipts(tmp_path):
    client = FakeClient([("", [("Guardian__write", {})])])
    driver = RpcDriver(tmp_path, "fake/model", client, Budget(20))
    clock = [0.0]
    driver.app.agent._confirmations._clock = lambda: clock[0]
    try:
        await driver.install_fixture(write=True)
        SettingsStore(driver.app._conn).set("action_mode", "ask")
        waiting = await driver.ask("請寄送測試信件，先讓我確認。")
        assert waiting["status"] == "confirm"
        clock[0] = 100.0
        await driver.confirm(waiting, True)
        assert driver.recorder.receipts == []
    finally:
        await driver.aclose()


@pytest.mark.parametrize("text,calls", [("需要重新登入並授予讀取權限。", []), ("已完成。", [("Guardian__read", {})])])
async def test_failure_honesty_needs_a_real_failed_call_and_no_false_completion(tmp_path, text, calls):
    client = FakeClient([("", calls), (text, [])] if calls else [(text, [])])
    driver = RpcDriver(tmp_path, "fake/model", client, Budget(20))
    try:
        reasons, _ = await driver.execute(selected("S17.missing-connection"))
        assert "missing_observed_failure:auth" in reasons if not calls else "false_completion_or_promise" in reasons
    finally:
        await driver.aclose()


async def test_report_keeps_verified_schema_and_redacts_provider_errors(tmp_path):
    registry = await inventory()
    result = await run_cell(selected("S17.missing-connection"), "A", "fake/model", tmp_path / "good",
                            client=FakeClient([("", [("Guardian__read", {})]), ("需要重新登入並授予讀取權限。", [])]),
                            brains=None, budget=Budget(20), seconds=3, run_id="run", head="head", registry=registry)
    assert result["status"] == "PASS", result
    assert result["actual_model"] == "fake/model" and result["evidence_paths"]

    class BrokenClient:
        async def chat_tools(self, **kwargs):
            raise RuntimeError("synthetic-private-secret-never-report")

    failed = await run_cell(selected("S17.missing-connection"), "A", "fake/model", tmp_path / "bad",
                            client=BrokenClient(), brains=None, budget=Budget(20), seconds=3,
                            run_id="run", head="head", registry=registry)
    assert failed["status"] == "FAIL" and "actual_model_unverified_or_changed" in failed["reason"]
    assert "synthetic-private-secret" not in json.dumps(failed)


async def test_mid_turn_budget_exhaustion_is_skip_and_retains_trace(tmp_path):
    result = await run_cell(selected("S17.missing-connection"), "A", "fake/model", tmp_path,
                            client=FakeClient([("", [("Guardian__read", {})])]), brains=None,
                            budget=Budget(1), seconds=3, run_id="run", head="head", registry=await inventory())
    assert result["status"] == "SKIP" and result["reason"] == "total_turn_cap"
    assert result["tools"][0]["outcome"] == "auth" and not result["tools"][0]["assertions"]


async def test_changed_actual_api_model_is_retained_and_fails(tmp_path):
    class ChangedClient(FakeClient):
        async def chat_tools(self, **kwargs):
            turn = await super().chat_tools(**kwargs)
            turn.model = "provider/changed-model"
            return turn
    result = await run_cell(selected("S17.missing-connection"), "A", "fake/model", tmp_path,
                            client=ChangedClient([("已完成。", [])]), brains=None, budget=Budget(20),
                            seconds=3, run_id="run", head="head", registry=await inventory())
    assert result["status"] == "FAIL" and result["actual_model"] == "provider/changed-model"


async def test_unknown_api_cost_is_null_instead_of_fake_zero(tmp_path):
    class UnknownCostClient(FakeClient):
        async def chat_tools(self, **kwargs):
            turn = await super().chat_tools(**kwargs)
            turn.usage_known = False
            return turn
    result = await run_cell(selected("S17.missing-connection"), "A", "fake/model", tmp_path,
                            client=UnknownCostClient([("", [("Guardian__read", {})]), ("需要重新登入並授予讀取權限。", [])]),
                            brains=None, budget=Budget(20), seconds=3, run_id="run", head="head", registry=await inventory())
    assert result["status"] == "PASS"
    assert result["cost"]["amount"] is None and result["cost"]["source"] == "unavailable"


async def test_a_room_cell_reports_its_result_through_the_suite(tmp_path, monkeypatch):
    # Room cells return a room view, not owner turns; capturing it once ended the whole live suite.
    from raphael_core.background.host import WorkerHost
    from raphael_core.evaluate import guardian_suite
    from background.conftest import Links

    class LinkedDriver(RpcDriver):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.app.background._host = WorkerHost(connect=Links(), grace_s=.1)

    class Client(FakeClient):
        async def chat_tools(self, **kwargs):
            self.turns = iter([("已核對目前沒有交付成果。", [])])
            return await super().chat_tools(**kwargs)

    monkeypatch.setattr(guardian_suite, "RpcDriver", LinkedDriver)
    result = await run_cell(selected("S07.short-wrapup"), "A", "fake/model", tmp_path, client=Client([]),
                            brains=None, budget=Budget(40), seconds=15, run_id="run", head="head",
                            registry=await inventory())
    assert result["status"] == "PASS", result["reason"]
    assert json.loads((tmp_path / "owner-view.json").read_text(encoding="utf-8"))["turns"][0]["status"] == "done"


async def test_missing_budget_and_brain_skip_but_unbuilt_contract_fails(tmp_path):
    registry = await inventory()
    for model, budget, reason in [(None, Budget(2), "configured brain unavailable"), ("fake/model", Budget(0), "total_turn_cap")]:
        result = await run_cell(selected("S17.missing-connection"), "A", model, tmp_path,
                                client=FakeClient([]), brains=FakeBrains(tmp_path), budget=budget,
                                seconds=1, run_id="run", head="head", registry=registry)
        assert result["status"] == "SKIP" and result["reason"] == reason
    unbuilt = next(cell for cell in SCENARIOS if not cell.driver)
    result = await run_cell(unbuilt, "A", None, tmp_path, client=None,
                            brains=None, budget=Budget(0), seconds=1, run_id="run", head="head", registry=registry)
    assert result["status"] == "FAIL" and result["reason"].startswith("not implemented")


async def test_suite_always_emits_44_basic_cells_and_tool_brain_matrix(tmp_path):
    code = await run({"C": None, "A": None}, tmp_path, client=FakeClient([]), brains=FakeBrains(tmp_path, installed=False),
                     max_turns=1, head="test-head", include_legacy=False)
    report = json.loads(next(tmp_path.glob("run-*/report.json")).read_text(encoding="utf-8"))
    assert code == 1
    basic = [row for row in report["results"] if "." not in row["scenario_id"]]
    assert len(basic) == 44
    assert report["coverage"]["status"] == "FAIL" and report["coverage"]["missing"]
    assert report["catalog"]["status"] == "unavailable"
    assert len({row["tool_key"] for row in report["coverage"]["results"]}) == len(report["registry"])


async def test_the_suite_runs_every_cell_that_has_a_driver(tmp_path):
    # A base scenario with child cells is only aggregated, so a driver on it never ran (S07's meeting did not).
    await run({"C": None, "A": None}, tmp_path, client=FakeClient([]), brains=FakeBrains(tmp_path, installed=False),
              max_turns=1, head="test-head", include_legacy=False)
    report = json.loads(next(tmp_path.glob("run-*/report.json")).read_text(encoding="utf-8"))
    attempted = {row["scenario_id"] for row in report["results"] if row["reason"] == "configured brain unavailable"}
    assert {cell.id for cell in cells() if cell.driver} <= attempted


async def test_a_scenario_with_an_unbuilt_half_never_passes(tmp_path, monkeypatch):
    # S14 is Gmail (P08a) and Instagram publishing (P08d): a passing Gmail half alone is not S14.
    from raphael_core.evaluate import guardian_suite

    async def gmail_passes(cell, brain, model, directory, **kwargs):
        result = guardian_suite.blank(cell, brain, model, "run", "head")
        result.update(status="PASS" if cell.id == "S14.gmail" else "FAIL",
                      reason="" if cell.id == "S14.gmail" else "not implemented: " + cell.dependency)
        return result

    monkeypatch.setattr(guardian_suite, "run_cell", gmail_passes)
    await run({"C": None, "A": None}, tmp_path, client=FakeClient([]), brains=FakeBrains(tmp_path, installed=False),
              max_turns=1, head="test-head", include_legacy=False)
    rows = json.loads(next(tmp_path.glob("run-*/report.json")).read_text(encoding="utf-8"))["results"]
    assert {(row["brain"], row["status"]) for row in rows if row["scenario_id"] == "S14.gmail"} == {("C", "PASS"), ("A", "PASS")}
    assert {(row["status"], row["reason"]) for row in rows if row["scenario_id"] == "S14"} == {
        ("FAIL", "required child cells incomplete")}


async def test_rpc_cli_brain_reaches_the_production_authenticated_mcp(tmp_path, monkeypatch):
    from raphael_core.agent.cli import loop

    class Driver:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, *, endpoint, token, **kwargs):
            yield Event("model", "owner-model")
            async with httpx.AsyncClient() as http:
                response = await http.post(endpoint, headers={"Authorization": "Bearer " + token},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "routine_create", "arguments": {
                              "title": title, "prompt": "練習寫作", "schedule": {"kind": "daily", "time": "08:00"}}}})
                assert not response.json()["result"]["isError"]
            yield Event("final", "已建立例行任務。")

        async def interrupt(self):
            pass

    monkeypatch.setattr(loop, "Driver", Driver)
    driver = RpcDriver(tmp_path, "cli:codex:owner-model?effort=high", FakeClient([]), Budget(20), brains=FakeBrains(tmp_path))
    title = "驗證寫作" + driver.nonce
    try:
        reasons, _ = await driver.execute(selected("S21.routines.create"))
        assert not reasons
        assert driver.traces[0]["name"] == "routine_create"
        assert driver.recorder.receipts == []
        assert driver.actual_cli_models == ["owner-model"]
    finally:
        await driver.aclose()


@pytest.mark.parametrize('calls,reason',[(0,'no_completed_action'),(1,'action_not_in_flight')])
async def test_a_stop_cell_without_its_held_call_fails_at_once(tmp_path,calls,reason):
    # Live, a member read the mail once where the cell waited for a second read, until its timeout.
    from raphael_core.background.host import WorkerHost
    from background.conftest import Links
    made=[]
    class Client(FakeClient):
        async def chat_tools(self,**kwargs):
            asked=not any(m['role']=='tool' for m in kwargs['messages']) and 'Close this exchange' not in str(kwargs['messages'])
            call=asked and len(made)<calls
            made.extend([1] if call else [])
            self.turns=iter([('' if call else '好了。',[('Guardian__read',{})] if call else [])])
            return await super().chat_tools(**kwargs)
    driver=RpcDriver(tmp_path,'fake/model',Client([]),Budget(40))
    driver.app.background._host=WorkerHost(connect=Links(),grace_s=.1)
    try:
        reasons,_=await asyncio.wait_for(driver.execute(selected('S11')),15)
        assert reason in reasons
    finally:
        await driver.aclose()


@pytest.mark.parametrize('brain',['api','cli'])
@pytest.mark.parametrize('identifier',['S07.short-wrapup','S11'])
async def test_p04_short_wrapup_and_stop_through_production_paths(tmp_path,monkeypatch,brain,identifier):
    from raphael_core.agent.cli import loop
    from raphael_core.background.host import WorkerHost
    from background.conftest import Links
    model='cli:codex:owner-model?effort=high' if brain=='cli' else 'fake/model'
    stopping=identifier=="S11"
    class Client(FakeClient):
        async def chat_tools(self,**kwargs):
            tools=sum(message['role']=='tool' for message in kwargs['messages'])
            calls=[('Guardian__read',{})] if stopping and tools<2 else []
            self.turns=iter([('已核對目前沒有交付成果。' if not calls else '',calls)])
            return await super().chat_tools(**kwargs)
    class Driver:
        def __init__(self,*args,**kwargs):
            pass
        async def run(self,*,endpoint,token,**kwargs):
            if stopping:
                async with httpx.AsyncClient() as http:
                    for index in range(2):
                        await http.post(endpoint,headers={'Authorization':'Bearer '+token},json={
                            'jsonrpc':'2.0','id':index,'method':'tools/call',
                            'params':{'name':'Guardian__read','arguments':{}}})
            yield Event('final','已核對目前沒有交付成果。')
        async def interrupt(self):
            pass
    monkeypatch.setattr(loop,'Driver',Driver)
    driver=RpcDriver(tmp_path,model,Client([]),Budget(40),brains=FakeBrains(tmp_path) if brain=='cli' else None)
    driver.app.background._host=WorkerHost(connect=Links(),grace_s=.1)
    try:
        reasons,_=await asyncio.wait_for(driver.execute(selected(identifier)),15)
        assert not reasons,reasons
        if stopping:
            assert any(event[0]=='guardian.stop_timings' for event in driver.events)
    finally:
        await driver.aclose()
