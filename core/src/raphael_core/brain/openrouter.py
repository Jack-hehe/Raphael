"""Client for OpenRouter's chat completions API: asks a model for JSON that fits a schema.

A call that fails raises an OpenRouterError that says what kind of failure it was, so callers
need not know the exceptions of httpx or of the key store: a key provider that fails is an auth
error, with the original exception chained. A cancellation passes through untouched, as
asyncio.wait_for needs. The key is read through `key_provider` for each call and goes only into
the Authorization header. Nothing here logs it: a failure is logged with its status and kind, and
the type of the exception behind it when there is one, and nothing else.
"""

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

from .settings import DEFAULTS

logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_CLEANUP_MODEL: str = DEFAULTS["cleanup_model"]
# Tried in this order when the chosen model fails. All three models can run with reasoning off,
# which google/gemini-3.8-flash cannot.
CLEANUP_FALLBACKS = ["openai/gpt-6-luna", "anthropic/claude-haiku-5.5"]
CLEANUP_MODEL_CHOICES = [DEFAULT_CLEANUP_MODEL, *CLEANUP_FALLBACKS]

# Sent with every request: OpenRouter lists them as the name of the app that made the call.
_APP_HEADERS = {"HTTP-Referer": "http://localhost/raphael", "X-OpenRouter-Title": "Raphael"}
# A bearer token is visible ASCII. Anything else (a stray space, a zero-width character from a web
# page) cannot go into a header: httpx would fail on it with an error that has nothing to do with
# the key, and h11's message for it would contain the key itself.
_TOKEN = re.compile(r"[\x21-\x7e]+")


@dataclass
class Completion:
    data: dict  # the model's reply, parsed
    model: str  # the model that answered, which can be one of the fallbacks
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int  # of the HTTP call


@dataclass
class KeyInfo:
    label: str
    limit: float | None  # None: the key has no spending limit
    limit_remaining: float | None
    limit_reset: str | None  # "daily", "weekly", "monthly" or None
    usage_daily: float
    usage_monthly: float


class OpenRouterError(Exception):
    """A call that did not produce a result.

    `status` is the HTTP status, or the code OpenRouter reports inside a 200 reply, or 0 when
    there was no reply. `kind` says what went wrong:

    - auth: no key, a key that cannot be read or cannot be one, or a key OpenRouter rejects (401)
    - credits: no credit left, or a spending limit reached (402, or 403 naming a limit)
    - rate_limit: 429
    - bad_request: the request was refused (400, a 403 that is no limit, any other 4xx)
    - server: a 5xx, or an error OpenRouter reports inside a 200 reply
    - timeout: no answer in time (httpx timeout, or 408)
    - network: no connection
    - bad_response: a reply that is not what the API sends
    """

    def __init__(self, status: int, kind: str, message: str) -> None:
        super().__init__(status, kind, message)
        self.status = status
        self.kind = kind
        self.message = message

    def __str__(self) -> str:
        return self.message


@dataclass
class _Reply:
    status: int
    body: Any  # the decoded JSON
    latency_ms: int


def _failure(
    status: int, kind: str, message: str, cause: Exception | None = None
) -> OpenRouterError:
    """The error to raise, after logging its status and kind as a warning.

    Nothing else is logged but the type of `cause`, when there is one: its message could hold
    anything, a key included.
    """
    if cause is None:
        logger.warning("OpenRouter call failed: status=%s kind=%s", status, kind)
    else:
        logger.warning(
            "OpenRouter call failed: status=%s kind=%s cause=%s", status, kind, type(cause).__name__
        )
    return OpenRouterError(status, kind, message)


def _authorization(key: str | None) -> str:
    """The Authorization header for `key`; an auth error, before any request, if it is no key."""
    key = (key or "").strip()
    if not key:
        raise OpenRouterError(0, "auth", "No OpenRouter key")
    if not _TOKEN.fullmatch(key):
        raise OpenRouterError(0, "auth", "The OpenRouter key has characters a key cannot have")
    return f"Bearer {key}"


def _described(exc: Exception) -> str:
    # httpx often gives a timeout no message at all.
    return str(exc) or type(exc).__name__


def _error_object(body: object) -> dict:
    """The `error` object of an OpenRouter reply or choice, or {} when there is none."""
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def _message(error: dict, default: str) -> str:
    message = error.get("message")
    return message if isinstance(message, str) and message else default


