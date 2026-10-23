"""The built-in page reader's fetch: only public addresses, every redirect checked, a time and a
size it may not pass, no cookies, and only pages, plain text and PDFs. No test reaches the network:
the transport is httpx's MockTransport and the resolver a dictionary."""

import asyncio

import httpx
import pytest

from raphael_core.services.common import ServiceError
from raphael_core.services.fetch import PageFetcher, decoded, is_public
from raphael_core.services.html import html_to_text

PUBLIC = "93.184.216.34"


def resolver_of(table):
    async def resolve(host, port):
        if host not in table:
            raise OSError("no such host")
        return list(table[host])

    return resolve


class Site:
    """A web of fake pages, by the address the request is sent to (the pinned IP) and its path."""

    def __init__(self, pages):
        self.pages = pages
        self.requests: list[httpx.Request] = []

    def handle(self, request):
        self.requests.append(request)
        answer = self.pages[(request.url.host, request.url.path)]
        return answer(request) if callable(answer) else answer


def fetcher(site, table=None, **kwargs):
    table = {"example.com": [PUBLIC]} if table is None else table
    return PageFetcher(
        resolver=resolver_of(table), transport=httpx.MockTransport(site.handle), **kwargs
    )


def page(html, status=200, **headers):
    return httpx.Response(
        status, content=html.encode(), headers={"content-type": "text/html; charset=utf-8", **headers}
    )


async def kind_of(awaitable):
    with pytest.raises(ServiceError) as caught:
        await awaitable
    return caught.value.kind


async def test_a_page_is_fetched_from_the_address_that_was_checked_with_the_host_named():
    site = Site({(PUBLIC, "/a"): page("<p>hi</p>")})
    found = await fetcher(site).fetch("https://example.com/a")
    assert found.content_type == "text/html" and decoded(found) == "<p>hi</p>"
    (request,) = site.requests
    assert request.url.host == PUBLIC  # pinned: no second resolution can change where it goes
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"
    assert "cookie" not in request.headers and "authorization" not in request.headers


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://[::1]/", "http://10.0.0.5/", "http://169.254.169.254/latest",
     "http://192.168.1.1/", "http://0.0.0.0/", "http://224.0.0.1/", "http://[::ffff:127.0.0.1]/",
     "http://100.64.0.1/"],
)  # fmt: skip
async def test_an_address_of_this_computer_or_the_local_network_is_refused(url):
    site = Site({})
    assert await kind_of(fetcher(site).fetch(url)) == "blocked_address"
    assert site.requests == []


async def test_a_name_that_resolves_to_a_private_address_is_refused():
    site = Site({})
    table = {"intranet.example": ["10.1.2.3"], "mixed.example": [PUBLIC, "127.0.0.1"]}
    assert await kind_of(fetcher(site, table).fetch("https://intranet.example/")) == "blocked_address"
    assert await kind_of(fetcher(site, table).fetch("https://mixed.example/")) == "blocked_address"
    assert site.requests == []


async def test_a_redirect_to_this_computer_is_refused_before_it_is_followed():
    site = Site({(PUBLIC, "/go"): httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"})})
    assert await kind_of(fetcher(site).fetch("https://example.com/go")) == "blocked_address"
    assert [r.url.host for r in site.requests] == [PUBLIC]


async def test_a_redirect_to_a_name_that_resolves_privately_is_refused():
    site = Site({(PUBLIC, "/go"): httpx.Response(301, headers={"location": "https://inside.example/"})})
    table = {"example.com": [PUBLIC], "inside.example": ["192.168.0.10"]}
    assert await kind_of(fetcher(site, table).fetch("https://example.com/go")) == "blocked_address"


async def test_a_public_redirect_is_followed_and_sends_no_cookie_it_set():
    site = Site(
        {
            (PUBLIC, "/go"): httpx.Response(
                302, headers={"location": "/there", "set-cookie": "session=abc; Path=/"}
            ),
            (PUBLIC, "/there"): page("<h1>There</h1>"),
        }
    )
    found = await fetcher(site).fetch("https://example.com/go")
    assert found.url == "https://example.com/there"
    assert "cookie" not in site.requests[1].headers


async def test_more_than_five_redirects_is_too_many():
    site = Site({(PUBLIC, "/loop"): httpx.Response(302, headers={"location": "/loop"})})
    assert await kind_of(fetcher(site).fetch("https://example.com/loop")) == "redirects"
    assert len(site.requests) == 6


@pytest.mark.parametrize(
    "url", ["ftp://example.com/", "file:///C:/Windows/win.ini", "https://user:pw@example.com/", "nonsense"]
)
async def test_only_http_and_https_without_credentials(url):
    assert await kind_of(fetcher(Site({})).fetch(url)) == "bad_url"


async def test_a_redirect_to_another_scheme_is_refused():
    site = Site({(PUBLIC, "/go"): httpx.Response(302, headers={"location": "file:///etc/passwd"})})
    assert await kind_of(fetcher(site).fetch("https://example.com/go")) == "bad_url"


async def test_a_body_larger_than_the_cap_is_refused_whether_it_says_so_or_not():
    big = httpx.Response(200, content=b"x" * 2000, headers={"content-type": "text/plain"})
    site = Site({(PUBLIC, "/big"): big})
    assert await kind_of(fetcher(site, max_bytes=1000).fetch("https://example.com/big")) == "too_large"

    async def stream():
        for _ in range(10):
            yield b"y" * 300

    def streamed(request):
        return httpx.Response(200, content=stream(), headers={"content-type": "text/plain"})

    site = Site({(PUBLIC, "/stream"): streamed})
    assert await kind_of(fetcher(site, max_bytes=1000).fetch("https://example.com/stream")) == "too_large"


@pytest.mark.parametrize("kind", ["application/octet-stream", "image/png", "application/json"])
async def test_only_pages_plain_text_and_pdfs_are_taken(kind):
    site = Site({(PUBLIC, "/f"): httpx.Response(200, content=b"x", headers={"content-type": kind})})
    assert await kind_of(fetcher(site).fetch("https://example.com/f")) == "unsupported_type"


async def test_a_pdf_is_brought_back_as_a_pdf():
    pdf = httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})
    found = await fetcher(Site({(PUBLIC, "/d.pdf"): pdf})).fetch("https://example.com/d.pdf")
    assert found.content_type == "application/pdf" and found.body == b"%PDF-1.4"


