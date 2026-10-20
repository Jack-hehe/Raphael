"""Dictation cleanup: a language model tidies what speech-to-text heard.

The model only cleans. It fixes words that speech-to-text misheard, removes fillers, keeps the
final version of a self-correction, adds punctuation, paragraphs and numbered lists, and sets the
tone for the app the text goes to. Whatever goes wrong while it runs, the user gets the raw text:
`clean` lets nothing out but a cancellation.

Nothing here logs the dictated text, the model's reply, the dictionary or a key. A failure is
logged by its kind and status (an OpenRouterError) or by the name of its type (anything else),
never with its message, its cause or a traceback: those could hold anything, the text of the key
store included.
"""

import asyncio
import functools
import logging
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import resources

from ..languages import DEFAULT_LANGUAGES, display_name
from .dictionary import DictionaryStore
from .openrouter import CLEANUP_FALLBACKS, Completion, OpenRouterClient, OpenRouterError
from .settings import SettingsStore
from .spend import SpendTracker
from .textnorm import to_mainland, to_simplified

logger = logging.getLogger(__name__)

CLEANUP_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def timeout_for(audio_seconds: float) -> float:
    """Seconds to wait for the cleanup of `audio_seconds` of speech: 10, plus 1 for every 3 of
    audio, and 90 at most.

    Every dictation is meant to be cleaned, however long (the owner's rule): the limit is only
    there for a model that hangs. The reply grows with what was said, and a slow provider writes
    a few dozen tokens a second, so a long dictation needs many seconds; the old limit (3 s, plus 1
    for every 20 s of audio) pasted long dictations raw. dictation.stop leaves room for this in
    the shell's wait (core_process.rs)."""
    return min(90.0, 10.0 + audio_seconds / 3.0)


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


def reply_length(text: str) -> int:
    """The length of a reply as the length rule counts it: the characters of `text` without the
    spaces around it and without line breaks. A list or paragraphs put things on lines of their
    own, and those breaks are structure, not words of the model's own."""
    return len(text.strip().replace("\r", "").replace("\n", ""))


# The length rule cannot catch a short answer to a short question ("Paris." for "what is the
# capital of France"), so a reply is also compared with the dictation for what they have in common.
# A cleanup keeps the words that were said: it drops fillers and restarts, adds punctuation and
# numbers, and fixes some spellings, so most of its character pairs (two letters side by side) were
# in the dictation. An answer, a translation or a refusal has hardly any. Only letters count: both
# texts are made Simplified Chinese in lower case, without punctuation, digits, spaces or list
# markers, and no pair runs across a line break of the reply. The dictation is often Mainland
# wording in Simplified characters and the cleanup Taiwan wording in Traditional ones (the prompt
# asks for it: 软件 and 内存 become 軟體 and 記憶體), so the share is the better of two conversions
# to Simplified, one by characters (資料 is 资料) and one by words (軟體 is 软件). A dictation of
# fewer than OVERLAP_MIN_LETTERS letters has too few pairs to say anything, and is not compared. The
# letters of a number written in Chinese do not count for that: the model may write 零九一二三 as
# digits, and a reply without letters would then look unrelated.
OVERLAP_MIN_LETTERS = 8
# A reply must have at least this share of its pairs in the dictation. Set from the cleanups the
# eval cases ask for and the answers they are meant to catch (tests/brain/test_cleanup_overlap.py):
# a good cleanup shares 0.44 at the least, for a dictation of three product names that all had to
# be fixed, and 0.59 for one full of mishearings; the answers share 0.36 at the most, and the short
# ones the length rule misses, 0.33.
MIN_OVERLAP = 0.4
_CHINESE_NUMERALS = frozenset("〇零一二三四五六七八九十百千萬万億亿兩两")


def _letters(text: str, convert: Callable[[str], str]) -> str:
    """The letters of `text`, converted by `convert` and in lower case."""
    return "".join(char for char in convert(text).casefold() if char.isalpha())


