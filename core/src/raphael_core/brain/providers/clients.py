"""Provider wire formats. Only configured endpoints are reached; credentials never enter errors."""

import asyncio
import base64
import ipaddress
import json
import math
import re
import time
from collections.abc import Callable
from urllib.parse import quote, unquote, urlsplit

import httpx

from ..openrouter import OpenRouterClient, OpenRouterError, Transcription, _Reply
from .presets import OPTIONAL_KEY_KINDS


def validate_base_url(value: str) -> str:
    """HTTPS everywhere; HTTP only on loopback, link-local and private LAN addresses."""
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
        valid = (parts.scheme in ("http", "https") and host and not parts.username
                 and not parts.password and not parts.query and not parts.fragment
                 and not any(c.isspace() or ord(c) < 32 for c in value)
                 and "\\" not in value and "?" not in value and "#" not in value)
        if port is not None and not 1 <= port <= 65535:
            valid = False
        if parts.scheme == "http":
            try:
                address = ipaddress.ip_address(host)
                lan = address.is_loopback or address.is_link_local or any(
                    address in ipaddress.ip_network(net) for net in
                    ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
                )
            except ValueError:
                lan = host == "localhost" or host.endswith((".localhost", ".local", ".lan"))
            valid = valid and lan
        if not valid:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Invalid provider base URL") from None
    return value.rstrip("/")


def amount(value):
    try:
        return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0 else None)
    except OverflowError:
        return None


