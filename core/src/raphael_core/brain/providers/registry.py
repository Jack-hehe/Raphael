"""Configured providers, vault-only secrets and backwards-compatible model references.

    A model reference is a plain OpenRouter slug or @provider-id:model-id. Configuration is
    stored in private settings rows, requiring no schema rewrite or change to existing choices.
    RPCs accept provider ids, never per-request URLs or arbitrary headers.
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import replace

import httpx

from ...protocol import INTERNAL_ERROR, INVALID_PARAMS, RpcError
from ..catalog import CatalogEntry, ModelPrice
from ..openrouter import OpenRouterError
from ..secrets import KeyStore
from ..settings import DEFAULTS, SettingsStore, api_model
from .base import ModelProvider
from .presets import BY_KIND, KINDS, OPTIONAL_KEY_KINDS
from .clients import AnthropicProvider, CompatibleProvider, is_transcription, model_info, validate_base_url
from .bedrock import BedrockProvider
from .vertex import VertexProvider
from .secrets import ProviderCredentialStore

CONFIG_KEY = "providers.config.v1"
MODELS_KEY = "providers.models.v1"
SUCCESSES_KEY = "providers.successful_models.v1"
logger = logging.getLogger(__name__)


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
        self._credential = credential or (lambda pid: ProviderCredentialStore(f"provider:{pid}"))
        self._own_http = httpx.AsyncClient(transport=transport) if transport is not None else None
        self._http = http if http is not None else self._own_http
        self._clients: dict[str, ModelProvider] = {}
        self._status: dict[str, str] = {}
        self._revisions: dict[str, int] = {}
        self._notify = lambda method, params: None
        self._configs = self._load(CONFIG_KEY, {})
        self._models = self._load(MODELS_KEY, {})
        self._successful_models = self._load(SUCCESSES_KEY, {})
        self.settings = SettingsStore(conn)

    def record_success(self, reference):
        """Remember only models that actually answered, never model-list discoveries."""
        pid, model = self.resolve(reference)
        models = self._successful_models.get(pid, [])
        models = models if isinstance(models, list) else []
        if model in models:
            return
        self._successful_models[pid] = [*models[-19:], model]
        with self._conn:
            self._conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (SUCCESSES_KEY, json.dumps(self._successful_models)))

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

    def configured_fallbacks(self, purpose):
        """None preserves legacy defaults; an explicitly saved empty array disables them."""
        key = purpose + "_fallbacks"
        saved = self._conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
        return self.settings.get(key) if saved else None

    def resolve(self, reference: str) -> tuple[str, str]:
        if not isinstance(reference, str) or not reference or reference.startswith("cli:") or len(reference) > 2048 or any(c.isspace() for c in reference):
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
            return self._has_credential(pid, config) or config["kind"] in OPTIONAL_KEY_KINDS
        except ValueError:
            return False

    def _has_credential(self, pid, config):
        value = self._secret(pid).get()
        if config["kind"] == "bedrock":
            try:
                credentials = json.loads(value or "{}")
                return all(isinstance(credentials.get(field), str) and credentials[field]
                           for field in ("key_id", "secret"))
            except (ValueError, AttributeError):
                return False
        return bool((value or "").strip())

    def client(self, pid) -> ModelProvider:
        config = self.config(pid)
        if not config["enabled"]:
            raise OpenRouterError(0, "error", "Provider is disabled")
        if pid == "openrouter":
            return OpenRouterProvider(self.openrouter)
        if pid not in self._clients:
            cls = {"anthropic": AnthropicProvider, "vertex": VertexProvider,
                   "bedrock": BedrockProvider}.get(config["kind"], CompatibleProvider)
            self._clients[pid] = cls(config["kind"], config["base_url"], self._secret(pid).get,
                api_version=config.get("api_version", ""), http=self._http,
                **({"region": config["region"]} if config["kind"] == "bedrock" else {}))
            self._clients[pid]._prices = {row["id"]: (row["price_in"], row["price_out"])
                                         for row in self._models.get(pid, [])}
        return self._clients[pid]

    def view(self, pid):
        config = self.config(pid)
        try:
            has_key = self._has_credential(pid, config)
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
        allowed = {"id", "kind", "name", "base_url", "key", "enabled", "api_version", "region", "key_id",
                   "secret", "service_account", "project", "location"}
        if set(params) - allowed:
            raise ValueError("Unknown provider field")
        kind = params.get("kind", (old or {}).get("kind"))
        preset = BY_KIND.get(kind)
        if preset is None or not preset["available"] or (old and kind != old["kind"]):
            raise ValueError("Unsupported provider kind")
        config = dict(old or {"kind": kind, "name": preset["name"], "enabled": True})
        config["name"] = _text(params.get("name", config["name"]))
        if set(params) & {"region", "key_id", "secret"} and kind != "bedrock":
            raise ValueError("Unexpected Bedrock field")
        if set(params) & {"project", "location", "service_account"} and kind != "vertex":
            raise ValueError("Unexpected Vertex field")
        if kind in ("bedrock", "vertex"):
            if "base_url" in params or "key" in params:
                raise ValueError("Native provider endpoint and credentials have dedicated fields")
            for field in (("region",) if kind == "bedrock" else ("project", "location")):
                value = params.get(field, config.get(field))
                if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", value):
                    raise ValueError("Invalid native provider field")
                config[field] = value
            if kind == "bedrock":
                suffix = "amazonaws.com.cn" if config["region"].startswith("cn-") else "amazonaws.com"
                config["base_url"] = f"https://bedrock-runtime.{config['region']}.{suffix}"
            else:
                config["base_url"] = (f"https://{config['location']}-aiplatform.googleapis.com/v1/projects/"
                                      f"{config['project']}/locations/{config['location']}/endpoints/openapi")
            for field in ("key_id", "secret"):
                if field in params and (not isinstance(params[field], str) or
                        (params[field] and not re.fullmatch(r"[!-~]{1,2048}", params[field]))):
                    raise ValueError("Invalid Bedrock credential")
            if "service_account" in params:
                value = params["service_account"]
                if not isinstance(value, str) or len(value) > 32768:
                    raise ValueError("Invalid service account")
                if value:
                    try:
                        account = json.loads(value)
                        if not all(isinstance(account[k], str) and account[k] for k in ("client_email", "private_key")):
                            raise ValueError
                    except (ValueError, KeyError, TypeError):
                        raise ValueError("Invalid service account") from None
        else:
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
        changed_secret = bool(set(params) & {"key", "secret", "key_id", "service_account"})
        old_key = secret.get() if changed_secret else None
        new_key = params.get("service_account", params.get("key"))
        if config["kind"] == "bedrock" and changed_secret:
            credentials = json.loads(old_key) if old_key else {}
            credentials.update({k: params[k] for k in ("key_id", "secret") if k in params})
            new_key = json.dumps(credentials) if any(credentials.values()) else ""
        previous, old_models = dict(self._configs), dict(self._models)
        try:
            if changed_secret:
                secret.set(new_key) if new_key else secret.clear()
            self._configs[pid] = config
            self._models.pop(pid, None)
            self._save()
        except Exception:
            self._configs, self._models = previous, old_models
            if changed_secret:
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
        pid = _text(params["id"])
        rows = await self.discover(pid)
        selected = {self.settings.get("agent_model"), *self.settings.get("agent_fallbacks")}
        selected.update(row[0] for row in self._conn.execute("SELECT model FROM members WHERE model != ''"))
        local = BY_KIND[self.config(pid)["kind"]]["local_context_hint"]
        return {"models": [{**row, "context_warning": bool(local and row["context"] is not None
                           and row["context"] < 65_536 and f"@{pid}:{row['id']}" in selected)} for row in rows]}

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


def for_purpose(client, purpose):
    """Scope the real router while retaining simple provider fakes at consumer seams."""
    return client.for_purpose(purpose) if isinstance(client, ProviderRouter) else client


class ProviderRouter:
    """One resolver injected into every model consumer. Plain slugs keep their old wire format.

    OpenRouter's public list/key balance and alpha decision endpoint stay OpenRouter-specific;
    completion methods route by reference and never send OpenRouter fallbacks to another service.
    """

    def __init__(self, registry, openrouter, purpose="agent"):
        self.registry, self.openrouter = registry, openrouter
        self.purpose = purpose

    def for_purpose(self, purpose):
        if purpose not in ("agent", "cleanup", "router", "vision", "pdf", "stt", "memory"):
            raise ValueError("Unknown model purpose")
        return ProviderRouter(self.registry, self.openrouter, purpose)

    def model_for(self, reference):
        if self.purpose in ("router", "memory", "pdf"):
            reference = self.registry.settings.get(self.purpose + "_model") or reference
        return api_model(self.registry.settings, reference) if reference.startswith("cli:") else reference

    def available(self, model):
        references = [self.model_for(model), *self.registry.settings.get(self.purpose + "_fallbacks")]
        if self.purpose == "cleanup":
            fallback = self.cleanup_fallback(model)
            if fallback:
                references.append(fallback)
        unreadable = False
        for reference in references:
            try:
                if self.registry.available(reference):
                    return True
            except Exception:
                unreadable = True
        if unreadable:
            raise OpenRouterError(0, "auth", "Could not read provider credential") from None
        return False

    def cleanup_fallback(self, primary):
        """The mandatory cleanup retry follows the owner's provider preference order."""
        api_providers = {pid for pid in self.registry.ids() if BY_KIND[self.registry.config(pid)["kind"]]["group"] != "local"}
        candidates = [DEFAULTS["cleanup_model"], api_model(self.registry.settings, self.registry.settings.get("agent_model"), api_providers=api_providers),
                      *self.registry.settings.get("cleanup_fallbacks")]
        for pid in self.registry.ids():
            models = self.registry._successful_models.get(pid, [])
            if isinstance(models, list):
                candidates.extend(model if pid == "openrouter" else f"@{pid}:{model}"
                                  for model in models if isinstance(model, str))
        # Prefer a different working model; retry the primary only when it is the
        # sole configured option (transient failures can still recover on retry).
        candidates = [ref for ref in candidates if ref != primary] + [primary]
        for reference in dict.fromkeys(candidates):
            try:
                if self.registry.available(reference):
                    return reference
            except Exception:
                continue  # An unreadable credential must not hide a usable alternative.
        return None

    async def complete_json_once(self, **kwargs):
        """Cleanup owns its two-attempt deadline; do not nest the generic fallback chain."""
        return await self._attempt("complete_json", kwargs)

    async def _call(self, method, kwargs):
        reference = self.model_for(kwargs["model"])
        configured = self.registry.configured_fallbacks(self.purpose)
        explicit = configured or []
        # Legacy curated fallbacks stay within OpenRouter. Cross-provider attempts are
        # possible only through the owner's ordered settings, never an implicit default.
        implicit = [ref for ref in kwargs.get("fallbacks", []) if not ref.startswith("@")]
        fallbacks = explicit if configured is not None else implicit if not reference.startswith("@") else []
        chain = list(dict.fromkeys([reference, *fallbacks]))
        timeout = kwargs["timeout_s"] / len(chain)
        for index, target in enumerate(chain):
            attempt = {**kwargs, "model": target, "timeout_s": timeout}
            try:
                return await asyncio.wait_for(self._attempt(method, attempt), timeout)
            except TimeoutError:
                error = OpenRouterError(0, "timeout", "Provider request timed out")
                pid, _ = self.registry.resolve(target)
                self.registry.record_result(pid, error)
            except OpenRouterError as caught:
                error = caught
            retryable = error.kind in ("offline", "network", "timeout", "rate_limit", "server") or (
                error.kind == "error" and error.status >= 500)
            # An auth failure may cross a provider boundary only with an explicitly
            # listed fallback. Content refusals and malformed requests never retry.
            if index + 1 == len(chain) or not (retryable or (error.kind == "auth" and explicit)):
                raise error from None
            source_id = target[1:].partition(":")[0] if target.startswith("@") else "openrouter"
            next_ref = chain[index + 1]
            next_id = next_ref[1:].partition(":")[0] if next_ref.startswith("@") else "openrouter"
            logger.warning("Provider fallback: purpose=%s from=%s to=%s kind=%s attempt=%d",
                           self.purpose, source_id, next_id, error.kind, index + 1)
        raise AssertionError("Empty provider chain")

    async def _attempt(self, method, kwargs):
        reference = kwargs["model"]
        try:
            pid, model = self.registry.resolve(reference)
            client = self.registry.client(pid)
        except ValueError:
            raise OpenRouterError(0, "error", "Unknown provider or invalid model reference") from None
        kwargs = {**kwargs, "model": model}
        if "fallbacks" in kwargs:
            kwargs["fallbacks"] = []
        try:
            result = await getattr(client, method)(**kwargs)
        except OpenRouterError as error:
            self.registry.record_result(pid, error)
            raise
        self.registry.record_result(pid)
        if pid != "openrouter":
            result = replace(result, model=f"@{pid}:{result.model}")
        if method != "transcribe":
            try:
                self.registry.record_success(reference)
            except Exception as error:
                logger.warning("Could not remember successful model: %s", type(error).__name__)
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

    def start_connection_warmup(self):
        warm = getattr(self.openrouter, "start_connection_warmup", None)
        if warm is not None:
            warm()

    async def aclose(self):
        await self.registry.aclose()
        await self.openrouter.aclose()
