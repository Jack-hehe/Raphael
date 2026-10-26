"""Bounded live brain/tool acceptance through the production conversation loops.

Only the model transport is live. Tool execution records synthetic outcomes; it never
opens a URL, changes a member, saves a memory, or schedules work. Reports contain
tool names and classified failures, never provider diagnostics or credentials.
"""

import argparse
import asyncio
import json
import os
import re
import sqlite3
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from ..agent.attachment_inputs import text_context
from ..agent.cli.discovery import CliBrains, brain_ref
from ..agent.cli.loop import CliLoop, MEMORY_TOOLS, Sessions
from ..agent.ledger import Ledger
from ..agent.narration import NarratedLoop
from ..agent.persona import cli_system_prompt, member_system_prompt, system_prompt
from ..agent.policy import Action, Confirmations, NotApproved, Tier, ToolRegistry
from ..agent.speaker import Speaker
from ..agent.team_admin import DEFINITIONS as ADMIN_TOOLS, LEADERSHIP
from ..background.snapshot import take_snapshot
from ..brain import db
from ..brain.openrouter import OpenRouterClient
from ..brain.settings import DEFAULTS
from ..brain.spend import SpendTracker
from ..brain.textnorm import convert_leftover_simplified
from ..paths import db_path
from ..services.attachments import Attachment
from ..worker.loop import Stop, ToolOutcome, WAITING
from . import use_utf8_output
from .credentials import key_provider

ROOT = Path(__file__).resolve().parents[4]
HARD_TURNS = 200


@dataclass(frozen=True)
class Scenario:
    name: str
    prompt: str
    required: dict = field(default_factory=dict)
    contains: tuple = ()
    attachment: str = ""
    raphael_only: bool = False
    approval: bool | None = None


SCENARIOS = (
    Scenario("members", "建立兩位成員：研究員青禾與編輯明月。請實際建立並簡短回報。", {"member_create": 2}, ("青禾", "明月"), raphael_only=True),
    Scenario("routine-create", "建立每天上午九點提醒我練習寫作的例行任務。", {"routine_create": 1}),
    Scenario("routine-list", "列出我的例行任務。", {"routine_list": 1}, ("寫作",)),
    Scenario("html", "請讀取附件，逐字引用其標題。", contains=("守護星光",), attachment="html"),
    Scenario("text", "請讀取文字附件，逐字引用其計畫名稱。", contains=("晨光計畫",), attachment="text"),
    Scenario("image", "請讀取圖片附件並說明主要顏色。", contains=("紅",), attachment="image"),
    Scenario("pdf", "請讀取 PDF 附件並引用標題。", {"read_attachment": 1}, ("星河筆記",), attachment="pdf"),
    Scenario("memory", "請記住：我喜歡在早上練習寫作。", {"memory_write": 1}),
    Scenario("open", "請開啟 https://example.com/ 。", {"open": 1}),
    Scenario("search", "請上網搜尋臺北今日的新聞並簡短說明。", {"web_search": 1}),
    Scenario("approval-yes", "請刪除成員 7，先讓我確認。", {"member_delete": 1}, raphael_only=True, approval=True),
    Scenario("approval-no", "請刪除成員 7，先讓我確認。", {"member_delete": 1}, raphael_only=True, approval=False),
)


def attachments(kind):
    if kind == "text":
        return [Attachment("plan.txt", "text/plain", "計畫名稱：晨光計畫。".encode())]
    if kind == "html":
        return [Attachment("headline.html", "text/html", "<h1>守護星光</h1><p>測試附件。</p>".encode())]
    if kind == "image":
        # A real red PNG, generated deterministically without an image dependency.
        import struct
        import zlib
        def chunk(name, data):
            return struct.pack("!I", len(data)) + name + data + struct.pack("!I", zlib.crc32(name + data))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 32, 32, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 32) * 32)) + chunk(b"IEND", b"")
        return [Attachment("red.png", "image/png", png)]
    if kind == "pdf":
        from io import BytesIO
        from pypdf import PdfWriter
        writer, stream = PdfWriter(), BytesIO()
        writer.add_blank_page(width=200, height=200)
        writer.add_metadata({"/Title": "星河筆記"})
        writer.write(stream)
        return [Attachment("notes.pdf", "application/pdf", stream.getvalue())]
    return []


