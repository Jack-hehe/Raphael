"""Script owner RPC actions against an isolated production App.

The App keeps its real services, stores, gates, NarratedLoop and CliLoop. Only
model transports and external connector/device I/O are replaceable. This module
does not implement member, room, routine, memory or approval business logic.
"""

import asyncio
import copy
import json
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


def claims_on(reply, app):
    """Whether `reply` still says `app` is on: a line that names it and calls it enabled or connected.
    Saying that it is gone is not stale (live: 「快照未提供 Guardian 狀態」)."""
    return any(re.search(r'已啟用|已連線|已連接|(?<!dis)connected|\benabled', line, re.I)
               for line in reply.splitlines() if app in line)


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

    async def owner_view(self, member=0):
        turns = (await self.app.call("agent.history.list", {"member_id": member}))["turns"]
        reasons = []
        for turn in turns:
            if turn.get("scope", "private") == "exchange":
                continue  # a colleague's question in this chat is that colleague's, drawn as one line
            if turn.get("member_id", 0) != member or turn.get("source") != "app":
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
        if cell.driver == 'task-stop':
            reasons,result = await self.stop_scenario()
        elif cell.driver == 'short-wrapup':
            reasons,result = await self.short_wrapup_scenario()
        elif cell.driver in ('immediate-dispatch','immediate-room-work'):
            reasons,result = await self.immediate_work_scenario(room=cell.driver=='immediate-room-work')
        elif cell.driver in ('roster', 'meeting'):
            reasons, result = await (self.roster_scenario() if cell.driver == 'roster' else self.meeting_scenario())
        elif cell.driver in ('consult', 'memory'):
            reasons, result = await (self.consult_scenario() if cell.driver == 'consult' else self.memory_scenario())
        elif cell.driver == "team-setup":
            reasons = []
            async def team_state():
                roster = await self.app.call("members.list", {})
                for member in roster["members"]:
                    for field in ("waiting", "unread", "preview", "last_at"):
                        member.pop(field, None)
                return roster

            before = await team_state()
            result = await self.ask(cell.prompt)
            if result.get("status") != "confirm" or len(result.get("confirm", {}).get("drafts", [])) != 5:
                return ["missing_editable_team_draft"], (await self.owner_view())[0]
            if await team_state() != before:
                reasons.append("early_team_mutation")
            cancelled_id = result["confirm"]["id"]
            await self.confirm(result, False)
            await self.app.call("agent.confirm", {"id": cancelled_id, "approved": True})
            if await team_state() != before:
                reasons.append("cancel_replay_mutated_team")
            result = await self.ask(cell.prompt)
            drafts = copy.deepcopy(result.get("confirm", {}).get("drafts", []))
            if len(drafts) != 5 or any(not draft.get("instructions", "").strip() for draft in drafts):
                return reasons + ["missing_member_prompts"], (await self.owner_view())[0]
            if {draft["name"] for draft in drafts} != {"行銷", "信件助理", "HiKari", "PM", "RD"}:
                reasons.append("incomplete_team")
            if await team_state() != before:
                reasons.append("early_team_mutation")
            drafts[0]["instructions"] = "Owner-edited instructions " + self.nonce
            approved_id = result["confirm"]["id"]
            self.approvals.append({"id_digest": digest(approved_id), "approved": True, "edited": True})
            result = await self.app.call("agent.confirm", {"id": approved_id, "approved": True, "drafts": drafts})
            after = await team_state()
            created = after["members"][1:]
            if len(created) != 5 or not any(item["instructions"] == drafts[0]["instructions"] for item in created):
                reasons.append("edited_prompt_not_persisted")
            await self.app.call("agent.confirm", {"id": approved_id, "approved": True, "drafts": drafts})
            if await team_state() != after:
                reasons.append("apply_replay_mutated_team")
            if self.recorder.receipts:
                reasons.append("unexpected_external_action")
        elif cell.driver in ("missing-connection", "missing-permission"):
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
        if cell.driver in ('immediate-room-work','short-wrapup','task-stop'):
            # This owner entered through rooms.send; do not invent a private conversation. The room's
            # turns are what the owner saw, each with the model that answered it.
            turns = [{'source':'room','scope':'room',**dict(row)} for row in self.app._conn.execute(
                "SELECT id,member_id,status,text,model FROM agent_turns WHERE scope='room' ORDER BY id")]
            owner_reasons = check_reply(result.get('text',''),Scenario('room-work',''))
        else:
            # A member's own conversation is the owner's view when the owner spoke to that member.
            turns, owner_reasons = await self.owner_view(result.get('viewer', 0))
        reasons += owner_reasons
        if not turns or result.get("status") not in {"done", "denied", "error"}:
            reasons.append("no_terminal_owner_result")
        return reasons, turns

    async def seed_team(self):
        return [(await self.app.call('members.create', {'name':name, 'role':role}))['member']['id']
                for name, role in [('行銷','行銷企劃'), ('信件助理','信件管理'), ('HiKari','社群內容'), ('PM','專案規劃'), ('RD','來源研究')]]

    async def immediate_work_scenario(self,*,room):
        """Scoped P03 acceptance; full Gmail/meeting acceptance remains in the parent S03/S08 cells."""
        ids = await self.seed_team()
        await self.install_fixture()
        target = ids[1]
        prompt = (f'請用 handoff 立即交給信件助理（member_id={target}），stage_id=mail-triage，'
            # 「本次代碼」 alone let members report the mail's receipt hash instead of its nonce live.
            f'工作是用 Guardian 讀取測試信件並回報信中的 nonce。來源文件寫「明早截止、明天中午提交」，仍現在做；'
            f'done_when=讀取工具成功並回報信中的 nonce。不要建立例行任務。')
        room_id = None
        if room:
            room_id = (await self.app.call('rooms.create',{'name':'立即工作驗收','member_ids':[0,target]}))['room']['id']
            await self.app.call('rooms.send',{'room_id':room_id,'request_id':'work-'+self.nonce,
                'text':'@拉斐爾 '+prompt,'ui_language':'zh-TW'})
            result = {'status':'done','text':''}
        else:
            result = await self.ask(prompt)
        # The suite's outer cell timeout bounds this wait. Observe persisted business state.
        while True:
            tasks = self.app._conn.execute("SELECT * FROM worker_tasks WHERE mode IS NOT NULL").fetchall()
            settled = not room or self.app.rooms._exchanges.latest(room_id)['state'] in ('done','stopped')
            if tasks and all(task['state'] not in ('queued','running','waiting') for task in tasks) and settled:
                break
            # Work done without a hand-off fails below as a missing receipt, not as a timeout.
            if not tasks and settled:
                break
            await asyncio.sleep(.02)
        reasons=[]
        if len(tasks)!=1 or tasks[0]['member_id']!=target:
            reasons.append('dispatch_receipt_or_owner')
        for task in tasks:
            if task['state']!='done' or task['ready_at_ms']!=task['created_at_ms'] or task['dispatch_policy']!='immediate' or task['started_at_ms'] is None:
                reasons.append('immediate_start')
            if task['scope']!=('room' if room else 'exchange') or task['model_ref']!=self.model:
                reasons.append('work_scope_or_model')
            if task['room_id']!=room_id or self.nonce not in (task['report_text'] or '')+(task['report_detail'] or ''):
                reasons.append('result_destination_or_nonce')
            if not self.app._conn.execute("SELECT 1 FROM task_outbox WHERE task_id=? AND type='result' AND disposition='delivered'",(task['id'],)).fetchone():
                reasons.append('result_not_delivered')
            if room and not self.app._conn.execute('SELECT 1 FROM room_messages WHERE room_id=? AND thread_id=? AND turn_id=?',
                (room_id,task['thread_id'],task['turn_id'])).fetchone():
                reasons.append('result_not_in_room_thread')
        if not self.recorder.receipts or self.app._conn.execute('SELECT COUNT(*) FROM routines').fetchone()[0]:
            reasons.append('no_action_or_implicit_schedule')
        if room:
            messages=(await self.app.call('rooms.messages',{'room_id':room_id}))['messages']
            if not messages or messages[-1]['member_id']!=0 or messages[-1]['kind']!='wrapup':
                reasons.append('chief_not_last')
            if any(m['kind']=='progress' for m in self.app._conn.execute("SELECT kind FROM agent_messages WHERE turn_id IN (SELECT turn_id FROM room_messages WHERE room_id=?)",(room_id,))):
                reasons.append('progress_in_messages')
            result['text']=' '.join(m['text'] for m in (await self.app.call('rooms.messages',{'room_id':room_id}))['messages'] if m['member_id'] is not None)
        return reasons,result

    async def short_wrapup_scenario(self):
        ids = await self.seed_team()
        room = (await self.app.call('rooms.create',{'name':'短討論','member_ids':[0,ids[0]],
            'default_responder':f'member:{ids[0]}'}))['room']
        await self.app.call('rooms.send',{'room_id':room['id'],'request_id':self.nonce,
            'text':'@行銷 請簡短回報：目前沒有交付成果，不要承諾未執行的工作。'})
        while self.app.rooms._exchanges.latest(room['id'])['state'] in ('queued','running'):
            await asyncio.sleep(.02)
        messages=(await self.app.call('rooms.messages',{'room_id':room['id']}))['messages']
        wrapups=[m for m in messages if m['kind']=='wrapup']
        reasons=[] if len(wrapups)==1 and messages[-1]['member_id']==0 and messages[-1]['status']=='done' else ['short_wrapup_missing']
        return reasons,{'status':'done','text':' '.join(m['text'] for m in messages if m['member_id'] is not None)}

    async def stop_scenario(self):
        """Hold only external I/O; production loops and tree cancellation stay authoritative."""
        ids=await self.seed_team()
        await self.install_fixture()
        room=(await self.app.call('rooms.create',{'name':'停止驗收','member_ids':[0,ids[1]]}))['room']
        entered,release=asyncio.Event(),asyncio.Event()
        armed=False
        original=self.recorder.call_tool
        async def delayed(*args,**kwargs):
            if armed:
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    # Model a provider that returns after cancellation.
                    await release.wait()
            return await original(*args,**kwargs)
        self.recorder.call_tool=delayed
        # One action completes first, so the stop has finished work to keep. One call per request is what
        # models follow: live, 「連續呼叫兩次」 was done once and the cell waited out its timeout.
        await self.app.call('rooms.send',{'room_id':room['id'],'request_id':self.nonce+'-first',
            'text':'@信件助理 現在呼叫 Guardian read 一次，再回報結果。'})
        while (self.app.rooms._exchanges.latest(room['id'])['state'] not in ('done','stopped') or self.app._conn.execute(
                "SELECT 1 FROM worker_tasks WHERE room_id=? AND state IN ('queued','running','waiting')",(room['id'],)).fetchone()):
            await asyncio.sleep(.02)
        if not self.recorder.receipts:
            return ['no_completed_action'],{'status':'done','text':''}
        armed=True
        await self.app.call('rooms.send',{'room_id':room['id'],'request_id':self.nonce,
            'text':'@信件助理 現在再呼叫 Guardian read 一次，再回報結果。'})
        while not entered.is_set():
            if self.app.rooms._exchanges.latest(room['id'])['state'] in ('done','stopped'):
                return ['action_not_in_flight'],{'status':'done','text':''}
            await asyncio.sleep(.02)
        before=len(self.recorder.receipts)
        old=self.app._conn.execute("SELECT id,generation FROM worker_tasks WHERE room_id=? AND state='running'",(room['id'],)).fetchone()
        started=time.perf_counter()
        await self.app.call('rooms.send',{'room_id':room['id'],'request_id':self.nonce+'-redirect',
            'text':'@信件助理 改為停止前只回報目前狀態。'})
        redirect_ms=round((time.perf_counter()-started)*1000)
        await self.app.call('rooms.stop',{'room_id':room['id']})
        ack_ms=round((time.perf_counter()-started)*1000)
        release.set()
        while self.app._conn.execute("SELECT 1 FROM worker_tasks WHERE room_id=? AND state IN ('queued','running','waiting')",(room['id'],)).fetchone():
            await asyncio.sleep(.02)
        reasons=[]
        current=self.app.background._store.get(old['id']) if old else None
        if not current or current.state!='cancelled' or current.generation<=old['generation']:
            reasons.append('generation_priority')
        if len(self.recorder.receipts)<before:
            reasons.append('completed_actions_lost')
        if self.app._conn.execute("SELECT 1 FROM worker_tasks WHERE room_id=? AND state='done' AND generation>0",(room['id'],)).fetchone():
            reasons.append('late_completion')
        await self.app.rooms.wait_idle()
        messages=(await self.app.call('rooms.messages',{'room_id':room['id']}))['messages']
        if not messages or messages[-1]['member_id']!=0 or messages[-1]['kind']!='wrapup':
            reasons.append('chief_not_last_after_stop')
        self.events.append(('guardian.stop_timings',{'redirect_ms':redirect_ms,'stop_ack_ms':ack_ms,
            'terminal_ms':round((time.perf_counter()-started)*1000)}))
        return reasons,{'status':'done','text':'已停止工作，已完成的動作保留。'}

    async def roster_scenario(self):
        ids = await self.seed_team()
        await self.install_fixture()
        reasons, result = [], {}
        for refresh in (False, True):
            if refresh:
                await self.app.call('members.update', {'id':ids[-1], 'name':'研究員' + self.nonce})
                self.app.integrations._config.remove('guardian-mail')
            for member in [0,*ids]:
                authority = self.app.members.team_context(member, 'zh-TW')
                result = await self.ask('請根據 TeamContextSnapshot 列出全員名字與原文角色、團隊總人數、主要窗口、幕僚長及應用程式連線狀態；不要輸出 JSON。', member=member)
                turns = (await self.app.call('agent.history.list', {'member_id':member}))['turns']
                latest = max(turns, key=lambda turn: turn['id']) if turns else {}
                reply = '\n'.join(m['text'] for m in latest.get('messages', []) if m['kind']=='reply') or result.get('text','')
                if not all(person['name'] in reply and person['role'] in reply for person in authority['members']):
                    reasons.append('roster_member_or_role_missing')
                if str(authority['count']) not in reply or not all(app['name'] in reply for app in authority['apps']):
                    reasons.append('roster_count_or_apps_missing')
                if not all(word in reply for word in ('主要窗口', '幕僚長')):
                    reasons.append('roster_primary_or_chief_missing')
                if refresh and claims_on(reply, 'Guardian'):
                    reasons.append('roster_disconnected_app_stale')
        return reasons, result

    async def settled(self):
        # Consultations, their answers and an asker's return are read back once nothing is under way.
        while self.app._conn.execute("SELECT 1 FROM worker_tasks WHERE state IN ('queued','running','waiting')").fetchone():
            await asyncio.sleep(.02)

    async def said_to(self, member):
        """What the owner reads in their own conversation with `member`: its answers, never a
        colleague's question drawn there."""
        turns = (await self.app.call('agent.history.list', {'member_id': member}))['turns']
        return '\n'.join(part for turn in turns if turn['scope'] == 'private' for part in
                         [turn['text'], *(m['text'] for m in turn.get('messages', []) if m['kind'] == 'reply')])

    def consulted(self, asked, asker, words):
        """The colleague records of `asker` asking `asked`, and those whose answer holds `words`."""
        rows = self.app._conn.execute("SELECT id, text, detail FROM agent_turns WHERE scope='exchange' AND member_id=? AND peer_id=?",
                                      (asked, asker)).fetchall()
        return rows, [row for row in rows if words in (row['text'] or '') + (row['detail'] or '')]

    async def consult_scenario(self):
        """S04: what the owner told 行銷 alone reaches 信件助理 only as 行銷's answer to its question."""
        ids = await self.seed_team()
        marketing, mail = ids[0], ids[1]
        code, other = 'NX-' + self.nonce, 'TR-' + self.nonce[::-1]
        await self.ask(f'下週上市的新品代號是 {code}。另外，我週五要看的房子編號是 {other}。', member=marketing)
        await self.settled()
        result = await self.ask('請向行銷詢問下週上市的新品代號，問到後告訴我。', member=mail)
        await self.settled()
        reasons = list((await self.owner_view(marketing))[1])
        asked, answered = self.consulted(marketing, mail, code)
        if not answered:
            reasons.append('consult_answer_missing')
        if any(other in (row['text'] or '') + (row['detail'] or '') for row in asked):
            reasons.append('consult_copied_private_history')
        if any(code in note.description + note.body for note in [*self.app.memory.shared.all(), *self.app.memory.store_for(mail).all()]):
            reasons.append('consult_before_recall')
        relayed = await self.said_to(mail)
        if code not in relayed:
            reasons.append('private_nonce_not_relayed')
        if other in relayed:
            reasons.append('private_history_leaked')
        turns = (await self.app.call('agent.history.list', {'member_id': mail}))['turns']
        events = [event for turn in turns for event in turn.get('handoffs', [])
                  if event['mode'] == 'consult' and event['member_id'] == marketing]
        if not events:
            reasons.append('source_event_missing')
        elif not any(event['state'] == 'done' and event['work_turn_id'] in {row['id'] for row in answered} for event in events):
            reasons.append('task_link_missing')
        theirs = (await self.app.call('agent.history.list', {'member_id': marketing}))['turns']
        if not any(turn['scope'] == 'exchange' and turn['peer_id'] == mail for turn in theirs):
            reasons.append('message_from_missing')
        return reasons, {**result, 'viewer': mail}

    async def memory_scenario(self):
        """S12: a team-wide fact told to 拉斐爾 reaches 行銷 with its provenance; a fact told to 行銷
        alone stays out of PM's reach until PM asks 行銷."""
        ids = await self.seed_team()
        marketing, pm = ids[0], ids[3]
        brand, secret = 'BC-' + self.nonce, 'PV-' + self.nonce[::-1]
        told = await self.ask(f'請記住，這是全團隊都要知道的共同資訊：我們的品牌主色是金色，色號 {brand}。')
        await self.settled()
        reasons = []
        shared = [note for note in self.app.memory.shared.all() if brand in note.description + note.body]
        if not shared:
            reasons.append('global_fact_not_shared')
        elif not any(note.member == 0 and note.origin == 'said' and told.get('turn_id') in note.turns
                     and 'member 0' in self.app.memory.index(marketing).shared for note in shared):
            reasons.append('provenance')
        listed = (await self.app.call('memory.list', {'space': 'shared', 'member_id': marketing}))['memories']
        if shared and not any(item['name'] == shared[0].name and item['member'] == 0 for item in listed):
            reasons.append('provenance_not_listed')
        await self.ask('我們的品牌主色和色號是什麼？', member=marketing)
        await self.settled()
        if brand not in await self.said_to(marketing):
            reasons.append('global_recall')
        await self.ask(f'跟你說一件我自己的事：我週五要看的房子編號是 {secret}。', member=marketing)
        await self.settled()
        await self.ask('我週五要看的房子編號是多少？只根據你自己知道的回答，這次不要問任何人。', member=pm)
        await self.settled()
        if secret in await self.said_to(pm) or self.consulted(marketing, pm, '')[0]:
            reasons.append('private_isolation')
        if any(secret in note.description + note.body for note in [*self.app.memory.shared.all(), *self.app.memory.store_for(pm).all()]):
            reasons.append('private_fact_shared')
        result = await self.ask('請向行銷詢問我週五要看的房子編號，問到後告訴我。', member=pm)
        await self.settled()
        if not self.consulted(marketing, pm, secret)[1] or secret not in await self.said_to(pm):
            reasons.append('authorized_consult')
        for member in (0, marketing):
            reasons += (await self.owner_view(member))[1]
        return reasons, {**result, 'viewer': pm}

    async def meeting_scenario(self):
        ids = await self.seed_team()
        selected = [0, ids[0], *ids[2:]]
        # An owner's request with a real topic: a code-only agenda left the members nothing to discuss.
        prompt = ('請開會討論「新款保溫瓶上架後兩週的行銷計畫」（議題代碼 ' + self.nonce + '）。'
                  '預算新台幣 5 萬元，目標是兩週內 300 筆預購。邀行銷、HiKari、PM、RD，不需要信件助理。'
                  '請 PM 主持兩輪：行銷是正方，主張先投放廣告；RD 是反方，主張先做小規模測試；'
                  'HiKari 從社群內容補充。每位回應前一位的論點，並 @ 你要交棒的同事。'
                  '最後由拉斐爾整理決策和每項工作的負責人。')
        result = await self.ask(prompt)
        reasons = []
        rooms = (await self.app.call('rooms.list'))['rooms']
        room = next((r for r in rooms if set(r['member_ids']) == set(selected)), None)
        if room is None:
            return ['meeting_room_not_created'], result
        # Completion is read back from production orchestration, never inferred from chief prose.
        while True:
            history = await self.app.call('rooms.messages', {'room_id':room['id']})
            exchange = history['exchange']
            if exchange and exchange['state'] in ('done','stopped'):
                break
            await asyncio.sleep(.02)
        messages = history['messages']
        if exchange['state'] != 'done' or any(i['state'] != 'done' for i in exchange['reply_intents']):
            reasons.append('meeting_pending_or_blocked_intents')
        if exchange['hops'] < 4 or exchange['turns_taken'] < 7:
            reasons.append('meeting_did_not_cross_admission_limits')
        authors = {m['member_id'] for m in messages if m['member_id'] is not None}
        if authors != set(selected) or len([m for m in messages if m['kind']=='wrapup' and m['member_id']==0]) != 1:
            reasons.append('meeting_authors_or_chief_wrapup')
        if any(m['member_id'] is not None and m['reply_to'] is not None and m['member_id']==m['reply_to_member'] for m in messages):
            reasons.append('meeting_self_reply')
        if not any(role=='moderator' for role in room.get('roles',{}).values()):
            reasons.append('meeting_moderator_missing')
        for member in selected[1:]:
            if len([m for m in messages if m['member_id']==member and m['kind']=='message']) < 2:
                reasons.append('meeting_member_missing_second_round')
        events = [json.loads(row[0]) for row in self.app._conn.execute(
            "SELECT m.text FROM agent_messages m JOIN agent_turns t ON t.id=m.turn_id "
            "WHERE m.kind='event' AND t.member_id=0 AND t.scope='private'") if row[0].startswith('{')]
        if len([event for event in events if event.get('type')=='meeting' and event.get('room_id')==room['id']]) != 1:
            reasons.append('meeting_private_link_not_once')
        count = self.app._conn.execute('SELECT COUNT(*) FROM rooms').fetchone()[0]
        again = await self.app.rooms.meeting({'member_ids':selected, 'agenda':'Reuse ' + self.nonce}, 'zh-TW', 2**50)
        if again['room_id'] != room['id'] or self.app._conn.execute('SELECT COUNT(*) FROM rooms').fetchone()[0] != count:
            reasons.append('meeting_did_not_reuse_exact_room')
        # Inspect accepted reuse without executing a second model discussion in this cell.
        await self.app.rooms._stop({'room_id':room['id']})
        private = self.app._conn.execute("SELECT COUNT(*) FROM agent_turns WHERE route='room' AND scope='private'").fetchone()[0]
        if private:
            reasons.append('meeting_private_history_pollution')
        return reasons, result

    async def aclose(self):
        await self.app.aclose()
