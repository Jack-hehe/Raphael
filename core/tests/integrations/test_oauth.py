"""Tests for the login in the browser: the storage of the tokens, the listener it ends at, and a whole
login, a silent refresh and a revocation against a fake service on this computer (`fake_oauth`)."""

import asyncio
import logging

import httpx
import pytest

from raphael_core import __version__
from raphael_core.integrations.client import USER_AGENT, McpConnector, McpFailure
from raphael_core.integrations.config import ServerConfig
from raphael_core.integrations.oauth import (
    CallbackListener,
    VaultTokenStorage,
    revoke,
)
from raphael_core.integrations.vault import MAX_SECRET_CHARS, MemoryVault

from .fake_oauth import FakeService


@pytest.fixture
async def service():
    fake = FakeService()
    await fake.start()
    yield fake
    await fake.close()


def server_for(fake: FakeService, ident: str = "s1") -> ServerConfig:
    return ServerConfig(id=ident, name="Fake", transport="http", url=fake.url, auth="oauth")


# --- what is kept ----------------------------------------------------------------------------


async def token_and_client():
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    token = OAuthToken(access_token="a" * 3000, token_type="Bearer", expires_in=3600, refresh_token="r" * 1500)
    client = OAuthClientInformationFull(client_id="cid", redirect_uris=["http://127.0.0.1:5/callback"])
    return token, client


async def test_long_tokens_are_kept_over_several_credentials_and_read_back():
    vault = MemoryVault()
    storage = VaultTokenStorage(vault, "s1")
    token, client = await token_and_client()
    await storage.set_tokens(token)
    await storage.set_client_info(client)
    assert all(len(vault.get(name) or "") <= MAX_SECRET_CHARS for name in vault.names())
    assert len(vault.names()) > 4
    again = VaultTokenStorage(vault, "s1")
    assert (await again.get_tokens()).access_token == "a" * 3000  # type: ignore[union-attr]
    assert (await again.get_client_info()).client_id == "cid"  # type: ignore[union-attr]
    assert again.expires_at is not None and again.expires_at > 0


async def test_the_tokens_of_one_server_are_not_those_of_another():
    vault = MemoryVault()
    token, _ = await token_and_client()
    await VaultTokenStorage(vault, "s1").set_tokens(token)
    other = VaultTokenStorage(vault, "s2")
    assert await other.get_tokens() is None and not other.has_tokens()


async def test_forgetting_leaves_nothing_in_the_vault():
    vault = MemoryVault()
    storage = VaultTokenStorage(vault, "s1")
    token, client = await token_and_client()
    await storage.set_tokens(token)
    await storage.set_client_info(client)
    storage.forget()
    assert vault.names() == [] and not storage.has_tokens()


async def test_tokens_that_are_not_what_was_written_read_as_none():
    vault = MemoryVault()
    storage = VaultTokenStorage(vault, "s1")
    from raphael_core.integrations.vault import store_long

    store_long(vault, "s1:oauth:tokens", "{not json")
    store_long(vault, "s1:oauth:client", "{not json")
    assert await storage.get_tokens() is None and await storage.get_client_info() is None


# --- the listener -----------------------------------------------------------------------------


async def get(listener: CallbackListener, path: str, host: str | None = None, language: str = "en") -> httpx.Response:
    headers = {"Host": host or f"127.0.0.1:{listener._port}", "Accept-Language": language}
    async with httpx.AsyncClient() as http:
        return await http.get(f"http://127.0.0.1:{listener._port}{path}", headers=headers)


async def test_the_listener_hands_over_the_code_and_says_the_tab_can_be_closed():
    listener = CallbackListener(timeout_s=5)
    await listener.start()
    listener.expect("st4te")
    try:
        assert listener.redirect_uri.startswith("http://127.0.0.1:") and listener.redirect_uri.endswith("/callback")
        waiting = asyncio.create_task(listener.wait())
        reply = await get(listener, "/callback?code=abc&state=st4te&iss=https%3A%2F%2Fas.example", language="zh-TW")
        result = await waiting
        assert (result.code, result.state, result.iss) == ("abc", "st4te", "https://as.example")
        assert reply.status_code == 200 and "關閉" in reply.text
    finally:
        await listener.close()


async def test_a_request_with_another_state_is_turned_away_and_the_wait_goes_on():
    listener = CallbackListener(timeout_s=5)
    await listener.start()
    listener.expect("right")
    try:
        waiting = asyncio.create_task(listener.wait())
        wrong = await get(listener, "/callback?code=evil&state=wrong")
        assert wrong.status_code == 400
        assert not waiting.done()
        await get(listener, "/callback?code=good&state=right")
        assert (await waiting).code == "good"
    finally:
        await listener.close()


