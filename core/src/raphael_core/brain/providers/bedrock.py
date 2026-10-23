"""AWS Signature Version 4 and Bedrock Converse, without an AWS SDK."""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlsplit

import httpx

from ..openrouter import OpenRouterError, _Reply
from .clients import CompatibleProvider, model_info


def sign_v4(method, url, payload, key_id, secret, region, *, service="bedrock", now=None, headers=None):
    """Sign the exact serialized bytes, with canonical URI, query and header values."""
    now = now or datetime.now(timezone.utc)
    stamp, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    parts = urlsplit(url)
    canonical_headers = {k.lower(): " ".join(v.strip().split()) for k, v in (headers or {}).items()}
    canonical_headers.update({"host": parts.netloc, "x-amz-date": stamp})
    names = ";".join(sorted(canonical_headers))
    values = "".join(f"{name}:{canonical_headers[name]}\n" for name in sorted(canonical_headers))
    query = "&".join(f"{k}={v}" for k, v in sorted(
        (quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in parse_qsl(parts.query, keep_blank_values=True)))
    # Standard SigV4 services encode an already escaped path again (unlike S3).
    uri = quote(parts.path or "/", safe="/-_.~")
    canonical = "\n".join((method.upper(), uri, query, values, names, hashlib.sha256(payload).hexdigest()))
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    def mac(key, value):
        return hmac.new(key, value.encode(), hashlib.sha256).digest()
    signing_key = mac(mac(mac(mac(("AWS4" + secret).encode(), date), region), service), "aws4_request")
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {**canonical_headers, "Authorization": f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={names}, Signature={signature}"}


def _content(content):
    if isinstance(content, str):
        return [{"text": content}] if content else []
    result = []
    for part in content or []:
        if part.get("type") == "text":
            result.append({"text": part["text"]})
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"]
            head, sep, encoded = url.partition(";base64,")
            format = head.removeprefix("data:image/")
            if not sep or format not in ("png", "jpeg", "gif", "webp"):
                raise OpenRouterError(0, "unsupported", "Bedrock requires an inline supported image")
            try:
                base64.b64decode(encoded, validate=True)
            except ValueError:
                raise OpenRouterError(0, "error", "Invalid Bedrock image") from None
            result.append({"image": {"format": format, "source": {"bytes": encoded}}})
        else:
            raise OpenRouterError(0, "unsupported", "Unsupported Bedrock content")
    return result


class BedrockProvider(CompatibleProvider):
    def __init__(self, *args, region, **kwargs):
        super().__init__(*args, **kwargs)
        self.region = region

    async def _signed(self, method, url, timeout_s, body=None):
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode() if body is not None else b""
        try:
            credentials = json.loads(self._key_provider() or "{}")
            key_id, secret = credentials["key_id"], credentials["secret"]
            if any(not isinstance(v, str) or not v or not v.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in v) for v in (key_id, secret)):
                raise ValueError
            headers = sign_v4(method, url, payload, key_id, secret, self.region,
                              headers={"Content-Type": "application/json"} if body is not None else {})
        except Exception:
            raise OpenRouterError(0, "auth", "Invalid Bedrock credentials") from None
        started = time.perf_counter()
        try:
            response = await self._client().request(method, url, content=payload, headers=headers,
                                                   timeout=timeout_s, follow_redirects=False)
        except httpx.TimeoutException:
            raise OpenRouterError(0, "timeout", "Bedrock request timed out") from None
        except httpx.TransportError:
            raise OpenRouterError(0, "offline", "Bedrock is offline") from None
        if not response.is_success:
            kind = {401: "auth", 403: "auth", 408: "timeout", 429: "rate_limit"}.get(response.status_code,
                "server" if response.status_code >= 500 else "error")
            raise OpenRouterError(response.status_code, kind, "Bedrock request failed")
        try:
            decoded = response.json()
            if not isinstance(decoded, dict):
                raise ValueError
        except ValueError:
            raise OpenRouterError(0, "error", "Invalid Bedrock response") from None
        return _Reply(response.status_code, decoded, round((time.perf_counter() - started) * 1000))

    async def _request(self, method, path, key, timeout_s, body=None, **kwargs):
        if body is None or path != "/chat/completions":
            raise OpenRouterError(0, "unsupported", "Unsupported Bedrock operation")
        if body.get("plugins"):
            raise OpenRouterError(0, "unsupported", "OpenRouter plugins require OpenRouter")
        system, messages = [], []
        for message in body["messages"]:
            role = message["role"]
            if role == "system":
                system.extend(_content(message.get("content")))
                continue
            if role == "tool":
                role = "user"
                content = [{"toolResult": {"toolUseId": message["tool_call_id"],
                                           "content": [{"text": message.get("content") or ""}]}}]
            else:
                content = _content(message.get("content"))
                for call in message.get("tool_calls", []):
                    content.append({"toolUse": {"toolUseId": call["id"], "name": call["function"]["name"],
                                                "input": json.loads(call["function"]["arguments"])}})
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(content)
            else:
                messages.append({"role": role, "content": content})
        native = {"messages": messages, "inferenceConfig": {"maxTokens": body["max_tokens"]}}
        if system:
            native["system"] = system
        tools = [{"toolSpec": {"name": tool["function"]["name"], "description": tool["function"].get("description", ""),
                               "inputSchema": {"json": tool["function"]["parameters"]}}} for tool in body.get("tools", [])]
        structured = body.get("response_format", {}).get("json_schema")
        if structured:
            tools = [{"toolSpec": {"name": structured["name"], "description": "Return the structured response",
                                   "inputSchema": {"json": structured["schema"]}}}]
        if tools:
            native["toolConfig"] = {"tools": tools}
            if structured:
                native["toolConfig"]["toolChoice"] = {"tool": {"name": structured["name"]}}
        reply = await self._signed("POST", self.base_url + "/model/" + quote(body["model"], safe="") + "/converse", timeout_s, native)
        data = reply.body
        if data.get("stopReason") in ("guardrail_intervened", "content_filtered"):
            raise OpenRouterError(200, "refusal", "Bedrock declined the request")
        blocks = data["output"]["message"]["content"]
        text, calls, json_content = [], [], None
        for block in blocks:
            if "text" in block:
                text.append(block["text"])
            elif "toolUse" in block:
                call = block["toolUse"]
                if structured and call["name"] == structured["name"]:
                    json_content = json.dumps(call["input"])
                else:
                    calls.append({"id": call["toolUseId"], "type": "function", "function": {
                        "name": call["name"], "arguments": json.dumps(call["input"])}})
        usage = data.get("usage") or {}
        normalized = {"model": body["model"], "choices": [{"finish_reason": "length" if data.get("stopReason") == "max_tokens" else "stop",
            "message": {"content": json_content if json_content is not None else "".join(text), "tool_calls": calls}}],
            "usage": {"prompt_tokens": usage.get("inputTokens"), "completion_tokens": usage.get("outputTokens")}}
        self._usage(normalized, body["model"])
        return _Reply(reply.status, normalized, reply.latency_ms)

    async def models(self, timeout_s=5):
        suffix = "amazonaws.com.cn" if self.region.startswith("cn-") else "amazonaws.com"
        reply = await self._signed("GET", f"https://bedrock.{self.region}.{suffix}/foundation-models", timeout_s)
        rows = reply.body.get("modelSummaries")
        if not isinstance(rows, list):
            raise OpenRouterError(200, "error", "Invalid Bedrock model list")
        result = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("modelId"), str) or "TEXT" not in row.get("outputModalities", []):
                continue
            if "TEXT" not in row.get("inputModalities", []):
                continue
            model = row["modelId"]
            result.append(model_info({"id": model, "name": row.get("modelName", model),
                "vision": "IMAGE" in row.get("inputModalities", []),
                "tools": model.startswith(("anthropic.claude-3", "anthropic.claude-sonnet-4", "anthropic.claude-opus-4", "amazon.nova"))}, kind="bedrock"))
        return result
