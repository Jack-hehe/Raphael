import itertools
import subprocess
import sys

import opencc
import pytest

from raphael_core.brain import textnorm
from raphael_core.brain.textnorm import apply_replacements, to_taiwan_traditional

# --- to_taiwan_traditional ---


def test_converts_simplified_chinese_with_taiwan_phrases():
    assert to_taiwan_traditional("这个软件很好用。") == "這個軟體很好用。"


def test_leaves_japanese_sentences_alone():
    assert to_taiwan_traditional("今日は会社に行きます。") == "今日は会社に行きます。"


def test_mixed_lines():
    assert (
        to_taiwan_traditional("第一点：用 OpenRouter。\n今日はいい天気です。")
        == "第一點：用 OpenRouter。\n今日はいい天気です。"
    )


def test_katakana_word_inside_chinese():
    assert to_taiwan_traditional("我想请ラファエル先生帮忙") == "我想請ラファエル先生幫忙"


def test_english_untouched():
    assert to_taiwan_traditional("ship it by Friday") == "ship it by Friday"


@pytest.mark.parametrize(
    "sentence",
    [
        "学校で勉強します。",
        "明日会社に来てください。",
        "日本の国です。",
        "私は医者になりたい。",
    ],
)
def test_japanese_kanji_that_opencc_would_turn_traditional_stay_as_they_are(sentence):
    assert to_taiwan_traditional(sentence) == sentence


@pytest.mark.parametrize("delimiter", ["。", "！", "？", "!", "?", "\n"])
def test_each_sentence_is_judged_on_its_own(delimiter):
    # Chinese, Japanese, Chinese. The three together are 25 % hiragana, so the Chinese parts only
    # come out Traditional when the text is split at the delimiter.
    text = f"这个软件很好用{delimiter}会社に来ました{delimiter}后来"
    expected = f"這個軟體很好用{delimiter}会社に来ました{delimiter}後來"
    assert to_taiwan_traditional(text) == expected


def test_chinese_sentences_with_every_kind_of_delimiter():
    text = "我们开会了！后来呢？还没有。\n明天开会"
    assert to_taiwan_traditional(text) == "我們開會了！後來呢？還沒有。\n明天開會"


@pytest.mark.parametrize(
    "text, expected",
    [
        # Spaces, blank lines and Windows line breaks come back exactly as they were.
        ("这个软件\n\n  很好用。 \r\n第二点  ", "這個軟體\n\n  很好用。 \r\n第二點  "),
        # Delimiters at the start, repeated, and at the end.
        ("\n。这个软件！！\n", "\n。這個軟體！！\n"),
        ("。！？!?\n", "。！？!?\n"),
        ("", ""),
    ],
)
def test_delimiters_spaces_and_line_breaks_are_kept_exactly(text, expected):
    assert to_taiwan_traditional(text) == expected


def test_a_sentence_with_at_least_ten_percent_hiragana_is_japanese():
    # One hiragana among 10 CJK characters is exactly 10 %: Japanese, so left alone.
    assert to_taiwan_traditional("软" * 9 + "の") == "软" * 9 + "の"
    # Among 11 it is under 10 %: Chinese, so converted.
    assert to_taiwan_traditional("软" * 10 + "の") == "軟" * 10 + "の"


@pytest.mark.parametrize(
    "katakana",
    [
        "\u30a2",  # ア, Katakana block
        "\u31f0",  # ㇰ, Katakana Phonetic Extensions
        "\uff71",  # ｱ, halfwidth
    ],
)
def test_katakana_counts_as_a_cjk_character_but_not_as_hiragana(katakana):
    # 11 CJK characters, one of them hiragana: under 10 %. Were the katakana not counted, the
    # sentence would be 1 in 10 and count as Japanese.
    sentence = "软" * 9 + "の" + katakana
    assert to_taiwan_traditional(sentence) == "軟" * 9 + "の" + katakana


@pytest.mark.parametrize(
    "han",
    [
        "\u3400",  # Extension A
        "\u4e00",  # the main block
        "\uf900",  # Compatibility Ideographs
    ],
)
def test_han_characters_of_every_block_count_as_cjk_characters(han):
    # 11 CJK characters, one of them hiragana: Chinese. Were the Han characters not counted, only
    # 软 and の would remain and the sentence would count as Japanese.
    assert to_taiwan_traditional("软" + han * 9 + "の").startswith("軟")


def test_a_sentence_without_hiragana_is_never_japanese():
    # U+2003E is a Han character of Extension B, which is not counted, so this sentence has no CJK
    # characters at all. That is no reason to leave it alone: it goes through OpenCC like any other.
    assert to_taiwan_traditional("\U0002003e") == "\U0002005e"


