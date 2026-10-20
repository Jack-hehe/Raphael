import itertools
import json
import time

import pytest

from raphael_core.brain import db
from raphael_core.brain.dictionary import DictionaryStore, Learned
from raphael_core.brain.textnorm import apply_replacements

from .conftest import learn


def ticking_clock():
    """A clock that moves 1000 ms on every call, so each added word gets its own time."""
    ticks = itertools.count(1000, 1000)
    return lambda: next(ticks)


def test_add_merges_misheard_forms(conn):
    d = DictionaryStore(conn)
    d.add("拉斐爾", ["拉菲爾"])
    e = d.add("拉斐爾", ["拉非爾"])
    assert sorted(e.misheard) == ["拉菲爾", "拉非爾"]
    assert len(d.list()) == 1


def test_replacements_longest_first(conn):
    d = DictionaryStore(conn)
    d.add("ラファエル先生", ["Lafia 先生"])
    d.add("拉斐爾", ["拉菲爾"])
    assert d.replacements() == [("Lafia 先生", "ラファエル先生"), ("拉菲爾", "拉斐爾")]


def test_empty_word_rejected(conn):
    with pytest.raises(ValueError):
        DictionaryStore(conn).add("   ")


def test_add_strips_the_word_and_drops_blank_and_repeated_forms(conn):
    e = DictionaryStore(conn).add("  拉斐爾 ", [" 拉菲爾 ", "", "   ", "拉菲爾"])
    assert (e.word, e.misheard, e.source) == ("拉斐爾", ["拉菲爾"], "manual")
    assert isinstance(e.id, int)


def test_misheard_is_stored_as_a_json_array(conn):
    DictionaryStore(conn).add("拉斐爾", ["拉菲爾", "拉非爾"])
    stored = conn.execute("SELECT misheard FROM dictionary").fetchone()[0]
    assert json.loads(stored) == ["拉菲爾", "拉非爾"]


def test_merging_keeps_the_old_forms_first_and_skips_known_ones(conn):
    d = DictionaryStore(conn)
    d.add("拉斐爾", ["拉菲爾"])
    e = d.add("拉斐爾", ["拉菲爾", "拉非爾"])
    assert e.misheard == ["拉菲爾", "拉非爾"]
    assert [x.misheard for x in d.list()] == [["拉菲爾", "拉非爾"]]


def test_adding_an_existing_word_returns_the_existing_entry(conn):
    d = DictionaryStore(conn)
    first = d.add("Tailscale", source="suggested")
    again = d.add("Tailscale", ["tail scale"], source="manual")
    assert again.id == first.id
    assert again.source == "suggested"
    assert again.misheard == ["tail scale"]
    assert d.add("Tailscale").misheard == ["tail scale"]  # nothing new: nothing lost


def test_list_is_newest_first(conn):
    d = DictionaryStore(conn, now_ms=ticking_clock())
    for word in ("甲", "乙", "丙"):
        d.add(word)
    assert [e.word for e in d.list()] == ["丙", "乙", "甲"]


def test_words_added_in_the_same_millisecond_list_newest_added_first(conn):
    d = DictionaryStore(conn, now_ms=lambda: 1000)
    d.add("甲")
    d.add("乙")
    assert [e.word for e in d.list()] == ["乙", "甲"]


def test_created_at_comes_from_the_clock(conn):
    DictionaryStore(conn, now_ms=lambda: 12345).add("甲")
    assert conn.execute("SELECT created_at_ms FROM dictionary").fetchone()[0] == 12345


def test_the_default_clock_is_the_wall_clock_in_milliseconds(conn):
    before = time.time_ns() // 1_000_000
    DictionaryStore(conn).add("甲")
    after = time.time_ns() // 1_000_000
    assert before <= conn.execute("SELECT created_at_ms FROM dictionary").fetchone()[0] <= after


def test_delete_says_whether_the_entry_existed(conn):
    d = DictionaryStore(conn)
    gone = d.add("甲")
    d.add("乙")
    assert d.delete(gone.id) is True
    assert [e.word for e in d.list()] == ["乙"]
    assert d.delete(gone.id) is False


def test_hint_words_are_the_words_newest_first(conn):
    d = DictionaryStore(conn, now_ms=ticking_clock())
    d.add("拉斐爾", ["拉菲爾"])
    d.add("Tailscale")
    assert d.hint_words() == ["Tailscale", "拉斐爾"]


