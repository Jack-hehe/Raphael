"""Provider contracts exercised through fake HTTP and the public router only."""

import json

import httpx
import pytest

from raphael_core.brain.openrouter import OpenRouterError
from raphael_core.brain.providers.clients import CompatibleProvider, validate_base_url


async def test_provider_rpc_storage_resolution_and_legacy_migration():
    from raphael_core.brain.db import connect
    from raphael_core.brain.openrouter import OpenRouterClient
    from raphael_core.brain.providers.registry import ProviderRegistry, ProviderRouter
    from raphael_core.brain.secrets import MemoryKeyStore
    from raphael_core.brain.settings import SettingsStore
    from raphael_core.router import Router

    conn = connect(":memory:")
    keys, vault = MemoryKeyStore(), {}
    keys.set("legacy-key")
    settings = SettingsStore(conn)
    settings.set("agent_model", "openai/old")
    def credential(provider_id):
        return vault.setdefault(provider_id, MemoryKeyStore())
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "llama3.1:8b"}]})
        assert json.loads(request.content)["model"] == "llama3.1:8b"
        return reply("local answer", cost=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        legacy = OpenRouterClient(keys.get, http=http)
        registry = ProviderRegistry(conn, legacy, keys, credential=credential, http=http)
        router, notices = Router(), []
        router.set_notifier(lambda method, params: notices.append((method, params)))
        registry.register(router)
        listed = await router.call("providers.list")
        assert listed["providers"][0]["id"] == "openrouter"
        assert settings.get("agent_model") == "openai/old"
        provider = (await router.call("providers.add", {"kind": "ollama", "key": "private-secret"}))["provider"]
        pid = provider["id"]
        assert provider["has_key"] and "key" not in provider
        assert "private-secret" not in "".join(r[0] for r in conn.execute("SELECT value FROM settings"))
        assert (await router.call("providers.test", {"id": pid})) == {"ok": True, "models": 1}
        assert (await router.call("providers.models", {"id": pid}))["models"][0]["id"] == "llama3.1:8b"
        dispatcher = ProviderRouter(registry, legacy)
        assert registry.resolve("openai/old") == ("openrouter", "openai/old")
        assert registry.resolve(f"@{pid}:llama3.1:8b") == (pid, "llama3.1:8b")
        result = await dispatcher.chat_text(model=f"@{pid}:llama3.1:8b", system="s", user="u", timeout_s=1, max_tokens=10)
        assert result.model == f"@{pid}:llama3.1:8b" and result.text == "local answer"
        await router.call("providers.update", {"id": pid, "enabled": False})
        assert not dispatcher.available(f"@{pid}:llama3.1:8b")
        with pytest.raises(OpenRouterError):
            await dispatcher.chat_text(model=f"@{pid}:llama3.1:8b", system="s", user="u", timeout_s=1, max_tokens=10)
        await router.call("providers.remove", {"id": pid})
        assert vault[pid].get() is None
        assert notices == [("providers.changed", {})] * 3
        await registry.aclose()
    conn.close()


async def test_brain_catalog_and_settings_use_provider_references_without_legacy_key():
    from raphael_core.brain.db import connect
    from raphael_core.brain.openrouter import OpenRouterClient
    from raphael_core.brain.providers.registry import ProviderRegistry, ProviderRouter
    from raphael_core.brain.secrets import MemoryKeyStore
    from raphael_core.brain.service import Brain
    from raphael_core.router import Router

    def handler(request):
        if request.method == "GET":
            if request.url.host == "openrouter.ai":
                return httpx.Response(503)
            return httpx.Response(200, json={"data": [{"id": "local-model", "supported_parameters": ["tools"]}]})
        return reply('{"text":"Hello world."}')
    conn, keys = connect(":memory:"), MemoryKeyStore()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        legacy = OpenRouterClient(keys.get, http=http)
        registry = ProviderRegistry(conn, legacy, keys, credential=lambda _: keys, http=http)
        dispatcher = ProviderRouter(registry, legacy)
        brain, router = Brain(conn, dispatcher, keys, providers=registry), Router()
        brain.register(router)
        registry.register(router)
        pid = (await router.call("providers.add", {"kind": "ollama"}))["provider"]["id"]
        model = f"@{pid}:local-model"
        catalog = await router.call("models.catalog")
        entry = next(e for e in catalog["llm"] if e["id"] == model)
        assert entry["provider_id"] == pid and entry["provider_name"] == "Ollama"
        assert all("provider_id" in e for e in catalog["llm"])
        await router.call("settings.set", {"key": "cleanup_model", "value": model})
        await router.call("models.catalog")
        assert (await router.call("settings.get"))["settings"]["cleanup_model"] == model
        result = await router.call("brain.finalize_dictation", {"raw_text": "Hello world.", "audio_seconds": 1, "stt_ms": 1})
        assert result["fallback_reason"] != "no_key"
        assert any(e.id == model for e in brain.known_models())
        await router.call("providers.update", {"id": "openrouter", "enabled": False})
        disabled_catalog = await router.call("models.catalog")
        assert all(e["provider_id"] != "openrouter" for e in disabled_catalog["llm"])
        await brain.aclose()
        await registry.aclose()
    conn.close()


async def test_anthropic_tools_vision_and_json():
    from raphael_core.brain.providers.clients import AnthropicProvider

    seen = []
    def handler(request):
        assert request.headers["x-api-key"] == "secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.url.path == "/v1/messages"
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            assert body["system"] == "rules"
            assert body["messages"][0]["content"][1] == {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": "YWJj"}}
            assert body["tools"][0]["input_schema"] == {"type": "object"}
            content = [{"type": "tool_use", "id": "t1", "name": "look", "input": {"x": 1}}]
        elif len(seen) == 2:
            assert body["messages"][-1]["content"][0] == {
                "type": "tool_result", "tool_use_id": "t1", "content": "answer"}
            content = [{"type": "text", "text": "done"}]
        else:
            assert body["tool_choice"]["type"] == "tool"
            content = [{"type": "tool_use", "id": "json", "name": "result", "input": {"text": "ok"}}]
        return httpx.Response(200, json={"content": content, "model": "claude-test",
            "usage": {"input_tokens": 20, "output_tokens": 4}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AnthropicProvider("anthropic", "https://api.anthropic.com", lambda: "secret", http=http)
        args = dict(model="claude-test", fallbacks=[], tools=[{"type": "function", "function": {
            "name": "look", "parameters": {"type": "object"}}}], timeout_s=1, max_tokens=10)
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": [
            {"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}}]}]
        first = await client.chat_tools(messages=messages, **args)
        messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function",
            "function": {"name": "look", "arguments": first.calls[0].raw_arguments}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "answer"}]
        second = await client.chat_tools(messages=messages, **args)
        result = await client.complete_json(model="claude-test", fallbacks=[], system="rules", user="q",
            schema={"type": "object"}, schema_name="result", timeout_s=1, max_tokens=10)
    assert first.calls[0].arguments == {"x": 1}
    assert second.content == "done"
    assert result.data == {"text": "ok"}
    assert result.prompt_tokens == 20 and not result.usage_known


async def test_google_chat_json_tools_and_native_listing():
    def handler(request):
        if request.method == "GET":
            assert request.url.path == "/v1beta/models"
            assert request.headers["x-goog-api-key"] == "secret"
            return httpx.Response(200, json={"models": [{"name": "models/gemini-test", "displayName": "Gemini"}]})
        assert request.url.path == "/v1beta/openai/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        return reply('{"text":"ok"}')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider("google", "https://generativelanguage.googleapis.com/v1beta/openai", lambda: "secret", http=http)
        assert (await client.models())[0]["id"] == "gemini-test"
        result = await client.complete_json(model="gemini-test", fallbacks=[], system="s", user="u",
            schema={"type": "object"}, schema_name="r", timeout_s=1, max_tokens=10)
        turn = await client.chat_tools(model="gemini-test", fallbacks=[], messages=[], tools=[], timeout_s=1, max_tokens=10)
    assert result.data == {"text": "ok"}
    assert turn.content == '{"text":"ok"}'


def reply(content="hello", **usage):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, **usage},
    })


