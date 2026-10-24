"""Managed MCP adapters, sharing Raphael's hub, gate and value-free audit.

The Composio HTTP adapter does not need a background GET stream. POST replies may
be JSON or SSE; DELETE 405 means the endpoint has no termination operation.
"""

import json
from types import SimpleNamespace
from urllib.parse import urlsplit

from ..integrations.client import McpFailure
from .composio import trusted_url
from .http import ConnectorFailure, PrivateHttp


def _tool(raw: dict):
    annotations = raw.get("annotations", {})
    return SimpleNamespace(name=raw["name"], description=raw.get("description", ""),
        input_schema=raw.get("inputSchema", {}), title=raw.get("title"),
        annotations=SimpleNamespace(read_only_hint=annotations.get("readOnlyHint"),
                                    destructive_hint=annotations.get("destructiveHint")))


def _result(raw: dict):
    # Connected app results are in memory only and are never diagnostics.
    content = [SimpleNamespace(**item) for item in raw.get("content", []) if isinstance(item, dict)]
    return SimpleNamespace(content=content, structured_content=raw.get("structuredContent"), is_error=raw.get("isError", False))


class ManagedMcp:
    def __init__(self, url: str, headers: dict, *, transport=None, call_guard=None) -> None:
        self.url = trusted_url(url)
        self._headers = {**headers, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self.http = PrivateHttp(transport=transport)
        self._next = 0
        self.call_guard = call_guard

    async def __aenter__(self):
        try:
            result = await self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                   "clientInfo": {"name": "Raphael", "version": "0.2.0"}})
            self._headers["MCP-Protocol-Version"] = result.get("protocolVersion", "2025-03-26")
            await self.rpc("notifications/initialized", {}, notification=True)
        except ConnectorFailure as error:
            raise McpFailure("auth" if error.kind == "auth" else "protocol") from None
        return self

    async def __aexit__(self, *args):
        if "Mcp-Session-Id" in self._headers:
            try:
                response = await self._request("DELETE")
                if response.status_code not in (200, 202, 204, 404, 405):
                    raise ConnectorFailure("protocol")
            except ConnectorFailure:
                pass

    async def _request(self, method: str, **kwargs):
        url = self.url
        for _ in range(5):
            response = await self.http.response(method, url, headers=self._headers, **kwargs)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            from urllib.parse import urljoin
            target = trusted_url(urljoin(url, response.headers.get("location", "")))
            if urlsplit(target).netloc != urlsplit(self.url).netloc:
                # Never forward the project key cross-origin, even within composio.dev.
                raise ConnectorFailure("untrusted_url")
            url = target
        raise ConnectorFailure("protocol")

    async def rpc(self, method: str, params: dict, *, notification: bool = False) -> dict:
        self._next += 1
        request = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            request["id"] = self._next
        response = await self._request("POST", json=request)
        if response.status_code in (401, 403):
            raise ConnectorFailure("auth")
        if response.status_code >= 300:
            raise ConnectorFailure("protocol")
        session = response.headers.get("mcp-session-id")
        if session:
            self._headers["Mcp-Session-Id"] = session
        if notification:
            return {}
        if len(response.content) > 2_000_000:
            raise ConnectorFailure("protocol")
        try:
            if response.headers.get("content-type", "").startswith("text/event-stream"):
                messages, lines = [], []
                for line in response.text.splitlines() + [""]:
                    if line.startswith("data:"):
                        lines.append(line[5:].lstrip())
                    elif not line and lines:
                        messages.append(json.loads("\n".join(lines)))
                        lines = []
                data = next(x for x in messages if isinstance(x, dict) and x.get("id") == self._next)
            else:
                data = response.json()
            if data.get("id") != self._next or data.get("jsonrpc") != "2.0" or not isinstance(data.get("result"), dict):
                raise ValueError()
            return data["result"]
        except (ValueError, StopIteration, AttributeError):
            raise ConnectorFailure("protocol") from None

    async def list_tools(self, cursor=None):
        try:
            data = await self.rpc("tools/list", {} if cursor is None else {"cursor": cursor})
            return SimpleNamespace(tools=[_tool(t) for t in data["tools"]], next_cursor=data.get("nextCursor"))
        except (ConnectorFailure, KeyError, TypeError):
            raise McpFailure("protocol") from None

    async def call_tool(self, name, arguments):
        if self.call_guard is not None and not self.call_guard(name, arguments):
            raise McpFailure("denied")
        try:
            return _result(await self.rpc("tools/call", {"name": name, "arguments": arguments}))
        except ConnectorFailure as error:
            raise McpFailure("auth" if error.kind == "auth" else "protocol") from None


class GoogleTools:
    def __init__(self, service):
        self.service = service

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def list_tools(self, cursor=None):
        from .google import TOOLS, READ_TOOLS
        return SimpleNamespace(tools=[_tool({"name": name, "description": description, "inputSchema": schema,
            "annotations": {"readOnlyHint": name in READ_TOOLS}}) for name, (description, schema) in TOOLS.items()], next_cursor=None)

    async def call_tool(self, name, arguments):
        try:
            data = await self.service.call_google(name, arguments)
        except ConnectorFailure as error:
            raise McpFailure("auth" if error.kind == "reconnect" else error.kind) from None
        return _result({"content": [{"type": "text", "text": json.dumps(data)}]})
