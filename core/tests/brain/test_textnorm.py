import itertools
import subprocess
import sys
from pathlib import Path

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
        # The word in other letter case is the word too: it is written right, not added to.
        (("Claude", "Claude Code"), "claude Code", "Claude Code"),
        (("Claude", "Claude Code"), "Claude code", "Claude Code"),
        (("Claude", "Claude Code"), "I use Claude code today", "I use Claude Code today"),
        (("Claude", "Claude Code"), "CLAUDE CODE and claude", "Claude Code and Claude Code"),
        (("code", "VS Code"), "open vs code", "open VS Code"),
        (("Lafia", "Lafia 先生"), "lafia 先生，LAFIA", "Lafia 先生，Lafia 先生"),
    ],
)
def test_a_word_that_contains_its_misheard_text_is_not_added_twice(rule, text, expected):
    # The brain applies the rules twice: to the speech-to-text result and to the cleaned text.
    once = apply_replacements(text, [rule])
    assert once == expected
    assert apply_replacements(once, [rule]) == expected


def test_the_word_in_other_case_is_left_alone_when_no_match_touches_it():
    # Only a mishearing, and the word it is part of, is written as the word.
    assert apply_replacements("CLAUDE and cloud", [("cloud", "Claude")]) == "CLAUDE and Claude"


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
    # the same or the other case ("A" in "a A"), with and without ASCII letters or digits at the
    # ends. A word that overlaps itself in another case ("a A a") is the hardest.
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
        ("A", "a A"),
        ("A", "A a"),
        ("A", "a-A-"),
        ("a", "A a "),
    ]:
        for text in texts:
            once = apply_replacements(text, [rule])
            assert apply_replacements(once, [rule]) == once, (rule, text)


# --- the converter ---


def test_importing_the_module_does_not_load_the_converters():
    # Loading the dictionaries takes about 100 ms; that waits until the first conversion.
    code = (
        "from raphael_core.brain import textnorm\n"
        "print(textnorm._converter.cache_info().currsize,"
        " textnorm._to_simplified.cache_info().currsize)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0 0"


# --- normalize_transcript and finish_cleaned_text ---


def test_normalize_transcript_converts_then_applies_the_replacements():
    rules = [("拉菲爾", "拉斐爾")]
    assert textnorm.normalize_transcript("拉菲爾说这个软件", rules) == "拉斐爾說這個軟體"


@pytest.mark.parametrize("chinese", ["zh-TW", "zh-HK", "yue-HK"])
@pytest.mark.parametrize("text", ["只要", "干擾", "系統", "發佈", "周末"])
def test_normalize_transcript_preserves_traditional_forms(chinese, text):
    assert textnorm.normalize_transcript(text, [], chinese=chinese) == text


@pytest.mark.parametrize(
    ("chinese", "expected"),
    [
        ("zh-TW", "這個軟體會干擾系統，後面的頭髮。"),
        ("zh-HK", "這個軟件會干擾系統，後面的頭髮。"),
        ("yue-HK", "這個軟件會干擾系統，後面的頭髮。"),
    ],
)
def test_normalize_transcript_still_converts_pure_simplified(chinese, expected):
    assert textnorm.normalize_transcript(
        "这个软件会干扰系统，后面的头发。", [], chinese=chinese
    ) == expected


def test_finish_cleaned_text_converts_and_applies_the_replacements():
    rules = [("拉菲爾", "拉斐爾")]
    assert textnorm.finish_cleaned_text("拉菲爾说：打开软件。", rules) == "拉斐爾說：開啟軟體。"


def test_finish_cleaned_text_leaves_a_text_with_hiragana_to_the_replacements_only():
    # OpenCC would turn 学校 into 學校 (and 会 into 會): a text with kana is Japanese.
    rules = [("ラファエロ", "ラファエル")]
    text = "ラファエロさんと学校で会います。"
    assert textnorm.finish_cleaned_text(text, rules) == "ラファエルさんと学校で会います。"


def test_finish_cleaned_text_gates_on_hiragana_in_the_whole_text():
    # A Chinese sentence next to a Japanese one: to_taiwan_traditional alone would convert the
    # Chinese part, but the service has never converted a cleaned text that has hiragana.
    text = "这个软件很好用。今日は学校に行きます。"
    assert textnorm.finish_cleaned_text(text, []) == text
    assert textnorm.normalize_transcript(text, []) == "這個軟體很好用。今日は学校に行きます。"


# OpenCC s2twp reads its input as Simplified Chinese. Run over text that is Traditional already, it
# gets some characters wrong: 就是只要 becomes 就是隻要, 干擾 becomes 幹擾. The cleanup model writes
# Traditional Chinese, so its reply is only converted where it still has Simplified characters.


@pytest.mark.parametrize(
    "text",
    [
        "就是只要按 Alt 就可以。",
        "他就是只會說好。",
        "這只是測試，只要一下。",
        "一隻貓。",
        "乾淨的房間。",
        "頭髮很長，我發現了。",
        "後面是皇后。",
        "裡面有一公里。",
        "台灣和臺灣。",
        "不要干擾他，我了解。",
        "舞台上的天干。",
    ],
)
def test_finish_cleaned_text_leaves_traditional_chinese_alone(text):
    assert textnorm.finish_cleaned_text(text, []) == text


def test_opencc_alone_would_corrupt_that_traditional_chinese():
    # Why the test above matters: these are the errors it guards against.
    assert to_taiwan_traditional("就是只要按 Alt 就可以。") == "就是隻要按 Alt 就可以。"
    assert to_taiwan_traditional("不要干擾他。") == "不要幹擾他。"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("明天下午三点开会。", "明天下午三點開會。"),
        ("后面的头发。", "後面的頭髮。"),
        ("这个软件很好用。", "這個軟體很好用。"),
    ],
)
def test_finish_cleaned_text_converts_a_sentence_written_in_simplified_chinese(text, expected):
    assert textnorm.finish_cleaned_text(text, []) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("幫我打开 YouTube。", "幫我開啟 YouTube。"),
        ("就是只要按這個开關。", "就是只要按這個開關。"),
        ("資料我後面再发給你。", "資料我後面再發給你。"),
    ],
)
def test_finish_cleaned_text_converts_only_the_simplified_words_of_a_traditional_sentence(
    text, expected
):
    assert textnorm.finish_cleaned_text(text, []) == expected


