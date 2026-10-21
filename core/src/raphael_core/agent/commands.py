"""The fixed commands: what the assistant does at once, with no language model.

A command is a small, known action on this computer: open one of nine web sites, change the volume,
pause or skip the media that plays, lock the screen, open an app of the Start menu by its name, and
(the one that asks first) copy text to the clipboard. The router picks one by its name; running it
is the device's work (`DeviceActions`), done on a worker thread. What Raphael says afterwards is
fixed too (《了。》).

`open_app` needs the app: `AppCatalog` reads the Start menu once, off the event loop, keeps the list
and finds an app in what the user said (`match_app`), or by the name a model extracted (`lookup_app`).
"""

import asyncio
import difflib
import logging
import re
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .device import DeviceActions, StartApp
from .policy import Tier

logger = logging.getLogger(__name__)

# What the router answers when the request is a command that no command here covers.
NONE = "none"
# One press of a volume key moves the volume by 2 %: five make a change that can be heard.
VOLUME_STEPS = 5
# The Start menu is read again after this long, and, when an app is asked for that is not in the
# list, when the list is at least this old (a program installed since).
APPS_TTL_S = 600.0
APPS_MISS_REFRESH_S = 30.0
# How many app names a model is shown to choose from.
MAX_APP_NAMES = 250
# An app name found only by being near another name: the least alike that is still taken.
_FUZZY_CUTOFF = 0.75


@dataclass(frozen=True)
class Command:
    name: str
    tier: Tier
    # What the router is told this command is for: one sentence in English.
    guidance: str
    # "site" (target: the URL), "key" (target: the key, pressed `times`), "lock", "app" (needs an
    # app_id) or "copy" (needs text).
    kind: str
    target: str = ""
    times: int = 1


def _site(name: str, url: str, guidance: str) -> Command:
    return Command(name, Tier.T1, guidance, "site", url)


def _key(name: str, key: str, guidance: str, times: int = 1) -> Command:
    return Command(name, Tier.T1, guidance, "key", key, times)


_COMMAND_LIST = (
    _site("open_youtube", "https://www.youtube.com/", "Open the YouTube website."),
    _site("open_gmail", "https://mail.google.com/", "Open Gmail, the email inbox, in the browser."),
    _site("open_github", "https://github.com/", "Open the GitHub website."),
    _site("open_google", "https://www.google.com/", "Open the Google search page."),
    _site("open_notion", "https://www.notion.so/", "Open Notion in the browser."),
    _site("open_chatgpt", "https://chatgpt.com/", "Open ChatGPT in the browser."),
    _site("open_claude", "https://claude.ai/", "Open Claude in the browser."),
    _site(
        "open_google_calendar", "https://calendar.google.com/", "Open Google Calendar, the calendar."
    ),
    _site("open_google_drive", "https://drive.google.com/", "Open Google Drive, the cloud storage."),
    _key("volume_up", "volume_up", "Raise the volume of the computer.", VOLUME_STEPS),
    _key("volume_down", "volume_down", "Lower the volume of the computer.", VOLUME_STEPS),
    _key("volume_mute", "volume_mute", "Mute the computer, or unmute it."),
    _key(
        "media_play_pause",
        "play_pause",
        "Pause the music or video that is playing, or play it again.",
    ),
    _key("media_next", "next_track", "Skip to the next song or video."),
    _key("media_previous", "previous_track", "Go back to the previous song or video."),
    Command("lock_screen", Tier.T1, "Lock the screen of the computer.", "lock"),
    Command(
        "open_app",
        Tier.T1,
        "Open an installed desktop application by its name, one that is not a website above.",
        "app",
    ),
    Command(
        "copy_text",
        Tier.T2,
        "Copy Raphael's last answer, or a text the speaker quotes, to the clipboard.",
        "copy",
    ),
)
COMMANDS: dict[str, Command] = {command.name: command for command in _COMMAND_LIST}
COMMAND_NAMES = tuple(COMMANDS)


async def run_command(command: Command, args: Mapping[str, Any], actions: DeviceActions) -> None:
    """Do `command` on the device, on a worker thread. `args` holds what the kind needs: the
    `app_id` of `open_app`, the `text` of `copy_text`. ValueError, before anything is done, if it is
    missing; what the device raises (OSError, say) comes out as it is."""
    kind = command.kind
    if kind == "site":
        await asyncio.to_thread(actions.open_url, command.target)
    elif kind == "key":
        await asyncio.to_thread(actions.press_key, command.target, command.times)
    elif kind == "lock":
        await asyncio.to_thread(actions.lock_screen)
    elif kind == "app":
        app_id = args.get("app_id")
        if not isinstance(app_id, str) or not app_id.strip():
            raise ValueError("open_app needs an app_id")
        await asyncio.to_thread(actions.launch_app, app_id)
    elif kind == "copy":
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("copy_text needs a text")
        await asyncio.to_thread(actions.copy_to_clipboard, text)
    else:  # a command added to the registry above without a way to run it
        raise ValueError(f"Cannot run a command of kind {kind!r}")


# --- finding an app ------------------------------------------------------------------------------


