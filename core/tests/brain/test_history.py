import dataclasses

from raphael_core.brain.history import HistoryStore

from .conftest import item


def test_list_is_newest_first_and_searchable(conn):
    h = HistoryStore(conn)
    h.add(item(created_at_ms=1000, text="明天開會", raw_text="明天 開會"))
    h.add(item(created_at_ms=2000, text="寫信給老闆", raw_text="寫信給 老闆"))
    assert [i.text for i in h.list()] == ["寫信給老闆", "明天開會"]
    assert [i.text for i in h.list(query="開會")] == ["明天開會"]


def test_prune_and_latest(conn):
    h = HistoryStore(conn)
    h.add(item(created_at_ms=1000))
    h.add(item(created_at_ms=5000))
    assert h.prune(older_than_ms=2000) == 1
    assert h.latest().created_at_ms == 5000


def test_add_returns_the_new_id_and_get_returns_every_field(conn):
    h = HistoryStore(conn)
    cleaned = item(created_at_ms=1_700_000_000_123)
    fell_back = item(
        created_at_ms=1_700_000_000_456,
        raw_text="嗯 明天 開會",
        text="嗯 明天 開會",
        cleaned=False,
        fallback_reason="timeout",
        target_app=None,
        audio_seconds=2.5,
        stt_ms=321,
        llm_ms=654,
        model=None,
        cost_usd=0.0,
    )
    first, second = h.add(cleaned), h.add(fell_back)
    assert first != second
    assert h.get(first) == dataclasses.replace(cleaned, id=first)
    assert h.get(second) == dataclasses.replace(fell_back, id=second)
    assert h.get(first).cleaned is True
    assert h.get(second).cleaned is False


def test_get_returns_none_for_an_unknown_id(conn):
    assert HistoryStore(conn).get(404) is None


def test_delete_removes_the_item_and_says_whether_it_existed(conn):
    h = HistoryStore(conn)
    kept = h.add(item(created_at_ms=1000))
    gone = h.add(item(created_at_ms=2000))
    assert h.delete(gone) is True
    assert h.get(gone) is None
    assert h.get(kept) is not None
    assert h.delete(gone) is False


def test_latest_is_none_without_history(conn):
    assert HistoryStore(conn).latest() is None


def test_list_pages_with_limit_and_before_ms(conn):
    h = HistoryStore(conn)
    for ms in (1000, 2000, 3000, 4000, 5000):
        h.add(item(created_at_ms=ms))
    assert [i.created_at_ms for i in h.list(limit=2)] == [5000, 4000]
    # before_ms is exclusive: the next page starts under the last item of the previous one.
    assert [i.created_at_ms for i in h.list(limit=2, before_ms=4000)] == [3000, 2000]
    assert [i.created_at_ms for i in h.list(query="你好", limit=1, before_ms=3000)] == [2000]


def test_items_from_the_same_millisecond_list_newest_added_first(conn):
    h = HistoryStore(conn)
    h.add(item(created_at_ms=1000, text="first"))
    h.add(item(created_at_ms=1000, text="second"))
    assert [i.text for i in h.list()] == ["second", "first"]
    assert h.latest().text == "second"


def test_search_also_matches_the_raw_text(conn):
    h = HistoryStore(conn)
    h.add(item(text="OpenRouter 很方便", raw_text="open router 很方便"))
    h.add(item(text="別的事", raw_text="別的事"))
    assert [i.text for i in h.list(query="open router")] == ["OpenRouter 很方便"]
    assert h.list(query="沒有這個詞") == []


def test_search_takes_percent_underscore_and_backslash_literally(conn):
    h = HistoryStore(conn)
    for text in ("成功率 100% 了", "成功率 1000 了", "foo_bar", "fooxbar", "C:\\temp", "C:temp"):
        h.add(item(text=text, raw_text=text))
    assert [i.text for i in h.list(query="100%")] == ["成功率 100% 了"]
    assert [i.text for i in h.list(query="foo_bar")] == ["foo_bar"]
    assert [i.text for i in h.list(query="C:\\temp")] == ["C:\\temp"]


def test_texts_since_returns_the_final_text_from_that_time_on_newest_first(conn):
    h = HistoryStore(conn)
    h.add(item(created_at_ms=1000, text="old", raw_text="old raw"))
    h.add(item(created_at_ms=2000, text="edge", raw_text="edge raw"))
    h.add(item(created_at_ms=3000, text="new", raw_text="new raw"))
    assert h.texts_since(2000) == ["new", "edge"]
    assert h.texts_since(4000) == []


def test_prune_keeps_items_at_the_cutoff(conn):
    h = HistoryStore(conn)
    h.add(item(created_at_ms=1000))
    assert h.prune(older_than_ms=1000) == 0
    assert h.prune(older_than_ms=1001) == 1
    assert h.latest() is None


def test_the_speech_to_text_that_heard_a_dictation_is_kept(conn):
    h = HistoryStore(conn)
    with_stt = h.add(item(stt="openai/gpt-transcribe"))
    without = h.add(item())
    assert h.get(with_stt).stt == "openai/gpt-transcribe"
    assert h.get(without).stt is None
