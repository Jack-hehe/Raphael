"""Dictation cleanup: a language model tidies what speech-to-text heard.

The model only cleans. It fixes words that speech-to-text misheard, removes fillers, keeps the
final version of a self-correction, adds punctuation, paragraphs and numbered lists, and sets the
tone for the app the text goes to. Whatever goes wrong while it runs, the user gets the raw text:
`clean` lets nothing out but a cancellation.

A dictation may be as long as the user speaks. One that is longer than CLEANUP_CHUNK_CHARS is cut
at the ends of its sentences into pieces (`split_for_cleanup`), which are cleaned side by side and
joined again in order, each followed by the spaces or line breaks it was cut at (`join_parts`), so
no reply has to be longer than a model can write and no paragraph runs into the next. A piece whose
cleanup fails keeps its raw text, and the others stay cleaned.

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
    audio, with no ceiling.

    Every dictation is meant to be cleaned, however long: the limit is only there for a model that
    hangs. The reply grows with what was said, and a slow provider writes a few dozen tokens a
    second, so a long dictation needs many seconds; a tight limit (3 s, plus 1 for every 20 s of
    audio) would paste long dictations raw, and so would a ceiling (it was 90 s, which a dictation
    of a few minutes outgrows). dictation.stop leaves room for this in the shell's wait, which
    grows with the recording the same way (core_process.rs `dictation_stop_timeout`)."""
    return 10.0 + audio_seconds / 3.0


# The longest piece of a dictation that one cleanup call is given; a longer dictation is cut into
# pieces of at most this many characters, which are cleaned side by side and joined again. A
# reply may be 1.5 times what it cleans and 20 characters more (`reply_length_limit`), and the call
# allows twice the characters in tokens (`max_tokens_for`): 3064 tokens for 1500 characters, where
# a character of Chinese is about one token and one of English a quarter of one. That is inside
# what any model the user may choose writes in one reply (many of OpenRouter's stop at 4096 or 8192
# tokens; gpt-6-luna and the fallbacks write 65,536 to 128,000), so no reply is cut off for its
# length; the pieces are also cleaned side by side, so an hour of speech does not wait for one
# reply of tens of thousands of tokens. A piece is still long enough for the model to see what it
# is about: five minutes of Chinese, or ten of English. A dictation up to that length is one piece,
# cleaned in one call as it always was.
CLEANUP_CHUNK_CHARS = 1500
# How many pieces of one dictation are cleaned at a time: an hour of speech is a dozen or more, and
# OpenRouter should not get them all at once from one user.
CLEANUP_CONCURRENCY = 4

# Where a dictation may be cut, best first: after the end of a sentence (and a closing quote or
# bracket), after a clause, at a space. A piece that none of them cuts short enough is cut at the
# limit itself. Whatever follows a cut (the spaces after a full stop) stays with the piece before.
_CUTS = (
    re.compile(r"(?:[。！？!?…\n]+|\.(?=\s))[」』）)\"'”’]*\s*"),
    re.compile(r"[，、,;；：:]\s*"),
    re.compile(r"\s+"),
)


def _cut_after(text: str, pattern: re.Pattern[str]) -> list[str]:
    """`text` cut after each match of `pattern`; nothing is lost."""
    parts: list[str] = []
    start = 0
    for match in pattern.finditer(text):
        if match.end() > start:
            parts.append(text[start : match.end()])
            start = match.end()
    if start < len(text):
        parts.append(text[start:])
    return parts


def _pieces(text: str, limit: int, level: int = 0) -> list[str]:
    """`text` as pieces of at most `limit` characters, cut at the best place there is (`_CUTS`)."""
    if len(text) <= limit:
        return [text]
    if level == len(_CUTS):
        return [text[start : start + limit] for start in range(0, len(text), limit)]
    pieces: list[str] = []
    for part in _cut_after(text, _CUTS[level]):
        pieces += _pieces(part, limit, level + 1)
    return pieces


