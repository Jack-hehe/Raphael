"""Typed terminal validation shared by API/CLI task publication and room projection."""
import re
from dataclasses import dataclass


def address_text(text):
    return re.sub(r'```[\s\S]*?```|`[^`\n]*`|(?m:^\s*>.*$)|[“「][^”」]*[”」]|"[^"\n]*"',
                  lambda match: ' ' * len(match.group()), text)


@dataclass(frozen=True)
class Terminal:
    text: str
    reason: str | None = None


def validate_terminal(text, *, names=None, wrapup=False):
    """The final text, with every address that would not be served written as plain text: a wrap-up
    or work result keeps no @, and elsewhere an @ stays only before a name of `names` ("@ Bob"
    becomes "@Bob"). Dropping the whole reply for one stray @ lost a moderator's opening."""
    if not isinstance(text, str) or not text.strip():
        return Terminal('', 'missing_terminal')
    public = address_text(text)
    kept, end = [], 0
    for address in re.finditer(r'(?<![\w\\])@', public):
        tail = public[address.end():]
        gap = len(tail) - len(tail.lstrip())
        named = (not wrapup and tail[gap:] and (names is None or
                 any(re.match(re.escape(name) + r'(?!\w)', tail[gap:], re.I) for name in names)))
        kept.append(text[end:address.start()] + ('@' if named else ''))
        spaced = address.start() == 0 or text[address.start() - 1].isspace()
        end = address.end() + (gap if named or spaced else 0)
    cleaned = (''.join(kept) + text[end:]).strip() if kept else text
    return Terminal(cleaned) if cleaned.strip() else Terminal('', 'missing_terminal')
