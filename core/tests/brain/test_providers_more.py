"""Extended provider contracts exercised through the registry and HTTP boundary."""

import httpx
import pytest

from raphael_core.brain.db import connect
from raphael_core.brain.openrouter import OpenRouterClient
from raphael_core.brain.providers.registry import ProviderRegistry
from raphael_core.brain.secrets import MemoryKeyStore


async def test_extended_presets_have_ui_metadata_and_local_fields():
    conn = connect(":memory:")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(), http=http)
        kinds = {row["kind"]: row for row in (await registry.kinds({}))["kinds"]}
        expected = "xai fireworks novita deepinfra together perplexity cerebras sambanova hyperbolic nvidia huggingface moonshot moonshot_cn dashscope dashscope_cn zai minimax minimax_cn stepfun arcee gmi ollama_cloud meta opencode_zen kilocode chutes siliconflow baidu_qianfan volcengine_ark venice featherless inference_net lambda scaleway cohere ai21 vertex bedrock vllm sglang llamacpp jan koboldcpp localai text_generation_webui".split()
        assert set(expected) <= kinds.keys()
        for row in kinds.values():
            assert row["group"] in ("official", "gateway", "local")
            assert row["docs_url"].startswith("https://")
            assert type(row["verified"]) is bool
            assert row["auth_style"]
            assert row["url_confidence"]
            assert row["local_context_hint"] == (row["group"] == "local")
            if row["group"] == "local":
                assert "base_url" in row["fields"]
        assert kinds["bedrock"]["available"]
        assert set(kinds["bedrock"]["fields"]) == {"key_id", "secret", "region"}
        assert set(kinds["vertex"]["fields"]) == {"service_account", "project", "location"}
        await registry.aclose()
    conn.close()

import base64
import json
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def service_account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    account = json.dumps({"client_email": "test@example.iam.gserviceaccount.com", "private_key": key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()})
    return key, account


async def test_vertex_jwt_exchange_and_cached_access_token():
    from raphael_core.brain.providers.vertex import VertexProvider
    key, account = service_account()
    requests = []
    now = [1000.0]
    def handler(request):
        requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            from urllib.parse import parse_qs
            form = parse_qs(request.content.decode())
            header, claims, signature = form["assertion"][0].split(".")
            decode = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
            key.public_key().verify(decode(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
            payload = json.loads(decode(claims))
            assert payload == {"iss": "test@example.iam.gserviceaccount.com", "scope": "https://www.googleapis.com/auth/cloud-platform", "aud": "https://oauth2.googleapis.com/token", "iat": 1000 if len(requests) == 1 else 5000, "exp": 4600 if len(requests) == 1 else 8600}
            return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 3600, "token_type": "Bearer"})
        assert str(request.url) == "https://us-central1-aiplatform.googleapis.com/v1/projects/test-project/locations/us-central1/endpoints/openapi/chat/completions"
        assert request.headers["authorization"] == "Bearer fake-token"
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VertexProvider("vertex", "https://us-central1-aiplatform.googleapis.com/v1/projects/test-project/locations/us-central1/endpoints/openapi", lambda: account, http=http, clock=lambda: now[0])
        for _ in range(2):
            assert (await client.chat_text(model="google/gemini-2.5-flash", system="s", user="u", timeout_s=1, max_tokens=10)).text == "answer"
        assert len(requests) == 3
        now[0] = 5000
        await client.chat_text(model="google/gemini-2.5-flash", system="s", user="u", timeout_s=1, max_tokens=10)
        assert len(requests) == 5
        models = await client.models()
        assert models and all(row["discovery"] == "static" for row in models)