# --- apply_replacements ---


def test_replacements():
    rules = [("Lafia 先生", "ラファエル先生"), ("拉菲爾", "拉斐爾")]
    assert apply_replacements("lafia 先生你好，拉菲爾", rules) == "ラファエル先生你好，拉斐爾"


def test_ascii_letters_match_in_any_case_and_every_match_is_replaced():
    rules = [("openrouter", "OpenRouter")]
    text = "OPENROUTER, openrouter and OpenRouter"
    assert apply_replacements(text, rules) == "OpenRouter, OpenRouter and OpenRouter"


def test_everything_but_ascii_letters_matches_exactly():
    # In a rule with ASCII letters, only the ASCII letters ignore case.
    assert apply_replacements("CAFé CAFÉ", [("Café", "X")]) == "X CAFÉ"
    # A rule without ASCII letters is exact.
    assert apply_replacements("é É", [("É", "E")]) == "é E"
    # Unicode folds the Kelvin sign (U+212A) together with k; here it must not match.
    assert apply_replacements("\u212a", [("k", "x")]) == "\u212a"


def test_rules_apply_in_the_given_order():
    text = "Lafia 先生と Lafia"
    longest_first = [("Lafia 先生", "ラファエル先生"), ("Lafia", "ラファ")]
    assert apply_replacements(text, longest_first) == "ラファエル先生と ラファ"
    # With the shorter rule first, the longer one no longer finds its text.
    assert apply_replacements(text, longest_first[::-1]) == "ラファ 先生と ラファ"


def test_a_rule_with_empty_misheard_text_is_skipped():
    # str.replace("", "X") would put an X between all characters.
    assert apply_replacements("abc", [("", "X")]) == "abc"


@pytest.mark.parametrize("misheard", ["c drive", "磁碟"])  # with and without ASCII letters
def test_the_word_is_inserted_as_it_is(misheard):
    # In a regular expression replacement, \U would be an error and \1 and \g<0> group references.
    word = r"C:\Users\1 \g<0>"
    assert apply_replacements(f"open {misheard} now", [(misheard, word)]) == f"open {word} now"


def test_a_rule_matches_its_text_literally():
    # "." and "+" are not regular expression syntax here.
    rules = [("a.b", "X"), ("c++", "Y")]
    assert apply_replacements("axb a.b c++ C+", rules) == "axb X Y C+"


def test_no_rules_leave_the_text_alone():
    assert apply_replacements("你好", []) == "你好"


# --- apply_replacements: word boundaries ---


@pytest.mark.parametrize(
    "text",
    [
        "iCloud",  # a letter before the match
        "clouds",  # a letter after it
        "9cloud",  # a digit before it
        "cloud9",  # a digit after it
    ],
)
def test_an_ascii_rule_does_not_match_inside_a_longer_run_of_letters_and_digits(text):
    assert apply_replacements(text, [("cloud", "Claude")]) == text


def test_an_ascii_rule_matches_next_to_anything_that_is_not_a_letter_or_digit():
    text = "Cloud Code, (cloud), cloud's, cloud-based.\ncloud"
    expected = "Claude Code, (Claude), Claude's, Claude-based.\nClaude"
    assert apply_replacements(text, [("Cloud", "Claude")]) == expected


def test_only_the_ends_of_a_rule_that_are_letters_or_digits_are_checked():
    # "c++" starts with a letter but ends with a plus: "abc++" does not match, "c++x" does.
    assert apply_replacements("c++x abc++ c++", [("c++", "Cpp")]) == "Cppx abc++ Cpp"
    # ".net" starts with a dot but ends with a letter: "asp.net" matches, ".nets" does not.
    assert apply_replacements("asp.net .nets", [(".net", ".NET")]) == "asp.NET .nets"


def test_chinese_and_japanese_characters_next_to_a_match_do_not_block_it():
    rules = [("Lafia 先生", "ラファエル先生"), ("cloud", "Claude"), ("拉菲爾", "拉斐爾")]
    text = "我Lafia 先生你好，用cloud做事，我拉菲爾你"
    assert apply_replacements(text, rules) == "我ラファエル先生你好，用Claude做事，我拉斐爾你"


def test_non_ascii_rules_still_match_as_plain_substrings():
    # The accent of é is no ASCII letter either, so "é" is found in the middle of "café".
    rules = [("拉菲爾", "拉斐爾"), ("é", "e")]
    assert apply_replacements("我拉菲爾你 café", rules) == "我拉斐爾你 cafe"


# --- apply_replacements: a word that contains its misheard text ---


