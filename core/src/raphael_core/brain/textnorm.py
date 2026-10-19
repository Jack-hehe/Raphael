"""Text normalization: the Chinese written form the user chose, Japanese left alone, and rules.

Speech-to-text writes Chinese in whichever script it likes, while Raphael's Chinese follows the
variant the user chose (raphael_core.languages): Traditional with Taiwan usage for zh-TW (OpenCC
`s2twp`, the default), Traditional with Hong Kong usage for zh-HK and yue-HK (`s2hk`), Simplified
for zh-CN (`t2s`), and no conversion when no Chinese is chosen. OpenCC does no language
detection: it also turns the Japanese kanji that share a code point with Simplified Chinese into
Traditional forms (学 into 學, 会 into 會, 来 into 來). That is why Japanese is skipped, sentence
by sentence. And OpenCC takes its input for Simplified Chinese: over text that is Traditional
already it corrupts some characters (只要 into 隻要, 干擾 into 幹擾). So the Traditional variants
convert only what is still Simplified (`convert_leftover_simplified`), for the cleaned text and for
the raw text alike.
"""

import difflib
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


# The OpenCC configuration that writes Simplified Chinese as each Traditional variant.
_TO_TRADITIONAL = {"zh-TW": "s2twp.json", "zh-HK": "s2hk.json", "yue-HK": "s2hk.json"}
CHINESE_VARIANTS = ("zh-TW", "zh-HK", "zh-CN", "yue-HK")


@functools.cache
def _opencc(config: str) -> opencc.OpenCC:
    """The OpenCC converter of `config`, made on first use (s2twp is `_converter`)."""
    return _converter() if config == "s2twp.json" else opencc.OpenCC(config)


def _checked_variant(chinese: str | None) -> str | None:
    if chinese is not None and chinese not in CHINESE_VARIANTS:
        raise ValueError(f"Unknown Chinese variant {chinese!r}: use {', '.join(CHINESE_VARIANTS)}")
    return chinese


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
    return _convert_sentences(text, _converter())


def _convert_sentences(text: str, converter: opencc.OpenCC) -> str:
    """`text` with each sentence that is not Japanese run through `converter` whole."""
    return "".join(
        sentence if _is_japanese(sentence) else converter.convert(sentence)
        for sentence in _SENTENCE.findall(text)
    )


def to_chinese_variant(text: str, chinese: str | None) -> str:
    """`text` in the script of the Chinese variant `chinese`, Japanese sentences left alone: every
    other sentence goes through OpenCC whole (s2twp, s2hk or t2s). None leaves the text as it is.
    ValueError for a variant that is not one of CHINESE_VARIANTS."""
    chinese = _checked_variant(chinese)
    if chinese is None:
        return text
    if chinese == "zh-CN":
        return _convert_sentences(text, _to_simplified())
    return _convert_sentences(text, _opencc(_TO_TRADITIONAL[chinese]))


# The keys of OpenCC's STCharacters table that are among their own Traditional forms: Simplified
# and Traditional Chinese share them (只 is 只 or 隻, 后 is 后 or 後, 台 is 台 or 臺, 干 is 干,
# 乾 or 幹). Generated from OpenCC 1.4.2; a test regenerates it from the installed dictionary.
_SHARED_CHARACTERS = frozenset(
    "㐹万丑丰了于云亘仆仇价仿伙余佛佣俊修借僵克党具冢冬准凌几凶出划刮制千升卜占卷厂厘"
    "只台叶吁吃合吊同后向吣呆周咨咸咽哄唇喂噪回困坐坯堤夫夸奸姜娘它家尸局岩岳巨布帘席"
    "干幸广庵弦彩征御志念恤愈愿戚扇才扎托扣折抵拐拿挂挨挽捆捍据搜斗斤斫旋昆暗曲札朱朴"
    "杆杠杯杰松板极果柜栗核梁棱檗欲沈沾泛注浚涂涌淀游溪漓澄焰熏狸玩琅璇症皂矩确硷私秋"
    "种穗筑筱糊系累耇胄背胜胡腊腌膻致舍芸苔苹范荐蒙蔑藤虫蚝蜡蝎表谷豆象跖辟适郁酸采里"
    "雇雕面"
)

# Characters below this code point are never Chinese: OpenCC leaves them alone.
_FIRST_CJK = 0x2E80


@functools.cache
def _to_simplified() -> opencc.OpenCC:
    """OpenCC's Traditional-to-Simplified converter, made on first use."""
    return opencc.OpenCC("t2s.json")


