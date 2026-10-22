"""Text as search terms, and text as a count of tokens: what the search over past turns and the
budget of the prompt are made of.

The search (`AgentHistory.search`, SQLite FTS5 with the plain `unicode61` tokenizer) cannot use
Chinese or Japanese as written: `unicode61` takes a run of Han characters with no spaces for one
word, and the `trigram` tokenizer finds nothing shorter than three characters, while most Chinese
words have two (天氣, 會議, 媽媽). So the text is made into terms here, in Python, before SQLite sees
it, the way Lucene's CJK analyzer does it, with no native extension to ship:

- a run of CJK characters (Han, kana, Hangul) becomes its overlapping pairs of characters, so 天氣很好
  is 天氣 氣很 很好, and a run of one character is that character;
- everything else becomes its words, in lower case;
- and each CJK character is also kept on its own, in a second list, for a search of one character.

A query is made into terms the same way, so a word of the query is found as a phrase of its pairs,
which is the word itself wherever it appears (會議 is the pair 會議; 會議室 is 會議 議室, two pairs
in a row). Nothing here reads or writes the database, and nothing logs the text.
"""

import math
import re
import unicodedata
from dataclasses import dataclass

# The scripts written without spaces between words: CJK ideographs (with the extensions and the
# compatibility block), hiragana, katakana (with the half-width forms) and Hangul.
_CJK_CLASS = (
    "぀-ヿ"  # hiragana, katakana
    "㐀-䶿"  # CJK extension A
    "一-鿿"  # CJK unified ideographs
    "가-힯"  # Hangul syllables
    "豈-﫿"  # CJK compatibility ideographs
    "ｦ-ﾟ"  # half-width katakana
    "\U00020000-\U0002ffff"  # CJK extensions B to F
)
_CJK_RUN = re.compile(f"[{_CJK_CLASS}]+")
_CJK_CHAR = re.compile(f"[{_CJK_CLASS}]")
# A CJK run, or a word of anything else: letters and digits that are not CJK (an underscore splits
# a word, as unicode61 does).
_SEGMENT = re.compile(f"[{_CJK_CLASS}]+|[^\\W_{_CJK_CLASS}]+")


def normalized(text: str) -> str:
    """`text` as it is compared: NFKC (full-width letters and digits as ASCII), in lower case."""
    return unicodedata.normalize("NFKC", text).casefold()


@dataclass(frozen=True)
class Terms:
    """A text as the search index holds it: `pairs`, the words and the pairs of CJK characters in
    the order they come, and `chars`, each CJK character on its own. Both are space-separated."""

    pairs: str
    chars: str


def _pairs(run: str) -> list[str]:
    if len(run) == 1:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def tokens(text: str) -> list[str]:
    """The terms of `text` in order: a CJK run as its pairs, anything else as its words."""
    found: list[str] = []
    for segment in _SEGMENT.findall(normalized(text)):
        if _CJK_RUN.fullmatch(segment):
            found.extend(_pairs(segment))
        else:
            found.append(segment)
    return found


def index_terms(*texts: str | None) -> Terms:
    """What the search index holds for a turn made of `texts` (the question, the answer...)."""
    pairs: list[str] = []
    chars: list[str] = []
    for text in texts:
        if not text:
            continue
        pairs.extend(tokens(text))
        chars.extend(_CJK_CHAR.findall(normalized(text)))
    return Terms(" ".join(pairs), " ".join(chars))


@dataclass(frozen=True)
class QueryWord:
    """One word of a search, as terms: the phrase of `pairs` to look for, or one CJK character
    (`char`) to look for on its own."""

    text: str
    pairs: tuple[str, ...]
    char: str | None = None


def query_words(query: str, *, limit: int = 8) -> list[QueryWord]:
    """The words of a search, each made into terms; at most `limit`, each once. A query written
    without spaces (a whole sentence in Chinese) is one long word: the search tries its pairs one
    by one when the phrase is not found."""
    words: list[QueryWord] = []
    seen: set[str] = set()
    for word in normalized(query).split():
        terms = tuple(tokens(word))
        if not terms or word in seen:
            continue
        seen.add(word)
        char = terms[0] if len(terms) == 1 and _CJK_CHAR.fullmatch(terms[0]) else None
        words.append(QueryWord(word, terms, char))
        if len(words) >= limit:
            break
    return words


def fts_quote(term: str) -> str:
    """A term as an FTS5 string: in double quotes, any quote inside doubled. The terms are letters
    and digits only, so this is a precaution."""
    return '"' + term.replace('"', '""') + '"'


# --- tokens -----------------------------------------------------------------------------------------

# About how many characters of text other than CJK make one token, for the tokenizers of the
# models Raphael uses (a CJK character is about one token in each of them, often more).
_CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str | None) -> int:
    """About how many tokens `text` is to a model: a CJK character is one, four other characters
    are one. An estimate, on the high side for English; the budgets are set with it."""
    if not text:
        return 0
    cjk = len(_CJK_CHAR.findall(text))
    return cjk + math.ceil((len(text) - cjk) / _CHARS_PER_TOKEN)
