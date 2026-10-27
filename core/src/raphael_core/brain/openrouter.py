"""Client for OpenRouter's API: JSON that fits a schema from a chat model, transcriptions, and
what the voice assistant asks (a decision from Jev, text from a model that searched the web).

A call that fails raises an OpenRouterError that says what kind of failure it was, so callers
need not know the exceptions of httpx or of the key store: a key provider that fails is an auth
error, with the original exception chained. A cancellation passes through untouched, as
asyncio.wait_for needs. The key is read through `key_provider` for each call and goes only into
the Authorization header. Nothing here logs it: a failure is logged with its status and kind, and
the type of the exception behind it when there is one, and nothing else.
"""

import asyncio
import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .catalog import CatalogEntry

import httpx

from .settings import DEFAULTS

logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
# Jev's decisions are an alpha API, outside /api/v1.
ALPHA_URL = "https://openrouter.ai/api/alpha"

DEFAULT_CLEANUP_MODEL: str = DEFAULTS["cleanup_model"]
# Tried in this order when the chosen model fails. All three models can run with reasoning off,
# which google/gemini-3.8-flash cannot.
CLEANUP_FALLBACKS = ["anthropic/claude-haiku-5.5", "google/gemini-3.1-flash-lite"]

# Sent with every request: OpenRouter lists them as the name of the app that made the call.
_APP_HEADERS = {"HTTP-Referer": "http://localhost/raphael", "X-OpenRouter-Title": "Raphael"}
# A bearer token is visible ASCII. Anything else (a stray space, a zero-width character from a web
# page) cannot go into a header: httpx would fail on it with an error that has nothing to do with
# the key, and h11's message for it would contain the key itself.
_TOKEN = re.compile(r"[\x21-\x7e]+")

# Transcription (POST /audio/transcriptions). Options for a provider go under
# provider.options.<tag>, where the tag is the one GET /models/{author}/{slug}/endpoints lists, and
# reach only that provider. gpt-transcribe (tag "openai") takes `keywords`, a free-form `prompt` and
# `languages`, which replaces the singular `language` (OpenAI's speech-to-text guide); Whisper on
# Groq takes a `prompt` (OpenRouter's speech-to-text guide). The other models get the language only:
# their hint fields are not documented, and some providers (Azure) refuse a field they do not know.
# The models that are given the dictionary that way, for the settings page to say so.
STT_MODELS_WITH_HINTS = ("openai/gpt-transcribe", "openai/whisper-large-v3-turbo")
MAX_KEYWORDS = 50
MAX_KEYWORD_CHARS = 64
_MAX_WHISPER_PROMPT_CHARS = 200
_TRANSCRIBE_CONTEXT = "口述聽寫。中文用繁體字（台灣用語），常夾雜英文技術名詞。"
_WHISPER_PROMPT_PREFIX = "詞彙："
_KEYWORD_FORBIDDEN = re.compile(r"[<>\r\n]")


@dataclass
class Completion:
    data: dict  # the model's reply, parsed
    model: str  # the model that answered, which can be one of the fallbacks
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int  # of the HTTP call
    usage_known: bool = True


@dataclass
class Decision:
    """What Jev answered. Each answer is as the API gives it: `{"type": "choice", "choice": ...,
    "confidence": ..., "probabilities": {...}}` or `{"type": "noul", "noul": ...}`."""

    answers: dict[str, Any]
    cost_usd: float  # always a number the spend can store: 0 or more, and finite
    latency_ms: int  # of the HTTP call


@dataclass
class TextCompletion:
    text: str
    model: str  # the model that answered, which can be one of the fallbacks
    cost_usd: float  # always a number the spend can store: 0 or more, and finite
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int  # of the HTTP call
    sources: list[dict[str, str]]  # {"title", "url"} of the pages a web search drew on
    usage_known: bool = True


@dataclass
class ToolCall:
    """A call the model asked for: the name it was offered the tool under, and the arguments as the
    model wrote them (JSON that was parsed; an object, if the model did as asked)."""

    name: str
    arguments: Any


