import httpx

from raphael_core.agent.mcp_server import RaphaelMcp
from raphael_core.worker.loop import ToolOutcome


async def test_http_mcp_requires_bearer_and_runs_only_offered_tools():
    calls = []

    async def execute(identifier, name, args):
        calls.append((name, args))
        return ToolOutcome(False, "approval_required", content="Blocked by the same gate")

    tools = [{"type": "function", "function": {"name": "routine_delete", "description": "Delete routine",
                                               "parameters": {"type": "object"}}}]
    async with RaphaelMcp(lambda: tools, execute) as server:
        async with httpx.AsyncClient() as client:
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "routine_delete", "arguments": {"id": 1}}}
            assert (await client.post(server.url, json=body)).status_code == 401
            assert not calls
            headers = {"Authorization": "Bearer " + server.token}
            response = await client.post(server.url, headers=headers, json=body)
            assert response.json()["result"]["isError"] is True
            assert calls == [("routine_delete", {"id": 1})]
            body["params"]["name"] = "unoffered"
            assert (await client.post(server.url, headers=headers, json=body)).json().get("error")
            assert len(calls) == 1
    assert server.closed


async def test_official_mcp_client_can_initialize_list_and_call():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def execute(identifier, name, args):
        return ToolOutcome(True, content="hello")

    async with RaphaelMcp(lambda: [{"type": "function", "function": {"name": "hello", "description": "Hello", "parameters": {"type": "object"}}}], execute) as server:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + server.token}) as http:
            async with streamable_http_client(server.url, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1]) as client:
                    await client.initialize()
                    assert (await client.list_tools()).tools[0].name == "hello"
                    assert (await client.call_tool("hello", {})).content[0].text == "hello"