class Recorder:
    def __init__(self, tools):
        self.offered = {tool["function"]["name"]: tool["function"]["parameters"] for tool in tools}
        self.calls, self.cards = [], []
        self.errors = []
        self.confirmations = Confirmations()
        self.registry = ToolRegistry({"member_delete": Tier.T3})
        self.pending = None

    async def __call__(self, identifier, name, args):
        self.calls.append(name)
        if name not in self.offered:
            self.errors.append("unoffered_tool")
            return ToolOutcome(False, "unknown_tool")
        if not Draft202012Validator(self.offered[name]).is_valid(args):
            self.errors.append("invalid_tool_arguments:" + name)
            return ToolOutcome(False, "bad_arguments")
        if len(self.calls) > 20:
            self.errors.append("scenario_tool_limit")
            raise Stop("limit_tool")
        if name == "member_delete":
            action = Action(name, args)
            try:
                self.registry.check(action, None)
            except NotApproved:
                self.pending = self.confirmations.open("guardian", 1, action, Tier.T3, "zh-TW")
                self.cards.append({"tool": name, "language": "zh-TW", "choices": ["YES", "NO"], "executed": False})
                raise Stop(WAITING) from None
        if name == "team_overview":
            data = {"members": [{"id": 7, "name": "測試員", "role": "測試"}], "groups": [], "avatar_presets": []}
        elif name in ("member_create", "create_member"):
            data = {"created": 100 + len(self.calls), "member": {"id": 100 + len(self.calls), **args}}
        elif name == "routine_list":
            data = {"routines": [{"id": 10, "title": "寫作", "schedule": {"kind": "daily", "time": "09:00"}}]}
        elif name == "routine_create":
            data = {"routine": {"id": 10, **args}}
        elif name == "read_attachment":
            data = {"text": "星河筆記", "description": "紅色正方形", "index": args.get("index")}
        elif name == "web_search":
            data = {"text": "臺北今日舉辦閱讀活動。", "sources": [{"title": "測試新聞", "url": "https://example.com/news"}]}
        else:
            data = {"ok": True}
        return ToolOutcome(True, content=json.dumps(data, ensure_ascii=False))

    def resolve(self, approved):
        resolution = self.confirmations.resolve(self.pending.id, approved)
        if approved:
            self.registry.check(resolution.pending.action, resolution.approval)
        self.cards[-1]["decision"] = "YES" if approved else "NO"
        # Even YES only simulates success: no destructive implementation exists here.
        return ToolOutcome(approved, None if approved else "denied", "已確認。" if approved else "已拒絕，未刪除。")


# Words only the mainland writes, where Taiwan has its own: 影片, 軟體, 硬體, 螢幕, 滑鼠, 預設, 網路, 伺服器, 資料庫,
# 資料夾, 部落格, 連結; and 以後, 之後, 然後, 最後, 後來, 裡面, 那裡, 哪裡, which the character check misses
# because Traditional has 后 and 里 too.
_MAINLAND_WORDS = re.compile("視頻|軟件|硬件|屏幕|鼠標|默認|網絡|服務器|數據庫|文件夾|博客|鏈接|"
                             "以后|之后|然后|最后|后来|里面|那里|哪里")


