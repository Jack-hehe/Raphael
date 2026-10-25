"""Offline connector contracts. Every outbound request stops at an HTTP double."""

import json
import asyncio
import contextlib
import base64
import hashlib
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from raphael_core.connectors.composio import ComposioClient, trusted_url
from raphael_core.connectors.google import GoogleClient, scopes_for
from raphael_core.connectors.store import ConnectorStore
from raphael_core.connectors.http import ConnectorFailure
from raphael_core.integrations.vault import MemoryVault
from raphael_core.integrations.service import IntegrationsService
from raphael_core.connectors.service import ConnectorsService
from raphael_core.agent.plugins import tool_key
from raphael_core.agent.policy import Tier
from raphael_core.router import Router
from raphael_core.connectors.google import Loopback, api_request
from raphael_core.connectors.mcp import ManagedMcp
from raphael_core.agent.gate import ToolGate
from raphael_core.agent.policy import Action, Confirmations
from raphael_core.protocol import RpcError
from raphael_core.home import ConnectedCalendar


@pytest.mark.parametrize("state", ["missing", "inactive", "disabled", "scope", "active", "expired"])
async def test_home_calendar_uses_only_enabled_private_google_tool(tmp_path, conn, state, caplog):
    from datetime import datetime

    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.host == "www.googleapis.com"
        assert request.url.path.endswith("/calendars/primary/events")
        assert request.method == "GET"
        assert request.url.params["singleEvents"] == "true"
        assert request.url.params["orderBy"] == "startTime"
        assert request.url.params["maxResults"] == "24"
        assert request.url.params["timeMin"] == "2026-10-24T00:00:00+00:00"
        assert request.url.params["timeMax"] == "2026-10-31T00:00:00+00:00"
        if state == "expired":
            return httpx.Response(401, json={"error": "private-provider-message"})
        return httpx.Response(200, json={"timeZone": "Asia/Taipei", "items": [
            {"id": "timed", "summary": " Planning\nmeeting ", "start": {"dateTime": "2026-10-24T09:00:00+08:00"},
             "description": "private-description", "attendees": [{"email": "private@example.com"}]},
            {"id": "all-day", "start": {"date": "2026-10-25"}},
            {"id": "cancelled", "status": "cancelled", "start": {"date": "2026-10-25"}},
            {"id": "outside", "start": {"date": "2026-11-01"}},
            {"id": "invalid", "start": {"dateTime": "invalid"}},
        ]})

    router, service, hub, vault = setup_service(tmp_path, conn, respond)
    # A configured Composio key must not trigger an account refresh from Home.
    vault.set("connectors:composio:key", "private-key")
    if state != "missing":
        await router.call("connectors.google.configure", {"client_id": "desktop", "gmail": "read", "calendar": "read"})
        entry = service.store.connectors["google"]
        entry.status = "INACTIVE" if state == "inactive" else "ACTIVE"
        entry.granted_scopes = [] if state == "scope" else scopes_for("read", "read")
        if state == "disabled":
            entry.disabled_tools = ["calendar_list_events"]
        service.google._access, service.google._expires = "access-private", float("inf")
        await service.sync_google()
    stamp = lambda value: int(datetime.fromisoformat(value).timestamp() * 1000)
    events = await ConnectedCalendar(router, service)(stamp("2026-10-24T00:00:00+00:00"), stamp("2026-10-31T00:00:00+00:00"))
    assert len(requests) == (1 if state in ("active", "expired") else 0)
    if state == "active":
        assert [e["id"] for e in events] == ["event:timed", "event:all-day"]
        assert events[0]["title"] == "Planning meeting"
        assert events[1]["at_ms"] == stamp("2026-10-25T00:00:00+08:00")
        assert "private" not in json.dumps(events)
        assert all(e["kind"] == "event" for e in events)
    else:
        assert events == []
    if state == "expired":
        assert service.store.connectors["google"].status == "EXPIRED"
    assert "private-provider-message" not in caplog.text


def setup_service(tmp_path, conn, responder, **options):
    vault = MemoryVault()
    hub = IntegrationsService(conn, tmp_path, vault)
    router = Router()
    hub.register(router)
    service = ConnectorsService(tmp_path, vault, hub, transport=httpx.MockTransport(responder), **options)
    service.register(router)
    return router, service, hub, vault