@dataclass
class ToolPlan:
    calls: list[ToolCall]  # none when the model asked for no tool
    model: str  # the model that answered, which can be one of the fallbacks
    cost_usd: float  # always a number the spend can store: 0 or more, and finite
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int  # of the HTTP call
    usage_known: bool = True


@dataclass
class ChatToolCall:
    """A call the model asked for in a conversation: the id it gave the call (the result is sent
    back under it), the name it was offered the tool under and the arguments. `arguments` is the
    JSON the model wrote, parsed (an object, if the model did as asked), or None when what it wrote
    is not JSON; `raw_arguments` is the text as it came, which goes back to the model as it wrote
    it."""

    id: str
    name: str
    arguments: Any
    raw_arguments: str


@dataclass
class ChatTurn:
    """One reply of a model in a conversation with tools: its words (`content`, which is the
    answer when `calls` is empty) and the calls it asks for."""

    content: str
    calls: list[ChatToolCall]
    model: str  # the model that answered, which can be one of the fallbacks
    cost_usd: float  # always a number the spend can store: 0 or more, and finite
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int  # of the HTTP call
    # Whether the reply supplied a usable cost (or a provider adapter priced its usage). When
    # unknown, cost_usd 0.0 is not a price: a spending guard must charge an estimate for it.
    usage_known: bool = True


@dataclass
class KeyInfo:
    label: str
    limit: float | None  # None: the key has no spending limit
    limit_remaining: float | None
    limit_reset: str | None  # "daily", "weekly", "monthly" or None
    usage_daily: float | None
    usage_monthly: float | None
    usage_weekly: float | None = None
    usage_total: float | None = None


@dataclass
class Transcription:
    text: str
    model: str  # the model that was asked (the reply does not name one)
    cost_usd: float
    seconds: float  # of audio, as OpenRouter billed it
    input_tokens: int
    output_tokens: int
    latency_ms: int  # of the HTTP call
    usage_known: bool = True


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
    - truncated: empty output, or an output limit reached before usable JSON
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


def _usage_cost(usage: dict) -> float:
    """usage["cost"] as a cost the spend tracker takes: 0.0 if it is missing, no number, negative
    or not finite."""
    cost = _usage_amount(usage, "cost")
    return cost if math.isfinite(cost) and cost >= 0 else 0.0


def _known_cost(usage: dict) -> bool:
    return _is_number(usage.get("cost")) and _usage_cost(usage) == usage.get("cost")


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
        empty = isinstance(message, dict) and (content is None or isinstance(content, str) and not content.strip())
        if empty or choice.get("finish_reason") == "length":
            logger.warning("Completion failed: model=%s kind=truncated", requested_model)
            raise OpenRouterError(reply.status, "truncated", "Incomplete JSON response") from None
        raise _failure(reply.status, "bad_response", "The reply is not valid JSON") from exc
    if not isinstance(data, dict):
        if choice.get("finish_reason") == "length":
            logger.warning("Completion failed: model=%s kind=truncated", requested_model)
            raise OpenRouterError(reply.status, "truncated", "Incomplete JSON response") from None
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
        usage_known=body.get("_usage_known", _known_cost(usage)),
    )


def _usage_of(body: Any) -> dict:
    raw_usage = body.get("usage") if isinstance(body, dict) else None
    return raw_usage if isinstance(raw_usage, dict) else {}


def _to_decision(reply: _Reply) -> Decision:
    answers = reply.body.get("answers") if isinstance(reply.body, dict) else None
    if not isinstance(answers, dict):
        raise _failure(reply.status, "bad_response", "The reply has no answers")
    return Decision(
        answers=answers,
        cost_usd=_usage_cost(_usage_of(reply.body)),
        latency_ms=reply.latency_ms,
    )


_MAX_SOURCES = 5


