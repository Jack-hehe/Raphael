"""Tests for the judgment of a correction: may it become a replacement rule?"""

import pytest

from raphael_core.brain import mishearing
from raphael_core.brain.mishearing import is_distinctive, is_plausible, may_become_rule

# --- distinctive forms --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "form",
    [
        # Two Latin letters or fewer.
        "to", "py", "ok", "O S", "x86", "a", "I",
        # Common function words, short and long.
        "too", "two", "the", "this", "that", "them", "their", "which", "with", "would", "is", "it",
        "in", "on", "an", "THE", "  This  ",
        # Made of function words only.
        "in a", "to the", "of the",
        # A single CJK character, or the function words and particles of Chinese and Japanese.
        "的", "了", "是", "在", "這", "那", "我", "你", "你們", "為什麼", "の", "です", "お",
        # Two CJK characters: too often a real word.
        "轉入", "明天", "手機", "拉菲", "會議", "ラフ",
        # Nothing to go by.
        "", "   ", "123", "!!", "-",
    ],
)
def test_these_forms_are_not_distinctive(form):
    assert not is_distinctive(form)


@pytest.mark.parametrize(
    "form",
    [
        "cloud", "clod", "tail scale", "open router", "chat G P T", "git hub", "gem mini",
        "Typelist", "gpt4", "curse or", "pie thon",
        "拉菲爾", "李慕璐", "托耳金", "轉入帳", "ラファエロ", "손님들",
        "手 phone", "Hermes 的",
    ],
)
def test_these_forms_are_distinctive(form):
    assert is_distinctive(form)


def test_three_latin_letters_are_the_least_and_digits_do_not_count():
    assert is_distinctive("cat") and not is_distinctive("ca") and not is_distinctive("c4")
    assert not is_distinctive("12 3") and is_distinctive("gpt 4") and not is_distinctive("x 86")


def test_three_cjk_characters_are_the_least():
    assert is_distinctive("拉斐爾") and not is_distinctive("拉斐")
    # The letters of one script do not make up for the other's.
    assert not is_distinctive("我 is") and not is_distinctive("轉入 x")


# --- plausible pairs ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "form"),
    [
        # The motivating sound-alikes.
        ("Claude", "cloud"),
        ("Claude", "clod"),
        # Spelled alike apart from spaces, hyphens and case.
        ("Tailscale", "tail scale"),
        ("OpenRouter", "open router"),
        ("ChatGPT", "chat G P T"),
        ("GitHub", "git hub"),
        ("GPT-4", "gpt 4"),
        # A few letters apart.
        ("Gemini", "gem mini"),
        ("Cursor", "curse or"),
        ("Python", "pie thon"),
        ("Alice", "Allice"),
        ("Typeless", "TypeList"),
        # Sound alike by the phonetic key.
        ("Tauri", "tow ree"),
        # A shared stem.
        ("migration", "migrate"),
        ("Hermes Agent", "Hermes"),
        # CJK: the same length and a character in the same place.
        ("拉斐爾", "拉菲爾"),
        ("托爾金", "托耳金"),
        ("ラファエル", "ラファエロ"),
        ("手機殼", "首機殼"),
    ],
)
def test_these_pairs_are_plausible_recognition_errors(word, form):
    assert is_plausible(word, form)


@pytest.mark.parametrize(
    ("word", "form"),
    [
        # A synonym, not a mishearing.
        ("workshop", "meeting"),
        ("後天", "明天幾"),
        # CJK of different lengths, or the same length with nothing in common.
        ("服務器", "電腦網"),
        ("利姆路", "李慕璐"),
        ("拉斐爾", "拉菲爾老師"),
        # Another script, or no text at all.
        ("Claude", "電腦"),
        ("拉斐爾", "Rafael"),
        ("Claude", ""),
        ("", "cloud"),
        ("Claude", "!!!"),
    ],
)
def test_these_pairs_are_not_plausible(word, form):
    assert not is_plausible(word, form)


def test_plausibility_ignores_case_spaces_and_punctuation():
    assert is_plausible("Node.js", "node js")
    assert is_plausible("TAILSCALE", "Tail-Scale")


