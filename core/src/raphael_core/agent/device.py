"""What the assistant does to this computer, behind an interface a test can replace.

`DeviceActions` is everything the assistant does that only Windows can do: open a page, press a
media key, lock the screen, list and start the apps of the Start menu, and put text on the
clipboard. `WindowsActions` does them with ctypes, `os.startfile` and PowerShell, and imports
nothing of Windows at import time; the tests give it fakes for all of it. `NullActions` does
nothing, for a core that runs without a device (the fake LLM of the end-to-end runs).

Every method blocks for a moment or more (PowerShell takes seconds), so the assistant calls them
on a worker thread, never on the event loop.
"""

import base64
import binascii
import ctypes
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

# The virtual-key codes of the keys that change the volume and drive the media that plays.
VIRTUAL_KEYS = {
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "volume_mute": 0xAD,
    "play_pause": 0xB3,
    "next_track": 0xB0,
    "previous_track": 0xB1,
}
KEYEVENTF_KEYUP = 0x0002
# No console window flashes up for the helper processes.
CREATE_NO_WINDOW = 0x08000000
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# Get-StartApps takes a few seconds the first time; this is the longest it is waited for.
START_APPS_TIMEOUT_S = 30.0
# Another program may hold the clipboard for a moment: it is tried this many times, this far apart.
CLIPBOARD_ATTEMPTS = 10
CLIPBOARD_RETRY_S = 0.05
_MAX_KEY_PRESSES = 20

# The names and ids of the Start menu apps, as JSON in base64: the bytes are plain ASCII, so the
# console's code page cannot garble a Chinese or Japanese name on the way out of PowerShell.
_LIST_APPS_SCRIPT = (
    "Get-StartApps | ConvertTo-Json -Compress | ForEach-Object "
    "{ [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($_)) }"
)
_WEB_PAGE = re.compile(r"https://[^\s\x00-\x1f]+")
_NOT_AN_ID = re.compile(r"[\x00-\x1f]")
# The Start menu also holds links to web pages and settings, with an id such as "https://..." or
# "ms-settings:about"; explorer.exe does not start those as apps. (An id such as "C:\..." is a
# path, and a colon followed by a backslash is no link.)
_A_LINK = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:(?!\\)")


@dataclass(frozen=True)
class StartApp:
    name: str  # as the Start menu shows it, in the language of Windows
    app_id: str  # what `explorer.exe shell:AppsFolder\<id>` starts


class DeviceActions(Protocol):
    def open_url(self, url: str) -> None:
        """Open a web page (`https://`) in the default browser."""
        ...

    def press_key(self, name: str, times: int = 1) -> None:
        """Press and release a key of VIRTUAL_KEYS, `times` times."""
        ...

    def lock_screen(self) -> None: ...

    def list_start_apps(self) -> list[StartApp]:
        """The apps of the Start menu. OSError if they cannot be listed."""
        ...

    def launch_app(self, app_id: str) -> None:
        """Start an app of the Start menu by its id."""
        ...

    def copy_to_clipboard(self, text: str) -> None:
        """Replace the clipboard with `text`. OSError if the clipboard cannot be written."""
        ...


class NullActions:
    """A device that does nothing."""

    def open_url(self, url: str) -> None:
        pass

    def press_key(self, name: str, times: int = 1) -> None:
        pass

    def lock_screen(self) -> None:
        pass

    def list_start_apps(self) -> list[StartApp]:
        return []

    def launch_app(self, app_id: str) -> None:
        pass

    def copy_to_clipboard(self, text: str) -> None:
        pass


def _bind_user32() -> Any:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    user32.keybd_event.restype = None
    user32.LockWorkStation.argtypes = []
    user32.LockWorkStation.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    return user32


def _bind_kernel32() -> Any:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    return kernel32


