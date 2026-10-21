"""Tests for the fixed commands: the registry, running them, and finding an app of the Start menu."""

import asyncio
import logging
import threading

import pytest

from raphael_core.agent import commands
from raphael_core.agent.commands import (
    COMMAND_NAMES,
    COMMANDS,
    NONE,
    VOLUME_STEPS,
    AppCatalog,
    lookup_app,
    match_app,
    run_command,
)
from raphael_core.agent.device import VIRTUAL_KEYS, StartApp
from raphael_core.agent.policy import Tier

from .conftest import FakeActions

URL = {"target_kind": "url", "target": "https://www.youtube.com/"}

# --- the registry --------------------------------------------------------------------------------


def test_the_commands_are_the_fixed_registry():
    assert COMMAND_NAMES == (
        "open",
        "volume_up",
        "volume_down",
        "volume_mute",
        "media_play_pause",
        "media_next",
        "media_previous",
        "lock_screen",
        "copy_text",
    )
    assert tuple(COMMANDS) == COMMAND_NAMES
    assert NONE == "none" and NONE not in COMMANDS


def test_no_site_and_no_app_is_named_in_the_registry():
    # Opening anything is one command, whose target is found when it is asked for.
    assert {c.kind for c in COMMANDS.values()} == {"open", "key", "lock", "copy"}
    for command in COMMANDS.values():
        for name in ("youtube", "gmail", "github", "google", "notion"):
            assert name not in command.guidance.lower() and name not in command.target


def test_only_copying_changes_state_so_everything_else_runs_at_once():
    assert {name: c.tier for name, c in COMMANDS.items() if c.tier != Tier.T1} == {
        "copy_text": Tier.T2
    }
    assert COMMANDS["lock_screen"].tier == Tier.T1  # locking is undone by signing in


def test_every_command_tells_the_router_what_it_is_for():
    for command in COMMANDS.values():
        assert command.guidance.endswith(".") and len(command.guidance) > 12, command.name
        assert command.guidance.isascii(), command.name
    guidance = [c.guidance for c in COMMANDS.values()]
    assert len(set(guidance)) == len(guidance)  # no two commands are described alike


def test_the_keys_the_commands_press_exist():
    keys = {c.target for c in COMMANDS.values() if c.kind == "key"}
    assert keys == {
        "volume_up",
        "volume_down",
        "volume_mute",
        "play_pause",
        "next_track",
        "previous_track",
    }
    assert keys <= set(VIRTUAL_KEYS)


# --- running them --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "call"),
    [
        ({"target_kind": "url", "target": "https://www.reddit.com/"},
         ("open_url", "https://www.reddit.com/")),
        ({"target_kind": "app", "target": "Microsoft.Calc!App"},
         ("launch_app", "Microsoft.Calc!App")),
        ({"target_kind": "settings", "target": "ms-settings:bluetooth"},
         ("open_settings", "ms-settings:bluetooth")),
    ],
)  # fmt: skip
async def test_opening_does_what_its_kind_says_on_a_worker_thread(actions, args, call):
    await run_command(COMMANDS["open"], args, actions)
    assert actions.calls == [call]
    assert actions.threads == [actions.threads[0]] and actions.threads[0] != threading.get_ident()


@pytest.mark.parametrize(
    ("name", "key", "times"),
    [
        ("volume_up", "volume_up", VOLUME_STEPS),
        ("volume_down", "volume_down", VOLUME_STEPS),
        ("volume_mute", "volume_mute", 1),
        ("media_play_pause", "play_pause", 1),
        ("media_next", "next_track", 1),
        ("media_previous", "previous_track", 1),
    ],
)
async def test_a_key_command_presses_its_key(actions, name, key, times):
    await run_command(COMMANDS[name], {}, actions)
    assert actions.calls == [("press_key", key, times)]
    assert actions.threads[0] != threading.get_ident()


def test_the_volume_moves_by_a_step_that_can_be_heard():
    assert VOLUME_STEPS == 5  # one press is 2 %


async def test_the_screen_is_locked(actions):
    await run_command(COMMANDS["lock_screen"], {}, actions)
    assert actions.calls == [("lock_screen",)]


async def test_text_is_copied_to_the_clipboard(actions):
    await run_command(COMMANDS["copy_text"], {"text": "台北 24 度"}, actions)
    assert actions.calls == [("copy_to_clipboard", "台北 24 度")]


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("open", {}),
        ("open", {"target_kind": "url"}),
        ("open", {"target_kind": "url", "target": ""}),
        ("open", {"target_kind": "folder", "target": "C:\\"}),
        ("open", {"target_kind": "app", "target": 5}),
        ("copy_text", {}),
        ("copy_text", {"text": 5}),
    ],
)
async def test_a_command_missing_what_it_needs_is_a_value_error_and_does_nothing(
    actions, name, args
):
    with pytest.raises(ValueError):
        await run_command(COMMANDS[name], args, actions)
    assert actions.calls == []


async def test_a_command_that_fails_raises_what_the_device_raised(actions):
    actions.fail["open_url"] = OSError("no browser")
    with pytest.raises(OSError):
        await run_command(COMMANDS["open"], URL, actions)


