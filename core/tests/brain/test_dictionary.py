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
