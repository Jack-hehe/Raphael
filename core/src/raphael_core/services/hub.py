"""The services of Raphael's tools in one place: which one the owner chose for each, their keys,
and the `services.*` methods of the settings page.

`ServiceHub` reads the choices from the settings at each call (`web_search_service`,
`web_read_service`, `pdf_service`, `vision_model`, `searxng_url`, `tts_service`, `tts_fish_voice`,
`tts_fish_model`) and whether each capability is on at all (`web_search_enabled`,
`web_read_enabled`, `vision_enabled`, `pdf_enabled`, `tts_enabled`: `enabled`), so a change the
owner makes takes effect with the next request, and it holds the keys (`keys.ServiceKeys`), the HTTP client the
services are called with, the safe fetch of the built-in page reader (`fetch.PageFetcher`) and the
model that sees (`vision.Vision`). The fast tier (`agent.tools.Tools`) and the background worker's
tools reach the services through it alone.

RPC methods (the choice itself is `settings.set`, like any other setting):

- `services.status` -> `{search, read, pdf, vision, voice}`. `search`, `read` and `pdf` are each
  `{enabled, service, options: [{id, needs_key, has_key, key_optional}]}`: whether the capability
  is on (a capability that is off is not used at all), the service chosen, and each one
  that could be, with whether it needs a key of the owner's, whether one is stored and whether a key
  is optional (Jina's: it works without one, with lower limits). SearXNG's option also has
  `url_set`, whether its address is set. `vision` is `{enabled, model, resolved_model, supports_images}`:
  the setting ("" for the answer model), the model that reads images in the end, and whether
  OpenRouter's list says it reads them (null when the list does not say). `voice` is Raphael's
  voice: `{enabled, service, options, fish_voice_set}`, with `enabled`, `service` and `options` as
  above (only "fish", which needs a key) and whether the owner has filled in the Fish Audio voice id (the id
  itself is a setting, `tts_fish_voice`). A key is never in it.
- `services.set_key {service, key}` -> `{ok: true}`: the key of a keyed service, without the
  spaces around it, 1 to 512 characters with no space or control character inside, stored in the
  vault. The key is never echoed back, and a key that is refused is refused by a kind.
- `services.clear_key {service}` -> `{ok: true}`: the key removed.

Nothing here logs a key, a query, an address, a file's name or a result.
"""

import logging
import sqlite3
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ..agent.ledger import Ledger
from ..brain.catalog import IMAGE_INPUT, LLM, CatalogEntry, find
from ..brain.providers.base import ModelProvider
from ..brain.settings import DEFAULTS, SettingsStore
from ..protocol import INTERNAL_ERROR, INVALID_PARAMS, RpcError, invalid_params
from ..router import Router
from . import pdf, tts, web
from .attachments import Attachment
from .common import ServiceError
from .fetch import PageFetcher, Resolver, system_resolver
from .keys import KEYED_SERVICES, KeyRefused, ServiceKeys
from .vision import Vision

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Option:
    """A service that may be chosen for a tool: whether it needs a key of the owner's, and whether
    one is optional (it works without, and better with)."""

    id: str
    needs_key: bool = False
    key_optional: bool = False


SEARCH_OPTIONS = (
    Option("openrouter"),
    Option("brave", needs_key=True),
    Option("tavily", needs_key=True),
    Option("exa", needs_key=True),
    Option("searxng"),
)
READ_OPTIONS = (
    Option("builtin"),
    Option("jina", key_optional=True),
    Option("firecrawl", needs_key=True),
    Option("tavily", needs_key=True),
)
PDF_OPTIONS = (Option("builtin"), Option("openrouter"))
TTS_OPTIONS = (Option("fish", needs_key=True),)

# The capabilities that can be turned off, and the setting that holds the switch of each.
CAPABILITIES = {
    "search": "web_search_enabled",
    "read": "web_read_enabled",
    "vision": "vision_enabled",
    "pdf": "pdf_enabled",
    "voice": "tts_enabled",
}


def _setting(settings: SettingsStore, key: str, choices: Sequence[Option]) -> str:
    """The service chosen for a tool; the default when the saved one cannot be read or is none of
    the choices (an older version's, say)."""
    try:
        value = settings.get(key)
    except sqlite3.Error as exc:
        logger.warning("Could not read the setting %s: %s", key, type(exc).__name__)
        value = None
    return value if any(option.id == value for option in choices) else DEFAULTS[key]