@pytest.mark.parametrize(
    "rule, text, expected",
    [
        (("Claude", "Claude Code"), "Claude", "Claude Code"),
        (("Claude", "Claude Code"), "Claude Code", "Claude Code"),
        (
            ("Claude", "Claude Code"),
            "I use Claude Code, Claude and CLAUDE.",
            "I use Claude Code, Claude Code and Claude Code.",
        ),
        (("code", "VS Code"), "open code and VS Code", "open VS Code and VS Code"),
        (("Lafia", "Lafia 先生"), "Lafia 先生，Lafia", "Lafia 先生，Lafia 先生"),
        (("拉菲爾", "拉菲爾先生"), "拉菲爾先生和拉菲爾", "拉菲爾先生和拉菲爾先生"),
    ],
)
def test_a_word_that_contains_its_misheard_text_is_not_added_twice(rule, text, expected):
    # The brain applies the rules twice: to the speech-to-text result and to the cleaned text.
    once = apply_replacements(text, [rule])
    assert once == expected
    assert apply_replacements(once, [rule]) == expected


def test_a_rule_that_hands_its_word_to_the_next_rule_does_not_add_it_twice():
    rules = [("Cloud", "Claude"), ("Claude", "Claude Code")]
    assert apply_replacements("Cloud", rules) == "Claude Code"
    once = apply_replacements("iCloud and Cloud Code", rules)
    assert once == "iCloud and Claude Code"
    assert apply_replacements(once, rules) == once


def test_a_word_inside_the_misheard_text_does_not_stop_the_rule():
    # Every match of "Claude Code" holds the word "Claude", and the rule must still replace it.
    rules = [("Claude Code", "Claude")]
    assert apply_replacements("use Claude Code now", rules) == "use Claude now"


def test_a_match_that_overlaps_the_word_is_left_alone_too():
    # After "go--" has become "go-go-", "go-" is found again: the end of the word and the "-" that
    # followed the first match. That is no mishearing either.
    rules = [("go-", "go-go")]
    once = apply_replacements("go--", rules)
    assert once == "go-go-"
    assert apply_replacements(once, rules) == once


@pytest.mark.parametrize(
    "rules",
    [
        [("Claude", "Claude Code")],
        [("Cloud", "Claude"), ("Claude", "Claude Code")],
        [("Lafia 先生", "ラファエル先生"), ("Lafia", "ラファエル"), ("拉菲爾", "拉斐爾")],
        [("open router", "OpenRouter"), ("openrouter", "OpenRouter")],
        [("拉菲爾", "拉菲爾先生"), ("code", "VS Code")],
    ],
)
def test_a_second_pass_over_the_result_changes_nothing(rules):
    texts = [
        "",
        "Claude",
        "claude code",
        "Claude Code, Claude",
        "iCloud, Cloud Code and cloud",
        "Lafia 先生你好，Lafia，拉菲爾",
        "拉菲爾先生和拉菲爾",
        "open router, OPENROUTER and OpenRouter",
        "use code or VS Code",
    ]
    for text in texts:
        once = apply_replacements(text, rules)
        assert apply_replacements(once, rules) == once, text


def test_a_rule_applied_to_its_own_result_changes_nothing_for_every_short_text():
    # Every text of up to six characters from a small alphabet, against rules shaped like the ones
    # that go wrong: the word holds the misheard text and adds to it on one side or the other, in
    # either case, with and without ASCII letters or digits at the ends.
    alphabet = "aA- "
    texts = [
        "".join(chars)
        for length in range(7)
        for chars in itertools.product(alphabet, repeat=length)
    ]
    for rule in [
        ("a", "a a"),
        ("a", "A a"),
        ("a-", "a-a"),
        ("-a", "a-a"),
        (" a", "a a"),
        ("a-a", "a-a-a"),
        ("A-", "a-A"),
    ]:
        for text in texts:
            once = apply_replacements(text, [rule])
            assert apply_replacements(once, [rule]) == once, (rule, text)


# --- the converter ---


def test_importing_the_module_does_not_load_the_converter():
    # Loading the dictionaries takes about 100 ms; that waits until the first conversion.
    code = (
        "from raphael_core.brain import textnorm\n"
        "print(textnorm._converter.cache_info().currsize)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_the_converter_is_created_once(monkeypatch):
    created = []
    real_opencc = opencc.OpenCC

    def counting_opencc(*args, **kwargs):
        created.append(args)
        return real_opencc(*args, **kwargs)

    monkeypatch.setattr(opencc, "OpenCC", counting_opencc)
    textnorm._converter.cache_clear()
    for _ in range(3):
        to_taiwan_traditional("这个软件很好用。我们开会了！")
    assert len(created) == 1