async def test_the_listener_answers_only_the_callback_from_its_own_address():
    listener = CallbackListener(timeout_s=5)
    await listener.start()
    listener.expect("s")
    try:
        assert (await get(listener, "/favicon.ico")).status_code == 404
        assert (await get(listener, "/callback?code=c&state=s", host="evil.example")).status_code == 404
        assert not listener._done.done()
    finally:
        await listener.close()


async def test_it_takes_one_login_only():
    listener = CallbackListener(timeout_s=5)
    await listener.start()
    listener.expect("s")
    try:
        await get(listener, "/callback?code=first&state=s")
        second = await get(listener, "/callback?code=second&state=s")
        assert second.status_code == 400
        assert (await listener.wait()).code == "first"
    finally:
        await listener.close()


async def test_a_refusal_in_the_browser_is_a_denied_login():
    listener = CallbackListener(timeout_s=5)
    await listener.start()
    listener.expect("s")
    try:
        waiting = asyncio.create_task(listener.wait())
        reply = await get(listener, "/callback?error=access_denied&state=s")
        assert reply.status_code == 200 and "not finished" in reply.text
        with pytest.raises(McpFailure) as failure:
            await waiting
        assert failure.value.kind == "denied"
    finally:
        await listener.close()


async def test_the_wait_has_a_limit():
    listener = CallbackListener(timeout_s=0.1)
    await listener.start()
    try:
        with pytest.raises(TimeoutError):
            await listener.wait()
    finally:
        await listener.close()


async def test_a_closed_listener_takes_no_more_connections():
    listener = CallbackListener(timeout_s=5)
    await listener.start()
    port = listener._port
    await listener.close()
    with pytest.raises(httpx.ConnectError):
        async with httpx.AsyncClient() as http:
            await http.get(f"http://127.0.0.1:{port}/callback")


# --- a whole login ----------------------------------------------------------------------------


async def test_a_login_signs_in_and_reads_the_tools(service):
    vault = MemoryVault()
    result = await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    assert [t.name for t in result.tools] == ["search", "create_page", "delete_page"]
    assert result.revocation_url == f"{service.base}/revoke"
    by_name = {t.name: t for t in result.tools}
    assert by_name["search"].read_only_hint is True and by_name["delete_page"].destructive_hint is True
    assert service.registrations == 1 and service.authorizations == 1
    assert VaultTokenStorage(vault, "s1").has_tokens()


async def test_the_tokens_never_leave_the_vault(service, caplog):
    vault = MemoryVault()
    with caplog.at_level(logging.DEBUG):
        await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    spoken = "\n".join(record.getMessage() for record in caplog.records)
    secrets_seen = [t for t in service.access] + list(service.refresh)
    assert secrets_seen and not any(secret in spoken for secret in secrets_seen)
    assert "state=" not in spoken and "code_challenge" not in spoken and "127.0.0.1" not in spoken


async def test_long_tokens_survive_a_login(service):
    service.padding = 2500
    vault = MemoryVault()
    connector = McpConnector(vault)
    await connector.login(server_for(service), service.browser, timeout_s=10)
    assert all(len(vault.get(name) or "") <= MAX_SECRET_CHARS for name in vault.names())
    tools = await McpConnector(vault).list_tools(server_for(service))  # a later job, from the vault
    assert len(tools) == 3


async def test_a_later_job_signs_in_silently_with_the_stored_tokens(service):
    vault = MemoryVault()
    await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    before = (service.registrations, service.authorizations)
    for _ in range(2):  # each job is a new connection with a new provider
        assert len(await McpConnector(vault).list_tools(server_for(service))) == 3
    assert (service.registrations, service.authorizations) == before  # no browser, no registration


async def test_an_access_token_that_ran_out_is_refreshed_without_the_browser(service):
    service.access_ttl = 1
    vault = MemoryVault()
    await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    await asyncio.sleep(1.2)
    assert len(await McpConnector(vault).list_tools(server_for(service))) == 3
    assert service.refreshes == 1 and service.authorizations == 1
    # the new tokens were kept: the next job uses them
    assert len(await McpConnector(vault).list_tools(server_for(service))) == 3


async def test_a_refresh_the_server_refuses_fails_as_auth_and_opens_no_browser(service):
    service.access_ttl = 1
    vault = MemoryVault()
    await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    await asyncio.sleep(1.2)
    service.reject_refresh = True
    with pytest.raises(McpFailure) as failure:
        await McpConnector(vault).list_tools(server_for(service))
    assert failure.value.kind == "auth"
    assert service.authorizations == 1  # the browser was not asked for again


