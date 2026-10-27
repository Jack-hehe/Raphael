"""Connector RPC contract and managed tool policy, independent of the later UI."""

import asyncio
import json
import re
import time
from dataclasses import asdict

from ..agent.policy import Tier
from ..integrations.config import ServerConfig, tool_record
from ..integrations.oauth import open_in_browser
from ..integrations.toolsets import tool_kind
from ..protocol import INTERNAL_ERROR, RpcError, invalid_params
from .apps import APPS
from .composio import KEY, ComposioClient, segment, trusted_url
from .google import PREFIX, READ_TOOLS, TOOLS, GoogleClient, permitted, scopes_for
from .http import ConnectorFailure
from .mcp import GoogleTools, ManagedMcp
from .store import STATUSES, Connector, ConnectorStore

META_READ = {"COMPOSIO_SEARCH_TOOLS", "COMPOSIO_GET_TOOL_SCHEMAS"}
META_EXECUTE = "COMPOSIO_MULTI_EXECUTE_TOOL"
META = META_READ | {META_EXECUTE}
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}")
_TOOL = re.compile(r"[A-Za-z0-9_.-]{1,128}")
COMPOSIO_SERVER = "connectors_composio"
GOOGLE_SERVER = "connectors_google"


def _text(params: dict, key: str, *, optional=False) -> str:
    value = params.get(key, "" if optional else None)
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 32 for c in value) or (not optional and not value):
        raise invalid_params(f"{key} must be valid text")
    return value.strip()


def _slug(params: dict, key="toolkit") -> str:
    value = _text(params, key)
    if not _SLUG.fullmatch(value):
        raise invalid_params("toolkit must be a lowercase slug")
    return value


def _names(value) -> list[str]:
    if not isinstance(value, list) or len(value) > 5000 or any(not isinstance(t, str) or not _TOOL.fullmatch(t) for t in value):
        raise invalid_params("disabled_tools must be a list of tool names")
    return sorted(set(value))


def _validate(value, schema: dict) -> bool:
    """Validate the small private Google surface before creating a request."""
    kind = schema.get("type")
    types = {"string": str, "integer": int, "boolean": bool, "object": dict, "array": list}
    if kind in types and type(value) is not types[kind]:
        return False
    if kind == "object":
        props = schema.get("properties", {})
        return (all(k in value for k in schema.get("required", []))
                and (schema.get("additionalProperties") is not False or all(k in props for k in value))
                and all(_validate(v, props[k]) for k, v in value.items() if k in props))
    if kind == "array":
        return all(_validate(v, schema.get("items", {})) for v in value)
    if kind == "integer":
        return schema.get("minimum", value) <= value <= schema.get("maximum", value)
    return True


def _kind(tool: dict, toolkit: str) -> str:
    tags = tool.get("tags") if isinstance(tool.get("tags"), list) else []
    if any(tag in tags for tag in ("createHint", "updateHint", "destructiveHint")):
        return "write"
    if "readOnlyHint" in tags:
        return "read"
    name = tool["slug"].removeprefix(toolkit.upper() + "_")
    record = tool_record(name, "", {})
    return tool_kind(record) if record else "write"


