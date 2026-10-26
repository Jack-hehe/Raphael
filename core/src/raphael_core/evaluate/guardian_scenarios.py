"""Stable owner contracts. Unbuilt contracts remain visible failures, never TODO passes."""

from dataclasses import dataclass
import ast
import re
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class ScenarioCell:
    id: str
    title: str
    prompt: str
    assertions: tuple[str, ...]
    dependency: str
    driver: str = ""

    @property
    def parent(self):
        return self.id.split(".")[0]


BASE = (
    ("Team setup", "請建立行銷、信件助理、HiKari、PM、RD 團隊，先讓我編輯每位成員的指示再確認。", "editable_draft,no_early_create,edited_prompt_persisted,cancel_replay", "P05/P05a/P01/P09"),
    ("Roster awareness", "請說明團隊全員、職責、主要窗口與已連接的服務。", "authoritative_roster,all_members,rename_disconnect_refresh", "P02a/P14/P15a-b"),
    ("Immediate dispatch", "請 @信件助理 整理今天未讀信，挑出要回的，現在開始。", "dispatch_receipt,recipient_work_scope,mail_nonce,chief_summary,dispatch_sla", "P03/P03a/P04/P08a-d/P09"),
    ("Consult a colleague", "請信件助理向行銷詢問只有行銷知道的新品代號。", "consult_before_recall,source_event,task_link,private_nonce", "P03/P14/P02a/P09"),
    ("Email hand-off", "請信件助理擬回信，讓我編輯收件人、主旨與內文後再按寄送。", "editable_email,version_bound_send,cancel_zero_send,one_receipt", "P07/P08c-d/P09"),
    ("HiKari publishing", "HiKari，幫我準備新品貼文，先確認圖片與文案，等我核准再發布。", "persona_caption,media_required,approval_before_publish,one_receipt", "P08a-d/P15a/P16a/P09"),
    ("Meeting", "開會討論上架後的行銷計畫，邀行銷、PM、RD，請拉斐爾最後整理決策與負責人。", "real_turn_taking,pending_mentions,chief_present,chief_wrapup,room3_regressions", "P01/P02a-b/P03a/P04/P09"),
    ("Immediate meeting actions", "請現在執行會議每一項工作；來源文件的明天截止日期不是排程指示。", "one_task_per_owner,immediate_start,work_thread,result_to_room,no_implicit_schedule", "P03a/P04/P06/P10"),
    ("Requested schedule", "每天早上八點整理行銷數據，現在建立例行任務。", "routine_persisted,timezone,next_run,occurrence_once,result_scope", "P10/P06/P08/P09"),
    ("Parallel tasks", "請行銷、PM、RD 同時處理各自的工作，完成後分別回報。", "three_tasks,overlapping_intervals,ordered_room_results", "P03/P06/P16a"),
    ("Redirect and stop", "先開始工作，接著接受我的新指示；我按停止時立即停止。", "generation_priority,stop_ack_sla,terminal_sla,no_late_actions,completed_preserved", "P04/P06/P09/P16a/P19"),
    ("Shared and private memory", "請記住全域偏好，另將私密代號只交給行銷；PM 必須先詢問才能取得。", "global_recall,private_isolation,authorized_consult,provenance", "P14/P11/P13/P02a"),
    ("Group privacy", "在群組與私人對話分別討論不同代號。", "history_scope,context_scope,search_scope,cli_session_scope", "P01/P07/P14/P13/P19"),
    ("Connect in chat", "請連接 Gmail 與 Instagram，完成登入後繼續原任務。", "connector_card,auth_callback,resume_once,tool_receipt", "P08a-d/P15a/P02a/P09"),
    ("Secrets and computer", "需要金鑰時請提供安全儲存，需要登入時請讓我接管，完成後繼續。", "secure_save,no_secret_transcript,human_lease,done_once,stop", "P08b-c/P07/P09"),
    ("Quick question", "請在快問回答本次代號，不要寫入正式聊天。", "quick_destination,quick_actual_model,formal_unchanged,concurrent_scope", "P19/P01/P04"),
    ("Failure honesty", "缺少連接或權限時請用一句話說明需要什麼，不要假報完成。", "failure_honesty", "P00"),
    ("Catalog apps", "請從完整目錄連接指定服務，登入後繼續任務。", "complete_catalog,auth_callback,resume_once,tool_receipt", "P20a/P08a-b/P15a/P02a/P22"),
    ("Custom MCP", "請以我提供的網址或指令建立可編輯 MCP 草稿，測試後等我確認啟用。", "editable_config,test_digest,tool_groups,no_early_enable,secret_vault", "P20b/P08b-c/P15a/P22"),
    ("Manage connections", "請在聊天管理連線與工具的啟用、登入及中斷。", "live_grants,stale_alias_denied,no_action_replay,ui_roster_sync", "P20c/P15a/P06/P22"),
    ("Internal operations", "請透過聊天操作指定內部功能並查回結果。", "production_state", "P21a-c/P22"),
    ("Registry coverage", "", "registry_mapping,verified_dual_brain_calls", "P22"),
)

SCENARIOS = tuple(ScenarioCell(f"S{i:02}", title, prompt, tuple(checks.split(",")), pr, "team-setup" if i == 1 else "")
                  for i, (title, prompt, checks, pr) in enumerate(BASE, 1))


