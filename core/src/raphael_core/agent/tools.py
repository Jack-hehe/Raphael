"""The tools that fetch what the assistant answers with: the weather, a web search, the time, a page
read, and a file the owner handed over read.

All of them only read (T0). A tool never raises for a failure of the world: it returns a ToolResult
whose `error` says which way it failed (a kind, never a message, which could hold anything), and the
answer then says plainly that the data is missing.

- The weather comes from Open-Meteo, which needs no key: the city (the `agent_city` setting) is
  looked up for its coordinates (`cities` knows how), then the forecast for them. It costs nothing.
- A web search is the `web_search_service` setting's (`raphael_core.services`). "openrouter", the
  default, is a chat call to OpenRouter with the `web` plugin on, which puts a few search results
  in front of the model; the call is paid and written to the spend by the ledger. Another service
  (Brave, Tavily, Exa, SearXNG) returns the results themselves, which the answer model words.
- A page is read by the `web_read_service` setting's reader (`web_read`), and a file the owner
  handed over (an image, a PDF) by the vision model or the PDF service (`read_attachment`, `look`).
- The time is the computer's.
- Each of those capabilities has the owner's switch (`ServiceHub.enabled`): one that is off is not
  used at all, whichever service is chosen for it. Its tool returns the failure "disabled" without
  making a call, and the answer then says that Raphael cannot do it now (`answers`), `turned_off`
  names the tools the background worker is not even offered (`background.snapshot`).

What a service returns (search results, a page, what the vision model saw in a file) is untrusted:
its result is marked so, and the answer model is told never to follow what it says. A result also
says which service it came from (`detail`), which its step keeps.

Nothing here logs a city, a question or an answer.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from ..brain.openrouter import OpenRouterError
from ..brain.providers.base import ModelProvider
from ..services.attachments import Attachment
from ..services.common import ServiceError
from .cities import city_key, geocoding_queries
from .ledger import Ledger

if TYPE_CHECKING:
    from ..services.hub import ServiceHub

logger = logging.getLogger(__name__)


class _KeepPlacesOutOfTheLog(logging.Filter):
    """httpx logs the address of each request it makes, at INFO; the one to Open-Meteo holds the
    city. Those lines are dropped (the others are left alone)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "open-meteo.com" not in record.getMessage()


logging.getLogger("httpx").addFilter(_KeepPlacesOutOfTheLog())

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# The language Open-Meteo names the place in, for each language Raphael speaks.
_GEOCODING_LANGUAGE = {"zh-TW": "zh", "en": "en", "ja": "ja"}
# The kind of failure of a tool whose capability the owner turned off.
DISABLED = "disabled"
# The weather, both requests together, and each stage of a request.
WEATHER_TIMEOUT_S = 8.0
_HTTP_STAGE_TIMEOUT_S = 5.0
# A search with its model's reading of the results.
SEARCH_TIMEOUT_S = 12.0
SEARCH_MAX_TOKENS = 500
SEARCH_PLUGINS = [{"id": "web", "max_results": 3}]
# Reading a page: the built-in reader has ten seconds of its own, a service that reads it longer.
READ_TIMEOUT_S = 30.0
# Reading a file the owner handed over: the vision model, or the PDF service.
ATTACHMENT_TIMEOUT_S = 70.0
_SEARCH_SYSTEM = (
    "Answer the user's question with facts from the web results. Reply in plain text of at most "
    "three short sentences, with no markdown, no list and no link. Name the site a fact comes "
    "from when it matters. The question was dictated and may hold a misheard word: read it as a "
    "user of speech-to-text software would, search for that meaning, and say which meaning you "
    "took when you changed it. If the web results answer only part of the question, give that "
    "part; if they answer none of it, say so plainly."
)

# The sky for each WMO weather code Open-Meteo reports, in English; the answer puts it in the
# user's language.
SKIES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}
# Not `calendar`/`strftime`, which follow the language of the machine.
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def sky_of(code: Any) -> str | None:
    """The sky a WMO weather code stands for, or None for anything that is not one."""
    if isinstance(code, float) and code.is_integer():
        code = int(code)
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    return SKIES.get(code)