def _sources(annotations: Any) -> list[dict[str, str]]:
    """The pages a reply cites: the `url_citation` annotations that have a URL, each page once."""
    sources: list[dict[str, str]] = []
    if not isinstance(annotations, list):
        return sources
    for annotation in annotations:
        if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
            continue
        citation = annotation.get("url_citation")
        url = citation.get("url") if isinstance(citation, dict) else None
        if not isinstance(url, str) or not url or any(s["url"] == url for s in sources):
            continue
        title = citation.get("title")
        sources.append({"title": title if isinstance(title, str) else "", "url": url})
        if len(sources) == _MAX_SOURCES:
            break
    return sources


def _to_text_completion(reply: _Reply, requested_model: str) -> TextCompletion:
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
    if not isinstance(content, str) or not content.strip():
        raise _failure(reply.status, "bad_response", "The reply has no text")
    usage = _usage_of(body)
    answered_by = body.get("model")
    return TextCompletion(
        text=content.strip(),
        model=answered_by if isinstance(answered_by, str) and answered_by else requested_model,
        cost_usd=_usage_cost(usage),
        prompt_tokens=_usage_count(usage, "prompt_tokens"),
        completion_tokens=_usage_count(usage, "completion_tokens"),
        latency_ms=reply.latency_ms,
        sources=_sources(message.get("annotations")),
        usage_known=body.get("_usage_known", _known_cost(usage)),
    )