@pytest.mark.parametrize("query,content_type", [
    ("Action=ListUsers&Version=2010-05-08", "application/x-www-form-urlencoded; charset=utf-8"),
    ("Version=2010-05-08&Action=ListUsers", "  application/x-www-form-urlencoded;  charset=utf-8  "),
])
def test_sigv4_aws_iam_documented_vector(query, content_type):
    from raphael_core.brain.providers.bedrock import sign_v4
    headers = sign_v4("GET", "https://iam.amazonaws.com/?" + query, b"", "AKIDEXAMPLE",
                      "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "us-east-1", service="iam",
                      now=datetime(2015, 8, 30, 12, 36, 0, tzinfo=timezone.utc),
                      headers={"Content-Type": content_type})
    assert headers["Authorization"].endswith("Signature=5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7")
    assert "SignedHeaders=content-type;host;x-amz-date" in headers["Authorization"]


async def test_bedrock_converse_tools_images_usage_and_discovery():
    from raphael_core.brain.providers.bedrock import BedrockProvider
    requests = []
    tool = {"type": "function", "function": {"name": "weather", "description": "Weather", "parameters": {"type": "object", "properties": {}}}}
    def handler(request):
        requests.append(request)
        assert "Credential=access/" in request.headers["authorization"]
        if request.method == "GET":
            assert request.url.host == "bedrock.us-east-1.amazonaws.com"
            return httpx.Response(200, json={"modelSummaries": [
                {"modelId": "anthropic.claude-test-v1:0", "modelName": "Claude", "inputModalities": ["TEXT", "IMAGE"], "outputModalities": ["TEXT"]},
                {"modelId": "embedding", "inputModalities": ["TEXT"], "outputModalities": ["EMBEDDING"]}]})
        assert request.url.raw_path == b"/model/anthropic.claude-test-v1%3A0/converse"
        body = json.loads(request.content)
        assert body["system"] == [{"text": "system"}]
        assert body["inferenceConfig"]["maxTokens"] == 20
        assert body["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {"json": tool["function"]["parameters"]}
        assert body["messages"][0]["content"][1] == {"image": {"format": "png", "source": {"bytes": "aW1hZ2U="}}}
        assert body["messages"][1]["content"][0]["toolUse"] == {"toolUseId": "call1", "name": "weather", "input": {}}
        assert body["messages"][2]["content"][0]["toolResult"] == {"toolUseId": "call1", "content": [{"text": "sunny"}]}
        return httpx.Response(200, json={"output": {"message": {"role": "assistant", "content": [{"text": "answer"}, {"toolUse": {"toolUseId": "call2", "name": "weather", "input": {}}}]}}, "stopReason": "tool_use", "usage": {"inputTokens": 5, "outputTokens": 2}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BedrockProvider("bedrock", "https://bedrock-runtime.us-east-1.amazonaws.com", lambda: json.dumps({"key_id": "access", "secret": "secret"}), region="us-east-1", http=http)
        turn = await client.chat_tools(model="anthropic.claude-test-v1:0", fallbacks=[], messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": [{"type": "text", "text": "question"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}}]},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "weather", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call1", "content": "sunny"}], tools=[tool], timeout_s=1, max_tokens=20)
        assert turn.content == "answer" and turn.calls[0].name == "weather"
        assert turn.prompt_tokens == 5 and turn.completion_tokens == 2 and not turn.usage_known
        rows = await client.models()
        assert [row["id"] for row in rows] == ["anthropic.claude-test-v1:0"]
        assert rows[0]["vision"] and rows[0]["price_in"] is None

from raphael_core.brain.openrouter import OpenRouterError
from raphael_core.brain.providers.registry import ProviderRouter
from raphael_core.brain.settings import SettingsStore


@pytest.mark.parametrize("purpose", ["agent", "cleanup", "router", "vision", "pdf", "stt", "memory"])
def test_fallback_settings_roundtrip_and_validation(purpose):
    conn = connect(":memory:")
    settings = SettingsStore(conn)
    assert settings.get(purpose + "_fallbacks") == []
    settings.set(purpose + "_fallbacks", ["@p1:model:tag", "openai/gpt-4o"])
    assert SettingsStore(conn).all()[purpose + "_fallbacks"] == ["@p1:model:tag", "openai/gpt-4o"]
    for invalid in (["a"] * 4, [""], ["bad ref"], ["@p:"], [True], "model"):
        with pytest.raises((TypeError, ValueError)):
            settings.set(purpose + "_fallbacks", invalid)
    conn.close()


@pytest.mark.parametrize("status,explicit,expected", [(429, True, "answer"), (503, True, "answer"),
    (401, True, "answer"), (401, False, "auth"), (400, True, "error"), (429, False, "rate_limit")])
async def test_router_fallback_policy_and_secret_free_logs(status, explicit, expected, caplog):
    conn, vault, requests = connect(":memory:"), {}, []
    settings = SettingsStore(conn)
    def handler(request):
        requests.append(request)
        if request.url.host == "api.openai.com":
            return httpx.Response(status, json={"error": "private-secret"})
        return httpx.Response(200, json={"model": "backup", "choices": [{"message": {"content": "answer"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: "secret", http=http), MemoryKeyStore(),
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        primary = (await registry.add({"kind": "openai", "key": "private-secret"}))["provider"]["id"]
        backup = (await registry.add({"kind": "groq", "key": "private-secret"}))["provider"]["id"]
        if explicit:
            settings.set("agent_fallbacks", [f"@{backup}:backup"])
        client = ProviderRouter(registry, registry.openrouter)
        args = dict(model=f"@{primary}:primary", system="private-prompt", user="private-text", timeout_s=1, max_tokens=10)
        if expected == "answer":
            result = await client.chat_text(**args)
            assert result.text == "answer" and result.model == f"@{backup}:backup"
            assert len(requests) == 2
            assert "Provider fallback" in caplog.text
        else:
            with pytest.raises(OpenRouterError) as error:
                await client.chat_text(**args)
            assert error.value.kind == expected and len(requests) == 1
        assert "private-secret" not in caplog.text and "private-prompt" not in caplog.text and "private-text" not in caplog.text
        await registry.aclose()
    conn.close()


async def test_fallback_exhaustion_reports_last_error_kind():
    conn, vault = connect(":memory:"), {}
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(429 if len(requests) == 1 else 504, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: "secret", http=http), MemoryKeyStore(),
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        primary = (await registry.add({"kind": "openai", "key": "secret"}))["provider"]["id"]
        backup = (await registry.add({"kind": "groq", "key": "secret"}))["provider"]["id"]
        SettingsStore(conn).set("cleanup_fallbacks", [f"@{backup}:backup"])
        client = ProviderRouter(registry, registry.openrouter).for_purpose("cleanup")
        with pytest.raises(OpenRouterError) as error:
            await client.chat_text(model=f"@{primary}:primary", system="s", user="u", timeout_s=1, max_tokens=10)
        assert error.value.kind == "error" and error.value.status == 504
        assert len(requests) == 2
        await registry.aclose()
    conn.close()


async def test_local_context_warning_follows_selected_agent_model():
    conn = connect(":memory:")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": [
            {"id": "small", "context_length": 8192}, {"id": "large", "context_length": 131072}]}))) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(), http=http)
        pid = (await registry.add({"kind": "vllm"}))["provider"]["id"]
        SettingsStore(conn).set("agent_model", f"@{pid}:small")
        rows = (await registry.models({"id": pid}))["models"]
        assert rows[0]["context_warning"] is True and rows[1]["context_warning"] is False
        SettingsStore(conn).set("agent_model", f"@{pid}:large")
        assert not any(row["context_warning"] for row in (await registry.models({"id": pid}))["models"])
        await registry.aclose()
    conn.close()

