"""Tests for the files handed with a request: only real images and PDFs, within their sizes."""

import base64

import pytest

from raphael_core.services.attachments import (
    MAX_ATTACHMENTS,
    MAX_IMAGE_BYTES,
    AttachmentError,
    checked,
    from_wire,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PDF = b"%PDF-1.7\n" + b"\x00" * 16
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def wire(name: str, mime: str, data: bytes) -> dict:
    return {"name": name, "mime": mime, "data": base64.b64encode(data).decode("ascii")}


def test_nothing_attached_is_an_empty_list():
    assert from_wire(None) == []
    assert from_wire([]) == []


def test_an_image_and_a_pdf_are_taken_with_their_bytes():
    got = from_wire([wire("貓.png", "image/png", PNG), wire("報告.pdf", "application/pdf", PDF)])
    assert [(a.name, a.mime, a.data) for a in got] == [
        ("貓.png", "image/png", PNG),
        ("報告.pdf", "application/pdf", PDF),
    ]
    assert got[0].is_image and not got[0].is_pdf
    assert got[1].is_pdf and not got[1].is_image
    assert got[0].data_url().startswith("data:image/png;base64,")


def test_webp_and_the_jpg_alias_are_known():
    assert checked("a.webp", "image/webp", WEBP).mime == "image/webp"
    assert checked("a.jpg", "image/jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 8).mime == "image/jpeg"


@pytest.mark.parametrize(
    ("mime", "data", "kind"),
    [
        ("text/plain", b"hello", "type"),
        ("image/png", b"MZ\x90\x00 an executable", "type"),  # named an image, is not one
        ("application/pdf", PNG, "type"),
        ("image/png", b"", "size"),
        ("image/png", None, "size"),  # one byte over the limit, made in the test
    ],
    ids=["text", "exe-as-png", "png-as-pdf", "empty", "too-big"],
)
def test_a_file_that_is_not_what_it_says_or_too_big_is_refused(mime, data, kind):
    if data is None:
        data = PNG + b"\x00" * (MAX_IMAGE_BYTES - len(PNG) + 1)
    with pytest.raises(AttachmentError) as caught:
        checked("f", mime, data)
    assert caught.value.kind == kind


def test_a_bad_list_is_refused_whole():
    with pytest.raises(AttachmentError) as caught:
        from_wire([wire("a.png", "image/png", PNG)] * (MAX_ATTACHMENTS + 1))
    assert caught.value.kind == "count"
    for bad in ("x", [1], [{"name": "a", "mime": "image/png", "data": "@@not base64@@"}]):
        with pytest.raises(AttachmentError):
            from_wire(bad)


def test_the_name_is_tidied_and_must_be_there():
    long = "a" * 500
    assert len(checked(long, "image/png", PNG).name) == 120
    assert checked("  my \n photo.png ", "image/png", PNG).name == "my photo.png"
    with pytest.raises(AttachmentError):
        checked("   ", "image/png", PNG)
