"""Dictionary suggestions: names and terms that keep coming up in the recent dictation.

A word the user dictates again and again, and that looks like a name or a technical term
(Tailscale, GPT-SoVITS, a katakana name), is worth a place in the personal dictionary: with it,
Raphael spells the word the same way every time.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from itertools import chain

# A Latin word: ASCII letters and digits, starting with a letter, with "-" or "." allowed between
# them (GPT-SoVITS, Node.js). It must not start inside a longer run of ASCII letters and digits
# ("Password" in "1Password" is not a word), and being greedy it never stops inside one either.
# Anything else may touch it, CJK text included: Chinese has no spaces, and the word in
# "用Tailscale連回家" has to be found.
_LATIN_WORD = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*")
# What makes a Latin word look like a name or a term: a capital letter (the first or an inner one,
# as in iPhone), a digit or a hyphen. Plain lowercase words are everyday language.
_NAME_LIKE = re.compile(r"[A-Z0-9-]")

# A katakana word: three or more characters, the first a katakana letter (ァ to ヺ), the rest
# letters or the prolonged sound mark (ー). The mark is in the katakana block, but hiragana use it
# too ("ねー"), so a word never starts with it. The block's punctuation (゠ ・) ends a word:
# "・・・" and "ーーー" are no words, a bullet in front of a word is no part of it, and
# "ジョン・スミス" is two words. The characters are given by code point, as they are easy to mix up
# with look-alikes.
_KATAKANA_LETTERS = f"{chr(0x30A1)}-{chr(0x30FA)}"
_PROLONGED_SOUND_MARK = chr(0x30FC)
_KATAKANA_WORD = re.compile(
    f"[{_KATAKANA_LETTERS}][{_KATAKANA_LETTERS}{_PROLONGED_SOUND_MARK}]{{2,}}"
)

# Words that look like names but are no use in a dictionary. They are compared exactly as written,
# unlike the known and dismissed words: "Ok" and "THE" are not stopped.
_STOPLIST = frozenset({"I", "OK", "AI", "API", "OS", "UI", "PC", "App", "The"})


def _candidates(text: str) -> Iterator[str]:
    """Every Latin word that looks like a name or a term, and every katakana word, in `text`."""
    for match in _LATIN_WORD.finditer(text):
        word = match.group()
        if _NAME_LIKE.search(word):
            yield word
    yield from _KATAKANA_WORD.findall(text)


def suggest_words(
    texts: Sequence[str],
    known: set[str],
    dismissed: set[str],
    min_count: int = 3,
    limit: int = 3,
) -> list[tuple[str, int]]:
    """The words most worth adding to the dictionary, as `(word, count)`, most used first.

    `texts` are the recent dictations. A candidate is a single token of one of two kinds: a Latin
    word with a capital letter, an inner capital, a digit or a hyphen (OpenRouter, iPhone, gpt4,
    GPT-SoVITS), or a katakana word of three or more characters. Every occurrence counts, and
    spellings that differ only in letter case are one word, shown in the spelling met most often
    (the one met first when they are equally common).

    A word in `known` (the dictionary) or in `dismissed`, in any letter case, is never suggested.
    Nor is one on the stoplist (I, OK, AI, API, OS, UI, PC, App, The), which is matched exactly as
    written. Words used fewer than `min_count` times are left out. The rest are sorted by count,
    then alphabetically, and the first `limit` of them are returned (none when `limit` is 0 or
    less).
    """
    skip = {word.casefold() for word in chain(known, dismissed)}
    # By the word in lowercase: how many times each of its spellings was met.
    spellings: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for text in texts:
        for word in _candidates(text):
            key = word.casefold()
            if word not in _STOPLIST and key not in skip:
                spellings[key][word] += 1

    suggestions: list[tuple[str, int]] = []
    for counts in spellings.values():
        count = counts.total()
        if count >= min_count:
            suggestions.append((counts.most_common(1)[0][0], count))
    # Alphabetical ignores letter case, so that iPhone sorts under I and not after Z.
    suggestions.sort(key=lambda suggestion: (-suggestion[1], suggestion[0].casefold()))
    # A negative limit would otherwise count from the end.
    return suggestions[: max(limit, 0)]
