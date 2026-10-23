"""Settings shared by the whole brain, kept in the database as JSON text.

Only keys in DEFAULTS exist. A key nobody has set reads as its default. Most keys are only
type-checked; the speech-to-text engine and the local model must also be one of their listed
values, `languages` a valid selection (see raphael_core.languages), `model_favorites` the starred
model ids of each role (at most 50 each), and the models and the assistant's city a text of some
substance (saved without the spaces around it; a model id has none inside). The services of the
tools (`web_search_service`, `web_read_service`, `pdf_service`) must be one of their listed values,
`vision_model` a model id or "" (the answer model), and `searxng_url` an http(s) address or "".
Raphael's voice is `tts_service` (the provider: "fish"), `tts_fish_voice` (the 32 hexadecimal
characters of the owner's Fish Audio voice model, or "": it is the owner's to fill in) and
`tts_fish_model` (a short token). `tts_language` is the language the voice speaks ("ja", the only
one the owner's voice model speaks; the seam for voices in other languages).

Each capability of the tools has a switch of its own: `web_search_enabled`, `web_read_enabled`,
`vision_enabled`, `pdf_enabled` (all on until the owner turns one off) and `tts_enabled` (off until
the owner turns Raphael's voice on). A capability that is off is not used at all, whichever service
is chosen for it. Before the switches, "no voice" was a provider, `tts_service` "none": a saved
"none" now reads as the default provider with `tts_enabled` off, and a saved "fish" with a voice id
(and no `tts_enabled` saved yet) reads as `tts_enabled` on, so a database made before them keeps
working without being rewritten.
Which models may be
chosen is for the brain to say, which knows OpenRouter's list of them (brain/catalog.py): here a
model is only an id.

A key that an older version saved but that is no longer a setting stays in the table and is never
read: `all` lists only the keys of DEFAULTS, and `get` and `set` refuse the others, so nothing has
to be rewritten at start-up. Such keys are `agent_router` (whether Jev or the language model
routed the assistant's requests; Jev always does now, so a saved "luna" changes nothing),
`learn_corrections` (whether the words corrected after a paste were learned; they always are now,
so a saved false changes nothing) and `language`, the single language that `languages` replaced
before the first release.
"""

import copy
import json
import re
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from ..device.models import LOCAL_STT_MODELS
from ..languages import DEFAULT_LANGUAGES, checked_languages

