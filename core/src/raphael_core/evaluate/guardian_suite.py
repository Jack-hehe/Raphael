"""P00/P22 orchestration. All required cells survive skips and partial runs."""

import asyncio
import json
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..agent.cli.discovery import CliBrains, brain_effort, brain_ref
from ..brain.openrouter import OpenRouterClient
from ..paths import db_path
from ..worker.loop import Stop
from .credentials import key_provider
from .guardian import Budget, HARD_TURNS, ROOT
from .guardian_driver import RpcDriver
from .guardian_registry import BRAINS, coverage_guard, digest, inventory, load_manifest
from .guardian_scenarios import SCENARIOS, cells


def read_pair(config=None):
    """Keep the owner's exact Codex model/effort and one explicit OpenRouter ID."""
    value = json.loads(Path(config).read_text(encoding="utf-8")) if config else {}
    models = value.get("models", [])
    if not isinstance(models, list) or any(not isinstance(model, str) for model in models):
        raise ValueError("invalid_models_config")
    codex = value.get("codex") or next((model for model in models if model.startswith("cli:codex:")), None)
    api = value.get("api") or next((model for model in models if not model.startswith("cli:")), None)
    if not config and db_path().is_file():
        with sqlite3.connect(db_path().as_uri() + "?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='agent_model'").fetchone()
            model = json.loads(row[0]) if row else None
            if isinstance(model, str) and model.startswith("cli:codex:"):
                codex = model
            elif isinstance(model, str) and not model.startswith("cli:"):
                api = model
    if codex is not None and (not isinstance(codex, str) or not codex.startswith("cli:codex:") or not brain_ref(codex)):
        raise ValueError("invalid_codex_model")
    if api is not None and (not isinstance(api, str) or "/" not in api or api.startswith("cli:") or len(api) > 220):
        raise ValueError("invalid_api_model")
    return {"C": codex, "A": api}


def aggregate(items):
    if not items or any(item["status"] == "FAIL" for item in items):
        return "FAIL"
    return "PASS" if all(item["status"] == "PASS" for item in items) else "SKIP"


def exit_code(results):
    status = aggregate(results)
    return 0 if status == "PASS" else 2 if status == "SKIP" else 1


def revision():
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True, check=True, timeout=10)
    return result.stdout.strip()


def blank(cell, brain, model, run_id, head):
    return {"scenario_id": cell.id, "brain": brain, "requested_model": model,
            "actual_model": None, "driver": "codex" if brain == "C" else "openrouter",
            "effort": brain_effort(model) if model and brain == "C" else None,
            "status": "FAIL", "reason": "", "run_id": run_id, "head": head,
            "tools": [], "approvals": [], "assertions": [], "conversation_scope": [], "task_ids": [],
            "timings": {"first_response_ms": None, "receipt_ms": None, "ready_to_start_ms": None,
                        "completion_ms": None, "redirect_ms": None, "stop_ms": None},
            "cost": {"amount": None, "currency": "USD", "source": "subscription" if brain == "C" else "unavailable"},
            "evidence_paths": []}


def capture(driver, turns, result, directory, registry):
    by_name = {name: item for item in registry for name in [item.tool_key, *item.execution_keys, *item.api_names, *item.mcp_names]}
    for trace in driver.traces:
        registered = by_name.get(trace["name"])
        role = "chief" if trace["actor"] == 0 else "member"
        schema_matches = registered and digest(registered.schemas.get(role)) == trace["input_schema_hash"]
        result["tools"].append({**trace, "tool_key": registered.tool_key if registered else trace["name"],
                                "schema_hash": registered.schema_hash if schema_matches else None,
                                "brain": result["brain"], "scenario_id": result["scenario_id"],
                                "requested_model": result["requested_model"], "run_id": result["run_id"],
                                "head": result["head"], "cell_status": result["status"],
                                "assertions": result["assertions"]})
    evidence = Path(directory) / "owner-view.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"turns": [{"id": turn.get("id"), "conversation_id": turn.get("conversation_id"),
                         "member_id": turn.get("member_id"), "status": turn.get("status"),
                         "text_digest": digest(turn.get("text", "")),
                         "message_kinds": [message.get("kind") for message in turn.get("messages", [])]}
                        for turn in turns], "receipts": driver.recorder.receipts,
                        "device_receipts": driver.actions.calls}, indent=2) + "\n", encoding="utf-8")
    result["evidence_paths"] = [str(evidence)]


