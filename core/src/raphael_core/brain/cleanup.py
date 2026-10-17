"""Dictation cleanup: a language model tidies what speech-to-text heard.

The model only cleans. It removes fillers, keeps the final version of a self-correction and adds
punctuation, paragraphs and numbering. Whatever goes wrong while it runs, the user gets the raw
text: `clean` lets nothing out but a cancellation.

Nothing here logs the dictated text, the model's reply, the dictionary or a key. A failure is
logged by its kind and status (an OpenRouterError) or by the name of its type (anything else),
never with its message, its cause or a traceback: those could hold anything, the text of the key
store included.
"""

import asyncio
import functools
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import resources

from .dictionary import DictionaryStore
from .openrouter import CLEANUP_FALLBACKS, Completion, OpenRouterClient, OpenRouterError
from .settings import SettingsStore
from .spend import SpendTracker

logger = logging.getLogger(__name__)

CLEANUP_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def timeout_for(audio_seconds: float) -> float:
    """Seconds to wait for the cleanup of `audio_seconds` of speech: 3, plus 1 for every 20 of
    audio, and 15 at most."""
    return min(15.0, 3.0 + audio_seconds / 20.0)


def build_user_message(raw: str) -> str:
    """The user message of a cleanup call: the dictated text, marked as a transcript to clean."""
    return f"<transcript>\n{raw}\n</transcript>"


def max_tokens_for(raw: str) -> int:
    """The most tokens a cleanup of `raw` may take: twice its length, and room for the JSON."""
    return max(256, 2 * len(raw) + 64)


def reply_length_limit(raw: str) -> float:
    """The longest reply, in characters, that counts as a cleanup of `raw`.

    A cleanup adds punctuation and the numbers of a list, so it may run longer than what was said.
    """
    return 1.5 * len(raw) + 20


def is_suspicious_reply(raw: str, text: str) -> bool:
    """Whether `text`, the model's reply to the cleanup of `raw`, is no cleanup: nothing at all,
    or longer than `reply_length_limit(raw)`, which means the model wrote something of its own,
    most likely an answer. Spaces around `text` do not count. The reply is judged as the model
    wrote it, before the normalization that follows."""
    text = text.strip()
    return not text or len(text) > reply_length_limit(raw)


@functools.cache
def _prompt_template() -> str:
    return (
        resources.files("raphael_core.brain.prompts")
        .joinpath("cleanup_system.md")
        .read_text(encoding="utf-8")
    )


def _one_line(text: str) -> str:
    """`text` with each line break turned into a space, so that it stays on one line."""
    return " ".join(text.splitlines()).strip()


def build_system_prompt(words: Sequence[str], replacements: Sequence[tuple[str, str]]) -> str:
    """The system prompt with the user's dictionary filled in.

    `words` are the dictionary's words and `replacements` its `(misheard, word)` pairs, as
    `DictionaryStore.hint_words()` and `replacements()` return them. There is one line per word, in
    the order of `words`, and a word that speech-to-text tends to get wrong also lists how it comes
    out, in the order of `replacements`. An empty dictionary is written as （空）. A line break
    inside a word becomes a space, and a blank word or form is left out.
    """
    misheard_forms: dict[str, list[str]] = {}
    for misheard, word in replacements:
        form = _one_line(misheard)
        if form:
            misheard_forms.setdefault(_one_line(word), []).append(form)
    lines = []
    for word in map(_one_line, words):
        if not word:
            continue
        forms = misheard_forms.get(word)
        lines.append(f"- {word}（常被聽成：{'、'.join(forms)}）" if forms else f"- {word}")
    # str.replace, not str.format: the prompt has braces of its own (the JSON of the reply), and
    # the words may have some too.
    return _prompt_template().replace("{dictionary}", "\n".join(lines) or "（空）")


@dataclass
class CleanupResult:
    text: str  # what to paste: the cleaned text, or the raw text when cleanup fell back
    cleaned: bool
    fallback_reason: str | None  # None when cleaned, else why the raw text is used
    model: str | None  # the model that answered; None when none did
    cost_usd: float  # always a number the history can store: 0 or more, and finite
    llm_ms: int  # how long the model was waited for; 0 when it was not asked


def _fallback(
    raw: str, reason: str, *, model: str | None = None, cost_usd: float = 0.0, llm_ms: int = 0
) -> CleanupResult:
    """The result that hands back the raw text, for `reason`."""
    return CleanupResult(
        text=raw,
        cleaned=False,
        fallback_reason=reason,
        model=model,
        cost_usd=cost_usd,
        llm_ms=llm_ms,
    )


def _unexpected(raw: str, exc: Exception, llm_ms: int = 0) -> CleanupResult:
    """The fallback for a failure nobody planned for. Only the type of `exc` is logged."""
    logger.warning("Cleanup failed unexpectedly, using the raw text: %s", type(exc).__name__)
    return _fallback(raw, "error:unexpected", llm_ms=llm_ms)


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _usable_cost(cost: float) -> float:
    """`cost`, or 0.0 when it cannot be one. SpendTracker refuses a cost that is negative or not
    finite, and the history table cannot store a nan."""
    return cost if math.isfinite(cost) and cost >= 0 else 0.0