# --- may it become a rule -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "form"),
    [
        ("Claude", "cloud"),
        ("Tailscale", "tail scale"),
        ("拉斐爾", "拉菲爾"),
        ("ラファエル", "ラファエロ"),
    ],
)
def test_a_distinctive_and_plausible_pair_may_become_a_rule(word, form):
    assert may_become_rule(word, form)


@pytest.mark.parametrize(
    ("word", "form"),
    [
        ("two", "to"),  # at most two letters
        ("parser", "py"),
        ("them", "the"),  # a function word
        ("which", "this"),
        ("他", "我"),  # a single CJK character
        ("轉錄", "轉入"),  # two CJK characters
        ("後天", "明天"),
        ("workshop", "meeting"),  # not alike
        ("服務器", "電腦網"),
    ],
)
def test_the_pairs_of_the_reported_failures_may_not(word, form):
    assert not may_become_rule(word, form)


# --- the pieces ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "code"),
    [
        ("Robert", "R163"),
        ("Rupert", "R163"),
        ("Rubin", "R150"),
        ("Ashcraft", "A261"),
        ("Tymczak", "T522"),
        ("Pfister", "P236"),
        ("Honeyman", "H555"),
        ("Claude", "C430"),
        ("cloud", "C430"),
        ("tow ree", "T600"),
        ("Café", "C100"),
        ("", ""),
        ("123", ""),
        ("拉斐爾", ""),
    ],
)
def test_the_american_soundex_code(word, code):
    assert mishearing._soundex(word) == code


@pytest.mark.parametrize(
    ("first", "second", "distance"),
    [("cloud", "claude", 2), ("", "abc", 3), ("abc", "abc", 0), ("kitten", "sitting", 3)],
)
def test_the_edit_distance_counts_insertions_deletions_and_substitutions(first, second, distance):
    assert mishearing._edit_distance(first, second) == distance
    assert mishearing._edit_distance(second, first) == distance


# --- dictionary words a transcript may hold -------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "text"),
    [
        ("OpenRouter", "我用歐噴 router 傳訊息"),
        ("Telegram", "跟 tele gram 傳訊息給你"),
        ("STT", "語音轉文字的 SST model"),
        ("Hermes Agent", "我想要變成類似Hermegie這樣子的感覺"),
        ("Hermes Agent", "home 目前 Herman Seligs 的金鏢在哪裡啊"),
        ("Typeless", "目前只有 typelist 的功能"),
        ("MCP", "plugins、NCP servers 之類的"),
        ("claude", "打開 Claude Code"),
        ("computer use", "還有 Computer News 這些"),
        ("拉斐爾", "拉菲爾老師"),
        ("拉斐爾", "no Chinese here at all"),
    ],
)
def test_a_word_said_or_misheard_may_be_in_the_transcript(word, text):
    assert mishearing.may_be_heard_in(word, text)


@pytest.mark.parametrize(
    ("word", "text"),
    [
        ("Commit", "歷史記錄要根據所有的提交記錄去做一個時間軸"),
        ("Repository", "然後倉庫保持乾淨"),
        ("Chief of Staff", "然後拉斐爾當幕僚長做總結"),
        ("Project", "幫我把專案記錄更新一下，跟 GitHub 對照"),
        ("Agent", "這個代理要可以自己操作電腦"),
        ("Windows", "把那個視窗拉到右邊"),
    ],
)
def test_a_latin_word_with_nothing_like_it_in_the_transcript_is_not(word, text):
    assert not mishearing.may_be_heard_in(word, text)


def test_a_known_misheard_form_in_the_transcript_counts():
    assert mishearing.may_be_heard_in("Alt", "右邊那個O S鍵", ["O S", "OS"])
    assert not mishearing.may_be_heard_in("Alt", "右邊那個鍵", ["O S", "OS"])
    # Speech-to-text wrote a CJK form for an English key once: the form is all that is like it.
    assert mishearing.may_be_heard_in("Alt", "按一下偶遇見就開始轉錄", ["偶遇見"])
