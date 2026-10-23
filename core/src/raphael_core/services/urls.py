"""The web addresses in a text, and whether two of them are the same page.

`urls_in` finds the `http` and `https` addresses written in a text: the owner's own request (which
the fast tier reads before it answers), or what a tool of a background task returned (which that
task may then read). An address ends at a space, at a quote or bracket, or at a CJK punctuation
mark; the punctuation a sentence puts after it (a full stop, a comma, a closing bracket that has no
opening one inside it) is not part of it.

`page_key` is what two addresses are compared by when a task asks to read one: the scheme, the host
in lower case, the port when it is not the scheme's own, and the path, without the query and the
fragment. A task may read an address it saw exactly, or one of the same page (`seen`).
"""

import re
from collections.abc import Iterable

import httpx

_URL = re.compile(r"""https?://[^\s<>"'`「」『』【】《》〈〉（）［］｛｝，。、；：！？]+""", re.IGNORECASE)
_TRAILING = ".,;:!?'\"*_~"
# The most addresses taken from one text.
MAX_URLS = 50
_PAIRS = {")": "(", "]": "[", "}": "{"}


def _trimmed(url: str) -> str:
    while url:
        last = url[-1]
        if last in _TRAILING:
            url = url[:-1]
        elif last in _PAIRS and url.count(_PAIRS[last]) < url.count(last):
            url = url[:-1]
        else:
            break
    return url


def urls_in(text: str, limit: int = MAX_URLS) -> list[str]:
    """The http and https addresses written in `text`, each once, in order, at most `limit`."""
    found: list[str] = []
    for match in _URL.finditer(text):
        url = _trimmed(match.group(0))
        if "://" not in url or url.endswith("://") or url in found:
            continue
        found.append(url)
        if len(found) >= limit:
            break
    return found


def page_key(url: str) -> str | None:
    """What `url` is compared by (see the module), or None for what is no http(s) address."""
    try:
        parsed = httpx.URL(url.strip())
    except (httpx.InvalidURL, TypeError, ValueError):
        return None
    if parsed.scheme not in ("http", "https") or not parsed.host:
        return None
    default = 443 if parsed.scheme == "https" else 80
    port = "" if parsed.port in (None, default) else f":{parsed.port}"
    path = parsed.path or "/"
    return f"{parsed.scheme}://{parsed.host.lower()}{port}{path}"


class SeenUrls:
    """The addresses a background task has seen: in its request, and in what its tools returned.
    In memory, for the task's run only."""

    def __init__(self, texts: Iterable[str] = ()) -> None:
        self._exact: set[str] = set()
        self._pages: set[str] = set()
        for text in texts:
            self.add_from(text)

    def add(self, url: str) -> None:
        key = page_key(url)
        if key is None:
            return
        self._exact.add(url.strip())
        self._pages.add(key)

    def add_from(self, text: str) -> None:
        """Every address written in `text`."""
        for url in urls_in(text, limit=10_000):
            self.add(url)

    def seen(self, url: str) -> bool:
        """Whether `url` was seen as it is written, or as the same page."""
        if url.strip() in self._exact:
            return True
        key = page_key(url)
        return key is not None and key in self._pages

    def __len__(self) -> int:
        return len(self._exact)
