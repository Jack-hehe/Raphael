"""Tests for the names of web pages that opened: what is kept, and that it can be cleared."""

import pytest

from raphael_core.agent.targets import MAX_TARGETS, OpenTargets


class Clock:
    def __init__(self, now=1000):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def targets(conn, clock):
    return OpenTargets(conn, clock)


def test_a_name_that_was_not_learned_has_no_address(targets):
    assert targets.get("reddit") is None


def test_a_learned_name_gives_its_address_back(targets):
    targets.learn("reddit", "https://www.reddit.com/")
    assert targets.get("reddit") == "https://www.reddit.com/"


def test_learning_a_name_again_replaces_its_address_and_counts_the_use(targets, conn, clock):
    targets.learn("reddit", "https://old.reddit.com/")
    clock.now = 2000
    targets.learn("reddit", "https://www.reddit.com/")
    assert targets.get("reddit") == "https://www.reddit.com/"
    row = conn.execute("SELECT uses, last_used_ms FROM open_targets").fetchone()
    assert (row["uses"], row["last_used_ms"]) == (2, 2000)


def test_an_address_that_may_not_be_opened_is_not_learned(targets):
    for url in ("http://example.com/", "https://localhost/", "file:///c:/x", "https://10.0.0.1/"):
        targets.learn("x", url)
    assert targets.get("x") is None


def test_an_address_that_may_not_be_opened_is_not_given_back_either(targets, conn):
    # A row put there by some other means: the door is checked on the way out as well.
    conn.execute("INSERT INTO open_targets VALUES ('bank', 'https://192.168.0.1/', 1, 5)")
    assert targets.get("bank") is None


@pytest.mark.parametrize("name", ["", "   ", "x" * 200])
def test_a_name_that_is_empty_or_too_long_is_not_learned(targets, conn, name):
    targets.learn(name, "https://example.com/")
    assert conn.execute("SELECT count(*) FROM open_targets").fetchone()[0] == 0
    assert targets.get(name) is None


def test_a_page_with_a_search_or_a_fragment_is_not_learned(targets):
    # What was searched for is what was said: only the page itself is kept.
    targets.learn("lofi", "https://www.youtube.com/results?search_query=lofi")
    targets.learn("part", "https://example.com/page#section")
    assert targets.get("lofi") is None and targets.get("part") is None


def test_the_names_that_are_kept_are_few(targets, conn, clock):
    for index in range(MAX_TARGETS + 5):
        clock.now = 1000 + index
        targets.learn(f"site{index}", f"https://site{index}.example.com/")
    assert conn.execute("SELECT count(*) FROM open_targets").fetchone()[0] == MAX_TARGETS
    assert targets.get("site0") is None  # the one used longest ago went
    assert targets.get(f"site{MAX_TARGETS + 4}") is not None


def test_prune_forgets_the_names_not_used_since_a_time(targets, clock):
    targets.learn("old", "https://old.example.com/")
    clock.now = 5000
    targets.learn("new", "https://new.example.com/")
    assert targets.prune(3000) == 1
    assert targets.get("old") is None and targets.get("new") is not None


def test_clear_forgets_everything(targets):
    targets.learn("a", "https://a.example.com/")
    targets.learn("b", "https://b.example.com/")
    assert targets.clear() == 2
    assert targets.get("a") is None
