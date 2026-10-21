import pytest

from raphael_core import languages as lang
from raphael_core.languages import (
    DEFAULT_LANGUAGES,
    LANGUAGE_IDS,
    MAX_LANGUAGES,
    checked_languages,
    chinese_variant,
    display_name,
    from_legacy,
    language_groups,
    legacy_language,
    stt_languages,
)

ALLOWED = [
    "zh-TW", "zh-HK", "zh-CN", "yue-HK",
    "en-US", "en-GB", "en-AU", "en-IN",
    "ja-JP", "ko-KR", "fr-FR", "de-DE", "es-ES", "es-MX",
    "it-IT", "pt-BR", "vi-VN", "th-TH", "id-ID",
]  # fmt: skip


def test_the_ids_are_exactly_the_contract_ones():
    assert list(LANGUAGE_IDS) == ALLOWED
    assert DEFAULT_LANGUAGES == ("zh-TW",)
    assert MAX_LANGUAGES == 5


# --- checked_languages ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        ["zh-TW"],
        ["zh-TW", "en-US", "ja-JP"],
        ["en-GB"],
        ["zh-CN", "en-US", "ko-KR", "fr-FR", "de-DE"],
    ],
)
def test_a_valid_selection_comes_back_as_a_new_list(value):
    result = checked_languages("languages", value)
    assert result == value and result is not value


@pytest.mark.parametrize("value", ["zh-TW", None, ("zh-TW",), [1], ["zh-TW", None]])
def test_a_selection_that_is_not_a_list_of_strings_is_a_type_error(value):
    with pytest.raises(TypeError, match="languages"):
        checked_languages("languages", value)


@pytest.mark.parametrize(
    "value",
    [
        [],  # empty
        ["auto"],  # not an id: several languages mean automatic detection
        ["zh"],
        ["zh-tw"],
        ["en-CA"],
        ["zh-TW", "zh-TW"],
        ["zh-TW", "zh-CN"],  # two Chinese written forms
        ["zh-HK", "yue-HK"],
        ["en-US", "en-GB"],
        ["es-ES", "es-MX"],
        ["zh-TW", "en-US", "ja-JP", "ko-KR", "fr-FR", "de-DE"],  # six
    ],
)
def test_an_invalid_selection_is_a_value_error_naming_the_key(value):
    with pytest.raises(ValueError, match="'languages'"):
        checked_languages("languages", value)


# --- languages.list -------------------------------------------------------------------------------


def test_the_groups_hold_every_id_once_in_order():
    groups = language_groups()["groups"]
    ids = [variant["id"] for group in groups for variant in group["variants"]]
    assert ids == ALLOWED
    for group in groups:
        assert set(group) == {"id", "name", "variants"}
        for variant in group["variants"]:
            assert set(variant) == {"id", "name", "note"}
            assert isinstance(variant["name"], str) and variant["name"]
            assert isinstance(variant["note"], str)


def test_the_groups_and_names_are_the_documented_ones():
    groups = {group["id"]: group for group in language_groups()["groups"]}
    assert list(groups) == ["zh", "en", "ja", "ko", "fr", "de", "es", "it", "pt", "vi", "th", "id"]
    zh = groups["zh"]
    assert zh["name"] == "中文"
    assert zh["variants"][0] == {
        "id": "zh-TW",
        "name": "繁體中文（台灣）",
        "note": "台灣用語，例如 軟體、視窗",
    }
    assert [v["id"] for v in zh["variants"]] == ["zh-TW", "zh-HK", "zh-CN", "yue-HK"]
    assert groups["en"]["name"] == "English"
    assert groups["en"]["variants"][0] == {
        "id": "en-US",
        "name": "English (United States)",
        "note": "color, center",
    }
    assert groups["en"]["variants"][1]["note"] == "colour, centre"
    assert groups["ja"]["variants"] == [{"id": "ja-JP", "name": "日本語", "note": ""}]
    assert groups["ko"]["name"] == "한국어"
    assert groups["fr"]["name"] == "Français"
    assert [v["id"] for v in groups["es"]["variants"]] == ["es-ES", "es-MX"]


def test_the_groups_are_a_fresh_copy_each_time():
    first = language_groups()
    first["groups"][0]["variants"].clear()
    assert language_groups()["groups"][0]["variants"]


def test_display_names():
    assert display_name("zh-TW") == "繁體中文（台灣）"
    assert display_name("en-US") == "English (United States)"
    assert display_name("ja-JP") == "日本語"


# --- speech-to-text hints -------------------------------------------------------------------------


def test_the_codes_of_several_languages_keep_their_order():
    assert stt_languages(["ja-JP", "zh-TW", "en-GB"]) == ["ja", "zh", "en"]
    assert stt_languages(["yue-HK", "en-US"]) == ["yue", "en"]


def test_every_id_has_a_two_or_three_letter_code():
    for language_id in LANGUAGE_IDS:
        assert lang.iso_code(language_id) in lang.STT_CODES


# --- the Chinese variant --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("languages", "variant"),
    [
        (["zh-TW"], "zh-TW"),
        (["en-US", "zh-HK"], "zh-HK"),
        (["ja-JP", "zh-CN"], "zh-CN"),
        (["yue-HK"], "yue-HK"),
        (["en-US", "ja-JP"], None),
    ],
)
def test_the_chinese_variant_is_the_one_selected(languages, variant):
    assert chinese_variant(languages) == variant


# --- the old `language` setting -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("auto", ["zh-TW", "en-US", "ja-JP"]),
        ("zh", ["zh-TW"]),
        ("ja", ["ja-JP"]),
        ("en", ["en-US"]),
    ],
)
def test_the_old_language_setting_maps_to_languages(old, new):
    assert from_legacy(old) == new


def test_an_unknown_old_language_maps_to_the_default():
    assert from_legacy("de") == ["zh-TW"]
    assert from_legacy(3) == ["zh-TW"]


@pytest.mark.parametrize(
    ("languages", "old"),
    [
        (["zh-TW"], "zh"),
        (["zh-CN"], "zh"),
        (["yue-HK"], "zh"),
        (["ja-JP"], "ja"),
        (["en-GB"], "en"),
        (["fr-FR"], "auto"),
        (["zh-TW", "en-US", "ja-JP"], "auto"),
    ],
)
def test_the_old_language_is_derived_from_languages(languages, old):
    assert legacy_language(languages) == old
