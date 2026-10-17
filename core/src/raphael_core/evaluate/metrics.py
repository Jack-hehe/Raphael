"""The character error rate (CER) the speech-to-text evaluation reports.

CER is the number of character edits (insertions, deletions, substitutions) that turn what the
engine heard into what was said, divided by the length of what was said. Lower is better. Both
texts are normalized first, so a difference of script, case, width, spacing or punctuation is not
counted as a mistake: the engines write Simplified or Traditional Chinese as they please, the
application turns it into Traditional Chinese (Taiwan) afterwards, and it is the characters that
matter here.
"""

import unicodedata

from raphael_core.brain.textnorm import to_taiwan_traditional


def normalize_for_cer(text: str) -> str:
    """`text` in Taiwan Traditional Chinese, lower case, without whitespace or punctuation.

    The Chinese is converted the way the application does it (Japanese text is left alone), then
    full-width letters and digits become plain ones (NFKC), then every space, control character and
    punctuation mark goes. Symbols such as + and % stay: they can be spoken.
    """
    text = unicodedata.normalize("NFKC", to_taiwan_traditional(text)).lower()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in "CPZ")


def edit_distance(a: str, b: str) -> int:
    """The Levenshtein distance between `a` and `b`, counted in characters (code points)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        current = [i]
        for j, ch_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,  # delete ch_a
                    current[j - 1] + 1,  # insert ch_b
                    previous[j - 1] + (ch_a != ch_b),  # substitute, or keep
                )
            )
        previous = current
    return previous[-1]


def cer(ref: str, hyp: str) -> float:
    """The character error rate of `hyp` against the reference `ref`, after normalizing both.

    It is above 1.0 when `hyp` is much longer than `ref`. With nothing in `ref` there is nothing to
    divide by: 0.0 if `hyp` is empty too, else 1.0.
    """
    ref, hyp = normalize_for_cer(ref), normalize_for_cer(hyp)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)
