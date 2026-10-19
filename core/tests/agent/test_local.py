"""Tests for the local matcher: the fixed commands, found with no model, for when there is no key,
no budget or no network. It must miss rather than guess."""

import pytest

from raphael_core.agent.commands import COMMANDS
from raphael_core.agent.local import match_command
from raphael_core.agent.routing import RouteDecision


def decided(text):
    decision = match_command(text)
    return None if decision is None else decision.command


@pytest.mark.parametrize(
    ("text", "command"),
    [
        # sites
        ("打開 YouTube", "open_youtube"),
        ("幫我開啟油管", "open_youtube"),
        ("open youtube", "open_youtube"),
        ("Open YouTube please", "open_youtube"),
        ("YouTubeを開いて", "open_youtube"),
        ("ユーチューブを開いて", "open_youtube"),
        ("打開 gmail", "open_gmail"),
        ("open Gmail", "open_gmail"),
        ("Gmailを開いて", "open_gmail"),
        ("打開 GitHub", "open_github"),
        ("open github", "open_github"),
        ("打開 google", "open_google"),
        ("open google", "open_google"),
        ("グーグルを開いて", "open_google"),
        ("打開 Google 日曆", "open_google_calendar"),
        ("open google calendar", "open_google_calendar"),
        ("Googleカレンダーを開いて", "open_google_calendar"),
        ("打開 Google 雲端硬碟", "open_google_drive"),
        ("open google drive", "open_google_drive"),
        ("Googleドライブを開いて", "open_google_drive"),
        ("打開 Notion", "open_notion"),
        ("open notion", "open_notion"),
        ("打開 ChatGPT", "open_chatgpt"),
        ("open chat gpt", "open_chatgpt"),
        ("打開 Claude", "open_claude"),
        ("open claude", "open_claude"),
        # volume
        ("音量調大", "volume_up"),
        ("大聲一點", "volume_up"),
        ("聲音大一點", "volume_up"),
        ("volume up", "volume_up"),
        ("turn the volume up", "volume_up"),
        ("louder", "volume_up"),
        ("音量を上げて", "volume_up"),
        ("音量調小", "volume_down"),
        ("小聲一點", "volume_down"),
        ("volume down", "volume_down"),
        ("turn the volume down", "volume_down"),
        ("quieter", "volume_down"),
        ("音量を下げて", "volume_down"),
        ("靜音", "volume_mute"),
        ("mute", "volume_mute"),
        ("Mute the sound", "volume_mute"),
        ("ミュートにして", "volume_mute"),
        # media
        ("暫停", "media_play_pause"),
        ("暫停播放", "media_play_pause"),
        ("繼續播放", "media_play_pause"),
        ("pause", "media_play_pause"),
        ("pause the music", "media_play_pause"),
        ("resume", "media_play_pause"),
        ("一時停止", "media_play_pause"),
        ("下一首", "media_next"),
        ("next song", "media_next"),
        ("skip", "media_next"),
        ("次の曲", "media_next"),
        ("上一首", "media_previous"),
        ("previous song", "media_previous"),
        ("前の曲", "media_previous"),
        # lock
        ("鎖定螢幕", "lock_screen"),
        ("鎖屏", "lock_screen"),
        ("lock the screen", "lock_screen"),
        ("lock my computer", "lock_screen"),
        ("画面をロックして", "lock_screen"),
    ],
)
def test_a_plain_command_is_found(text, command):
    decision = match_command(text)
    assert decision == RouteDecision(route="command", command=command, source="local")
    assert decision.command in COMMANDS


@pytest.mark.parametrize(
    "text",
    [
        "打開計算機",
        "打開小算盤",
        "開啟記事本",
        "open the calculator",
        "launch spotify",
        "please launch notepad",
        "電卓を開いて",
        "メモ帳を起動して",
    ],
)
def test_opening_something_that_is_no_site_is_opening_an_app(text):
    assert decided(text) == "open_app"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "你好",
        "今天天氣如何",
        "what is the weather in Taipei",
        "tell me a joke",
        "台北明天會下雨嗎",
        "我想看 YouTube 上關於音量的影片",  # a site is named, but nothing is opened
        "youtube 是什麼時候成立的",
        "play me something by Radiohead",  # "play" within a longer request
        "what does mute mean in a chat room when the host does it",  # too long to be a command
        "我今天要去開會，順便把音量調整一下然後再把門鎖上然後去買咖啡",  # long
        "pause is a verb that means to stop for a short time",
        "open youtube and search for cats",  # more than a command: left to the models
        "youtube",  # a site named, nothing asked
        "音量",  # the word alone asks nothing
        "start the timer",  # "start" is not "open"
        "skip the intro of my presentation and start with the summary",
        "I want to open a bakery",  # open, but nothing to open
        "打開窗戶",  # open, but not on this computer: no app named... still looks for an app
    ],
)
def test_what_is_no_plain_command_is_left_alone(text):
    decision = match_command(text)
    # "打開窗戶" asks to open something and names nothing known: the matcher may say open_app,
    # and the app lookup then finds nothing. Everything else must be left to the models.
    if text == "打開窗戶":
        assert decision is None or decision.command == "open_app"
    else:
        assert decision is None


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("please mute", "volume_mute"),
        ("Can you pause the music please?", "media_play_pause"),
        ("could you open youtube for me", "open_youtube"),
        ("turn it up", "volume_up"),
        ("lock it", "lock_screen"),
        ("麻煩幫我暫停一下", "media_play_pause"),
        ("麻煩鎖定螢幕", "lock_screen"),
        ("請幫我打開 YouTube", "open_youtube"),
        ("音量調大一點", "volume_up"),
        ("ミュートにしてください", "volume_mute"),
        ("YouTubeを開いてください", "open_youtube"),
        ("  MUTE!!  ", "volume_mute"),
        ("Ｍｕｔｅ", "volume_mute"),  # full-width letters
    ],
)
def test_polite_words_and_punctuation_around_a_command_do_not_hide_it(text, command):
    assert decided(text) == command


def test_the_site_wins_over_the_app_and_google_calendar_over_google():
    assert decided("open google calendar") == "open_google_calendar"
    assert decided("open google drive") == "open_google_drive"
    assert decided("open chatgpt") == "open_chatgpt"


def test_a_request_to_copy_is_never_found_here():
    # Copying changes state: it is asked about, and that needs the router to understand it.
    assert decided("copy that") is None
    assert decided("把答案複製起來") is None


def test_the_decision_is_marked_as_local_and_has_no_confidence():
    decision = match_command("mute")
    assert decision.source == "local" and decision.confidence is None
    assert not (decision.needs_weather or decision.needs_search or decision.needs_time)
