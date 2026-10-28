"""A web page as readable text, made here with the standard library's `html.parser`.

What is kept: the title, the headings (as "#" lines, one "#" for each level), the paragraphs and the
other blocks as lines, the items of a list as "- " lines, the cells of a table side by side, and the
words of a link (its address is not: an address in the text is what the background worker may read
next, and the words are what a reader sees). What is left out: scripts, styles, the navigation, the
footer, and what a reader never sees as text (`noscript`, templates, SVG, frames, embedded objects,
canvases, buttons and the options of a list to pick from). Ruby annotations and their fallback
parentheses are omitted; the base text is kept.
Spaces are folded, and a line has words: there are no empty ones.

The parser takes any HTML, however broken: what it cannot make sense of it reads as text. An
element left open that is one of those left out leaves out the rest of the page, which is the safe
way to be wrong.
"""

from html.parser import HTMLParser

# The elements whose content is not text a reader sees, or is the page's frame around its content.
_SKIPPED = frozenset(
    {"script", "style", "nav", "footer", "noscript", "template", "svg", "iframe", "object", "canvas",
     "select", "button", "rt", "rp"}
)  # fmt: skip
# The elements that end a line.
_BLOCKS = frozenset(
    {"p", "div", "section", "article", "main", "header", "aside", "ul", "ol", "li", "table", "tr",
     "blockquote", "pre", "dl", "dt", "dd", "figure", "figcaption", "form", "fieldset", "address",
     "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "details", "summary"}
)  # fmt: skip
_HEADINGS = {f"h{level}": level for level in range(1, 7)}
# Elements that have no end tag: they never open a stretch to skip.
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track",
     "wbr"}
)  # fmt: skip


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skipping: list[str] = []  # the open elements being left out, innermost last
        self._lines: list[str] = []
        self._line: list[str] = []
        self._prefix = ""

    def _end_line(self) -> None:
        words = " ".join("".join(self._line).split())
        if words:
            self._lines.append(self._prefix + words)
        self._line = []
        self._prefix = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skipping:
            if tag in _SKIPPED and tag not in _VOID:
                self._skipping.append(tag)
            return
        if tag in _SKIPPED:
            self._skipping.append(tag)
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in ("td", "th"):
            self._line.append(" ")
        if tag in _BLOCKS:
            self._end_line()
            if tag in _HEADINGS:
                self._prefix = "#" * _HEADINGS[tag] + " "
            elif tag == "li":
                self._prefix = "- "

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._skipping and tag in ("br", "hr"):
            self._end_line()

    def handle_endtag(self, tag: str) -> None:
        if self._skipping:
            # The end of an element left out closes whatever was left open inside it.
            if tag in self._skipping:
                while self._skipping.pop() != tag:
                    pass
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _BLOCKS:
            self._end_line()

    def handle_data(self, data: str) -> None:
        if self._skipping:
            return
        if self._in_title:
            self.title += data
            return
        self._line.append(data)

    def text(self) -> str:
        self._end_line()
        return "\n".join(self._lines)


def html_to_text(html: str) -> tuple[str, str]:
    """The title and the readable text of the page `html`."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    return " ".join(reader.title.split()), reader.text()