class Platform:
    def __init__(self, statuses=None):
        self.requests = []
        self.statuses = list(statuses or ["INITIALIZING", "INITIATED", "ACTIVE"])
        self.version = 1

    def __call__(self, request):
        self.requests.append(request)
        path, method = request.url.path.removeprefix("/api/v3.1"), request.method
        if path == "/toolkits":
            return httpx.Response(200, json={"items": [{"slug": "gmail", "name": "Gmail", "no_auth": False,
                "composio_managed_auth_schemes": ["oauth2"], "auth_schemes": ["OAuth2"], "meta": {}}]})
        if path == "/toolkits/gmail":
            return httpx.Response(200, json={"slug": "gmail", "no_auth": False,
                                           "composio_managed_auth_schemes": ["oauth2"]})
        if path == "/tools":
            return httpx.Response(200, json={"items": [
                {"slug": "GMAIL_FETCH_EMAILS", "tags": ["readOnlyHint"]},
                {"slug": "GMAIL_SEND_EMAIL", "tags": ["createHint"]},
                {"slug": "GMAIL_DELETE_MESSAGE", "tags": ["destructiveHint"]}]})
        if path == "/tool_router/session" or (path == "/tool_router/session/trs_test" and method == "GET"):
            return httpx.Response(201 if method == "POST" else 200, json={"session_id": "trs_test", "config_version": self.version,
                "mcp": {"url": "https://app.composio.dev/mcp"}})
        if path == "/tool_router/session/trs_test" and method == "PATCH":
            self.version += 1
            return httpx.Response(200, json={"config_version": self.version})
        if path.endswith("/link"):
            return httpx.Response(201, json={"connected_account_id": "ca_test", "redirect_url": "https://connect.composio.dev/link/lt_private"})
        if path == "/connected_accounts/ca_test":
            if method == "DELETE":
                return httpx.Response(200, json={"success": True})
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"id": "ca_test", "status": status, "toolkit": {"slug": "gmail"},
                "state": {"val": {"access_token": "private", "displayName": "private@example.com"}}})
        if path == "/connected_accounts":
            return httpx.Response(200, json={"items": [{"id": "ca_test", "status": self.statuses[-1], "toolkit": {"slug": "gmail"},
                "state": {"val": {"refresh_token": "secret"}}}]})
        if path == "/tool_router/session/trs_test/toolkits":
            return httpx.Response(200, json={"items": []})
        if request.url.host == "app.composio.dev":
            if method in ("GET", "DELETE"):
                return httpx.Response(405)
            body = json.loads(request.content)
            rpc = body.get("method")
            if rpc == "notifications/initialized":
                return httpx.Response(202)
            if rpc == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "test", "version": "1"}}
            elif rpc == "tools/list":
                result = {"tools": [{"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "inputSchema": {"type": "object"}},
                                    {"name": "COMPOSIO_REMOTE_BASH_TOOL", "inputSchema": {"type": "object"}}]}
            else:
                result = {"content": [{"type": "text", "text": "mail-private"}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        raise AssertionError((method, str(request.url)))


async def no_wait(seconds):
    pass


async def test_connect_poll_enable_managed_mcp_and_local_enforcement(tmp_path, conn):
    fake, opened = Platform(), []

    async def browser(url):
        opened.append(url)

    router, service, hub, vault = setup_service(tmp_path, conn, fake, open_browser=browser, sleep=no_wait)
    assert await router.call("connectors.set_key", {"key": "ak_private"}) == {"configured": True}
    result = await router.call("connectors.connect", {"toolkit": "gmail"})
    assert result["connector"]["status"] == "ACTIVE"
    assert opened == ["https://connect.composio.dev/link/lt_private"]
    assert vault.get("connectors:composio:key") == "ak_private"
    record = hub._config.get("connectors_composio")
    assert record.managed_by == "composio"
    assert record.header_refs == {"x-api-key": "connectors:composio:key"}
    assert hub.tier_of(tool_key(record.id, "COMPOSIO_MULTI_EXECUTE_TOOL")) == Tier.T2
    assert not any(x.name == "COMPOSIO_REMOTE_BASH_TOOL" for x in hub.offered())
    args = {"tools": [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {}}]}
    assert not (await hub.call(tool_key(record.id, "COMPOSIO_MULTI_EXECUTE_TOOL"), args, approver="auto")).ok
    assert (await hub.call(tool_key(record.id, "COMPOSIO_MULTI_EXECUTE_TOOL"), args, approver="user")).ok
    await router.call("connectors.set_tools", {"id": "composio:gmail", "disabled_tools": ["GMAIL_FETCH_EMAILS"]})
    before = len(fake.requests)
    assert not (await hub.call(tool_key(record.id, "COMPOSIO_MULTI_EXECUTE_TOOL"), args, approver="user")).ok
    assert len(fake.requests) == before
    for path in (tmp_path / "connectors.json", tmp_path / "integrations.json"):
        assert not any(secret in path.read_text() for secret in ("ak_private", "lt_private", "mail-private", "refresh_token", "private@example.com"))
    await router.call("connectors.disconnect", {"id": "composio:gmail"})
    assert not hub.offered()
    assert any(r.method == "DELETE" and r.url.params.get("revoke_on_delete") == "true" for r in fake.requests)


@pytest.mark.parametrize("terminal", ["FAILED", "EXPIRED", "INACTIVE", "REVOKED"])
async def test_terminal_connect_never_enables(tmp_path, conn, terminal):
    fake = Platform([terminal])

    async def browser(url):
        pass

    router, service, hub, vault = setup_service(tmp_path, conn, fake, open_browser=browser, sleep=no_wait)
    await router.call("connectors.set_key", {"key": "ak_private"})
    result = await router.call("connectors.connect", {"toolkit": "gmail"})
    assert result["connector"]["status"] == terminal
    assert not hub.offered()


async def test_google_provider_draft_policy_scope_filter_and_approval(tmp_path, conn):
    def respond(request):
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "private", "expires_in": 3600})
        return httpx.Response(200, json={"id": "message"})

    router, service, hub, vault = setup_service(tmp_path, conn, respond)
    await router.call("connectors.google.configure", {"client_id": "desktop", "gmail": "write", "calendar": "read", "draft_only": True})
    connector = service.store.connectors["google"]
    connector.status = "ACTIVE"
    connector.granted_scopes = scopes_for("write", "read")
    vault.set("connectors:google:refresh_token", "refresh-private")
    await service.sync_google()
    names = {t.name for t in hub.offered()}
    assert "gmail_create_draft" in names
    assert "gmail_send" not in names and "calendar_create_event" not in names
    args = {"to": "test@example.com", "subject": "Test", "body": "Private"}
    key = tool_key("connectors_google", "gmail_send")
    assert not (await hub.call(key, args, approver="user")).ok
    connector.draft_only = False
    await service.sync_google()
    assert hub.tier_of(key) == Tier.T2
    assert not (await hub.call(key, args, approver="auto")).ok
    assert (await hub.call(key, args, approver="user")).ok


