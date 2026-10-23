"""Images and PDFs sent to the bot: a photo (its largest size) or a document is fetched into memory,
checked, and handed to the assistant with its caption as the request; an album is one request. The
Bot API is the fake of test_voice, which also serves files; nothing here touches the network."""

import asyncio
import contextlib
import logging
import time

from raphael_core.messaging.channels.base import Attachment, Incoming
from raphael_core.messaging.channels.telegram import TelegramApi, TelegramChannel
from raphael_core.messaging.core import Switchboard
from raphael_core.messaging.words import words
from raphael_core.services.attachments import MAX_ATTACHMENTS, MAX_IMAGE_BYTES, MAX_PDF_BYTES

from .test_switchboard import FakeChannel
from .test_telegram import OWNER, TOKEN
from .test_voice import files, files_http  # noqa: F401 (fixtures)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32


async def settle(predicate, seconds=3.0):
    """Wait for `predicate` by the clock on the wall. (A sleep of a few milliseconds may end at
    once on Windows, so counting them does not measure the album's wait.)"""
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("did not happen")
        await asyncio.sleep(0.01)


async def run_until(gateway, predicate):
    task = asyncio.ensure_future(gateway.run())
    try:
        await settle(predicate)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def gateway_for(fake, http, *, answer="《答》好", allowed=(OWNER,), album_wait=0.05):
    """A switchboard on the fake Bot API whose assistant records each request with its files."""
    asked: list[dict] = []

    async def ask(session, text, language, source, /, *, attachments=()):
        asked.append(
            {"session": session, "text": text, "source": source, "files": list(attachments)}
        )
        return answer

    async def stop(session):
        return False

    async def nap(seconds):
        await asyncio.sleep(0)

    users = {str(user) for user in allowed}
    gateway = Switchboard(
        TelegramChannel(TelegramApi(TOKEN, http), sleep=nap),
        ask=ask,
        stop=stop,
        is_allowed=users.__contains__,
        chat_allowed=users.__contains__,
        language=lambda: "en",
        album_wait=album_wait,
    )
    gateway.asked = asked
    return gateway


def photo_sizes(prefix="p", size=len(JPEG)):
    """A photo as Telegram sends it: the same picture in sizes from a thumbnail up, the largest
    not last, so that the choice is by the pixels and not by the order."""
    return [
        {"file_id": f"{prefix}-small", "width": 90, "height": 60, "file_size": 100},
        {"file_id": f"{prefix}-large", "width": 1280, "height": 853, "file_size": size},
        {"file_id": f"{prefix}-medium", "width": 320, "height": 213, "file_size": 900},
    ]


# --- one file ---


async def test_a_photo_is_asked_about_at_its_largest_with_the_caption(files, files_http):
    files.files["p-large"] = JPEG
    files.message(OWNER, None, photo=photo_sizes(), caption="what is this plant")
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    assert files.get_files == ["p-large"] and files.downloads == ["p-large"]
    [request] = gateway.asked
    assert request["session"] == f"telegram:{OWNER}" and request["source"] == "telegram"
    assert request["text"] == "what is this plant"
    [photo] = request["files"]
    assert (photo.name, photo.mime, photo.data) == ("photo.jpg", "image/jpeg", JPEG)
    assert files.sent == [(OWNER, "《答》好")]


async def test_a_photo_without_a_caption_is_a_request_with_empty_text(files, files_http):
    files.files["p-large"] = JPEG
    files.message(OWNER, None, photo=photo_sizes())
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    assert gateway.asked[0]["text"] == "" and len(gateway.asked[0]["files"]) == 1


async def test_a_pdf_document_keeps_its_name(files, files_http):
    files.files["d1"] = PDF
    files.message(
        OWNER,
        None,
        document={"file_id": "d1", "file_name": "invoice.pdf", "mime_type": "application/pdf", "file_size": len(PDF)},
        caption="summarise",
    )
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    [pdf] = gateway.asked[0]["files"]
    assert (pdf.name, pdf.mime, pdf.data, pdf.is_pdf) == ("invoice.pdf", "application/pdf", PDF, True)
    assert gateway.asked[0]["text"] == "summarise"


async def test_an_image_sent_as_a_document_is_an_image(files, files_http):
    files.files["d2"] = PNG
    files.message(OWNER, None, document={"file_id": "d2", "mime_type": "image/png", "file_size": len(PNG)})
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    [image] = gateway.asked[0]["files"]
    assert (image.name, image.mime, image.is_image) == ("image.png", "image/png", True)


async def test_another_kind_of_document_gets_the_unsupported_reply_unfetched(files, files_http):
    files.files["z"] = b"PK\x03\x04"
    files.message(
        OWNER, None, document={"file_id": "z", "file_name": "a.zip", "mime_type": "application/zip", "file_size": 4}
    )
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    assert files.get_files == [] and files.downloads == [] and gateway.asked == []
    assert files.sent == [(OWNER, words("en", "unsupported"))]


async def test_a_video_gets_the_unsupported_reply(files, files_http):
    files.message(OWNER, None, video={"file_id": "v", "duration": 3, "file_size": 1000})
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    assert files.downloads == [] and gateway.asked == []
    assert files.sent == [(OWNER, words("en", "unsupported"))]


async def test_a_photo_larger_than_allowed_is_declined_before_it_is_fetched(files, files_http):
    files.message(OWNER, None, photo=photo_sizes(size=MAX_IMAGE_BYTES + 1))
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    assert files.get_files == [] and files.downloads == [] and gateway.asked == []
    assert files.sent == [(OWNER, words("en", "file_large", mb="10"))]


