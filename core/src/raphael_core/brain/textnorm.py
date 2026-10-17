"""Text normalization: Taiwan Traditional Chinese that leaves Japanese alone, and dictionary rules.

Speech-to-text often writes Chinese in Simplified characters, while Raphael's Chinese is always
Traditional with Taiwan usage. OpenCC's `s2twp` converts it, but OpenCC does no language
detection: it also turns the Japanese kanji that share a code point with Simplified Chinese into
Traditional forms (学 into 學, 会 into 會, 来 into 來). That is why Japanese is skipped, sentence
by sentence.
"""

import functools
import re
import string
from collections.abc import Sequence

import opencc

# A sentence ends after one of 。！？!? or a line break. A run of them stays attached to the
# sentence before it (a run at the very start of the text forms a piece of its own), so the pieces
# always add up to the whole text.
_SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?\n]*|[。！？!?\n]+")


def _in_blocks(*blocks: tuple[int, int]) -> re.Pattern[str]:
    """A pattern for one character from any of the blocks, given as (first, last) code points."""
    return re.compile("[" + "".join(f"{chr(first)}-{chr(last)}" for first, last in blocks) + "]")


_HIRAGANA_BLOCK = (0x3040, 0x309F)
_HIRAGANA = _in_blocks(_HIRAGANA_BLOCK)
# What counts as a CJK character when the share of hiragana is measured: hiragana, katakana, Han.
_CJK = _in_blocks(
    _HIRAGANA_BLOCK,
    (0x30A0, 0x30FF),  # Katakana
    (0x31F0, 0x31FF),  # Katakana Phonetic Extensions
    (0xFF66, 0xFF9F),  # halfwidth Katakana
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
)

# A sentence whose CJK characters are at least this many percent hiragana is Japanese.
_JAPANESE_PERCENT = 10

# What may not stand next to a rule's text when that text starts or ends with one of them.
_ASCII_LETTERS_AND_DIGITS = string.ascii_letters + string.digits


@functools.cache
def _converter() -> opencc.OpenCC:
    """The OpenCC converter, made on first use: it loads its dictionaries, about 100 ms."""
    return opencc.OpenCC("s2twp.json")


def _is_japanese(sentence: str) -> bool:
    """Whether at least 10 % of the sentence's CJK characters are hiragana.

    Japanese grammar always brings hiragana. A katakana name inside a Chinese sentence does not,
    and neither does a sentence of kanji alone, which therefore counts as Chinese. Without any
    hiragana a sentence is never Japanese, even when it has no CJK characters to measure.
    """
    hiragana = len(_HIRAGANA.findall(sentence))
    cjk = len(_CJK.findall(sentence))
    return hiragana > 0 and hiragana * 100 >= _JAPANESE_PERCENT * cjk


def to_taiwan_traditional(text: str) -> str:
    """`text` in Traditional Chinese with Taiwan usage ("软件" becomes "軟體"), except Japanese.

    The text is split into sentences after 。！？!? and line breaks, and each sentence is judged on
    its own. A Japanese sentence stays as it is; every other one goes through OpenCC whole, which
    leaves kana and Latin text alone. Spaces and line breaks come back exactly as they were.
    """
    return "".join(
        sentence if _is_japanese(sentence) else _converter().convert(sentence)
        for sentence in _SENTENCE.findall(text)
    )


def _misheard_pattern(misheard: str) -> re.Pattern[str]:
    """The pattern that finds the text of one rule.

    ASCII letters match in either case, everything else exactly. re.ASCII keeps the case folding
    to the ASCII letters. Otherwise é would match É, and the Kelvin sign (U+212A) would match k.

    A text that starts with an ASCII letter or digit does not match right after another one, and a
    text that ends with one does not match right before another one: "cloud" is not found in
    "iCloud" or "clouds". At an end that is anything else, nothing is checked. Chinese and Japanese
    characters next to a match do not matter either, as there are no spaces between the words.
    """
    letters_and_digits = f"[{_ASCII_LETTERS_AND_DIGITS}]"
    not_after = f"(?<!{letters_and_digits})" if misheard[0] in _ASCII_LETTERS_AND_DIGITS else ""
    not_before = f"(?!{letters_and_digits})" if misheard[-1] in _ASCII_LETTERS_AND_DIGITS else ""
    return re.compile(not_after + re.escape(misheard) + not_before, re.IGNORECASE | re.ASCII)


def _overlaps_word(text: str, start: int, end: int, word: str) -> bool:
    """Whether an occurrence of `word` in `text` shares a character with `text[start:end]`."""
    # Such an occurrence starts after start - len(word) and before end, so it lies within this
    # stretch of the text.
    return text.find(word, max(0, start - len(word) + 1), end + len(word) - 1) != -1


def _apply_rule(text: str, misheard: str, word: str) -> str:
    """`text` with each match of `misheard` replaced by `word`, but not where `word` is already.

    A match that shares a character with an occurrence of `word` is not a mishearing but the word
    itself, or part of it, and stays as it is.
    """
    pattern = _misheard_pattern(misheard)
    # A word that is shorter than the misheard text cannot contain a match, and a rule such as
    # ("Claude Code", "Claude") has to replace matches that contain the word.
    protect_word = len(word) >= len(misheard)
    pieces: list[str] = []
    copied = 0  # text[:copied] is in `pieces`, replaced or not
    position = 0  # where the search for the next match starts
    while (match := pattern.search(text, position)) is not None:
        start, end = match.span()
        if protect_word and _overlaps_word(text, start, end, word):
            position = start + 1  # the next match may start inside this one
            continue
        pieces.append(text[copied:start])
        # The word itself, so that backslashes in it are not read as escapes or group references.
        pieces.append(word)
        copied = position = end
    pieces.append(text[copied:])
    return "".join(pieces)


def apply_replacements(text: str, rules: Sequence[tuple[str, str]]) -> str:
    """Replace each `misheard` text by its `word`, rule after rule in the order given.

    `DictionaryStore.replacements()` returns the rules longest first, so that a long form gets
    its chance before a shorter one inside it. ASCII letters in a rule match in either case,
    everything else exactly. A rule with empty `misheard` text is skipped.

    A text that starts or ends with an ASCII letter or digit matches only as a word of its own:
    "cloud" is not found in "iCloud" or "clouds". And the rules never damage a word that is
    already right: with ("Claude", "Claude Code"), the "Claude" in "Claude Code" stays. The brain
    applies the rules to the speech-to-text result and again to the cleaned text, so the second
    time must not add another "Code". Applied to its own result, a rule whose word is at least as
    long as its misheard text changes nothing more. Rules can still undo or build on each other
    (a word that holds the misheard text of an earlier rule), and a word shorter than its misheard
    text can leave a second match behind.
    """
    for misheard, word in rules:
        if misheard:
            text = _apply_rule(text, misheard, word)
    return text