def test_store_has_stable_identity_and_no_secrets(tmp_path):
    store = ConnectorStore(tmp_path)
    first = store.user_id
    assert first.startswith("raphael_")
    assert ConnectorStore(tmp_path).user_id == first
    assert store.connectors == {}


@pytest.mark.parametrize("url", ["http://connect.composio.dev/link/x", "https://composio.dev.evil/x",
                                   "https://evil/x", "https://a@composio.dev/x", "https://composio.dev:444/x"])
def test_untrusted_redirects_are_rejected(url):
    with pytest.raises(ConnectorFailure):
        trusted_url(url)


async def test_composio_pagination_key_and_error_redaction(caplog):
    caplog.set_level(logging.DEBUG)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["x-api-key"] == "ak_private"
        if request.url.path.endswith("/tools"):
            return httpx.Response(403, json={"error": {"message": "ak_private lt_secret Bearer secret refresh_token"}})
        cursor = request.url.params.get("cursor")
        return httpx.Response(200, json={"items": [{"slug": "gmail" if not cursor else "slack"}],
                                         "next_cursor": "next" if not cursor else None})

    client = ComposioClient(lambda: "ak_private", transport=httpx.MockTransport(respond))
    assert [x["slug"] for x in await client.pages("/toolkits", {"search": "mail"})] == ["gmail", "slack"]
    with pytest.raises(ConnectorFailure) as exc:
        await client.request("GET", "/tools")
    assert str(exc.value) == "auth"
    assert requests[0].url.params["search"] == "mail"
    assert not any(x in caplog.text for x in ("ak_", "lt_", "Bearer ", "refresh_token"))


async def test_google_refresh_preserves_token_and_optional_secret():
    vault = MemoryVault()
    vault.set("connectors:google:client_id", "desktop-client")
    vault.set("connectors:google:refresh_token", "refresh-private")
    forms = []

    def respond(request):
        forms.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "access-private", "expires_in": 3600,
                                         "scope": " ".join(scopes_for("write", "read"))})

    client = GoogleClient(vault, transport=httpx.MockTransport(respond))
    assert await client.access_token() == "access-private"
    assert await client.access_token() == "access-private"
    assert len(forms) == 1
    assert "client_secret" not in forms[0]
    assert vault.get("connectors:google:refresh_token") == "refresh-private"
    assert "access-private" not in repr(client)


async def test_google_invalid_grant_is_reconnect():
    vault = MemoryVault()
    vault.set("connectors:google:client_id", "desktop-client")
    vault.set("connectors:google:refresh_token", "refresh-private")
    client = GoogleClient(vault, transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={"error": "invalid_grant"})))
    with pytest.raises(ConnectorFailure, match="reconnect"):
        await client.access_token()