def _to_tool_plan(reply: _Reply, requested_model: str) -> ToolPlan:
    body = reply.body
    choices = body.get("choices") if isinstance(body, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict):
        raise _failure(reply.status, "bad_response", "The reply has no choices")
    # Checked before the calls: a choice that failed midway holds a part of the reply.
    if choice.get("finish_reason") == "error":
        raise _reported_error(
            _error_object(choice), reply.status, "The model ended its reply with an error"
        )
    message = choice.get("message")
    raw_calls = message.get("tool_calls") if isinstance(message, dict) else None
    calls: list[ToolCall] = []
    for raw in raw_calls if isinstance(raw_calls, list) else []:
        function = raw.get("function") if isinstance(raw, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                continue  # a call whose arguments are not JSON cannot be made
        calls.append(ToolCall(name=name, arguments=arguments))
    usage = _usage_of(body)
    answered_by = body.get("model")
    return ToolPlan(
        calls=calls,
        model=answered_by if isinstance(answered_by, str) and answered_by else requested_model,
        cost_usd=_usage_cost(usage),
        prompt_tokens=_usage_count(usage, "prompt_tokens"),
        completion_tokens=_usage_count(usage, "completion_tokens"),
        latency_ms=reply.latency_ms,
        usage_known=body.get("_usage_known", _known_cost(usage)),
    )


def _content_text(content: Any) -> str:
    """The words of a message: a string, or a list of parts of which the text ones count."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "".join(parts)
    return ""


def _to_chat_turn(reply: _Reply, requested_model: str) -> ChatTurn:
    body = reply.body
    choices = body.get("choices") if isinstance(body, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict):
        raise _failure(reply.status, "bad_response", "The reply has no choices")
    if choice.get("finish_reason") == "error":
        raise _reported_error(
            _error_object(choice), reply.status, "The model ended its reply with an error"
        )
    message = choice.get("message")
    message = message if isinstance(message, dict) else {}
    raw_calls = message.get("tool_calls")
    calls: list[ChatToolCall] = []
    for index, raw in enumerate(raw_calls if isinstance(raw_calls, list) else []):
        function = raw.get("function") if isinstance(raw, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not name:
            continue
        written = function.get("arguments")
        arguments: Any = written
        text = written if isinstance(written, str) else json.dumps(written or {})
        if isinstance(written, str):
            try:
                arguments = json.loads(written) if written.strip() else {}
            except ValueError:
                arguments = None  # the model wrote something that is not JSON
        call_id = raw.get("id")
        calls.append(
            ChatToolCall(
                id=call_id if isinstance(call_id, str) and call_id else f"unnamed_call_{index}",
                name=name,
                arguments=arguments,
                raw_arguments=text,
            )
        )
    usage = _usage_of(body)
    answered_by = body.get("model")
    return ChatTurn(
        content=_content_text(message.get("content")),
        calls=calls,
        model=answered_by if isinstance(answered_by, str) and answered_by else requested_model,
        cost_usd=_usage_cost(usage),
        prompt_tokens=_usage_count(usage, "prompt_tokens"),
        completion_tokens=_usage_count(usage, "completion_tokens"),
        latency_ms=reply.latency_ms,
        usage_known=_known_cost(usage),
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
        usage_daily=_amount_or_none(data.get("usage_daily")),
        usage_monthly=_amount_or_none(data.get("usage_monthly")),
        usage_weekly=_amount_or_none(data.get("usage_weekly")),
        usage_total=_amount_or_none(data.get("usage")),
    )


def _keywords(hints: Sequence[str]) -> list[str]:
    """The hints as keywords a provider takes: trimmed, on one line, without `<` or `>` (OpenAI
    refuses the whole request for such a keyword), each at most MAX_KEYWORD_CHARS long, in order,
    MAX_KEYWORDS at most. A hint that breaks a rule is left out, not cut."""
    kept: list[str] = []
    for hint in hints:
        word = hint.strip()
        if not word or len(word) > MAX_KEYWORD_CHARS or _KEYWORD_FORBIDDEN.search(word):
            continue
        if word not in kept:
            kept.append(word)
        if len(kept) == MAX_KEYWORDS:
            break
    return kept


def _whisper_prompt(keywords: list[str]) -> str:
    """The keywords as a short Whisper prompt (Whisper reads at most 224 tokens of it)."""
    prompt = _WHISPER_PROMPT_PREFIX
    for word in keywords:
        piece = word if prompt == _WHISPER_PROMPT_PREFIX else f"、{word}"
        if len(prompt) + len(piece) > _MAX_WHISPER_PROMPT_CHARS:
            break
        prompt += piece
    return prompt


def transcription_body(
    model: str,
    audio_b64: str,
    audio_format: str,
    *,
    language: str | None,
    hints: Sequence[str],
    languages: Sequence[str] = (),
) -> dict[str, Any]:
    """The JSON body of a transcription of `audio_b64` by `model`.

    `language` is an ISO-639-1 code, or None for automatic. `languages` are the codes automatic
    detection may choose among; only gpt-transcribe takes such a list (as `languages`, which a
    chosen `language` replaces), the other models detect the language on their own. The hints and
    the language reach the
    model the way it documents (see MAX_KEYWORDS above). `data_collection: "deny"` keeps the audio
    away from providers that train on what they are sent.
    """
    body: dict[str, Any] = {
        "model": model,
        "input_audio": {"data": audio_b64, "format": audio_format},
    }
    provider: dict[str, Any] = {"data_collection": "deny"}
    keywords = _keywords(hints)
    if model == "openai/gpt-transcribe":
        options: dict[str, Any] = {"prompt": _TRANSCRIBE_CONTEXT}
        if keywords:
            options["keywords"] = keywords
        if language:
            options["languages"] = [language]
        elif languages:
            options["languages"] = list(languages)
        provider["options"] = {"openai": options}
    else:
        if language:
            body["language"] = language
        if model == "openai/whisper-large-v3-turbo" and keywords:
            provider["options"] = {"groq": {"prompt": _whisper_prompt(keywords)}}
    body["provider"] = provider
    return body


def _to_transcription(reply: _Reply, model: str) -> Transcription:
    body = reply.body
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise _failure(reply.status, "bad_response", "The reply has no text")
    raw_usage = body.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    return Transcription(
        text=text,
        model=model,
        cost_usd=_usage_amount(usage, "cost"),
        seconds=_usage_amount(usage, "seconds"),
        input_tokens=_usage_count(usage, "input_tokens"),
        output_tokens=_usage_count(usage, "output_tokens"),
        latency_ms=reply.latency_ms,
        usage_known=_known_cost(usage),
    )


class OpenRouterClient:
    """Calls OpenRouter with the key that `key_provider` returns.

    `key_provider` returns the key, or None when there is none. It is called for each request, so
    a key that is replaced takes effect at once; if it raises, the call fails with an auth error.
    `http` is the httpx client to use, which the caller then owns. Without one, the client makes
    its own on first use, and `aclose` closes it.
    """

    def __init__(
        self, key_provider: Callable[[], str | None], http: httpx.AsyncClient | None = None,
        *, model_catalog: Callable[[], Sequence["CatalogEntry"] | None] | None = None,
    ) -> None:
        self._key_provider = key_provider
        self._model_catalog = model_catalog
        self._http = http
        self._own_http: httpx.AsyncClient | None = None
        self._own_http_lock = threading.Lock()  # warm_up runs on another thread than the requests
        self._connection_warm: asyncio.Task | None = None

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
        reasoning_effort: str = "none",
    ) -> Completion:
        """Ask `model` for JSON that fits `schema`; OpenRouter tries `fallbacks` if it fails.

        Only the parameters OpenRouter documents for structured output are sent. In particular
        there is no temperature or top_p: with require_parameters, a model that cannot take one
        (gpt-6-luna has no temperature; claude-haiku-5.5 rejects any value but its default) would
        be left out. Unless an answer effort is explicitly chosen, the cached catalog decides:
        turn reasoning off where supported, omit it where absent, or use the lowest listed effort
        where required (minimal, then low, then the first listed; low if no efforts are listed).
        An unknown model retains the existing "none" default. Reading the catalog never fetches it.
        `data_collection: "deny"` keeps the dictated text away from providers that may store or
        train on what they are sent, as it does for the audio of a transcription
        (`transcription_body`). `timeout_s` applies to each stage of the request (connect, send,
        wait, read), so the caller still needs a deadline of its own for the whole call.
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
            "provider": {"require_parameters": True, "sort": "latency", "data_collection": "deny"},
            "max_tokens": max_tokens,
        }
        effort = reasoning_effort
        if effort == "none" and self._model_catalog is not None:
            entry = next((e for e in self._model_catalog() or () if e.id == model), None)
            if entry is not None:
                if "reasoning" not in entry.capabilities:
                    effort = None
                elif "reasoning_off" not in entry.capabilities:
                    efforts = entry.reasoning_efforts or ("low",)
                    effort = next((e for e in ("minimal", "low") if e in efforts), efforts[0])
        if effort is not None:
            body["reasoning"] = {"effort": effort}
        # The chosen model is tried first, so listing it again would only repeat a failed attempt.
        models = [m for m in fallbacks if m != model]
        if models:
            body["models"] = models
        reply = await self._request(
            "POST", "/chat/completions", self._stored_key(), timeout_s, body
        )
        return _to_completion(reply, model)

    async def decide(
        self, *, model: str, state: dict, questions: dict, timeout_s: float
    ) -> Decision:
        """Put `questions` about `state` to a decision model (Jev) and return its answers.

        One request answers every question. A `choice` question has the criteria of each option
        and is answered with the option and a confidence; a `noul` one has a criterion for true
        and one for false and is answered with a number. `timeout_s` applies to each stage of the
        request, like complete_json's, so the caller still needs a deadline of its own. The state
        is what the user said, so `data_collection: "deny"` keeps it away from providers that may
        store or train on it, as every other call does (the decisions API takes the same provider
        preferences).
        """
        body = {
            "model": model,
            "state": state,
            "questions": questions,
            "provider": {"data_collection": "deny"},
        }
        reply = await self._request(
            "POST", "/decisions", self._stored_key(), timeout_s, body, base_url=ALPHA_URL
        )
        return _to_decision(reply)

    async def chat_text(
        self,
        *,
        model: str,
        system: str,
        user: str | Sequence[dict],
        timeout_s: float,
        max_tokens: int,
        plugins: Sequence[dict] | None = None,
        reasoning_effort: str = "none",
    ) -> TextCompletion:
        """Ask `model` for text, with the OpenRouter `plugins` given (`{"id": "web",
        "max_results": 3}` lets it search the web first). The pages it drew on are in `sources`.
        `user` is the text of the user's message, or its content parts (`{"type": "text"}`,
        `{"type": "image_url"}`, `{"type": "file"}`: an image or a PDF for a model to read, with the
        `file-parser` plugin for the PDF; raphael_core.services.vision).

        Like complete_json, this sends no temperature and denies data collection. Unlike it, the
        reply is not JSON and the routing does not require every parameter, since the plugins are
        OpenRouter's own and no provider has to know them.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user if isinstance(user, str) else list(user)},
            ],
            "provider": {"sort": "latency", "data_collection": "deny"},
            "reasoning": {"effort": reasoning_effort},
            "max_tokens": max_tokens,
        }
        if plugins:
            body["plugins"] = list(plugins)
        reply = await self._request(
            "POST", "/chat/completions", self._stored_key(), timeout_s, body
        )
        return _to_text_completion(reply, model)

    async def plan_tools(
        self,
        *,
        model: str,
        fallbacks: list[str],
        system: str,
        user: str,
        tools: Sequence[dict],
        timeout_s: float,
        max_tokens: int,
        reasoning_effort: str = "none",
    ) -> ToolPlan:
        """Ask `model` which of `tools` (in the chat format: `{"type": "function", "function":
        {name, description, parameters}}`) the request needs, if any. The model answers with the
        calls it wants and nothing else; none of them is made here.

        Like complete_json, this requires every parameter from the provider (a provider that cannot
        take tools is left out), sends no temperature, denies data collection and tries the
        `fallbacks` if the model fails.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": list(tools),
            "tool_choice": "auto",
            "provider": {"require_parameters": True, "sort": "latency", "data_collection": "deny"},
            "reasoning": {"effort": reasoning_effort},
            "max_tokens": max_tokens,
        }
        models = [m for m in fallbacks if m != model]
        if models:
            body["models"] = models
        reply = await self._request(
            "POST", "/chat/completions", self._stored_key(), timeout_s, body
        )
        return _to_tool_plan(reply, model)

    async def chat_tools(
        self,
        *,
        model: str,
        fallbacks: list[str],
        messages: Sequence[dict],
        tools: Sequence[dict],
        timeout_s: float,
        max_tokens: int,
        reasoning_effort: str = "none",
    ) -> ChatTurn:
        """One turn of a conversation in which `model` may call `tools` (the chat format, as for
        plan_tools). `messages` are the whole conversation so far, in the chat format: the system
        message, the user's, the model's own earlier replies with their `tool_calls`, and a `tool`
        message for the result of each call. The reply holds the words of the model and the calls it
        asks for; none of them is made here. With no `tools` the model can only answer.

        Like plan_tools, this requires every parameter from the provider (a provider that cannot
        take tools is left out), sends no temperature, denies data collection and tries the
        `fallbacks` if the model fails.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "provider": {"require_parameters": True, "sort": "latency", "data_collection": "deny"},
            "reasoning": {"effort": reasoning_effort},
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"
        models = [m for m in fallbacks if m != model]
        if models:
            body["models"] = models
        reply = await self._request(
            "POST", "/chat/completions", self._stored_key(), timeout_s, body
        )
        return _to_chat_turn(reply, model)

    async def transcribe(
        self,
        *,
        model: str,
        audio_b64: str,
        language: str | None,
        hints: Sequence[str],
        timeout_s: float,
        audio_format: str = "wav",
        languages: Sequence[str] = (),
    ) -> Transcription:
        """The text `model` hears in `audio_b64` (base64 of the file's bytes, not a data URI).

        `language` is an ISO-639-1 code or None for automatic, and `languages` the codes automatic
        detection may choose among (see `transcription_body`); `hints` are words the model should
        write as given. `timeout_s` applies to each stage of the request, like complete_json's.
        """
        body = transcription_body(
            model, audio_b64, audio_format, language=language, hints=hints, languages=languages
        )
        reply = await self._request(
            "POST", "/audio/transcriptions", self._stored_key(), timeout_s, body
        )
        return _to_transcription(reply, model)

    async def key_info(self, key: str | None = None, timeout_s: float = 5.0) -> KeyInfo:
        """What OpenRouter says about `key`, or about the stored key when `key` is None.

        This is how a key is checked: a key OpenRouter rejects raises an auth error. A `key` that
        is given but blank is not replaced by the stored one: it is an auth error too.
        """
        key = self._stored_key() if key is None else key
        return _to_key_info(await self._request("GET", "/key", key, timeout_s))

    async def models(self, timeout_s: float = 5.0) -> list[dict]:
        """Every model OpenRouter lists, of every kind (text, transcription, image, ...), as the
        API gives them: the `data` list of GET /models?output_modalities=all.

        The list is public, so no key is sent and none is needed: this works without one, and with
        a key store that fails. `timeout_s` applies to each stage of the request, like the others.
        """
        reply = await self._request(
            "GET", "/models?output_modalities=all", None, timeout_s, public=True
        )
        data = reply.body.get("data") if isinstance(reply.body, dict) else None
        if not isinstance(data, list):
            raise _failure(reply.status, "bad_response", "The reply is not a list of models")
        return data

    def warm_up(self) -> None:
        """Make the httpx client now instead of at the first request.

        Building it takes over a second (it loads the TLS certificates), which a dictation should
        not wait for. Makes no request. Safe to call from any thread, and more than once.
        """
        self._client()

    def start_connection_warmup(self) -> None:
        """Start TLS while recording, without delaying the microphone or making a paid call."""
        if self._connection_warm is None or self._connection_warm.done():
            self._connection_warm = asyncio.create_task(self.warm_connection())

    async def warm_connection(self) -> None:
        try:
            client = await asyncio.to_thread(self._client)
            await client.head(BASE_URL + "/models", headers=_APP_HEADERS, timeout=2.0)
        except Exception as exc:
            logger.debug("OpenRouter connection warmup failed: %s", type(exc).__name__)

    async def aclose(self) -> None:
        """Close the httpx client this object made. One it was given stays open.

        A call after this makes a new client.
        """
        if self._connection_warm is not None and not self._connection_warm.done():
            self._connection_warm.cancel()
            await asyncio.gather(self._connection_warm, return_exceptions=True)
        self._connection_warm = None
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
        with self._own_http_lock:
            if self._own_http is None:
                # Dictations can be minutes apart, and httpx drops an idle connection after 5 s.
                # Two minutes spares most dictations the DNS, TCP and TLS set-up. A connection the
                # server closed in the meantime is noticed and replaced before it is used.
                limits = httpx.Limits(keepalive_expiry=120)
                try:
                    self._own_http = httpx.AsyncClient(http2=True, limits=limits)
                except ImportError:
                    # An existing dev venv may not have been synced after the HTTP/2 extra was
                    # added. Keep dictation usable until `uv sync --locked` can install it.
                    logger.warning("HTTP/2 dependencies missing; using HTTP/1.1 until uv sync --locked")
                    self._own_http = httpx.AsyncClient(limits=limits)
            return self._own_http

    async def _request(
        self,
        method: str,
        path: str,
        key: str | None,
        timeout_s: float,
        body: dict | None = None,
        *,
        public: bool = False,
        base_url: str = BASE_URL,
    ) -> _Reply:
        """Send one request and return the decoded reply, or raise the OpenRouterError it earns.

        A `public` request is for an endpoint that needs no key: `key` is ignored and not sent.
        `path` is under `base_url`.
        """
        headers = dict(_APP_HEADERS)
        if not public:
            headers["Authorization"] = _authorization(key)
        client = self._client()  # made before the clock starts: building it takes a while
        started = time.perf_counter()
        try:
            response = await client.request(
                method, base_url + path, headers=headers, json=body, timeout=timeout_s
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
