"""HTTP boundary with deliberately value-free failures and diagnostics."""

import logging
from typing import Any

import httpx


class ConnectorFailure(Exception):
    def __init__(self, kind: str, status: int = 0) -> None:
        self.kind = kind
        self.status = status
        super().__init__(kind)


class _PrivateRequests(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not any(host in record.getMessage() for host in (
            "composio.dev", "googleapis.com", "accounts.google.com"))


logging.getLogger("httpx").addFilter(_PrivateRequests())
# Debug transport traces may include authorization headers. Match the MCP boundary's policy.
logging.getLogger("httpcore").setLevel(logging.CRITICAL)


class PrivateHttp:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def response(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=20, follow_redirects=False) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException:
            raise ConnectorFailure("timeout") from None
        except Exception:
            raise ConnectorFailure("network") from None
        return response

    async def json(self, method: str, url: str, **kwargs: Any) -> dict:
        response = await self.response(method, url, **kwargs)
        if response.status_code >= 300:
            # Do not pass an upstream error message, URL, body or exception to the router.
            kind = {401: "auth", 403: "auth", 404: "gone", 409: "conflict", 429: "rate_limit"}.get(
                response.status_code, "http_error")
            try:
                data = response.json()
                error = data.get("error")
                if error == "invalid_grant":
                    kind = "reconnect"
                detail = error if isinstance(error, dict) else data
                if response.status_code == 400 and (detail.get("slug") == "ToolRouterV2_ToolkitNotAllowed"
                        or detail.get("code") == "ToolRouterV2_ToolkitNotAllowed"):
                    kind = "toolkit_not_allowed"
            except (ValueError, AttributeError):
                pass
            raise ConnectorFailure(kind, response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise ConnectorFailure("protocol") from None
        if not isinstance(data, dict):
            raise ConnectorFailure("protocol")
        return data
