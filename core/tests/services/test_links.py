"""The chat preview RPC uses bounded public fetches and never the browser's cookies."""

import asyncio

import httpx
import pytest

from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router
from raphael_core.services.hub import ServiceHub
from raphael_core.services.keys import memory_keys
from .test_fetch import PUBLIC, Site, page, resolver_of


def rpc(conn, site, table=None):
    hub = ServiceHub(conn, object(), memory_keys(), transport=httpx.MockTransport(site.handle),
                     resolver=resolver_of(table or {"example.com": [PUBLIC]}))
    router = Router()
    hub.register(router)
    return router


async def test_preview_rpc_metadata_safe_thumbnail_and_one_day_cache(conn, monkeypatch):
    import raphael_core.services.links as links
    now = [100.0]
    monkeypatch.setattr(links, "monotonic", lambda: now[0])
    site = Site({(PUBLIC, "/a"): page('<title>Fallback</title><meta property="og:title" content="News &amp; weather"><meta property="og:site_name" content="Weather"><meta name="description" content="Today"><meta property="og:image" content="/image">'),
                 (PUBLIC, "/image"): httpx.Response(200, content=b"png", headers={"content-type": "image/png"})})
    router = rpc(conn, site)
    result, same = await asyncio.gather(*(router.call("links.preview", {"url": "https://example.com/a"}) for _ in range(2)))
    assert result == same
    assert result == {"url": "https://example.com/a", "title": "News & weather", "site": "Weather", "description": "Today", "thumbnail": "data:image/png;base64,cG5n"}
    assert len(site.requests) == 2
    now[0] += 86399
    assert await router.call("links.preview", {"url": "https://example.com/a"}) == result
    assert len(site.requests) == 2
    now[0] += 2
    await router.call("links.preview", {"url": "https://example.com/a"})
    assert len(site.requests) == 4
    assert all("cookie" not in r.headers and "authorization" not in r.headers for r in site.requests)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "https://inside.example/", "https://example.com/private"])
async def test_preview_blocks_private_addresses_and_redirects(conn, url):
    site = Site({(PUBLIC, "/private"): httpx.Response(302, headers={"location": "http://127.0.0.1/"})})
    router = rpc(conn, site, {"example.com": [PUBLIC], "inside.example": ["10.0.0.1"]})
    assert await router.call("links.preview", {"url": url}) is None
    assert len(site.requests) == (1 if url.endswith("/private") else 0)


@pytest.mark.parametrize("url", [None, 42, "javascript:alert(1)", "https://user:password@example.com", "x" * 8193])
async def test_preview_validates_rpc_params(conn, url):
    with pytest.raises(RpcError) as caught:
        await rpc(conn, Site({})).call("links.preview", {"url": url})
    assert caught.value.code == INVALID_PARAMS


async def test_thumbnail_redirects_cookies_and_size_cap(conn):
    site = Site({(PUBLIC, "/a"): page('<title>Article</title><meta property="og:image" content="/go">', **{"set-cookie": "secret=yes"}),
                 (PUBLIC, "/go"): httpx.Response(302, headers={"location": "/huge", "set-cookie": "secret=yes"}),
                 (PUBLIC, "/huge"): httpx.Response(200, content=b"x", headers={"content-type": "image/png", "content-length": "300000"})})
    result = await rpc(conn, site).call("links.preview", {"url": "https://example.com/a"})
    assert result["title"] == "Article" and result["thumbnail"] is None
    assert all("cookie" not in r.headers for r in site.requests)


async def test_preview_enforces_whole_request_timeout_and_page_size(conn, monkeypatch):
    from raphael_core.services.fetch import PageFetcher
    async def slow(request):
        await asyncio.sleep(0.1)
        return page("late")
    hub = ServiceHub(conn, object(), memory_keys())
    hub.links.fetcher = PageFetcher(resolver=resolver_of({"example.com": [PUBLIC]}), transport=httpx.MockTransport(slow), max_seconds=0.001)
    router = Router(); hub.register(router)
    assert await router.call("links.preview", {"url": "https://example.com/slow"}) is None
    site = Site({(PUBLIC, "/large"): page("x", **{"content-length": "4000000"})})
    assert await rpc(conn, site).call("links.preview", {"url": "https://example.com/large"}) is None
