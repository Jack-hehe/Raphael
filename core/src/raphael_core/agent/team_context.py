"""One secret-free, content-addressed team inventory for prompts and mention menus."""

import hashlib
import json
from dataclasses import asdict, dataclass

from .speaker import RAPHAEL_NAMES


@dataclass(frozen=True)
class TeamContextSnapshot:
    schema_version: int
    revision: str
    self_id: int
    count: int
    primary_id: int
    chief_id: int
    members: list
    apps: list
    inventory_state: str
    room_member_ids: list | None

    def wire(self):
        return asdict(self)


def snapshot(team, self_id, language, room_member_ids=None):
    members = [{"id": s.id, "name": RAPHAEL_NAMES[language] if s.id == 0 else s.name,
                "role": ({'zh-TW':'核心助手／幕僚長','en':'Core assistant / Chief of staff','ja':'コアアシスタント／参謀長'}[language]
                         if s.id == 0 else s.role), "blurb": s.blurb}
               for s in team.roster()]
    state = "complete"
    try:
        connections = team._connections()
    except Exception:
        connections, state = [], "unknown"
    apps = []
    for app in connections:
        # Copy only public metadata, never provider configuration or credentials.
        status = app.get("status", "unknown")
        apps.append({"id": app["id"], "name": app["name"], "enabled": bool(app["enabled"]),
                     "state": "needs_sign_in" if app.get("error") == "auth" else status})
    content = {"members": members, "apps": apps, "inventory_state": state,
               "primary_id": 0, "chief_id": 0}
    revision = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return TeamContextSnapshot(1, revision, self_id, len(members), 0, 0, members, apps, state, room_member_ids)
