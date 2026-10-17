"""Text normalization: Taiwan Traditional Chinese that leaves Japanese alone, and dictionary rules.

Speech-to-text often writes Chinese in Simplified characters, while Raphael's Chinese is always
Traditional with Taiwan usage. OpenCC's `s2twp` converts it, but OpenCC does no language
detection: it also turns the Japanese kanji that share a code point with Simplified Chinese into
Traditional forms (学 into 學, 会 into 會, 来 into 來). That is why Japanese is skipped, sentence
by sentence.
"""

import functools
import re
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

_ASCII_LETTER = re.compile("[A-Za-z]")


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


def apply_replacements(text: str, rules: Sequence[tuple[str, str]]) -> str:
    """Replace each `misheard` text by its `word`, rule after rule in the order given.

    `DictionaryStore.replacements()` returns the rules longest first, so that a long form gets
    its chance before a shorter one inside it. ASCII letters in a rule match in either case,
    everything else exactly. A rule with empty `misheard` text is skipped.
    """
    for misheard, word in rules:
        if not misheard:
            continue
        if _ASCII_LETTER.search(misheard):
            # re.ASCII keeps the case folding to the ASCII letters. Otherwise é would match É,
            # and the Kelvin sign (U+212A) would match k.
            pattern = re.compile(re.escape(misheard), re.IGNORECASE | re.ASCII)
            # A function, so that backslashes in `word` are not read as escapes or group refs.
            text = pattern.sub(lambda _match: word, text)
        else:
            text = text.replace(misheard, word)
    return text
