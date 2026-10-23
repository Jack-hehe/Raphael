"""Mandatory cleanup retries and reasoning budgets, using fake HTTP only."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from raphael_core.brain.cleanup import CleanupService
from raphael_core.brain.db import connect
from raphael_core.brain.dictionary import DictionaryStore
from raphael_core.brain.openrouter import OpenRouterClient, OpenRouterError
from raphael_core.brain.providers.clients import AnthropicProvider, CompatibleProvider
from raphael_core.brain.providers.registry import ProviderRegistry, ProviderRouter
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.settings import DEFAULTS, SettingsStore
from raphael_core.brain.spend import SpendTracker
from raphael_core.router import Router


async def test_empty_reasoning_response_retries_and_records_actual_cleanup_model():
    seen = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "gpt-6-luna"}]})
        body = json.loads(request.content)
        seen.append((request.url.host, body, request.extensions["timeout"]["read"]))
        direct = request.url.host == "api.openai.com"
        return httpx.Response(200, json={
            "model": body["model"],
            "choices": [{"finish_reason": "length" if direct else "stop",
                         "message": {"content": "" if direct else '{"text":"Hello world."}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 256},
        })

    conn, keys, vault = connect(":memory:"), MemoryKeyStore(), {}
    keys.set("openrouter-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        legacy = OpenRouterClient(keys.get, http=http)
        registry = ProviderRegistry(conn, legacy, keys,
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        brain = Brain(conn, ProviderRouter(registry, legacy), keys, providers=registry)
        router = Router()
        brain.register(router)
        registry.register(router)
        pid = (await router.call("providers.add", {"kind": "openai", "key": "direct-secret"}))["provider"]["id"]
        await registry.discover(pid)
        await router.call("settings.set", {"key": "cleanup_model", "value": f"@{pid}:gpt-6-luna"})
        result = await router.call("brain.finalize_dictation", {
            "raw_text": "Hello world", "audio_seconds": 3, "stt_ms": 1,
        })
        assert result["cleaned"] and result["text"] == "Hello world."
        assert result["fallback_reason"] is None
        assert len(seen) == 2
        assert seen[0][1]["reasoning_effort"] == "low"
        assert seen[0][1]["max_completion_tokens"] == 256 + 2048
        assert seen[1][1]["model"] == DEFAULTS["cleanup_model"]
        history = conn.execute("SELECT * FROM history").fetchone()
        assert history["model"] == DEFAULTS["cleanup_model"]
        assert seen[0][2] <= 11 * 0.6
        assert 0 < seen[1][2] <= 11
        await brain.aclose()
        await registry.aclose()
    conn.close()


def response(content='{"text":"Hello world."}', finish="stop", model=None):
    return httpx.Response(200, json={
        **({"model": model} if model else {}),
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.01},
    })


@asynccontextmanager
async def cleanup_case(handler, *, openrouter_key=True, timeout=1):
    conn, keys, vault = connect(":memory:"), MemoryKeyStore(), {}
    if openrouter_key:
        keys.set("openrouter-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        legacy = OpenRouterClient(keys.get, http=http)
        registry = ProviderRegistry(conn, legacy, keys,
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        rpc = Router()
        registry.register(rpc)
        pid = (await rpc.call("providers.add", {"kind": "openai", "key": "secret"}))["provider"]["id"]
        settings = SettingsStore(conn)
        settings.set("cleanup_model", f"@{pid}:gpt-6-luna")
        dispatcher = ProviderRouter(registry, legacy)
        svc = CleanupService(dispatcher, settings, DictionaryStore(conn), SpendTracker(conn),
            has_key=lambda: dispatcher.for_purpose("cleanup").available(settings.get("cleanup_model")),
            timeout_fn=lambda _: timeout)
        try:
            yield SimpleNamespace(svc=svc, registry=registry, settings=settings, rpc=rpc,
                                  keys=keys, conn=conn, dispatcher=dispatcher, pid=pid,
                                  vault=vault, http=http)
        finally:
            await registry.aclose()
            await legacy.aclose()
    conn.close()


@pytest.mark.parametrize("failure", [401, 402, 403, 408, 429, 500, "empty", "invalid", "suspicious", "unexpected"])
async def test_any_chosen_model_failure_retries_only_once(failure):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            if isinstance(failure, int):
                return httpx.Response(failure, json={"error": "private failure"})
            if failure == "unexpected":
                raise RuntimeError("private failure")
            return response({"empty": "", "invalid": "private failure", "suspicious": '{"text":""}'}[failure])
        return response()

    async with cleanup_case(handler) as case:
        # Even an explicit generic chain must not create a third HTTP attempt.
        case.settings.set("cleanup_fallbacks", ["some/third-model"])
        result = await case.svc.clean("Hello world", audio_seconds=1)
        assert result.cleaned and result.text == "Hello world."
        assert result.model == DEFAULTS["cleanup_model"]
        assert result.fallback_reason is None
        assert len(calls) == 2
        assert all("models" not in call for call in calls)
        expected_cost = 0.02 if failure == "suspicious" else 0.01
        assert result.cost_usd == pytest.approx(expected_cost)
        assert case.conn.execute("SELECT SUM(cost_usd) FROM spend").fetchone()[0] == pytest.approx(expected_cost)


@pytest.mark.parametrize("second", ["empty", 401, 429, 500, "suspicious"])
async def test_both_fail_returns_raw_with_final_failure_reason(second, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1 or second == "empty":
            return response("", "length")
        if second == "suspicious":
            return response('{"text":""}')
        return httpx.Response(second, json={"error": "private failure"})

    async with cleanup_case(handler) as case:
        result = await case.svc.clean("Hello world", audio_seconds=1)
        assert not result.cleaned and result.text == "Hello world"
        assert result.fallback_reason == {
            "empty": "error:truncated", 401: "error:auth", 429: "error:rate_limit",
            500: "error:server", "suspicious": "suspicious",
        }[second]
        assert len(calls) == 2
    assert "Hello world" not in caplog.text and "private failure" not in caplog.text


async def test_timeout_reserves_retry_time_inside_original_deadline():
    budgets, cancelled = [], []

    async def handler(request):
        budgets.append(request.extensions["timeout"]["read"])
        if len(budgets) == 1:
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
        return response()

    async with cleanup_case(handler, timeout=0.2) as case:
        started = time.perf_counter()
        result = await case.svc.clean("Hello world", audio_seconds=1)
        elapsed = time.perf_counter() - started
        assert result.cleaned and cancelled == [True]
        assert budgets[0] == pytest.approx(0.12)
        assert 0 < budgets[1] <= 0.08
        assert elapsed < 0.4


async def test_owner_cancellation_does_not_retry():
    started, calls = asyncio.Event(), []

    async def handler(request):
        calls.append(request)
        started.set()
        await asyncio.sleep(10)
        return response()

    async with cleanup_case(handler) as case:
        task = asyncio.create_task(case.svc.clean("Hello world", audio_seconds=1))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1


async def test_cancel_dictation_rpc_does_not_start_a_retry_or_keep_history():
    started, release, calls = asyncio.Event(), asyncio.Event(), []

    async def handler(request):
        calls.append(request)
        started.set()
        await release.wait()
        return response("", "length")

    async with cleanup_case(handler) as case:
        brain = Brain(case.conn, case.dispatcher, case.keys, providers=case.registry)
        brain.register(case.rpc)
        task = asyncio.create_task(case.rpc.call("brain.finalize_dictation", {
            "raw_text": "Hello world", "audio_seconds": 1, "stt_ms": 1, "session_id": "cancel-me",
        }))
        await started.wait()
        await case.rpc.call("brain.cancel_dictation", {"session_id": "cancel-me"})
        release.set()
        await task
        assert len(calls) == 1
        assert case.conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0
        await brain.aclose()


async def test_without_openrouter_key_uses_answer_model():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return response("", "length") if len(calls) == 1 else response()

    async with cleanup_case(handler, openrouter_key=False) as case:
        case.settings.set("agent_model", f"@{case.pid}:gpt-4o")
        result = await case.svc.clean("Hello world", audio_seconds=1)
        assert result.cleaned and result.model == f"@{case.pid}:gpt-4o"
        assert calls == ["gpt-6-luna", "gpt-4o"]


async def test_successful_provider_model_is_persisted_and_discovery_alone_is_not_success():
    calls = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "unproven"}]})
        model = json.loads(request.content)["model"]
        calls.append(model)
        return response("", "length") if model == "gpt-6-luna" else response()

    async with cleanup_case(handler, openrouter_key=False) as case:
        unproven = (await case.rpc.call("providers.add", {"kind": "groq", "key": "secret"}))["provider"]["id"]
        await case.registry.discover(unproven)
        proven = (await case.rpc.call("providers.add", {"kind": "ollama"}))["provider"]["id"]
        await case.dispatcher.chat_text(model=f"@{proven}:proven", system="s", user="u", timeout_s=1, max_tokens=20)
        calls.clear()
        # A fresh registry reads model successes without relying on runtime status/discovery.
        restored = ProviderRegistry(case.conn, case.registry.openrouter, case.keys,
            credential=lambda pid: case.vault.setdefault(pid, MemoryKeyStore()), http=case.http)
        restored_service = CleanupService(ProviderRouter(restored, restored.openrouter),
            case.settings, DictionaryStore(case.conn), SpendTracker(case.conn), has_key=lambda: True)
        result = await restored_service.clean("Hello world", audio_seconds=1)
        assert result.cleaned and result.model == f"@{proven}:proven"
        assert calls == ["gpt-6-luna", "proven"]
        await case.rpc.call("providers.update", {"id": proven, "enabled": False})
        # The live registry must not select a disabled provider, even with a past success.
        assert case.dispatcher.for_purpose("cleanup").cleanup_fallback(f"@{case.pid}:gpt-6-luna") == f"@{case.pid}:gpt-6-luna"
        await restored.aclose()


@pytest.mark.parametrize("kind,model,effort,expected", [
    ("openai", "gpt-5", "none", "minimal"),
    ("openai", "gpt-5-mini", "none", "minimal"),
    ("openai", "gpt-5-2025-08-07", "none", "minimal"),
    ("openai", "gpt-5.1", "none", "low"),
    ("openai", "gpt-6-luna", "none", "low"),
    ("azure_openai", "gpt-6-luna", "none", "low"),
    ("azure_openai", "gpt-5", "none", "minimal"),
    ("openai", "o3", "none", "low"),
    ("openai", "o4-mini", "high", "high"),
    ("openai", "gpt-6-luna", "medium", "medium"),
    ("openai", "gpt-4o", "none", None),
    ("openai_compatible", "gpt-6-luna", "none", None),
    # Unknown Gemini family support is deliberately omitted, as allowed by the brief.
    ("google", "gemini-test", "none", None),
    ("google", "gemini-2.5-flash", "none", "none"),
    ("google", "gemini-2.5-pro", "none", "low"),
    ("google", "gemini-3.1-flash-lite", "none", "low"),
    ("google", "gemini-3.8-flash", "high", "high"),
])
async def test_provider_reasoning_intent_and_budget(kind, model, effort, expected):
    def handler(request):
        body = json.loads(request.content)
        assert "reasoning" not in body
        if expected:
            assert body["reasoning_effort"] == expected
            if kind == "google":
                assert body["max_tokens"] == (256 if expected == "none" else 256 + 2048)
                assert "max_completion_tokens" not in body
            else:
                assert body["max_completion_tokens"] == 256 + 2048
                assert "max_tokens" not in body
        else:
            assert "reasoning_effort" not in body and body["max_tokens"] == 256
        return response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider(kind, "https://example.com/v1", lambda: "secret", http=http)
        await client.complete_json(model=model, fallbacks=[], system="s", user="u",
            schema={"type": "object"}, schema_name="cleanup", timeout_s=1,
            max_tokens=256, reasoning_effort=effort)


@pytest.mark.parametrize("content,finish,kind", [
    (None, "stop", "truncated"), ("", "stop", "truncated"),
    ("  ", "stop", "truncated"), ('{"text":', "length", "truncated"),
    ("[]", "length", "truncated"), ("not JSON", "stop", "error"),
    ('{"text":"Hello world."}', "length", None),
])
async def test_truncated_classification_preserves_usable_json(content, finish, kind, caplog):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response(content, finish))) as http:
        client = CompatibleProvider("openai", "https://example.com/v1", lambda: "secret", http=http)
        kwargs = dict(model="gpt-6-luna", fallbacks=[], system="s", user="private dictation",
                      schema={"type": "object"}, schema_name="cleanup", timeout_s=1, max_tokens=256)
        if kind:
            with pytest.raises(OpenRouterError) as caught:
                await client.complete_json(**kwargs)
            assert caught.value.kind == kind
        else:
            assert (await client.complete_json(**kwargs)).data == {"text": "Hello world."}
    if kind == "truncated":
        assert [record.getMessage() for record in caplog.records] == [
            "Completion failed: model=gpt-6-luna kind=truncated"]


@pytest.mark.parametrize("effort", ["none", "high"])
async def test_anthropic_thinking_requires_explicit_intent(effort):
    def handler(request):
        body = json.loads(request.content)
        assert "reasoning" not in body and "reasoning_effort" not in body
        if effort == "none":
            assert "thinking" not in body and body["max_tokens"] == 256
        else:
            assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
            assert body["max_tokens"] == 256 + 4096
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hello"}],
                                         "stop_reason": "end_turn"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AnthropicProvider("anthropic", "https://example.com", lambda: "secret", http=http)
        result = await client.chat_text(model="claude-sonnet-4", system="s", user="u",
            timeout_s=1, max_tokens=256, reasoning_effort=effort)
        assert result.text == "hello"


async def test_modern_anthropic_explicit_thinking_uses_adaptive_mode():
    def handler(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"] == {"effort": "high"}
        assert body["max_tokens"] > 256
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hello"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AnthropicProvider("anthropic", "https://example.com", lambda: "secret", http=http)
        await client.chat_text(model="claude-haiku-5.5", system="s", user="u",
            timeout_s=1, max_tokens=256, reasoning_effort="high")