_KIND_OF_STATUS = {
    HTTPStatus.UNAUTHORIZED: "auth",
    HTTPStatus.PAYMENT_REQUIRED: "credits",
    HTTPStatus.REQUEST_TIMEOUT: "timeout",
    HTTPStatus.TOO_MANY_REQUESTS: "rate_limit",
}


def _kind_for(status: int, error: dict) -> str:
    """The kind of error that an HTTP status other than 2xx stands for."""
    if status == HTTPStatus.FORBIDDEN:
        # A budget block names where the limit comes from (the key, a workspace, a guardrail).
        # Any other 403 refuses this request: moderation, a guardrail rule, missing permission.
        metadata = error.get("metadata")
        limited = isinstance(metadata, dict) and metadata.get("limit_source")
        return "credits" if limited else "bad_request"
    if status in _KIND_OF_STATUS:
        return _KIND_OF_STATUS[status]
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return "server"
    if status >= HTTPStatus.BAD_REQUEST:
        return "bad_request"
    return "bad_response"  # a redirect, say: a captive portal answering instead of OpenRouter


def _reported_error(error: dict, status: int, default_message: str) -> OpenRouterError:
    """The error for a reply that says, inside a 200, that it failed: it carries its own code."""
    code = error.get("code")
    return _failure(
        code if type(code) is int else status, "server", _message(error, default_message)
    )


