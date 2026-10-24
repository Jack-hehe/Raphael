"""Files supplied with a request, as bounded bytes rather than caller-supplied paths.

Every MIME type is accepted. Known vision/PDF types still require their magic bytes;
all files share the per-file and total limits. Readers treat contents as untrusted data.
Stored IDs are assigned only by the local attachment store, never taken from the wire.
No file names, contents or bytes are logged.
"""

import base64
import binascii
import mimetypes
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
PDF_TYPE = "application/pdf"
ACCEPTED_TYPES = (*IMAGE_TYPES, PDF_TYPE)
MAX_ATTACHMENTS = 4
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
# Legacy avatar and messaging download budgets stay independent of composer uploads.
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
    stored_id: str | None = None

    def metadata(self) -> dict:
        result = {"name": self.name, "mime": self.mime}
        if self.stored_id:
            result.update(id=self.stored_id, size=len(self.data))
        return result

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


def checked(name: str, mime: str, data: bytes, *, max_bytes: int = MAX_FILE_BYTES) -> Attachment:
    """An `Attachment` of these parts, or AttachmentError when one of them is not acceptable."""
    mime = mime.strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", mime):
        mime = "application/octet-stream"
    if mime == "application/octet-stream":
        mime = mimetypes.guess_type(name)[0] or mime
    if not data or len(data) > max_bytes:
        raise AttachmentError("size")
    if mime in ACCEPTED_TYPES and not sniff_ok(mime, data):
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
        if len(data) > (MAX_FILE_BYTES * 4) // 3 + 8:
            raise AttachmentError("size")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise AttachmentError("data") from None
        result.append(checked(name, mime, raw))
        if sum(len(a.data) for a in result) > MAX_TOTAL_BYTES:
            raise AttachmentError("size")
    return result