def _pairs(letters: str) -> list[str]:
    return [letters[i : i + 2] for i in range(len(letters) - 1)]


def _share(heard: str, reply: str, convert: Callable[[str], str]) -> float:
    """The share of the pairs of `reply`, line by line, that `heard` has too, both converted by
    `convert`; 0 for a reply without a pair."""
    heard_pairs = set(_pairs(_letters(heard, convert)))
    reply_pairs = [pair for line in reply.splitlines() for pair in _pairs(_letters(line, convert))]
    if not reply_pairs:
        return 0.0
    return sum(pair in heard_pairs for pair in reply_pairs) / len(reply_pairs)


def content_overlap(heard: str, reply: str) -> float | None:
    """The share of the character pairs of `reply` that `heard` has too, from 0 to 1, by the
    better of the two conversions to Simplified; None when `heard` has fewer than
    OVERLAP_MIN_LETTERS letters that are no Chinese numerals, so that there is nothing to compare.
    A reply without a pair has no share: 0."""
    dictated = _letters(heard, to_simplified)
    if sum(char not in _CHINESE_NUMERALS for char in dictated) < OVERLAP_MIN_LETTERS:
        return None
    return max(_share(heard, reply, convert) for convert in (to_simplified, to_mainland))


def suspicion(raw: str, text: str) -> str | None:
    """Why `text`, the model's reply to the cleanup of `raw`, is no cleanup; None if it passes.

    "empty": nothing at all. "too_long": longer than `reply_length_limit(raw)` as `reply_length`
    counts it, which means the model wrote something of its own. "unrelated": with a share of
    character pairs in `raw` under MIN_OVERLAP (`content_overlap`), which means the reply says
    something else than the dictation did, most likely an answer to it. The reply is judged as the
    model wrote it, before the normalization that follows.
    """
    if not text.strip():
        return "empty"
    if reply_length(text) > reply_length_limit(raw):
        return "too_long"
    overlap = content_overlap(raw, text)
    return "unrelated" if overlap is not None and overlap < MIN_OVERLAP else None


def is_suspicious_reply(raw: str, text: str) -> bool:
    """Whether `text`, the model's reply to the cleanup of `raw`, is no cleanup (`suspicion`)."""
    return suspicion(raw, text) is not None


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


# The kind of app each process is, by its name in lower case, so that the model can set the tone.
_APP_KINDS = {
    "chat": (
        "line.exe",
        "discord.exe",
        "messenger.exe",
        "telegram.exe",
        "whatsapp.exe",
        "slack.exe",
        "teams.exe",
        "ms-teams.exe",
        "signal.exe",
        "wechat.exe",
        "weixin.exe",
    ),
    "email or document": (
        "outlook.exe",
        "olk.exe",  # the new Outlook
        "winword.exe",
        "thunderbird.exe",
        "onenote.exe",
        "notion.exe",
        "obsidian.exe",
    ),
    "code editor or terminal": (
        "code.exe",
        "code - insiders.exe",
        "cursor.exe",
        "windsurf.exe",
        "zed.exe",
        "devenv.exe",
        "idea64.exe",
        "pycharm64.exe",
        "sublime_text.exe",
        "windowsterminal.exe",
        "wt.exe",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "conhost.exe",
    ),
}
_KIND_OF_APP = {name: kind for kind, names in _APP_KINDS.items() for name in names}
# A process name is short; anything longer is cut, so that it stays a label.
_TARGET_APP_MAX = 80


def describe_target_app(target_app: str | None) -> str:
    """The target app as the prompt names it: the process name on one line, at most 80
    characters, and its kind when it is a known one ("LINE.exe (chat)"); "unknown" without one."""
    name = _one_line(target_app or "")[:_TARGET_APP_MAX].strip()
    if not name:
        return "unknown"
    kind = _KIND_OF_APP.get(name.lower())
    return f"{name} ({kind})" if kind else name