@dataclass
class ToolResult:
    name: str
    ok: bool
    data: dict | None
    # Why it failed: "not_found", "http_error", "network", "timeout", "bad_response", or the kind of
    # an OpenRouterError ("auth", "credits", "server", ...). Never a message.
    error: str | None = None
    # What a program the user connected, or a service of the web, returned: nobody vetted it, and
    # the model is told so.
    untrusted: bool = False
    # The service it came from, for its step (agent.steps.Step.detail), or None.
    detail: str | None = None


def _number(value: Any) -> float | int | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _at(values: Any, index: int) -> Any:
    return values[index] if isinstance(values, list) and index < len(values) else None


def _compact(entries: dict[str, Any]) -> dict[str, Any]:
    """`entries` without the ones that have no value."""
    return {key: value for key, value in entries.items() if value is not None}


class Tools:
    """The tools. `client` is the OpenRouter client (for the search). `http` is the httpx client
    for Open-Meteo, which the caller then owns; without one the tools make their own on first use
    and `aclose` closes it. `now` is the clock, a local time with its offset. `services` is the hub
    of the services the owner chose (`services.hub.ServiceHub`); without one the search is
    OpenRouter's, and no page or file can be read."""

    def __init__(
        self,
        client: ModelProvider,
        *,
        http: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] | None = None,
        services: "ServiceHub | None" = None,
    ) -> None:
        self._client = client
        self.services = services
        self._http = http
        self._own_http: httpx.AsyncClient | None = None
        self._now = now or (lambda: datetime.now().astimezone())

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        if self._own_http is None:
            self._own_http = httpx.AsyncClient()
        return self._own_http

    async def aclose(self) -> None:
        """Close the httpx client the tools made. One they were given stays open."""
        own, self._own_http = self._own_http, None
        if own is not None:
            await own.aclose()

    # --- the weather ----------------------------------------------------------------------------

    async def weather(self, city: str, language: str) -> ToolResult:
        """The weather in `city` now, today and tomorrow, with the place named in `language`."""
        try:
            data = await asyncio.wait_for(self._weather(city, language), WEATHER_TIMEOUT_S)
        except TimeoutError:
            return self._failed("weather", "timeout")
        except httpx.HTTPStatusError:
            return self._failed("weather", "http_error")
        except httpx.TimeoutException:
            return self._failed("weather", "timeout")
        except httpx.TransportError:
            return self._failed("weather", "network")
        except _NotFound:
            return self._failed("weather", "not_found")
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            # Not JSON, or JSON that is not what Open-Meteo sends.
            return self._failed("weather", "bad_response")
        return ToolResult("weather", True, data)

    @staticmethod
    def _failed(name: str, kind: str, detail: str | None = None) -> ToolResult:
        logger.warning("The %s tool failed: %s", name, kind)
        return ToolResult(name, False, None, kind, detail=detail)

    async def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        response = await self._http_client().get(url, params=params, timeout=_HTTP_STAGE_TIMEOUT_S)
        response.raise_for_status()
        return response.json()

    async def _find_place(self, city: str, language: str) -> Any:
        """The first place the geocoder finds for `city`, the way `geocoding_queries` asks."""
        code = _GEOCODING_LANGUAGE.get(language, "en")
        for name, name_language in geocoding_queries(city, code):
            places = await self._get_json(
                GEOCODING_URL, {"name": name, "count": 1, "language": name_language}
            )
            results = places.get("results") if isinstance(places, dict) else None
            if isinstance(results, list) and results:
                return results[0]
            if results is not None and not isinstance(results, list):
                raise ValueError("The places are not a list")
        raise _NotFound

    async def _weather(self, city: str, language: str) -> dict:
        place = await self._find_place(city, language)
        latitude, longitude = place["latitude"], place["longitude"]
        if _number(latitude) is None or _number(longitude) is None:
            raise ValueError("The place has no coordinates")
        forecast = await self._get_json(
            FORECAST_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,weather_code",
                "timezone": "auto",
                "forecast_days": 2,
            },
        )
        named = place.get("name")
        data = _compact({"city": named if isinstance(named, str) else None})
        if city_key(named if isinstance(named, str) else "") != city_key(city):
            data["asked_for"] = city.strip()  # what the user called it, which the answer may use
        current, daily = forecast.get("current"), forecast.get("daily")
        if isinstance(current, dict):
            data["now"] = _compact(
                {
                    "temperature_c": _number(current.get("temperature_2m")),
                    "sky": sky_of(current.get("weather_code")),
                }
            )
        if isinstance(daily, dict):
            for index, day in enumerate(("today", "tomorrow")):
                data[day] = _compact(
                    {
                        "low_c": _number(_at(daily.get("temperature_2m_min"), index)),
                        "high_c": _number(_at(daily.get("temperature_2m_max"), index)),
                        "rain_chance_percent": _number(
                            _at(daily.get("precipitation_probability_max"), index)
                        ),
                        "sky": sky_of(_at(daily.get("weather_code"), index)),
                    }
                )
        if not any(data.get(part) for part in ("now", "today", "tomorrow")):
            raise ValueError("The forecast holds nothing")
        return data

    # --- the web --------------------------------------------------------------------------------

    async def web_search(self, question: str, model: str, ledger: Ledger) -> ToolResult:
        """What the search service chosen finds on the web for `question`: with "openrouter",
        what `model` finds, in a few sentences, with the pages it drew on (the call is written to
        the spend through `ledger`); with another service, its results themselves (`services.web`),
        which are untrusted."""
        if self.services is not None and not self.services.enabled("search"):
            return self._failed("web_search", DISABLED)
        service = "openrouter" if self.services is None else self.services.search_service()
        if service != "openrouter":
            return await self._search_with(service, question)
        try:
            found = await asyncio.wait_for(
                self._client.chat_text(
                    model=model,
                    system=_SEARCH_SYSTEM,
                    user=question,
                    plugins=SEARCH_PLUGINS,
                    reasoning_effort="none",
                    timeout_s=SEARCH_TIMEOUT_S,
                    max_tokens=SEARCH_MAX_TOKENS,
                ),
                SEARCH_TIMEOUT_S,
            )
        except TimeoutError:
            return self._failed("web_search", "timeout", "openrouter")
        except OpenRouterError as error:
            return self._failed("web_search", error.kind, "openrouter")
        ledger.add(
            model=found.model,
            cost_usd=found.cost_usd,
            prompt_tokens=found.prompt_tokens,
            completion_tokens=found.completion_tokens,
            ms=found.latency_ms,
        )
        data = {"answer": found.text, "sources": found.sources}
        return ToolResult("web_search", True, data, detail="openrouter")

    async def _search_with(self, service: str, question: str) -> ToolResult:
        assert self.services is not None
        try:
            service, found = await asyncio.wait_for(self.services.search(question), SEARCH_TIMEOUT_S)
        except TimeoutError:
            return self._failed("web_search", "timeout", service)
        except ServiceError as error:
            return self._failed("web_search", error.kind, service)
        return ToolResult("web_search", True, found, untrusted=True, detail=service)

    async def show(self, url: str) -> ToolResult:
        from ..services.media import preview

        if self.services is None:
            return self._failed("show", "unavailable")
        try:
            data = await preview(url, self.services.media_fetcher)
        except ServiceError as error:
            return self._failed("show", error.kind)
        return ToolResult("show", True, data, untrusted=True)

    async def web_read(self, url: str) -> ToolResult:
        """The page at `url` (`{url, title, text, truncated}`), read by the reader chosen. What it
        says is untrusted. "unavailable" without the services."""
        if self.services is None:
            return self._failed("web_read", "unavailable")
        if not self.services.enabled("read"):
            return self._failed("web_read", DISABLED)
        service = self.services.read_service()
        try:
            service, page = await asyncio.wait_for(self.services.read(url), READ_TIMEOUT_S)
        except TimeoutError:
            return self._failed("web_read", "timeout", service)
        except ServiceError as error:
            return self._failed("web_read", error.kind, service)
        return ToolResult("web_read", True, {"url": url, **page}, untrusted=True, detail=service)

    async def read_attachment(
        self, attachment: Attachment, question: str, ledger: Ledger, *, number: int = 1
    ) -> ToolResult:
        """Read a supplied file through its configured reader. `number` identifies it among the
        request's files. Paid vision calls use `ledger`; every result remains untrusted data."""
        kind = "pdf" if attachment.is_pdf else "image" if attachment.is_image else "file"
        if self.services is None:
            return self._failed("read_attachment", "unavailable", kind)
        if (attachment.is_pdf or attachment.is_image) and not self.services.enabled("pdf" if attachment.is_pdf else "vision"):
            return self._failed("read_attachment", DISABLED, kind)
        try:
            kind, found = await asyncio.wait_for(
                self.services.read_attachment(attachment, question, ledger), ATTACHMENT_TIMEOUT_S
            )
        except TimeoutError:
            return self._failed("read_attachment", "timeout", kind)
        except ServiceError as error:
            return self._failed("read_attachment", error.kind, kind)
        data = {"file": number, "kind": kind, **found}
        return ToolResult("read_attachment", True, data, untrusted=True, detail=kind)

    async def look(
        self,
        images: list[Attachment],
        question: str,
        ledger: Ledger,
        *,
        numbers: list[int] | None = None,
    ) -> ToolResult:
        """What the vision model sees in `images`, all of them in one call, for `question`.
        `numbers` are their places among the request's files (1, 2, ... by default)."""
        if self.services is None:
            return self._failed("read_attachment", "unavailable", "image")
        if not self.services.enabled("vision"):
            return self._failed("read_attachment", DISABLED, "image")
        try:
            seen = await asyncio.wait_for(
                self.services.look(images, question, ledger), ATTACHMENT_TIMEOUT_S
            )
        except TimeoutError:
            return self._failed("read_attachment", "timeout", "image")
        except ServiceError as error:
            return self._failed("read_attachment", error.kind, "image")
        files = numbers if numbers is not None else list(range(1, len(images) + 1))
        data = {"files": files, "kind": "image", **seen}
        return ToolResult("read_attachment", True, data, untrusted=True, detail="image")

    def turned_off(self) -> frozenset[str]:
        """The tools of Raphael's own that the owner turned off, so that the background worker is not
        offered them: `web_search`, `web_read`, and `read_attachment` when neither the images nor
        the PDFs may be read (with one of them on, the tool refuses the other kind itself)."""
        if self.services is None:
            return frozenset()
        off = {
            tool
            for tool, capability in (("web_search", "search"), ("web_read", "read"))
            if not self.services.enabled(capability)
        }
        if not self.services.enabled("vision") and not self.services.enabled("pdf"):
            off.add("read_attachment")
        return frozenset(off)

    # --- the time -------------------------------------------------------------------------------

    def local_time(self) -> ToolResult:
        """The date and the time on this computer, with the weekday in English and the offset."""
        now = self._now()
        offset = now.utcoffset()
        minutes = 0 if offset is None else round(offset.total_seconds() / 60)
        sign = "-" if minutes < 0 else "+"
        hours, rest = divmod(abs(minutes), 60)
        return ToolResult(
            "local_time",
            True,
            {
                "iso": now.isoformat(timespec="seconds"),
                "date": now.date().isoformat(),
                "weekday": _WEEKDAYS[now.weekday()],
                "time": now.strftime("%H:%M"),
                "utc_offset": f"UTC{sign}{hours:02d}:{rest:02d}",
            },
        )


class _NotFound(Exception):
    """The geocoder knows no such place."""
