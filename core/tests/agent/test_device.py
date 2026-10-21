"""Tests for the actions on this computer, with fakes in place of Windows.

`WindowsActions` is handed the user32 and kernel32 libraries, `os.startfile` and `subprocess`, so
none of these tests presses a key, locks a screen, opens a page or touches the clipboard.
"""

import base64
import ctypes
import json
import subprocess
from pathlib import Path

import pytest

from raphael_core.agent.device import (
    CF_UNICODETEXT,
    CREATE_NO_WINDOW,
    KEYEVENTF_KEYUP,
    VIRTUAL_KEYS,
    NullActions,
    StartApp,
    WindowsActions,
)


class FakeUser32:
    def __init__(self, kernel32=None):
        self.events = []  # (vk, flags)
        self.locked = 0
        self.calls = []
        self.open_failures = 0  # the next OpenClipboard calls fail
        self.set_fails = False
        self.clipboard = None  # (format, bytes)
        self.kernel32 = kernel32

    def keybd_event(self, vk, scan, flags, extra):
        self.events.append((vk, flags))

    def LockWorkStation(self):
        self.locked += 1
        return 1

    def OpenClipboard(self, owner):
        self.calls.append("open")
        if self.open_failures:
            self.open_failures -= 1
            return 0
        return 1

    def EmptyClipboard(self):
        self.calls.append("empty")
        self.clipboard = None
        return 1

    def SetClipboardData(self, fmt, handle):
        self.calls.append("set")
        if self.set_fails:
            return 0
        self.clipboard = (fmt, self.kernel32.read(handle))
        return handle

    def CloseClipboard(self):
        self.calls.append("close")
        return 1


class FakeKernel32:
    """Memory that is real, so that the actions' memmove writes somewhere."""

    def __init__(self):
        self.buffers = {}
        self.freed = []
        self.locked = []
        self.unlocked = []
        self.fail_alloc = False
        self.fail_lock = False

    def GlobalAlloc(self, flags, size):
        if self.fail_alloc:
            return 0
        buffer = ctypes.create_string_buffer(size)
        address = ctypes.addressof(buffer)
        self.buffers[address] = (buffer, size)
        return address

    def GlobalLock(self, handle):
        self.locked.append(handle)
        return 0 if self.fail_lock else handle

    def GlobalUnlock(self, handle):
        self.unlocked.append(handle)
        return 1

    def GlobalFree(self, handle):
        self.freed.append(handle)
        return 0

    def read(self, handle):
        buffer, size = self.buffers[handle]
        return ctypes.string_at(ctypes.addressof(buffer), size)


class Rig:
    def __init__(self, start_menu=()):
        self.kernel32 = FakeKernel32()
        self.user32 = FakeUser32(self.kernel32)
        self.started = []
        self.ran = []
        self.popened = []
        self.sleeps = []
        self.run_result = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        self.actions = WindowsActions(
            user32=self.user32,
            kernel32=self.kernel32,
            startfile=self.started.append,
            run=self._run,
            popen=self._popen,
            sleep=self.sleeps.append,
            start_menu=start_menu,
        )

    def _run(self, args, **kwargs):
        self.ran.append((args, kwargs))
        return self.run_result

    def _popen(self, args, **kwargs):
        self.popened.append((args, kwargs))


@pytest.fixture
def rig():
    return Rig()  # a Start menu with no folders: the tests never read the real one


def apps_output(apps):
    """What the PowerShell command prints: the apps as JSON, in base64."""
    return base64.b64encode(json.dumps(apps, ensure_ascii=False).encode("utf-8")) + b"\r\n"


# --- keys ----------------------------------------------------------------------------------------


def test_the_virtual_key_codes_are_the_windows_ones():
    assert VIRTUAL_KEYS == {
        "volume_up": 0xAF,
        "volume_down": 0xAE,
        "volume_mute": 0xAD,
        "play_pause": 0xB3,
        "next_track": 0xB0,
        "previous_track": 0xB1,
    }
    assert KEYEVENTF_KEYUP == 2