def test_replacements_list_every_form_and_skip_words_without_any(conn):
    d = DictionaryStore(conn)
    d.add("OpenRouter", ["open router", "open rooter"])
    d.add("Tailscale")
    assert d.replacements() == [("open router", "OpenRouter"), ("open rooter", "OpenRouter")]


def test_dismiss_remembers_words(conn):
    d = DictionaryStore(conn)
    assert d.dismissed() == set()
    d.dismiss("Tailscale")
    d.dismiss("  Tailscale ")
    d.dismiss("GPT-SoVITS")
    assert d.dismissed() == {"Tailscale", "GPT-SoVITS"}


def test_dismissing_an_empty_word_is_rejected(conn):
    d = DictionaryStore(conn)
    with pytest.raises(ValueError, match="empty"):
        d.dismiss("  ")
    assert d.dismissed() == set()


def test_unknown_source_is_rejected(conn):
    d = DictionaryStore(conn)
    with pytest.raises(ValueError, match="source"):
        d.add("拉斐爾", source="typo")
    assert d.list() == []


def test_misheard_must_be_a_list_not_one_string(conn):
    # A bare string would be split into characters, and each one would become a replacement rule.
    d = DictionaryStore(conn)
    with pytest.raises(TypeError):
        d.add("拉斐爾", "拉菲爾")
    assert d.list() == []


# --- words learned from the user's corrections ----------------------------------------------------
# A learned pair is a hint (the word becomes a term of the dictionary, the form a "heard as" for the
# cleanup prompt) and only a rule when it passes `mishearing.may_become_rule`, is seen a second time
# and breaks no other replacement. The tests of what makes a form distinctive are in
# test_mishearing.py; these are about what the dictionary does with the verdict.


def test_learn_adds_a_new_word_as_learned_with_the_misheard_form_as_a_hint(conn):
    d = DictionaryStore(conn)
    assert learn(d, "Tailscale", "tail scale") is True
    [entry] = d.list()
    assert (entry.word, entry.misheard, entry.source) == ("Tailscale", [], "learned")
    assert d.hint_words() == ["Tailscale"]
    assert d.replacements() == []
    assert d.hint_forms() == [("tail scale", "Tailscale")]


def test_a_pair_becomes_a_rule_when_it_is_seen_a_second_time(conn):
    d = DictionaryStore(conn)
    assert learn(d, "Tailscale", "tail scale") is True
    assert d.replacements() == []
    assert learn(d, "Tailscale", "tail scale") is True
    assert d.replacements() == [("tail scale", "Tailscale")]
    [entry] = d.list()
    assert (entry.misheard, entry.source) == (["tail scale"], "learned")
    assert learn(d, "Tailscale", "tail scale") is False  # a rule already
    assert d.hint_forms() == [("tail scale", "Tailscale")], "told to the model once"


def test_learn_pair_says_what_a_correction_did(conn):
    d = DictionaryStore(conn)
    assert d.learn_pair("Alice", "Allice") is Learned.TERM
    assert d.learn_pair("Alice", "Alyce") is Learned.PENDING
    assert d.learn_pair("Alice", "Allice") is Learned.RULE
    assert d.learn_pair("Alice", "Allice") is Learned.NOTHING
    assert d.learn_pair("Allice", "Alice") is Learned.REVERTED
    assert d.learn_pair("Alice", "Allice") is Learned.NOTHING
    assert d.learn_pair("", "Allice") is Learned.NOTHING


def test_learn_keeps_a_new_form_of_a_known_word_a_hint_until_it_is_seen_again(conn):
    d = DictionaryStore(conn)
    d.add("Tailscale", ["tail scale"])
    assert learn(d, "Tailscale", "tailskill") is True
    [entry] = d.list()
    assert (entry.misheard, entry.source) == (["tail scale"], "manual")
    assert d.hint_forms() == [("tail scale", "Tailscale"), ("tailskill", "Tailscale")]
    assert learn(d, "Tailscale", "tailskill") is True
    [entry] = d.list()
    assert (entry.misheard, entry.source) == (["tail scale", "tailskill"], "manual")


def test_a_first_correction_does_not_rewrite_the_literal_word(conn):
    d = DictionaryStore(conn)
    assert learn(d, "Claude", "cloud") is True
    sentence = "Upload it to the cloud."
    assert apply_replacements(sentence, d.replacements()) == sentence
    assert d.hint_words() == ["Claude"]
    assert d.hint_forms() == [("cloud", "Claude")]