class WindowsActions:
    """`DeviceActions` on Windows. The libraries it calls are loaded when first needed, and can be
    given instead (`user32`, `kernel32`, `startfile`, `run`, `popen`, `sleep`) so that a test
    presses nothing."""

    def __init__(
        self,
        *,
        user32: Any = None,
        kernel32: Any = None,
        startfile: Callable[[str], None] | None = None,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._user32 = user32
        self._kernel32 = kernel32
        self._startfile = startfile
        self._run = run
        self._popen = popen
        self._sleep = sleep

    @property
    def user32(self) -> Any:
        if self._user32 is None:
            self._user32 = _bind_user32()
        return self._user32

    @property
    def kernel32(self) -> Any:
        if self._kernel32 is None:
            self._kernel32 = _bind_kernel32()
        return self._kernel32

    def open_url(self, url: str) -> None:
        # startfile runs whatever it is given (a program, a document, any protocol), so nothing
        # but a web page goes to it.
        if not _WEB_PAGE.fullmatch(url):
            raise ValueError("Only an https:// page can be opened")
        startfile = self._startfile or os.startfile  # type: ignore[attr-defined]
        startfile(url)

    def press_key(self, name: str, times: int = 1) -> None:
        if name not in VIRTUAL_KEYS:
            raise ValueError(f"Unknown key {name!r}")
        if not 1 <= times <= _MAX_KEY_PRESSES:
            raise ValueError(f"A key is pressed between 1 and {_MAX_KEY_PRESSES} times")
        code = VIRTUAL_KEYS[name]
        for _ in range(times):
            self.user32.keybd_event(code, 0, 0, 0)
            self.user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)

    def lock_screen(self) -> None:
        if not self.user32.LockWorkStation():
            raise OSError("The screen could not be locked")

    def list_start_apps(self) -> list[StartApp]:
        completed = self._run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _LIST_APPS_SCRIPT],
            capture_output=True,
            timeout=START_APPS_TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
        if completed.returncode != 0:
            raise OSError(f"Get-StartApps ended with {completed.returncode}")
        try:
            output = completed.stdout.strip()
            if not output:
                return []
            listed = json.loads(base64.b64decode(output, validate=True).decode("utf-8"))
        except (binascii.Error, ValueError):  # not base64, not UTF-8, not JSON
            raise OSError("The list of apps could not be read") from None
        if isinstance(listed, dict):  # ConvertTo-Json writes one app as an object
            listed = [listed]
        if not isinstance(listed, list):
            raise OSError("The list of apps could not be read")
        apps = []
        for entry in listed:
            name = entry.get("Name") if isinstance(entry, dict) else None
            app_id = entry.get("AppID") if isinstance(entry, dict) else None
            if isinstance(name, str) and isinstance(app_id, str) and name.strip() and app_id.strip():
                if not _A_LINK.match(app_id.strip()):
                    apps.append(StartApp(name.strip(), app_id.strip()))
        return apps

    def launch_app(self, app_id: str) -> None:
        if not app_id.strip() or _NOT_AN_ID.search(app_id):
            raise ValueError("Not an app id")
        # Not waited for: explorer.exe ends with an exit code that means nothing here.
        self._popen(
            ["explorer.exe", f"shell:AppsFolder\\{app_id.strip()}"],
            creationflags=CREATE_NO_WINDOW,
        )

    def copy_to_clipboard(self, text: str) -> None:
        data = (text + "\0").encode("utf-16-le")
        kernel32, user32 = self.kernel32, self.user32
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise OSError("No memory for the clipboard")
        try:
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise OSError("The clipboard memory could not be locked")
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                kernel32.GlobalUnlock(handle)
            self._open_clipboard()
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                    raise OSError("The clipboard did not take the text")
                handle = None  # the clipboard owns the memory now
            finally:
                user32.CloseClipboard()
        finally:
            if handle:
                kernel32.GlobalFree(handle)

    def _open_clipboard(self) -> None:
        for attempt in range(CLIPBOARD_ATTEMPTS):
            if self.user32.OpenClipboard(None):
                return
            if attempt < CLIPBOARD_ATTEMPTS - 1:
                self._sleep(CLIPBOARD_RETRY_S)
        raise OSError("The clipboard is busy")
