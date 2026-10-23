"""The files the owner hands Raphael with a request: an image or a PDF.

They come from the app (the composer's 「＋」) and from a chat app (a photo or a document sent to
the bot), always as bytes with the request itself, never as a path: nothing in the core opens a file
because a request named it. On the wire, `agent.ask` takes them as `attachments`, a list of
`{name, mime, data}` with the bytes in base64; `from_wire` turns that list into `Attachment`s and
refuses anything else whole, so a request either comes with every file it was sent or fails as a bad
request (a text file is not one of these: the app puts its text into the question).

What is checked here: at most MAX_ATTACHMENTS, a type of ACCEPTED_TYPES, and the first bytes really
of that type (a file named photo.png that is an executable is refused), and at most the size of its
kind. The name is only for showing the owner which file a turn had; it is cut to MAX_NAME_CHARS.

Nothing here logs a name or a byte of a file.
"""

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
PDF_TYPE = "application/pdf"
ACCEPTED_TYPES = (*IMAGE_TYPES, PDF_TYPE)
MAX_ATTACHMENTS = 4
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_NAME_CHARS = 120

# The first bytes of each type. WebP is "RIFF", four bytes of length, then "WEBP".
_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    PDF_TYPE: (b"%PDF-",),
}


class AttachmentError(ValueError):
    """A list of attachments that cannot be taken. `kind` says why, in one word: "count", "type",
    "size", "data" or "name"; never what the file held."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class Attachment:
    """One file handed with a request: its name as the owner saw it, its type, and its bytes."""

    name: str
    mime: str
    data: bytes

    @property
    def is_image(self) -> bool:
        return self.mime in IMAGE_TYPES

    @property
    def is_pdf(self) -> bool:
        return self.mime == PDF_TYPE

    def data_url(self) -> str:
        """The file as a `data:` URL, the way a model is shown an image or a PDF."""
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


def sniff_ok(mime: str, data: bytes) -> bool:
    """Whether `data` starts the way a file of type `mime` does."""
    if mime == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return any(data.startswith(magic) for magic in _MAGIC.get(mime, ()))


def checked(name: str, mime: str, data: bytes) -> Attachment:
    """An `Attachment` of these parts, or AttachmentError when one of them is not acceptable."""
    mime = mime.strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in ACCEPTED_TYPES:
        raise AttachmentError("type")
    limit = MAX_PDF_BYTES if mime == PDF_TYPE else MAX_IMAGE_BYTES
    if not data or len(data) > limit:
        raise AttachmentError("size")
    if not sniff_ok(mime, data):
        raise AttachmentError("type")
    name = " ".join(name.split())[:MAX_NAME_CHARS]
    if not name:
        raise AttachmentError("name")
    return Attachment(name=name, mime=mime, data=data)


def from_wire(items: Any) -> list[Attachment]:
    """The attachments of a request as `agent.ask` received them (None or a list of `{name, mime,
    data}`, data in base64), or AttachmentError for the first thing wrong with them."""
    if items is None:
        return []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise AttachmentError("data")
    if len(items) > MAX_ATTACHMENTS:
        raise AttachmentError("count")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise AttachmentError("data")
        name, mime, data = item.get("name"), item.get("mime"), item.get("data")
        if not isinstance(name, str) or not isinstance(mime, str) or not isinstance(data, str):
            raise AttachmentError("data")
        # base64 of the largest file a request may carry, plus a little: anything longer is refused
        # before it is decoded.
        if len(data) > (MAX_PDF_BYTES * 4) // 3 + 8:
            raise AttachmentError("size")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise AttachmentError("data") from None
        result.append(checked(name, mime, raw))
    return result