def test_the_count_of_a_pair_survives_reopening_the_database(tmp_path):
    path = tmp_path / "dictionary.db"
    first = db.connect(path)
    learn(DictionaryStore(first), "Claude", "cloud")
    first.close()
    second = db.connect(path)
    try:
        d = DictionaryStore(second)
        assert d.replacements() == [] and d.hint_forms() == [("cloud", "Claude")]
        assert learn(d, "Claude", "cloud") is True
        assert d.replacements() == [("cloud", "Claude")]
    finally:
        second.close()


@pytest.mark.parametrize(
    ("word", "misheard", "sentence"),
    [
        ("two", "to", "Go to the station."),  # at most two letters
        ("parser", "py", "Run py today."),
        ("them", "the", "Read the report."),  # a function word
        ("which", "this", "Keep this here."),
        ("他", "我", "我是學生。"),  # a single CJK character
        ("轉錄", "轉入", "我想把錢轉入帳戶"),  # two CJK characters
        ("後天", "明天", "明天我們再見。"),
        ("workshop", "meeting", "Join the meeting."),  # a synonym, not alike
        ("服務器", "電腦網", "電腦網已連接。"),  # CJK with no character in common
    ],
)
def test_pairs_that_may_not_be_rules_stay_hints_however_often_they_are_seen(
    conn, word, misheard, sentence
):
    d = DictionaryStore(conn)
    for _ in range(4):
        assert learn(d, word, misheard) is True, "the evidence is kept all the same"
    assert d.replacements() == []
    assert d.hint_words() == [word]
    assert d.hint_forms() == [(misheard, word)]
    assert apply_replacements(sentence, d.replacements()) == sentence


@pytest.mark.parametrize(
    ("word", "misheard", "sentence", "expected"),
    [
        # A Latin rule keeps its word boundaries.
        ("Claude", "cloud", "cloud clouds iCloud", "Claude clouds iCloud"),
        ("Tailscale", "tail scale", "Open tail scale.", "Open Tailscale."),
        # A rule of three CJK characters applies as it always did.
        ("拉斐爾", "拉菲爾", "請問拉菲爾先生。", "請問拉斐爾先生。"),
    ],
)
def test_distinctive_similar_pairs_become_rules_after_two_sightings(
    conn, word, misheard, sentence, expected
):
    d = DictionaryStore(conn)
    learn(d, word, misheard)
    assert apply_replacements(sentence, d.replacements()) == sentence
    learn(d, word, misheard)
    assert apply_replacements(sentence, d.replacements()) == expected


def test_a_rule_that_would_change_what_another_rule_writes_is_refused(conn):
    d = DictionaryStore(conn)
    learn(d, "Claude", "cloud")
    learn(d, "Claude", "cloud")
    assert d.replacements() == [("cloud", "Claude")]
    # "clod storage" for "cloud storage" would give "Claude storage": cloud is Claude now.
    assert learn(d, "cloud storage", "clod storage") is True
    assert learn(d, "cloud storage", "clod storage") is True
    assert d.replacements() == [("cloud", "Claude")]
    assert ("clod storage", "cloud storage") in d.hint_forms()
    assert apply_replacements("clod storage", d.replacements()) == "clod storage"


def test_the_same_pair_is_a_rule_when_nothing_else_is_in_the_way(conn):
    d = DictionaryStore(conn)
    learn(d, "cloud storage", "clod storage")
    learn(d, "cloud storage", "clod storage")
    assert d.replacements() == [("clod storage", "cloud storage")]


def test_a_rule_that_builds_on_another_rule_without_breaking_it_is_accepted(conn):
    d = DictionaryStore(conn)
    learn(d, "Claude", "cloud")
    learn(d, "Claude", "cloud")
    learn(d, "Claude Code", "cloud code")
    learn(d, "Claude Code", "cloud code")
    assert ("cloud code", "Claude Code") in d.replacements()
    assert apply_replacements("use cloud code", d.replacements()) == "use Claude Code"


def test_the_users_own_rules_are_not_judged(conn):
    d = DictionaryStore(conn)
    d.add("兩", ["to"])  # a rule by hand, as short and unlike as can be
    assert d.replacements() == [("to", "兩")]