async def test_a_server_that_forgot_the_access_token_fails_as_auth_when_no_refresh_is_left(service):
    vault = MemoryVault()
    await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    service.expire_access_tokens()
    service.refresh.clear()
    with pytest.raises(McpFailure) as failure:
        await McpConnector(vault).list_tools(server_for(service))
    assert failure.value.kind == "auth" and service.authorizations == 1


async def test_a_server_never_signed_in_to_fails_as_auth_without_touching_the_network(service):
    with pytest.raises(McpFailure) as failure:
        await McpConnector(MemoryVault()).list_tools(server_for(service))
    assert failure.value.kind == "auth"
    assert service.mcp_calls == [] and service.registrations == 0


async def test_a_tool_call_uses_the_stored_login(service):
    vault = MemoryVault()
    await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    outcome = await McpConnector(vault).call_tool(server_for(service), "search", {"q": "x"})
    assert outcome.text == "did search" and not outcome.is_error


async def test_a_login_the_user_refuses_is_denied(service):
    service.deny = True
    vault = MemoryVault()
    with pytest.raises(McpFailure) as failure:
        await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    assert failure.value.kind == "denied"
    assert vault.names() == [] or not VaultTokenStorage(vault, "s1").has_tokens()


async def test_a_login_nobody_finishes_times_out(service):
    async def nobody(url: str) -> None:
        return None

    with pytest.raises(McpFailure) as failure:
        await McpConnector(MemoryVault()).login(server_for(service), nobody, timeout_s=0.3)
    assert failure.value.kind == "timeout"


async def test_a_login_can_be_cancelled_and_closes_its_listener(service):
    opened: list[str] = []

    async def nobody(url: str) -> None:
        opened.append(url)

    task = asyncio.create_task(McpConnector(MemoryVault()).login(server_for(service), nobody, timeout_s=30))
    for _ in range(100):
        if opened:
            break
        await asyncio.sleep(0.05)
    assert opened
    redirect = httpx.URL(opened[0]).params["redirect_uri"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(httpx.ConnectError):
        async with httpx.AsyncClient() as http:
            await http.get(redirect)


async def test_a_new_login_starts_from_nothing(service):
    vault = MemoryVault()
    connector = McpConnector(vault)
    await connector.login(server_for(service), service.browser, timeout_s=10)
    await connector.login(server_for(service), service.browser, timeout_s=10)
    assert service.registrations == 2  # the old registration was forgotten, not reused


# --- who is asking ------------------------------------------------------------------------------


def test_the_user_agent_is_ours_and_says_so():
    assert USER_AGENT == f"Raphael/{__version__} (Windows)"


async def test_every_request_to_the_service_says_who_is_asking(service):
    vault = MemoryVault()
    result = await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    await McpConnector(vault).list_tools(server_for(service))  # a later job, the MCP calls again
    await revoke(VaultTokenStorage(vault, "s1"), result.revocation_url)
    # discovery, registration, token and MCP calls, and the revocation; the browser's own are the test's
    ours = [agent for agent in service.agents if agent != "Browser/1"]
    assert len(ours) >= 8 and set(ours) == {USER_AGENT}


async def test_a_user_agent_the_user_sets_in_a_header_is_kept(service):
    from raphael_core.integrations.client import with_user_agent

    assert with_user_agent({"user-agent": "Mine/1"}) == {"user-agent": "Mine/1"}
    assert with_user_agent({"X-Key": "v"}) == {"X-Key": "v", "User-Agent": USER_AGENT}


# --- taking the tokens back ------------------------------------------------------------------


async def test_revoking_sends_both_tokens_to_the_server(service):
    vault = MemoryVault()
    result = await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    storage = VaultTokenStorage(vault, "s1")
    refresh, access, _ = storage.read_for_revocation()
    assert await revoke(storage, result.revocation_url)
    assert set(service.revoked) == {refresh, access}


async def test_revoking_fails_quietly(service, caplog):
    vault = MemoryVault()
    result = await McpConnector(vault).login(server_for(service), service.browser, timeout_s=10)
    storage = VaultTokenStorage(vault, "s1")
    assert not await revoke(storage, "")
    assert not await revoke(VaultTokenStorage(vault, "nobody"), result.revocation_url)
    assert not await revoke(storage, "http://example.com/revoke")  # not https, not this computer
    await service.close()
    with caplog.at_level(logging.WARNING):
        assert not await revoke(storage, result.revocation_url, timeout_s=2)
    assert all("127.0.0.1" not in r.getMessage() for r in caplog.records)
