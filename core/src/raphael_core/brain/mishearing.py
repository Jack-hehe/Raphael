"""Whether a correction can be a replacement rule: a distinctive form that sounds like its word.

A replacement rule rewrites its misheard form wherever it appears, whatever the sentence means.
The brain learns pairs from corrections the user made by hand, and a correction is often not a
recognition error at all: a synonym ("meeting" for "workshop"), a different word of the same shape
("轉入" for "轉錄"), a word the user simply changed their mind about. So a learned pair may become a
rule only when both of these hold:

- The misheard form is distinctive (`is_distinctive`). Not a function word (to, the, 的, 了, の), not
  a Latin form of two letters or fewer, not a single CJK character, and never a CJK form of two
  characters: those are too often real words, and a rule for them would rewrite correct text. A
  form that fails stays a hint for the speech-to-text engine and the cleanup model, which can judge
  the sentence around it.
- The pair is plausible as a recognition error (`is_plausible`): the two are spelled or sound
  alike, so that the engine could plausibly have written one for the other.

  Latin text (letters and digits, compared without case, spaces and punctuation) is alike when any
  of these holds: the two are the same (a split compound: "tail scale" for "Tailscale"); their edit
  distance is at most 40 % of the longer; they share a stem (a prefix of at least four letters and
  two thirds of the shorter, or one holds the other); or their American Soundex codes are equal
  ("cloud" and "Claude" are both C430).

  CJK text (Han, kana, Hangul) is alike when it has the same length and shares a character in at
  least one position ("拉菲爾" for "拉斐爾").

  A pair that mixes scripts is not plausible by these tests.

The tests are heuristics with no dictionary behind them (the core has no phonetic library and takes
no new dependency), so they only decide what may become a rule. A pair also needs a second
observation, and must not break the replacements the user already has (`DictionaryStore.learn`).
"""

import re
import unicodedata

# What a form must have to be distinctive: more than two Latin letters, or at least three CJK
# characters.
_MIN_LATIN_LETTERS = 3
_MIN_CJK_CHARACTERS = 3
# The Latin pairs that are alike by spelling: the edit distance over the longer text, at most.
_MAX_EDIT_RATIO = 0.4
# A shared stem: this many letters, and two thirds of the shorter text.
_MIN_STEM_LETTERS = 4

# Han, kana and Hangul as (first, last) code points.
_CJK_RANGES = (
    (0x3400, 0x4DBF),  # Han, extension A
    (0x4E00, 0x9FFF),  # Han
    (0xF900, 0xFAFF),  # Han, compatibility ideographs
    (0x20000, 0x2FA1F),  # Han, extension B onwards
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x31F0, 0x31FF),  # Katakana phonetic extensions
    (0xFF66, 0xFF9F),  # halfwidth Katakana
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3130, 0x318F),  # Hangul compatibility Jamo
    (0xAC00, 0xD7AF),  # Hangul syllables
)
# Letters up to here are Latin: Basic Latin, Latin-1 and Latin Extended-A and -B.
_LAST_LATIN = 0x024F

# Words that carry little meaning of their own and turn up in every sentence. A rule for one of
# them would rewrite text that is right.
_LATIN_FUNCTION_WORDS = frozenset(
    """
    a an the this that these those some any each every all both either neither such another other
    many much more most few less several
    i me my mine we us our ours you your yours he him his she her hers it its they them their
    theirs who whom whose which what one ones myself yourself himself herself itself ourselves
    themselves
    of in on at to for from by with about as into onto over under after before between through
    during without within against among around up down out off near than like per via upon
    and or but nor so yet if then because while when where though although unless until whether
    how why
    is am are was were be been being do does did done have has had having will would can could
    shall should may might must
    not no yes too two also only even still here there now well again once ever never always
    already maybe really quite rather very just ok okay oh ah um uh hmm yeah yep nope hey hi hello
    thanks please sorry
    """.split()
)
# Chinese (Traditional and Simplified) and Japanese: the particles, pronouns and conjunctions.
_CJK_FUNCTION_WORDS = frozenset(
    """
    的 了 是 在 這 这 那 我 你 他 她 它 們 们 個 个 有 不 沒 没 和 與 与 就 也 都 還 还 要 會 会 可 能 以
    嗎 吗 呢 吧 啊 喔 欸 嘛 呀 哦 呃 嗯 對 对 好 很 太 把 被 讓 让 給 给 從 从 到 於 于 為 为 因 而 但 如 若
    或 及 之 其 所 著 着 過 过 說 说 去 來 来 上 下 中 裡 里 後 后 前
    我們 我们 你們 你们 他們 他们 她們 她们 它們 它们 這個 这个 那個 那个 這些 这些 那些 什麼 什么
    怎麼 怎么 為什麼 为什么 因為 因为 所以 但是 可是 如果 不過 不过 然後 然后 而且 或者 還是 还是
    已經 已经 可以 沒有 没有 不是 就是 只是 自己 一個 一个 一些 一下 現在 现在 時候 时候 知道
    覺得 觉得 這樣 这样 那樣 那样 這裡 这里 那裡 那里 哪裡 哪里 多少 其實 其实
    の は が を に へ で と も や か ね よ わ ぞ さ です ます でした ました する した して ある いる
    この その あの どの これ それ あれ どれ ここ そこ あそこ こと もの ない なる から まで より
    ので のに けど でも ため など だけ ほど くらい ありません いません
    """.split()
)