class ConnectorsService:
    def __init__(self, directory, vault, integrations, *, transport=None, open_browser=open_in_browser,
                 sleep=asyncio.sleep, now=time.monotonic, poll_timeout=600, google_listener=None) -> None:
        self.store = ConnectorStore(directory)
        self.vault = vault
        self.hub = integrations
        self.composio = ComposioClient(lambda: vault.get(KEY), transport=transport, now=now)
        self.google = GoogleClient(vault, transport=transport, **({"listener": google_listener} if google_listener else {}))
        self.open_browser, self.sleep, self.now = open_browser, sleep, now
        self.poll_timeout = poll_timeout
        self._lock = asyncio.Lock()
        self._tool_catalog: dict[str, list[dict]] = {}
        self._store_catalog: tuple[float, dict] | None = None
        self._important: dict[str, set[str]] = {}
        self._notify = lambda: None
        self._transport = transport
        integrations.managed = self
        integrations._connector.managed_opener = self.open_managed
        # Existing cached tool lists must already obey connector policy before any RPC runs.
        for server in integrations._config.servers():
            if server.managed_by and server.id not in (COMPOSIO_SERVER, GOOGLE_SERVER):
                server.enabled = False

    def register(self, router) -> None:
        methods = {"catalog": self.catalog, "categories": self.categories, "connect": self.connect,
                   "status": self.status, "disconnect": self.disconnect, "tools": self.tools,
                   "set_tools": self.set_tools, "set_key": self.set_key,
                   "google.configure": self.google_configure, "google.connect": self.google_connect}
        for name, handler in methods.items():
            router.register("connectors." + name, self._handler(handler, name))
        self._notify = lambda: router.notify("connectors.changed", {})

    def _handler(self, handler, name):
        async def run(params):
            try:
                if name == "status":
                    return await handler(params)
                async with self._lock:
                    return await handler(params)
            except ConnectorFailure as error:
                return {"ok": False, "error": error.kind, "http_status": error.status or None}
            except RpcError:
                raise
            except Exception:
                # The router normally logs exception tracebacks; never let secret-bearing
                # vault, browser, callback or HTTP exception messages reach that path.
                raise RpcError(INTERNAL_ERROR, "Connector operation failed") from None
        return run

    def _save(self):
        self.store.save()
        self._notify()
        self.hub._changed()

    def _entry(self, params) -> Connector:
        entry = self.store.connectors.get(_text(params, "id"))
        if entry is None:
            raise invalid_params("There is no such connector")
        return entry

    def _view(self, entry: Connector) -> dict:
        view = asdict(entry)
        if entry.kind == "google":
            view["enabled_tools"] = [name for name in TOOLS if self.tool_available(GOOGLE_SERVER, name)]
        return {**view, "stores_tool_payloads": entry.kind == "composio",
                "payload_retention_days": 365 if entry.kind == "composio" else 0,
                "draft_only_enforced_by": "raphael"}

    async def catalog(self, params):
        # Publish only a complete snapshot; a failed refresh must not replace the last good list.
        if not self.vault.get(KEY):
            raise ConnectorFailure("missing_key")
        before = self._store_catalog
        if before and self.now() - before[0] < 3600:
            return before[1]
        try:
            items, slugs, cursors = [], set(), set()
            cursor, count, truncated = None, 0, False
            for _ in range(100):
                data = await self.composio.page("/toolkits", {"limit": 500, "sort_by": "usage", "cursor": cursor})
                remaining = 3000 - count
                for item in data["items"][:remaining]:
                    count += 1
                    if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
                        raise ConnectorFailure("protocol")
                    if item["slug"] in slugs:
                        continue
                    slugs.add(item["slug"])
                    meta = item.get("meta") or {}
                    if (not isinstance(meta, dict) or not isinstance(meta.get("categories", []), list)
                            or not isinstance(item.get("auth_schemes", []), list)):
                        raise ConnectorFailure("protocol")
                    # Account state and auth configuration never reach the UI.
                    items.append({"slug": item["slug"], "name": item.get("name", item["slug"]),
                        "description": meta.get("description", ""), "logo": meta.get("logo", ""),
                        "categories": meta.get("categories", []), "tools_count": meta.get("tools_count", 0),
                        "can_connect": bool(item.get("no_auth") or item.get("composio_managed_auth_schemes")),
                        "no_auth": bool(item.get("no_auth")),
                        "auth_schemes": [str(x).lower() for x in item.get("auth_schemes", [])]})
                next_cursor = data.get("next_cursor")
                if count >= 3000 or not next_cursor:
                    truncated = bool(next_cursor or len(data["items"]) > remaining)
                    break
                if not isinstance(next_cursor, str) or next_cursor in cursors:
                    raise ConnectorFailure("pagination")
                cursors.add(next_cursor)
                cursor = next_cursor
            else:
                raise ConnectorFailure("pagination")
            categories = await self.composio.pages("/toolkits/categories")
            result = {"items": items, "categories": categories, "next_cursor": None,
                      "total_items": len(items), "truncated": truncated, "stale": False,
                      "stores_tool_payloads": True, "payload_retention_days": 365}
            self._store_catalog = (self.now(), result)
            return result
        except ConnectorFailure:
            if before:
                return {**before[1], "stale": True}
            raise

    async def categories(self, params):
        return await self.composio.page("/toolkits/categories", cache=True)

    async def set_key(self, params):
        key = _text(params, "key", optional=True)
        if key.startswith("sk-or-"):
            raise ConnectorFailure("openrouter_key")
        if key.startswith("ck_"):
            raise ConnectorFailure("consumer_key")
        before = self.vault.get(KEY)
        if key:
            probe = ComposioClient(lambda: key, transport=self._transport)
            try:
                await probe.request("GET", "/toolkits", params={"limit": 1})
            except ConnectorFailure as error:
                if error.status in (401, 403):
                    raise ConnectorFailure("auth", error.status) from None
                raise
        if key != before:
            self._remove_managed(COMPOSIO_SERVER)
            self.store.session_id, self.store.config_version = "", None
            self.store.connectors = {k: v for k, v in self.store.connectors.items() if v.kind != "composio"}
            if key:
                self.vault.set(KEY, key)
            else:
                self.vault.delete(KEY)
            self.composio._cache.clear()
            self._store_catalog = None
            self._tool_catalog.clear()
            self._important.clear()
        self._save()
        return {"configured": bool(key)}

    def _config(self):
        active = {c.id.split(":", 1)[1]: c for c in self.store.connectors.values()
                  if c.kind == "composio" and c.status == "ACTIVE" and c.enabled}
        return {"toolkits": {"enable": sorted(active)},
                "tools": {slug: {"disable": c.disabled_tools} for slug, c in active.items()},
                "manage_connections": {"enable": False}, "workbench": {"enable": False},
                "proxy_execute": {"enable": False}}

    async def _session(self):
        data = None
        if self.store.session_id:
            try:
                data = await self.composio.request("GET", "/tool_router/session/" + segment(self.store.session_id))
            except ConnectorFailure as error:
                if error.kind != "gone":
                    raise
        if data is None:
            data = await self.composio.request("POST", "/tool_router/session", json={"user_id": self.store.user_id, **self._config()})
        ident = data.get("session_id", self.store.session_id)
        if not isinstance(ident, str) or not ident:
            raise ConnectorFailure("protocol")
        url = trusted_url(data.get("mcp", {}).get("url", ""))
        self.store.session_id = ident
        self.store.config_version = data.get("config_version")
        self._save()
        await self._install_composio(url, refresh=False)
        return data

    async def _patch(self, config=None, *, refresh=True):
        if not self.store.session_id:
            await self._session()
        config = ({**self._config(), "search": {"enable": True}, "execute": {"enable_multi_execute": True}}
                  if config is None else config)
        data = await self.composio.patch(self.store.session_id, config, self.store.config_version)
        self.store.config_version = data.get("config_version", self.store.config_version)
        self._save()
        server = self.hub._config.get(COMPOSIO_SERVER)
        if server and refresh:
            await self._install_composio(server.url)

    async def _install_composio(self, url, *, refresh=True):
        server = self.hub._config.get(COMPOSIO_SERVER)
        active = any(c.kind == "composio" and c.status == "ACTIVE" for c in self.store.connectors.values())
        if server is None:
            server = ServerConfig(id=COMPOSIO_SERVER, name="Composio", transport="http", url=url,
                headers=["x-api-key"], managed_by="composio", header_refs={"x-api-key": KEY}, enabled=False)
            self.hub._config.add(server)
        if server.url != url:
            self.hub._connector.invalidate(server.id)
        server.url, server.enabled = url, active
        if active and refresh:
            try:
                server.tools = await self.hub._connector.list_tools(server)
                server.tools_checked_at_ms = self.hub._now_ms()
            except Exception:
                server.enabled = False
                self.hub._config.save()
                raise ConnectorFailure("mcp_unavailable") from None
        self.hub._config.save()
        self.hub._changed()

    async def connect(self, params):
        slug = _slug(params)
        details = await self.composio.request("GET", "/toolkits/" + segment(slug))
        if not (details.get("no_auth") or details.get("composio_managed_auth_schemes")):
            raise ConnectorFailure("needs_own_oauth_app")
        body = {"toolkit": slug}
        if "alias" in params:
            body["alias"] = _text(params, "alias")
        await self._session()
        before = self._config()
        pending = {**before, "toolkits": {"enable": sorted(set(before["toolkits"]["enable"]) | {slug})}}
        connected = False
        try:
            # The toolkit must be permitted before the provider can issue a login link.
            await self._patch(pending, refresh=False)
            link = await self.composio.request("POST", "/tool_router/session/" + segment(self.store.session_id) + "/link", json=body)
            url = trusted_url(link.get("redirect_url", ""))
            account_id = link.get("connected_account_id")
            if not isinstance(account_id, str) or not account_id:
                raise ConnectorFailure("protocol")
            ident = "composio:" + slug
            entry = self.store.connectors.setdefault(ident, Connector(ident, "composio"))
            entry.name = details.get("name", slug)
            entry.description = (details.get("meta") or {}).get("description", "")
            entry.logo = (details.get("meta") or {}).get("logo", "")
            prior_active = entry.status == "ACTIVE"
            entry.status = "INITIALIZING"
            entry.accounts = [a for a in entry.accounts if a["id"] != account_id] + [{"id": account_id, "status": "INITIALIZING"}]
            self._save()
            try:
                await self.open_browser(url)
                deadline = self.now() + self.poll_timeout
                while self.now() < deadline:
                    account = await self.composio.request("GET", "/connected_accounts/" + segment(account_id))
                    status = account.get("status")
                    if status not in STATUSES:
                        raise ConnectorFailure("protocol")
                    if account.get("id") != account_id:
                        raise ConnectorFailure("account_mismatch")
                    if account.get("toolkit", {}).get("slug", slug) != slug:
                        raise ConnectorFailure("account_mismatch")
                    entry.accounts = [a for a in entry.accounts if a["id"] != account_id] + [{"id": account_id, "status": status}]
                    entry.status = "ACTIVE" if prior_active else status
                    self._save()
                    if status == "ACTIVE":
                        await self._load_tools(entry)
                        await self._patch()
                        connected = True
                        return {"connected": True, "connector": self._view(entry)}
                    if status not in ("INITIALIZING", "INITIATED"):
                        return {"connected": False, "error": status.lower(), "connector": self._view(entry)}
                    await self.sleep(2)
                entry.status = "ACTIVE" if prior_active else "EXPIRED"
                self._save()
                return {"connected": False, "error": "timeout", "connector": self._view(entry)}
            except BaseException:
                entry.status = "ACTIVE" if prior_active else "INACTIVE"
                self._save()
                raise
        finally:
            if not connected:
                # Restore the prior toolkit permissions on refusal, timeout or cancellation.
                await self._patch(before, refresh=False)

    async def _load_tools(self, entry):
        slug = entry.id.split(":", 1)[1]
        if entry.id not in self._important:
            featured = await self.composio.pages("/tools", {"toolkit_slug": slug, "limit": 1000, "important": "true"})
            self._important[entry.id] = {t["slug"] for t in featured if isinstance(t.get("slug"), str)}
        tools = await self.composio.pages("/tools", {"toolkit_slug": slug, "limit": 1000})
        checked = [t for t in tools if isinstance(t, dict) and isinstance(t.get("slug"), str) and _TOOL.fullmatch(t["slug"])]
        # New tools are on by default; only the owner's explicit exclusions persist.
        entry.enabled_tools = sorted({t["slug"] for t in checked} - set(entry.disabled_tools))
        self._tool_catalog[entry.id] = checked
        self._save()

    async def tools(self, params):
        entry = self._entry(params)
        if entry.kind == "google":
            items = [{"slug": name, "name": name, "description": description,
                      "enabled": name not in entry.disabled_tools and permitted(name, entry.granted_scopes, entry.draft_only)
                          and permitted(name, scopes_for(entry.gmail, entry.calendar), entry.draft_only),
                      "kind": "read" if name in READ_TOOLS else "write"}
                     for name, (description, _) in TOOLS.items()]
        else:
            await self._load_tools(entry)
            items = [{"slug": t["slug"], "name": t.get("name", t["slug"]), "description": t.get("description", ""),
                      "tags": t.get("tags", []), "enabled": t["slug"] in entry.enabled_tools,
                      "kind": _kind(t, entry.id.split(":", 1)[1]),
                      "important": t["slug"] in self._important.get(entry.id, set())}
                     for t in self._tool_catalog[entry.id]]
        return {"items": items, "total_items": len(items)}

    async def set_tools(self, params):
        entry = self._entry(params)
        names = _names(params.get("disabled_tools", entry.disabled_tools))
        enabled = params.get("enabled", entry.enabled)
        if type(enabled) is not bool:
            raise invalid_params("enabled must be boolean")
        if entry.kind == "google":
            known = set(TOOLS)
        elif "disabled_tools" not in params:
            # A service switch preserves tool decisions and can narrow access offline.
            known = set(entry.enabled_tools) | set(entry.disabled_tools)
        else:
            await self._load_tools(entry)
            known = {t["slug"] for t in self._tool_catalog[entry.id]}
        if set(names) - known:
            raise invalid_params("Unknown tool name")
        before = set(entry.disabled_tools)
        draft_only = params.get("draft_only", entry.draft_only)
        if type(draft_only) is not bool:
            raise invalid_params("draft_only must be boolean")
        # This is a local policy and takes effect even if the remote PATCH is unavailable.
        entry.draft_only = draft_only
        # A failed remote update must never undo a local switch-off.
        entry.enabled = entry.enabled and enabled
        # Narrow locally before a network call. Broaden only after PATCH succeeds.
        entry.disabled_tools = sorted(before | set(names))
        entry.enabled_tools = sorted(known - set(entry.disabled_tools))
        self._save()
        if entry.kind == "composio":
            await self._session()
            config = self._config()
            if entry.status == "ACTIVE" and enabled:
                config["toolkits"]["enable"] = sorted(set(config["toolkits"]["enable"]) | {entry.id.split(":", 1)[1]})
                config["tools"][entry.id.split(":", 1)[1]] = {"disable": names}
            result = await self.composio.patch(self.store.session_id, config, self.store.config_version)
            self.store.config_version = result.get("config_version", self.store.config_version)
        entry.disabled_tools, entry.enabled_tools = names, sorted(known - set(names))
        entry.enabled = enabled
        self.hub._connector.invalidate(COMPOSIO_SERVER if entry.kind == "composio" else GOOGLE_SERVER)
        self._save()
        return {"connector": self._view(entry)}

    async def status(self, params):
        refresh = params.get("refresh", True)
        if type(refresh) is not bool:
            raise invalid_params("refresh must be boolean")
        if refresh and self.vault.get(KEY) and not self._lock.locked():
            async with self._lock:
                accounts = await self.composio.pages("/connected_accounts", {"user_ids": self.store.user_id, "limit": 500})
                by_slug = {}
                for account in accounts:
                    slug = account.get("toolkit", {}).get("slug")
                    status = account.get("status")
                    if isinstance(slug, str) and status in STATUSES and isinstance(account.get("id"), str):
                        by_slug.setdefault(slug, []).append({"id": account["id"], "status": status})
                for entry in self.store.connectors.values():
                    if entry.kind == "composio":
                        entry.accounts = by_slug.get(entry.id.split(":", 1)[1], [])
                        entry.status = "ACTIVE" if any(a["status"] == "ACTIVE" for a in entry.accounts) else (
                            entry.accounts[0]["status"] if entry.accounts else "INACTIVE")
                await self._session()
                await self.composio.pages("/tool_router/session/" + segment(self.store.session_id) + "/toolkits",
                                          {"is_connected": "true", "limit": 50})
                await self._patch()
                self._save()
        return {"connectors": [self._view(c) for c in self.store.connectors.values()],
                "composio_configured": bool(self.vault.get(KEY)),
                "google_configured": bool(self.vault.get(PREFIX + "client_id")),
                "duplicate_google_paths": "google" in self.store.connectors and any(
                    c.id in ("composio:gmail", "composio:googlecalendar") and c.status == "ACTIVE"
                    for c in self.store.connectors.values())}

    def _remove_managed(self, ident):
        self.hub._connector.invalidate(ident)
        self.hub._config.remove(ident)
        self.hub._changed()

    async def disconnect(self, params):
        entry = self._entry(params)
        if entry.kind == "google":
            # Disable first even if provider revocation is temporarily unavailable; retain
            # credentials on failure so the owner can retry revocation rather than lose it.
            entry.status = "REVOKED"
            self._remove_managed(GOOGLE_SERVER)
            self._save()
            await self.google.revoke()
            del self.store.connectors[entry.id]
        else:
            entry.status = "INACTIVE"
            self.hub._connector.invalidate(COMPOSIO_SERVER)
            self._save()
            for account in list(entry.accounts):
                try:
                    result = await self.composio.request("DELETE", "/connected_accounts/" + segment(account["id"]),
                                                        params={"revoke_on_delete": "true"})
                    if result.get("success") is not True:
                        raise ConnectorFailure("revocation_failed")
                except ConnectorFailure as error:
                    if error.kind != "gone":
                        raise
                entry.accounts.remove(account)
                self._save()
            del self.store.connectors[entry.id]
            await self._session()
            await self._patch()
        self._save()
        return {"disconnected": True}

    async def google_configure(self, params):
        client_id = _text(params, "client_id")
        client_secret = _text(params, "client_secret", optional=True)
        gmail, calendar = params.get("gmail", "write"), params.get("calendar", "write")
        try:
            scopes_for(gmail, calendar)
        except ValueError:
            raise invalid_params("Scope presets must be read or write") from None
        draft_only = params.get("draft_only", False)
        if type(draft_only) is not bool:
            raise invalid_params("draft_only must be boolean")
        entry = self.store.connectors.setdefault("google", Connector("google", "google"))
        changed = (self.vault.get(PREFIX + "client_id") != client_id or entry.gmail != gmail or entry.calendar != calendar
                   or self.vault.get(PREFIX + "client_secret") != (client_secret or None))
        if changed:
            entry.status, entry.granted_scopes = "INACTIVE", []
            self.google.forget_access()
            self.vault.delete(PREFIX + "refresh_token")
        entry.gmail, entry.calendar, entry.draft_only = gmail, calendar, draft_only
        self.vault.set(PREFIX + "client_id", client_id)
        if client_secret:
            self.vault.set(PREFIX + "client_secret", client_secret)
        else:
            self.vault.delete(PREFIX + "client_secret")
        await self.sync_google()
        self._save()
        return {"configured": True, "connector": self._view(entry), "requested_scopes": scopes_for(gmail, calendar),
                "testing_refresh_expires_days": 7, "revocation_affects_whole_project": True}

    async def google_connect(self, params):
        entry = self.store.connectors.get("google")
        if entry is None:
            raise ConnectorFailure("not_configured")
        entry.status = "INITIATED"
        self._save()
        try:
            entry.granted_scopes = await self.google.connect(scopes_for(entry.gmail, entry.calendar), self.open_browser)
            entry.status, entry.accounts = "ACTIVE", [{"id": "me", "status": "ACTIVE"}]
            await self.sync_google()
            self._save()
        except BaseException:
            entry.status = "INACTIVE"
            await self.sync_google()
            self._save()
            raise
        return {"connected": True, "connector": self._view(entry)}

    async def sync_google(self):
        entry = self.store.connectors.get("google")
        if entry is None:
            return
        server = self.hub._config.get(GOOGLE_SERVER)
        if server is None:
            server = ServerConfig(id=GOOGLE_SERVER, name="Google (private)", transport="stdio", managed_by="google", enabled=False)
            self.hub._config.add(server)
        server.enabled = entry.status == "ACTIVE"
        server.tools = [tool_record(name, description, schema, name in READ_TOOLS) for name, (description, schema) in TOOLS.items()]
        server.tools_checked_at_ms = self.hub._now_ms()
        entry.enabled_tools = [name for name in TOOLS if self.tool_available(GOOGLE_SERVER, name)]
        self.hub._connector.invalidate(GOOGLE_SERVER)
        self.hub._config.save()
        self.hub._changed()

    def open_managed(self, server, secrets):
        if server.managed_by == "composio":
            return ManagedMcp(server.url, secrets.headers, transport=self._transport,
                call_guard=lambda name, args: self.allow_call(server.id, name, args, "user"))
        if server.managed_by == "google":
            return GoogleTools(self)
        raise ConnectorFailure("blocked")

    def tool_available(self, server_id, name):
        if server_id == COMPOSIO_SERVER:
            return bool(self.vault.get(KEY)) and name in META and any(
                c.kind == "composio" and c.status == "ACTIVE" and c.enabled and c.enabled_tools for c in self.store.connectors.values())
        if server_id == GOOGLE_SERVER:
            entry = self.store.connectors.get("google")
            return bool(entry and entry.status == "ACTIVE" and entry.enabled and name not in entry.disabled_tools
                        and permitted(name, entry.granted_scopes, entry.draft_only)
                        and permitted(name, scopes_for(entry.gmail, entry.calendar), entry.draft_only))
        return False

    def ready_tools_of(self, entry: Connector) -> list[str]:
        """The tools of one connector that a call can reach now: those the gate itself lets through
        (`tool_available`, `allow_call`) when the connector is signed in, on, and its server is on. The count
        of the owner's apps (`integrations.inventory`) and the cards of a chat both use this."""
        server = self.hub._config.get(GOOGLE_SERVER if entry.kind == "google" else COMPOSIO_SERVER)
        if server is None or not server.enabled:
            return []
        if entry.kind == "google":
            return [name for name in TOOLS if self.tool_available(GOOGLE_SERVER, name)]
        if entry.status != "ACTIVE" or not entry.enabled or not self.tool_available(COMPOSIO_SERVER, META_EXECUTE):
            return []
        return [name for name in entry.enabled_tools if name not in entry.disabled_tools]

    @staticmethod
    def services_of(entry: Connector, ready: list[str]) -> list[str]:
        """The apps a connector reaches now, by the ids a Composio toolkit and the cards share: a toolkit is
        its own slug, the owner's Google is each product whose tools can be called."""
        if entry.kind == "google":
            return [app.id for app in APPS.values() if any(name.startswith(app.prefix) for name in ready)]
        return [entry.id.removeprefix("composio:")]

    def tier(self, server_id, name):
        if server_id == COMPOSIO_SERVER:
            return Tier.T2 if name == META_EXECUTE else Tier.T1
        return Tier.T1 if name in READ_TOOLS else Tier.T2

    def allow_call(self, server_id, name, args, approver):
        if not self.tool_available(server_id, name):
            return False
        if self.tier(server_id, name) >= Tier.T2 and approver != "user":
            return False
        if server_id == GOOGLE_SERVER:
            return _validate(args, TOOLS[name][1])
        if name in META_READ:
            return True
        # [U] The execution envelope is supplied by the live MCP schema. This isolated
        # adapter accepts only the tested tool_slug envelope and fails closed otherwise.
        calls = args.get("tools")
        if set(args) != {"tools"} or not isinstance(calls, list) or not 1 <= len(calls) <= 50:
            return False
        for call in calls:
            if (not isinstance(call, dict) or set(call) != {"tool_slug", "arguments"}
                    or not isinstance(call.get("tool_slug"), str) or not isinstance(call.get("arguments"), dict)):
                return False
            tool = call["tool_slug"]
            entries = [c for c in self.store.connectors.values() if c.kind == "composio" and c.status == "ACTIVE" and c.enabled
                       and tool in c.enabled_tools and tool not in c.disabled_tools]
            if not entries or all(c.draft_only for c in entries) and re.search(r"(^|_)(SEND|POST|PUBLISH)(_|$)", tool):
                return False
        return True

    async def auth_failed(self, server_id):
        if server_id != COMPOSIO_SERVER:
            return
        for entry in self.store.connectors.values():
            if entry.kind == "composio" and entry.status == "ACTIVE":
                entry.status = "EXPIRED"
        self.hub._connector.invalidate(COMPOSIO_SERVER)
        self._save()
        try:
            await self.status({"refresh": True})
        except Exception:
            # Status refresh is best effort; tools remain unavailable until reconfirmed.
            pass

    async def call_google(self, name, args):
        if not self.tool_available(GOOGLE_SERVER, name) or not _validate(args, TOOLS[name][1]):
            raise ConnectorFailure("blocked")
        try:
            await self.google.access_token()
            entry = self.store.connectors["google"]
            if self.google.scopes is not None and entry.granted_scopes != self.google.scopes:
                entry.granted_scopes = self.google.scopes
                await self.sync_google()
                self._save()
            if not self.tool_available(GOOGLE_SERVER, name):
                raise ConnectorFailure("blocked")
            return await self.google.call(name, args)
        except ConnectorFailure as error:
            if error.kind in ("auth", "reconnect"):
                entry = self.store.connectors["google"]
                entry.status = "EXPIRED"
                await self.sync_google()
                self._save()
            raise