@pytest.mark.parametrize("name", list(VIRTUAL_KEYS))
def test_a_key_is_pressed_and_released(rig, name):
    rig.actions.press_key(name)
    assert rig.user32.events == [(VIRTUAL_KEYS[name], 0), (VIRTUAL_KEYS[name], KEYEVENTF_KEYUP)]


def test_a_key_pressed_three_times_is_pressed_and_released_three_times(rig):
    rig.actions.press_key("volume_up", 3)
    assert rig.user32.events == [(0xAF, 0), (0xAF, 2)] * 3


@pytest.mark.parametrize("bad", [("enter", 1), ("volume_up", 0), ("volume_up", -1)])
def test_a_key_that_is_none_of_them_or_pressed_no_times_is_refused(rig, bad):
    with pytest.raises(ValueError):
        rig.actions.press_key(*bad)
    assert rig.user32.events == []


# --- locking and opening ---------------------------------------------------------------------------


def test_the_screen_is_locked_with_the_windows_call(rig):
    rig.actions.lock_screen()
    assert rig.user32.locked == 1


def test_a_page_is_opened_with_startfile(rig):
    rig.actions.open_url("https://www.youtube.com/")
    assert rig.started == ["https://www.youtube.com/"]


@pytest.mark.parametrize(
    "url",
    ["", "file:///C:/Windows/notepad.exe", "C:\\Windows\\notepad.exe", "calc.exe", "javascript:1",
     "ms-settings:", "ftp://example.com/", "https://", "https:// evil", "https://a\nb"],
)  # fmt: skip
def test_only_a_web_page_is_opened_since_startfile_would_run_anything(rig, url):
    with pytest.raises(ValueError):
        rig.actions.open_url(url)
    assert rig.started == []


@pytest.mark.parametrize(
    "url",
    ["https://localhost/", "https://127.0.0.1/", "https://192.168.1.1/", "https://printer.local/",
     "https://example.com/setup.exe", "https://user:pw@example.com/"],
)  # fmt: skip
def test_a_page_of_this_computer_or_its_network_or_a_program_is_not_opened(rig, url):
    with pytest.raises(ValueError):
        rig.actions.open_url(url)
    assert rig.started == []


def test_an_http_page_is_opened_when_it_is_given(rig):
    # The assistant gives one only when the speaker said "http://" outright.
    rig.actions.open_url("http://example.com/")
    assert rig.started == ["http://example.com/"]


def test_a_settings_page_of_the_list_is_opened_with_startfile(rig):
    rig.actions.open_settings("ms-settings:bluetooth")
    assert rig.started == ["ms-settings:bluetooth"]


@pytest.mark.parametrize(
    "uri",
    ["", "ms-settings:", "ms-settings:nosuchpage", "ms-settings:bluetooth?x=1", "calc.exe",
     "https://example.com/", "ms-settings:../x", "MS-SETTINGS:BLUETOOTH", "ms-settings:bluetooth\n"],
)  # fmt: skip
def test_only_a_page_of_the_list_of_settings_is_opened(rig, uri):
    with pytest.raises(ValueError):
        rig.actions.open_settings(uri)
    assert rig.started == []


# --- the apps in the Start menu --------------------------------------------------------------------