async def test_native_credentials_are_vault_only_and_endpoints_are_derived():
    conn, vault = connect(":memory:"), {}
    _, account = service_account()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(),
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        vertex = (await registry.add({"kind": "vertex", "project": "my-project", "location": "us-central1", "service_account": account}))["provider"]
        bedrock = (await registry.add({"kind": "bedrock", "region": "us-east-1", "key_id": "access-id", "secret": "private-secret"}))["provider"]
        assert vertex["has_key"] and bedrock["has_key"]
        assert vertex["base_url"].endswith("/projects/my-project/locations/us-central1/endpoints/openapi")
        assert bedrock["base_url"] == "https://bedrock-runtime.us-east-1.amazonaws.com"
        assert type(registry.client(vertex["id"])).__name__ == "VertexProvider"
        assert type(registry.client(bedrock["id"])).__name__ == "BedrockProvider"
        saved = "".join(row[0] for row in conn.execute("SELECT value FROM settings")) + json.dumps(await registry.list({}))
        assert "private-secret" not in saved and "access-id" not in saved and "private_key" not in saved
        await registry.update({"id": bedrock["id"], "secret": "replacement"})
        assert json.loads(vault[bedrock["id"]].get()) == {"key_id": "access-id", "secret": "replacement"}
        await registry.remove({"id": vertex["id"]})
        assert vault[vertex["id"]].get() is None
        for params in ({"kind": "vertex", "project": "bad/path", "location": "us-central1"},
                       {"kind": "bedrock", "region": "us-east-1.evil"},
                       {"kind": "vertex", "project": "p", "location": "us-central1", "service_account": "[]"}):
            with pytest.raises(ValueError):
                await registry.add(params)
        await registry.aclose()
    conn.close()


