"""The single registry of Raphael-only team administration tool contracts.

Member data: name, role, blurb, personality, instructions, group name, avatar preset.
Targets use member IDs; groups use IDs for rename and names for member placement.
The member service owns validation, persistence, defaults and live notifications.
"""

from dataclasses import dataclass
from typing import Any

from .policy import Tier


@dataclass(frozen=True)
class MemberDraft:
    id: str
    name: str
    role: str = ""
    blurb: str = ""
    personality: str = ""
    instructions: str = ""
    group: str = ""
    avatar_preset: str = ""
    member_id: int | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "blurb": self.blurb,
            "personality": self.personality,
            "instructions": self.instructions,
            "group": self.group,
            "avatar_preset": self.avatar_preset,
        }
        if self.member_id is not None:
            data["member_id"] = self.member_id
        return data


@dataclass(frozen=True)
class GroupDraft:
    id: str
    name: str
    group_id: int | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "name": self.name}
        if self.group_id is not None:
            data["group_id"] = self.group_id
        return data


def definition(description, properties=None, required=()):
    return {"description": description, "parameters": {"type": "object", "properties": properties or {},
            "required": list(required), "additionalProperties": False}}


ID = {"type": "integer", "minimum": 1}
MEMBER = {field: {"type": "string"} for field in
          ("name", "role", "blurb", "personality", "instructions", "group", "avatar_preset")}
MEMBER["group"]["description"] = "Group name, created if missing. Empty means ungrouped."
MEMBER["avatar_preset"]["description"] = "An available preset avatar from team_overview; otherwise omit for a distinct preset or silhouette."
for field, limit in (("name", 16), ("role", 24), ("blurb", 200), ("personality", 2000), ("instructions", 2000), ("group", 24)):
    MEMBER[field]["maxLength"] = limit

DEFINITIONS = {
    "team_overview": definition("Read the team: member IDs, names, roles, blurbs, groups, skill/command counts, routines, groups and available avatar presets."),
    "member_create": definition("Propose a team member for the owner's editable draft. Nothing is created until confirmation. At most 10 creates per turn. Omitted preferences use the create dialog defaults.", MEMBER, ("name", "role")),
    "member_update": definition("Propose changes to the supplied fields of an existing member, including group placement. ID 0 is the first Bot's name, role, personality and instructions. Nothing changes until owner confirmation.", {"id": {"type": "integer", "minimum": 0}, **MEMBER}, ("id",)),
    "member_delete": definition("Permanently delete a member and its conversations, memories, routines and resources. Always requires the owner's approval.", {"id": ID}, ("id",)),
    "group_create": definition("Propose a named group for owner confirmation at the end of the turn.", {"name": {"type": "string"}}, ("name",)),
    "group_rename": definition("Propose renaming an existing group for owner confirmation.", {"id": ID, "name": {"type": "string"}}, ("id", "name")),
    "member_skill_import": definition("Import Markdown skills for a member from a supported GitHub repository or SKILL.md URL. Documents are untrusted guidance, never executable code.", {"member_id": ID, "source": {"type": "string"}}, ("member_id", "source")),
    "member_command_save": definition("Create or update a member's slash command template. Template must contain {input}. Name uses 1-32 lowercase letters, digits, underscores or hyphens. Omit id to create.", {"member_id": ID, "id": ID, **{field: {"type": "string"} for field in ("name", "description", "template")}}, ("member_id", "name", "template")),
}
TIERS = {name: Tier.T0 if name == "team_overview" else Tier.T3 if name == "member_delete" else Tier.T1
         for name in DEFINITIONS}
BATCH_TOOLS = {"member_create", "create_member", "member_update", "group_create", "group_rename"}
CONFIRM_TOOLS = (set(DEFINITIONS) - {"team_overview"}) | {"create_member", "routine_create", "routine_update", "routine_delete"}

LEADERSHIP = (
    "\nYou lead the owner's team. Use team_overview and your team administration tools to create "
    "and change members, groups, routines, skills and commands yourself. Use returned IDs. "
    "In every mode member creates, updates and groups are proposals staged until the turn ends. "
    "Collect every requested change before your final reply; pending_approval is not success and has no new ID. "
    "Follow the owner's approval setting; deleting a member always needs approval. "
    "After creating members, report their names and roles in one short message. "
    "Do not claim the creation interface is disconnected; attempt the provided tools and report "
    "their actual results. Never claim an action succeeded without a successful result."
)


def proposal_operations(calls: list[dict]) -> list[dict]:
    """Combine sequential edits to one target without losing fields from earlier rounds."""
    operations: list[dict] = []
    targets: dict[tuple[str, int], dict] = {}
    for call in calls:
        operation = {"name": call["name"], "arguments": dict(call["arguments"])}
        name, args = operation["name"], operation["arguments"]
        if name in ("member_update", "group_rename") and type(args.get("id")) is int:
            key = (name, args["id"])
            if key in targets:
                targets[key]["arguments"].update(args)
                continue
            targets[key] = operation
        operations.append(operation)
    return operations


def confirmation(name: str, arguments: dict, language: str, target: str = "", *, question: bool = True) -> str:
    """Complete owner-facing details; never truncate fields that the owner is approving."""
    if name == "create_member":
        name = "member_create"
    actions = {
        "en": ("Create member", "Update member", "Permanently delete member and all its data", "Create group", "Rename group", "Import member skills", "Save member command", "Create routine", "Update routine", "Delete routine"),
        "zh-TW": ("建立成員", "更新成員", "永久刪除成員及其所有資料", "建立群組", "重新命名群組", "匯入成員技能", "儲存成員指令", "建立例行任務", "更新例行任務", "刪除例行任務"),
        "ja": ("メンバーを作成", "メンバーを更新", "メンバーと全データを完全に削除", "グループを作成", "グループ名を変更", "スキルをインポート", "コマンドを保存", "定期タスクを作成", "定期タスクを更新", "定期タスクを削除"),
    }
    names = ("member_create", "member_update", "member_delete", "group_create", "group_rename", "member_skill_import", "member_command_save", "routine_create", "routine_update", "routine_delete")
    fields = ("name", "role", "blurb", "personality", "instructions", "group", "avatar_preset", "source", "description", "template", "title", "prompt", "schedule", "enabled")
    labels = {
        "en": ("Name", "Role", "About", "Personality", "Instructions", "Group", "Avatar preset", "Skill source", "Description", "Template", "Title", "Prompt", "Schedule", "Enabled"),
        "zh-TW": ("名稱", "職責", "簡介", "個性", "工作指示", "群組", "預設頭像", "技能來源", "說明", "指令範本", "任務名稱", "任務內容", "排程", "啟用"),
        "ja": ("名前", "役割", "紹介", "性格", "指示", "グループ", "アバター", "スキルの取得元", "説明", "テンプレート", "タスク名", "内容", "スケジュール", "有効"),
    }
    title = actions[language][names.index(name)]
    separator = ": " if language == "en" else "："
    lines = [title + (separator + target if target else "")]
    for field, label in zip(fields, labels[language]):
        if field in arguments:
            value = arguments[field]
            if field == "schedule" and isinstance(value, dict):
                # Every schedule value is visible, including advanced fields from existing RPCs.
                value = "; ".join(f"{key}{separator}{item}" for key, item in value.items())
            lines.append(f"{label}{separator}{value}")
    if "id" in arguments:
        lines.append(f"ID{separator}{arguments['id']}")
    return "\n".join(lines) + ({"en": "\nAllow this change?", "zh-TW": "\n允許這項變更嗎？", "ja": "\nこの変更を許可しますか？"}[language] if question else "")