async def run_cell(cell, brain, model, directory, *, client, brains, budget, seconds, run_id, head, registry, servers=()):
    result = blank(cell, brain, model, run_id, head)
    if not cell.driver:
        result["reason"] = "not implemented: " + cell.dependency
        return result
    if model is None:
        result.update(status="SKIP", reason="configured brain unavailable")
        return result
    if brain == "C":
        try:
            ready = brains.command("codex") and brains.logged_in("codex") is True
        except OSError:
            ready = False
        if not ready:
            result.update(status="SKIP", reason="Codex CLI missing or login unverified")
            return result
    if client is None and brain == "A":
        result.update(status="SKIP", reason="OpenRouter key unavailable")
        return result
    if budget.used >= budget.limit:
        result.update(status="SKIP", reason="total_turn_cap")
        return result
    began, driver, turns = time.perf_counter(), None, []
    try:
        driver = RpcDriver(directory, model, client, budget, brains=brains if brain == "C" else None, servers=servers)
        async with asyncio.timeout(seconds):
            reasons, turns = await driver.execute(cell)
        actual = {turn.get("model") for turn in turns if turn.get("model")}
        if brain == "A":
            actual = set(driver.transport.models)
        if not actual or actual != {model}:
            reasons.append("actual_model_unverified_or_changed")
        if brain == "C" and set(driver.actual_cli_models) != {brain_ref(model)[1]}:
            reasons.append("cli_actual_model_unverified_or_changed")
        result.update(status="FAIL" if reasons else "PASS", reason="; ".join(dict.fromkeys(reasons)),
                      actual_model=(driver.actual_cli_models[-1] if driver.actual_cli_models else None) if brain == "C"
                      else next(iter(actual)) if len(actual) == 1 else None,
                      assertions=list(cell.assertions) if not reasons else [],
                      conversation_scope=sorted({turn.get("conversation_id") for turn in turns if turn.get("conversation_id") is not None}),
                      approvals=driver.approvals)
    except Stop as error:
        result.update(status="SKIP" if error.kind == "total_turn_cap" else "FAIL", reason=error.kind)
    except TimeoutError:
        result["reason"] = "scenario_timeout"
    except Exception as error:
        result["reason"] = type(error).__name__
    finally:
        if driver is not None:
            if budget.capped:
                result.update(status="SKIP", reason="total_turn_cap", assertions=[])
            try:
                capture(driver, turns, result, directory, registry)
            except Exception as error:  # evidence for one cell must not end the whole suite
                result.update(status="FAIL", reason="capture:" + type(error).__name__, assertions=[])
            result["approvals"] = driver.approvals
            result["timings"]["first_response_ms"] = driver.transport.first_response_ms
            if brain == "A" and driver.transport.models:
                result["cost"].update(amount=driver.transport.cost if driver.transport.cost_known else None,
                                       source="reported_or_estimated" if driver.transport.cost_known else "unavailable")
            result["usage"] = driver.transport.tokens
            try:
                await driver.aclose()
            except Exception as error:
                result.update(status="FAIL", reason="cleanup:" + type(error).__name__)
        result["timings"]["completion_ms"] = round((time.perf_counter() - began) * 1000)
    return result