def _is_cjk(char: str) -> bool:
    """Whether `char` is a Han, kana or Hangul letter (punctuation of those blocks is not)."""
    if not char.isalpha():
        return False
    code = ord(char)
    return any(first <= code <= last for first, last in _CJK_RANGES)


def _is_latin_letter(char: str) -> bool:
    return char.isalpha() and ord(char) <= _LAST_LATIN


def _phrase(text: str) -> str:
    """`text` for a look-up: the spaces around and inside it collapsed to one, in lower case."""
    return " ".join(text.split()).casefold()


def _compact(text: str) -> str:
    """The letters and digits of `text` in lower case: spaces, hyphens and other marks are gone."""
    return "".join(char for char in text.casefold() if char.isalnum())


def is_distinctive(form: str) -> bool:
    """Whether `form`, a misheard text, is distinctive enough for a replacement rule.

    It is not when it is a function word, or made of function words only ("in a"), or when it
    has fewer than three Latin letters ("to", "py", "O S") and fewer than three CJK characters
    ("我", "轉入"). Digits and punctuation count for nothing: "x86" has one letter.
    """
    phrase = _phrase(form)
    if not phrase or phrase in _CJK_FUNCTION_WORDS:
        return False
    latin = sum(_is_latin_letter(char) for char in phrase)
    cjk = sum(_is_cjk(char) for char in phrase)
    words = re.findall(r"[^\W\d_]+", phrase)  # the runs of letters
    if cjk == 0 and all(word in _LATIN_FUNCTION_WORDS for word in words):
        return False
    return latin >= _MIN_LATIN_LETTERS or cjk >= _MIN_CJK_CHARACTERS


def _edit_distance(first: str, second: str) -> int:
    """The Levenshtein distance: the fewest insertions, deletions and substitutions of one
    character between the two."""
    previous = list(range(len(second) + 1))
    for i, a in enumerate(first, 1):
        current = [i]
        for j, b in enumerate(second, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


_SOUNDEX_DIGITS = {
    **dict.fromkeys("bfpv", "1"),
    **dict.fromkeys("cgjkqsxz", "2"),
    **dict.fromkeys("dt", "3"),
    "l": "4",
    **dict.fromkeys("mn", "5"),
    "r": "6",
}


def _soundex(text: str) -> str:
    """The American Soundex code of the Latin letters in `text` ("" without any): the first
    letter and three digits for the consonants that follow, which sound alike in groups (b f p v,
    c g j k q s x z, d t, l, m n, r). Vowels, h, w and y are skipped; a letter next to another of
    its group counts once, unless a vowel stands between them."""
    letters = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if char.isascii() and char.isalpha()
    )
    if not letters:
        return ""
    code = letters[0].upper()
    previous = _SOUNDEX_DIGITS.get(letters[0], "")
    for char in letters[1:]:
        digit = _SOUNDEX_DIGITS.get(char, "")
        if digit and digit != previous:
            code += digit
        if char not in "hw":  # h and w do not separate two letters of one group
            previous = digit
    return code[:4].ljust(4, "0")


def _alike_in_latin(word: str, form: str) -> bool:
    first, second = _compact(word), _compact(form)
    if first == second:
        return True
    longer, shorter = max(len(first), len(second)), min(len(first), len(second))
    if _edit_distance(first, second) <= _MAX_EDIT_RATIO * longer:
        return True
    stem = 0
    for a, b in zip(first, second, strict=False):
        if a != b:
            break
        stem += 1
    if stem >= _MIN_STEM_LETTERS and stem * 3 >= shorter * 2:
        return True
    if shorter >= _MIN_STEM_LETTERS and (first in second or second in first):
        return True
    code = _soundex(first)
    return bool(code) and code == _soundex(second)


def _alike_in_cjk(word: str, form: str) -> bool:
    first, second = _compact(word), _compact(form)
    return len(first) == len(second) and any(a == b for a, b in zip(first, second, strict=True))


def is_plausible(word: str, form: str) -> bool:
    """Whether the engine could plausibly have written `form` where the speaker said `word`: the
    two are spelled or sound alike (see the module's docstring). A pair of different scripts, or
    one with a text that has no letters, is not."""
    first, second = _compact(word), _compact(form)
    if not first or not second:
        return False
    cjk = [any(_is_cjk(char) for char in text) for text in (first, second)]
    if not any(cjk):
        return _alike_in_latin(word, form)
    if all(cjk):
        return _alike_in_cjk(word, form)
    return False


def may_become_rule(word: str, form: str) -> bool:
    """Whether the pair (`form` misheard for `word`) may become a replacement rule: its form is
    distinctive and the pair is plausible as a recognition error."""
    return is_distinctive(form) and is_plausible(word, form)
