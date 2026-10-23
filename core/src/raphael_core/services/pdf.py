"""Reading a PDF the owner handed Raphael, or one a page link led to: by the `pdf_service` setting.

- "builtin" takes the text here, page by page, with pypdf, at no cost. A page that has no text
  layer (a scan: its text is a picture of text) is shown to the vision model (`vision.Vision`) as
  an image instead, at most MAX_SCANNED_PAGES of them: the picture is the page's own embedded JPEG,
  which is what a scanner writes. A scan of another kind (a fax's black-and-white image, say), and
  the pages past the limit, are said to be unread. Without a vision model (a PDF from the web, read
  by the page reader) only the text layer is read.
- "openrouter" sends the whole file to the vision model, which reads it through OpenRouter's
  `file-parser` plugin (`Vision.read_file`). It is paid.

Either way the result is `{pages, text, truncated}`: the number of pages (0 when it is not known),
the text held to the budget of a tool's text (`common.fit`), and whether it had to be cut. The text
is data for the call that words the answer, never instructions.

pypdf runs on a worker thread: a large PDF takes a while, and the event loop must not wait for it.
A file pypdf cannot read is "bad_pdf"; one locked with a password is "encrypted". Nothing here
logs a file's name or a word of it.
"""

import asyncio
import io
import logging
from dataclasses import dataclass, field

from ..agent.ledger import Ledger
from .attachments import Attachment, AttachmentError, checked
from .common import TEXT_BUDGET, ServiceError, fit
from .vision import Vision

logger = logging.getLogger(__name__)

MAX_SCANNED_PAGES = 8
# A page with fewer characters of text than this has no text layer worth the name.
_SCAN_CHARS = 8
# The text of more pages than this is not taken: what would be cut anyway is not read.
_MAX_PAGES_READ = 300
_STOP_CHARS = TEXT_BUDGET * 4
_SCAN_QUESTION = (
    "These are scanned pages of a document. Give their text, page by page, word for word."
)


@dataclass
class _Pages:
    count: int = 0
    texts: list[tuple[int, str]] = field(default_factory=list)  # (page number, its text)
    scans: list[tuple[int, bytes]] = field(default_factory=list)  # (page number, its JPEG)
    unread: list[int] = field(default_factory=list)  # pages with no text that could be read


def _page_jpeg(page: object) -> bytes | None:
    """The JPEG a scanned page is a picture of: the largest image of the page that is stored as a
    JPEG (`/DCTDecode`), as its file's bytes. None when it has none."""
    found: bytes | None = None
    try:
        resources = page["/Resources"]  # type: ignore[index]
        objects = resources.get("/XObject") or {}
        objects = objects.get_object()
        for name in objects:
            image = objects[name].get_object()
            if image.get("/Subtype") != "/Image":
                continue
            filters = image.get("/Filter")
            names = filters if isinstance(filters, list) else [filters]
            if [str(n) for n in names] != ["/DCTDecode"]:
                continue
            data = image.get_data()  # a JPEG stream decodes to itself: the JPEG file
            if isinstance(data, bytes) and (found is None or len(data) > len(found)):
                found = data
    except Exception:
        return None
    return found


def _read(data: bytes) -> _Pages:
    """The pages of the PDF `data`: their text, and the scans among them. On a worker thread."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ServiceError("encrypted")
        pages = _Pages(count=len(reader.pages))
        total = 0
        for number, page in enumerate(reader.pages, start=1):
            if number > _MAX_PAGES_READ or total > _STOP_CHARS:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
            if len(text.strip()) >= _SCAN_CHARS:
                pages.texts.append((number, text))
                total += len(text)
                continue
            jpeg = _page_jpeg(page) if len(pages.scans) < MAX_SCANNED_PAGES else None
            if jpeg is not None:
                pages.scans.append((number, jpeg))
            else:
                pages.unread.append(number)
        return pages
    except ServiceError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError, IndexError, OSError):
        raise ServiceError("bad_pdf") from None
    except Exception as exc:  # pypdf raises more kinds than it documents
        logger.warning("pypdf could not read a PDF: %s", type(exc).__name__)
        raise ServiceError("bad_pdf") from None


def page_count(data: bytes) -> int:
    """The number of pages of the PDF `data`; 0 when pypdf cannot say."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return 0


async def read_pdf(
    document: Attachment,
    *,
    service: str = "builtin",
    vision: Vision | None = None,
    question: str = "",
    ledger: Ledger | None = None,
) -> dict:
    """`{pages, text, truncated}` of the PDF `document`, read by `service` (see the module).
    `vision` and `ledger` are what reads a scanned page, or the whole file for "openrouter"; without
    them only the text layer is read. ServiceError of a kind."""
    if service == "openrouter":
        if vision is None or ledger is None:
            raise ServiceError("not_configured")
        read = await vision.read_file(document, question, ledger)
        pages = await asyncio.to_thread(page_count, document.data)
        text, cut = fit(read["text"])
        return {"pages": pages, "text": text, "truncated": cut}
    found = await asyncio.to_thread(_read, document.data)
    parts = [(number, f"[Page {number}]\n{text}") for number, text in found.texts]
    if found.scans and vision is not None and ledger is not None:
        images = []
        for number, jpeg in found.scans:
            try:
                images.append(checked(f"page-{number}.jpg", "image/jpeg", jpeg))
            except AttachmentError:  # too large for a model, or no JPEG after all
                found.unread.append(number)
        if images:
            try:
                seen = await vision.look(images, _SCAN_QUESTION, ledger)
                numbers = ", ".join(str(n) for n, _ in found.scans)
                heading = f"[Pages {numbers}, read from their images]"
                parts.append((found.scans[0][0], f"{heading}\n{seen['text']}"))
            except ServiceError as error:
                logger.info("The scanned pages of a PDF were not read: %s", error.kind)
                found.unread.extend(n for n, _ in found.scans)
    else:
        found.unread.extend(n for n, _ in found.scans)
    parts.sort(key=lambda part: part[0])
    text = "\n\n".join(body for _, body in parts)
    if found.unread:
        unread = ", ".join(str(n) for n in sorted(set(found.unread)))
        text = f"{text}\n\n[Pages with no text that could be read: {unread}]".strip()
    if not parts:
        raise ServiceError("no_text")
    text, cut = fit(text)
    return {"pages": found.count, "text": text, "truncated": cut}
