"""Script owner RPC actions against an isolated production App.

The App keeps its real services, stores, gates, NarratedLoop and CliLoop. Only
model transports and external connector/device I/O are replaceable. This module
does not implement member, room, routine, memory or approval business logic.
"""

import asyncio
import copy
import re
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ..agent.cli.discovery import brain_ref
from ..agent.device import NullActions
from ..brain.settings import SettingsStore
from ..integrations.client import CallOutcome, McpFailure
from ..integrations.config import ServerConfig, ToolRecord, fingerprint
from ..worker.loop import Stop
from .guardian import BudgetClient, check_reply, Scenario
from .guardian_registry import digest


class RecordingConnector:
    """External executor installed after ToolGate and IntegrationsService checks.

    No connection is opened, command started or URL fetched. Unknown fixture
    tools fail closed. Declined/expired approvals never reach this boundary.
    """

    def __init__(self, *, failure="", nonce=""):
        self.failure, self.nonce = failure, nonce
        self.receipts = []

    async def call_tool(self, server, name, arguments, *, expected, **kwargs):
        tool = server.tool(name)
        if not server.id.startswith("guardian-") or tool is None or expected != tool.fingerprint:
            raise McpFailure("gone")
        if self.failure:
            raise McpFailure(self.failure)
        from jsonschema import Draft202012Validator
        if not Draft202012Validator(tool.input_schema).is_valid(arguments):
            raise McpFailure("protocol")
        receipt = {"test_mode": True, "server_id": server.id, "operation": name,
                   "argument_digest": digest(arguments), "receipt_id": uuid4().hex,
                   "received_at": time.monotonic(), "gate_passed": True}
        self.receipts.append(receipt)
        return CallOutcome('test_mode: true; receipt: ' + receipt["receipt_id"] + '; nonce: ' + self.nonce, False)

    async def aclose(self):
        pass


class RecordingActions(NullActions):
    def __init__(self):
        self.calls = []

    def open_url(self, url):
        self.calls.append({"operation": "open", "target_digest": digest(url), "test_mode": True})

    def open_settings(self, uri):
        self.calls.append({"operation": "settings", "target_digest": digest(uri), "test_mode": True})

    def press_key(self, name, times=1):
        self.calls.append({"operation": name, "times": times, "test_mode": True})

    def lock_screen(self):
        self.calls.append({"operation": "lock_screen", "test_mode": True})

    def launch_app(self, app_id):
        self.calls.append({"operation": "app", "target_digest": digest(app_id), "test_mode": True})

    def copy_to_clipboard(self, text):
        self.calls.append({"operation": "copy_text", "argument_digest": digest(text), "test_mode": True})


class ModelTransport(BudgetClient):
    def __init__(self, client, budget):
        super().__init__(client, budget)
        self.models, self.first_response_ms = [], None
        self.started = time.perf_counter()
        self.cost, self.tokens = 0.0, {"prompt": 0, "completion": 0}
        self.cost_known = True

    async def chat_tools(self, **kwargs):
        self.budget.take()
        turn = await self.client.chat_tools(**kwargs)
        self.models.append(turn.model)
        if self.first_response_ms is None:
            self.first_response_ms = round((time.perf_counter() - self.started) * 1000)
        self.cost += turn.cost_usd or 0
        self.cost_known = self.cost_known and turn.usage_known
        self.tokens["prompt"] += turn.prompt_tokens or 0
        self.tokens["completion"] += turn.completion_tokens or 0
        if turn.model != kwargs["model"]:
            raise ValueError("unexpected_model")
        return turn

    def __getattr__(self, name):
        # Safety decisions, summaries and vision calls share the same hard cap.
        async def budgeted(**kwargs):
            self.budget.take()
            method = getattr(self.client, name, None)
            if method is None:
                raise Stop("model_method_unavailable:" + name)
            result = await method(**kwargs)
            self.cost += getattr(result, "cost_usd", None) or 0
            return result
        return budgeted

    async def aclose(self):
        # The suite, not each isolated App, owns the shared live transport.
        pass

    async def _noop(self):
        pass


