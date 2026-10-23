"""Configured providers, vault-only secrets and backwards-compatible model references.

    A model reference is a plain OpenRouter slug or @provider-id:model-id. Configuration is
    stored in private settings rows, requiring no schema rewrite or change to existing choices.
    RPCs accept provider ids, never per-request URLs or arbitrary headers.
"""

import asyncio
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import replace

import httpx

from ...protocol import INTERNAL_ERROR, INVALID_PARAMS, RpcError
from ..catalog import CatalogEntry, ModelPrice
from ..openrouter import OpenRouterError
from ..secrets import CredentialKeyStore, KeyStore
from .base import ModelProvider
from .clients import AnthropicProvider, CompatibleProvider, is_transcription, model_info, validate_base_url

CONFIG_KEY = "providers.config.v1"
MODELS_KEY = "providers.models.v1"


def _kind(kind, name, group, url="", fields=("key",), available=True):
    return {"kind": kind, "name": name, "group": group, "fields": list(fields),
            "default_base_url": url, "available": available}


KINDS = [
    _kind("openrouter", "OpenRouter", "gateway", "https://openrouter.ai/api/v1"),
    _kind("openai", "OpenAI", "official", "https://api.openai.com/v1"),
    _kind("anthropic", "Anthropic (Claude)", "official", "https://api.anthropic.com"),
    _kind("google", "Google Gemini", "official", "https://generativelanguage.googleapis.com"),
    _kind("azure_openai", "Azure OpenAI", "official", fields=("key", "base_url", "api_version")),
    # TODO: Bedrock needs SigV4 signing and a separate request dialect; no heavy SDK is bundled.
    _kind("bedrock", "Amazon Bedrock", "official", fields=("key_id", "key", "region"), available=False),
    _kind("openai_compatible", "Custom (OpenAI-compatible)", "gateway", fields=("base_url", "key")),
    _kind("litellm", "LiteLLM", "gateway", fields=("base_url", "key")),
    _kind("portkey", "Portkey", "gateway", "https://api.portkey.ai/v1"),
    _kind("vercel", "Vercel AI Gateway", "gateway", "https://ai-gateway.vercel.sh/v1"),
    _kind("nebius", "Nebius", "gateway", "https://api.studio.nebius.com/v1"),
    _kind("bifrost", "Bifrost", "gateway", fields=("base_url", "key")),
    _kind("groq", "Groq", "gateway", "https://api.groq.com/openai/v1"),
    _kind("deepseek", "DeepSeek", "gateway", "https://api.deepseek.com/v1"),
    _kind("mistral", "Mistral", "gateway", "https://api.mistral.ai/v1"),
    _kind("ollama", "Ollama", "local", "http://localhost:11434/v1", ("base_url",)),
    _kind("lmstudio", "LM Studio", "local", "http://localhost:1234/v1", ("base_url",)),
]
BY_KIND = {item["kind"]: item for item in KINDS}


def _text(value, *, empty=False):
    if not isinstance(value, str) or len(value) > 2048 or (not empty and not value.strip()) or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid provider field")
    return value.strip()


class OpenRouterProvider:
    """An unchanged existing client behind the provider interface."""

    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        return getattr(self.client, name)