DEFAULTS: dict[str, Any] = {
    "monthly_limit_usd": 10.0,
    # Chosen from the cleanup eval of 2026-10-18 for its cost and its speed (~1.25 s on average).
    # The cases and the prompt have changed since, so no pass rate is claimed for it: compare the
    # models again with `python -m raphael_core.evaluate.cleanup`.
    "cleanup_model": "openai/gpt-6-luna",
    # The dictation languages, in the user's order of priority (raphael_core.languages).
    "languages": list(DEFAULT_LANGUAGES),
    "history_retention_days": 30,
    # Speech-to-text: "cloud" (OpenRouter, with the local engine as the fallback when there is one)
    # or "local" (the engine on this computer only; no audio leaves it). Read at the start of each
    # dictation.
    "stt_engine": "cloud",
    # Which of the catalogue's models (device/models.py) the local engine is. The one Raphael has
    # always used, so that nobody who runs locally needs a new download.
    "stt_local_model": "qwen3-asr",
    # The most accurate on the samples tried live on 2026-10-18 (gpt-transcribe returned nothing
    # for an English clip and garbled Chinese).
    "stt_cloud_model": "elevenlabs/scribe-v2",
    # The voice assistant (raphael_core.agent). The model that words its answers, and that decides
    # what a request is when Jev (agent/routing.py, not a setting) fails or is unsure.
    "agent_model": "openai/gpt-6-luna",
    # Where the weather is looked up when the user does not say.
    "agent_city": "台北",
    # The most one background task may spend, in US$ (one of WORKER_COST_CAPS).
    "worker_cost_cap_usd": 0.20,
    # The models the user starred in the pickers, per role (the page's names for them), in the order
    # they were added. The page shows the ones the catalog still has at the top of its list; the
    # others stay here, in case the catalog gets them back.
    "model_favorites": {"cleanup": [], "answer": [], "stt": []},
    # The services Raphael's tools stand on (raphael_core.services), each chosen by the owner. The
    # web search: "openrouter" is the answer model with OpenRouter's `web` plugin, as it always was;
    # the others are search engines that return results for the answer model to word.
    "web_search_service": "openrouter",
    # Reading a page: "builtin" fetches it from this computer and turns its HTML into text here, at
    # no cost; the others are services that read it for Raphael.
    "web_read_service": "builtin",
    # The model that reads an image, or "" for the answer model (`agent_model`).
    "vision_model": "",
    # Reading a PDF: "builtin" takes its text here (pypdf) and shows the vision model the pages that
    # have none; "openrouter" sends the whole file to the vision model, through OpenRouter's
    # `file-parser` plugin.
    "pdf_service": "builtin",
    # The address of the owner's own SearXNG, for `web_search_service` "searxng"; "" when none.
    "searxng_url": "",
    # Whether each capability is used at all (the switch of its row in Settings › 拉斐爾 › 服務). The
    # search, the reading of pages, the reading of images and the reading of PDFs are on by default;
    # Raphael's voice is off until the owner turns it on.
    "web_search_enabled": True,
    "web_read_enabled": True,
    "vision_enabled": True,
    "pdf_enabled": True,
    "tts_enabled": False,
    # Raphael's voice (raphael_core.services.tts): the service that speaks his answers aloud when
    # `tts_enabled` is on. Fish Audio's voice is the owner's own clone, chosen by the id of its
    # model, which is not in the repository: "" until the owner fills it in.
    "tts_service": "fish",
    "tts_fish_voice": "",
    "tts_fish_model": "s2.1-pro-free",
    # The language Raphael speaks aloud, whatever the language on screen: the owner's voice model
    # speaks Japanese only, so what is shown in another language is spoken in a Japanese rendering
    # (see raphael_core.agent.persona.spoken_line). No page offers it: a seam for other voices.
    "tts_language": "ja",
}

# The longest model id or city name taken: far more than any real one, short of an accident.
AGENT_TEXT_MAX_CHARS = 100
# The roles that have favourites, and the most that one of them holds.
FAVORITE_ROLES = ("cleanup", "answer", "stt")
MAX_FAVORITES = 50
STT_ENGINES = ("cloud", "local")
# The services each tool may stand on (raphael_core.services.catalog says what each needs).
WEB_SEARCH_SERVICES = ("openrouter", "brave", "tavily", "exa", "searxng")
WEB_READ_SERVICES = ("builtin", "jina", "firecrawl", "tavily")
PDF_SERVICES = ("builtin", "openrouter")
# The services that may speak Raphael's answers. "No voice" is `tts_enabled` off, not a service.
TTS_SERVICES = ("fish",)
# The languages a voice may speak (for now the owner's only).
TTS_LANGUAGES = ("ja",)
# The switches of the capabilities (the keys of DEFAULTS that are on or off).
ENABLED_SETTINGS = (
    "web_search_enabled",
    "web_read_enabled",
    "vision_enabled",
    "pdf_enabled",
    "tts_enabled",
)
# A Fish Audio voice model is named by 32 hexadecimal characters; its model by a short token.
FISH_VOICE_PATTERN = re.compile(r"[0-9a-f]{32}")
FISH_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
# The longest address of a SearXNG taken.
URL_MAX_CHARS = 300
# What one background task may cost, as the settings page offers it.
WORKER_COST_CAPS = (0.10, 0.20, 0.50, 1.00)
_CHOICES: dict[str, tuple[Any, ...]] = {
    "worker_cost_cap_usd": WORKER_COST_CAPS,
    "stt_engine": STT_ENGINES,
    "stt_local_model": tuple(entry.name for entry in LOCAL_STT_MODELS),
    "web_search_service": WEB_SEARCH_SERVICES,
    "web_read_service": WEB_READ_SERVICES,
    "pdf_service": PDF_SERVICES,
    "tts_service": TTS_SERVICES,
    "tts_language": TTS_LANGUAGES,
}
# The settings that hold the id of an OpenRouter model.
MODEL_SETTINGS = ("cleanup_model", "stt_cloud_model", "agent_model")
_TRIMMED = (*MODEL_SETTINGS, "agent_city")