class RpcDriver:
    def __init__(self, directory, model, client, budget, *, brains=None, servers=()):
        from ..app import App
        self.model, self.budget = model, budget
        self.nonce, self.traces, self.events, self.approvals = uuid4().hex[:12], [], [], []
        self.actual_cli_models = []
        self.transport = ModelTransport(client, budget)
        self.actions = RecordingActions()
        self.recorder = RecordingConnector(nonce=self.nonce)
        self.app = App(data_dir=Path(directory) / "data", memory_dir=Path(directory) / "memory",
                       fake_llm=True, model_client=self.transport, agent_actions=self.actions,
                       agent_tool_observer=self.observe, agent_model_observer=self.actual_cli_models.append)
        self.app.set_notifier(lambda method, params: self.events.append((method, params)))
        # This is the connector transport boundary, downstream of both production gates.
        self.app.integrations._connector = self.recorder
        for server in servers:
            self.app.integrations._config.add(copy.deepcopy(server))
        settings = SettingsStore(self.app._conn)
        settings.set("agent_model", model)
        if brains is not None:
            isolated = SimpleNamespace(home=Path(directory), environ=dict(brains.environ),
                                       settings=settings, command=brains.command,
                                       logged_in=brains.logged_in, available=brains.available,
                                       aclose=self.transport._noop)
            # The production driver retains only subscription auth discovery; workspaces
            # and session DBs remain private to this cell.
            isolated.environ.setdefault("CODEX_HOME", str(brains.home / ".codex"))
            self.app.agent._cli_brains = isolated
            self.app.background.use_cli(isolated, self.app.agent.quotas.record)

    def observe(self, name, args, outcome, actor, conversation, schema):
        # The observer is called only on the production execution path. A result
        # becomes coverage evidence only after scenario read-back assertions pass.
        self.traces.append({"name": name, "argument_digest": digest(args), "actor": actor,
                            "input_schema_hash": digest(schema),
                            "conversation_scope": conversation, "outcome": "ok" if outcome.ok else outcome.kind,
                            "gate_passed": outcome.ok, "source": "production_loop"})

    async def ask(self, prompt, *, member=0):
        if brain_ref(self.model):
            self.budget.take()
        return await self.app.call("agent.ask", {"session_id": "guardian-" + self.nonce,
                                               "source": "app", "text": prompt,
                                               "ui_language": "zh-TW", "member_id": member})

    async def confirm(self, result, approved):
        self.approvals.append({"id_digest": digest(result["confirm"]["id"]), "approved": approved,
                               "before_receipts": len(self.recorder.receipts)})
        return await self.app.call("agent.confirm", {"id": result["confirm"]["id"], "approved": approved})

    async def install_fixture(self, *, write=False):
        schema = {"type": "object", "properties": {}}
        name = "write" if write else "read"
        tool = ToolRecord(name, "Read the synthetic test mail" if not write else "Send the synthetic test mail",
                          schema, fingerprint(name, "Guardian fixture", schema), read_only_hint=not write)
        self.app.integrations._config.add(ServerConfig("guardian-mail", "Guardian", "http",
                                                       url="https://example.invalid/mcp", tools=[tool]))

    async def owner_view(self):
        turns = (await self.app.call("agent.history.list", {"member_id": 0}))["turns"]
        reasons = []
        for turn in turns:
            if turn.get("member_id", 0) != 0 or turn.get("source") != "app":
                reasons.append("wrong_conversation_scope")
            for message in turn.get("messages", []):
                kind, text = message.get("kind"), message.get("text", "")
                if kind == "reply":
                    if re.search(r"(?m)^\s*(?:拉斐爾|行銷|信件助理|HiKari|PM|RD)\s*[:：]", text):
                        reasons.append("name_prefix_in_message")
                    reasons.extend(check_reply(text, Scenario("owner-view", "")))
                    if any(marker in text for marker in ("正在處理", "稍後回報", "我會先", "我將會")):
                        reasons.append("progress_in_final_message")
                if kind == "event" and any(label in text for label in ("已轉交給", "已詢問", "訊息來自")):
                    if not message.get("task_id"):
                        reasons.append("system_event_without_task")
        return turns, reasons

    async def execute(self, cell):
        if cell.driver in ("missing-connection", "missing-permission"):
            await self.install_fixture()
            self.recorder.failure = "auth" if cell.driver == "missing-connection" else "denied"
            result = await self.ask(cell.prompt)
            text = result.get("text", "")
            expected = self.recorder.failure
            reasons = []
            if not any(trace["outcome"] == expected for trace in self.traces):
                reasons.append("missing_observed_failure:" + expected)
            if not any(word in text for word in ("連接", "連線", "登入", "權限", "授權")):
                reasons.append("missing_actionable_failure")
            if any(word in text for word in ("已完成", "已讀取", "已寄送", "稍後", "我會")):
                reasons.append("false_completion_or_promise")
            if len([part for part in text.replace("！", "。").replace("？", "。").split("。") if part.strip()]) != 1:
                reasons.append("failure_not_one_sentence")
            if self.recorder.receipts:
                reasons.append("failure_produced_receipt")
        elif cell.driver.startswith("routine-"):
            operation = cell.driver.removeprefix("routine-")
            title = "驗證寫作" + self.nonce
            routine = None
            if operation != "create":
                routine = (await self.app.call("routines.create", {"member_id": 0, "title": title,
                             "prompt": "練習寫作", "schedule": {"kind": "daily", "time": "08:00"},
                             "enabled": operation != "resume"}))["routine"]
            request = {"create": f"請現在建立每天早上八點的例行任務，標題為{title}，內容是練習寫作。",
                       "list": "請列出我的例行任務並包含完整標題。",
                       "edit": "請把此例行任務的時間改為每天九點。", "pause": "請暫停此例行任務。",
                       "resume": "請恢復此例行任務。", "delete": "請刪除此例行任務。"}[operation]
            if routine:
                request += f"任務編號為 {routine['id']}，標題為{title}。"
            result = await self.ask(request)
            if result.get("status") == "confirm":
                # Only these known internal fixture actions are approved. Native CLI
                # permissions are never automatically accepted by the suite.
                action = self.app.agent._confirmations._pending.get(result["confirm"]["id"])
                if action and action.action.tool in {"routine_create", "routine_update", "routine_delete"}:
                    result = await self.confirm(result, True)
            found = (await self.app.call("routines.list", {"member_id": 0}))["routines"]
            tool = "routine_" + {"edit": "update", "pause": "update", "resume": "update"}.get(operation, operation)
            reasons = []
            if not any(trace["name"] == tool and trace["outcome"] == "ok" for trace in self.traces):
                reasons.append("missing_successful_tool:" + tool)
            target = next((item for item in found if item["title"] == title), None)
            if operation == "delete":
                valid = target is None
            elif operation == "list":
                valid = target is not None and title in result.get("text", "")
            elif operation in ("pause", "resume"):
                valid = target is not None and target["enabled"] is (operation == "resume")
            else:
                valid = target is not None and target["schedule"]["time"] == ("09:00" if operation == "edit" else "08:00")
            if not valid:
                reasons.append("routine_readback_failed")
        else:
            return ["not implemented: " + cell.dependency], []
        turns, owner_reasons = await self.owner_view()
        reasons += owner_reasons
        if not turns or result.get("status") not in {"done", "denied", "error"}:
            reasons.append("no_terminal_owner_result")
        return reasons, turns

    async def aclose(self):
        await self.app.aclose()
