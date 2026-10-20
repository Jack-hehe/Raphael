import pytest

from raphael_core.brain.suggest import suggest_words

# Characters that look like others, or are easy to mistype, are written by code point.
MIDDLE_DOT = chr(0x30FB)  # the dot between the parts of a katakana name
PROLONGED = chr(0x30FC)  # the long-vowel mark; it is not the kanji for "one" (U+4E00)


def suggest(texts, **kwargs):
    """suggest_words with nothing known and nothing dismissed, unless given."""
    kwargs.setdefault("known", set())
    kwargs.setdefault("dismissed", set())
    return suggest_words(texts, **kwargs)


# --- what is suggested, and what is not ---


def test_suggests_frequent_proper_nouns_only():
    texts = (
        ["用 Tailscale 連回家"] * 4
        + ["OpenRouter 很方便"] * 5
        + ["裝了 GPT-SoVITS"] * 3
        + ["OK 好"] * 6
        + ["the end"] * 9
    )
    assert suggest_words(texts, known={"openrouter"}, dismissed=set()) == [
        ("Tailscale", 4),
        ("GPT-SoVITS", 3),
    ]


def test_respects_dismissed_and_min_count():
    texts = ["Tailscale"] * 4 + ["ラファエル先生"] * 2
    assert suggest_words(texts, known=set(), dismissed={"Tailscale"}) == []


# --- Latin words ---


@pytest.mark.parametrize(
    "word",
    [
        "Tailscale",  # a capital letter
        "iPhone",  # a capital letter inside
        "gpt4",  # a digit
        "e-mail",  # a hyphen
        "GPT-SoVITS",
        "Node.js",  # a dot inside does not end the word
    ],
)
def test_latin_words_with_a_capital_letter_a_digit_or_a_hyphen_are_candidates(word):
    # Glued to the Chinese text on both sides, where there are no spaces to find the word by.
    assert suggest([f"用{word}連回家"] * 3) == [(word, 3)]


@pytest.mark.parametrize("word", ["tailscale", "node.js", "e.g"])
def test_plain_lowercase_words_are_not_candidates(word):
    assert suggest([f"用{word}連回家"] * 3) == []


@pytest.mark.parametrize(
    "text",
    [
        "Tailscale.",
        "Tailscale,",
        "Tailscale-",
        "-Tailscale",
        "(Tailscale)",
        "「Tailscale」",
        "Tailscale。",
    ],
)
def test_punctuation_around_a_word_is_not_part_of_it(text):
    assert suggest([text] * 3) == [("Tailscale", 3)]


def test_a_word_starting_with_a_digit_is_no_candidate_and_is_not_cut():
    # "Password" is another word than "1Password", so it must not be offered in its place.
    assert suggest(["1Password"] * 3) == []


def test_a_latin_word_needs_two_letters():
    # The "A" of "A 方案" is no name, and a digit is no letter: "x86" and "A4" have only one.
    assert suggest(["A 方案", "B 方案", "用X光", "x86", "A4"] * 3) == []
    assert suggest(["Qt"] * 3) == [("Qt", 3)]


def test_cjk_text_next_to_a_word_does_not_hide_it():
    # Chinese and Japanese have no spaces around a Latin word.
    texts = ["用Tailscale連回家", "Tailscaleを使う", "使うTailscale"]
    assert suggest(texts) == [("Tailscale", 3)]


@pytest.mark.parametrize(
    "word",
    [
        f"Caf{chr(0xE9)}",  # Café
        f"Pok{chr(0xE9)}mon",  # Pokémon
        f"{chr(0xC9)}COLE",  # ÉCOLE: "COLE" is not a word either
        "Didn't",
        f"Didn{chr(0x2019)}t",  # with a typographic apostrophe
        "O'Brien",  # "Brien" is not a word either
        "GPT-4's",  # nor is "GPT", what is left of a hyphenated word
    ],
)
def test_a_word_touching_an_accented_letter_or_an_apostrophe_is_no_word(word):
    assert suggest([f"用{word}連回家"] * 3) == []


# --- everyday words that began a sentence ---


def test_a_capitalized_word_is_dropped_when_its_lowercase_form_is_met_too():
    # Alone, "This" could be a name; a "this" elsewhere shows that it is an everyday word.
    assert suggest(["This is fine"] * 3) == [("This", 3)]
    assert suggest(["This is fine"] * 3 + ["I like this"]) == []
    # The lowercase form counts even when it is glued to CJK text.
    assert suggest(["This is fine"] * 3 + ["我覺得this很好"]) == []


@pytest.mark.parametrize(
    "word, count",
    [
        ("OpenRouter", 3),  # an inner capital
        ("GPT", 3),  # capitals only
        ("Python3", 4),  # a digit; the lowercase spelling has one too, so it counts as well
        ("GPT-SoVITS", 4),  # a hyphen; likewise
    ],
)
def test_more_than_a_first_capital_keeps_a_word_even_with_its_lowercase_form(word, count):
    assert suggest([word] * 3 + [word.lower()]) == [(word, count)]


# --- katakana words ---


def test_katakana_words_need_three_characters():
    texts = ["請ラファエル先生幫忙"] * 3 + ["アイ"] * 5
    assert suggest(texts) == [("ラファエル", 3)]