@pytest.mark.parametrize("url", [
    "https://user:secret@example.com/v1", "https://example.com/v1?key=secret",
    "https://example.com/#secret", "file:///tmp/key", "http://example.com/v1",
    "http://8.8.8.8/v1", "https:///missing",
])
def test_reject_unsafe_base_urls(url):
    with pytest.raises(ValueError, match="base URL"):
        validate_base_url(url)


@pytest.mark.parametrize("url", [
    "https://api.example.com/v1", "http://localhost:1234/v1/",
    "http://127.0.0.1:11434/v1", "http://[::1]:11434/v1", "http://192.168.1.3/v1",
])
def test_accept_https_and_local_http(url):
    assert validate_base_url(url) == url.rstrip("/")


async def test_compatible_json_and_usage_without_openrouter_options():
    def handler(request):
        assert str(request.url) == "https://api.example.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        body = json.loads(request.content)
        assert body["model"] == "my-model"
        assert not {"provider", "models", "reasoning"} & body.keys()
        assert body["response_format"]["type"] == "json_schema"
        return reply('{"text":"done"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider("openai_compatible", "https://api.example.com/v1", lambda: "secret", http=http)
        result = await client.complete_json(model="my-model", fallbacks=["openai/fallback"],
            system="system", user="user", schema={"type": "object"}, schema_name="result",
            timeout_s=1, max_tokens=100)
    assert result.data == {"text": "done"}
    assert (result.prompt_tokens, result.completion_tokens, result.cost_usd) == (12, 3, 0)
    assert result.usage_known is False


@pytest.mark.parametrize("kind", ["ollama", "openai", "google"])
async def test_compatible_tool_roundtrip(kind):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{
                "id": "c1", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"x"}'},
            }]}}], "usage": {"cost": 0.1}})
        assert body["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "found"}
        return reply("done", cost=0.2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider(kind, "http://localhost:11434/v1", lambda: None if kind == "ollama" else "secret", http=http)
        args = dict(model="llama3.1:8b", fallbacks=[], tools=[], timeout_s=1, max_tokens=100)
        first = await client.chat_tools(messages=[{"role": "user", "content": "find"}], **args)
        second = await client.chat_tools(messages=[{"role": "assistant", "tool_calls": [{
            "id": first.calls[0].id, "type": "function", "function": {"name": first.calls[0].name,
            "arguments": first.calls[0].raw_arguments}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "found"}], **args)
    assert first.calls[0].arguments == {"q": "x"}
    assert second.content == "done" and second.usage_known


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (429, "rate_limit"), (408, "timeout"), (500, "error")])
@pytest.mark.parametrize("native", [False, True])
async def test_compatible_errors_are_safe(status, kind, native):
    from raphael_core.brain.providers.clients import AnthropicProvider
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json={"error": "secret"}))) as http:
        cls = AnthropicProvider if native else CompatibleProvider
        client = cls("anthropic" if native else "openai", "https://api.example.com/v1", lambda: "secret", http=http)
        with pytest.raises(OpenRouterError) as caught:
            await client.chat_text(model="m", system="s", user="u", timeout_s=1, max_tokens=10)
    assert caught.value.kind == kind
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("kind,url,header", [
    ("openai", "https://api.openai.com/v1", "authorization"),
    ("azure_openai", "https://resource.example/openai/deployments/chat", "api-key"),
    ("portkey", "https://api.portkey.ai/v1", "x-portkey-api-key"),
    ("vercel", "https://ai-gateway.vercel.sh/v1", "authorization"),
    ("nebius", "https://api.studio.nebius.com/v1", "authorization"),
    ("litellm", "http://localhost:4000/v1", "authorization"),
    ("bifrost", "http://localhost:8080/openai/v1", "authorization"),
    ("groq", "https://api.groq.com/openai/v1", "authorization"),
    ("deepseek", "https://api.deepseek.com/v1", "authorization"),
    ("mistral", "https://api.mistral.ai/v1", "authorization"),
    ("lmstudio", "http://localhost:1234/v1", "authorization"),
])
async def test_presets_headers_and_azure_query(kind, url, header):
    def handler(request):
        assert request.headers[header] == ("Bearer secret" if header == "authorization" else "secret")
        assert request.url.params.get("api-version") == ("2024-10-21" if kind == "azure_openai" else None)
        return reply()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider(kind, url, lambda: "secret", http=http,
            api_version="2024-10-21" if kind == "azure_openai" else "")
        assert (await client.chat_text(model="m", system="s", user="u", timeout_s=1, max_tokens=10)).text == "hello"


