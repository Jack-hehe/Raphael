"""MCP definition imports from a fixed list of config files. No login or history is consulted.

Previews retain secret values in memory only and return opaque, short-lived selection ids. A new
preview replaces the old one; importing revalidates against the current names before saving.
"""

import json
import os
import secrets
import tomllib
from pathlib import Path

from ..protocol import invalid_params
from .config import parse_new_server


class McpImports:
    def __init__(self, home=None):
        self.home = Path.home() if home is None else Path(home)
        self.pending = {}

    def preview(self, documents, taken):
        self.pending.clear()
        result, rejected = [], 0
        names = list(taken)
        for source, document in documents:
            if not isinstance(document, dict):
                continue
            definitions = document.get("mcpServers", document.get("mcp_servers", {}))
            if not isinstance(definitions, dict):
                continue
            for name, definition in definitions.items():
                if not isinstance(name, str) or any(name.casefold() == other.casefold() for other in names):
                    continue
                try:
                    if not isinstance(definition, dict):
                        raise ValueError
                    params = {"name": name, "transport": "http" if definition.get("url") else "stdio"}
                    for key in ("command", "args", "url", "result_size_limit"):
                        if key in definition:
                            params[key] = definition[key]
                    for key in ("env", "headers"):
                        values = definition.get(key, definition.get("http_headers", {}) if key == "headers" else {})
                        if not isinstance(values, dict):
                            raise ValueError
                        params[key] = [{"name": k, "value": v} for k, v in values.items()]
                    parsed = parse_new_server(params, names)
                except (TypeError, ValueError):
                    rejected += 1
                    continue
                ident = secrets.token_hex(12)
                self.pending[ident] = params
                server = parsed.config
                result.append({"id": ident, "source": source, "name": name, "transport": server.transport,
                               "command": server.command, "args": server.args, "url": server.url,
                               "env": server.env, "headers": server.headers})
                names.append(name)
        return {"candidates": result, "rejected": rejected}

    def pasted(self, text, taken):
        if not isinstance(text, str) or len(text) > 2_000_000:
            raise invalid_params("Expected MCP JSON under 2 MB")
        try:
            document = json.loads(text)
            if not isinstance(document, dict) or not isinstance(document.get("mcpServers"), dict):
                raise ValueError
        except ValueError:
            raise invalid_params("Expected a JSON object with mcpServers") from None
        return self.preview([("JSON", document)], taken)

    def discover(self, taken):
        home = self.home
        # Only these exact configuration paths are ever opened. Symlinks, oversized files and
        # malformed configs are skipped; project history and authentication stores are not paths.
        desktop = (Path(os.environ.get("APPDATA", home / "AppData/Roaming")) if home == Path.home()
                   else home / "AppData/Roaming") / "Claude/claude_desktop_config.json"
        files = [("Claude Code", home / ".claude.json"), ("Claude Code", home / ".claude/settings.json"),
                 ("Claude Desktop", desktop), ("Codex", home / ".codex/config.toml"),
                 ("Cursor", home / ".cursor/mcp.json"), ("Antigravity", home / ".gemini/config/mcp_config.json")]
        documents = []
        for source, path in files:
            try:
                if path.is_symlink() or path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8-sig")
                document = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
                documents.append((source, document))
            except (OSError, ValueError, UnicodeError):
                continue
        return self.preview(documents, taken)
