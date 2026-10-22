"""Tests for the device login (GitHub): the code is asked for, the token endpoint is polled, and
every way it can end. GitHub is a fake behind httpx2's MockTransport; nothing leaves the test."""

import asyncio
from urllib.parse import parse_qs

import httpx2
import pytest

from raphael_core.integrations import catalog
from raphael_core.integrations.client import McpConnector, McpFailure
from raphael_core.integrations.config import ServerConfig
from raphael_core.integrations.device import BearerAuth, device_login
from raphael_core.integrations.oauth import VaultTokenStorage
from raphael_core.integrations.vault import MemoryVault

LOGIN = catalog.DeviceLogin(
    client_id="Iv-test",
    scopes=("repo", "read:org"),
    device_endpoint="https://github.com/login/device/code",
    token_endpoint="https://github.com/login/oauth/access_token",
)
TOKEN = "gho_" + "x" * 36


class FakeGitHub:
    """A device flow endpoint: `answers` are what the token endpoint says, one for each poll (the
    last one again once they are used up)."""

    def __init__(self, answers, *, interval=5, expires_in=900):
        self.answers = list(answers)
        self.interval = interval
        self.expires_in = expires_in
        self.requests: list[httpx2.Request] = []
        self.forms: list[dict[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.forms.append(form)
        if request.url.path == "/login/device/code":
            return httpx2.Response(
                200,
                json={
                    "device_code": "dev-secret",
                    "user_code": "WDJB-MJHT",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": self.expires_in,
                    "interval": self.interval,
                },
            )
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return httpx2.Response(200, json=answer)

    @property
    def transport(self):
        return httpx2.MockTransport(self)


def ok():
    return {"access_token": TOKEN, "token_type": "bearer", "scope": "repo,read:org"}


def err(code):
    return {"error": code}


class Clock:
    def __init__(self):
        self.slept: list[float] = []

    async def __call__(self, seconds):
        self.slept.append(seconds)


async def run(fake, clock=None, codes=None):
    codes = [] if codes is None else codes

    async def on_code(code):
        codes.append(code)

    return await device_login(LOGIN, on_code, transport=fake.transport, sleep=clock or Clock())


async def test_a_login_that_is_approved_at_once_returns_the_token():
    fake = FakeGitHub([ok()])
    codes = []
    assert await run(fake, codes=codes) == TOKEN
    assert codes[0].user_code == "WDJB-MJHT" and codes[0].verification_uri == "https://github.com/login/device"
    assert codes[0].expires_in == 900
    assert fake.forms[0] == {"client_id": "Iv-test", "scope": "repo read:org"}
    second = fake.forms[1]
    assert second["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert second["device_code"] == "dev-secret" and "client_secret" not in second
    for request in fake.requests:
        assert request.headers["accept"] == "application/json"
        assert request.headers["user-agent"].startswith("Raphael/")


async def test_it_polls_at_the_interval_until_the_user_approves():
    fake = FakeGitHub([err("authorization_pending"), err("authorization_pending"), ok()], interval=7)
    clock = Clock()
    assert await run(fake, clock) == TOKEN
    assert clock.slept == [7, 7, 7]


async def test_slow_down_adds_five_seconds():
    fake = FakeGitHub([err("slow_down"), err("authorization_pending"), ok()], interval=5)
    clock = Clock()
    await run(fake, clock)
    assert clock.slept == [5, 10, 10]


async def test_an_expired_code_is_a_timeout():
    with pytest.raises(McpFailure) as failure:
        await run(FakeGitHub([err("expired_token")]))
    assert failure.value.kind == "timeout"


async def test_a_code_nobody_uses_runs_out_by_itself():
    fake = FakeGitHub([err("authorization_pending")], interval=5, expires_in=20)
    with pytest.raises(McpFailure) as failure:
        await run(fake)
    assert failure.value.kind == "timeout"


async def test_a_code_that_claims_to_last_longer_is_cut_to_the_deadline_of_the_call():
    fake = FakeGitHub([err("authorization_pending")], interval=5, expires_in=3600)
    clock = Clock()
    with pytest.raises(McpFailure) as failure:
        await run(fake, clock)
    assert failure.value.kind == "timeout" and sum(clock.slept) <= 900


async def test_a_refusal_is_denied():
    with pytest.raises(McpFailure) as failure:
        await run(FakeGitHub([err("authorization_pending"), err("access_denied")]))
    assert failure.value.kind == "denied"


@pytest.mark.parametrize("code", ["incorrect_client_credentials", "device_flow_disabled", "unsupported_grant_type"])
async def test_any_other_error_is_an_auth_failure(code):
    with pytest.raises(McpFailure) as failure:
        await run(FakeGitHub([err(code)]))
    assert failure.value.kind == "auth"


async def test_a_verification_address_that_is_not_https_is_refused():
    def handler(request):
        return httpx2.Response(
            200,
            json={"device_code": "d", "user_code": "ABCD-EFGH", "verification_uri": "http://evil.example/x", "expires_in": 900, "interval": 5},
        )

    async def on_code(code):
        raise AssertionError("must not be shown")

    with pytest.raises(McpFailure) as failure:
        await device_login(LOGIN, on_code, transport=httpx2.MockTransport(handler), sleep=Clock())
    assert failure.value.kind == "protocol"


async def test_a_network_that_fails_is_a_network_failure():
    def handler(request):
        raise httpx2.ConnectError("down")

    async def on_code(code):
        pass

    with pytest.raises(McpFailure) as failure:
        await device_login(LOGIN, on_code, transport=httpx2.MockTransport(handler), sleep=Clock())
    assert failure.value.kind == "network"


async def test_a_login_can_be_cancelled_while_it_waits():
    fake = FakeGitHub([err("authorization_pending")])
    shown = asyncio.Event()

    async def on_code(code):
        shown.set()

    async def forever(_seconds):
        await asyncio.sleep(3600)

    task = asyncio.create_task(device_login(LOGIN, on_code, transport=fake.transport, sleep=forever))
    await asyncio.wait_for(shown.wait(), 5)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- the connector: token in the vault, bearer on the wire ----------------------------------------


def github_server():
    return ServerConfig(
        id="g1", name="GitHub", transport="http", url="https://api.githubcopilot.com/mcp/", auth="device", catalog_id="github"
    )


@pytest.fixture
def github_in_store(monkeypatch):
    services = catalog.SERVICES + (catalog.github_service("Iv-test"),)
    monkeypatch.setattr(catalog, "SERVICES", services)
    monkeypatch.setattr(catalog, "_BY_ID", {s.id: s for s in services})


async def test_the_connector_keeps_the_token_in_the_vault_and_reads_tools(servers, github_in_store):
    servers.add("GitHub")
    vault = MemoryVault()
    fake = FakeGitHub([err("authorization_pending"), ok()])
    codes = []

    async def on_code(code):
        codes.append(code.user_code)

    connector = McpConnector(vault, servers, device_http={"transport": fake.transport, "sleep": Clock()})

    async def never(_url):
        raise AssertionError("the browser is the window's to open")

    result = await connector.login(github_server(), never, timeout_s=10, on_code=on_code)
    assert codes == ["WDJB-MJHT"] and result.tools
    assert (await VaultTokenStorage(vault, "g1").get_tokens()).access_token == TOKEN  # type: ignore[union-attr]


async def test_a_login_that_is_denied_leaves_nothing_in_the_vault(servers, github_in_store):
    servers.add("GitHub")
    vault = MemoryVault()
    fake = FakeGitHub([err("access_denied")])
    connector = McpConnector(vault, servers, device_http={"transport": fake.transport, "sleep": Clock()})

    async def on_code(code):
        pass

    with pytest.raises(McpFailure) as failure:
        await connector.login(github_server(), None, timeout_s=10, on_code=on_code)
    assert failure.value.kind == "denied" and vault.names() == []


async def test_a_silent_job_without_a_token_is_an_auth_failure(servers):
    servers.add("GitHub")
    connector = McpConnector(MemoryVault(), servers)
    with pytest.raises(McpFailure) as failure:
        await connector.list_tools(github_server())
    assert failure.value.kind == "auth"


async def test_the_bearer_is_sent_and_a_401_is_an_auth_failure():
    from mcp.shared.auth import OAuthToken

    storage = VaultTokenStorage(MemoryVault(), "g1")
    await storage.set_tokens(OAuthToken(access_token=TOKEN, token_type="bearer"))
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx2.Response(401)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), auth=BearerAuth(storage)) as http:
        with pytest.raises(McpFailure) as failure:
            await http.get("https://api.githubcopilot.com/mcp/")
    assert seen == [f"Bearer {TOKEN}"] and failure.value.kind == "auth"


async def test_a_job_picks_the_bearer_for_a_device_server(monkeypatch):
    import mcp.client.streamable_http as streamable
    from mcp.shared.auth import OAuthToken

    from raphael_core.integrations import client

    vault = MemoryVault()
    await VaultTokenStorage(vault, "g1").set_tokens(OAuthToken(access_token=TOKEN, token_type="bearer"))
    secrets = McpConnector(vault)._secrets(github_server())
    captured = {}

    class Stop(Exception):
        pass

    def fake_client(url, http_client):
        captured["auth"] = http_client.auth
        raise Stop

    monkeypatch.setattr(streamable, "streamable_http_client", fake_client)
    with pytest.raises(Stop):
        async with client.open_client(github_server(), secrets):
            pass
    assert isinstance(captured["auth"], BearerAuth)


# --- the RPC methods ------------------------------------------------------------------------------


@pytest.fixture
def github(conn, tmp_path, servers, github_in_store):
    from raphael_core.integrations.service import IntegrationsService
    from raphael_core.router import Router

    servers.add("GitHub")
    vault = MemoryVault()
    fake = FakeGitHub([err("authorization_pending"), ok()])
    sent: list[tuple[str, dict]] = []
    connector = McpConnector(vault, servers, device_http={"transport": fake.transport, "sleep": Clock()})
    service = IntegrationsService(
        conn, tmp_path, vault, connector=connector, notify=lambda method, params: sent.append((method, params))
    )
    router = Router()
    service.register(router)
    service.router, service.vault, service.sent, service.fake = router, vault, sent, fake
    return service


async def test_connecting_github_tells_the_window_the_code_and_adds_the_server(github):
    done = await github.router.call("integrations.connect", {"catalog_id": "github"})
    assert done["connected"] is True and done["server"]["auth"] == "device" and done["server"]["status"] == "ready"
    assert github.sent == [
        (
            "integrations.device_code",
            {
                "catalog_id": "github",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
            },
        )
    ]
    listed = (await github.router.call("integrations.catalog"))["services"]
    assert [s["server_id"] for s in listed if s["id"] == "github"] == [done["server"]["id"]]


async def test_the_token_is_in_the_vault_only(github, tmp_path):
    done = await github.router.call("integrations.connect", {"catalog_id": "github"})
    kept = "".join(github.vault.get(name) or "" for name in github.vault.names())
    assert TOKEN in kept
    shown = (tmp_path / "integrations.json").read_text(encoding="utf-8")
    shown += repr(await github.router.call("toolsets.list")) + repr(done)
    assert TOKEN not in shown and "dev-secret" not in shown


async def test_a_denied_code_adds_nothing(github):
    github.fake.answers[:] = [err("access_denied")]
    done = await github.router.call("integrations.connect", {"catalog_id": "github"})
    assert done == {"connected": False, "error": "denied"}
    assert github.vault.names() == [] and (await github.router.call("integrations.list"))["servers"] == []


async def test_the_wait_can_be_cancelled(github):
    github.fake.answers[:] = [err("authorization_pending")]

    async def forever(_seconds):
        await asyncio.sleep(3600)

    github._connector._device_http["sleep"] = forever
    call = asyncio.create_task(github.router.call("integrations.connect", {"catalog_id": "github"}))
    for _ in range(200):
        if github.sent:
            break
        await asyncio.sleep(0.01)
    assert github.sent
    assert await github.router.call("integrations.connect_cancel") == {"cancelled": True}
    assert await call == {"connected": False, "error": "cancelled"}
    assert github.vault.names() == []


async def test_disconnecting_clears_the_vault_and_asks_nobody(github):
    done = await github.router.call("integrations.connect", {"catalog_id": "github"})
    assert github.vault.names()
    gone = await github.router.call("integrations.disconnect", {"id": done["server"]["id"]})
    assert gone == {"removed": True, "revoked": False}
    assert github.vault.names() == []
    assert len(github.fake.requests) == 3  # the code and two polls; nothing to revoke with
