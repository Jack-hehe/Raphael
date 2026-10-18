import itertools
import json
import time

import pytest

from raphael_core.brain.dictionary import DictionaryStore


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


def test_learn_adds_a_new_word_with_the_misheard_form_as_learned(conn):
    d = DictionaryStore(conn)
    assert d.learn("Tailscale", "tail scale") is True
    [entry] = d.list()
    assert (entry.word, entry.misheard, entry.source) == ("Tailscale", ["tail scale"], "learned")


def test_learn_appends_the_form_to_a_known_word_and_keeps_its_source(conn):
    d = DictionaryStore(conn)
    d.add("Tailscale", ["tail scale"])
    assert d.learn("Tailscale", "tailskill") is True
    [entry] = d.list()
    assert (entry.misheard, entry.source) == (["tail scale", "tailskill"], "manual")


def test_learning_a_known_pair_again_changes_nothing(conn):
    d = DictionaryStore(conn)
    assert d.learn("Tailscale", "tail scale") is True
    assert d.learn("Tailscale", "tail scale") is False
    assert [e.misheard for e in d.list()] == [["tail scale"]]


def test_learn_skips_a_dismissed_word_in_any_letter_case(conn):
    d = DictionaryStore(conn)
    d.dismiss("tailscale")
    assert d.learn("Tailscale", "tail scale") is False
    assert d.list() == []


@pytest.mark.parametrize(
    ("word", "misheard"),
    [("", "tail scale"), ("Tailscale", " "), ("Tailscale", "Tailscale"), ("Tailscale", "tailscale")],
)
def test_learn_skips_blank_or_same_pairs(conn, word, misheard):
    d = DictionaryStore(conn)
    assert d.learn(word, misheard) is False
    assert d.list() == []


def test_learn_does_not_turn_a_dictionary_word_into_a_misheard_form(conn):
    # The user changed a word the dictionary knows into another: that is no misheard form, and as
    # a replacement rule it would undo the dictionary's own word.
    d = DictionaryStore(conn)
    d.add("拉斐爾")
    assert d.learn("拉菲爾", "拉斐爾") is False
    assert [e.word for e in d.list()] == ["拉斐爾"]


def test_learn_does_not_add_a_word_that_is_a_misheard_form_of_another(conn):
    # 首機 → 手機 is a rule; learning 手機 → 首機 as well would swap them back and forth.
    d = DictionaryStore(conn)
    d.learn("手機", "首機")
    assert d.learn("首機", "手機") is False
    assert d.replacements() == [("首機", "手機")]


def test_deleting_a_learned_word_dismisses_it_so_it_is_not_learned_again(conn):
    d = DictionaryStore(conn)
    d.learn("手機", "首機")
    [entry] = d.list()
    assert d.delete(entry.id) is True
    assert d.dismissed() == {"手機"}
    assert d.learn("手機", "首機") is False


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
    assert d.learn("Cloud", "claude") is False
    assert d.hint_words() == ["Claude"]


def test_learning_deduplicates_ascii_case_and_preserves_canonical_spelling(conn):
    d = DictionaryStore(conn)
    d.add("Alice", ["Allice"])
    assert d.learn("alice", "ALLICE") is False
    assert d.learn("ALICE", "Alyce") is True
    [entry] = d.list()
    assert (entry.word, entry.misheard, entry.source) == ("Alice", ["Allice", "Alyce"], "manual")


def test_learning_cannot_reassign_an_existing_alias_or_reverse_it_in_other_case(conn):
    d = DictionaryStore(conn)
    d.learn("Alice", "Allice")
    assert d.learn("Alicia", "ALLICE") is False
    assert d.learn("ALLICE", "Alicia") is False
    assert d.replacements() == [("Allice", "Alice")]


def test_learning_keeps_non_ascii_case_distinct_like_replacement_matching(conn):
    d = DictionaryStore(conn)
    d.add("Éclair")
    assert d.learn("Patisserie", "éclair") is True
    assert ("éclair", "Patisserie") in d.replacements()
