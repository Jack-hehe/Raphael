"""Small PDFs written by hand for the tests: pages of text, and pages that are only a JPEG (a scan)."""

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x00" * 64 + b"\xff\xd9"


def make_pdf(pages: list[str | bytes]) -> bytes:
    """A PDF of these pages: a page that is text shows it in Helvetica, one that is bytes is a
    picture of those bytes (a JPEG) and nothing else."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in at the end
    pages_id = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for page in pages:
        if isinstance(page, str):
            text = page.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("latin-1")
            resources = f"<< /Font << /F1 {font} 0 R >> >>".encode()
        else:
            image = add(
                b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceRGB"
                b" /BitsPerComponent 8 /Filter /DCTDecode /Length "
                + str(len(page)).encode()
                + b" >>\nstream\n"
                + page
                + b"\nendstream"
            )
            stream = b"q 500 0 0 700 50 50 cm /Im1 Do Q"
            resources = f"<< /XObject << /Im1 {image} 0 R >> >>".encode()
        content = add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        kids.append(
            add(
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792]"
                f" /Contents {content} 0 R /Resources ".encode()
                + resources
                + b" >>"
            )
        )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    refs = " ".join(f"{kid} 0 R" for kid in kids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{refs}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
