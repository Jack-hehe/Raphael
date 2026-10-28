"""Media previews use the safe fetcher and return the shared message contract."""

import pytest

from raphael_core.services.fetch import Fetched
from raphael_core.services.media import preview


class Fetcher:
    def __init__(self, kind="text/html", body=b"", url=None):
        self.kind, self.body, self.url = kind, body, url
        self.calls = []

    async def fetch(self, url):
        self.calls.append(url)
        return Fetched(self.url or url, self.kind, "utf-8", self.body)

    async def public_url(self, url):
        return url


@pytest.mark.parametrize(("url", "kind", "expected", "embed"), [
    ("https://www.youtube.com/watch?v=abc123_de-4", "text/html", "video", "https://www.youtube-nocookie.com/embed/abc123_de-4"),
    ("https://example.org/read.pdf", "application/pdf", "pdf", None),
    ("https://example.org/photo", "image/png", "image", None),
    ("https://example.org/news", "text/html", "link", None),
])
async def test_media_types_and_contract(url, kind, expected, embed):
    fetcher = Fetcher(kind)
    result = await preview(url, fetcher)
    assert result["type"] == expected and result["embed"] == embed
    assert result["url"] == url and fetcher.calls == [url]
    assert set(result) == {"type", "url", "title", "site", "description", "thumbnail", "embed"}


async def test_open_graph_metadata_resolves_safe_relative_thumbnails():
    page = b'<title>Fallback</title><meta property="og:title" content="News &amp; analysis"><meta property="og:site_name" content="NHK"><meta name="description" content="Today"><meta property="og:image" content="/photo.jpg">'
    result = await preview("https://news.example/article", Fetcher(body=page))
    assert (result["title"], result["site"], result["description"]) == ("News & analysis", "NHK", "Today")
    assert result["thumbnail"] == "https://news.example/photo.jpg"


@pytest.mark.parametrize("thumbnail", ["http://127.0.0.1/a.png", "file:///private", "javascript:alert(1)", "http://local.example/a.png"])
async def test_a_thumbnail_cannot_reach_a_local_address(thumbnail):
    from .test_fetch import Site, fetcher, page, PUBLIC
    site = Site({(PUBLIC, "/article"): page(f'<meta property="og:image" content="{thumbnail}">')})
    result = await preview("https://example.com/article", fetcher(site, {"example.com": [PUBLIC], "local.example": ["192.168.1.1"]}, media=True))
    assert result["thumbnail"] is None
    assert len(site.requests) == 1


async def test_media_fetches_images_with_the_existing_size_and_redirect_guards():
    import httpx
    from .test_fetch import Site, fetcher, PUBLIC, kind_of
    site = Site({(PUBLIC, "/image"): httpx.Response(200, content=b"image", headers={"content-type": "image/png"}),
                 (PUBLIC, "/redirect"): httpx.Response(302, headers={"location": "http://127.0.0.1/image"})})
    assert (await preview("https://example.com/image", fetcher(site, media=True)))["type"] == "image"
    assert await kind_of(fetcher(site, media=True, max_bytes=2).fetch("https://example.com/image")) == "too_large"
    assert await kind_of(fetcher(site, media=True).fetch("https://example.com/redirect")) == "blocked_address"


@pytest.mark.parametrize("url", ["https://youtube.com.evil.example/watch?v=abc123_de-4", "https://example.com/watch?v=abc123_de-4", "https://youtube.com/watch?v=invalid"])
async def test_only_a_valid_youtube_address_gets_an_embed(url):
    assert (await preview(url, Fetcher()))["embed"] is None
