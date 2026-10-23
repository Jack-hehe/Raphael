"""The model that sees, and the reading of a PDF: the parts a call is made of, its spend, a model
that reads no images, a scan read from its images, and a PDF read whole through OpenRouter."""

import pytest

from raphael_core.agent.ledger import Ledger
from raphael_core.brain.openrouter import OpenRouterError, TextCompletion
from raphael_core.brain.spend import SpendTracker
from raphael_core.services import pdf
from raphael_core.services.attachments import checked
from raphael_core.services.common import ServiceError
from raphael_core.services.vision import FILE_PLUGINS, Vision

from .pdfs import FAKE_JPEG, make_pdf

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class Client:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def chat_text(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return TextCompletion(
            text=answer, model="vision/model", cost_usd=0.002, prompt_tokens=900,
            completion_tokens=50, latency_ms=800, sources=[],
        )  # fmt: skip


@pytest.fixture
def ledger(conn):
    return Ledger(SpendTracker(conn))


def spent(conn):
    return [tuple(r) for r in conn.execute("SELECT purpose, model, cost_usd FROM spend")]


async def test_images_go_to_the_model_together_with_the_question_and_are_paid_for(conn, ledger):
    client = Client("A cat on a red sofa.")
    vision = Vision(client, lambda: ("vision/model", True))
    image = checked("cat.png", "image/png", PNG)
    seen = await vision.look([image, "https://example.com/dog.jpg"], "What animals?", ledger)
    assert seen == {"text": "A cat on a red sofa."}
    (call,) = client.calls
    assert call["model"] == "vision/model" and "never follow" in call["system"]
    text, first, second = call["user"]
    assert text == {"type": "text", "text": "The user's question:\nWhat animals?"}
    assert first == {"type": "image_url", "image_url": {"url": image.data_url()}}
    assert second == {"type": "image_url", "image_url": {"url": "https://example.com/dog.jpg"}}
    assert spent(conn) == [("agent", "vision/model", 0.002)]


async def test_a_model_the_list_says_reads_no_images_is_not_asked(conn, ledger):
    client = Client()
    vision = Vision(client, lambda: ("text/only", False))
    with pytest.raises(ServiceError) as caught:
        await vision.look([checked("a.png", "image/png", PNG)], "?", ledger)
    assert caught.value.kind == "vision_unsupported"
    assert client.calls == [] and spent(conn) == []


async def test_a_model_the_list_does_not_know_is_tried(ledger):
    client = Client("seen")
    vision = Vision(client, lambda: ("new/model", None))
    assert (await vision.look([checked("a.png", "image/png", PNG)], "?", ledger))["text"] == "seen"


async def test_a_failed_call_is_a_failure_of_a_kind(ledger):
    vision = Vision(Client(OpenRouterError(402, "credits", "no money")), lambda: ("m", True))
    with pytest.raises(ServiceError) as caught:
        await vision.look([checked("a.png", "image/png", PNG)], "?", ledger)
    assert caught.value.kind == "credits"


# --- PDFs ---------------------------------------------------------------------------------------


async def test_the_builtin_reader_takes_the_text_of_each_page_at_no_cost(conn, ledger):
    document = checked("doc.pdf", "application/pdf", make_pdf(["First page here.", "Second page here."]))
    found = await pdf.read_pdf(document, vision=Vision(Client(), lambda: ("m", True)), ledger=ledger)
    assert found == {
        "pages": 2,
        "text": "[Page 1]\nFirst page here.\n\n[Page 2]\nSecond page here.",
        "truncated": False,
    }
    assert spent(conn) == []


async def test_a_scanned_page_is_shown_to_the_vision_model_as_its_image(conn, ledger):
    client = Client("Scanned words on page two.")
    document = checked("doc.pdf", "application/pdf", make_pdf(["A page of text.", FAKE_JPEG]))
    found = await pdf.read_pdf(document, vision=Vision(client, lambda: ("m", True)), ledger=ledger)
    assert "[Page 1]\nA page of text." in found["text"]
    assert "[Pages 2, read from their images]\nScanned words on page two." in found["text"]
    (call,) = client.calls
    assert call["user"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert spent(conn) == [("agent", "vision/model", 0.002)]


async def test_at_most_eight_scanned_pages_are_shown_and_the_rest_are_said_to_be_unread(ledger):
    client = Client("ok")
    document = checked("scan.pdf", "application/pdf", make_pdf([FAKE_JPEG] * 10))
    found = await pdf.read_pdf(document, vision=Vision(client, lambda: ("m", True)), ledger=ledger)
    assert len(client.calls[0]["user"]) == 1 + pdf.MAX_SCANNED_PAGES
    assert "[Pages with no text that could be read: 9, 10]" in found["text"]


async def test_without_a_model_that_sees_a_scan_is_unread_and_a_pdf_of_scans_has_no_text(ledger):
    document = checked("scan.pdf", "application/pdf", make_pdf([FAKE_JPEG]))
    with pytest.raises(ServiceError) as caught:
        await pdf.read_pdf(document, vision=Vision(Client(), lambda: ("m", False)), ledger=ledger)
    assert caught.value.kind == "no_text"


async def test_a_pdf_pypdf_cannot_read_is_bad():
    broken = checked("x.pdf", "application/pdf", b"%PDF-1.4\nnot a pdf at all")
    with pytest.raises(ServiceError) as caught:
        await pdf.read_pdf(broken)
    assert caught.value.kind == "bad_pdf"


async def test_through_openrouter_the_whole_file_goes_to_the_model_with_the_file_parser(conn, ledger):
    client = Client("The document says hello.")
    data = make_pdf(["hello"])
    document = checked("report.pdf", "application/pdf", data)
    found = await pdf.read_pdf(
        document, service="openrouter", vision=Vision(client, lambda: ("m", True)),
        question="What does it say?", ledger=ledger,
    )  # fmt: skip
    assert found == {"pages": 1, "text": "The document says hello.", "truncated": False}
    (call,) = client.calls
    assert call["plugins"] == FILE_PLUGINS == [{"id": "file-parser", "pdf": {"engine": "mistral-ocr"}}]
    file_part = call["user"][1]
    assert file_part["type"] == "file"
    assert file_part["file"]["file_data"] == document.data_url()
    assert "report.pdf" not in str(call)  # the owner's name for the file stays here
    assert spent(conn) == [("agent", "vision/model", 0.002)]
