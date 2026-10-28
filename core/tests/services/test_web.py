"""The web's services: each search engine and each page reader, as httpx MockTransport fakes of
their APIs, and the budget a page's text is held to. No test reaches the network."""

import json

import httpx
import pytest

from raphael_core.services import pdf, web
from raphael_core.services.common import TEXT_BUDGET, ServiceError, fit
from raphael_core.services.fetch import PageFetcher

from .pdfs import make_pdf

KEY = "test-service-key-1234"
PUBLIC = "93.184.216.34"


class Api:
    """A fake of the services' APIs: an answer for each host, and every request kept."""

    def __init__(self, answers):
        self.answers = answers
        self.requests: list[httpx.Request] = []
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        self.requests.append(request)
        answer = self.answers[request.url.host]
        return answer(request) if callable(answer) else answer


async def kind_of(awaitable):
    with pytest.raises(ServiceError) as caught:
        await awaitable
    return caught.value.kind


# --- the budget ---------------------------------------------------------------------------------


def test_a_text_within_the_budget_is_kept_whole():
    assert fit("short") == ("short", False)
    text = "x" * TEXT_BUDGET
    assert fit(text) == (text, False)


def test_a_longer_text_keeps_three_quarters_of_its_head_a_quarter_of_its_tail_and_says_so():
    text = "H" * 20_000 + "M" * 10_000 + "T" * 20_000
    kept, cut = fit(text)
    assert cut
    head, rest = kept.split("\n\n[…]\n\n")
    tail, footer = rest.rsplit("\n\n", 1)
    assert head == "H" * 11_250 and tail == "T" * 3_750
    assert footer == "[Cut short: the text had 50000 characters; 35000 in the middle were left out.]"


# --- the search ---------------------------------------------------------------------------------


async def test_brave_is_asked_with_its_key_in_its_header_and_its_results_are_read():
    api = Api(
        {
            "api.search.brave.com": httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {"title": "<strong>Raphael</strong> app", "url": "https://a.example/1",
                             "description": "The <strong>best</strong> app."},
                            {"title": "No address", "url": "javascript:alert(1)"},
                            {"title": "Two", "url": "https://b.example/2", "description": "B."},
                        ]
                    }
                },
            )
        }
    )  # fmt: skip
    found = await web.search("brave", "raphael app", http=api.http, key=KEY)
    assert found == {
        "results": [
            {"title": "Raphael app", "url": "https://a.example/1", "snippet": "The best app."},
            {"title": "Two", "url": "https://b.example/2", "snippet": "B."},
        ],
        "sources": [
            {"title": "Raphael app", "url": "https://a.example/1"},
            {"title": "Two", "url": "https://b.example/2"},
        ],
    }
    (request,) = api.requests
    assert request.headers["x-subscription-token"] == KEY
    assert request.url.params["q"] == "raphael app" and request.url.params["count"] == "5"


async def test_tavily_gives_its_own_answer_besides_the_results():
    def answer(request):
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert json.loads(request.content)["query"] == "q"
        return httpx.Response(
            200,
            json={"answer": "Forty-two.", "results": [{"title": "T", "url": "https://t.example/", "content": "c"}]},
        )

    found = await web.search("tavily", "q", http=Api({"api.tavily.com": answer}).http, key=KEY)
    assert found["answer"] == "Forty-two."
    assert found["results"] == [{"title": "T", "url": "https://t.example/", "snippet": "c"}]


async def test_exa_takes_its_key_as_x_api_key_and_its_text_or_highlights_as_the_snippet():
    def answer(request):
        assert request.headers["x-api-key"] == KEY
        return httpx.Response(
            200,
            json={"results": [
                {"title": "E", "url": "https://e.example/", "highlights": ["one.", "two."]},
                {"title": "F", "url": "https://f.example/", "text": "the text"},
            ]},
        )  # fmt: skip

    found = await web.search("exa", "q", http=Api({"api.exa.ai": answer}).http, key=KEY)
    assert [r["snippet"] for r in found["results"]] == ["one. two.", "the text"]


async def test_searxng_is_the_owners_own_address_and_takes_no_key():
    def answer(request):
        assert request.url.path == "/search" and request.url.params["format"] == "json"
        assert "authorization" not in request.headers
        return httpx.Response(
            200, json={"results": [{"title": "S", "url": "https://s.example/", "content": "s"}]}
        )

    api = Api({"searx.local": answer})
    found = await web.search("searxng", "q", http=api.http, searxng_url="http://searx.local/")
    assert found["results"][0]["url"] == "https://s.example/"
    assert await kind_of(web.search("searxng", "q", http=api.http, searxng_url="")) == "not_configured"


@pytest.mark.parametrize("service", ["brave", "tavily", "exa"])
async def test_a_keyed_search_without_a_key_asks_nothing(service):
    api = Api({})
    assert await kind_of(web.search(service, "q", http=api.http, key=None)) == "missing_secret"
    assert api.requests == []


