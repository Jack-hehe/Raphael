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
    if not isinstance(text, str) or not text.strip():
        return Terminal('', 'missing_terminal')
    public = address_text(text)
    addresses = list(re.finditer(r'(?<![\w\\])@', public))
    for address in addresses:
        tail = public[address.end():]
        if wrapup or not tail or tail[0].isspace():
            return Terminal('', 'dangling_mention')
        if names is not None and not any(re.match(re.escape(name) + r'(?!\w)', tail, re.I) for name in names):
            return Terminal('', 'unknown_mention')
    return Terminal(text)