class ProviderRegistry:
    def __init__(self, conn, openrouter, keys: KeyStore, *,
                 credential: Callable[[str], KeyStore] | None = None,
                 http: httpx.AsyncClient | None = None, transport=None):
        self._conn, self.openrouter, self.keys = conn, openrouter, keys
        self._credential = credential or (lambda pid: CredentialKeyStore(username=f"provider:{pid}"))
        self._own_http = httpx.AsyncClient(transport=transport) if transport is not None else None
        self._http = http if http is not None else self._own_http
        self._clients: dict[str, ModelProvider] = {}
        self._status: dict[str, str] = {}
        self._revisions: dict[str, int] = {}
        self._notify = lambda method, params: None
        self._configs = self._load(CONFIG_KEY, {})
        self._models = self._load(MODELS_KEY, {})

    def _load(self, key, default):
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            data = json.loads(row["value"])
            return data if isinstance(data, dict) else default
        except (ValueError, TypeError):
            return default

    def _save(self):
        with self._conn:
            for key, value in ((CONFIG_KEY, self._configs), (MODELS_KEY, self._models)):
                self._conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                   (key, json.dumps(value)))

    def config(self, pid):
        if pid == "openrouter":
            return {"id": pid, "kind": pid, "name": "OpenRouter", "base_url": BY_KIND[pid]["default_base_url"],
                    "enabled": True, **self._configs.get(pid, {})}
        if pid not in self._configs:
            raise ValueError("Unknown provider")
        return dict(self._configs[pid])

    def _secret(self, pid):
        return self.keys if pid == "openrouter" else self._credential(pid)

    def ids(self):
        return ["openrouter", *(pid for pid in self._configs if pid != "openrouter")]

    def resolve(self, reference: str) -> tuple[str, str]:
        if not isinstance(reference, str) or not reference or len(reference) > 2048 or any(c.isspace() for c in reference):
            raise ValueError("Invalid model reference")
        if not reference.startswith("@"):
            return "openrouter", reference
        pid, sep, model = reference[1:].partition(":")
        if not sep or not model or not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
            raise ValueError("Invalid model reference")
        self.config(pid)
        return pid, model

    def available(self, reference):
        try:
            pid, _ = self.resolve(reference)
            config = self.config(pid)
            if not config["enabled"]:
                return False
            return bool((self._secret(pid).get() or "").strip()) or config["kind"] in (
                "ollama", "lmstudio", "openai_compatible", "litellm", "bifrost")
        except ValueError:
            return False

    def client(self, pid) -> ModelProvider:
        config = self.config(pid)
        if not config["enabled"]:
            raise OpenRouterError(0, "error", "Provider is disabled")
        if pid == "openrouter":
            return OpenRouterProvider(self.openrouter)
        if pid not in self._clients:
            cls = AnthropicProvider if config["kind"] == "anthropic" else CompatibleProvider
            self._clients[pid] = cls(config["kind"], config["base_url"], self._secret(pid).get,
                api_version=config.get("api_version", ""), http=self._http)
            self._clients[pid]._prices = {row["id"]: (row["price_in"], row["price_out"])
                                         for row in self._models.get(pid, [])}
        return self._clients[pid]

    def view(self, pid):
        config = self.config(pid)
        try:
            has_key = bool((self._secret(pid).get() or "").strip())
            status = self._status.get(pid, "unknown")
        except Exception:
            has_key, status = False, "auth"
        return {k: config[k] for k in ("id", "kind", "name", "base_url", "enabled")} | {
            "has_key": has_key, "status": status, "models": len(self._models.get(pid, []))}

    def register(self, router):
        self._notify = router.notify
        handlers = {"kinds": self.kinds, "list": self.list, "add": self.add, "update": self.update,
                    "remove": self.remove, "test": self.test, "models": self.models}
        for name, handler in handlers.items():
            async def safe(params, handler=handler):
                try:
                    return await handler(params)
                except (ValueError, TypeError, KeyError):
                    raise RpcError(INVALID_PARAMS, "Invalid provider configuration") from None
                except OpenRouterError as error:
                    raise RpcError(INTERNAL_ERROR, f"Provider request failed: {error.kind}", {"kind": error.kind}) from None
                except TimeoutError:
                    raise RpcError(INTERNAL_ERROR, "Provider request timed out", {"kind": "timeout"}) from None
                except Exception:
                    raise RpcError(INTERNAL_ERROR, "Provider operation failed") from None
            router.register(f"providers.{name}", safe)

    async def kinds(self, params):
        return {"kinds": KINDS}

    async def list(self, params):
        providers = []
        for pid in self.ids():
            view = self.view(pid)
            if pid != "openrouter" or view["has_key"] or pid in self._configs:
                providers.append(view)
        return {"providers": providers}

    def _checked(self, params, old=None):
        allowed = {"id", "kind", "name", "base_url", "key", "enabled", "api_version", "region", "key_id"}
        if set(params) - allowed:
            raise ValueError("Unknown provider field")
        kind = params.get("kind", (old or {}).get("kind"))
        preset = BY_KIND.get(kind)
        if preset is None or not preset["available"] or (old and kind != old["kind"]):
            raise ValueError("Unsupported provider kind")
        config = dict(old or {"kind": kind, "name": preset["name"], "enabled": True})
        config["name"] = _text(params.get("name", config["name"]))
        config["base_url"] = validate_base_url(_text(params.get("base_url", config.get("base_url", preset["default_base_url"]))))
        if kind == "openrouter" and config["base_url"] != BY_KIND[kind]["default_base_url"]:
            raise ValueError("OpenRouter endpoint is fixed")
        if kind == "google":
            # The shared contract's root URL is accepted as well as the full compatible URL.
            if url_path(config["base_url"]) in ("", "/"):
                config["base_url"] += "/v1beta/openai"
        if "enabled" in params:
            if type(params["enabled"]) is not bool:
                raise ValueError("Invalid enabled flag")
            config["enabled"] = params["enabled"]
        if "api_version" in params:
            config["api_version"] = _text(params["api_version"], empty=True)
        if kind == "azure_openai" and not config.get("api_version"):
            config["api_version"] = "2024-10-21"
        if "key" in params:
            key = params["key"]
            if not isinstance(key, str) or (key and not re.fullmatch(r"[!-~]+", key)):
                raise ValueError("Invalid provider key")
        if params.get("key_id") or params.get("region"):
            raise ValueError("Bedrock is not available")
        return config

    async def add(self, params):
        config = self._checked(params)
        pid = "openrouter" if config["kind"] == "openrouter" else "p" + uuid.uuid4().hex[:12]
        if pid == "openrouter" and (pid in self._configs or self.view(pid)["has_key"]):
            raise ValueError("Provider already exists")
        config["id"] = pid
        await self._write(pid, config, params)
        return {"provider": self.view(pid)}

    async def update(self, params):
        pid = _text(params["id"])
        config = self._checked(params, self.config(pid))
        await self._write(pid, config, params)
        return {"provider": self.view(pid)}

    async def _write(self, pid, config, params):
        secret = self._secret(pid)
        old_key = secret.get() if "key" in params else None
        previous, old_models = dict(self._configs), dict(self._models)
        try:
            if "key" in params:
                secret.set(params["key"]) if params["key"] else secret.clear()
            self._configs[pid] = config
            self._models.pop(pid, None)
            self._save()
        except Exception:
            self._configs, self._models = previous, old_models
            if "key" in params:
                secret.set(old_key) if old_key else secret.clear()
            raise
        self._revisions[pid] = self._revisions.get(pid, 0) + 1
        client = self._clients.pop(pid, None)
        if client:
            await client.aclose()
        self._status.pop(pid, None)
        self._notify("providers.changed", {})

    async def remove(self, params):
        pid = _text(params["id"])
        self.config(pid)
        secret, previous, old_models = self._secret(pid), dict(self._configs), dict(self._models)
        old_key = secret.get()
        try:
            secret.clear()
            self._configs.pop(pid, None)
            self._models.pop(pid, None)
            self._save()
        except Exception:
            self._configs, self._models = previous, old_models
            if old_key:
                secret.set(old_key)
            raise
        self._revisions[pid] = self._revisions.get(pid, 0) + 1
        client = self._clients.pop(pid, None)
        if client:
            await client.aclose()
        self._status.pop(pid, None)
        self._notify("providers.changed", {})
        return {"removed": True}

    async def discover(self, pid):
        revision = self._revisions.get(pid, 0)
        rows = await asyncio.wait_for(self.client(pid).models(timeout_s=5), 6)
        if self._revisions.get(pid, 0) != revision:
            raise OpenRouterError(0, "error", "Provider changed during discovery")
        if pid == "openrouter":
            rows = [model_info(row, kind="openrouter") for row in rows]
        self._models[pid] = rows
        self._status[pid] = "ok"
        self._save()
        return rows

    async def test(self, params):
        pid = _text(params["id"])
        self.config(pid)
        try:
            # OpenRouter discovery is public, so checking its key is a separate authenticated call.
            if pid == "openrouter":
                await asyncio.wait_for(self.client(pid).key_info(), 6)
            rows = await self.discover(pid)
            return {"ok": True, "models": len(rows)}
        except (OpenRouterError, TimeoutError) as error:
            kind = getattr(error, "kind", "timeout")
            kind = "auth" if kind == "auth" else "offline" if kind in ("offline", "network", "timeout") else "error"
            self._status[pid] = kind if kind != "error" else "unknown"
            return {"ok": False, "kind": kind, "models": 0}

    async def models(self, params):
        return {"models": await self.discover(_text(params["id"]))}

    def catalog_entries(self):
        entries = []
        for pid in self.ids():
            if pid == "openrouter" or not self.config(pid)["enabled"]:
                continue
            config = self.config(pid)
            for row in self._models.get(pid, []):
                caps = {"structured_output", "reasoning", "reasoning_off", "max_tokens"}
                if row["tools"]:
                    caps.add("tool_calling")
                if row["vision"]:
                    caps.add("image_input")
                audio = is_transcription(config["kind"], row["id"])
                entries.append(CatalogEntry(f"@{pid}:{row['id']}", "stt" if audio else "llm", row["name"], config["name"],
                    ModelPrice(input_per_mtok=row["price_in"], output_per_mtok=row["price_out"]), False,
                    row["context"], None, frozenset() if audio else frozenset(caps)))
        return entries

    async def refresh_catalog(self):
        async def fetch(pid):
            try:
                await self.discover(pid)
            except (OpenRouterError, TimeoutError):
                pass  # A failed refresh keeps the last known metadata and never resets choices.
        await asyncio.gather(*(fetch(pid) for pid in self.ids()
            if pid != "openrouter" and self.config(pid)["enabled"]))

    def record_result(self, pid, error=None):
        kind = getattr(error, "kind", None)
        self._status[pid] = ("ok" if error is None else "auth" if kind == "auth" else
                             "offline" if kind in ("offline", "network", "timeout") else "unknown")

    async def aclose(self):
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            await client.aclose()
        if self._own_http is not None:
            await self._own_http.aclose()