def check_reply(text, scenario):
    reasons = []
    if not text.strip():
        reasons.append("empty_reply")
    if not re.search(r"[\u3400-\u9fff]", text):
        reasons.append("reply_not_zh_TW")
    prose = re.sub(r"https?://\S+|\b[\w.-]+\.(?:html|png|pdf|txt)\b", "", text)
    prose = re.sub(r"\b(?:Raphael|OpenRouter|OpenAI|Codex|Claude|Gemini|Antigravity|GitHub|Telegram|Google|Notion|MCP|PDF|CLI|HTML|YES|NO)\b", "", prose, flags=re.I)
    if re.search(r"\b[A-Za-z]{2,}(?:[ ,.;:]+[A-Za-z]{2,}){2}", prose):
        reasons.append("english_sentence_in_zh_TW_reply")
    # Taiwan writing is what the app itself leaves as it is (台 and 臺 alike), without the mainland's unmistakable
    # words. OpenCC alone is no test of it: it takes its input for Simplified and rewrote Taiwan's own 了解, 權限,
    # 主要窗口, 代碼, 項目, 登錄 and 對象 live, failing correct replies.
    if convert_leftover_simplified(text) != text or _MAINLAND_WORDS.search(text):
        reasons.append("reply_not_traditional_Taiwan")
    if re.search(r'\{\s*"|```\s*json|"(?:progress|thinking|tool_calls)"\s*:', text):
        reasons.append("raw_json_reply")
    for value in scenario.contains:
        if value not in text:
            reasons.append("missing_expected_text:" + value)
    return reasons


def read_models(config=None):
    if config:
        value = json.loads(Path(config).read_text(encoding="utf-8"))
    else:
        current = DEFAULTS["agent_model"]
        path = db_path()
        if path.is_file():
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
                row = conn.execute("SELECT value FROM settings WHERE key='agent_model'").fetchone()
                if row:
                    current = json.loads(row[0])
        value = {"models": [current, "google/gemini-3.1-flash-lite"]}
    models = list(dict.fromkeys(value["models"]))
    if not 1 <= len(models) <= 6 or any(not isinstance(m, str) or not m or len(m) > 220 for m in models):
        raise ValueError("invalid_models_config")
    return list(dict.fromkeys(["cli:codex:gpt-6-luna?effort=low", "cli:claude:haiku", "cli:gemini", *models]))


class Budget:
    def __init__(self, limit):
        self.limit, self.used = limit, 0
        self.capped = False

    def take(self):
        if self.used >= self.limit:
            self.capped = True
            raise Stop("total_turn_cap")
        self.used += 1


class BudgetClient:
    def __init__(self, client, budget):
        self.client, self.budget = client, budget

    async def chat_tools(self, **kwargs):
        self.budget.take()
        result = await self.client.chat_tools(**kwargs)
        if result.model != kwargs["model"]:
            raise ValueError("unexpected_model")
        return result