async def run(pair, output, *, max_turns=200, seconds=90, client=None, brains=None,
              api_only=False, registry=None, mapping=None, catalog=(), head=None, include_legacy=True, servers=()):
    if set(pair) != set(BRAINS) or not 1 <= max_turns <= HARD_TURNS or not 1 <= seconds <= 900:
        raise ValueError("invalid_suite_config")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    run_id, head = directory.name, head or revision()
    registry = await inventory(servers) if registry is None else registry
    mapping = load_manifest() if mapping is None else mapping
    expanded = cells(registry, catalog, servers=servers)
    base_ids = {cell.id for cell in SCENARIOS}
    expanded_parents = {cell.parent for cell in expanded if cell.id != cell.parent}
    budget = Budget(max_turns)
    brains, own_brains = brains or CliBrains(), brains is None
    own_client = client is None
    key = key_provider() if own_client else "fake"
    client = client or (OpenRouterClient(lambda: key) if key else None)
    results = []
    preflight = coverage_guard(registry, mapping, scenario_ids={cell.id for cell in expanded})
    report = {"version": 2, "run_id": run_id, "head": head, "models": pair,
              "execution_mode": "injected_offline_test" if not own_client or not own_brains else "live",
              "started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "registry": [item.report(mapping.get(item.tool_key)) for item in registry],
              "catalog": {"status": "available" if catalog else "unavailable", "apps": list(catalog),
                          "reason": "" if catalog else "complete catalog snapshot not implemented (P20a)"},
              "preflight": preflight["errors"], "results": results, "max_turns": max_turns}
    path = directory / "report.json"

    def save():
        report["turns"] = budget.used
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        for cell in expanded:
            if (cell.id in base_ids and cell.id in expanded_parents) or cell.parent == "S22":
                continue
            for brain in BRAINS:
                model = pair[brain]
                if api_only and brain == "C" and cell.driver:
                    result = blank(cell, brain, model, run_id, head)
                    result.update(status="SKIP", reason="CLI excluded by API-only mode")
                else:
                    result = await run_cell(cell, brain, model, directory / (cell.id + "-" + brain),
                                            client=client, brains=brains, budget=budget, seconds=seconds,
                                            run_id=run_id, head=head, registry=registry, servers=servers)
                results.append(result)
                print(f"{result['status']:4} {cell.id:44} {brain} {result['reason']}", flush=True)
                save()
        calls = [tool for result in results for tool in result["tools"]]
        coverage = coverage_guard(registry, mapping, calls, scenario_ids={cell.id for cell in expanded},
                                  run_id=run_id, head=head, models=pair)
        report["coverage"] = coverage
        for tool in registry:
            cell = next(cell for cell in expanded if cell.id == "S22." + tool.tool_key)
            for brain in BRAINS:
                rows = [row for row in coverage["results"] if row["tool_key"] == tool.tool_key and row["brain"] == brain]
                result = blank(cell, brain, pair[brain], run_id, head)
                result.update(status=aggregate(rows), reason="; ".join(sorted({row["reason"] for row in rows if row["reason"]})))
                results.append(result)
        for cell in SCENARIOS:
            if any(item["scenario_id"] == cell.id for item in results):
                continue
            for brain in BRAINS:
                children = [result for result in results if result["scenario_id"].startswith(cell.id + ".") and result["brain"] == brain]
                result = blank(cell, brain, pair[brain], run_id, head)
                result.update(status=aggregate(children) if children else "FAIL",
                              reason="required child cells incomplete" if children and aggregate(children) != "PASS"
                              else "not implemented: " + cell.dependency if not children else "")
                if cell.id == "S22" and coverage["status"] != "PASS":
                    result.update(status="FAIL", reason="tool coverage incomplete")
                results.append(result)
                print(f"{result['status']:4} {cell.id:44} {brain} {result['reason']}", flush=True)
        # Retain the original Guardian matrix and its report, using only the
        # remaining global turn allowance. Its simulated outcomes do not count
        # toward scenario coverage.
        report["legacy"] = {"status": "SKIP", "reason": "total_turn_cap" if budget.used >= budget.limit else "disabled"}
        if include_legacy and budget.used < budget.limit:
            from .guardian import run as legacy_run
            legacy_dir = directory / "legacy"
            models = [model for model in pair.values() if model]
            if models:
                legacy_exit = await legacy_run(models, legacy_dir, max_turns=budget.limit - budget.used,
                                               seconds=seconds, brains=brains, client=client, api_only=api_only)
                legacy_path = next(legacy_dir.glob("run-*/report.json"))
                legacy_report = json.loads(legacy_path.read_text(encoding="utf-8"))
                budget.used += legacy_report["turns"]
                report["legacy"] = {"status": "PASS" if legacy_exit == 0 else "FAIL" if legacy_exit == 1 else "SKIP",
                                    "report_path": str(legacy_path)}
    finally:
        if own_client and client is not None:
            await client.aclose()
        if own_brains:
            await brains.aclose()
    report.update(status=aggregate([*results, {"status": report["legacy"]["status"]}] if include_legacy else results),
                  counts=dict(Counter(item["status"] for item in results)),
                  scenarios=[{"scenario_id": cell.id, "status": aggregate([item for item in results if item["scenario_id"] == cell.id])}
                             for cell in SCENARIOS],
                  finished_at=datetime.now(timezone.utc).isoformat())
    save()
    print("Matrix: " + report["status"] + " " + json.dumps(report["counts"]))
    print("Report: " + str(path))
    return exit_code([{"status": report["status"]}])