# How each language's text is written, for the languages whose variants differ in script, usage or
# spelling. One sentence each, put into rule 6 of the prompt in the order of the user's languages.
_SCRIPT_RULES = {
    "zh-TW": (
        "Write Chinese in Traditional characters with Taiwan usage (軟體, 程式, 資料, 視窗),"
        " also when the transcript is in Simplified characters: 设定听写 → 設定聽寫."
    ),
    "zh-HK": (
        "Write Chinese in Traditional characters with Hong Kong usage (軟件, 程式, 數據),"
        " also when the transcript is in Simplified characters: 设定软件 → 設定軟件;"
        " use 「」 for quotation marks."
    ),
    "zh-CN": (
        "Write Chinese in Simplified characters with Mainland usage (软件, 程序, 数据),"
        " also when the transcript is in Traditional characters: 設定軟體 → 设定软件;"
        " use “” for quotation marks. The examples below are in Traditional characters: write"
        " your Chinese in Simplified characters all the same."
    ),
    "yue-HK": (
        "Write Chinese as written Cantonese in Traditional characters with Hong Kong usage"
        " (軟件, 數據), also when the transcript is in Simplified characters; keep the Cantonese"
        " words and particles as spoken (嘅、咗、唔、喺、哋) and never rewrite them as Standard"
        " Chinese."
    ),
    "en-US": "Write English with American spelling (color, center, organize).",
    "en-GB": "Write English with British spelling (colour, centre, organise).",
    "en-AU": "Write English with Australian spelling (colour, centre, organise).",
    "en-IN": "Write English with Indian English spelling (colour, centre, organise).",
}


def describe_languages(languages: Sequence[str]) -> str:
    """The user's languages as the prompt names them, in their order:
    "繁體中文（台灣）, English (United States)"."""
    return ", ".join(display_name(language) for language in languages)


def script_rules(languages: Sequence[str]) -> str:
    """The sentences of rule 6 that say how the chosen variants are written, each after a space;
    empty when no chosen language has such a rule."""
    return "".join(f" {_SCRIPT_RULES[item]}" for item in languages if item in _SCRIPT_RULES)


_PLACEHOLDER = re.compile(r"\{(dictionary|target_app|languages|script_rules)\}")

# The dictionary in the prompt is cut off, so that a dictionary of hundreds of words (learned ones
# pile up, up to three a dictation) cannot make each cleanup slower and dearer, or bury the rules
# of the prompt. It is cut off at the end of the order the caller gives, which puts the words the
# user chose first (`DictionaryStore.hint_words`): at most this many words, each with at most this
# many of the forms it is heard as, and at most this many characters in all. A word that would
# take the block past the characters is left out, and the later ones are still tried.
PROMPT_MAX_WORDS = 100
PROMPT_MAX_FORMS = 5
PROMPT_MAX_CHARS = 4_000