def test_large_service_account_credential_fits_windows_vault_and_clears_all_parts(monkeypatch):
    from raphael_core.brain.providers.secrets import ProviderCredentialStore
    import keyring
    values = {}
    def set_password(service, username, password):
        assert len(password.encode("utf-16-le")) <= 2560
        values[(service, username)] = password
    def delete_password(service, username):
        values.pop((service, username), None)
    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: values.get((service, username)))
    monkeypatch.setattr(keyring, "delete_password", delete_password)
    store = ProviderCredentialStore("provider:test")
    _, account = service_account()
    store.set(account)
    assert store.get() == account and len(values) > 1
    store.set("replacement")
    assert store.get() == "replacement" and len(values) == 1
    store.clear()
    assert not values

@pytest.mark.parametrize("failure", ["timeout", "offline", "hang"])
async def test_transport_failures_and_per_attempt_deadlines_try_the_configured_fallback(failure):
    import asyncio
    conn, vault, seen = connect(":memory:"), {}, []
    async def handler(request):
        seen.append(request.url.host)
        if len(seen) == 1:
            if failure == "hang":
                await asyncio.Event().wait()
            if failure == "timeout":
                raise httpx.ReadTimeout("private-secret", request=request)
            raise httpx.ConnectError("private-secret", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(),
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        pid = (await registry.add({"kind": "openai", "key": "secret"}))["provider"]["id"]
        backup = (await registry.add({"kind": "groq", "key": "secret"}))["provider"]["id"]
        SettingsStore(conn).set("agent_fallbacks", [f"@{backup}:backup"])
        result = await ProviderRouter(registry, registry.openrouter).chat_text(model=f"@{pid}:primary", system="s", user="u", timeout_s=0.05, max_tokens=10)
        assert result.text == "answer" and len(seen) == 2
        assert registry.view(pid)["status"] == "offline"
        await registry.aclose()
    conn.close()


@pytest.mark.parametrize("status", [200, 403])
async def test_content_refusal_never_tries_another_provider(status):
    conn, vault, requests = connect(":memory:"), {}, []
    def handler(request):
        requests.append(request)
        if status == 403:
            return httpx.Response(403, json={"error": {"code": "content_filter", "message": "private-refusal"}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "content_filter", "message": {"content": None, "refusal": "private-refusal"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(),
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        pid = (await registry.add({"kind": "openai", "key": "secret"}))["provider"]["id"]
        backup = (await registry.add({"kind": "groq", "key": "secret"}))["provider"]["id"]
        SettingsStore(conn).set("agent_fallbacks", [f"@{backup}:backup"])
        with pytest.raises(OpenRouterError) as error:
            await ProviderRouter(registry, registry.openrouter).chat_text(model=f"@{pid}:primary", system="s", user="u", timeout_s=1, max_tokens=10)
        assert error.value.kind == "refusal" and len(requests) == 1
        assert "private-refusal" not in str(error.value)
        await registry.aclose()
    conn.close()


@pytest.mark.parametrize("primary_key", [None, "rejected"])
async def test_brain_cleanup_rpc_uses_its_fallback_setting_without_an_openrouter_key(primary_key):
    from raphael_core.brain.service import Brain
    from raphael_core.router import Router
    conn, vault, requests = connect(":memory:"), {}, []
    def handler(request):
        requests.append(request)
        if request.url.host == "openrouter.ai":
            return httpx.Response(401, json={})
        assert request.url.host == "api.groq.com"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"text":"Hello world."}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        keys = MemoryKeyStore()
        if primary_key:
            keys.set(primary_key)
        registry = ProviderRegistry(conn, OpenRouterClient(keys.get, http=http), keys,
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        brain, rpc = Brain(conn, ProviderRouter(registry, registry.openrouter), keys, providers=registry), Router()
        brain.register(rpc)
        registry.register(rpc)
        pid = (await rpc.call("providers.add", {"kind": "groq", "key": "secret"}))["provider"]["id"]
        await rpc.call("settings.set", {"key": "cleanup_fallbacks", "value": [f"@{pid}:backup"]})
        assert (await rpc.call("settings.get"))["settings"]["cleanup_fallbacks"] == [f"@{pid}:backup"]
        await rpc.call("status.get", {})
        result = await rpc.call("brain.finalize_dictation", {"raw_text": "Hello world", "audio_seconds": 1, "stt_ms": 1})
        assert result["cleaned"]
        assert (await rpc.call("history.list", {}))["items"][0]["model"] == f"@{pid}:backup"
        assert len([r for r in requests if r.url.host == "api.groq.com"]) == 1
        assert (await rpc.call("status.get", {}))["openrouter"]["state"] != "ok"
        await brain.aclose()
        await registry.aclose()
    conn.close()


def test_large_credentials_use_the_real_store_on_the_in_memory_keyring(memory_keyring):
    from raphael_core.brain.providers.secrets import ProviderCredentialStore
    _, account = service_account()
    store = ProviderCredentialStore("provider:test")
    store.set(account)
    assert store.get() == account
    store.set(account.replace("test@example", "other@example"))
    assert "other@example" in store.get()
    store.clear()
    assert not memory_keyring.credentials

@pytest.mark.parametrize("purpose", ["agent", "cleanup", "router", "vision", "pdf", "stt", "memory"])
async def test_each_purpose_uses_only_its_ordered_chain(purpose):
    conn, vault, seen = connect(":memory:"), {}, []
    def handler(request):
        seen.append(json.loads(request.content)["model"])
        if len(seen) == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(),
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        pid = (await registry.add({"kind": "openai", "key": "secret"}))["provider"]["id"]
        settings = SettingsStore(conn)
        settings.set(purpose + "_fallbacks", [f"@{pid}:backup"])
        if purpose in ("router", "pdf", "memory"):
            settings.set(purpose + "_model", f"@{pid}:override")
        client = ProviderRouter(registry, registry.openrouter).for_purpose(purpose)
        assert client.available(f"@{pid}:primary")
        await client.chat_text(model=f"@{pid}:primary", system="s", user="u", timeout_s=1, max_tokens=10)
        assert seen == ["override" if purpose in ("router", "pdf", "memory") else "primary", "backup"]
        await registry.aclose()
    conn.close()


async def test_vllm_reports_max_model_len_for_context_warning():
    conn = connect(":memory:")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": [
            {"id": "small", "max_model_len": 32768}]}))) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(), http=http)
        pid = (await registry.add({"kind": "vllm"}))["provider"]["id"]
        SettingsStore(conn).set("agent_model", f"@{pid}:small")
        row = (await registry.models({"id": pid}))["models"][0]
        assert row["context"] == 32768 and row["context_warning"]
        await registry.aclose()
    conn.close()


