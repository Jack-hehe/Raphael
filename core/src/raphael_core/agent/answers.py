"""The answers: the language model words what Raphael says.

Raphael answers a question, takes part in a discussion or makes small talk by asking the model
(`agent_model`, with no reasoning: a line is short, and every second shows) for a JSON reply: a `prefix` (one of the language's set),
the `text` for the overlay, one line, and a `detail` for the history. The model is shown what it
needs and no more: the time, the last turns of the conversation (the context), the results of the
tools (data, not instructions) and the question in tags. Its reply is made safe before it is
used: the prefix is one of the language's, the text one line of about the length Raphael speaks
(what had to be left out is kept in the detail), and Chinese in Traditional characters.

Nothing here logs the question, the context, the tool results or the reply.
"""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..brain.openrouter import CLEANUP_FALLBACKS, OpenRouterClient, OpenRouterError
from ..brain.textnorm import convert_leftover_simplified
from .failure import AgentFailure, failure_kind
from .history import Turn
from .ledger import Ledger
from .persona import (
    PREFIXES,
    canonical_prefix,
    fit_text,
    language_reminder,
    phrase,
    system_prompt,
)
from .tools import ToolResult

logger = logging.getLogger(__name__)

# The longest an answer may take, and the most it may run to. The line is short and the detail is
# not. Reasoning is off: measured live, "low" made a discussion take 7 to 10 s against 2 to 3 s,
# for a line of 80 characters that read no better.
ANSWER_TIMEOUT_S = 10.0
REPLY_MAX_TOKENS = 1200


@dataclass
class Reply:
    prefix: str
    text: str  # one line, at most about 80 characters or 40 words
    detail: str | None  # the longer text for the history, if there is one


def reply_schema(language: str) -> dict:
    """The JSON the model must return: the prefixes offered are the language's own."""
    return {
        "type": "object",
        "properties": {
            "prefix": {"type": "string", "enum": list(PREFIXES[language])},
            "text": {"type": "string"},
            "detail": {"type": "string"},
        },
        "required": ["prefix", "text", "detail"],
        "additionalProperties": False,
    }


def _tool_line(result: ToolResult) -> str:
    if result.ok:
        return f"{result.name}: {json.dumps(result.data, ensure_ascii=False)}"
    return f"{result.name}: unavailable ({result.error})"


def build_user_message(
    question: str,
    moment: Mapping[str, str],
    context: Sequence[Turn],
    tools: Sequence[ToolResult],
    language: str,
) -> str:
    """What the model is shown: the date and time, the conversation so far (oldest first), the
    results of the tools, the question in tags, which mark it as the user's words, and last the
    language to reply in."""
    parts = [
        f"Current date and time: {moment['date']} {moment['time']}, {moment['weekday']} "
        f"({moment['utc_offset']})"
    ]
    if context:
        lines = [
            f"User: {turn.question}\nRaphael: {turn.text or turn.prefix}" for turn in context
        ]
        parts.append("Conversation so far (oldest first):\n" + "\n".join(lines))
    if tools:
        parts.append("Tool results:\n" + "\n".join(_tool_line(result) for result in tools))
    parts.append(f"<question>\n{question}\n</question>")
    parts.append(language_reminder(language))
    return "\n\n".join(parts)


def _one_line_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def parse_reply(data: Any, language: str) -> Reply:
    """The model's JSON as Raphael's words. AgentFailure if it says nothing.

    The prefix is turned into the language's own, or is an answer when it is none. The text is one
    line cut to the length Raphael speaks; when it had to be cut, the detail is the whole of it (or
    the detail the model wrote, if that is longer). Chinese is made Traditional, as the user
    reads it.
    """
    if not isinstance(data, Mapping):
        raise AgentFailure("error")
    full = " ".join(_one_line_text(data.get("text")).split())
    if not full:
        logger.warning("The model's reply said nothing")
        raise AgentFailure("error")
    line, rest = fit_text(full, language)
    detail = _one_line_text(data.get("detail")).strip()
    if rest and len(full) > len(detail):
        detail = full
    if language == "zh-TW":
        line = convert_leftover_simplified(line)
        detail = convert_leftover_simplified(detail)
    return Reply(
        prefix=canonical_prefix(data.get("prefix"), language, default=PREFIXES[language].answer),
        text=line,
        detail=detail if detail and detail != line else None,
    )


def _with_sources(detail: str | None, tools: Sequence[ToolResult], language: str) -> str | None:
    """`detail` followed by the pages a web search drew on, under a heading."""
    pages: list[str] = []
    for result in tools:
        if result.name == "web_search" and result.ok and result.data:
            for source in result.data.get("sources", []):
                title = source.get("title", "")
                pages.append(f"- {title} {source['url']}" if title else f"- {source['url']}")
    if not pages:
        return detail
    listed = f"{phrase(language, 'sources')}:\n" + "\n".join(pages)
    return f"{detail}\n\n{listed}" if detail else listed


class Voice:
    """Asks the model for Raphael's words. `client` is the OpenRouter client; `timeout_s` is how
    long an answer may take."""

    def __init__(self, client: OpenRouterClient, *, timeout_s: float = ANSWER_TIMEOUT_S) -> None:
        self._client = client
        self._timeout_s = timeout_s

    async def reply(
        self,
        *,
        model: str,
        language: str,
        route: str,
        question: str,
        context: Sequence[Turn],
        tools: Sequence[ToolResult],
        moment: Mapping[str, str],
        ledger: Ledger,
    ) -> Reply:
        """What Raphael says to `question` on `route` ("answer", "discuss" or "chat"), in
        `language`. AgentFailure (of a kind) if the model fails or says nothing."""
        try:
            completion = await asyncio.wait_for(
                self._client.complete_json(
                    model=model,
                    fallbacks=CLEANUP_FALLBACKS,
                    system=system_prompt(language, route),
                    user=build_user_message(question, moment, context, tools, language),
                    schema=reply_schema(language),
                    schema_name="raphael_reply",
                    timeout_s=self._timeout_s,
                    max_tokens=REPLY_MAX_TOKENS,
                    reasoning_effort="none",
                ),
                self._timeout_s,
            )
        except OpenRouterError as error:
            logger.warning("The answering model failed: kind=%s status=%s", error.kind, error.status)
            raise AgentFailure(failure_kind(error)) from None
        except TimeoutError:
            logger.warning("The answering model timed out")
            raise AgentFailure("offline") from None
        except Exception as exc:
            logger.warning("The answering model failed unexpectedly: %s", type(exc).__name__)
            raise AgentFailure("error") from None
        ledger.add(
            model=completion.model,
            cost_usd=completion.cost_usd,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
        )
        result = parse_reply(completion.data, language)
        result.detail = _with_sources(result.detail, tools, language)
        return result
