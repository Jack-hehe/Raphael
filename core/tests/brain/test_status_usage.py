"""OpenRouter account usage through real RPC handlers and an HTTP mock, without credentials."""

import asyncio

import httpx
import pytest

from raphael_core.brain.openrouter import OpenRouterClient
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.router import Router

from .test_service import Clock, finalize

KEY = "sk-or-v1-usage-test"
NEW_KEY = "sk-or-v1-new-usage-test"


def key_data(monthly=3.4):
    return {
        "label": "Usage test",
        "usage_daily": 0.12,
        "usage_weekly": 1.23,
        "usage_monthly": monthly,
        "usage": 12.34,
        "limit": 25,
        "limit_remaining": 21.6,
        "limit_reset": "monthly",
    }


class KeyEndpoint:
    def __init__(self):
        self.data = {KEY: key_data(), NEW_KEY: key_data(8.9)}
        self.requests = []
        self.gate = None
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.failure = None
        self.ignore_cancellation = False
        self.block_request = None

    async def __call__(self, request):
        if request.url.path.endswith("/key"):
            key = request.headers["Authorization"].removeprefix("Bearer ")
            self.requests.append(key)
            data = dict(self.data[key])
            if (
                self.gate is not None
                and key == KEY
                and (self.block_request is None or len(self.requests) == self.block_request)
            ):
                self.started.set()
                try:
                    await self.gate.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    if not self.ignore_cancellation:
                        raise
            if isinstance(self.failure, Exception):
                raise self.failure
            if self.failure is not None:
                return httpx.Response(self.failure, json={"error": {"message": "unavailable"}})
            return httpx.Response(200, json={"data": data})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"text": "Hello."}'}}],
            "usage": {"cost": 0.0002, "prompt_tokens": 5, "completion_tokens": 2},
        })


@pytest.fixture
async def usage_brain(conn):
    opened = []

    def make(endpoint, *, timeout=0.5):
        keys, clock = MemoryKeyStore(), Clock()
        http = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
        brain = Brain(
            conn, OpenRouterClient(keys.get, http=http), keys, now_ms=clock,
            key_check_timeout_s=timeout,
        )
        router = Router()
        brain.register(router)
        opened.append((brain, http))
        return brain, router, keys, clock

    yield make
    for brain, http in opened:
        await brain.aclose()
        await http.aclose()


async def status(router):
    return (await router.call("status.get", {}))["openrouter"]


async def test_usage_bill_rpc_matches_status_sidebar_and_reports_enabled_voice(usage_brain):
    brain, router, _, _ = usage_brain(KeyEndpoint())
    for purpose in ("agent", "stt", "cleanup", "memory", "other"):
        brain._spend.record(purpose=purpose, model="m", cost_usd=.1, prompt_tokens=1, completion_tokens=1)
    bill = (await router.call("usage.runs", {"range": "month", "tz": "Pacific/Honolulu"}))["bill"]
    assert bill["cost_usd"] == (await router.call("status.get", {}))["spend"]["month_usd"]
    assert len(bill["purposes"]) == 5
    brain._settings.set("tts_enabled", True)
    assert (await router.call("usage.runs", {}))["fish_voice"] is True


async def wait_status(router, predicate):
    async def poll():
        while True:
            result = await status(router)
            if predicate(result):
                return result
            await asyncio.sleep(0)

    return await asyncio.wait_for(poll(), 1)


async def test_status_usage_reports_key_account_amounts(usage_brain):
    endpoint = KeyEndpoint()
    _, router, _, _ = usage_brain(endpoint)
    await router.call("secrets.set_openrouter_key", {"key": KEY})
    assert (await status(router))["usage"] == {
        "daily": 0.12, "weekly": 1.23, "monthly": 3.4, "total": 12.34,
        "limit": 25, "limit_remaining": 21.6,
    }