async def cell(model, member, scenario, *, brains, client, sessions, conn, budget, seconds=90):
    began = time.perf_counter()
    result = {"brain": model, "member": member, "scenario": scenario.name, "status": "FAIL", "tools": [], "latency_ms": 0, "cost_usd": None, "reason": ""}
    cli = brain_ref(model)
    if scenario.raphael_only and member != 0:
        result.update(status="SKIP", reason="Raphael-only scenario; member exposure checked in every cell")
        return result
    if cli:
        try:
            available = bool(brains.command(cli[0])) and brains.logged_in(cli[0]) is True
        except OSError:
            available = False
        if not available:
            result.update(status="SKIP", reason="CLI missing or login unverified")
            return result
    speaker = Speaker(id=member, name="測試員", role="助理")
    files = attachments(scenario.attachment)
    tools = take_snapshot(None, memory=True, attachments=bool(files), speaker=speaker, interactive=True, max_tier=Tier.T3).definitions
    if cli:
        tools = [*tools, *MEMORY_TOOLS]
    names = {tool["function"]["name"] for tool in tools}
    if member and names & (set(ADMIN_TOOLS) | {"create_member"}):
        result["reason"] = "member_exposed_to_administration"
        return result
    recorder = Recorder(tools)
    ledger = Ledger(SpendTracker(conn))
    user = scenario.prompt + text_context(files)
    if files:
        user += "\nFiles: " + json.dumps([{"index": n, **a.metadata()} for n, a in enumerate(files, 1)], ensure_ascii=False)
        user += "\nUse read_attachment by index for PDFs and images not included natively."
    index = "No saved memories."
    if cli:
        system = cli_system_prompt("zh-TW", name=None if member == 0 else speaker.name, role=speaker.role, memory_index=index)
    elif member == 0:
        system = system_prompt("zh-TW", "answer", index, narrated=True)
    else:
        system = member_system_prompt("zh-TW", "answer", name=speaker.name, role=speaker.role, instructions="", personality="", memory_index=index, narrated=True)
    if member == 0:
        system += LEADERSHIP
    loop = None
    try:
        common = dict(model=model, ledger=ledger, language="zh-TW", plain=member != 0, system=system, user=user, tools=tools, progress=lambda _: None, thinking=lambda _: None, execute=recorder)
        if cli:
            budget.take()
            loop = CliLoop(brains=brains, sessions=sessions, member_id=member, event=lambda _: None, attachments=files, **common)
        else:
            loop = NarratedLoop(client=BudgetClient(client, budget), fallbacks=[], **common)
        async with asyncio.timeout(seconds):
            outcome = await loop.run()
            if scenario.approval is not None:
                if outcome.kind != WAITING or not recorder.cards:
                    raise ValueError("approval_card_not_raised")
                loop.continue_with(recorder.resolve(scenario.approval))
                outcome = await loop.run()
            if outcome.status != "done":
                raise Stop(outcome.kind or "loop_did_not_finish")
            if getattr(loop, "failure", None):
                raise Stop("cli_transport_or_login_failed")
            reply = loop.reply(outcome)
            text = "\n".join(reply.messages or [reply.text, reply.detail or ""])
            reasons = check_reply(text, scenario)
            reasons.extend(recorder.errors)
            counts = Counter(recorder.calls)
            for name, count in scenario.required.items():
                # API brains express memory writes in the production reply envelope.
                actual = sum(item.op == "ADD" for item in reply.memory) if name == "memory_write" and not cli else counts[name]
                if actual < count:
                    reasons.append("missing_tool:" + name)
            if scenario.name == "members" and counts["member_create"] != 2:
                reasons.append("expected_exactly_two_creates")
            if scenario.attachment == "image" and (not cli or cli[0] == "gemini") and not counts["read_attachment"]:
                reasons.append("image_not_read")
            result.update(status="FAIL" if reasons else "PASS", reason="; ".join(reasons))
    except (TimeoutError, Stop) as error:
        result["reason"] = error.kind if isinstance(error, Stop) else "scenario_timeout"
    except Exception as error:
        # Never serialize a provider/process exception message.
        result["reason"] = str(error) if type(error) is ValueError and str(error) in {"approval_card_not_raised", "loop_did_not_finish"} else type(error).__name__
    finally:
        if isinstance(loop, CliLoop):
            await loop.aclose()
        result.update(tools=recorder.calls, approvals=recorder.cards, latency_ms=round((time.perf_counter() - began) * 1000), cost_usd=None if cli else ledger.total, cost_kind="subscription" if cli else "reported_or_estimated")
    return result