async def connected(tmp_path, conn, fake=None, **options):
    async def browser(url):
        pass

    fake = fake or Platform(["ACTIVE"])
    router, service, hub, vault = setup_service(tmp_path, conn, fake, open_browser=browser, sleep=no_wait, **options)
    await router.call("connectors.set_key", {"key": "ak_private"})
    assert (await router.call("connectors.connect", {"toolkit": "gmail"}))["connected"]
    return router, service, hub, vault, fake


async def test_timeout_uses_ten_minute_deadline_and_never_enables(tmp_path, conn):
    clock = [0]

    async def wait(seconds):
        clock[0] += seconds

    async def browser(url):
        pass

    router, service, hub, vault = setup_service(tmp_path, conn, Platform(["INITIATED"]),
        open_browser=browser, sleep=wait, now=lambda: clock[0])
    await router.call("connectors.set_key", {"key": "ak_private"})
    result = await router.call("connectors.connect", {"toolkit": "gmail"})
    assert result["error"] == "timeout"
    assert clock[0] == 600
    assert result["connector"]["status"] == "EXPIRED"
    assert not hub.offered()


async def test_session_reused_and_recreated_with_same_install_identity(tmp_path, conn):
    router, service, hub, vault, fake = await connected(tmp_path, conn)
    identity = service.store.user_id
    await service._session()
    assert sum(r.method == "POST" and r.url.path.endswith("/session") for r in fake.requests) == 1
    original = service.composio.http.transport

    def missing(request):
        if request.method == "GET" and request.url.path.endswith("/session/trs_test"):
            fake.requests.append(request)
            return httpx.Response(404)
        return fake(request)

    service.composio.http.transport = httpx.MockTransport(missing)
    await service._session()
    creates = [json.loads(r.content) for r in fake.requests if r.method == "POST" and r.url.path.endswith("/session")]
    assert len(creates) == 2
    assert {body["user_id"] for body in creates} == {identity}
    assert creates[-1]["workbench"] == {"enable": False}
    assert creates[-1]["manage_connections"] == {"enable": False}


async def test_conflict_keeps_local_restrictions_and_entire_tools_map(tmp_path, conn):
    router, service, hub, vault, fake = await connected(tmp_path, conn)
    from raphael_core.connectors.store import Connector
    service.store.connectors["composio:slack"] = Connector("composio:slack", "composio", status="ACTIVE", disabled_tools=["SLACK_SEND"])

    def conflict(request):
        if request.method == "PATCH":
            body = json.loads(request.content)
            assert body["tools"]["slack"] == {"disable": ["SLACK_SEND"]}
            assert "expected_config_version" in body
            return httpx.Response(409, json={"error": {"message": "secret"}})
        return fake(request)

    service.composio.http.transport = httpx.MockTransport(conflict)
    result = await router.call("connectors.set_tools", {"id": "composio:gmail", "disabled_tools": ["GMAIL_FETCH_EMAILS"]})
    assert result["error"] == "conflict"
    assert not service.allow_call("connectors_composio", "COMPOSIO_MULTI_EXECUTE_TOOL", {
        "tools": [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {}}]}, "user")