async def test_status_usage_preserves_zero_and_unknown_amounts(usage_brain):
    endpoint = KeyEndpoint()
    endpoint.data[KEY] = {"usage_monthly": 0, "limit": None}
    _, router, _, _ = usage_brain(endpoint)
    await router.call("secrets.set_openrouter_key", {"key": KEY})
    assert (await status(router))["usage"] == {
        "daily": None, "weekly": None, "monthly": 0, "total": None,
        "limit": None, "limit_remaining": None,
    }


async def test_status_usage_without_key_does_not_call_openrouter(usage_brain):
    endpoint = KeyEndpoint()
    _, router, _, _ = usage_brain(endpoint)
    assert (await status(router))["usage"] is None
    await asyncio.sleep(0)
    assert endpoint.requests == []


async def test_status_usage_loads_existing_key_in_background(usage_brain):
    endpoint = KeyEndpoint()
    endpoint.gate = asyncio.Event()
    _, router, keys, _ = usage_brain(endpoint)
    keys.set(KEY)
    first = await asyncio.wait_for(status(router), 0.1)
    assert first["state"] == "unknown" and first["usage"] is None
    await asyncio.wait_for(endpoint.started.wait(), 1)
    endpoint.gate.set()
    result = await wait_status(router, lambda r: r["usage"] is not None)
    assert result["usage"]["monthly"] == 3.4


async def test_status_usage_refreshes_expired_cache_without_blocking_dictation(usage_brain):
    endpoint = KeyEndpoint()
    _, router, _, clock = usage_brain(endpoint)
    await router.call("secrets.set_openrouter_key", {"key": KEY})
    clock.now_ms += 59_999
    assert (await status(router))["usage"]["monthly"] == 3.4
    await asyncio.sleep(0)
    assert endpoint.requests == [KEY]

    # A newer cleanup contact must not make the older key usage look fresh.
    await finalize(router, "Hello")
    endpoint.data[KEY]["usage_monthly"] = 4.5
    endpoint.gate = asyncio.Event()
    clock.now_ms += 1
    stale = await asyncio.wait_for(status(router), 0.1)
    assert stale["usage"]["monthly"] == 3.4
    await asyncio.wait_for(endpoint.started.wait(), 1)
    for _ in range(5):
        assert (await asyncio.wait_for(status(router), 0.1))["usage"]["monthly"] == 3.4
    result = await asyncio.wait_for(finalize(router, "Hello"), 0.2)
    assert result["cleaned"] is True
    assert endpoint.requests == [KEY, KEY]
    # Account usage and key limits never change the app's own spend tracking or budget.
    spend = (await router.call("status.get", {}))["spend"]
    assert spend["month_usd"] == pytest.approx(0.0004)
    assert spend["limit_usd"] == 10.0
    endpoint.gate.set()
    fresh = await wait_status(router, lambda r: r["usage"]["monthly"] == 4.5)
    assert fresh["usage"]["total"] == 12.34


@pytest.mark.parametrize("failure, kind", [
    (401, "auth"), (503, "server"),
    (httpx.ConnectError("offline"), "network"),
    (RuntimeError("private key store details"), "unexpected"),
])
async def test_status_usage_refresh_failure_keeps_cache_and_throttles_retries(
    usage_brain, failure, kind
):
    endpoint = KeyEndpoint()
    _, router, _, clock = usage_brain(endpoint)
    await router.call("secrets.set_openrouter_key", {"key": KEY})
    endpoint.failure = failure
    clock.now_ms += 60_000
    await status(router)
    failed = await wait_status(router, lambda r: r["state"] == "error")
    assert failed["error"] == kind and failed["usage"]["monthly"] == 3.4
    for _ in range(3):
        await status(router)
        await asyncio.sleep(0)
    assert endpoint.requests == [KEY, KEY]
    endpoint.failure = None
    endpoint.data[KEY]["usage_monthly"] = 5.6
    clock.now_ms += 60_000
    await status(router)
    assert (await wait_status(router, lambda r: r["state"] == "ok"))["usage"]["monthly"] == 5.6


