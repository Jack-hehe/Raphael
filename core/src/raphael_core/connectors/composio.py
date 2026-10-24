"""Platform v3.1 only; injectable HTTP, pagination and version negotiation seams."""

import time
import re
from collections.abc import Callable
from urllib.parse import quote, urlsplit

from .http import ConnectorFailure, PrivateHttp

KEY = "connectors:composio:key"
BASE = "https://backend.composio.dev/api/v3.1"
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*composio\.dev")


def trusted_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if (parts.scheme == "https" and _HOST.fullmatch(host)
                and parts.username is None and parts.password is None and not parts.fragment
                and parts.port in (None, 443) and not any(ord(c) <= 32 for c in value)):
            return value
    except (ValueError, TypeError):
        pass
    raise ConnectorFailure("untrusted_url")


def segment(value: str) -> str:
    return quote(value, safe="")


class ComposioClient:
    def __init__(self, key: Callable[[], str | None], *, transport=None, now=time.monotonic,
                 expected_version: bool = True) -> None:
        self._key = key
        self.http = PrivateHttp(transport=transport)
        self.now = now
        # [U] Prose documents this field; OpenAPI omits it. Never silently downgrade on conflict.
        self.expected_version = expected_version
        self._cache: dict[tuple, tuple[float, dict]] = {}

    async def request(self, method: str, path: str, **kwargs) -> dict:
        key = self._key()
        if not key:
            raise ConnectorFailure("missing_key")
        return await self.http.json(method, BASE + path, headers={"x-api-key": key}, **kwargs)

    async def page(self, path: str, params: dict | None = None, *, cache: bool = False) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        cache_key = (path, tuple(sorted(params.items())))
        before = self._cache.get(cache_key)
        if cache and before and self.now() - before[0] < 600:
            return before[1]
        data = await self.request("GET", path, params=params)
        if not isinstance(data.get("items"), list):
            raise ConnectorFailure("protocol")
        if cache:
            self._cache[cache_key] = (self.now(), data)
        return data

    async def pages(self, path: str, params: dict | None = None) -> list[dict]:
        result, seen = [], set()
        params = dict(params or {})
        previous_page = None
        for _ in range(100):
            data = await self.page(path, params)
            result.extend(data["items"])
            cursor = data.get("next_cursor")
            page = data.get("current_page")
            if not cursor:
                return result
            if cursor in seen or (page is not None and previous_page is not None and page <= previous_page):
                raise ConnectorFailure("pagination")
            seen.add(cursor)
            previous_page = page
            params["cursor"] = cursor
        raise ConnectorFailure("pagination")

    async def patch(self, session_id: str, config: dict, version: int | None) -> dict:
        body = dict(config)
        if version is not None and self.expected_version:
            body["expected_config_version"] = version
        return await self.request("PATCH", f"/tool_router/session/{segment(session_id)}", json=body)
