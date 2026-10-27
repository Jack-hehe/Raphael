"""Per-turn, authenticated, stateless Streamable HTTP MCP (JSON response mode).

POST supports the MCP lifecycle and tools; an optional GET SSE stream is deliberately
not offered. No browser origins, remote bindings, credentials in URLs or access logs.
"""

import asyncio
import hmac
import json
import secrets

from ..worker.guards import MAX_ARGUMENT_CHARS, canonical

MAX_BODY = 1024 * 1024
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")


class RaphaelMcp:
    def __init__(self, definitions, execute):
        self.definitions, self.execute = definitions, execute
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.url = ""
        self.closed = False
        self._tasks = set()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._connection, "127.0.0.1", 0, limit=65536)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        self.server.close()
        # A call waiting at an owner's question is cancelled first: since Python 3.12 wait_closed() waits for every open
        # connection, so a call left waiting would hold the end of the turn for as long as the question was allowed to live.
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.server.wait_closed()
        self.token = ""

    async def _connection(self, reader, writer):
        task = asyncio.current_task()
        if len(self._tasks) >= 64:
            writer.close()
            await writer.wait_closed()
            return
        self._tasks.add(task)
        try:
            async with asyncio.timeout(15):
                line = (await reader.readline()).decode("ascii").strip().split()
                if len(line) != 3:
                    return
                method, path, _ = line
                headers = {}
                total = 0
                while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                    total += len(line)
                    if total > 16384:
                        return
                    key, value = line.decode("latin-1").split(":", 1)
                    if key.lower() in headers:
                        return
                    headers[key.lower()] = value.strip()
                if not hmac.compare_digest(headers.get("authorization", "").encode("utf-8"), ("Bearer " + self.token).encode("ascii")):
                    await self._respond(writer, 401, {})
                    return
                if headers.get("host") != self.url.split("/")[2] or headers.get("origin"):
                    await self._respond(writer, 403, {})
                    return
                if path != "/mcp":
                    await self._respond(writer, 404, {})
                    return
                if method != "POST":
                    await self._respond(writer, 405, {})
                    return
                if "application/json" not in headers.get("content-type", "") or "transfer-encoding" in headers:
                    await self._respond(writer, 415, {})
                    return
                size = int(headers.get("content-length", "0"))
                if not 0 < size <= MAX_BODY:
                    await self._respond(writer, 413, {})
                    return
                packet = json.loads(await reader.readexactly(size))
            if not isinstance(packet, dict) or packet.get("jsonrpc") != "2.0":
                await self._respond(writer, 400, {})
                return
            if "id" in packet and (type(packet["id"]) not in (int, str) or not isinstance(packet.get("method"), str)):
                await self._respond(writer, 400, {})
                return
            if "id" not in packet:
                await self._respond(writer, 202, None)
                return
            # A tool may pause at an owner approval card. The run itself bounds its lifetime.
            result = await self._dispatch(packet)
            await self._respond(writer, 200, {"jsonrpc": "2.0", "id": packet["id"], **result})
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, OSError, TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            finally:
                self._tasks.discard(task)

    async def _dispatch(self, packet):
        method = packet.get("method")
        params = packet.get("params", {})
        if not isinstance(params, dict):
            return {"error": {"code": -32602, "message": "Invalid arguments"}}
        if method == "initialize":
            requested = params.get("protocolVersion")
            return {"result": {"protocolVersion": requested if requested in VERSIONS else VERSIONS[0],
                    "capabilities": {"tools": {}}, "serverInfo": {"name": "raphael", "version": "0.2.0"}}}
        if method == "ping":
            return {"result": {}}
        tools = {definition["function"]["name"]: definition["function"] for definition in self.definitions()}
        if method == "tools/list":
            return {"result": {"tools": [{"name": item["name"], "description": item.get("description", ""),
                    "inputSchema": item.get("parameters", {"type": "object"})} for item in tools.values()]}}
        if method == "tools/call" and isinstance(params.get("name"), str) and params["name"] in tools:
            args = params.get("arguments", {})
            if not isinstance(args, dict) or len(canonical(args)) > MAX_ARGUMENT_CHARS:
                return {"error": {"code": -32602, "message": "Invalid arguments"}}
            try:
                result = await self.execute(str(packet["id"]), params["name"], args)
            except Exception:
                return {"error": {"code": -32603, "message": "Tool execution failed"}}
            return {"result": {"content": [{"type": "text", "text": result.content or result.kind or ""}], "isError": not result.ok}}
        return {"error": {"code": -32601, "message": "Unknown method or tool"}}

    @staticmethod
    async def _respond(writer, status, body):
        payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        writer.write(f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n".encode() + payload)
        await writer.drain()
