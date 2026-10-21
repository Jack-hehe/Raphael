"""Tests for opening anything: which addresses may be opened, the spoken domain, and the Windows
settings pages. Nothing here opens anything."""

import pytest

from raphael_core.agent.opening import (
    SETTINGS_PAGES,
    SETTINGS_URIS,
    clean_target,
    match_settings,
    parse_domain,
    validate_url,
)

# --- the addresses that may be opened --------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/",
        "https://github.com/notifications",
        "https://www.youtube.com/results?search_query=lofi+beats",
        "https://例え.jp/",
        "https://sub.domain.co.uk/a/b?c=d#e",
    ],
)
def test_a_public_https_page_may_be_opened(url):
    assert validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",  # not said outright
        "ftp://example.com/",
        "file:///C:/Windows/System32/cmd.exe",
        "javascript:alert(1)",
        "steam://run/10",
        "ms-settings:display",
        "mailto:a@b.co",
        "data:text/html,hi",
        "https://localhost/",
        "https://localhost:8080/",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://192.168.0.1/admin",
        "https://10.0.0.5/",
        "https://169.254.169.254/latest/meta-data",
        "https://8.8.8.8/",  # no address literals at all
        "https://2130706433/",
        "https://router.local/",
        "https://printer.lan/",
        "https://nas.home.arpa/",
        "https://intranet/",  # no dot: a name of the local network
        "https://user:pw@example.com/",
        "https://example.com:8443/",
        "https:///nohost",
        "https://exa mple.com/",
        "https://example.com/setup.exe",
        "https://example.com/run.ps1?x=1",
        "https://example.com/" + "a" * 2100,
        "",
        "example.com",
    ],
)
def test_anything_else_is_refused(url):
    assert validate_url(url) is None


def test_http_is_allowed_only_when_the_speaker_said_so():
    assert validate_url("http://example.com/", allow_http=True) == "http://example.com/"
    assert validate_url("http://localhost/", allow_http=True) is None
    assert validate_url("http://192.168.1.1/", allow_http=True) is None


def test_the_surrounding_white_space_is_ignored_but_the_inside_is_not():
    assert validate_url("  https://example.com/  ") == "https://example.com/"
    assert validate_url("https://example.com/a b") is None
    assert validate_url("https://example.com/\x00") is None


# --- a domain said outright ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spoken", "url"),
    [
        ("reddit.com", "https://reddit.com/"),
        ("www.reddit.com", "https://www.reddit.com/"),
        ("github.com/notifications", "https://github.com/notifications"),
        ("https://news.ycombinator.com/", "https://news.ycombinator.com/"),
        ("Example.ORG", "https://example.org/"),
        ("ｒｅｄｄｉｔ.com", "https://reddit.com/"),
    ],
)
def test_a_domain_said_outright_is_an_https_page(spoken, url):
    assert parse_domain(spoken) == url


def test_http_that_was_said_is_kept_and_http_that_was_not_said_is_not_made():
    assert parse_domain("http://example.com") == "http://example.com/"
    assert parse_domain("example.com").startswith("https://")


@pytest.mark.parametrize(
    "spoken",
    [
        "youtube",
        "notepad.exe",
        "setup.msi",
        "run.bat",
        "script.ps1",
        "a.js",
        "link.lnk",
        "localhost",
        "127.0.0.1",
        "192.168.0.1/admin",
        "router.local",
        "file:///c:/x",
        "javascript:alert(1)",
        "two words.com",
        "",
        "1.2",
        "reddit.",
    ],
)
def test_what_is_no_public_domain_is_not_one(spoken):
    assert parse_domain(spoken) is None


# --- the target as it is kept ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("YouTube please", "youtube"),
        ("the Calculator", "calculator"),
        ("my   github   notifications", "github notifications"),
        ("youtube一下", "youtube"),
        ("藍牙設定吧", "藍牙設定"),
        ("Ｇｍａｉｌ", "gmail"),
        ("  reddit.com ", "reddit.com"),
    ],
)
def test_the_polite_words_around_a_target_are_taken_off(raw, clean):
    assert clean_target(raw) == clean


# --- the Windows settings pages --------------------------------------------------------------------