async def run(models, output, *, max_turns=200, seconds=90, brains=None, client=None, api_only=False):
    if not 1 <= max_turns <= HARD_TURNS or not 1 <= seconds <= 900:
        raise ValueError("invalid_budget")
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    budget, results = Budget(max_turns), []
    started_at = datetime.now(timezone.utc).isoformat()
    report = {"version": 1, "started_at": started_at, "turns": 0, "max_turns": max_turns, "status": "running", "results": results}
    report_path = directory / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    own_client = client is None
    key = key_provider() if own_client else "fake"
    client = client or OpenRouterClient(lambda: key)
    brains = brains or CliBrains()
    conn = db.connect(directory / "scratch.db")
    sessions = Sessions(conn)
    # Keep CLI workspaces inside the run while retaining existing subscription login discovery.
    environ = {**brains.environ, "CODEX_HOME": str(Path(brains.environ.get("CODEX_HOME", brains.home / ".codex"))), "CLAUDE_CONFIG_DIR": str(Path(brains.environ.get("CLAUDE_CONFIG_DIR", brains.home / ".claude")))}
    class IsolatedBrains:
        home = directory
        command = brains.command
        logged_in = brains.logged_in
    isolated = IsolatedBrains()
    isolated.environ = environ
    try:
        for model in models:
            for member in (0, 1):
                for scenario in SCENARIOS:
                    if (api_only and brain_ref(model)) or (not brain_ref(model) and not key):
                        item = {"brain": model, "member": member, "scenario": scenario.name, "status": "SKIP", "tools": [], "latency_ms": 0, "cost_usd": None, "reason": "CLI excluded by API-only mode" if brain_ref(model) else "OpenRouter key unavailable"}
                    elif budget.used >= budget.limit:
                        item = {"brain": model, "member": member, "scenario": scenario.name, "status": "SKIP", "tools": [], "latency_ms": 0, "cost_usd": None, "reason": "total_turn_cap"}
                    else:
                        sessions.clear()
                        item = await cell(model, member, scenario, brains=isolated, client=client, sessions=sessions, conn=conn, budget=budget, seconds=seconds)
                    results.append(item)
                    print(f"{item['status']:4} {model:42} member={member} {scenario.name:16} {item['reason']}", flush=True)
                    report["turns"] = budget.used
                    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        conn.close()
        if own_client:
            await client.aclose()
        await brains.aclose()
    failed = any(item["status"] == "FAIL" for item in results)
    incomplete = not any(item["status"] == "PASS" for item in results) or any(item["reason"] == "total_turn_cap" for item in results)
    report.update(status="failed" if failed else "inconclusive" if incomplete else "passed", finished_at=datetime.now(timezone.utc).isoformat(), counts=dict(Counter(item["status"] for item in results)))
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Matrix: " + report["status"] + " " + json.dumps(report["counts"]))
    print(f"Report: {report_path}")
    return 1 if failed else 2 if incomplete else 0


def main():
    use_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("RAPHAEL_LIVE_CONFIG"), help="JSON with codex and api model IDs (or a models list)")
    parser.add_argument("--out", type=Path, default=ROOT / "core" / ".live")
    parser.add_argument("--max-turns", type=int, default=200)
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--api-only", action="store_true", help="Hosted nightly: skip subscription CLIs")
    parser.add_argument("--legacy-only", action="store_true", help="Run the original Guardian cells without the scenario suite")
    parser.add_argument("--registry-directory", type=Path, help="Sanitized ConfigStore snapshot directory; inventory includes disabled/lazy tools")
    parser.add_argument("--manifest", type=Path, help="Reviewed tool-to-cell manifest for this registry snapshot")
    parser.add_argument("--real-smoke", metavar="CONNECTOR", help="Separate owner-approved connector smoke (no automatic approval)")
    parser.add_argument("--owner-approved-real-smoke", action="store_true", help="Explicit owner authorization for --real-smoke")
    args = parser.parse_args()
    if args.real_smoke:
        print("Real smoke refused: " + ("connector smoke adapter not implemented" if args.owner_approved_real_smoke
                                        else "requires --owner-approved-real-smoke"))
        return 1
    try:
        if args.legacy_only:
            return asyncio.run(run(read_models(args.config), args.out, max_turns=args.max_turns, seconds=args.seconds, api_only=args.api_only))
        from .guardian_suite import read_pair, run as run_suite
        from .guardian_registry import load_manifest
        from ..integrations.config import ConfigStore
        if args.registry_directory and not (args.registry_directory / "integrations.json").is_file():
            raise ValueError("registry_snapshot_missing")
        servers = ConfigStore(args.registry_directory).servers() if args.registry_directory else ()
        return asyncio.run(run_suite(read_pair(args.config), args.out, max_turns=args.max_turns, seconds=args.seconds,
                                     api_only=args.api_only, servers=servers,
                                     mapping=load_manifest(args.manifest) if args.manifest else None))
    except Exception as error:
        print("Guardian could not start: " + type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
