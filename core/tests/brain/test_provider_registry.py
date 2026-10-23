"""Provider lifecycle, vault isolation, persistence and malformed model-reference contracts."""

import asyncio
import httpx
import pytest

from raphael_core.brain.db import connect
from raphael_core.brain.openrouter import OpenRouterClient, OpenRouterError
from raphael_core.brain.providers.registry import ProviderRegistry, ProviderRouter
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router


@pytest.fixture
async def setup_registry():
    conn, keys, vault = connect(":memory:"), MemoryKeyStore(), {}
    def credential(pid):
        return vault.setdefault(pid, MemoryKeyStore())
    def handler(request):
        return httpx.Response(200, json={"data": [
            {"id": "gpt-4o", "supported_parameters": ["tools"]}, {"id": "whisper-1"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        legacy = OpenRouterClient(keys.get, http=http)
        registry = ProviderRegistry(conn, legacy, keys, credential=credential, http=http)
        router, notices = Router(), []
        router.set_notifier(lambda method, params: notices.append((method, params)))
        registry.register(router)
        yield conn, keys, vault, credential, http, legacy, registry, router, notices
        await registry.aclose()
    conn.close()


async def test_provider_config_models_and_vault_survive_registry_reload(setup_registry):
    conn, keys, vault, credential, http, legacy, registry, router, notices = setup_registry
    pid = (await router.call("providers.add", {"kind": "openai", "key": "first-secret"}))["provider"]["id"]
    await router.call("providers.models", {"id": pid})
    entries = registry.catalog_entries()
    assert {(e.id, e.kind) for e in entries} == {(f"@{pid}:gpt-4o", "llm"), (f"@{pid}:whisper-1", "stt")}
    await registry.aclose()
    restored = ProviderRegistry(conn, legacy, keys, credential=credential, http=http)
    other = Router()
    restored.register(other)
    try:
        view = (await other.call("providers.list"))["providers"][0]
        assert view["id"] == pid and view["has_key"] and view["models"] == 2
        assert len(restored.catalog_entries()) == 2
        await other.call("providers.update", {"id": pid, "key": "replacement", "name": "My OpenAI"})
        assert vault[pid].get() == "replacement" and keys.get() is None
        assert (await other.call("providers.list"))["providers"][0]["name"] == "My OpenAI"
        await other.call("providers.remove", {"id": pid})
        assert (await other.call("providers.list")) == {"providers": []}
        assert vault[pid].get() is None
        saved = "".join(row[0] for row in conn.execute("SELECT value FROM settings"))
        assert "first-secret" not in saved and "replacement" not in saved
        with pytest.raises(ValueError):
            restored.resolve(f"@{pid}:gpt-4o")
    finally:
        await restored.aclose()


@pytest.mark.parametrize("reference", ["", "@", "@p:", "@:m", "@p", "@p/evil:m", "a b", "@unknown:m"])
async def test_invalid_reference_never_defaults_to_openrouter(setup_registry, reference):
    *_, legacy, registry, router, notices = setup_registry
    with pytest.raises(ValueError):
        registry.resolve(reference)
    with pytest.raises(OpenRouterError) as error:
        await ProviderRouter(registry, legacy).chat_text(model=reference, system="s", user="u", timeout_s=1, max_tokens=10)
    assert error.value.kind == "error"


@pytest.mark.parametrize("params", [
    {"kind": "bedrock", "key": "private-secret"},
    {"kind": "missing"}, {"kind": "openai", "enabled": "true"},
    {"kind": "openai_compatible", "base_url": "https://user:private-secret@example.com/v1"},
    {"kind": "openai_compatible", "base_url": "http://example.com/v1"},
    {"kind": "openai", "key": "private-secret\nheader"},
    {"kind": "openai", "headers": {"Authorization": "private-secret"}},
])
async def test_rpc_rejects_invalid_configuration_without_logging_secrets(setup_registry, params, caplog):
    *_, registry, router, notices = setup_registry
    with pytest.raises(RpcError) as error:
        await router.call("providers.add", params)
    assert error.value.code == INVALID_PARAMS
    assert "private-secret" not in str(error.value) + caplog.text
    assert notices == []


async def test_available_bedrock_is_advertised_and_google_contract_root_is_accepted(setup_registry):
    *_, registry, router, notices = setup_registry
    kinds = (await router.call("providers.kinds"))["kinds"]
    assert next(k for k in kinds if k["kind"] == "bedrock")["available"] is True
    google = (await router.call("providers.add", {"kind": "google", "base_url": "https://generativelanguage.googleapis.com", "key": "secret"}))["provider"]
    assert google["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai"
    await router.call("providers.update", {"id": google["id"], "enabled": False})
    assert registry.catalog_entries() == []
    assert notices == [("providers.changed", {})] * 2


async def test_remove_legacy_openrouter_clears_the_existing_key_and_list_entry(setup_registry):
    conn, keys, *_, registry, router, notices = setup_registry
    keys.set("legacy-secret")
    assert (await router.call("providers.list"))["providers"][0]["id"] == "openrouter"
    await router.call("providers.remove", {"id": "openrouter"})
    assert keys.get() is None
    assert (await router.call("providers.list")) == {"providers": []}
    provider = (await router.call("providers.add", {"kind": "openrouter", "key": "new-key"}))["provider"]
    assert provider["id"] == "openrouter" and keys.get() == "new-key"


async def test_vault_failures_do_not_leak_exception_text(setup_registry, caplog):
    conn, keys, vault, credential, http, legacy, *rest = setup_registry
    class BrokenVault:
        def get(self):
            raise RuntimeError("private-secret and https://user:password@example.com")
    registry = ProviderRegistry(conn, legacy, keys, credential=lambda _: BrokenVault(), http=http)
    router = Router()
    registry.register(router)
    with pytest.raises(RpcError) as error:
        await router.call("providers.add", {"kind": "openai", "key": "secret"})
    assert "private-secret" not in str(error.value) + caplog.text
    assert "password" not in str(error.value) + caplog.text
    await registry.aclose()


async def test_discovery_in_flight_cannot_restore_a_removed_provider(setup_registry):
    conn, keys, vault, credential, _, legacy, _, _, _ = setup_registry
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"data": [{"id": "old-model"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = ProviderRegistry(conn, legacy, keys, credential=credential, http=http)
        router = Router()
        registry.register(router)
        pid = (await router.call("providers.add", {"kind": "ollama"}))["provider"]["id"]
        request = asyncio.create_task(router.call("providers.models", {"id": pid}))
        await entered.wait()
        await router.call("providers.remove", {"id": pid})
        release.set()
        with pytest.raises(RpcError):
            await request
        assert registry.catalog_entries() == []
        assert (await router.call("providers.list")) == {"providers": []}
        await registry.aclose()