async def test_bedrock_json_schema_uses_a_forced_tool_and_filters_refusals():
    from raphael_core.brain.providers.bedrock import BedrockProvider
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["toolConfig"]["toolChoice"] == {"tool": {"name": "result"}}
        assert body["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {"json": schema}
        return httpx.Response(200, json={"stopReason": "tool_use" if len(requests) == 1 else "guardrail_intervened",
            "output": {"message": {"content": [{"toolUse": {"toolUseId": "call", "name": "result", "input": {"answer": "yes"}}}]}}, "usage": {"inputTokens": 2, "outputTokens": 1}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BedrockProvider("bedrock", "https://bedrock-runtime.us-east-1.amazonaws.com", lambda: '{"key_id":"id","secret":"secret"}', region="us-east-1", http=http)
        args = dict(model="test", fallbacks=[], system="s", user="u", schema=schema, schema_name="result", timeout_s=1, max_tokens=10)
        result = await client.complete_json(**args)
        assert result.data == {"answer": "yes"} and not result.usage_known
        with pytest.raises(OpenRouterError) as error:
            await client.complete_json(**args)
        assert error.value.kind == "refusal"


@pytest.mark.parametrize("status,kind", [(400, "auth"), (429, "rate_limit"), (503, "error")])
async def test_vertex_token_failures_are_typed_and_do_not_echo_credentials(status, kind, caplog):
    from raphael_core.brain.providers.vertex import VertexProvider
    _, account = service_account()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json={"error": "private-secret"}))) as http:
        client = VertexProvider("vertex", "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/endpoints/openapi", lambda: account, http=http)
        with pytest.raises(OpenRouterError) as error:
            await client.chat_text(model="google/gemini-2.5-flash", system="s", user="u", timeout_s=1, max_tokens=10)
        assert error.value.kind == kind
        assert "private-secret" not in str(error.value) + caplog.text and "PRIVATE KEY" not in caplog.text