async def test_the_event_loop_is_not_blocked_while_a_command_runs():
    gate = threading.Event()

    class Slow(FakeActions):
        def open_url(self, url):
            gate.wait(5)

    running = asyncio.create_task(run_command(COMMANDS["open"], URL, Slow()))
    await asyncio.sleep(0.05)
    assert not running.done()  # still inside the device call, and the loop is free to say so
    gate.set()
    await running


# --- finding an app in an utterance ------------------------------------------------------------------

APPS = [
    StartApp("Calculator", "calc!App"),
    StartApp("Notepad", "notepad.exe"),
    StartApp("Microsoft Edge", "edge!App"),
    StartApp("Microsoft Word", "word!App"),
    StartApp("WordPad", "wordpad.exe"),
    StartApp("小算盤", "xiaosuanpan!App"),
    StartApp("Spotify", "spotify!App"),
    StartApp("7-Zip File Manager", "7zfm.exe"),
    StartApp("7-Zip Help", "7zhelp.chm"),
    StartApp("Visual Studio Code", "code.exe"),
]


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("open spotify", "Spotify"),
        ("Open SPOTIFY please", "Spotify"),
        ("please open the calculator app", "Calculator"),
        ("打開小算盤", "小算盤"),
        ("幫我開 spotify 一下", "Spotify"),
        ("open microsoft edge", "Microsoft Edge"),  # the whole name
        ("open edge", "Microsoft Edge"),  # its distinctive word
        ("open word", "Microsoft Word"),
        ("open wordpad", "WordPad"),  # not Word: WordPad is a word of its own
        ("open visual studio code", "Visual Studio Code"),
        ("open 7-zip file manager", "7-Zip File Manager"),
        ("ＯＰＥＮ　ＳＰＯＴＩＦＹ", "Spotify"),  # full-width letters
    ],
)
def test_an_app_the_utterance_names_is_found(utterance, expected):
    assert match_app(utterance, APPS).name == expected


@pytest.mark.parametrize(
    "utterance",
    [
        "",
        "   ",
        "open the thing",
        "what is a password",  # "word" is inside "password", not a word of it
        "open microsoft",  # Edge and Word alike
        "open 7-zip",  # the manager and the help alike
        "打開那個東西",
    ],
)
def test_an_utterance_that_names_no_one_app_finds_none(utterance):
    assert match_app(utterance, APPS) is None


def test_no_apps_are_no_match():
    assert match_app("open spotify", []) is None


def test_the_longest_name_in_the_utterance_wins():
    apps = [StartApp("Visual Studio", "vs"), StartApp("Visual Studio Code", "code")]
    assert match_app("open visual studio code", apps).app_id == "code"
    assert match_app("open visual studio", apps).app_id == "vs"


def test_two_apps_of_the_same_name_are_the_first_listed():
    apps = [StartApp("Notepad", "first"), StartApp("Notepad", "second")]
    assert match_app("open notepad", apps).app_id == "first"


def test_a_strict_match_wants_the_whole_name_of_the_app_in_the_utterance():
    # "google" is the web site before it is the one app that has the word in its name.
    apps = [*APPS, StartApp("Google Chrome", "chrome")]
    assert match_app("open google", apps).name == "Google Chrome"
    assert match_app("open google", apps, loose=False) is None
    assert match_app("open edge", apps, loose=False) is None
    assert match_app("open microsoft edge", apps, loose=False).name == "Microsoft Edge"
    assert match_app("open spotify", apps, loose=False).name == "Spotify"
    assert match_app("打開小算盤", apps, loose=False).name == "小算盤"


# --- looking an app up by the name a model gave -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Spotify", "Spotify"),
        ("spotify", "Spotify"),
        ("  SPOTIFY ", "Spotify"),
        ("小算盤", "小算盤"),
        ("Visual Studio", "Visual Studio Code"),  # part of a name
        ("Microsoft Edge Browser", "Microsoft Edge"),  # a name with more than the app's
        ("Calculater", "Calculator"),  # a slip
        ("Notepd", "Notepad"),
    ],
)
def test_an_app_is_looked_up_by_a_name_near_enough(name, expected):
    assert lookup_app(name, APPS).name == expected


@pytest.mark.parametrize("name", ["", " ", "x", "Photoshop", "Calendar of the moon", "無此應用"])
def test_a_name_that_is_no_app_finds_none(name):
    assert lookup_app(name, APPS) is None


def test_an_exact_name_beats_a_longer_one_that_contains_it():
    apps = [StartApp("Notepad++", "pp"), StartApp("Notepad", "plain")]
    assert lookup_app("Notepad", apps).app_id == "plain"


# --- the catalog -------------------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def catalog(actions, clock):
    actions.apps = list(APPS)
    return AppCatalog(actions, clock=clock)


async def test_the_start_menu_is_not_read_until_it_is_needed(catalog, actions):
    assert actions.listed == 0
    assert [a.name for a in await catalog.apps()][:2] == ["Calculator", "Notepad"]
    assert actions.listed == 1