def _checked(key: str, value: Any) -> Any:
    """The value to save, or TypeError if it does not have the type of the key's default.

    A whole number is accepted for a float setting and saved as a float. Types are compared
    exactly, so True is not a number here.
    """
    expected = type(DEFAULTS[key])
    if type(value) is expected:
        return value
    if expected is float and type(value) is int:
        return float(value)
    raise TypeError(f"Setting {key!r} must be a {expected.__name__}, not {type(value).__name__}")


# The settings that may also be None: the local model, when there is none to fall back on.
_NULLABLE = ("stt_local_model",)


def _allowed(key: str, value: Any) -> Any:
    """`value`, or ValueError if the key has a list of choices and `value` is not on it."""
    choices = _CHOICES.get(key)
    if choices is not None and value not in choices:
        raise ValueError(f"Setting {key!r} must be one of {', '.join(str(c) for c in choices)}")
    return value


def _trimmed(key: str, value: str) -> str:
    """`value` without the spaces around it, or ValueError if that leaves nothing or too much.

    A model id also has no space inside it. A city may: "New York".
    """
    text = value.strip()
    if not text:
        raise ValueError(f"Setting {key!r} must not be blank")
    if len(text) > AGENT_TEXT_MAX_CHARS:
        raise ValueError(f"Setting {key!r} must be at most {AGENT_TEXT_MAX_CHARS} characters")
    if key in (*MODEL_SETTINGS, "model_favorites", "vision_model") and any(char.isspace() for char in text):
        raise ValueError(f"Setting {key!r} must not contain spaces")
    return text


def _url(key: str, value: str) -> str:
    """`value` without the spaces around it: "" for none, else an http or https address with a
    host, of at most URL_MAX_CHARS and with no space inside. ValueError for anything else."""
    text = value.strip()
    if not text:
        return ""
    if len(text) > URL_MAX_CHARS or any(char.isspace() for char in text):
        raise ValueError(f"Setting {key!r} must be an address of at most {URL_MAX_CHARS} characters")
    try:
        parsed = urlsplit(text)
        host = parsed.hostname
    except ValueError:
        host = None
    if host is None or parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Setting {key!r} must be an http or https address")
    return text


def _optional_model(key: str, value: str) -> str:
    """`value` without the spaces around it: "" (the answer model), or a model id (`_trimmed`)."""
    text = value.strip()
    return _trimmed(key, text) if text else ""


def _fish_voice(key: str, value: str) -> str:
    """`value` without the spaces around it, in lower case: "" (none yet) or the 32 hexadecimal
    characters of a Fish Audio voice model. ValueError for anything else."""
    text = value.strip().lower()
    if text and not FISH_VOICE_PATTERN.fullmatch(text):
        raise ValueError(f"Setting {key!r} must be empty or 32 hexadecimal characters")
    return text


def _fish_model(key: str, value: str) -> str:
    """`value` without the spaces around it: a short token such as "s2.1-pro-free". ValueError for
    a blank text, one with a space or other mark in it, or one of more than 40 characters."""
    text = value.strip()
    if not FISH_MODEL_PATTERN.fullmatch(text):
        raise ValueError(f"Setting {key!r} must be a short token of letters, digits, . _ and -")
    return text


def _favorites(value: dict) -> dict[str, list[str]]:
    """The favourites to save: every role with its list, each id once and in order.

    TypeError for a list that is not a list of texts, ValueError for a role that does not exist,
    an id that is blank, long or has a space in it, and more than MAX_FAVORITES in one list.
    """
    unknown = [role for role in value if role not in FAVORITE_ROLES]
    if unknown:
        raise ValueError(f"Setting 'model_favorites' has no role {unknown[0]!r}")
    result: dict[str, list[str]] = {}
    for role in FAVORITE_ROLES:
        ids = value.get(role, [])
        if type(ids) is not list or any(type(i) is not str for i in ids):
            raise TypeError(f"Favourites of {role!r} must be a list of model ids")
        kept = list(dict.fromkeys(_trimmed("model_favorites", i) for i in ids))
        if len(kept) > MAX_FAVORITES:
            raise ValueError(f"At most {MAX_FAVORITES} favourite models for {role!r}")
        result[role] = kept
    return result