def test_every_settings_page_is_a_plain_ms_settings_uri_and_none_is_listed_twice():
    seen = {}
    for key, (uri, names) in SETTINGS_PAGES.items():
        assert uri.startswith("ms-settings:") and uri[12:].replace("-", "").isalnum(), key
        assert uri == uri.lower()
        assert names, key
        for name in names:
            assert name == name.casefold() and name == name.strip(), name
            assert seen.setdefault(name, key) == key, f"{name!r} is on two pages"
    assert SETTINGS_URIS == {uri for uri, _ in SETTINGS_PAGES.values()}


def test_the_pages_are_named_in_chinese_english_and_japanese():
    for key, (_, names) in SETTINGS_PAGES.items():
        assert any(name.isascii() for name in names), f"{key} has no English name"
        assert any(not name.isascii() for name in names), f"{key} has no Chinese or Japanese name"
    assert len(SETTINGS_PAGES) >= 50


@pytest.mark.parametrize(
    ("target", "uri"),
    [
        ("bluetooth settings", "ms-settings:bluetooth"),
        ("藍牙設定", "ms-settings:bluetooth"),
        ("ブルートゥースの設定", "ms-settings:bluetooth"),
        ("display settings", "ms-settings:display"),
        ("顯示設定", "ms-settings:display"),
        ("ディスプレイ設定", "ms-settings:display"),
        ("sound settings", "ms-settings:sound"),
        ("聲音設定", "ms-settings:sound"),
        ("サウンド設定", "ms-settings:sound"),
        ("wi-fi settings", "ms-settings:network-wifi"),
        ("wifi 設定", "ms-settings:network-wifi"),
        ("windows update settings", "ms-settings:windowsupdate"),
        ("アップデート設定", "ms-settings:windowsupdate"),
        ("night light settings", "ms-settings:nightlight"),
        ("dark mode settings", "ms-settings:colors"),
        ("default apps settings", "ms-settings:defaultapps"),
        ("預設應用程式設定", "ms-settings:defaultapps"),
    ],
)
def test_a_page_named_with_the_word_for_settings(target, uri):
    assert match_settings(target, bare=False) == uri


@pytest.mark.parametrize(
    ("target", "uri"),
    [
        ("bluetooth", "ms-settings:bluetooth"),
        ("藍牙", "ms-settings:bluetooth"),
        ("wifi", "ms-settings:network-wifi"),
        ("wi-fi", "ms-settings:network-wifi"),
        ("無線網路", "ms-settings:network-wifi"),
        ("ネットワーク", "ms-settings:network"),
        ("windows update", "ms-settings:windowsupdate"),
        ("airplane mode", "ms-settings:network-airplanemode"),
        ("飛航模式", "ms-settings:network-airplanemode"),
        ("機内モード", "ms-settings:network-airplanemode"),
        ("night light", "ms-settings:nightlight"),
        ("夜間模式", "ms-settings:nightlight"),
        ("startup apps", "ms-settings:startupapps"),
        ("vpn", "ms-settings:network-vpn"),
        ("printers", "ms-settings:printers"),
        ("印表機", "ms-settings:printers"),
        ("troubleshoot", "ms-settings:troubleshoot"),
    ],
)
def test_a_page_named_by_its_name_alone(target, uri):
    assert match_settings(target, bare=True) == uri


@pytest.mark.parametrize(
    "target",
    ["", "youtube", "play the sound of rain", "bluetooth speaker review", "我想聽藍牙喇叭的評價"],
)
def test_a_sentence_that_only_mentions_a_page_is_not_the_page(target):
    assert match_settings(target, bare=True) is None
    assert match_settings(target, bare=False) is None


def test_the_word_for_settings_alone_is_no_page_but_the_settings_app():
    for target in ("settings", "設定", "設定アプリ"):
        assert match_settings(target, bare=True) is None
        assert match_settings(target, bare=False) is None


def test_a_name_without_the_word_for_settings_waits_for_the_last_stage():
    # The page named alone is the last stage, after the apps: a name that is also an app (the
    # camera) is the app unless "settings" was said.
    assert match_settings("bluetooth", bare=False) is None
    assert match_settings("camera settings", bare=False) is None  # there is no camera page