class CleanupService:
    """Cleans dictated text with the model chosen in the settings.

    `has_key` says whether an OpenRouter key is stored. `timeout_fn` gives the seconds to wait for
    the cleanup of so many seconds of speech. `on_result` hears how each call to the model went,
    so that the status of OpenRouter can follow: None when the model answered (even if what it
    said was no cleanup), the OpenRouterError when the call failed, and an OpenRouterError of kind
    "timeout" (status 0) when no answer came in time. It hears nothing when the model was not
    asked, nor of an unexpected error, and a failure of its own changes nothing.
    """

    def __init__(
        self,
        client: OpenRouterClient,
        settings: SettingsStore,
        dictionary: DictionaryStore,
        spend: SpendTracker,
        has_key: Callable[[], bool],
        timeout_fn: Callable[[float], float] = timeout_for,
        on_result: Callable[[Exception | None], None] | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._dictionary = dictionary
        self._spend = spend
        self._has_key = has_key
        self._timeout_fn = timeout_fn
        self._on_result = on_result

    async def clean(self, raw: str, *, audio_seconds: float) -> CleanupResult:
        """The cleaned text, or the raw text with the reason why it is all there is.

        The first of these that applies decides the reason: blank text is "empty", no key is
        "no_key", the monthly limit reached is "budget". Then the model is asked, and the raw text
        stays when it does not answer in time ("timeout"), when the call fails (f"error:{kind}"),
        or when its answer is no cleanup of the text: nothing, or longer than 1.5 times what was
        said plus 20 characters ("suspicious"). Anything else that goes wrong is
        "error:unexpected".

        An answer is paid for, so it is recorded in the spend whatever it holds. A cancellation
        passes through.
        """
        if not raw.strip():
            return _fallback(raw, "empty")
        try:
            return await self._clean(raw, audio_seconds)
        except Exception as exc:
            # Whatever broke, speech was understood: it is better pasted as it is than lost.
            return _unexpected(raw, exc)

    async def _clean(self, raw: str, audio_seconds: float) -> CleanupResult:
        if not self._has_key():
            return _fallback(raw, "no_key")
        if self._spend.is_over(self._settings.get("monthly_limit_usd")):
            return _fallback(raw, "budget")

        system = build_system_prompt(self._dictionary.hint_words(), self._dictionary.replacements())
        timeout = self._timeout_fn(audio_seconds)
        started = time.perf_counter()
        try:
            completion = await asyncio.wait_for(
                self._client.complete_json(
                    model=self._settings.get("cleanup_model"),
                    fallbacks=CLEANUP_FALLBACKS,
                    system=system,
                    user=build_user_message(raw),
                    schema=CLEANUP_SCHEMA,
                    schema_name="cleaned_dictation",
                    timeout_s=timeout,
                    max_tokens=max_tokens_for(raw),
                ),
                timeout,
            )
        except TimeoutError:
            logger.warning("Cleanup timed out after %.1f s, using the raw text", timeout)
            self._notify(OpenRouterError(0, "timeout", "Cleanup timed out"))
            return _fallback(raw, "timeout", llm_ms=_elapsed_ms(started))
        except OpenRouterError as error:
            logger.warning(
                "Cleanup failed, using the raw text: kind=%s status=%s", error.kind, error.status
            )
            self._notify(error)
            return _fallback(raw, f"error:{error.kind}", llm_ms=_elapsed_ms(started))
        except Exception as exc:  # anything else the client lets out
            return _unexpected(raw, exc, _elapsed_ms(started))
        llm_ms = _elapsed_ms(started)

        # Before anything looks at the reply: the money is spent whatever it holds.
        self._record_spend(completion)
        self._notify(None)

        cost_usd = _usable_cost(completion.cost_usd)
        text = completion.data.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if is_suspicious_reply(raw, text):
            logger.warning(
                "Cleanup reply is empty or too long, using the raw text: reply %d characters,"
                " dictation %d",
                len(text),
                len(raw),
            )
            return _fallback(
                raw, "suspicious", model=completion.model, cost_usd=cost_usd, llm_ms=llm_ms
            )
        return CleanupResult(
            text=text,
            cleaned=True,
            fallback_reason=None,
            model=completion.model,
            cost_usd=cost_usd,
            llm_ms=llm_ms,
        )

    def _record_spend(self, completion: Completion) -> None:
        try:
            self._spend.record(
                purpose="cleanup",
                model=completion.model,
                cost_usd=completion.cost_usd,
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
            )
        except Exception as exc:  # a cost it refuses, or a database that fails
            logger.warning("Could not record what a cleanup cost: %s", type(exc).__name__)

    def _notify(self, result: Exception | None) -> None:
        if self._on_result is None:
            return
        try:
            self._on_result(result)
        except Exception as exc:  # the status is a side matter: the text does not depend on it
            logger.warning("The cleanup status callback failed: %s", type(exc).__name__)
