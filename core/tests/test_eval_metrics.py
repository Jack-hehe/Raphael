"""Tests for the character error rate that the evaluation tools report."""

import pytest

from raphael_core.evaluate.metrics import cer, edit_distance, normalize_for_cer


def test_cer_counts_character_edits():
    assert cer("今天天氣很好", "今天天汽很好") == pytest.approx(1 / 6)


def test_normalize_converts_and_strips_punctuation():
    assert normalize_for_cer("今天天气很好！ OK") == "今天天氣很好ok"


def test_cer_ignores_punctuation_and_script():
    assert cer("今天天氣很好。", "今天天气很好") == 0.0


def test_cer_empty_reference():
    assert cer("", "") == 0.0 and cer("", "多") == 1.0


def test_cer_of_identical_text_is_zero():
    assert cer("明日の会議は三時からです", "明日の会議は三時からです") == 0.0


def test_cer_counts_insertions_and_deletions():
    assert cer("今天天氣", "今天") == pytest.approx(2 / 4)  # two deleted
    assert cer("今天", "今天天氣") == pytest.approx(2 / 2)  # two inserted: more than the reference
    assert cer("今天天氣", "") == 1.0


def test_cer_ignores_case_and_spaces_between_words():
    assert cer("Open Router API", "openrouter api") == 0.0
    assert cer("這個 bug 是 race condition", "這個bug是Race Condition") == 0.0


def test_cer_ignores_ascii_and_full_width_punctuation():
    assert cer("你好，世界。(OK)", "你好 世界 ok") == 0.0
    assert cer("你好", "你好！？…、「」") == 0.0


def test_cer_treats_full_width_letters_as_the_same_as_plain_ones():
    assert cer("OK 123", "ＯＫ １２３") == 0.0


def test_cer_leaves_japanese_kanji_alone():
    # 会 is Japanese here: turning it into 會 on one side only would count a mistake that is not.
    assert cer("明日の会議は三時から。", "明日の会議は三時から") == 0.0


def test_normalize_keeps_digits_and_symbols_that_are_spoken():
    assert normalize_for_cer("C++ 3.5 版") == "c++35版"


def test_edit_distance_of_basic_cases():
    assert edit_distance("", "") == 0
    assert edit_distance("abc", "") == 3
    assert edit_distance("", "abc") == 3
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("flaw", "lawn") == 2
