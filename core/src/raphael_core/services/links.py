"""Lazy chat previews: bounded public fetches, isolated cookies and a small one-day cache."""

import asyncio
import base64
from collections import OrderedDict
from time import monotonic

from ..protocol import invalid_params
from .common import ServiceError
from .fetch import PageFetcher, checked_url
from .media import preview

DAY = 86400
MAX_ENTRIES = 64
THUMBNAIL_BYTES = 256 * 1024


class LinkPreviews:
    def __init__(self, fetcher: PageFetcher, thumbnails: PageFetcher):
        self.fetcher = fetcher
        self.thumbnails = thumbnails
        self._cache: OrderedDict[str, tuple[float, dict | None]] = OrderedDict()
        self._pending: dict[str, asyncio.Task] = {}
        self._slots = asyncio.Semaphore(4)

    async def handle(self, params: dict) -> dict | None:
        raw = params.get("url")
        if not isinstance(raw, str) or len(raw) > 8192:
            raise invalid_params("url must be an http(s) address of at most 8192 characters")
        try:
            url = str(checked_url(raw).copy_with(fragment=None))
        except ServiceError:
            raise invalid_params("url must be an http(s) address without credentials") from None
        cached = self._cache.get(url)
        if cached and cached[0] > monotonic():
            self._cache.move_to_end(url)
            return cached[1]
        task = self._pending.get(url)
        if task is None:
            if len(self._pending) >= MAX_ENTRIES:
                return None
            task = asyncio.create_task(self._load(url))
            self._pending[url] = task
        return await asyncio.shield(task)

    async def _load(self, url: str) -> dict | None:
        result = None
        try:
            async with self._slots:
                # One overall budget also bounds the extra image fetch and metadata DNS check.
                async with asyncio.timeout(20):
                    data = await preview(url, self.fetcher)
                    thumbnail = None
                    if data["thumbnail"]:
                        try:
                            image = await self.thumbnails.fetch(data["thumbnail"])
                            if image.content_type in ("image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"):
                                thumbnail = f"data:{image.content_type};base64,{base64.b64encode(image.body).decode('ascii')}"
                        except ServiceError:
                            pass
                    result = {key: data[key] for key in ("url", "site", "title", "description")}
                    result["thumbnail"] = thumbnail
        except (ServiceError, TimeoutError):
            pass  # A failed preview leaves the original clickable URL; no address is logged.
        finally:
            self._pending.pop(url, None)
        self._cache[url] = (monotonic() + (DAY if result else 300), result)
        self._cache.move_to_end(url)
        while len(self._cache) > MAX_ENTRIES:
            self._cache.popitem(last=False)
        return result

    async def aclose(self) -> None:
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._cache.clear()
