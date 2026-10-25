"""One atomic store for non-secret connector identity, accounts and tool decisions."""

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATUSES = {"INITIALIZING", "INITIATED", "ACTIVE", "FAILED", "EXPIRED", "INACTIVE", "REVOKED"}


@dataclass
class Connector:
    id: str
    kind: str
    status: str = "INACTIVE"
    accounts: list[dict] = field(default_factory=list)
    disabled_tools: list[str] = field(default_factory=list)
    enabled_tools: list[str] = field(default_factory=list)
    draft_only: bool = False
    gmail: str = "write"
    calendar: str = "write"
    granted_scopes: list[str] = field(default_factory=list)


class ConnectorStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "connectors.json"
        self.user_id = "raphael_" + str(uuid.uuid4())
        self.session_id = ""
        self.config_version: int | None = None
        self.connectors: dict[str, Connector] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError("Invalid connector store")
            self.user_id = raw["user_id"]
            if not isinstance(self.user_id, str) or not self.user_id.startswith("raphael_"):
                raise ValueError("Invalid connector identity")
            self.session_id = raw.get("session_id", "")
            self.config_version = raw.get("config_version")
            for item in raw.get("connectors", []):
                connector = Connector(**item)
                if connector.kind not in ("composio", "google") or connector.status not in STATUSES:
                    raise ValueError("Invalid connector")
                self.connectors[connector.id] = connector
        except FileNotFoundError:
            self.save()
        except (ValueError, TypeError, KeyError):
            # Fail closed; never silently replace the install identity owning remote accounts.
            raise ValueError("Could not read connector configuration") from None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = {"version": 1, "user_id": self.user_id, "session_id": self.session_id,
               "config_version": self.config_version,
               "connectors": [asdict(c) for c in self.connectors.values()]}
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