def test_the_prolonged_sound_mark_belongs_to_a_katakana_word():
    computer = f"コンピュ{PROLONGED}タ{PROLONGED}"
    game = f"ゲ{PROLONGED}ム"  # three characters only with the mark
    assert suggest([f"{computer}と{game}"] * 3) == [(game, 3), (computer, 3)]


def test_katakana_letters_reach_up_to_the_vu():
    # ヴァイオリン: ヴ (U+30F4) comes after ン (U+30F3) in the block.
    violin = "".join(chr(code) for code in (0x30F4, 0x30A1, 0x30A4, 0x30AA, 0x30EA, 0x30F3))
    assert suggest([violin] * 3) == [(violin, 3)]


@pytest.mark.parametrize(
    "text, expected",
    [
        # Marks and dots without a katakana letter are no word: an ellipsis, a stretched vowel.
        (MIDDLE_DOT * 3, []),
        (PROLONGED * 3, []),
        # A bullet in front of a word, or the long-vowel mark of a hiragana, is not part of it.
        (f"{MIDDLE_DOT}リンゴ", [("リンゴ", 3)]),
        (f"ね{PROLONGED}ラファエル", [("ラファエル", 3)]),
        # The dot between the parts of a name ends a word.
        (f"ジョン{MIDDLE_DOT}スミス", [("ジョン", 3), ("スミス", 3)]),
    ],
)
def test_katakana_punctuation_is_no_word_and_ends_words(text, expected):
    assert suggest([text] * 3) == expected


# --- counting ---


def test_every_occurrence_counts():
    assert suggest(["Tailscale 比 Tailscale 快，Tailscale 也穩"]) == [("Tailscale", 3)]


def test_spellings_that_differ_in_case_are_one_word_in_the_most_common_spelling():
    assert suggest(["OpenRouter"] * 3 + ["Openrouter"] * 2 + ["OPENROUTER"]) == [("OpenRouter", 6)]
    assert suggest(["Openrouter"] * 3 + ["OpenRouter"]) == [("Openrouter", 4)]
    # The most common one wins, whichever was met first.
    assert suggest(["OPENROUTER"] + ["OpenRouter"] * 3) == [("OpenRouter", 4)]


def test_equally_common_spellings_go_to_the_one_met_first():
    assert suggest(["OpenRouter"] * 2 + ["Openrouter"] * 2) == [("OpenRouter", 4)]
    assert suggest(["Openrouter"] * 2 + ["OpenRouter"] * 2) == [("Openrouter", 4)]


def test_a_spelling_that_is_no_candidate_adds_nothing():
    # The all-lowercase one is not a candidate; two "OpenRouter" are too few.
    assert suggest(["OpenRouter"] * 2 + ["openrouter"] * 5) == []


def test_min_count_is_the_fewest_uses_that_count():
    texts = ["Tailscale"] * 2 + ["OpenRouter"] * 3
    assert suggest(texts) == [("OpenRouter", 3)]
    assert suggest(texts, min_count=2) == [("OpenRouter", 3), ("Tailscale", 2)]
    assert suggest(texts, min_count=4) == []


# --- order and limit ---


def test_orders_by_count_then_alphabetically():
    texts = ["Zed"] * 4 + ["Beta"] * 3 + ["Alpha"] * 3 + ["Mike"] * 5
    assert suggest(texts, limit=10) == [("Mike", 5), ("Zed", 4), ("Alpha", 3), ("Beta", 3)]
    # Alphabetical ignores case: iPhone sorts under I, not after Z.
    assert suggest(["Zed"] * 3 + ["iPhone"] * 3) == [("iPhone", 3), ("Zed", 3)]


def test_limit():
    texts = ["Alpha"] * 7 + ["Beta"] * 6 + ["Gamma"] * 5 + ["Delta"] * 4 + ["Epsilon"] * 3
    assert suggest(texts) == [("Alpha", 7), ("Beta", 6), ("Gamma", 5)]
    assert suggest(texts, limit=1) == [("Alpha", 7)]
    assert len(suggest(texts, limit=10)) == 5
    assert suggest(texts, limit=0) == []
    assert suggest(texts, limit=-1) == []


def test_left_out_words_do_not_use_up_the_limit():
    texts = ["Alpha"] * 7 + ["Beta"] * 6 + ["Gamma"] * 5 + ["Delta"] * 4
    assert suggest(texts, dismissed={"Alpha"}) == [("Beta", 6), ("Gamma", 5), ("Delta", 4)]
    assert suggest(texts, known={"alpha"}) == [("Beta", 6), ("Gamma", 5), ("Delta", 4)]


# --- what is left out ---


def test_known_and_dismissed_words_are_matched_ignoring_case():
    texts = ["Tailscale GPT-SoVITS OpenRouter ラファエル"] * 3
    result = suggest(texts, known={"TAILSCALE", "ラファエル"}, dismissed={"gpt-sovits"})
    assert result == [("OpenRouter", 3)]


def test_the_stoplist_words_are_never_suggested():
    texts = ["I OK AI API OS UI PC App The"] * 3
    assert suggest(texts, limit=20) == []


def test_the_stoplist_is_matched_exactly():
    # Unlike known and dismissed words, only the listed spellings are stopped.
    assert suggest(["Ok Api THE"] * 3) == [("Api", 3), ("Ok", 3), ("THE", 3)]


def test_nothing_to_suggest():
    assert suggest([]) == []
    assert suggest(["", "你好嗎，今天天氣很好"] * 5) == []