@pytest.mark.parametrize(
    ("status", "kind"), [(401, "auth"), (403, "auth"), (402, "credits"), (429, "rate_limit"), (500, "http_error")]
)
async def test_a_service_that_refuses_is_a_failure_of_a_kind(status, kind):
    api = Api({"api.search.brave.com": httpx.Response(status, text="refused: test-service-key-1234")})
    assert await kind_of(web.search("brave", "q", http=api.http, key=KEY)) == kind


async def test_a_service_that_cannot_be_reached_or_answers_nonsense_is_a_failure():
    def down(request):
        raise httpx.ConnectError("down", request=request)

    assert await kind_of(web.search("brave", "q", http=Api({"api.search.brave.com": down}).http, key=KEY)) == "network"
    nonsense = Api({"api.search.brave.com": httpx.Response(200, text="<html>")})
    assert await kind_of(web.search("brave", "q", http=nonsense.http, key=KEY)) == "bad_response"


# --- reading a page -----------------------------------------------------------------------------


def page_fetcher(pages):
    async def resolve(host, port):
        return [PUBLIC]

    def handle(request):
        return pages[request.url.path]

    return PageFetcher(resolver=resolve, transport=httpx.MockTransport(handle))


async def test_the_builtin_reader_turns_a_page_into_its_title_and_text():
    fetcher = page_fetcher(
        {"/": httpx.Response(200, html="<title>T</title><script>x</script><p>Body text.</p>")}
    )
    found = await web.read(
        "builtin", "https://example.com/", http=Api({}).http, fetcher=fetcher, read_pdf=pdf.read_pdf
    )
    assert found == {"title": "T", "text": "Body text.", "truncated": False}


async def test_the_builtin_reader_holds_a_long_page_to_the_budget():
    body = "<p>" + "word " * 10_000 + "</p>"
    fetcher = page_fetcher({"/": httpx.Response(200, html=body)})
    found = await web.read(
        "builtin", "https://example.com/", http=Api({}).http, fetcher=fetcher, read_pdf=pdf.read_pdf
    )
    assert found["truncated"] and len(found["text"]) < TEXT_BUDGET + 200
    assert found["text"].endswith("were left out.]")


async def test_the_builtin_reader_reads_a_pdf_it_is_led_to_as_a_pdf():
    document = make_pdf(["The text layer of the document."])
    fetcher = page_fetcher(
        {"/d.pdf": httpx.Response(200, content=document, headers={"content-type": "application/pdf"})}
    )
    found = await web.read(
        "builtin", "https://example.com/d.pdf", http=Api({}).http, fetcher=fetcher,
        read_pdf=pdf.read_pdf,
    )  # fmt: skip
    assert "The text layer of the document." in found["text"]


async def test_jina_reads_a_page_with_or_without_a_key():
    def answer(request):
        assert request.url.path == "/https://example.com/a"
        return httpx.Response(200, json={"data": {"title": "J", "content": "Jina's text"}})

    api = Api({"r.jina.ai": answer})
    found = await web.read(
        "jina", "https://example.com/a", http=api.http, fetcher=page_fetcher({}), read_pdf=pdf.read_pdf
    )
    assert found == {"title": "J", "text": "Jina's text", "truncated": False}
    assert "authorization" not in api.requests[0].headers
    await web.read(
        "jina", "https://example.com/a", http=api.http, fetcher=page_fetcher({}),
        read_pdf=pdf.read_pdf, key=KEY,
    )  # fmt: skip
    assert api.requests[1].headers["authorization"] == f"Bearer {KEY}"


async def test_firecrawl_and_tavily_read_a_page_with_their_keys():
    def firecrawl(request):
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert json.loads(request.content)["url"] == "https://example.com/a"
        return httpx.Response(200, json={"success": True, "data": {"markdown": "# F", "metadata": {"title": "F"}}})

    def tavily(request):
        assert json.loads(request.content) == {"urls": ["https://example.com/a"]}
        return httpx.Response(200, json={"results": [{"url": "https://example.com/a", "raw_content": "raw"}]})

    api = Api({"api.firecrawl.dev": firecrawl, "api.tavily.com": tavily})
    kwargs = {"http": api.http, "fetcher": page_fetcher({}), "read_pdf": pdf.read_pdf, "key": KEY}
    assert (await web.read("firecrawl", "https://example.com/a", **kwargs))["text"] == "# F"
    assert (await web.read("tavily", "https://example.com/a", **kwargs))["text"] == "raw"
    kwargs["key"] = None
    assert await kind_of(web.read("firecrawl", "https://example.com/a", **kwargs)) == "missing_secret"


@pytest.mark.parametrize("service", ["builtin", "jina", "firecrawl", "tavily"])
async def test_no_reader_takes_an_address_that_is_not_http(service):
    api = Api({})
    found = web.read(
        service, "file:///C:/secret.txt", http=api.http, fetcher=page_fetcher({}),
        read_pdf=pdf.read_pdf, key=KEY,
    )  # fmt: skip
    assert await kind_of(found) == "bad_url"
    assert api.requests == []