def test_the_start_menu_apps_are_listed_with_powershell_in_a_window_nobody_sees(rig):
    rig.run_result = subprocess.CompletedProcess(
        [], 0, stdout=apps_output([{"Name": "計算機", "AppID": "Microsoft.Calc_8wek!App"}])
    )
    apps = rig.actions.list_start_apps()
    assert apps == [StartApp("計算機", "Microsoft.Calc_8wek!App")]
    args, kwargs = rig.ran[0]
    assert args[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    assert "Get-StartApps | ConvertTo-Json" in args[4]
    assert "ToBase64String" in args[4]  # so the console's code page cannot garble the names
    assert kwargs["creationflags"] == CREATE_NO_WINDOW == 0x08000000
    assert kwargs["capture_output"] is True and kwargs["timeout"] > 0


def test_one_app_is_a_json_object_and_not_a_list(rig):
    rig.run_result = subprocess.CompletedProcess(
        [], 0, stdout=apps_output({"Name": "Notepad", "AppID": "notepad.exe"})
    )
    assert rig.actions.list_start_apps() == [StartApp("Notepad", "notepad.exe")]


def test_apps_without_a_name_or_an_id_are_left_out(rig):
    rig.run_result = subprocess.CompletedProcess(
        [],
        0,
        stdout=apps_output(
            [
                {"Name": "Good", "AppID": "good"},
                {"Name": "", "AppID": "x"},
                {"Name": "No id", "AppID": None},
                {"Name": 5, "AppID": "y"},
                "text",
                {"Name": "  Padded  ", "AppID": " pad "},
            ]
        ),
    )
    assert rig.actions.list_start_apps() == [StartApp("Good", "good"), StartApp("Padded", "pad")]


def test_a_link_in_the_start_menu_is_not_an_app(rig):
    rig.run_result = subprocess.CompletedProcess(
        [],
        0,
        stdout=apps_output(
            [
                {"Name": "ApowerREC on the Web", "AppID": "https://www.apowersoft.com"},
                {"Name": "Docs", "AppID": "ms-settings:about"},
                {"Name": "ApowerREC", "AppID": "{7C5A}\\ApowerREC\\ApowerREC.exe"},
            ]
        ),
    )
    assert [app.name for app in rig.actions.list_start_apps()] == ["ApowerREC"]


def test_no_output_is_no_apps(rig):
    assert rig.actions.list_start_apps() == []


@pytest.mark.parametrize("stdout", [b"not base64 !!", base64.b64encode(b"not json"), apps_output(5)])
def test_output_that_cannot_be_read_is_an_error_without_the_output_in_it(rig, stdout):
    rig.run_result = subprocess.CompletedProcess([], 0, stdout=stdout)
    with pytest.raises(OSError) as e:
        rig.actions.list_start_apps()
    assert stdout.decode("ascii", "replace").strip() not in str(e.value)


def test_powershell_that_fails_is_an_error(rig):
    rig.run_result = subprocess.CompletedProcess([], 1, stdout=b"", stderr="秘密".encode())
    with pytest.raises(OSError) as e:
        rig.actions.list_start_apps()
    assert "秘密" not in str(e.value)


def test_an_app_is_started_through_explorer_in_its_apps_folder(rig):
    rig.actions.launch_app("Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")
    args, kwargs = rig.popened[0]
    assert args == [
        "explorer.exe",
        "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
    ]
    assert kwargs["creationflags"] == CREATE_NO_WINDOW


@pytest.mark.parametrize("app_id", ["", "  ", "a\nb", "a\0b"])
def test_an_app_id_that_cannot_be_one_is_refused(rig, app_id):
    with pytest.raises(ValueError):
        rig.actions.launch_app(app_id)
    assert rig.popened == []


# --- the shortcuts of the Start menu ---------------------------------------------------------------


def start_menu(tmp_path, *names):
    """A Start menu folder holding a shortcut for each of `names` (a name with a slash is in a
    folder)."""
    root = tmp_path / "Programs"
    for name in names:
        path = root / f"{name}.lnk"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"L")
    return root


def test_the_shortcuts_of_the_start_menu_are_apps_found_without_powershell(tmp_path):
    root = start_menu(tmp_path, "Spotify", "Office/Word", "筆記本")
    rig = Rig(start_menu=[root])
    rig.run_result = subprocess.CompletedProcess([], 1, stdout=b"")  # PowerShell is no help
    apps = rig.actions.list_start_apps()
    assert sorted((app.name, Path(app.app_id).name) for app in apps) == [
        ("Spotify", "Spotify.lnk"),
        ("Word", "Word.lnk"),
        ("筆記本", "筆記本.lnk"),
    ]


def test_an_app_of_powershell_wins_over_the_shortcut_of_the_same_name(tmp_path):
    rig = Rig(start_menu=[start_menu(tmp_path, "Spotify", "Paint")])
    rig.run_result = subprocess.CompletedProcess(
        [], 0, stdout=apps_output([{"Name": "spotify", "AppID": "Spotify.Store!App"}])
    )
    apps = {app.name: app.app_id for app in rig.actions.list_start_apps()}
    assert apps["spotify"] == "Spotify.Store!App" and "Spotify" not in apps
    assert Path(apps["Paint"]).name == "Paint.lnk"