def test_finish_cleaned_text_judges_each_sentence_on_its_own():
    text = "就是只要按 Alt。明天开会\n後面是皇后。"
    assert textnorm.finish_cleaned_text(text, []) == "就是只要按 Alt。明天開會\n後面是皇后。"


def test_a_conversion_that_changes_the_length_is_taken_whole():
    # s2twp writes some words with more or fewer characters; the whole word is taken.
    assert textnorm.finish_cleaned_text("這個U盘給你。", []) == "這個隨身碟給你。"


def test_the_shared_characters_are_those_of_the_installed_opencc(tmp_path):
    """_SHARED_CHARACTERS lists the keys of OpenCC's STCharacters that are among their own
    Traditional forms (只 is 只 or 隻). It was generated from OpenCC 1.4.2; this regenerates it."""
    tool = Path(opencc.__file__).parent / "clib" / "bin" / "opencc_dict.exe"
    table = Path(opencc.__file__).parent / "clib" / "share" / "opencc" / "STCharacters.ocd2"
    if not tool.exists() or not table.exists():
        pytest.skip("this OpenCC has no opencc_dict tool")
    out = tmp_path / "st.txt"
    subprocess.run(
        [str(tool), "-i", str(table), "-o", str(out), "-f", "ocd2", "-t", "text"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    shared = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        key, values = line.split("\t")
        if key in values.split(" "):
            shared.add(key)
    assert shared == set(textnorm._SHARED_CHARACTERS)
    assert {"只", "后", "台", "干", "里"} <= shared and "发" not in shared


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


# --- the Chinese variants ---
# The script and usage follow the Chinese written form the user chose: Taiwan (s2twp, as above),
# Hong Kong and Cantonese (s2hk), Mainland (t2s), or none at all (no conversion).


def test_the_opencc_configurations_the_variants_use_are_installed():
    for config in ("s2twp.json", "s2hk.json", "t2s.json"):
        opencc.OpenCC(config)  # raises when the configuration is missing


@pytest.mark.parametrize(
    ("variant", "text", "expected"),
    [
        ("zh-TW", "这个软件的数据。", "這個軟體的資料。"),
        ("zh-HK", "这个软件的数据。", "這個軟件的數據。"),
        ("yue-HK", "我哋嘅软件。", "我哋嘅軟件。"),
        ("zh-CN", "這個軟件的數據。", "这个软件的数据。"),
        ("zh-CN", "这个软件的数据。", "这个软件的数据。"),
        (None, "这个軟件。", "这个軟件。"),
    ],
)
def test_normalize_transcript_writes_the_chosen_chinese(variant, text, expected):
    assert textnorm.normalize_transcript(text, [], chinese=variant) == expected


@pytest.mark.parametrize("variant", ["zh-TW", "zh-HK", "yue-HK", "zh-CN"])
def test_every_variant_leaves_japanese_sentences_alone(variant):
    text = "今日は学校で会います。"
    assert textnorm.normalize_transcript(text, [], chinese=variant) == text
    assert textnorm.finish_cleaned_text(text, [], chinese=variant) == text


def test_normalize_transcript_is_taiwan_unless_told_otherwise():
    assert textnorm.normalize_transcript("软件", []) == "軟體"
    assert textnorm.finish_cleaned_text("软件。", []) == "軟體。"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("这个软件的数据。", "這個軟件的數據。"),
        ("幫我打开視窗。", "幫我打開視窗。"),
        ("後面的头发。", "後面的頭髮。"),
    ],
)
def test_finish_cleaned_text_converts_leftover_simplified_to_hong_kong(text, expected):
    assert textnorm.finish_cleaned_text(text, [], chinese="zh-HK") == expected
    assert textnorm.finish_cleaned_text(text, [], chinese="yue-HK") == expected


@pytest.mark.parametrize(
    "text",
    ["就是只要按 Alt 就可以。", "不要干擾他。", "這只是測試。", "我哋聽日去咗。", "裏面有數據。"],
)
def test_finish_cleaned_text_leaves_hong_kong_traditional_alone(text):
    assert textnorm.finish_cleaned_text(text, [], chinese="zh-HK") == text


def test_s2hk_alone_would_corrupt_that_traditional_chinese_too():
    assert opencc.OpenCC("s2hk.json").convert("就是只要按 Alt。") == "就是隻要按 Alt。"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("这个软件很好用。", "这个软件很好用。"),
        ("這個軟件很好用。", "这个软件很好用。"),
        ("我们明天開會。", "我们明天开会。"),
        ("就是只要按 Alt。", "就是只要按 Alt。"),
    ],
)
def test_finish_cleaned_text_converts_leftover_traditional_to_simplified(text, expected):
    assert textnorm.finish_cleaned_text(text, [], chinese="zh-CN") == expected


def test_without_a_chinese_variant_nothing_is_converted():
    text = "这个軟體。"
    assert textnorm.finish_cleaned_text(text, [("軟體", "app")], chinese=None) == "这个app。"


def test_an_unknown_variant_is_refused():
    with pytest.raises(ValueError):
        textnorm.normalize_transcript("x", [], chinese="zh-SG")
