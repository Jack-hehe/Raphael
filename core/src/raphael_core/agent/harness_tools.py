"""Tool descriptions shared by the interactive harness and the safety gate."""

from ..connectors.apps import APPS
from .commands import COMMANDS
from .plugins import PluginTool
from .policy import Tier
from .team_admin import DEFINITIONS as ADMIN_TOOLS, TIERS as ADMIN_TIERS

CONNECT_APP = "connect_app"
REQUEST_SECRET = "request_secret"
EMAIL_DRAFT = "email_draft"


class HarnessToolError(Exception):
    """A public failure kind for a native tool; never contains arguments or private data."""


SCHEDULE = {
    "type": "object", "description": "Owner's local time zone. Daily or weekly at HH:MM (24-hour). Weekdays means weekly days [1,2,3,4,5]; Sunday is 0.",
    "properties": {"kind": {"type": "string", "enum": ["daily", "weekly", "every"]}, "time": {"type": "string"}, "days": {"type": "array", "items": {"type": "integer"}}, "hours": {"type": "integer"}}, "required": ["kind"],
}
ROUTINE_FIELDS = {"title": {"type": "string"}, "prompt": {"type": "string", "description": "What this speaker should do when due, including the learning goal."}, "schedule": SCHEDULE, "enabled": {"type": "boolean"}}


def definition(description, properties=None, required=()):
    return {"description": description, "parameters": {"type": "object", "properties": properties or {}, "required": list(required)}}


DEFINITIONS = {
    "show": definition("Show a seen URL inside this conversation as a video, PDF, image or link card. Prefer this over opening a browser.", {"url": {"type": "string"}}, ["url"]),
    "routine_create": definition("Create a routine for the current speaker from the owner's request. Translate daily/weekdays/weekly times into a schedule in the owner's local time zone.", ROUTINE_FIELDS, ["title", "prompt", "schedule"]),
    "routine_list": definition("List only the current speaker's routines."),
    "routine_update": definition("Update the current speaker's routine by id; give only changed fields.", {"id": {"type": "integer"}, **ROUTINE_FIELDS}, ["id"]),
    "routine_delete": definition("Delete the current speaker's routine by id.", {"id": {"type": "integer"}}, ["id"]),
    "computer": definition("Operate the computer for the owner at the keyboard, using the guarded desktop loop and its permission questions. For an explicitly requested desktop task only; prefer showing web content in this chat. When the task reaches a sign-in, a password or a payment it is the owner's to do: a card in this chat hands the computer over, this call returns at once saying it is paused, and the task carries on by itself, once, when the owner gives the computer back. Then end your turn with one short sentence, and never claim the task's result before it is done.", {"request": {"type": "string"}}, ["request"]),
    CONNECT_APP: definition("Show the owner a card in this chat to connect an app this request needs that is not connected, or whose sign-in expired (see the apps in the TeamContextSnapshot). The owner connects from the card; then this request resumes by itself, once, with the app's tools. After calling it, end your turn with one short sentence, and never claim the app's data before it is connected.", {"app": {"type": "string", "enum": list(APPS)}}, ["app"]),
    REQUEST_SECRET: definition("Show the owner a card in this chat to save a key or token that a connection needs and does not have: a tool of it failed with missing_secret, or its state in the TeamContextSnapshot is needs_secret. Give the connection's id or name as listed there, and the secret's name only when it declares several. The owner types the key into the card and it goes straight to the secure vault: you never see it. Then this request resumes by itself, once, with the connection's tools. After calling it, end your turn with one short sentence, and never claim the connection's data before the key is saved.", {"connection": {"type": "string"}, "secret": {"type": "string"}}, ["connection"]),
    EMAIL_DRAFT: definition(
        "Write an email for the owner to review: it is shown in this chat as an editable card, and only the owner sends it (the card's 傳送), once, "
        "after they have edited whatever they want. You cannot send it and must not call a tool that sends mail yourself; never say it was sent. "
        "Give the recipients (to, and cc if any), the subject and the whole body in the owner's language. Give via only to name which connected mail app "
        "sends it. Give hand_to (a colleague's name) when the owner wants that colleague to send it: the owner is then asked whether the colleague may "
        "send it, and on yes the same draft appears in the colleague's chat; handing it over never sends it. Give revise as true to update the draft "
        "still waiting in this chat instead of writing another. After calling it, end your turn with one short sentence.",
        {"to": {"type": "string", "description": "Recipient addresses, separated by commas."},
         "cc": {"type": "string", "description": "Copied addresses, separated by commas."},
         "subject": {"type": "string"}, "body": {"type": "string"},
         "via": {"type": "string", "description": "The connected mail app that sends it, as the TeamContextSnapshot lists it."},
         "hand_to": {"type": "string", "description": "The name of the colleague who may send it."},
         "revise": {"type": "boolean"}},
        ["to", "subject", "body"]),
}
for name, command in COMMANDS.items():
    if name == "open":
        properties = {"target_kind": {"type": "string", "enum": ["url", "app", "settings"]}, "target": {"type": "string", "description": "A public http(s) URL, installed app name, or Windows settings page name."}}
        required = ["target_kind", "target"]
    elif name == "copy_text":
        properties, required = {"text": {"type": "string"}}, ["text"]
    else:
        properties, required = {}, []
    DEFINITIONS[name] = definition(command.guidance + " Only for an owner at this computer. Open a browser only when explicitly asked to open it.", properties, required)