def _norm(text: str) -> str:
    """`text` as names are compared: compatibility-folded (full-width letters become ASCII), in
    lower case, with the runs of white space made one space."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _ascii_word_char(char: str) -> bool:
    return char.isascii() and char.isalnum()


def _contains(haystack: str, needle: str) -> bool:
    """Whether `needle` is in `haystack` as a name: where it starts or ends with a letter or digit
    of ASCII, it is not the middle of a longer word ("word" is not found in "password")."""
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before_ok = (
            start == 0 or not _ascii_word_char(needle[0]) or not _ascii_word_char(haystack[start - 1])
        )
        after_ok = (
            end == len(haystack)
            or not _ascii_word_char(needle[-1])
            or not _ascii_word_char(haystack[end])
        )
        if before_ok and after_ok:
            return True
        start = haystack.find(needle, start + 1)
    return False


_WORD = re.compile(r"[a-z0-9]+")
_MIN_TOKEN = 3


def match_app(utterance: str, apps: Sequence[StartApp]) -> StartApp | None:
    """The one app that `utterance` names, or None when it names none or cannot tell.

    The longest app name that is in the utterance wins. Failing that, a distinctive word of a name
    does ("edge" for Microsoft Edge), when only one app has the longest such word in the
    utterance. Names are compared in lower case and full-width letters count as ASCII ones.
    """
    spoken = _norm(utterance)
    if not spoken:
        return None
    best: StartApp | None = None
    best_length = 0
    for app in apps:
        name = _norm(app.name)
        if len(name) >= 2 and len(name) > best_length and _contains(spoken, name):
            best, best_length = app, len(name)
    if best is not None:
        return best
    words = set(_WORD.findall(spoken))
    scores: dict[int, list[StartApp]] = {}
    for app in apps:
        shared = [w for w in _WORD.findall(_norm(app.name)) if len(w) >= _MIN_TOKEN and w in words]
        if shared:
            scores.setdefault(max(len(w) for w in shared), []).append(app)
    if not scores:
        return None
    winners = scores[max(scores)]
    return winners[0] if len(winners) == 1 else None


def lookup_app(name: str, apps: Sequence[StartApp]) -> StartApp | None:
    """The app that `name` (a name a model gave) stands for: the one of that name; else the one
    whose name contains it or is contained in it, the nearest in length; else the one with the
    most alike name, if it is alike enough. None when there is none."""
    wanted = _norm(name)
    if len(wanted) < 2:
        return None
    named = [(app, _norm(app.name)) for app in apps]
    for app, spelled in named:
        if spelled == wanted:
            return app
    related = [
        (abs(len(spelled) - len(wanted)), index, app)
        for index, (app, spelled) in enumerate(named)
        if _contains(spelled, wanted) or (len(spelled) >= _MIN_TOKEN and _contains(wanted, spelled))
    ]
    if related:
        return min(related, key=lambda entry: entry[:2])[2]
    near = difflib.get_close_matches(
        wanted, [spelled for _, spelled in named], n=1, cutoff=_FUZZY_CUTOFF
    )
    return next((app for app, spelled in named if near and spelled == near[0]), None)


class AppCatalog:
    """The apps of the Start menu, read once (PowerShell takes seconds) and kept.

    The first question reads them on a worker thread; two asked at once share the read. The list is
    read again when it is `APPS_TTL_S` old, and when an app is asked for that it lacks and it is
    `APPS_MISS_REFRESH_S` old. A list that cannot be read is empty, is logged by the type of the
    error and is not kept: the next question tries again.
    """

    def __init__(
        self, actions: DeviceActions, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._actions = actions
        self._clock = clock
        self._apps: list[StartApp] = []
        self._loaded_at: float | None = None
        self._lock = asyncio.Lock()

    def _age(self) -> float | None:
        return None if self._loaded_at is None else self._clock() - self._loaded_at

    async def apps(self, *, refresh_after_s: float = APPS_TTL_S) -> list[StartApp]:
        """The apps, read now if they were never read or are `refresh_after_s` old."""
        async with self._lock:
            age = self._age()
            if age is None or age >= refresh_after_s:
                try:
                    self._apps = await asyncio.to_thread(self._actions.list_start_apps)
                    self._loaded_at = self._clock()
                except Exception as exc:  # PowerShell missing, too slow, unreadable output
                    logger.warning(
                        "Could not list the apps of the Start menu: %s", type(exc).__name__
                    )
                    self._apps, self._loaded_at = [], None
            return list(self._apps)

    async def find(self, utterance: str) -> StartApp | None:
        """The app `utterance` names (`match_app`), after reading the list again if it lacks it
        and has gone old."""
        hit = match_app(utterance, await self.apps())
        if hit is None and self._missed():
            hit = match_app(utterance, await self.apps(refresh_after_s=APPS_MISS_REFRESH_S))
        return hit

    async def lookup(self, name: str) -> StartApp | None:
        """The app a model named (`lookup_app`), with the same fresh read on a miss."""
        hit = lookup_app(name, await self.apps())
        if hit is None and self._missed():
            hit = lookup_app(name, await self.apps(refresh_after_s=APPS_MISS_REFRESH_S))
        return hit

    def _missed(self) -> bool:
        age = self._age()
        return age is not None and age >= APPS_MISS_REFRESH_S

    async def names(self) -> list[str]:
        """The names of the apps, each once, for a model to choose from: MAX_APP_NAMES at most."""
        seen: set[str] = set()
        names: list[str] = []
        for app in await self.apps():
            key = _norm(app.name)
            if key not in seen:
                seen.add(key)
                names.append(app.name)
            if len(names) == MAX_APP_NAMES:
                break
        return names