@pytest.mark.parametrize("arguments", [{}, {"tools": []}, {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL"}]},
    {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL", "arguments": {}, "code": "bad"}]},
    {"tools": [{"tool_slug": "NOT_ALLOWED", "arguments": {}}]},
    {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL", "arguments": {}}], "code": "bad"}])
async def test_unknown_execution_envelopes_fail_closed(tmp_path, conn, arguments):
    router, service, hub, vault, fake = await connected(tmp_path, conn)
    before = len(fake.requests)
    outcome = await hub.call(tool_key("connectors_composio", "COMPOSIO_MULTI_EXECUTE_TOOL"), arguments, approver="user")
    assert not outcome.ok and len(fake.requests) == before


async def test_key_clear_and_managed_mutation_guards(tmp_path, conn):
    router, service, hub, vault, fake = await connected(tmp_path, conn)
    for method, args in [("integrations.update", {}), ("integrations.set_enabled", {"enabled": False}),
                         ("integrations.disconnect", {}), ("integrations.set_tool_enabled", {"tool": "COMPOSIO_MULTI_EXECUTE_TOOL", "enabled": False})]:
        with pytest.raises(RpcError):
            await router.call(method, {"id": "connectors_composio", **args})
    await router.call("connectors.set_key", {"key": ""})
    assert not hub.offered()
    assert vault.get("connectors:composio:key") is None
    assert hub._config.get("connectors_composio") is None


@pytest.mark.parametrize("sse", [False, True])
async def test_mcp_json_sse_accept_headers_and_405_delete(sse):
    methods = []

    def respond(request):
        methods.append(request.method)
        assert request.headers["accept"] == "application/json, text/event-stream"
        assert request.headers["x-api-key"] == "ak_private"
        if request.method == "DELETE":
            return httpx.Response(405)
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26"}
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "COMPOSIO_SEARCH_TOOLS", "inputSchema": {}}]}
        else:
            result = {"content": [{"type": "text", "text": "private-mail"}]}
        data = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        if sse:
            return httpx.Response(200, text="event: message\ndata: " + json.dumps(data) + "\n\n",
                headers={"content-type": "text/event-stream", "mcp-session-id": "session"})
        return httpx.Response(200, json=data, headers={"mcp-session-id": "session"})

    async with ManagedMcp("https://app.composio.dev/mcp", {"x-api-key": "ak_private"}, transport=httpx.MockTransport(respond)) as client:
        assert (await client.list_tools()).tools[0].name == "COMPOSIO_SEARCH_TOOLS"
        assert (await client.call_tool("COMPOSIO_SEARCH_TOOLS", {})).content[0].text == "private-mail"
    assert methods[-1] == "DELETE"


async def test_mcp_never_forwards_key_on_cross_origin_redirect():
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://other.composio.dev/mcp"})

    with pytest.raises(Exception):
        async with ManagedMcp("https://app.composio.dev/mcp", {"x-api-key": "ak_private"}, transport=httpx.MockTransport(respond)):
            pass
    assert seen == ["https://app.composio.dev/mcp"]


@pytest.mark.parametrize("with_secret", [False, True])
async def test_google_real_loopback_pkce_exchange_and_partial_consent(with_secret):
    vault = MemoryVault()
    vault.set("connectors:google:client_id", "desktop")
    if with_secret:
        vault.set("connectors:google:client_secret", "secret-private")
    authorization = {}
    forms = []

    async def browser(url):
        from urllib.parse import urlsplit
        authorization.update(parse_qs(urlsplit(url).query))
        async with httpx.AsyncClient(trust_env=False) as browser_http:
            response = await browser_http.get(authorization["redirect_uri"][0], params={"code": "code-private", "state": authorization["state"][0]})
            assert response.status_code == 200

    def token(request):
        form = parse_qs(request.content.decode())
        forms.append(form)
        verifier = form["code_verifier"][0]
        assert 43 <= len(verifier) <= 128
        assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") == authorization["code_challenge"][0]
        assert form["redirect_uri"] == authorization["redirect_uri"]
        assert ("client_secret" in form) is with_secret
        return httpx.Response(200, json={"access_token": "access-private", "refresh_token": "refresh-private",
            "expires_in": 3600, "scope": scopes_for("read", "read")[0], "token_type": "Bearer"})

    client = GoogleClient(vault, transport=httpx.MockTransport(token))
    scopes = await client.connect(scopes_for("write", "write"), browser)
    assert scopes == scopes_for("read", "read")[:1]
    assert vault.get("connectors:google:refresh_token") == "refresh-private"
    assert not any("access" in n for n in vault.names())
    from raphael_core.connectors.google import permitted
    assert permitted("gmail_search", scopes, False)
    assert not permitted("gmail_send", scopes, False)
    assert not permitted("calendar_list_events", scopes, False)


async def test_loopback_state_mismatch_and_timeout_close_listener():
    async with Loopback("correct", timeout=0.1) as listener:
        async with httpx.AsyncClient(trust_env=False) as browser:
            with contextlib.suppress(httpx.RemoteProtocolError):
                await browser.get(listener.redirect_uri, params={"code": "private", "state": "wrong"})
        with pytest.raises(ConnectorFailure, match="state"):
            await listener.wait()
    async with Loopback("correct", timeout=0.01) as listener:
        with pytest.raises(ConnectorFailure, match="timeout"):
            await listener.wait()
    assert not listener.server.is_serving()


async def test_google_revoke_deletes_all_credentials_without_logging(caplog):
    vault = MemoryVault()
    for name in ("client_id", "client_secret", "refresh_token"):
        vault.set("connectors:google:" + name, "private-" + name)

    def respond(request):
        assert request.method == "POST"
        assert parse_qs(request.content.decode())["token"] == ["private-refresh_token"]
        return httpx.Response(200)

    caplog.set_level(logging.DEBUG)
    await GoogleClient(vault, transport=httpx.MockTransport(respond)).revoke()
    assert not vault.names()
    assert "refresh_token" not in caplog.text and "Bearer " not in caplog.text


async def test_google_send_through_real_gate_requires_one_approval(tmp_path, conn):
    router, service, hub, vault = setup_service(tmp_path, conn, lambda request: httpx.Response(200, json={"id": "message"}))
    await router.call("connectors.google.configure", {"client_id": "desktop"})
    entry = service.store.connectors["google"]
    entry.status, entry.granted_scopes = "ACTIVE", scopes_for("write", "write")
    service.google._access, service.google._expires = "access-private", float("inf")
    await service.sync_google()
    gate = ToolGate(hub)
    args = {"to": "test@example.com", "subject": "Test", "body": "Private"}
    key = tool_key("connectors_google", "gmail_send")
    assert not (await gate.call_plugin(key, args)).ok
    confirmations = Confirmations()
    action = Action(key, args)
    question = confirmations.open("session", 1, action, Tier.T2, "en")
    approval = confirmations.resolve(question.id, True).approval
    assert (await gate.call_plugin(key, args, action=action, approval=approval)).ok
    assert not (await gate.call_plugin(key, args, action=action, approval=approval)).ok
    audit = await router.call("integrations.audit", {})
    assert "Private" not in json.dumps(audit)


@pytest.mark.parametrize("tool,args,path", [
    ("gmail_search", {"q": "hello"}, "/messages"),
    ("gmail_get_message", {"id": "x/y"}, "/messages/x%2Fy"),
    ("gmail_create_draft", {"to": "a@b.com", "subject": "Hi", "body": "Body"}, "/drafts"),
    ("gmail_send", {"to": "a@b.com", "subject": "Hi", "body": "Body"}, "/messages/send"),
    ("gmail_modify_labels", {"id": "one", "addLabelIds": ["STARRED"]}, "/messages/one/modify"),
    ("calendar_list_events", {}, "/primary/events"),
    ("calendar_create_event", {"summary": "Meet", "start": {"date": "2026-10-25"}, "end": {"date": "2026-10-26"}}, "/primary/events"),
])
def test_google_api_routes(tool, args, path):
    method, url, options = api_request(tool, args)
    assert url.endswith(path)
    assert method == ("GET" if tool in {"gmail_search", "gmail_get_message", "calendar_list_events"} else "POST")


async def test_disabled_during_discovery_is_blocked_at_mcp_call_boundary(tmp_path, conn):
    router, service, hub, vault, fake = await connected(tmp_path, conn)

    def changing_policy(request):
        if request.url.host == "app.composio.dev" and request.method == "POST":
            body = json.loads(request.content)
            if body.get("method") == "tools/list":
                service.store.connectors["composio:gmail"].disabled_tools.append("GMAIL_SEND_EMAIL")
        return fake(request)

    service._transport = httpx.MockTransport(changing_policy)
    args = {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL", "arguments": {}}]}
    result = await hub.call(tool_key("connectors_composio", "COMPOSIO_MULTI_EXECUTE_TOOL"), args, approver="user")
    assert not result.ok
    assert not any(r.method == "POST" and r.url.host == "app.composio.dev" and
                   json.loads(r.content).get("method") == "tools/call" for r in fake.requests)


async def test_all_tools_default_on_and_new_tools_preserve_owner_choices(tmp_path, conn):
    router, service, hub, vault, fake = await connected(tmp_path, conn)
    tools = await router.call("connectors.tools", {"id": "composio:gmail"})
    assert all(t["enabled"] for t in tools["items"])
    await router.call("connectors.set_tools", {"id": "composio:gmail", "disabled_tools": ["GMAIL_FETCH_EMAILS"]})
    original = fake.__call__

    def added_tool(request):
        result = original(request)
        if request.url.path.endswith("/tools"):
            return httpx.Response(200, json={"items": result.json()["items"] + [
                {"slug": "GMAIL_NEW_ACTION", "tags": ["destructiveHint"]}]})
        return result

    service.composio.http.transport = httpx.MockTransport(added_tool)
    service.composio._cache.clear()
    tools = await router.call("connectors.tools", {"id": "composio:gmail"})
    assert next(t for t in tools["items"] if t["slug"] == "GMAIL_DELETE_MESSAGE")["enabled"]
    assert next(t for t in tools["items"] if t["slug"] == "GMAIL_NEW_ACTION")["enabled"]
    assert not next(t for t in tools["items"] if t["slug"] == "GMAIL_FETCH_EMAILS")["enabled"]
    assert service.allow_call("connectors_composio", "COMPOSIO_MULTI_EXECUTE_TOOL", {
        "tools": [{"tool_slug": "GMAIL_DELETE_MESSAGE", "arguments": {}}]}, "user")


@pytest.mark.parametrize("kind", ["composio", "google"])
async def test_service_switch_preserves_tools_and_updates_offers_without_restart(tmp_path, conn, kind):
    from raphael_core.connectors.store import Connector
    from raphael_core.agent.speaker import Speaker
    from raphael_core.agent.mcp_server import RaphaelMcp
    from raphael_core.background.snapshot import take_snapshot

    router, service, hub, vault, fake = await connected(tmp_path, conn)
    ident, server = "composio:gmail", "connectors_composio"
    if kind == "google":
        ident, server = "google", "connectors_google"
        service.store.connectors[ident] = Connector(ident, "google", status="ACTIVE", granted_scopes=scopes_for("write", "write"))
        await service.sync_google()
    names = [t.name for t in hub.offered() if t.key.startswith(tool_key(server, ""))]
    assert names

    async def assert_brain_offers(available):
        for speaker in (Speaker(), Speaker(id=7, tools=frozenset())):
            # API and CLI receive the same fresh production snapshot on each turn.
            snapshot = take_snapshot(hub, speaker=speaker, interactive=True, max_tier=Tier.T3)
            refs = [ref for ref in snapshot.refs.values() if ref.key.startswith(tool_key(server, ""))]
            assert bool(refs) is available
            bridge = RaphaelMcp(lambda: snapshot.definitions, None)
            listed = await bridge._dispatch({"method": "tools/list"})
            api_names = {item["function"]["name"] for item in snapshot.definitions}
            assert {item["name"] for item in listed["result"]["tools"]} == api_names

    await assert_brain_offers(True)
    await router.call("connectors.set_tools", {"id": ident, "disabled_tools": [], "enabled": False})
    assert not [t for t in hub.offered() if t.key.startswith(tool_key(server, ""))]
    assert ConnectorStore(tmp_path).connectors[ident].enabled is False
    await assert_brain_offers(False)
    await router.call("connectors.set_tools", {"id": ident, "enabled": True})
    assert [t.name for t in hub.offered() if t.key.startswith(tool_key(server, ""))] == names
    await assert_brain_offers(True)


async def test_failed_remote_switch_never_reenables_local_service(tmp_path, conn):
    router, service, hub, vault, fake = await connected(tmp_path, conn)

    def unavailable(request):
        if request.url.host != "app.composio.dev":
            return httpx.Response(503, json={"message": "private-provider-text"})
        return fake(request)

    service.composio.http.transport = httpx.MockTransport(unavailable)
    result = await router.call("connectors.set_tools", {"id": "composio:gmail", "enabled": False})
    assert result["ok"] is False
    assert not hub.offered()
    assert ConnectorStore(tmp_path).connectors["composio:gmail"].enabled is False


async def test_catalog_projection_cache_and_missing_managed_oauth(tmp_path, conn):
    fake = Platform()

    def response(request):
        if request.url.path.endswith("/categories"):
            return httpx.Response(200, json={"items": [{"id": "productivity", "name": "Productivity"}]})
        if request.url.path.endswith("/xero"):
            return httpx.Response(200, json={"slug": "xero", "no_auth": False, "composio_managed_auth_schemes": []})
        return fake(request)

    router, service, hub, vault = setup_service(tmp_path, conn, response)
    await router.call("connectors.set_key", {"key": "ak_private"})
    catalog = await router.call("connectors.catalog", {"search": "mail", "category": "productivity", "limit": 50})
    assert catalog["stores_tool_payloads"] is True
    assert catalog["items"][0]["can_connect"] is True
    assert catalog["items"][0]["auth_schemes"] == ["oauth2"]
    before = len(fake.requests)
    assert await router.call("connectors.catalog", {"search": "mail", "category": "productivity", "limit": 50}) == catalog
    assert len(fake.requests) == before
    assert (await router.call("connectors.categories"))["items"][0]["id"] == "productivity"
    assert (await router.call("connectors.connect", {"toolkit": "xero"}))["error"] == "needs_own_oauth_app"


async def test_pagination_stalls_fail_instead_of_returning_partial_catalog():
    client = ComposioClient(lambda: "ak_private", transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={"items": [], "next_cursor": "repeated", "current_page": 1})))
    with pytest.raises(ConnectorFailure, match="pagination"):
        await client.pages("/toolkits")


async def test_notifications_and_status_projection_never_return_account_credentials(tmp_path, conn):
    router, service, hub, vault, fake = await connected(tmp_path, conn)
    notifications = []
    router.set_notifier(lambda name, params: notifications.append((name, params)))
    result = await router.call("connectors.status")
    assert result["connectors"][0]["status"] == "ACTIVE"
    assert result["connectors"][0]["accounts"] == [{"id": "ca_test", "status": "ACTIVE"}]
    assert "refresh_token" not in json.dumps(result)
    assert ("connectors.changed", {}) in notifications
    assert all(params == {} for name, params in notifications if name == "connectors.changed")
    listing = await router.call("toolsets.list")
    assert listing["groups"][0]["tools_locked"] is True
    assert listing["groups"][0]["tools"] == []


async def test_google_auth_failure_disables_provider_and_has_testing_hint(tmp_path, conn, caplog):
    caplog.set_level(logging.DEBUG)
    router, service, hub, vault = setup_service(tmp_path, conn, lambda request:
        httpx.Response(400, json={"error": "invalid_grant", "error_description": "refresh_token Bearer private lt_private ak_private"}))
    setup = await router.call("connectors.google.configure", {"client_id": "desktop"})
    assert setup["testing_refresh_expires_days"] == 7
    assert setup["revocation_affects_whole_project"] is True
    entry = service.store.connectors["google"]
    entry.status, entry.granted_scopes = "ACTIVE", scopes_for("write", "write")
    vault.set("connectors:google:refresh_token", "refresh-private")
    await service.sync_google()
    result = await hub.call(tool_key("connectors_google", "gmail_search"), {}, approver="auto")
    assert not result.ok and entry.status == "EXPIRED"
    assert not hub.offered()
    assert not any(x in caplog.text for x in ("ak_", "Bearer ", "refresh_token", "lt_"))


async def test_google_refresh_scope_reduction_blocks_send_before_api_call(tmp_path, conn):
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"access_token": "access-private", "expires_in": 3600,
            "scope": " ".join(scopes_for("read", "read"))})

    router, service, hub, vault = setup_service(tmp_path, conn, response)
    await router.call("connectors.google.configure", {"client_id": "desktop"})
    entry = service.store.connectors["google"]
    entry.status, entry.granted_scopes = "ACTIVE", scopes_for("write", "write")
    vault.set("connectors:google:refresh_token", "refresh-private")
    await service.sync_google()
    result = await hub.call(tool_key("connectors_google", "gmail_send"),
        {"to": "a@b.com", "subject": "Private", "body": "Private mail"}, approver="user")
    assert not result.ok
    assert len(requests) == 1 and requests[0].url.path == "/token"
    assert "gmail_send" not in {t.name for t in hub.offered()}