TIERS = {name: Tier.T0 if name in ("show", "routine_list", "computer", CONNECT_APP, REQUEST_SECRET, EMAIL_DRAFT) else Tier.T1 for name in DEFINITIONS if name not in COMMANDS}
ROUTINE_TOOLS = frozenset(name for name in DEFINITIONS if name.startswith("routine_"))
DEFINITIONS.update(ADMIN_TOOLS)
TIERS.update(ADMIN_TIERS)


def definitions_for(member_id: int):
    """Administration is never offered to peers; only Raphael can select routine targets."""
    for name, item in DEFINITIONS.items():
        if name in ADMIN_TOOLS and member_id != 0:
            continue
        if name in ROUTINE_TOOLS and member_id == 0:
            schema = item["parameters"]
            item = {**item, "description": item["description"] + " Raphael may supply member_id to manage any member's routines.",
                    "parameters": {**schema, "properties": {**schema["properties"], "member_id": {"type": "integer", "minimum": 0}}}}
        yield name, item


def tool(name: str) -> PluginTool:
    item = DEFINITIONS[name]
    return PluginTool(key=name, server="Raphael", name=name, description=item["description"], schema=item["parameters"], tier=COMMANDS[name].tier if name in COMMANDS else TIERS[name])


def routine_event(name: str, data: dict, language: str) -> str:
    routine = data.get("routine", {})
    schedule = routine.get("schedule", {})
    words = {"zh-TW": ("已建立例行任務", "已更新例行任務", "已刪除例行任務", "每天", "每週", "每小時"), "en": ("Routine created", "Routine updated", "Routine deleted", "Daily", "Weekly", "Every hour"), "ja": ("定期タスクを作成しました", "定期タスクを更新しました", "定期タスクを削除しました", "毎日", "毎週", "毎時")}[language]
    action = words[0 if name == "routine_create" else 1 if name == "routine_update" else 2]
    kind = schedule.get("kind")
    if kind == "weekly":
        days = schedule.get("days", [])
        names = {"zh-TW": "日一二三四五六", "ja": "日月火水木金土", "en": ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")}[language]
        timing = words[4] + " " + ",".join(names[day] for day in days) + " " + str(schedule.get("time", ""))
    elif kind == "every":
        hours = schedule.get("hours", 1)
        timing = {"zh-TW": f"每 {hours} 小時", "ja": f"{hours}時間ごと", "en": f"Every {hours} hours"}[language]
    else:
        timing = words[3] + " " + str(schedule.get("time", ""))
    return action if not routine else f"{action} · {timing.strip()} {routine.get('title', '')}"
