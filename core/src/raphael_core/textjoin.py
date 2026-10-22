"""Joining pieces of text the way they were spoken: the stretches of speech that speech-to-text
heard one at a time, in a dictation or a voice message. (The pieces of a long dictation that the
cleanup tidies one at a time are joined by what they were cut at instead: `cleanup.join_parts`.)

Chinese and Japanese are not written with spaces, so two pieces that meet between CJK characters
are joined with nothing; any other join takes one space. Nothing here needs more than the standard
library, so the device side and the messaging can both use it.
"""

from collections.abc import Sequence


def is_cjk(ch: str) -> bool:
    """Han, kana, and the punctuation that goes with them (CJK and full-width forms)."""
    code = ord(ch)
    return (
        0x3000 <= code <= 0x30FF  # CJK symbols and punctuation, hiragana, katakana
        or 0x31F0 <= code <= 0x31FF  # katakana phonetic extensions
        or 0x3400 <= code <= 0x4DBF  # Han, extension A
        or 0x4E00 <= code <= 0x9FFF  # Han
        or 0xF900 <= code <= 0xFAFF  # Han, compatibility ideographs
        or 0xFF00 <= code <= 0xFFEF  # full-width and half-width forms
        or 0x20000 <= code <= 0x2FA1F  # Han, extension B onwards
    )


def join_segments(texts: Sequence[str]) -> str:
    """The texts of the segments as one: no separator where both sides of the join are CJK (Chinese
    and Japanese are not written with spaces), one space everywhere else."""
    joined = ""
    for text in texts:
        piece = text.strip()
        if not piece:
            continue
        if joined and not (is_cjk(joined[-1]) and is_cjk(piece[0])):
            joined += " "
        joined += piece
    return joined