def url_path(url):
    from urllib.parse import urlsplit
    return urlsplit(url).path


class ProviderRouter:
    """One resolver injected into every model consumer. Plain slugs keep their old wire format.

    OpenRouter's public list/key balance and alpha decision endpoint stay OpenRouter-specific;
    completion methods route by reference and never send OpenRouter fallbacks to another service.
    """

    def __init__(self, registry, openrouter):
        self.registry, self.openrouter = registry, openrouter

    def available(self, model):
        return self.registry.available(model)

    async def _call(self, method, kwargs):
        reference = kwargs["model"]
        try:
            pid, model = self.registry.resolve(reference)
            client = self.registry.client(pid)
        except ValueError:
            raise OpenRouterError(0, "error", "Unknown provider or invalid model reference") from None
        kwargs = {**kwargs, "model": model}
        if "fallbacks" in kwargs:
            kwargs["fallbacks"] = [m for m in kwargs["fallbacks"] if not m.startswith("@")] if pid == "openrouter" else []
        try:
            result = await getattr(client, method)(**kwargs)
        except OpenRouterError as error:
            self.registry.record_result(pid, error)
            raise
        self.registry.record_result(pid)
        if pid != "openrouter":
            result = replace(result, model=f"@{pid}:{result.model}")
        return result

    async def complete_json(self, **kwargs):
        return await self._call("complete_json", kwargs)

    async def chat_text(self, **kwargs):
        return await self._call("chat_text", kwargs)

    async def plan_tools(self, **kwargs):
        return await self._call("plan_tools", kwargs)

    async def chat_tools(self, **kwargs):
        return await self._call("chat_tools", kwargs)

    async def transcribe(self, **kwargs):
        return await self._call("transcribe", kwargs)

    async def decide(self, **kwargs):
        if kwargs["model"].startswith("@"):
            raise OpenRouterError(0, "unsupported", "Decisions require OpenRouter")
        return await self.registry.client("openrouter").decide(**kwargs)

    async def models(self, timeout_s=5):
        return await self.openrouter.models(timeout_s=timeout_s)

    async def key_info(self, key=None, timeout_s=5):
        return await self.openrouter.key_info(key=key, timeout_s=timeout_s)

    def warm_up(self):
        self.openrouter.warm_up()

    async def aclose(self):
        await self.registry.aclose()
        await self.openrouter.aclose()