def test_learn_skips_a_dismissed_word_in_any_letter_case(conn):
    d = DictionaryStore(conn)
    d.dismiss("tailscale")
    assert learn(d, "Tailscale", "tail scale") is False
    assert d.list() == []


@pytest.mark.parametrize(
    ("word", "misheard"),
    [("", "tail scale"), ("Tailscale", " "), ("Tailscale", "Tailscale"), ("Tailscale", "tailscale")],
)
def test_learn_skips_blank_or_same_pairs(conn, word, misheard):
    d = DictionaryStore(conn)
    assert learn(d, word, misheard) is False
    assert d.list() == []


def test_learn_does_not_turn_a_dictionary_word_into_a_misheard_form(conn):
    # The user changed a word the dictionary knows into another: that is no misheard form, and as
    # a replacement rule it would undo the dictionary's own word.
    d = DictionaryStore(conn)
    d.add("拉斐爾")
    assert learn(d, "拉菲爾", "拉斐爾") is False
    assert [e.word for e in d.list()] == ["拉斐爾"]


def test_learn_does_not_add_a_word_that_is_a_form_of_another_that_the_user_chose(conn):
    # 首機 → 手機 is a rule of the user's; learning 手機 → 首機 as well would swap them back and
    # forth, and the user typed that rule: a correction does not withdraw it.
    d = DictionaryStore(conn)
    d.add("手機", ["首機"])
    assert learn(d, "首機", "手機") is False
    assert d.replacements() == [("首機", "手機")]


def test_learn_does_not_swap_a_word_with_one_of_its_pending_forms_either(conn):
    d = DictionaryStore(conn)
    learn(d, "Alice", "Allice")
    assert d.learn_pair("Allice", "Alicia") is Learned.NOTHING
    assert d.hint_forms() == [("Allice", "Alice")]


def test_deleting_a_learned_word_dismisses_it_so_it_is_not_learned_again(conn):
    d = DictionaryStore(conn)
    learn(d, "手機", "首機")
    [entry] = d.list()
    assert d.delete(entry.id) is True
    assert d.dismissed() == {"手機"}
    assert learn(d, "手機", "首機") is False


def test_deleting_a_word_drops_what_the_brain_had_learned_about_it(conn):
    d = DictionaryStore(conn)
    learn(d, "Claude", "cloud")
    learn(d, "Claude", "cloud")
    learn(d, "Claude", "clod")
    learn(d, "Claude", "clawed")
    d.learn_pair("clawed", "Claude")  # the user rejected this one
    learn(d, "Alice", "Allice")
    claude = next(e for e in d.list() if e.word == "Claude")
    d.delete(claude.id)
    assert d.hint_forms() == [("Allice", "Alice")]
    rows = conn.execute(
        "SELECT form_key, status FROM dictionary_evidence WHERE word_key = 'claude'"
    )
    assert [tuple(row) for row in rows] == [("clawed", "blocked")], "the rejection is kept"


def test_deleting_a_manual_word_does_not_dismiss_it(conn):
    d = DictionaryStore(conn)
    entry = d.add("手機")
    d.delete(entry.id)
    assert d.dismissed() == set()


def test_the_learned_source_can_be_given_to_add(conn):
    assert DictionaryStore(conn).add("手機", source="learned").source == "learned"


def test_learning_protects_known_words_in_ascii_letter_case(conn):
    d = DictionaryStore(conn)
    d.add("Claude")
    assert learn(d, "Cloud", "claude") is False
    assert d.hint_words() == ["Claude"]


def test_learning_deduplicates_ascii_case_and_preserves_canonical_spelling(conn):
    d = DictionaryStore(conn)
    d.add("Alice", ["Allice"])
    assert learn(d, "alice", "ALLICE") is False
    assert learn(d, "ALICE", "Alyce") is True
    assert learn(d, "ALICE", "alyce") is True
    [entry] = d.list()
    assert (entry.word, entry.misheard, entry.source) == ("Alice", ["Allice", "Alyce"], "manual")


def test_learning_cannot_reassign_an_existing_alias_or_reverse_it_in_other_case(conn):
    d = DictionaryStore(conn)
    learn(d, "Alice", "Allice")
    learn(d, "Alice", "Allice")
    assert learn(d, "Alicia", "ALLICE") is False
    assert learn(d, "ALLICE", "Alicia") is False
    assert d.replacements() == [("Allice", "Alice")]


