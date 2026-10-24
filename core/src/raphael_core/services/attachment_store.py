"""Opaque IDs for stored copies. Never use an owner-supplied name as a path."""

import asyncio
import base64
import json
import re
import uuid
from dataclasses import replace
from pathlib import Path

from ..protocol import invalid_params
from .attachments import Attachment, MAX_FILE_BYTES

ID = re.compile(r"[a-f0-9]{32}\.[a-z0-9]{1,12}")
EXECUTABLE = {"exe", "com", "bat", "cmd", "ps1", "vbs", "vbe", "js", "jse", "wsf", "wsh", "msi", "msp", "scr", "hta", "lnk", "url", "reg", "cpl", "dll", "sh", "py", "jar", "appref-ms"}


class AttachmentStore:
    def __init__(self, root: Path):
        self.root = root / "attachments"

    def save(self, attachment: Attachment) -> Attachment:
        self.root.mkdir(parents=True, exist_ok=True)
        suffix = Path(attachment.name).suffix.lower().lstrip(".")
        suffix = suffix if re.fullmatch(r"[a-z0-9]{1,12}", suffix) else "bin"
        key = f"{uuid.uuid4().hex}.{suffix}"
        target = self.root / key
        target.write_bytes(attachment.data)
        target.with_name(key + ".json").write_text(json.dumps({"mime": attachment.mime}), encoding="utf-8")
        return replace(attachment, stored_id=key)

    def path(self, key) -> Path:
        if not isinstance(key, str) or not ID.fullmatch(key):
            raise invalid_params("invalid attachment ID")
        target = (self.root / key).resolve()
        if target.parent != self.root.resolve() or not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
            raise invalid_params("attachment is unavailable")
        return target

    def register(self, router):
        router.register("attachments.get", self.get)
        router.register("attachments.path", self.open_path)

    async def get(self, params):
        path = self.path(params.get("id"))
        def read():
            meta = json.loads(path.with_name(path.name + ".json").read_text(encoding="utf-8"))
            return {"mime": meta["mime"], "data": base64.b64encode(path.read_bytes()).decode("ascii")}
        return await asyncio.to_thread(read)

    async def open_path(self, params):
        path = self.path(params.get("id"))
        if path.suffix.lstrip(".") in EXECUTABLE:
            raise invalid_params("executable attachments cannot be opened")
        return {"path": str(path)}
