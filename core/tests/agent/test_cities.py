"""Tests for finding a city: the names Open-Meteo's geocoder does not know."""

import pytest

from raphael_core.agent.cities import ALIASES, cities_in, city_in, city_key, geocoding_queries


def test_a_city_key_ignores_case_width_and_spaces():
    assert city_key(" New  York ") == "newyork"
    assert city_key("ＴＯＫＹＯ") == "tokyo"
    assert city_key("台　北") == "台北"


def test_the_default_city_is_in_the_table_in_every_spelling_it_is_written():
    for spelling in ("台北", "臺北", "台北市", "臺北市"):
        assert ALIASES[city_key(spelling)] == "Taipei"


def test_every_city_of_the_table_is_asked_for_in_english_letters():
    assert ALIASES
    for key, english in ALIASES.items():
        assert english.isascii() and english[0].isupper(), (key, english)
        assert key == city_key(key), key  # already as lookups make it


def test_no_spelling_is_given_to_two_cities():
    # A dict would hide it: count the spellings written in the table against the dict's size.
    from raphael_core.agent import cities

    written = [
        city_key(spelling)
        for spellings in cities._ALIASES
        for spelling in spellings.split(", ")
    ]
    assert len(written) == len(set(written)) == len(ALIASES)


@pytest.mark.parametrize(
    ("city", "queries"),
    [
        ("台北", [("Taipei", "en")]),
        ("高雄", [("Kaohsiung", "en")]),
        ("Paris", [("Paris", "zh")]),
        ("New York", [("New York", "zh")]),
        ("羅東", [("羅東", "zh"), ("羅東市", "zh")]),
        ("羅東市", [("羅東市", "zh")]),
        ("宜蘭縣", [("宜蘭縣", "zh")]),
        ("奈良県", [("奈良県", "zh")]),
        ("  Reykjavik  ", [("Reykjavik", "zh")]),
    ],
)
def test_the_geocoder_is_asked_in_order(city, queries):
    assert geocoding_queries(city, "zh") == queries


def test_the_language_asked_in_is_the_one_given_except_for_a_city_of_the_table():
    assert geocoding_queries("Reykjavik", "ja") == [("Reykjavik", "ja")]
    assert geocoding_queries("台北", "ja") == [("Taipei", "en")]


@pytest.mark.parametrize(
    ("question", "city"),
    [
        ("今天台北天氣如何？", "台北"),
        ("臺北明天會下雨嗎", "臺北"),
        ("新北市明天下雨嗎", "新北市"),
        ("東京の天気は？", "東京"),
        ("ソウルの天気", "ソウル"),
        ("What's the weather in Taipei tomorrow?", "Taipei"),
        ("Will it rain in New York?", "New York"),
        ("weather in ROME", "Rome"),
    ],
)
def test_the_city_a_question_names_is_found(question, city):
    assert city_in(question) == city


@pytest.mark.parametrize(
    "question", ["明天要帶傘嗎", "今天天氣如何", "open chrome", "a comparison of prices", ""]
)
def test_a_question_that_names_no_city_of_the_table_has_none(question):
    assert city_in(question) is None


@pytest.mark.parametrize(
    ("question", "cities"),
    [
        ("明天台北和台中哪裡比較適合出門", ["台北", "台中"]),
        ("東京と大阪の天気を比べて", ["東京", "大阪"]),
        ("東京 and Osaka tomorrow, which is better?", ["東京", "Osaka"]),
        ("Osaka or Tokyo for a trip?", ["Osaka", "Tokyo"]),
        ("新北市和台北誰比較冷", ["新北市", "台北"]),
        ("台北京站附近的天氣", ["台北"]),
        ("東京 東京都 Tokyo 都是同一個地方", ["東京"]),
        ("台北台中高雄新竹基隆天氣", ["台北", "台中", "高雄", "新竹"]),
        ("明天要帶傘嗎", []),
    ],
)
def test_every_city_a_question_names_is_found_in_order_without_repeats(question, cities):
    assert cities_in(question) == cities