def token_count(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _refused_error(data):
    error = data.get("error") if isinstance(data, dict) else None
    markers = {"content_filter", "content_policy_violation", "safety_violation"}
    return isinstance(error, dict) and any(isinstance(error.get(field), str) and
        error[field] in markers for field in ("type", "code"))


def is_transcription(kind, model_id):
    return kind in ("openai", "groq") and (model_id.startswith("whisper") or "transcribe" in model_id)


def generation_model(raw, kind):
    model_id = str(raw.get("id", raw.get("name", ""))).lower()
    if kind == "google" and raw.get("supportedGenerationMethods"):
        return "generateContent" in raw["supportedGenerationMethods"]
    if any(marker in model_id for marker in ("embedding", "embed-", "rerank", "moderation", "tts-", "dall-e", "gpt-image", "realtime")):
        return False
    return model_id not in ("babbage-002", "davinci-002")


def model_info(raw: dict, *, kind: str) -> dict:
    """Discovery metadata. Missing capabilities and prices stay conservative."""
    model_id = raw.get("id", raw.get("name", ""))
    if kind == "google":
        model_id = model_id.removeprefix("models/")
    arch = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
    inputs = arch.get("input_modalities") if isinstance(arch.get("input_modalities"), list) else []
    params = raw.get("supported_parameters") if isinstance(raw.get("supported_parameters"), list) else []
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    official_chat = kind in ("anthropic", "google") or (kind in ("openai", "azure_openai") and model_id.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4")) and not is_transcription(kind, model_id))
    vision = kind in ("anthropic", "google") or (official_chat and model_id.startswith(("gpt-4o", "gpt-4.1", "gpt-5", "gpt-6", "chatgpt-4o", "o1", "o3", "o4")) and not model_id.startswith(("o1-mini", "o3-mini")))
    def price(name):
        try:
            value = amount(float(pricing[name]))
            return amount(value * 1_000_000) if value is not None else None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
    name = raw.get("display_name") or raw.get("displayName") or raw.get("name")
    return {
        "id": model_id, "name": name if isinstance(name, str) else model_id,
        "context": next((value for field in ("context_length", "inputTokenLimit", "max_model_len")
                         if (value := token_count(raw.get(field))) and value > 0), None),
        "vision": "image" in inputs or raw.get("vision") is True or vision,
        "tools": "tools" in params or raw.get("tools") is True or official_chat,
        "audio_in": "audio" in inputs or raw.get("audio_in") is True or is_transcription(kind, model_id),
        "price_in": price("prompt"), "price_out": price("completion"),
    }


class CompatibleProvider(OpenRouterClient):
    """OpenAI chat/tools/vision/JSON plus the compatible Gemini endpoint and Azure headers.

    The existing parser is shared, but OpenRouter-only routing, plugins and reasoning fields
    never reach another provider. Fallbacks belong to the resolver, never a remote gateway.
    """

    def __init__(self, kind: str, base_url: str, key_provider: Callable[[], str | None],
                 *, api_version: str = "", http: httpx.AsyncClient | None = None):
        super().__init__(key_provider, http=http)
        self.kind = kind
        self.base_url = validate_base_url(base_url)
        self.api_version = api_version
        self._prices: dict[str, tuple[float | None, float | None]] = {}

    def _stored_key(self):
        # Authentication is built only by _headers, without chaining vault exceptions.
        return None

    def _headers(self):
        try:
            key = self._key_provider()
            if key is not None and (not isinstance(key, str) or not re.fullmatch(r"[!-~]+", key)):
                raise ValueError
        except Exception:
            raise OpenRouterError(0, "auth", "Could not read provider credential") from None
        if not key and self.kind not in OPTIONAL_KEY_KINDS:
            raise OpenRouterError(0, "auth", "No provider credential")
        headers = {}
        if key:
            name = "api-key" if self.kind == "azure_openai" else "x-api-key" if self.kind == "anthropic" else "Authorization"
            headers[name] = key if name != "Authorization" else f"Bearer {key}"
        if self.kind == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        if self.kind == "portkey" and key:
            headers = {"x-portkey-api-key": key}
        if self.kind == "google" and not self.base_url.endswith("/openai") and key:
            headers = {"x-goog-api-key": key}
        return headers

    async def _send(self, method, path, timeout_s, *, body=None, data=None, files=None, params=None):
        headers = self._headers()
        started = time.perf_counter()
        try:
            response = await self._client().request(method, self.base_url + path,
                headers=headers, json=body, data=data, files=files,
                params={**(params or {}), **({"api-version": self.api_version} if self.api_version else {})},
                timeout=timeout_s, follow_redirects=False)
        except httpx.TimeoutException:
            raise OpenRouterError(0, "timeout", "Provider request timed out") from None
        except httpx.TransportError:
            raise OpenRouterError(0, "offline", "Provider is offline") from None
        except (httpx.DecodingError, httpx.InvalidURL):
            raise OpenRouterError(0, "error", "Invalid provider response or URL") from None
        except (ValueError, TypeError):
            raise OpenRouterError(0, "error", "Invalid provider request") from None
        status = response.status_code
        if not response.is_success:
            try:
                refused = _refused_error(response.json())
            except ValueError:
                refused = False
            if refused:
                raise OpenRouterError(status, "refusal", "Provider declined the request")
            kind = {401: "auth", 403: "auth", 408: "timeout", 429: "rate_limit"}.get(status, "error")
            raise OpenRouterError(status, kind, f"Provider request failed ({status})")
        try:
            decoded = response.json()
            if _refused_error(decoded):
                raise OpenRouterError(status, "refusal", "Provider declined the request")
            if not isinstance(decoded, dict) or decoded.get("error"):
                raise ValueError
        except ValueError:
            raise OpenRouterError(status, "error", "Invalid provider response") from None
        return _Reply(status, decoded, round((time.perf_counter() - started) * 1000))

    async def _request(self, method, path, key, timeout_s, body=None, **kwargs):
        if body is not None:
            effort = body.get("reasoning", {}).get("effort", "none")
            body = {k: v for k, v in body.items() if k not in ("provider", "models", "reasoning")}
            if body.pop("plugins", None):
                raise OpenRouterError(0, "unsupported", "OpenRouter plugins require OpenRouter")
            model = body.get("model", "")
            if self.kind in ("openai", "azure_openai") and model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
                # Only the original GPT-5 family advertises minimal effort. Unknown/new
                # families use low rather than sending an unsupported effort value.
                minimal = re.fullmatch(r"gpt-5(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?", model)
                body["reasoning_effort"] = ("minimal" if minimal else "low") if effort in ("none", "minimal") else effort
                content_budget = body.pop("max_tokens")
                body["max_completion_tokens"] = max(content_budget, min(content_budget + 2048, 32768))
            if self.kind == "google" and model.startswith(("gemini-2.5-", "gemini-3")):
                # Only 2.5 Flash permits thinking to be disabled; Pro and 3 require it.
                disabled = effort == "none" and model.startswith("gemini-2.5-flash")
                body["reasoning_effort"] = "none" if disabled else "low" if effort in ("none", "minimal") else effort
                if not disabled:
                    content_budget = body["max_tokens"]
                    body["max_tokens"] = max(content_budget, min(content_budget + 2048, 32768))
            schema = body.get("response_format", {}).get("json_schema")
            if schema and (self.kind == "deepseek" or model.startswith(("gpt-3.5", "gpt-4-turbo"))):
                body["response_format"] = {"type": "json_object"}
                body["messages"] = [dict(message) for message in body["messages"]]
                body["messages"][0]["content"] += "\nReturn only JSON matching this schema:\n" + json.dumps(schema["schema"])
            if self.kind == "azure_openai" and urlsplit(self.base_url).path in ("", "/openai"):
                prefix = "/openai" if not urlsplit(self.base_url).path else ""
                path = f"{prefix}/deployments/{quote(model, safe='')}{path}"
        reply = await self._send(method, path, timeout_s, body=body)
        for choice in reply.body.get("choices", []):
            if isinstance(choice, dict) and (choice.get("finish_reason") == "content_filter" or
                    isinstance(choice.get("message"), dict) and choice["message"].get("refusal")):
                raise OpenRouterError(reply.status, "refusal", "Provider declined the request")
        self._usage(reply.body, (body or {}).get("model", ""))
        return reply

    def _usage(self, body, model):
        usage = body.setdefault("usage", {})
        if not isinstance(usage, dict):
            usage = body["usage"] = {}
        known = amount(usage.get("cost")) is not None
        if not known and model in self._prices:
            pin, pout = self._prices[model]
            tin, tout = token_count(usage.get("prompt_tokens")), token_count(usage.get("completion_tokens"))
            if None not in (pin, pout, tin, tout):
                estimate = amount((pin * tin + pout * tout) / 1_000_000)
                if estimate is not None:
                    usage["cost"] = estimate
                    known = True
        body["_usage_known"] = known
        if not known:
            usage.pop("cost", None)
        for field in ("prompt_tokens", "completion_tokens"):
            usage[field] = token_count(usage.get(field)) or 0

    async def _result(self, method, kwargs):
        try:
            result = await method(**kwargs)
        except OpenRouterError as error:
            if error.kind in ("bad_response", "bad_request", "server"):
                raise OpenRouterError(error.status, "error", "Invalid provider response") from None
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            raise OpenRouterError(0, "error", "Invalid provider response") from None
        return result

    async def complete_json(self, **kwargs):
        return await self._result(super().complete_json, kwargs)

    async def chat_text(self, **kwargs):
        return await self._result(super().chat_text, kwargs)

    async def plan_tools(self, **kwargs):
        return await self._result(super().plan_tools, kwargs)

    async def chat_tools(self, **kwargs):
        return await self._result(super().chat_tools, kwargs)

    async def key_info(self, key=None, timeout_s=5):
        raise OpenRouterError(0, "unsupported", "Provider has no key balance endpoint")

    async def models(self, timeout_s=5):
        if self.kind == "google":
            base = self.base_url.removesuffix("/openai")
            # A separate instance avoids changing configuration during concurrent requests.
            listing = CompatibleProvider("google", base, self._key_provider, http=self._client())
            rows = await listing._pages("/models", "models", timeout_s)
        elif self.kind == "azure_openai" and (urlsplit(self.base_url).path in ("", "/openai") or "/openai/deployments/" in urlsplit(self.base_url).path):
            parts = urlsplit(self.base_url)
            prefix, separator, deployment = parts.path.partition("/deployments/")
            base = parts._replace(path=prefix or "/openai").geturl()
            listing = CompatibleProvider("azure_openai", base, self._key_provider,
                api_version=self.api_version, http=self._client())
            rows = await listing._pages("/models", "data", timeout_s)
            if separator:
                # The URL pins a deployment; don't advertise unrelated resource models for it.
                deployment = unquote(deployment)
                metadata = next((row for row in rows if row["id"] == deployment), {})
                rows = [{**metadata, "id": deployment, "name": deployment, "tools": True}]
        else:
            rows = await self._pages("/models", "data", timeout_s)
        if self.kind == "ollama":
            await self._ollama_capabilities(rows, timeout_s)
        result = [model_info(row, kind=self.kind) for row in rows if generation_model(row, self.kind)]
        self._prices = {row["id"]: (row["price_in"], row["price_out"]) for row in result}
        return result

    async def _ollama_capabilities(self, rows, timeout_s):
        native = CompatibleProvider("ollama", self.base_url.removesuffix("/v1"),
                                    self._key_provider, http=self._client())
        limit = asyncio.Semaphore(4)
        async def enrich(row):
            async with limit:
                try:
                    reply = await native._send("POST", "/api/show", timeout_s, body={"model": row["id"]})
                except OpenRouterError:
                    return  # Older servers/proxies can expose only the compatible endpoint.
                capabilities = reply.body.get("capabilities")
                if isinstance(capabilities, list):
                    row["tools"] = "tools" in capabilities
                    row["vision"] = "vision" in capabilities
                info = reply.body.get("model_info")
                if isinstance(info, dict):
                    contexts = [value for key, value in info.items()
                                if key.endswith(".context_length") and token_count(value)]
                    if contexts:
                        row["context_length"] = max(contexts)
        await asyncio.gather(*(enrich(row) for row in rows))

    async def _pages(self, path, field, timeout_s):
        rows, params, seen = {}, {}, set()
        for _ in range(100):
            reply = await self._send("GET", path, timeout_s, params=params)
            page = reply.body.get(field)
            if not isinstance(page, list):
                raise OpenRouterError(200, "error", "Invalid provider model list")
            for row in page:
                model_id = row.get("id", row.get("name")) if isinstance(row, dict) else None
                if isinstance(model_id, str) and model_id:
                    rows[model_id] = {**row, "id": model_id}
            token = reply.body.get("nextPageToken") if field == "models" else reply.body.get("last_id") if reply.body.get("has_more") else None
            if not token:
                return list(rows.values())
            if not isinstance(token, str) or token in seen:
                raise OpenRouterError(200, "error", "Invalid provider pagination")
            seen.add(token)
            params = {"pageToken" if field == "models" else "after_id": token}
        raise OpenRouterError(200, "error", "Provider model list exceeds page limit")

    async def transcribe(self, *, model, audio_b64, language, hints, timeout_s,
                         audio_format="wav", languages=()):
        if self.kind not in ("openai", "groq"):
            raise OpenRouterError(0, "unsupported", "Provider does not support transcription")
        try:
            audio = base64.b64decode(audio_b64, validate=True)
        except ValueError:
            raise OpenRouterError(0, "error", "Invalid audio") from None
        data = {"model": model}
        if language:
            data["language"] = language
        if hints:
            data["prompt"] = ", ".join(hints)[:200]
        reply = await self._send("POST", "/audio/transcriptions", timeout_s, data=data,
            files={"file": (f"audio.{audio_format}", audio, f"audio/{audio_format}")})
        if not isinstance(reply.body.get("text"), str):
            raise OpenRouterError(200, "error", "Invalid transcription response")
        usage = reply.body.get("usage") if isinstance(reply.body.get("usage"), dict) else {}
        cost = amount(usage.get("cost"))
        return Transcription(reply.body["text"], model, cost or 0, amount(reply.body.get("duration")) or 0,
            int(amount(usage.get("input_tokens")) or 0), int(amount(usage.get("output_tokens")) or 0),
            reply.latency_ms, usage_known=cost is not None)


def _anthropic_content(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks = []
    for part in content or []:
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"]
            if url.startswith("data:"):
                header, data = url.split(",", 1)
                source = {"type": "base64", "media_type": header[5:].removesuffix(";base64"), "data": data}
            else:
                source = {"type": "url", "url": url}
            blocks.append({"type": "image", "source": source})
        else:
            raise OpenRouterError(0, "unsupported", "Unsupported Anthropic content type")
    return blocks


class AnthropicProvider(CompatibleProvider):
    """Native Messages API; structured JSON uses a forced tool with the requested schema."""

    async def _request(self, method, path, key, timeout_s, body=None, **kwargs):
        if path != "/chat/completions":
            raise OpenRouterError(0, "unsupported", "Unsupported Anthropic operation")
        if body.get("plugins"):
            raise OpenRouterError(0, "unsupported", "OpenRouter plugins require OpenRouter")
        messages, system = [], []
        for message in body["messages"]:
            role = message["role"]
            if role == "system":
                system.extend(_anthropic_content(message.get("content")))
                continue
            if role == "tool":
                role = "user"
                content = [{"type": "tool_result", "tool_use_id": message["tool_call_id"],
                            "content": message.get("content") or ""}]
            else:
                content = _anthropic_content(message.get("content"))
                for call in message.get("tool_calls", []):
                    try:
                        arguments = json.loads(call["function"]["arguments"])
                    except (ValueError, TypeError):
                        raise OpenRouterError(0, "error", "Invalid tool arguments") from None
                    content.append({"type": "tool_use", "id": call["id"],
                                    "name": call["function"]["name"], "input": arguments})
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(content)
            else:
                messages.append({"role": role, "content": content})
        native = {"model": body["model"], "messages": messages, "max_tokens": body["max_tokens"]}
        effort = body.get("reasoning", {}).get("effort", "none")
        if effort != "none":
            if re.match(r"claude-[a-z]+-(?:4[.-][6-9]|[5-9](?:[.-]|$))", body["model"]):
                native["thinking"] = {"type": "adaptive"}
                native["output_config"] = {"effort": effort if effort in ("low", "medium", "high") else
                                          "low" if effort == "minimal" else "high"}
                native["max_tokens"] += 2048
            else:
                budget = {"minimal": 1024, "low": 1024, "medium": 2048, "high": 4096}.get(effort, 4096)
                native["thinking"] = {"type": "enabled", "budget_tokens": budget}
                native["max_tokens"] += budget
        if system:
            native["system"] = system[0]["text"] if len(system) == 1 else system
        tools = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                  "input_schema": t["function"]["parameters"]} for t in body.get("tools", [])]
        structured = body.get("response_format", {}).get("json_schema")
        if structured:
            tools = [{"name": structured["name"], "input_schema": structured["schema"]}]
            native["tool_choice"] = ({"type": "auto"} if "thinking" in native else
                                     {"type": "tool", "name": structured["name"]})
        if tools:
            native["tools"] = tools
        reply = await self._send("POST", "/v1/messages", timeout_s, body=native)
        data = reply.body
        if data.get("stop_reason") == "refusal":
            raise OpenRouterError(reply.status, "refusal", "Provider declined the request")
        if not isinstance(data.get("content"), list):
            raise OpenRouterError(200, "error", "Invalid Anthropic response")
        content, calls, json_content = [], [], None
        for block in data["content"]:
            if block.get("type") == "text":
                content.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                if structured and block.get("name") == structured["name"]:
                    json_content = json.dumps(block.get("input"))
                else:
                    calls.append({"id": block["id"], "type": "function", "function": {
                        "name": block["name"], "arguments": json.dumps(block.get("input", {}))}})
        usage = data.get("usage") or {}
        prompt_tokens = sum(int(amount(usage.get(field)) or 0) for field in
                            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        normalized = {"model": data.get("model", body["model"]), "choices": [{
            "finish_reason": "length" if data.get("stop_reason") == "max_tokens" else "stop",
            "message": {"content": json_content if json_content is not None else "".join(content), "tool_calls": calls}}],
            "usage": {"prompt_tokens": prompt_tokens,
                      "completion_tokens": usage.get("output_tokens", 0), "cost": usage.get("cost")}}
        self._usage(normalized, body["model"])
        return _Reply(reply.status, normalized, reply.latency_ms)

    async def models(self, timeout_s=5):
        rows = await self._pages("/v1/models", "data", timeout_s)
        return [model_info(row, kind="anthropic") for row in rows]