@pytest.mark.parametrize("error,expected", [(httpx.ConnectError, "offline"), (httpx.ReadTimeout, "timeout")])
@pytest.mark.parametrize("native", [False, True])
async def test_transport_failure_kinds(native, error, expected):
    from raphael_core.brain.providers.clients import AnthropicProvider
    def fail(request):
        raise error("private-secret-url", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        cls = AnthropicProvider if native else CompatibleProvider
        client = cls("anthropic" if native else "openai", "https://example.com", lambda: "secret", http=http)
        with pytest.raises(OpenRouterError) as caught:
            await client.chat_text(model="m", system="s", user="u", timeout_s=1, max_tokens=10)
    assert caught.value.kind == expected and "private-secret-url" not in str(caught.value)


@pytest.mark.parametrize("kind", ["openai", "groq"])
async def test_multipart_transcription(kind):
    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["content-type"].startswith("multipart/form-data;")
        assert b'filename="audio.wav"' in request.content and b"abc" in request.content
        assert b'name="language"' in request.content and b'name="prompt"' in request.content
        return httpx.Response(200, json={"text": "heard", "duration": 1.2})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider(kind, "https://example.com/v1", lambda: "secret", http=http)
        result = await client.transcribe(model="whisper-1", audio_b64="YWJj", language="en", hints=["Raphael"], timeout_s=1)
    assert result.text == "heard" and result.seconds == 1.2 and not result.usage_known


async def test_unsupported_transcription_and_plugins_make_no_request():
    def fail(request):
        pytest.fail("Unsupported operations must fail before HTTP")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        client = CompatibleProvider("google", "https://example.com/v1beta/openai", lambda: "secret", http=http)
        with pytest.raises(OpenRouterError, match="transcription"):
            await client.transcribe(model="gemini", audio_b64="YWJj", language=None, hints=[], timeout_s=1)
        with pytest.raises(OpenRouterError, match="plugins"):
            await client.chat_text(model="gemini", system="s", user="u", timeout_s=1, max_tokens=10, plugins=[{"id": "web"}])


async def test_discovered_prices_estimate_cost_and_flag_it_known():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "m", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]})
        return reply()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider("openai_compatible", "https://example.com/v1", lambda: None, http=http)
        await client.models()
        result = await client.chat_text(model="m", system="s", user="u", timeout_s=1, max_tokens=10)
    assert result.cost_usd == pytest.approx(0.000018) and result.usage_known