class ServiceHub:
    """The services, as the owner chose them. `conn` is the open database (the settings; used on
    the event loop only), `client` the OpenRouter client (the model that sees), `keys` the keys of
    the keyed services, `models` the catalog of OpenRouter's models as it was last read
    (`Brain.known_models`; None or no catalog: what a model reads is not known). `transport` and
    `resolver` are for tests: httpx's transport for every call (the services' and the page
    reader's), and the resolver of the page reader."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: ModelProvider,
        keys: ServiceKeys,
        *,
        models: Callable[[], Sequence[CatalogEntry] | None] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = system_resolver,
    ) -> None:
        self._settings = SettingsStore(conn)
        self._keys = keys
        self._models = models
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self.fetcher = PageFetcher(resolver=resolver, transport=transport)
        self.media_fetcher = PageFetcher(resolver=resolver, transport=transport, media=True)
        self.vision = Vision(client, self.vision_model)

    def register(self, router: Router) -> None:
        for method, handler in {
            "services.status": self._status,
            "services.set_key": self._set_key,
            "services.clear_key": self._clear_key,
        }.items():
            router.register(method, handler)

    async def aclose(self) -> None:
        http, self._http = self._http, None
        if http is not None:
            await http.aclose()

    def _client(self) -> httpx.AsyncClient:
        """The HTTP client the services are called with, made on first use. It reads no proxy or
        credentials from the environment."""
        if self._http is None:
            self._http = httpx.AsyncClient(transport=self._transport, trust_env=False)
        return self._http

    # --- what is chosen -------------------------------------------------------------------------

    def search_service(self) -> str:
        return _setting(self._settings, "web_search_service", SEARCH_OPTIONS)

    def read_service(self) -> str:
        return _setting(self._settings, "web_read_service", READ_OPTIONS)

    def pdf_service(self) -> str:
        return _setting(self._settings, "pdf_service", PDF_OPTIONS)

    def tts_service(self) -> str:
        """Who speaks Raphael's answers aloud: "fish". Whether he speaks at all is `enabled("voice")`."""
        return _setting(self._settings, "tts_service", TTS_OPTIONS)

    def enabled(self, capability: str) -> bool:
        """Whether the owner has `capability` ("search", "read", "vision", "pdf" or "voice") on. A
        capability that is off is not used at all, whatever service is chosen for it. A setting
        that cannot be read is its default."""
        key = CAPABILITIES[capability]
        try:
            value = self._settings.get(key)
        except sqlite3.Error as exc:
            logger.warning("Could not read the setting %s: %s", key, type(exc).__name__)
            value = DEFAULTS[key]
        return value is True

    def vision_model(self) -> tuple[str, bool | None]:
        """The model that reads images (the `vision_model` setting, or the answer model when it is
        ""), and whether OpenRouter's list says it reads them: None when the list does not say."""
        chosen = self._text("vision_model")
        model = chosen or self._text("agent_model") or DEFAULTS["agent_model"]
        return model, self._sees(model)

    def _text(self, key: str) -> str:
        try:
            value = self._settings.get(key)
        except sqlite3.Error as exc:
            logger.warning("Could not read the setting %s: %s", key, type(exc).__name__)
            return ""
        return value.strip() if isinstance(value, str) else ""

    def _sees(self, model: str) -> bool | None:
        try:
            entries = None if self._models is None else self._models()
        except Exception as exc:  # what the catalog knows must not stop a request
            logger.warning("Could not read the catalog of models: %s", type(exc).__name__)
            entries = None
        entry = None if not entries else find(entries, LLM, model)
        return None if entry is None else IMAGE_INPUT in entry.capabilities

    # --- the tools ------------------------------------------------------------------------------

    async def search(self, query: str) -> tuple[str, dict]:
        """(the service, what it found for `query`) with the search service chosen. Not for
        "openrouter", which the answer model's own call is. ServiceError of a kind."""
        service = self.search_service()
        key = self._keys.get(service) if service in KEYED_SERVICES else None
        found = await web.search(
            service, query, http=self._client(), key=key, searxng_url=self._text("searxng_url")
        )
        return service, found

    async def read(self, url: str) -> tuple[str, dict]:
        """(the service, the page at `url`) with the reading service chosen. ServiceError."""
        service = self.read_service()
        key = self._keys.get(service) if service in KEYED_SERVICES else None
        page = await web.read(
            service, url, http=self._client(), fetcher=self.fetcher, read_pdf=pdf.read_pdf, key=key
        )
        return service, page

    def tts_ready(self) -> bool:
        """Whether Raphael can be heard: a voice service is chosen and has what it needs (for
        Fish Audio, a key and the owner's voice id). Nothing is requested to tell."""
        if not self.enabled("voice"):
            return False
        service = self.tts_service()
        if service in KEYED_SERVICES and not self._keys.has(service):
            return False
        return service != "fish" or bool(self._text("tts_fish_voice"))

    def speech(self, text: str) -> AsyncIterator[bytes]:
        """The speech of `text` by the voice service chosen (`tts.stream`) as chunks of PCM
        (`tts.PCM_RATE`, mono, 16-bit), with its key, voice and model as the settings and the vault
        hold them now. Close it (`contextlib.aclosing`) unless it is read to its end. ServiceError
        of a kind, among them "not_configured" (no service, or no voice id) and "missing_secret"
        (no key), raised when it is first read."""
        service = self.tts_service()
        key = self._keys.get(service) if service in KEYED_SERVICES else None
        return tts.stream(
            service,
            text,
            http=self._client(),
            key=key,
            voice=self._text("tts_fish_voice"),
            model=self._text("tts_fish_model"),
        )

    async def read_attachment(
        self, attachment: Attachment, question: str, ledger: Ledger
    ) -> tuple[str, dict]:
        """("image" or "pdf", what the file holds) for `question`: an image as the vision model
        sees it (`{text}`), a PDF read with the PDF service chosen (`{pages, text, truncated}`).
        ServiceError of a kind."""
        if attachment.is_pdf:
            service = self.pdf_service()
            # The scanned pages of a PDF are read as images, so with the images off they stay
            # unread; "openrouter" reads the whole file and needs the model whatever that switch says.
            sees = service == "openrouter" or self.enabled("vision")
            found = await pdf.read_pdf(
                attachment,
                service=service,
                vision=self.vision if sees else None,
                question=question,
                ledger=ledger,
            )
            return "pdf", found
        return "image", await self.vision.look([attachment], question, ledger)

    async def look(self, images: Sequence[Attachment], question: str, ledger: Ledger) -> dict:
        """What the vision model sees in several images at once, for `question`: one call."""
        return await self.vision.look(images, question, ledger)

    # --- services.* -----------------------------------------------------------------------------

    def _options(self, options: Sequence[Option]) -> list[dict[str, Any]]:
        views = []
        for option in options:
            keyed = option.id in KEYED_SERVICES
            view: dict[str, Any] = {
                "id": option.id,
                "needs_key": option.needs_key,
                "has_key": keyed and self._keys.has(option.id),
                "key_optional": option.key_optional,
            }
            if option.id == "searxng":
                view["url_set"] = bool(self._text("searxng_url"))
            views.append(view)
        return views

    def status(self) -> dict[str, Any]:
        model, sees = self.vision_model()
        return {
            "search": {
                "enabled": self.enabled("search"),
                "service": self.search_service(),
                "options": self._options(SEARCH_OPTIONS),
            },
            "read": {
                "enabled": self.enabled("read"),
                "service": self.read_service(),
                "options": self._options(READ_OPTIONS),
            },
            "pdf": {
                "enabled": self.enabled("pdf"),
                "service": self.pdf_service(),
                "options": self._options(PDF_OPTIONS),
            },
            "vision": {
                "enabled": self.enabled("vision"),
                "model": self._text("vision_model"),
                "resolved_model": model,
                "supports_images": sees,
            },
            "voice": {
                "enabled": self.enabled("voice"),
                "service": self.tts_service(),
                "options": self._options(TTS_OPTIONS),
                "fish_voice_set": bool(self._text("tts_fish_voice")),
            },
        }

    async def _status(self, params: dict) -> dict:
        return self.status()

    @staticmethod
    def _service(params: dict) -> str:
        service = params.get("service")
        if not isinstance(service, str) or service not in KEYED_SERVICES:
            raise invalid_params(f"service must be one of {', '.join(KEYED_SERVICES)}")
        return service

    async def _set_key(self, params: dict) -> dict:
        service = self._service(params)
        key = params.get("key")
        if not isinstance(key, str):
            raise invalid_params("key must be a string")
        try:
            self._keys.set(service, key)
        except KeyRefused as refused:
            message = f"Invalid params: key must be a key ({refused.kind})"
            raise RpcError(INVALID_PARAMS, message, {"kind": refused.kind}) from None
        except Exception as exc:  # the vault's message could hold the key
            logger.warning("Could not store the key of %s: %s", service, type(exc).__name__)
            raise RpcError(INTERNAL_ERROR, "The key could not be stored", {"kind": "vault"}) from None
        logger.info("The key of %s was stored", service)
        return {"ok": True}

    async def _clear_key(self, params: dict) -> dict:
        service = self._service(params)
        try:
            self._keys.clear(service)
        except Exception as exc:
            logger.warning("Could not remove the key of %s: %s", service, type(exc).__name__)
            raise RpcError(INTERNAL_ERROR, "The key could not be removed", {"kind": "vault"}) from None
        logger.info("The key of %s was removed", service)
        return {"ok": True}


def offline_transport() -> httpx.MockTransport:
    """A transport that reaches nothing, for the fake LLM's runs: every service is offline."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    return httpx.MockTransport(refuse)


__all__ = ["ServiceError", "ServiceHub", "offline_transport"]