async def test_a_pdf_may_be_larger_than_an_image_but_not_without_bound(files, files_http):
    files.message(
        OWNER,
        None,
        document={"file_id": "d", "mime_type": "application/pdf", "file_size": MAX_PDF_BYTES + 1},
    )
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    assert files.get_files == [] and gateway.asked == []
    assert files.sent == [(OWNER, words("en", "file_large", mb="20"))]


async def test_a_file_that_is_not_what_its_type_says_is_refused(files, files_http):
    files.files["fake"] = b"MZ\x90\x00 an executable"
    files.message(OWNER, None, document={"file_id": "fake", "file_name": "cat.png", "mime_type": "image/png"})
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    assert files.downloads == ["fake"] and gateway.asked == []
    assert files.sent == [(OWNER, words("en", "unsupported"))]


async def test_a_file_the_bot_api_reports_as_larger_than_the_message_said_is_not_downloaded(
    files, files_http
):
    files.files["p-large"] = JPEG + b"\x00" * MAX_IMAGE_BYTES  # getFile tells its real size
    files.message(OWNER, None, photo=photo_sizes(size=10))
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    assert files.get_files == ["p-large"] and files.downloads == [] and gateway.asked == []
    assert files.sent == [(OWNER, words("en", "file_large", mb="10"))]


async def test_a_strangers_photo_gets_a_code_and_is_not_fetched(files, files_http):
    files.files["p-large"] = JPEG
    files.message(555, None, photo=photo_sizes(), caption="let me in")
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    assert files.get_files == [] and gateway.asked == []
    assert gateway.pairing.pending()[0][0].code in files.sent[0][1]


# --- albums ---


async def test_an_album_is_one_request_with_its_caption(files, files_http):
    files.files["a-large"], files.files["b-large"] = JPEG + b"a", JPEG + b"b"
    files.message(OWNER, None, photo=photo_sizes("a"), media_group_id="g1")
    files.message(OWNER, None, photo=photo_sizes("b"), media_group_id="g1", caption="compare these")
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    await asyncio.sleep(0.1)
    [request] = gateway.asked
    assert request["text"] == "compare these"
    assert [f.data[-1:] for f in request["files"]] == [b"a", b"b"]  # in the order they were sent
    assert files.sent == [(OWNER, "《答》好")]


async def test_an_album_of_more_than_four_files_asks_about_the_first_four(files, files_http):
    for n in range(6):
        files.files[f"{n}-large"] = JPEG
        files.message(OWNER, None, photo=photo_sizes(str(n)), media_group_id="g2")
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    [request] = gateway.asked
    assert len(request["files"]) == MAX_ATTACHMENTS
    assert files.downloads == [f"{n}-large" for n in range(MAX_ATTACHMENTS)]
    note = words("en", "files_first", count=str(MAX_ATTACHMENTS))
    assert files.sent == [(OWNER, f"{note}\n\n《答》好")]


async def test_an_album_of_videos_gets_one_unsupported_reply(files, files_http):
    files.message(OWNER, None, video={"file_id": "v1"}, media_group_id="g3")
    files.message(OWNER, None, video={"file_id": "v2"}, media_group_id="g3")
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: files.sent)
    await asyncio.sleep(0.1)
    assert gateway.asked == [] and files.sent == [(OWNER, words("en", "unsupported"))]


async def test_two_albums_are_two_requests(files, files_http):
    files.files["a-large"], files.files["b-large"] = JPEG, JPEG
    files.message(OWNER, None, photo=photo_sizes("a"), media_group_id="one")
    files.message(OWNER, None, photo=photo_sizes("b"), media_group_id="two")
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: len(files.sent) == 2)
    assert [len(r["files"]) for r in gateway.asked] == [1, 1]


class FileChannel(FakeChannel):
    """The channel of test_switchboard, serving the bytes of its attachments."""

    def __init__(self, served):
        super().__init__()
        self.served = served

    async def fetch(self, attachment, limit):
        return self.served[attachment.ref]


async def test_an_album_that_arrives_over_two_polls_is_still_one_request():
    channel = FileChannel({"a": JPEG, "b": JPEG})
    asked = []

    async def ask(session, text, language, source, /, *, attachments=()):
        asked.append((text, len(attachments)))
        return "ok"

    async def stop(session):
        return False

    board = Switchboard(
        channel,
        ask=ask,
        stop=stop,
        is_allowed=lambda user: user == "U1",
        chat_allowed=lambda chat: True,
        language=lambda: "en",
        album_wait=0.1,
    )

    def item(ref, caption=None):
        return Incoming(
            chat="D1",
            user="U1",
            attachments=[Attachment("image", ref, "image/jpeg", len(JPEG), "photo.jpg")],
            caption=caption,
            group="g",
        )

    task = asyncio.ensure_future(board.run())
    try:
        await channel.inbox.put(item("a", "two photos"))
        await asyncio.sleep(0.06)  # less than the wait: the album is not over
        assert asked == []
        await channel.inbox.put(item("b"))
        await settle(lambda: channel.sent)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert asked == [("two photos", 2)]


# --- the log ---


async def test_no_caption_file_name_or_file_id_reaches_the_log(files, files_http, caplog):
    caplog.set_level(logging.DEBUG)
    files.files["secret-file-id"] = PDF
    files.message(
        OWNER,
        None,
        document={"file_id": "secret-file-id", "file_name": "my-diagnosis.pdf", "mime_type": "application/pdf"},
        caption="my private caption",
    )
    files.message(OWNER, None, document={"file_id": "other-id", "file_name": "taxes.zip", "mime_type": "application/zip"})
    gateway = gateway_for(files, files_http)
    await run_until(gateway, lambda: len(files.sent) == 2)
    logged = caplog.text
    for private in ("my private caption", "my-diagnosis", "secret-file-id", "taxes.zip", "other-id", TOKEN):
        assert private not in logged