async def test_the_start_menu_is_read_off_the_event_loop(catalog, actions):
    await catalog.apps()
    assert actions.threads[0] != threading.get_ident()


async def test_the_list_is_cached(catalog, actions, clock):
    await catalog.apps()
    clock.now += commands.APPS_TTL_S - 1
    await catalog.apps()
    assert actions.listed == 1
    clock.now += 2  # now it is old
    await catalog.apps()
    await catalog.settle()
    assert actions.listed == 2


async def test_an_old_list_is_served_at_once_and_read_again_in_the_background(
    catalog, actions, clock
):
    await catalog.apps()
    actions.apps = [StartApp("Zoom", "zoom!App")]
    release = threading.Event()
    started = threading.Event()
    original = actions.list_start_apps

    def slow():
        started.set()
        release.wait(5)  # PowerShell, slow
        return original()

    actions.list_start_apps = slow
    clock.now += commands.APPS_TTL_S + 1
    stale = await asyncio.wait_for(catalog.apps(), 1)  # does not wait for the read
    assert [a.name for a in stale][:2] == ["Calculator", "Notepad"]
    assert await asyncio.to_thread(started.wait, 5)
    again = await asyncio.wait_for(catalog.apps(), 1)  # still the old list, and no second read
    assert again == stale
    release.set()
    await catalog.settle()
    assert [a.name for a in await catalog.apps()] == ["Zoom"]


async def test_a_read_that_fails_in_the_background_keeps_the_old_list(catalog, actions, clock):
    await catalog.apps()
    actions.fail["list_start_apps"] = OSError("slow")
    clock.now += commands.APPS_TTL_S + 1
    assert len(await catalog.apps()) == len(APPS)
    await catalog.settle()
    assert len(await catalog.apps()) == len(APPS)


async def test_two_asks_at_once_read_it_once(catalog, actions):
    first, second = await asyncio.gather(catalog.apps(), catalog.apps())
    assert first == second and actions.listed == 1


async def test_the_catalog_finds_strictly_when_asked_to(catalog):
    assert await catalog.find("open edge", loose=False) is None
    assert (await catalog.find("open edge")).name == "Microsoft Edge"


async def test_the_catalog_can_be_warmed_before_anyone_asks(catalog, actions):
    await catalog.warm()
    assert actions.listed == 1
    assert (await catalog.find("open spotify")).name == "Spotify"
    assert actions.listed == 1


async def test_an_app_found_in_an_utterance_is_returned_from_the_cache(catalog, actions):
    assert (await catalog.find("open spotify")).app_id == "spotify!App"
    assert (await catalog.find("open the calculator")).app_id == "calc!App"
    assert actions.listed == 1


async def test_an_app_that_is_not_in_the_list_makes_one_fresh_read_in_the_background(
    catalog, actions, clock
):
    await catalog.apps()
    actions.apps.append(StartApp("Zoom", "zoom!App"))  # installed since
    assert await catalog.find("open zoom") is None  # the list is still new: no new read
    await catalog.settle()
    assert actions.listed == 1
    clock.now += commands.APPS_MISS_REFRESH_S
    assert await catalog.find("open zoom") is None  # answered at once, from the list it has
    await catalog.settle()
    assert actions.listed == 2
    assert (await catalog.find("open zoom")).app_id == "zoom!App"  # the next question finds it


async def test_a_name_is_looked_up_with_the_same_refresh(catalog, actions, clock):
    await catalog.apps()
    actions.apps.append(StartApp("Zoom", "zoom!App"))
    clock.now += commands.APPS_MISS_REFRESH_S
    assert await catalog.lookup("Zoom") is None
    await catalog.settle()
    assert (await catalog.lookup("Zoom")).app_id == "zoom!App"


async def test_a_list_that_cannot_be_read_is_empty_and_read_again_next_time(
    catalog, actions, caplog
):
    actions.fail["list_start_apps"] = OSError("秘密 powershell output")
    with caplog.at_level(logging.WARNING, logger=commands.logger.name):
        assert await catalog.apps() == []
        assert await catalog.find("open spotify") is None
    assert "OSError" in caplog.text and "秘密" not in caplog.text
    del actions.fail["list_start_apps"]
    assert (await catalog.find("open spotify")).name == "Spotify"


async def test_the_names_are_listed_for_a_model_to_choose_from(catalog):
    names = await catalog.names()
    assert names[:3] == ["Calculator", "Notepad", "Microsoft Edge"]
    assert len(names) == len(APPS)


async def test_no_more_names_are_listed_than_a_prompt_should_hold(actions, clock):
    actions.apps = [StartApp(f"App {n}", f"id{n}") for n in range(commands.MAX_APP_NAMES + 50)]
    names = await AppCatalog(actions, clock=clock).names()
    assert len(names) == commands.MAX_APP_NAMES


async def test_the_names_are_listed_once_each(actions, clock):
    actions.apps = [StartApp("Notepad", "a"), StartApp("notepad", "b"), StartApp("Paint", "c")]
    assert await AppCatalog(actions, clock=clock).names() == ["Notepad", "Paint"]