def build_system_prompt(
    words: Sequence[str],
    replacements: Sequence[tuple[str, str]],
    target_app: str | None = None,
    *,
    languages: Sequence[str] = DEFAULT_LANGUAGES,
) -> str:
    """The system prompt with the user's languages, dictionary and the target app filled in.

    `languages` is the `languages` setting: the prompt names them in that order and says how the
    chosen Chinese and English variants are written (`script_rules`). The same arguments always
    give the same prompt.

    `target_app` is the process name of the window the text goes to, written as
    `describe_target_app` does. It is in the system prompt, never inside the transcript, so it
    cannot pass for something the speaker said.

    `words` are the dictionary's words and `replacements` its `(misheard, word)` pairs, as
    `DictionaryStore.hint_words()` and `hint_forms()` return them: the replacement rules, and the
    forms the brain has only seen in corrections so far, which the model reads in context. There
    is one line per word, in the order of `words`, and a word that speech-to-text tends to get
    wrong also lists how it comes out, in the order of `replacements`. An empty dictionary is
    written as （空）. A line break inside a word becomes a space, and a blank word or form is left
    out. The block is cut off at the end of `words` (PROMPT_MAX_WORDS, PROMPT_MAX_FORMS and
    PROMPT_MAX_CHARS above).
    """
    misheard_forms: dict[str, list[str]] = {}
    for misheard, word in replacements:
        form = _one_line(misheard)
        if form:
            misheard_forms.setdefault(_one_line(word), []).append(form)
    lines: list[str] = []
    size = 0  # the characters of the lines, a line break after each
    for word in map(_one_line, words):
        if not word:
            continue
        if len(lines) == PROMPT_MAX_WORDS:
            break
        forms = misheard_forms.get(word)
        line = f"- {word}（常被聽成：{'、'.join(forms[:PROMPT_MAX_FORMS])}）" if forms else f"- {word}"
        if size + len(line) + 1 > PROMPT_MAX_CHARS:
            continue
        lines.append(line)
        size += len(line) + 1
    values = {
        "dictionary": "\n".join(lines) or "（空）",
        "target_app": describe_target_app(target_app),
        "languages": describe_languages(languages),
        "script_rules": script_rules(languages),
    }
    # One pass of re.sub, not str.format or one str.replace after another: the prompt has braces
    # of its own (the JSON of the reply), and a word or an app name may look like a placeholder.
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], _prompt_template())


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

    async def clean(
        self,
        raw: str,
        *,
        audio_seconds: float,
        heard: str | None = None,
        target_app: str | None = None,
    ) -> CleanupResult:
        """The cleaned text, or the raw text with the reason why it is all there is.

        `raw` is what is pasted when there is no cleanup. `heard` is what the model reads: the
        speech-to-text result before OpenCC, so that the model is not fed conversion errors such
        as 隻要 for 只要; it is `raw` unless given. `target_app` is the process name of the window
        the text goes to, named in the system prompt so that the tone can follow it.

        The first of these that applies decides the reason: blank text is "empty", no key is
        "no_key", the monthly limit reached is "budget". Then the model is asked, and the raw text
        stays when it does not answer in time ("timeout"), when the call fails (f"error:{kind}"),
        or when its answer is no cleanup of the text: nothing, longer than 1.5 times what it read
        plus 20 characters (line breaks not counted), or with too few character pairs in common
        with it (`suspicion`); that is "suspicious". Anything else that goes wrong is
        "error:unexpected".

        An answer is paid for, so it is recorded in the spend whatever it holds. A cancellation
        passes through.
        """
        if not raw.strip():
            return _fallback(raw, "empty")
        try:
            return await self._clean(
                raw, raw if heard is None else heard, audio_seconds, target_app
            )
        except Exception as exc:
            # Whatever broke, speech was understood: it is better pasted as it is than lost.
            return _unexpected(raw, exc)

    async def _clean(
        self, raw: str, heard: str, audio_seconds: float, target_app: str | None
    ) -> CleanupResult:
        if not self._has_key():
            return _fallback(raw, "no_key")
        if self._spend.is_over(self._settings.get("monthly_limit_usd")):
            return _fallback(raw, "budget")

        system = build_system_prompt(
            self._dictionary.hint_words(),
            self._dictionary.hint_forms(),
            target_app,
            languages=self._settings.get("languages"),
        )
        timeout = self._timeout_fn(audio_seconds)
        started = time.perf_counter()
        try:
            completion = await asyncio.wait_for(
                self._client.complete_json(
                    model=self._settings.get("cleanup_model"),
                    fallbacks=CLEANUP_FALLBACKS,
                    system=system,
                    user=build_user_message(heard),
                    schema=CLEANUP_SCHEMA,
                    schema_name="cleaned_dictation",
                    timeout_s=timeout,
                    max_tokens=max_tokens_for(heard),
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
        if is_suspicious_reply(heard, text):
            logger.warning(
                "Cleanup reply is no cleanup (empty, too long or unrelated), using the raw text:"
                " reply %d characters, dictation %d",
                len(text),
                len(heard),
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