async def test_status_usage_timeout_without_cache_is_safe(usage_brain):
    endpoint = KeyEndpoint()
    endpoint.gate = asyncio.Event()
    _, router, keys, _ = usage_brain(endpoint, timeout=0.02)
    keys.set(KEY)
    assert (await asyncio.wait_for(status(router), 0.1))["usage"] is None
    failed = await wait_status(router, lambda r: r["state"] == "error")
    assert failed["error"] == "timeout" and failed["usage"] is None
    assert endpoint.cancelled.is_set()


@pytest.mark.parametrize("change", ["clear", "replace", "external"])
async def test_status_usage_cannot_restore_old_key_data_after_key_changes(usage_brain, change):
    endpoint = KeyEndpoint()
    _, router, keys, clock = usage_brain(endpoint)
    await router.call("secrets.set_openrouter_key", {"key": KEY})
    endpoint.gate = asyncio.Event()
    endpoint.ignore_cancellation = True  # A response racing cancellation still must be discarded.
    clock.now_ms += 60_000
    await status(router)
    await asyncio.wait_for(endpoint.started.wait(), 1)
    if change == "clear":
        await router.call("secrets.clear_openrouter_key", {})
    elif change == "replace":
        await router.call("secrets.set_openrouter_key", {"key": NEW_KEY})
    else:
        keys.set(NEW_KEY)
    current = await status(router)
    assert current["usage"] is None or current["usage"]["monthly"] == 8.9
    endpoint.gate.set()
    if change == "clear":
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cleared = await status(router)
        assert cleared["state"] == "no_key" and cleared["usage"] is None
    else:
        fresh = await wait_status(router, lambda r: r["usage"] is not None)
        assert fresh["usage"]["monthly"] == 8.9
        await asyncio.sleep(0)
        assert (await status(router))["usage"]["monthly"] == 8.9


async def test_status_usage_key_store_failure_hides_cache(usage_brain):
    endpoint = KeyEndpoint()
    _, router, keys, _ = usage_brain(endpoint)
    await router.call("secrets.set_openrouter_key", {"key": KEY})

    def locked():
        raise RuntimeError("private vault details")

    keys.get = locked
    inaccessible = await status(router)
    assert inaccessible["state"] == "error" and inaccessible["error"] == "auth"
    assert inaccessible["usage"] is None
    assert endpoint.requests == [KEY]


@pytest.mark.parametrize("newer_check", ["status.check_openrouter", "secrets.set_openrouter_key"])
@pytest.mark.parametrize("older_failure", [None, 503, RuntimeError("private provider details")])
async def test_older_background_check_cannot_overwrite_a_newer_same_key_check(
    usage_brain, newer_check, older_failure
):
    endpoint = KeyEndpoint()
    brain, router, _, clock = usage_brain(endpoint)
    await router.call("secrets.set_openrouter_key", {"key": KEY})
    endpoint.data[KEY]["usage_monthly"] = 4.5
    endpoint.gate, endpoint.block_request = asyncio.Event(), 2
    clock.now_ms += 60_000
    await status(router)
    await asyncio.wait_for(endpoint.started.wait(), 1)
    pending = brain._key_refresh_task  # Synchronize the older request before reading public status.

    endpoint.data[KEY]["usage_monthly"] = 6.7
    await router.call(newer_check, {"key": KEY} if newer_check.startswith("secrets.") else {})
    assert (await status(router))["usage"]["monthly"] == 6.7
    endpoint.failure = older_failure
    endpoint.gate.set()
    await asyncio.wait_for(pending, 1)
    newest = await status(router)
    assert newest["state"] == "ok" and newest["usage"]["monthly"] == 6.7


async def test_closing_brain_cancels_usage_refresh(usage_brain):
    endpoint = KeyEndpoint()
    endpoint.gate = asyncio.Event()
    brain, router, keys, _ = usage_brain(endpoint)
    keys.set(KEY)
    await status(router)
    await asyncio.wait_for(endpoint.started.wait(), 1)
    await asyncio.wait_for(brain.aclose(), 0.1)
    assert endpoint.cancelled.is_set()
    await status(router)
    await asyncio.sleep(0)
    assert endpoint.requests == [KEY]
