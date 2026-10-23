"""Tests for text made into search terms (pairs of CJK characters, words) and into tokens."""

from raphael_core.memory.terms import (
    estimate_tokens,
    fts_quote,
    index_terms,
    query_words,
    tokens,
)


def test_a_run_of_chinese_becomes_its_overlapping_pairs():
    assert tokens("今天天氣很好") == ["今天", "天天", "天氣", "氣很", "很好"]
    assert tokens("貓") == ["貓"]  # a run of one character is that character


def test_other_text_becomes_its_words_in_lower_case_and_full_width_is_ascii():
    assert tokens("Open OpenRouter_key ＡＢＣ") == ["open", "openrouter", "key", "abc"]
    assert tokens("用Notion記") == ["用", "notion", "記"]
    assert tokens("会議はミーティング") == ["会議", "議は", "はミ", "ミー", "ーテ", "ティ", "ィン", "ング"]


def test_the_index_holds_the_pairs_and_each_character_on_its_own():
    terms = index_terms("天氣", None, "Amy 會議室")
    assert terms.pairs == "天氣 amy 會議 議室"
    assert terms.chars == "天 氣 會 議 室"


def test_a_query_is_words_each_made_into_its_terms_once():
    words = query_words("會議 天氣很好 貓 Notion 會議")
    assert [w.text for w in words] == ["會議", "天氣很好", "貓", "notion"]
    assert words[1].pairs == ("天氣", "氣很", "很好")
    assert words[2].char == "貓" and words[0].char is None
    assert query_words("  ") == []
    assert len(query_words(" ".join(f"w{n}" for n in range(20)))) == 8


def test_a_term_is_quoted_for_fts5():
    assert fts_quote("天氣") == '"天氣"'
    assert fts_quote('a"b') == '"a""b"'


def test_tokens_are_estimated_one_for_a_cjk_character_and_one_for_four_others():
    assert estimate_tokens("") == 0 and estimate_tokens(None) == 0
    assert estimate_tokens("天氣") == 2
    assert estimate_tokens("hello world!") == 3
    assert estimate_tokens("天氣 good") == 2 + 2