def test_learning_cannot_reassign_a_pending_form_either(conn):
    d = DictionaryStore(conn)
    learn(d, "Alice", "Allice")
    assert learn(d, "Alicia", "ALLICE") is False
    assert d.hint_forms() == [("Allice", "Alice")]


def test_learning_keeps_non_ascii_case_distinct_like_replacement_matching(conn):
    d = DictionaryStore(conn)
    d.add("Éclair")
    assert learn(d, "Patisserie", "éclair") is True
    assert ("éclair", "Patisserie") in d.hint_forms()


# --- the user changes a learned rule back ---------------------------------------------------------
# Retyping the form over the word that a rule wrote is evidence against the rule.


def test_changing_a_learned_rule_back_withdraws_it_and_blocks_the_pair(conn):
    d = DictionaryStore(conn)
    learn(d, "Claude", "cloud")
    learn(d, "Claude", "cloud")
    assert d.replacements() == [("cloud", "Claude")]
    assert learn(d, "cloud", "Claude") is True
    assert d.replacements() == [] and d.hint_forms() == []
    assert [e.misheard for e in d.list()] == [[]]
    # The word stays and is not dismissed; the pair is not learned again.
    assert d.hint_words() == ["Claude"] and d.dismissed() == set()
    assert learn(d, "Claude", "cloud") is False
    assert learn(d, "Claude", "cloud") is False
    assert d.replacements() == [] and d.hint_forms() == []


def test_changing_a_hint_back_blocks_the_pair_too(conn):
    d = DictionaryStore(conn)
    learn(d, "Claude", "cloud")
    assert d.learn_pair("cloud", "Claude") is Learned.REVERTED
    assert d.hint_forms() == [] and d.hint_words() == ["Claude"]
    assert d.learn_pair("Claude", "cloud") is Learned.NOTHING


def test_changing_back_only_withdraws_the_pair_that_was_changed(conn):
    d = DictionaryStore(conn)
    d.add("Claude", ["clod"])
    learn(d, "Claude", "cloud")
    learn(d, "Claude", "cloud")
    assert d.learn_pair("cloud", "Claude") is Learned.REVERTED
    assert d.replacements() == [("clod", "Claude")]


def test_changing_back_the_text_of_a_rule_the_user_typed_does_not_withdraw_it(conn):
    d = DictionaryStore(conn)
    d.add("Claude", ["cloud"])
    assert learn(d, "cloud", "Claude") is False
    assert d.replacements() == [("cloud", "Claude")]


def test_a_rule_added_by_hand_overrides_a_blocked_pair_and_a_hint(conn):
    d = DictionaryStore(conn)
    learn(d, "Claude", "cloud")
    d.learn_pair("cloud", "Claude")
    learn(d, "Tailscale", "tail scale")
    d.add("Claude", ["cloud"])
    d.add("Tailscale", ["tail scale"])
    assert d.replacements() == [("tail scale", "Tailscale"), ("cloud", "Claude")]
    assert d.hint_forms() == d.replacements(), "no hint is told twice"
    assert learn(d, "cloud", "Claude") is False, "a rule typed by the user is not withdrawn"


# --- the hints the brain reads --------------------------------------------------------------------


def test_hint_words_put_what_the_user_chose_before_what_was_learned(conn):
    d = DictionaryStore(conn, now_ms=ticking_clock())
    d.add("OpenRouter")
    learn(d, "Typeless", "type list")
    d.add("Tailscale", source="suggested")
    learn(d, "Gemini", "gem mini")
    # A list of hints is cut off at its end, so the learned words go there.
    assert d.hint_words() == ["Tailscale", "OpenRouter", "Gemini", "Typeless"]
    assert [e.word for e in d.list()] == ["Gemini", "Tailscale", "Typeless", "OpenRouter"]


def test_hint_forms_are_the_rules_and_then_the_newest_hints_first(conn):
    d = DictionaryStore(conn, now_ms=ticking_clock())
    d.add("拉斐爾", ["拉菲爾"])
    learn(d, "Claude", "cloud")
    learn(d, "Typeless", "type list")
    assert d.hint_forms() == [("拉菲爾", "拉斐爾"), ("type list", "Typeless"), ("cloud", "Claude")]
    assert d.replacements() == [("拉菲爾", "拉斐爾")]