@pytest.mark.parametrize("kind", ["google", "anthropic"])
async def test_native_discovery_paginates_and_excludes_non_generation_google_models(kind):
    from raphael_core.brain.providers.clients import AnthropicProvider
    calls = []
    def handler(request):
        calls.append(request)
        if kind == "google":
            assert request.url.params.get("pageToken") == (None if len(calls) == 1 else "next token")
            data = {"models": [{"name": "models/gemini-test", "supportedGenerationMethods": ["generateContent"]},
                               {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]}]}
            if len(calls) == 1:
                data["nextPageToken"] = "next token"
        else:
            assert request.url.params.get("after_id") == (None if len(calls) == 1 else "claude-test")
            data = {"data": [{"id": "claude-test"}], "has_more": len(calls) == 1, "last_id": "claude-test"}
        return httpx.Response(200, json=data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        cls = AnthropicProvider if kind == "anthropic" else CompatibleProvider
        client = cls(kind, "https://example.com/v1beta/openai" if kind == "google" else "https://example.com", lambda: "secret", http=http)
        models = await client.models()
    assert len(calls) == 2
    assert len(models) == 1
    assert models[0]["vision"] and models[0]["tools"]


async def test_openai_discovery_distinguishes_transcription_from_chat():
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "whisper-1"}, {"id": "gpt-4o"}, {"id": "text-embedding-3-small"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider("openai", "https://example.com/v1", lambda: "secret", http=http)
        models = await client.models()
    chat = next(m for m in models if m["id"] == "gpt-4o")
    audio = next(m for m in models if m["id"] == "whisper-1")
    assert chat["vision"] and chat["tools"]
    assert audio["audio_in"]
    assert "text-embedding-3-small" not in {m["id"] for m in models}


@pytest.mark.parametrize("kind,model", [("deepseek", "deepseek-chat"), ("openai", "gpt-3.5-turbo")])
async def test_json_object_dialects_include_the_schema_in_the_prompt(kind, model):
    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert "JSON" in body["messages"][0]["content"]
        assert '"required": ["text"]' in body["messages"][0]["content"]
        return reply('{"text":"ok"}')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider(kind, "https://example.com/v1", lambda: "secret", http=http)
        result = await client.complete_json(model=model, fallbacks=[], system="s", user="u",
            schema={"type": "object", "required": ["text"]}, schema_name="r", timeout_s=1, max_tokens=10)
    assert result.data == {"text": "ok"}


@pytest.mark.parametrize("kind", ["openai", "azure_openai"])
async def test_modern_openai_models_use_completion_token_limit(kind):
    def handler(request):
        body = json.loads(request.content)
        assert "max_tokens" not in body and body["max_completion_tokens"] == 2148
        assert body["reasoning_effort"] == "low"
        return reply()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider(kind, "https://example.com/v1", lambda: "secret", http=http)
        await client.chat_text(model="gpt-5.1", system="s", user="u", timeout_s=1, max_tokens=100)


async def test_other_provider_auth_failure_does_not_change_openrouter_status():
    from raphael_core.brain.db import connect
    from raphael_core.brain.openrouter import OpenRouterClient
    from raphael_core.brain.providers.registry import ProviderRegistry, ProviderRouter
    from raphael_core.brain.secrets import MemoryKeyStore
    from raphael_core.brain.service import Brain
    from raphael_core.router import Router

    def handler(request):
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"label": "legacy"}})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
        if request.url.host == "openrouter.ai":
            return reply('{"text":"Hello."}')
        return httpx.Response(401, json={"error": "wrong key"})
    conn, keys, other = connect(":memory:"), MemoryKeyStore(), MemoryKeyStore()
    keys.set("legacy-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        legacy = OpenRouterClient(keys.get, http=http)
        registry = ProviderRegistry(conn, legacy, keys, credential=lambda _: other, http=http)
        brain = Brain(conn, ProviderRouter(registry, legacy), keys, providers=registry)
        router = Router()
        brain.register(router)
        registry.register(router)
        pid = (await router.call("providers.add", {"kind": "openai", "key": "bad-secret"}))["provider"]["id"]
        await router.call("providers.models", {"id": pid})
        await router.call("settings.set", {"key": "cleanup_model", "value": f"@{pid}:gpt-4o"})
        assert (await router.call("status.check_openrouter"))["state"] == "ok"
        await router.call("brain.finalize_dictation", {"raw_text": "hello", "audio_seconds": 1, "stt_ms": 1})
        assert (await router.call("status.get"))["openrouter"]["state"] == "ok"
        assert next(p for p in (await router.call("providers.list"))["providers"] if p["id"] == pid)["status"] == "auth"
        await brain.aclose()
        await registry.aclose()
    conn.close()


@pytest.mark.parametrize("fixed", [False, True])
async def test_azure_resource_and_deployment_urls_discover_and_call(fixed):
    def handler(request):
        assert request.headers["api-key"] == "secret"
        assert request.url.params["api-version"] == "2024-10-21"
        if request.method == "GET":
            assert request.url.path == "/openai/models"
            return httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
        assert request.url.path == "/openai/deployments/chat/chat/completions"
        return reply()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        base = "https://resource.example" + ("/openai/deployments/chat" if fixed else "")
        client = CompatibleProvider("azure_openai", base, lambda: "secret", http=http, api_version="2024-10-21")
        models = await client.models()
        assert models[0]["id"] == ("chat" if fixed else "gpt-4o")
        await client.chat_text(model="chat", system="s", user="u", timeout_s=1, max_tokens=100)


async def test_ollama_discovery_reads_native_model_capabilities():
    def handler(request):
        if request.method == "GET":
            assert request.url.path == "/v1/models"
            return httpx.Response(200, json={"data": [{"id": "qwen3:8b"}]})
        assert request.url.path == "/api/show"
        assert json.loads(request.content) == {"model": "qwen3:8b"}
        return httpx.Response(200, json={"capabilities": ["completion", "tools", "vision"],
            "model_info": {"qwen3.context_length": 32768}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CompatibleProvider("ollama", "http://localhost:11434/v1", lambda: None, http=http)
        model = (await client.models())[0]
    assert model["tools"] and model["vision"] and model["context"] == 32768