def _no_favorites() -> dict[str, list[str]]:
    return copy.deepcopy(DEFAULTS["model_favorites"])


def _read_favorites(saved: Any) -> dict[str, list[str]]:
    """The favourites as saved; a saved value that is damaged reads as no favourites."""
    try:
        return _favorites(saved) if type(saved) is dict else _no_favorites()
    except (TypeError, ValueError):
        return _no_favorites()


def checked(key: str, value: Any) -> tuple[str, Any]:
    """The key and the value that setting `key` to `value` saves, or the error `SettingsStore.set`
    raises for them (see there). Nothing is saved."""
    if key == "languages":
        return key, checked_languages(key, value)
    if value is None and key in _NULLABLE:
        return key, None
    value = _allowed(key, _checked(key, value))
    if key in _TRIMMED:
        value = _trimmed(key, value)
    if key == "model_favorites":
        value = _favorites(value)
    if key == "searxng_url":
        value = _url(key, value)
    if key == "vision_model":
        value = _optional_model(key, value)
    if key == "tts_fish_voice":
        value = _fish_voice(key, value)
    if key == "tts_fish_model":
        value = _fish_model(key, value)
    return key, value


class SettingsStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def _saved(self, key: str) -> Any:
        """The saved value of `key`, or None when it has none."""
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else json.loads(row["value"])

    def _languages(self) -> list[str]:
        """The saved selection, else the default. A saved selection that is no longer valid reads
        as the default."""
        saved = self._saved("languages")
        if saved is None:
            return list(DEFAULT_LANGUAGES)
        try:
            return checked_languages("languages", saved)
        except (TypeError, ValueError):
            return list(DEFAULT_LANGUAGES)

    def _voice_was_on(self) -> bool:
        """Whether Raphael's voice was on in a database made before `tts_enabled`: a saved
        `tts_service` "fish" together with a voice id (a voice that cannot speak was off in
        effect). Used only while `tts_enabled` itself has never been saved."""
        return self._saved("tts_service") == "fish" and bool(self._saved("tts_fish_voice"))

    def get(self, key: str) -> Any:
        """The saved value, else the default (`tts_service` and `tts_enabled` read the older voice
        setting as the module says). KeyError for a key that is not a setting."""
        default = DEFAULTS[key]
        if key == "languages":
            return self._languages()
        saved = self._saved(key)
        if key == "model_favorites":
            return _read_favorites(saved)
        if key == "tts_service":
            return saved if saved in TTS_SERVICES else default  # a saved "none" was "no voice"
        if key == "tts_enabled" and saved is None:
            return self._voice_was_on()
        if saved is None and key in _NULLABLE and self._conn.execute(
            "SELECT 1 FROM settings WHERE key = ?", (key,)
        ).fetchone():
            return None  # saved as "none", which is not the same as never saved
        return default if saved is None else saved

    def set(self, key: str, value: Any) -> None:
        """Save a value.

        Raises KeyError for an unknown key, TypeError for a value of the wrong type and ValueError
        for a number JSON cannot hold (nan, inf), a value that is not one of the key's choices, a
        blank or absurd text or a selection of languages that is not valid.
        """
        key, value = checked(key, value)
        if key == "stt_engine" and value == "local" and self.get("stt_local_model") is None:
            raise ValueError("Setting 'stt_local_model' must name a model for the local engine")
        if key == "stt_local_model" and value is None and self.get("stt_engine") == "local":
            raise ValueError("Setting 'stt_local_model' must name a model while the engine is local")
        encoded = json.dumps(value, allow_nan=False)
        with self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, encoded),
            )

    def all(self) -> dict[str, Any]:
        """Every setting: the defaults with the saved values on top."""
        values = dict(DEFAULTS)
        for row in self._conn.execute("SELECT key, value FROM settings"):
            if row["key"] in values:
                values[row["key"]] = json.loads(row["value"])
        values["model_favorites"] = _read_favorites(values["model_favorites"])
        values["languages"] = self._languages()
        values["tts_service"] = self.get("tts_service")
        values["tts_enabled"] = self.get("tts_enabled")
        return values