def test_the_shortcuts_to_uninstall_something_are_not_apps(tmp_path):
    rig = Rig(start_menu=[start_menu(tmp_path, "Uninstall Foo", "Foo", "解除安裝 Bar")])
    assert [app.name for app in rig.actions.list_start_apps()] == ["Foo"]


def test_a_start_menu_with_no_folder_is_no_apps(tmp_path):
    rig = Rig(start_menu=[tmp_path / "nothing"])
    assert rig.actions.list_start_apps() == []


def test_the_apps_cannot_be_listed_when_neither_powershell_nor_the_folders_give_any(tmp_path):
    rig = Rig(start_menu=[tmp_path / "nothing"])
    rig.run_result = subprocess.CompletedProcess([], 1, stdout=b"")
    with pytest.raises(OSError):
        rig.actions.list_start_apps()


def test_a_shortcut_of_the_start_menu_is_opened_with_startfile(tmp_path):
    rig = Rig(start_menu=[start_menu(tmp_path, "Spotify")])
    (app,) = rig.actions.list_start_apps()
    rig.actions.launch_app(app.app_id)
    assert [Path(started).name for started in rig.started] == ["Spotify.lnk"]
    assert rig.popened == []


def test_a_shortcut_outside_the_start_menu_is_not_opened(tmp_path):
    root = start_menu(tmp_path, "Spotify")
    elsewhere = tmp_path / "Downloads" / "evil.lnk"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"L")
    sneaky = root / ".." / "Downloads" / "evil.lnk"
    rig = Rig(start_menu=[root])
    for app_id in (str(elsewhere), str(sneaky), str(root / "missing.lnk")):
        with pytest.raises(ValueError):
            rig.actions.launch_app(app_id)
    assert rig.started == [] and rig.popened == []


# --- the clipboard ---------------------------------------------------------------------------------


def test_text_is_put_on_the_clipboard_as_utf16_with_its_terminator(rig):
    rig.actions.copy_to_clipboard("台北 24°C ☁")
    fmt, data = rig.user32.clipboard
    assert fmt == CF_UNICODETEXT == 13
    assert data == "台北 24°C ☁\0".encode("utf-16-le")
    assert rig.user32.calls == ["open", "empty", "set", "close"]
    assert rig.kernel32.freed == []  # the clipboard owns the memory now
    assert rig.kernel32.locked == rig.kernel32.unlocked


def test_a_clipboard_that_is_busy_is_tried_again_a_few_times(rig):
    rig.user32.open_failures = 3
    rig.actions.copy_to_clipboard("x")
    assert rig.user32.calls.count("open") == 4 and rig.user32.clipboard is not None
    assert len(rig.sleeps) == 3


def test_a_clipboard_that_stays_busy_is_an_error_and_the_memory_is_freed(rig):
    rig.user32.open_failures = 100
    with pytest.raises(OSError):
        rig.actions.copy_to_clipboard("x")
    assert rig.user32.clipboard is None
    assert len(rig.kernel32.freed) == 1 and "close" not in rig.user32.calls


def test_a_clipboard_that_refuses_the_data_is_closed_and_the_memory_freed(rig):
    rig.user32.set_fails = True
    with pytest.raises(OSError):
        rig.actions.copy_to_clipboard("x")
    assert rig.user32.calls[-1] == "close"
    assert len(rig.kernel32.freed) == 1


@pytest.mark.parametrize("failing", ["fail_alloc", "fail_lock"])
def test_memory_that_cannot_be_had_is_an_error(rig, failing):
    setattr(rig.kernel32, failing, True)
    with pytest.raises(OSError):
        rig.actions.copy_to_clipboard("x")
    assert rig.user32.calls == []  # the clipboard was not even opened


# --- no device -------------------------------------------------------------------------------------


def test_the_null_actions_do_nothing_and_find_no_apps():
    actions = NullActions()
    actions.open_url("https://example.com/")
    actions.press_key("volume_up", 2)
    actions.lock_screen()
    actions.launch_app("x")
    actions.open_settings("ms-settings:display")
    actions.copy_to_clipboard("x")
    assert actions.list_start_apps() == []