async def test_google_revocation_failure_can_be_retried(tmp_path, conn):
    fail = [True]

    def response(request):
        return httpx.Response(503 if fail[0] else 200)

    router, service, hub, vault = setup_service(tmp_path, conn, response)
    await router.call("connectors.google.configure", {"client_id": "desktop", "client_secret": "secret"})
    vault.set("connectors:google:refresh_token", "refresh-private")
    result = await router.call("connectors.disconnect", {"id": "google"})
    assert result["error"] == "revocation_failed"
    assert vault.get("connectors:google:refresh_token") == "refresh-private"
    assert not hub.offered()
    fail[0] = False
    assert (await router.call("connectors.disconnect", {"id": "google"}))["disconnected"]
    assert not vault.names()


async def test_rpc_exception_messages_are_value_free(tmp_path, conn, caplog):
    async def browser(url):
        raise RuntimeError("ak_private lt_private Bearer secret refresh_token")

    router, service, hub, vault = setup_service(tmp_path, conn, Platform(), open_browser=browser)
    await router.call("connectors.set_key", {"key": "ak_private"})
    with pytest.raises(RpcError) as failure:
        await router.call("connectors.connect", {"toolkit": "gmail"})
    assert str(failure.value.message) == "Connector operation failed"
    assert not any(x in caplog.text for x in ("ak_", "Bearer ", "refresh_token", "lt_"))