def _is_number(value: object) -> bool:
    """Whether `value` is a JSON number. A bool is not, though Python makes it an int."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _amount(value: object) -> float:
    return value if _is_number(value) else 0.0


def _amount_or_none(value: object) -> float | None:
    return value if _is_number(value) else None


def _warn_unusable(field: str, value: object) -> None:
    """Log that `usage[field]` is present but no use, naming the field and never the value.

    A missing or null field is normal, since a reply need not carry a cost. A cost that read as 0
    without a word would keep the monthly limit from ever being reached.
    """
    if value is not None:
        logger.warning("OpenRouter usage.%s is not a usable number, counted as 0", field)


def _usage_amount(usage: dict, field: str) -> float:
    """usage[field], or 0.0 if it is missing, null or no number."""
    value = usage.get(field)
    if _is_number(value):
        return value
    _warn_unusable(field, value)
    return 0.0


def _usage_count(usage: dict, field: str) -> int:
    """usage[field], or 0 if it is missing, null or no whole number."""
    value = usage.get(field)
    if isinstance(value, int) and _is_number(value):
        return value
    _warn_unusable(field, value)
    return 0


def _to_completion(reply: _Reply, requested_model: str) -> Completion:
    body = reply.body
    choices = body.get("choices") if isinstance(body, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict):
        raise _failure(reply.status, "bad_response", "The reply has no choices")
    # Checked before the content: a choice that failed midway holds a part of the reply.
    if choice.get("finish_reason") == "error":
        raise _reported_error(
            _error_object(choice), reply.status, "The model ended its reply with an error"
        )

    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    try:
        data = json.loads(content)
    except (TypeError, ValueError) as exc:  # not a string, or not JSON
        raise _failure(reply.status, "bad_response", "The reply is not valid JSON") from exc
    if not isinstance(data, dict):
        raise _failure(reply.status, "bad_response", "The reply is JSON but not an object")

    raw_usage = body.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    answered_by = body.get("model")
    return Completion(
        data=data,
        model=answered_by if isinstance(answered_by, str) and answered_by else requested_model,
        cost_usd=_usage_amount(usage, "cost"),
        prompt_tokens=_usage_count(usage, "prompt_tokens"),
        completion_tokens=_usage_count(usage, "completion_tokens"),
        latency_ms=reply.latency_ms,
    )


def _to_key_info(reply: _Reply) -> KeyInfo:
    data = reply.body.get("data") if isinstance(reply.body, dict) else None
    if not isinstance(data, dict):
        raise _failure(reply.status, "bad_response", "The reply has no key data")
    label = data.get("label")
    limit_reset = data.get("limit_reset")
    return KeyInfo(
        label=label if isinstance(label, str) else "",
        limit=_amount_or_none(data.get("limit")),
        limit_remaining=_amount_or_none(data.get("limit_remaining")),
        limit_reset=limit_reset if isinstance(limit_reset, str) else None,
        usage_daily=_amount(data.get("usage_daily")),
        usage_monthly=_amount(data.get("usage_monthly")),
    )


class OpenRouterClient:
    """Calls OpenRouter with the key that `key_provider` returns.

    `key_provider` returns the key, or None when there is none. It is called for each request, so
    a key that is replaced takes effect at once; if it raises, the call fails with an auth error.
    `http` is the httpx client to use, which the caller then owns. Without one, the client makes
    its own on first use, and `aclose` closes it.
    """

    def __init__(
        self, key_provider: Callable[[], str | None], http: httpx.AsyncClient | None = None
    ) -> None:
        self._key_provider = key_provider
        self._http = http
        self._own_http: httpx.AsyncClient | None = None

    async def complete_json(
        self,
        *,
        model: str,
        fallbacks: list[str],
        system: str,
        user: str,
        schema: dict,
        schema_name: str,
        timeout_s: float,
        max_tokens: int,
    ) -> Completion:
        """Ask `model` for JSON that fits `schema`; OpenRouter tries `fallbacks` if it fails.

        Only the parameters OpenRouter documents for structured output are sent. In particular
        there is no temperature or top_p: with require_parameters, a model that cannot take one
        (gpt-6-luna has no temperature; claude-haiku-5.5 rejects any value but its default) would
        be left out. Reasoning is off. `timeout_s` applies to each stage of the request (connect,
        send, wait, read), so the caller still needs a deadline of its own for the whole call.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "provider": {"require_parameters": True, "sort": "latency"},
            "reasoning": {"effort": "none"},
            "max_tokens": max_tokens,
        }
        # The chosen model is tried first, so listing it again would only repeat a failed attempt.
        models = [m for m in fallbacks if m != model]
        if models:
            body["models"] = models
        reply = await self._request(
            "POST", "/chat/completions", self._stored_key(), timeout_s, body
        )
        return _to_completion(reply, model)

    async def key_info(self, key: str | None = None, timeout_s: float = 5.0) -> KeyInfo:
        """What OpenRouter says about `key`, or about the stored key when `key` is None.

        This is how a key is checked: a key OpenRouter rejects raises an auth error. A `key` that
        is given but blank is not replaced by the stored one: it is an auth error too.
        """
        key = self._stored_key() if key is None else key
        return _to_key_info(await self._request("GET", "/key", key, timeout_s))

    async def aclose(self) -> None:
        """Close the httpx client this object made. One it was given stays open.

        A call after this makes a new client.
        """
        own_http, self._own_http = self._own_http, None
        if own_http is not None:
            await own_http.aclose()

    def _stored_key(self) -> str | None:
        """The key from the key provider, or an auth error if the provider itself fails.

        A key store can fail in ways of its own (a locked vault, no keyring backend). Callers fall
        back to the raw text on an OpenRouterError, and only on that.
        """
        try:
            return self._key_provider()
        except Exception as exc:
            raise _failure(0, "auth", "Could not read the OpenRouter key", exc) from exc

    def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        if self._own_http is None:
            # Dictations can be minutes apart, and httpx drops an idle connection after 5 s. Two
            # minutes spares most dictations the DNS, TCP and TLS set-up. A connection the server
            # closed in the meantime is noticed and replaced before it is used.
            self._own_http = httpx.AsyncClient(limits=httpx.Limits(keepalive_expiry=120))
        return self._own_http

    async def _request(
        self, method: str, path: str, key: str | None, timeout_s: float, body: dict | None = None
    ) -> _Reply:
        """Send one request and return the decoded reply, or raise the OpenRouterError it earns."""
        headers = {"Authorization": _authorization(key), **_APP_HEADERS}
        client = self._client()  # made before the clock starts: building it takes a while
        started = time.perf_counter()
        try:
            response = await client.request(
                method, BASE_URL + path, headers=headers, json=body, timeout=timeout_s
            )
        except httpx.TimeoutException as exc:
            raise _failure(0, "timeout", _described(exc)) from exc
        except httpx.DecodingError as exc:  # a body that does not match its Content-Encoding
            raise _failure(0, "bad_response", _described(exc)) from exc
        except httpx.TransportError as exc:
            raise _failure(0, "network", _described(exc)) from exc
        latency_ms = round((time.perf_counter() - started) * 1000)

        status = response.status_code
        try:
            decoded = response.json()
        except ValueError:  # an HTML page, an empty body, bytes that are not UTF-8
            decoded = None
        error = _error_object(decoded)

        if not response.is_success:
            raise _failure(status, _kind_for(status, error), _message(error, f"HTTP {status}"))
        if isinstance(decoded, dict) and decoded.get("error"):
            # OpenRouter answers 200 once a provider has accepted the request; a failure after
            # that is reported in the body.
            raise _reported_error(error, status, "OpenRouter reported an error")
        return _Reply(status, decoded, latency_ms)