def test_vault_chunk_write_failure_preserves_previous_value_and_removes_new_parts(monkeypatch, memory_keyring):
    from raphael_core.brain.providers.secrets import ProviderCredentialStore
    import keyring
    _, account = service_account()
    store = ProviderCredentialStore("provider:test")
    store.set(account)
    original = dict(memory_keyring.credentials)
    write = keyring.set_password
    failed = [False]
    def fail_once(service, username, password):
        if username.endswith(":1") and not failed[0]:
            failed[0] = True
            raise RuntimeError("private-secret")
        return write(service, username, password)
    monkeypatch.setattr(keyring, "set_password", fail_once)
    with pytest.raises(RuntimeError):
        store.set(account.replace("test@example", "other@example"))
    assert store.get() == account
    assert memory_keyring.credentials == original
    store.clear()

async def test_explicitly_empty_fallback_setting_disables_legacy_openrouter_defaults():
    conn, requests, keys = connect(":memory:"), [], MemoryKeyStore()
    keys.set("secret")
    def handler(request):
        requests.append(request)
        return httpx.Response(429, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        legacy = OpenRouterClient(keys.get, http=http)
        registry = ProviderRegistry(conn, legacy, keys, http=http)
        SettingsStore(conn).set("cleanup_fallbacks", [])
        with pytest.raises(OpenRouterError) as error:
            await ProviderRouter(registry, legacy).for_purpose("cleanup").complete_json(model="primary", fallbacks=["legacy-backup"], system="s", user="u", schema={}, schema_name="result", timeout_s=1, max_tokens=10)
        assert error.value.kind == "rate_limit" and len(requests) == 1
        await registry.aclose()
    conn.close()

async def test_router_purpose_configuration_bypasses_jev_and_reaches_its_fallback():
    from raphael_core.agent.routing import Router
    from raphael_core.agent.ledger import Ledger
    from raphael_core.brain.spend import SpendTracker
    conn, vault, seen = connect(":memory:"), {}, []
    def handler(request):
        seen.append(request)
        assert request.url.host == "api.groq.com" and request.url.path.endswith("/chat/completions")
        answer = {"route": "chat", "command": "none", "needs_weather": False, "needs_search": False, "needs_time": False, "target": ""}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(),
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        pid = (await registry.add({"kind": "groq", "key": "secret"}))["provider"]["id"]
        SettingsStore(conn).set("router_fallbacks", [f"@{pid}:backup"])
        router = Router(ProviderRouter(registry, registry.openrouter))
        decision = await router.classify("hello", "openai/gpt-6-luna", Ledger(SpendTracker(conn)))
        assert decision.route == "chat" and len(seen) == 1
        await registry.aclose()
    conn.close()


async def test_vision_and_pdf_scans_use_distinct_chains():
    from raphael_core.services.vision import Vision
    from raphael_core.agent.ledger import Ledger
    from raphael_core.brain.spend import SpendTracker
    conn, vault, seen = connect(":memory:"), {}, []
    def handler(request):
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(),
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        pid = (await registry.add({"kind": "groq", "key": "secret"}))["provider"]["id"]
        settings = SettingsStore(conn)
        settings.set("vision_fallbacks", [f"@{pid}:vision-backup"])
        settings.set("pdf_fallbacks", [f"@{pid}:pdf-backup"])
        vision = Vision(ProviderRouter(registry, registry.openrouter), lambda: ("openai/gpt-6-luna", None))
        ledger = Ledger(SpendTracker(conn))
        assert (await vision.look(["data:image/png;base64,aW1hZ2U="], "question", ledger))["text"] == "answer"
        assert (await vision.for_purpose("pdf").look(["data:image/png;base64,aW1hZ2U="], "question", ledger))["text"] == "answer"
        assert seen == ["vision-backup", "pdf-backup"]
        await registry.aclose()
    conn.close()


async def test_stt_rpc_uses_its_chain_without_a_primary_credential():
    import io
    import wave
    from raphael_core.brain.service import Brain
    from raphael_core.router import Router
    conn, vault, seen = connect(":memory:"), {}, []
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    def handler(request):
        seen.append(request)
        assert request.url.host == "api.groq.com" and request.url.path.endswith("/audio/transcriptions")
        return httpx.Response(200, json={"text": "Hello", "duration": 1})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        keys = MemoryKeyStore()
        registry = ProviderRegistry(conn, OpenRouterClient(keys.get, http=http), keys,
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        brain, rpc = Brain(conn, ProviderRouter(registry, registry.openrouter), keys, providers=registry), Router()
        brain.register(rpc)
        pid = (await registry.add({"kind": "groq", "key": "secret"}))["provider"]["id"]
        await rpc.call("settings.set", {"key": "stt_fallbacks", "value": [f"@{pid}:whisper-large-v3"]})
        result = await rpc.call("stt.transcribe", {"audio": base64.b64encode(buffer.getvalue()).decode()})
        assert result["text"] == "Hello" and len(seen) == 1
        await brain.aclose()
        await registry.aclose()
    conn.close()

async def test_explicit_fallback_can_pass_preflight_when_primary_vault_read_fails():
    conn, vault = connect(":memory:"), {}
    class BrokenCredential:
        def get(self):
            raise RuntimeError("private-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
            "choices": [{"message": {"content": "answer"}}]}))) as http:
        registry = ProviderRegistry(conn, OpenRouterClient(lambda: None, http=http), MemoryKeyStore(),
            credential=lambda pid: vault.setdefault(pid, MemoryKeyStore()), http=http)
        pid = (await registry.add({"kind": "openai", "key": "secret"}))["provider"]["id"]
        backup = (await registry.add({"kind": "groq", "key": "secret"}))["provider"]["id"]
        vault[pid] = BrokenCredential()
        SettingsStore(conn).set("agent_fallbacks", [f"@{backup}:backup"])
        client = ProviderRouter(registry, registry.openrouter)
        assert client.available(f"@{pid}:primary")
        result = await client.chat_text(model=f"@{pid}:primary", system="s", user="u", timeout_s=1, max_tokens=10)
        assert result.text == "answer" and result.model == f"@{backup}:backup"
        await registry.aclose()
    conn.close()