@functools.lru_cache(maxsize=8192)
def _is_simplified_only(char: str, config: str = "s2twp.json") -> bool:
    """Whether the converter of `config` changes `char` on its own and it is no Traditional
    character (软, 发, 开, but not 只 or 后). Traditional forms of another region (裏 for s2twp)
    count too: they are to be converted."""
    return (
        ord(char) >= _FIRST_CJK
        and char not in _SHARED_CHARACTERS
        and _opencc(config).convert(char) != char
    )


@functools.lru_cache(maxsize=8192)
def _is_traditional_only(char: str) -> bool:
    """Whether `char` is written differently in Simplified Chinese (軟, 髮, 後, 隻)."""
    return ord(char) >= _FIRST_CJK and _to_simplified().convert(char) != char


def _convert_simplified_words(sentence: str, config: str = "s2twp.json") -> str:
    """One sentence that is meant to be Traditional Chinese, with what is still Simplified in it
    converted, and nothing else.

    A sentence without a Simplified-only character stays as it is. One with no character that only
    Traditional Chinese has is Simplified Chinese and goes through s2twp whole, as speech-to-text
    output does. In a sentence that has both, only the words that s2twp changes and that hold a
    Simplified-only character take its result: 「幫我打开」 becomes 「幫我開啟」, but the 只 of
    「就是只要按這個开關」 is not turned into 隻 as s2twp alone would.
    """

    def simplified_only(char: str) -> bool:
        return _is_simplified_only(char, config)

    if not any(map(simplified_only, sentence)):
        return sentence
    converted = _opencc(config).convert(sentence)
    if not any(map(_is_traditional_only, sentence)):
        return converted
    pieces: list[str] = []
    changed_from: list[str] = []  # a run of changes, as it was and as s2twp wrote it
    changed_to: list[str] = []

    def take_changes() -> None:
        before = "".join(changed_from)
        pieces.append("".join(changed_to) if any(map(simplified_only, before)) else before)
        changed_from.clear()
        changed_to.clear()

    matcher = difflib.SequenceMatcher(None, sentence, converted, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            take_changes()
            pieces.append(sentence[i1:i2])
        else:
            changed_from.append(sentence[i1:i2])
            changed_to.append(converted[j1:j2])
    take_changes()
    return "".join(pieces)


def convert_leftover_simplified(text: str, config: str = "s2twp.json") -> str:
    """`text`, written to be Traditional Chinese with Taiwan usage, with what is still Simplified
    in it converted by s2twp, sentence by sentence (Japanese sentences stay as they are). With
    `config` "s2hk.json" the same is done for Hong Kong Traditional.

    s2twp takes its input for Simplified Chinese, and it corrupts some Traditional text:
    「就是只要」 becomes 「就是隻要」, 「干擾」 becomes 「幹擾」. This leaves the Traditional
    parts alone (see `_convert_simplified_words`). Spaces and line breaks come back exactly as
    they were.
    """
    return "".join(
        sentence if _is_japanese(sentence) else _convert_simplified_words(sentence, config)
        for sentence in _SENTENCE.findall(text)
    )


def convert_leftover_traditional(text: str) -> str:
    """`text`, written to be Simplified Chinese, with each sentence that still has a character
    only Traditional Chinese has run through t2s (Japanese sentences stay as they are). t2s leaves
    Simplified characters as they are, so the rest of such a sentence does not change."""
    return "".join(
        _to_simplified().convert(sentence)
        if not _is_japanese(sentence) and any(map(_is_traditional_only, sentence))
        else sentence
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


def _window(start: int, end: int, word: str) -> tuple[int, int]:
    """The stretch of a text that holds every occurrence of `word` sharing a character with
    `text[start:end]`: such an occurrence starts after start - len(word) and before end."""
    return max(0, start - len(word) + 1), end + len(word) - 1


def _overlaps_word(text: str, start: int, end: int, word: str) -> bool:
    """Whether `word`, spelled exactly like that, shares a character with `text[start:end]`."""
    return text.find(word, *_window(start, end, word)) != -1


def _apply_rule(text: str, misheard: str, word: str) -> str:
    """`text` with each match of `misheard` replaced by `word`, but not where `word` is already.

    A match that shares a character with `word` as it is spelled in the text is not a mishearing
    but the word itself, or part of it, and stays. When it shares one with the word in other
    letter case ("claude code" for "Claude Code"), that whole occurrence is written as `word`.
    """
    pattern = _misheard_pattern(misheard)
    # A word that is shorter than the misheard text cannot contain a match, and a rule such as
    # ("Claude Code", "Claude") has to replace matches that contain the word.
    protect_word = len(word) >= len(misheard)
    word_in_any_case = re.compile(re.escape(word), re.IGNORECASE | re.ASCII)
    pieces: list[str] = []
    copied = 0  # text[:copied] is in `pieces`, replaced or not
    position = 0  # where the search for the next match starts
    while (match := pattern.search(text, position)) is not None:
        start, end = match.span()
        first, last = start, end  # what gets replaced: the match, or the word it overlaps
        if protect_word:
            if _overlaps_word(text, start, end, word):
                position = start + 1  # the next match may start inside this one
                continue
            other_case = word_in_any_case.search(text, *_window(start, end, word))
            if other_case is not None:
                first, last = other_case.span()
                # An occurrence that begins in text that is dealt with, or that overlaps the word
                # as spelled, is the end of one word and the start of another, not a word.
                if first < copied or _overlaps_word(text, first, last, word):
                    position = start + 1
                    continue
        pieces.append(text[copied:first])
        # The word itself, so that backslashes in it are not read as escapes or group references.
        pieces.append(word)
        copied = position = last
    pieces.append(text[copied:])
    return "".join(pieces)


def apply_replacements(text: str, rules: Sequence[tuple[str, str]]) -> str:
    """Replace each `misheard` text by its `word`, rule after rule in the order given.

    `DictionaryStore.replacements()` returns the rules longest first, so that a long form gets
    its chance before a shorter one inside it. ASCII letters in a rule match in either case,
    everything else exactly. A rule with empty `misheard` text is skipped.

    A text that starts or ends with an ASCII letter or digit matches only as a word of its own:
    "cloud" is not found in "iCloud" or "clouds". And a match that overlaps the `word` itself is
    not a mishearing: with ("Claude", "Claude Code"), the "Claude" in "Claude Code" stays, and
    "claude code" is written as "Claude Code" instead of gaining a second "Code". The brain
    applies the rules to the speech-to-text result and again to the cleaned text, so the second
    time must not add another "Code". For a word at least as long as its misheard text, applying
    the rule to its own result is meant to change nothing more. That does not extend to a word
    shorter than its misheard text, which can leave another match behind, or to rules that build
    on each other (a word that holds the misheard text of an earlier rule).

    The word in other letter case is only rewritten where a match overlaps it. Elsewhere it stays
    as it is: ("cloud", "Claude") leaves "CLAUDE" alone.
    """
    for misheard, word in rules:
        if misheard:
            text = _apply_rule(text, misheard, word)
    return text


def normalize_transcript(
    text: str, rules: Sequence[tuple[str, str]], *, chinese: str | None = "zh-TW"
) -> str:
    """Speech-to-text output as the rest of the app sees it: the Chinese of the variant `chinese`
    (Japanese sentences left alone), then the dictionary's replacements. This is the text that is
    saved, and the one pasted when the cleanup fails.

    The speech-to-text engines often write Traditional Chinese already, and OpenCC over a whole
    Traditional sentence corrupts it (只要 into 隻要, 干擾 into 幹擾, and with s2hk 系統 into 係統).
    So for the Traditional variants (zh-TW, zh-HK, yue-HK) only what is still Simplified is
    converted (`convert_leftover_simplified`), as it is for a cleaned text: Traditional text comes
    through as it is, and a sentence that is Simplified Chinese through and through is converted
    whole, as `to_chinese_variant` does. zh-CN goes through `to_chinese_variant` (t2s, which leaves
    Simplified characters alone), and None leaves the text as it is.
    """
    chinese = _checked_variant(chinese)
    if chinese in _TO_TRADITIONAL:
        text = convert_leftover_simplified(text, _TO_TRADITIONAL[chinese])
    else:
        text = to_chinese_variant(text, chinese)
    return apply_replacements(text, rules)


def finish_cleaned_text(
    text: str, rules: Sequence[tuple[str, str]], *, chinese: str | None = "zh-TW"
) -> str:
    """The text the user gets from what the cleanup model wrote: its reply, normalized again.

    The model is told to write Traditional Chinese with Taiwan usage, so only what it left in
    Simplified Chinese is converted (`convert_leftover_simplified`): s2twp over the whole reply
    would turn its 只要 into 隻要. OpenCC would also corrupt the kanji of a Japanese text (学校
    into 學校), so a reply with any hiragana is not converted at all, only given the replacements.
    That gate is on the whole reply, a step beyond the sentence-by-sentence rule of
    `to_taiwan_traditional`. A text that fell back to the raw speech-to-text result is already
    normalized and must not go through this: a second pass could repeat a replacement.

    `chinese` is the variant the model was told to write: zh-TW as above, zh-HK and yue-HK the
    same with s2hk, zh-CN with what is left in Traditional made Simplified by t2s
    (`convert_leftover_traditional`), and None for no conversion.
    """
    chinese = _checked_variant(chinese)
    if chinese is not None and not _HIRAGANA.search(text):
        if chinese == "zh-CN":
            text = convert_leftover_traditional(text)
        else:
            text = convert_leftover_simplified(text, _TO_TRADITIONAL[chinese])
    return apply_replacements(text, rules)