async def test_an_error_page_and_a_name_that_resolves_to_nothing_are_failures():
    site = Site({(PUBLIC, "/404"): page("gone", status=404)})
    assert await kind_of(fetcher(site).fetch("https://example.com/404")) == "http_error"
    assert await kind_of(fetcher(site).fetch("https://nowhere.example/")) == "network"


async def test_a_page_that_takes_too_long_is_given_up():
    async def slow(request):
        await asyncio.sleep(5)
        return page("late")

    site = Site({(PUBLIC, "/slow"): slow})
    assert await kind_of(fetcher(site, max_seconds=0.05).fetch("https://example.com/slow")) == "timeout"


def test_the_public_addresses():
    assert is_public(PUBLIC) and is_public("2606:4700::1111")
    for address in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "fe80::1", "fc00::1", "::",
                    "2002:7f00:1::1", "255.255.255.255", "nonsense"):  # fmt: skip
        assert not is_public(address), address


def test_the_charset_a_page_names_is_used_when_the_server_names_none():
    from raphael_core.services.fetch import Fetched

    html = '<meta charset="big5"><p>中文</p>'.encode("big5")
    assert "中文" in decoded(Fetched("https://x/", "text/html", None, html))


# --- the page as text ---------------------------------------------------------------------------


def test_a_page_becomes_its_title_headings_paragraphs_and_items_without_its_frame():
    html = """<html><head><title> The  Title </title><style>p{color:red}</style>
    <script>alert("x")</script></head><body>
    <nav><a href="/">Home</a> <a href="/about">About</a></nav>
    <h1>Main heading</h1><p>First   paragraph with a <a href="https://e.com/x">link's words</a>.</p>
    <h2>Second</h2><ul><li>one</li><li>two</li></ul>
    <noscript>enable js</noscript><svg><text>drawn</text></svg>
    <footer>© the footer</footer></body></html>"""
    title, text = html_to_text(html)
    assert title == "The Title"
    assert text.splitlines() == [
        "# Main heading",
        "First paragraph with a link's words.",
        "## Second",
        "- one",
        "- two",
    ]
    for gone in ("alert", "color", "Home", "About", "enable js", "drawn", "footer", "https://e.com"):
        assert gone not in text


def test_an_element_left_open_inside_a_skipped_one_does_not_swallow_the_page():
    _, text = html_to_text("<nav><svg><g>menu</nav><p>after</p>")
    assert text == "after"


def test_nhk_ruby_keeps_base_text_without_furigana_or_fallback_parentheses():
    title, text = html_to_text('''<title>台風のニュース</title>
    <p><ruby>弱点<rt>じゃくてん</rt></ruby>と
    <ruby>子吉<rp>（</rp><rt>こよし</rt><rp>）</rp></ruby><ruby>川<rt>がわ</rt></ruby>。
    <ruby>台風<rp>(</rp><rt><span>たいふう</span></rt><rp>)</rp></ruby>30<ruby>号<rt>ごう</rt></ruby>。</p>
    <p>次の段落。</p>''')
    assert title == "台風のニュース"
    assert text == "弱点と 子吉川。 台風30号。\n次の段落。"