@pytest.mark.parametrize("tool,args", [
    ("gmail_search", {"q": "private-mail"}), ("gmail_get_message", {"id": "private"}),
    ("gmail_create_draft", {"to": "a@b.com", "subject": "Private subject", "body": "private-mail"}),
    ("gmail_send", {"to": "a@b.com", "subject": "Private subject", "body": "private-mail"}),
    ("gmail_modify_labels", {"id": "private", "addLabelIds": ["STARRED"]}),
    ("calendar_list_events", {"q": "private-event"}),
    ("calendar_create_event", {"summary": "private-event", "start": {"date": "2026-10-25"}, "end": {"date": "2026-10-26"}}),
])
async def test_google_api_http_boundary_and_private_logs(tool, args, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []

    def response(request):
        assert request.headers["authorization"] == "Bearer access-private"
        assert "access_token" not in str(request.url)
        seen.append(request)
        return httpx.Response(200, json={"id": "test", "body": "private-mail private-event"})

    client = GoogleClient(MemoryVault(), transport=httpx.MockTransport(response))
    client._access, client._expires = "access-private", float("inf")
    assert (await client.call(tool, args))["id"] == "test"
    assert len(seen) == 1
    assert not any(x in caplog.text for x in ("Bearer ", "private-mail", "private-event", "Private subject"))