def split_for_cleanup(text: str, limit: int = CLEANUP_CHUNK_CHARS) -> list[str]:
    """`text` as the pieces the cleanup is given, in order: sentences put together into pieces of
    at most `limit` characters each (see CLEANUP_CHUNK_CHARS), besides the spaces and line breaks
    a piece ends with. A text within the limit is one piece, itself.

    Nothing is lost: joined, the pieces are `text` again. What a piece was cut at (the space after
    a full stop, the break between two paragraphs) stays at its end, so that the pieces can be
    joined as they were after they are cleaned (`join_parts`). No piece starts with a space or is
    nothing but spaces."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text, limit):
        if current and len(current) + len(piece) > limit:
            chunks.append(current)
            current = ""
        current += piece
    chunks.append(current)
    pieces: list[str] = []
    lead = ""  # the spaces the text starts with, until its first words
    for chunk in chunks:
        words = chunk.lstrip()
        space = chunk[: len(chunk) - len(words)]
        if pieces:
            pieces[-1] += space  # spaces at the start of a piece end the one before
        else:
            lead += space
        if words:
            pieces.append(words if pieces else lead + words)
    return pieces or [text]


def separator_after(piece: str) -> str:
    """The spaces and line breaks `piece` ends with: what it was cut at."""
    return piece[len(piece.rstrip()) :]


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


@dataclass(frozen=True)
class CleanedPart:
    """One piece of a dictation that was cleaned in pieces."""

    text: str  # the piece as the model cleaned it, or its raw text when its call fell back
    cleaned: bool
    # The spaces or line breaks that followed the piece in the dictation, where it was cut: the
    # joined text keeps them (`join_parts`). Empty after the last piece.
    after: str = ""


def join_parts(parts: Sequence[CleanedPart]) -> str:
    """The parts of a dictation as one text: each followed by what it was cut at, so that a
    paragraph break, or the space between two sentences, is where it was."""
    return "".join(part.text + part.after for part in parts).strip()


@dataclass
class CleanupResult:
    text: str  # what to paste: the cleaned text, or the raw text when cleanup fell back
    cleaned: bool  # some or all of it was cleaned
    # None when all of it was cleaned, else why the raw text is used (for all of it, or, when
    # `cleaned`, for the pieces whose cleanup fell back: the reason of the first of them).
    fallback_reason: str | None
    model: str | None  # the model that answered; None when none did
    cost_usd: float  # always a number the history can store: 0 or more, and finite; all calls
    llm_ms: int  # how long the model was waited for; 0 when it was not asked
    # A dictation cleaned in pieces, when some were: each piece, in order (`text` is them joined).
    # Empty when it was one piece, or when nothing of it was cleaned.
    parts: tuple[CleanedPart, ...] = ()


def _bodies(chunks: Sequence[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """The pieces of a long dictation as (raw, heard, after): the raw and heard text without the
    spaces around them, which is what the model is given and what a fallback keeps, and what the
    piece was cut at (`separator_after` of the raw text), which the join puts back. A piece with
    nothing to clean is left out; when it is only spaces, they go to the piece before."""
    bodies: list[tuple[str, str, str]] = []
    for raw, heard in chunks:
        if raw.strip() and heard.strip():
            bodies.append((raw.strip(), heard.strip(), separator_after(raw)))
        elif bodies and not raw.strip():
            last_raw, last_heard, after = bodies[-1]
            bodies[-1] = (last_raw, last_heard, after + raw)
    if bodies:  # the end of the dictation is the end of the text, not a separator
        last_raw, last_heard, _ = bodies[-1]
        bodies[-1] = (last_raw, last_heard, "")
    return bodies


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
        chunks: Sequence[tuple[str, str]] | None = None,
    ) -> CleanupResult:
        """The cleaned text, or the raw text with the reason why it is all there is.

        `raw` is what is pasted when there is no cleanup. `heard` is what the model reads: the
        speech-to-text result before OpenCC, so that the model is not fed conversion errors such
        as 隻要 for 只要; it is `raw` unless given. `target_app` is the process name of the window
        the text goes to, named in the system prompt so that the tone can follow it.

        A long dictation comes as `chunks`: the (raw, heard) of each of its pieces, in order, as
        `split_for_cleanup` cut it, each with what it was cut at at its end. Each piece is a call
        of its own, CLEANUP_CONCURRENCY at a time, judged and paid for by itself; the model is
        given it without the spaces around it, and the pieces are joined again in order, each
        followed by what it was cut at (`join_parts`).
        A piece whose call falls back keeps its raw text, and the others stay cleaned: the result
        is then `cleaned`, its `parts` say which piece is which, and `fallback_reason` is the
        reason of the first piece that fell back. If none of them was cleaned, the result is the
        raw text, as for a dictation of one piece. With no `chunks`, or one, there is one call.

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
        pieces = _bodies(chunks or ())
        try:
            return await self._clean(
                raw, raw if heard is None else heard, audio_seconds, target_app, pieces
            )
        except Exception as exc:
            # Whatever broke, speech was understood: it is better pasted as it is than lost.
            return _unexpected(raw, exc)

    async def _clean(
        self,
        raw: str,
        heard: str,
        audio_seconds: float,
        target_app: str | None,
        pieces: Sequence[tuple[str, str, str]],
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
        if len(pieces) <= 1:
            return await self._clean_one(raw, heard, system, self._timeout_fn(audio_seconds))
        return await self._clean_pieces(raw, pieces, system, audio_seconds)

    async def _clean_pieces(
        self,
        raw: str,
        pieces: Sequence[tuple[str, str, str]],
        system: str,
        audio_seconds: float,
    ) -> CleanupResult:
        """A long dictation, piece by piece (see `clean`). Each piece is waited for as long as its
        share of the speech would be on its own."""
        total = sum(len(heard) for _, heard, _ in pieces)
        gate = asyncio.Semaphore(CLEANUP_CONCURRENCY)

        async def one(piece_raw: str, piece_heard: str) -> CleanupResult:
            async with gate:
                seconds = audio_seconds * len(piece_heard) / total
                try:
                    return await self._clean_one(
                        piece_raw, piece_heard, system, self._timeout_fn(seconds)
                    )
                except Exception as exc:  # one piece that breaks leaves the others cleaned
                    return _unexpected(piece_raw, exc)

        started = time.perf_counter()
        results = await asyncio.gather(*(one(r, h) for r, h, _ in pieces))
        llm_ms = _elapsed_ms(started)
        cost_usd = _usable_cost(sum(result.cost_usd for result in results))
        models = [result.model for result in results if result.model is not None]
        failed = [result.fallback_reason for result in results if not result.cleaned]
        logger.info(
            "A long dictation was cleaned in %d pieces; %d fell back", len(results), len(failed)
        )
        if len(failed) == len(results):
            return _fallback(
                raw,
                failed[0] or "error:unexpected",
                model=models[0] if models else None,
                cost_usd=cost_usd,
                llm_ms=llm_ms,
            )
        parts = tuple(
            CleanedPart(result.text, result.cleaned, after)
            for result, (_, _, after) in zip(results, pieces, strict=True)
        )
        return CleanupResult(
            text=join_parts(parts),
            cleaned=True,
            fallback_reason=failed[0] if failed else None,
            model=next(result.model for result in results if result.cleaned),
            cost_usd=cost_usd,
            llm_ms=llm_ms,
            parts=parts,
        )

    async def _clean_one(self, raw: str, heard: str, system: str, timeout: float) -> CleanupResult:
        """One call to the model: the cleanup of `heard`, or `raw` with the reason."""
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