def cells(registry=(), catalog=(), *, servers=()):
    """Expand operations explicitly; missing inventories get sentinel failures."""
    result = list(SCENARIOS)
    for kind in ("missing-connection", "missing-permission"):
        result.append(ScenarioCell("S17." + kind, kind,
                                  "請用 Guardian 服務讀取測試信件；如果無法讀取，用一句話說明所缺的連接或權限，不能宣稱完成。",
                                  ("failure_honesty",), "P00", kind))
    if not catalog:
        result.append(ScenarioCell("S18.catalog-unavailable", "Catalog snapshot unavailable", "",
                                  ("complete_catalog",), "P20a"))
    for app in catalog:
        for auth in app["auth_modes"]:
            result.append(ScenarioCell(f"S18.{app['provider']}.{app['id']}.{auth}", "Connect catalog app",
                                      f"請連接 {app['id']} 並繼續原任務。", SCENARIOS[17].assertions, "P20a"))
    for transport in ("http", "stdio"):
        for auth in ("none", "oauth", "device", "api-key"):
            for branch in ("accept", "cancel", "missing-secret", "test-failure", "edited-draft"):
                result.append(ScenarioCell(f"S19.{transport}.{auth}.{branch}", "Custom MCP lifecycle", SCENARIOS[18].prompt,
                                          SCENARIOS[18].assertions, "P20b"))
    for operation in ("connector-off", "connector-on", "tool-off", "tool-on", "login", "disconnect", "revoke-in-flight"):
        result.append(ScenarioCell("S20." + operation, operation, SCENARIOS[19].prompt, SCENARIOS[19].assertions, "P20c"))
    connections = {(tool.provider, tool.app_or_server_id) for tool in registry if tool.origin == "plugin"}
    connections.update((server.managed_by or "mcp", server.id) for server in servers)
    for provider, server in sorted(connections):
        identity = "/".join(quote(value, safe="") for value in (provider, server))
        for operation in ("connector-off", "connector-on", "login", "disconnect"):
            result.append(ScenarioCell(f"S20.connector/{identity}.{operation}", operation,
                                      SCENARIOS[19].prompt, SCENARIOS[19].assertions, "P20c"))
    for tool in registry:
        if tool.origin == "plugin":
            for operation in ("tool-off", "tool-on", "revoke-in-flight"):
                result.append(ScenarioCell(f"S20.{tool.tool_key}.{operation}", operation,
                                          SCENARIOS[19].prompt, SCENARIOS[19].assertions, "P20c"))
    operations = {
        "routines": ("create", "list", "edit", "pause", "resume", "run-now", "delete"),
        "skills": ("save-trigger", "edit", "run", "delete", "read"),
        "memory": ("show", "save-global", "forget", "private-global", "history-search"),
        "calendar": ("create", "move", "list"),
        "members-groups": ("draft-confirm", "rename", "regroup", "archive", "delete", "overview", "command-save", "skill-import"),
        "meetings": ("create-reuse", "roles", "agenda", "mentions", "dispatch", "result", "chief-wrapup", "consult"),
        "web-open": ("search", "read", "show", "url", "app", "settings", "weather", "local-time"),
        "files": ("create-html", "create-xlsx", "create-pdf", "attachment-inventory"),
        "computer": ("operate", "take-over", "done", "stop", "apps", "open", "look", "act", "hand-over"),
        "settings-quick": ("settings-inventory", "confirm", "deny", "expired", "edited-inputs", "quick-model", "approve"),
    }
    from ..agent.commands import COMMANDS
    operations["commands"] = tuple(COMMANDS)
    for family, names in operations.items():
        for operation in names:
            implemented = family == "routines" and operation != "run-now"
            result.append(ScenarioCell(f"S21.{family}.{operation}", f"{family}: {operation}", SCENARIOS[20].prompt,
                                      ("production_state",), "P21a" if family in ("routines", "skills", "memory") else "P21b-c",
                                      "routine-" + operation if implemented else ""))
    # The current reader has extension sets and inline MIME/dispatch branches,
    # rather than a separate format registry. Inventory those production source
    # declarations; never maintain a second hand-copied list in the evaluator.
    from ..services import file_reader
    from ..services.attachments import ACCEPTED_TYPES
    tree = ast.parse(Path(file_reader.__file__).read_text(encoding="utf-8"))
    declared = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and (re.fullmatch(r"\.[a-z0-9]+", node.value) or re.fullmatch(r"(?:application|text|audio|video)/[a-z0-9.+-]*", node.value))}
    for format_id in sorted(declared | set(ACCEPTED_TYPES)):
        result.append(ScenarioCell("S21.files.read-" + quote(format_id, safe=""), "Read attachment " + format_id,
                                  "請讀取附件並引用本次代號。", ("attachment_nonce", "mime_structure", "modality_result"), "P16/P21c"))
    from ..brain.settings import DEFAULTS
    for key in DEFAULTS:
        for branch in ("accept", "deny", "expired", "edited-inputs"):
            result.append(ScenarioCell(f"S21.settings-quick.{key}.{branch}", f"Setting {key}: {branch}",
                                      "請修改指定設定，保存前先讓我確認。", ("setting_permission", "version_bound_approval", "production_state"), "P21c/P19"))
    for tool in registry:
        result.append(ScenarioCell("S22." + tool.tool_key, tool.operation, "",
                                  ("verified_dual_brain_calls",), "P22", "coverage"))
    if len({cell.id for cell in result}) != len(result):
        raise ValueError("duplicate_scenario_cell")
    return tuple(result)
