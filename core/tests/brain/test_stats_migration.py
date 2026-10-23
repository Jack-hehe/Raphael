"""Legacy history is backfilled once; deleted dictations are recovered as explicit estimates."""

from raphael_core.brain import db
from raphael_core.brain.history import HistoryStore
from raphael_core.brain.spend import SpendTracker
from .test_stats import NOW, TPE, at, store_at, period, say


def legacy(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    with monkeypatch.context() as old:
        old.setattr(db, "LATEST_VERSION", 19)
        conn = db.connect(path)
    return path, conn


def old_dictation(conn, when, text, *, audio=1, stt=300, llm=700, kind="dictation", model="m"):
    # A version-19 fixture predates the aggregate-aware HistoryStore writer.
    conn.execute(
        "INSERT INTO history (created_at_ms,kind,raw_text,text,cleaned,audio_seconds,stt_ms,llm_ms,model)"
        " VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)", (when, kind, text, text, audio, stt, llm, model),
    )
    conn.commit()


def cleanup(conn, when, tokens, *, purpose="cleanup", model="m"):
    SpendTracker(conn, now_ms=lambda: when, tz=TPE).record(
        purpose=purpose, model=model, cost_usd=0, prompt_tokens=1, completion_tokens=tokens,
    )


def test_migration_backfills_the_previous_counts_durations_and_heatmap_once(tmp_path, monkeypatch):
    path, conn = legacy(tmp_path, monkeypatch)
    old_dictation(conn, at(2026, 10, 13), "你好", audio=1, stt=200, llm=300)
    old_dictation(conn, NOW, "one two three", audio=2, stt=200, llm=300)
    old_dictation(conn, NOW, "ignored", kind="other")
    conn.close()
    conn = db.connect(path)
    before = store_at(conn, NOW).summary()
    assert before["all"] == period(2, 5, 3)
    assert before["today"] == period(1, 3, 2)
    assert before["time_saved_seconds"] == 3.5
    assert before["speed_wpm"] == 100
    assert before["active_days"] == before["streak_days"] == before["longest_streak_days"] == 2
    assert before["by_day"][-2:] == [
        {"date": "2026-10-13", "dictations": 1, "words": 2},
        {"date": "2026-10-14", "dictations": 1, "words": 3},
    ]
    assert before["estimated_words"] == 0
    HistoryStore(conn).clear()
    conn.close()
    conn = db.connect(path)
    try:
        assert store_at(conn, NOW).summary() == before
    finally:
        conn.close()


def test_a_partial_day_preserves_exact_rows_and_new_dictations_add_to_the_estimated_day(tmp_path, monkeypatch):
    path, conn = legacy(tmp_path, monkeypatch)
    old_dictation(conn, NOW, "字" * 40, audio=4)
    cleanup(conn, NOW - 500, 10)
    cleanup(conn, NOW - 10, 10)  # two chunks of the retained dictation, 40 / 20 = 2
    cleanup(conn, NOW - 60_000, 10)  # the deleted dictation, outside the retained cleanup's window
    conn.close()
    conn = db.connect(path)
    try:
        before = store_at(conn, NOW).summary()
        assert before["today"] == period(2, 60, 6)
        assert before["estimated_words"] == 20
        assert before["estimated_dictations"] == 1
        assert conn.execute("SELECT estimated FROM dictation_daily").fetchone()[0] == 1
        say(conn, NOW + 1, "one two", audio=1)
        after = store_at(conn, NOW).summary()
        assert after["today"] == period(3, 62, 7)
        assert after["estimated_words"] == 20 and after["estimated_dictations"] == 1
    finally:
        conn.close()


def test_model_ratios_are_used_when_supported_otherwise_the_pooled_ratio_is_used(tmp_path, monkeypatch):
    path, conn = legacy(tmp_path, monkeypatch)
    for index in range(3):
        for model, text, tokens in [("m", "字" * 10, 5), ("n", "one two", 4)]:
            when = NOW + index * 1000
            old_dictation(conn, when, text, model=model)
            cleanup(conn, when - 10, tokens, model=model)
    for model in ("m", "n", "unknown"):
        cleanup(conn, at(2026, 10, 12), 10, model=model)
    conn.close()
    conn = db.connect(path)
    try:
        result = store_at(conn, NOW).summary()
        # m: 20, n: 5, unknown: 13.33 (pooled 36/27); round the daily total once.
        assert result["estimated_words"] == 38
        assert result["estimated_dictations"] == 3
        assert result["all"]["words"] == 74
    finally:
        conn.close()


def test_without_overlap_only_cleanup_is_recovered_with_an_explicit_one_word_per_token_estimate(tmp_path, monkeypatch):
    path, conn = legacy(tmp_path, monkeypatch)
    cleanup(conn, NOW, 23)
    cleanup(conn, NOW + 1, 0)
    cleanup(conn, NOW + 2, 9000, purpose="stt")
    conn.close()
    conn = db.connect(path)
    try:
        result = store_at(conn, NOW).summary()
        assert result["all"] == period(2, 23, 0)
        assert result["estimated_words"] == 23 and result["estimated_dictations"] == 2
        assert result["speed_wpm"] is None
    finally:
        conn.close()


def test_missing_cleanup_dictations_use_a_paired_word_token_ratio_without_double_counting(tmp_path, monkeypatch):
    path, conn = legacy(tmp_path, monkeypatch)
    for when, words, tokens in [(NOW - 2000, 10, 5), (NOW, 30, 15)]:
        old_dictation(conn, when, "字" * words, audio=words / 10)
        cleanup(conn, when - 10, tokens)
    cleanup(conn, at(2026, 10, 12), 20)
    cleanup(conn, at(2026, 10, 12) + 1000, 40)
    cleanup(conn, at(2026, 10, 11), 500, purpose="agent")
    conn.close()
    conn = db.connect(path)
    try:
        result = store_at(conn, NOW).summary()
        assert result["all"] == period(4, 160, 16)
        assert result["estimated_dictations"] == 2
        assert result["estimated_words"] == 120
        assert result["time_saved_seconds"] == 220
        assert result["by_day"][-3] == {"date": "2026-10-12", "dictations": 2, "words": 120}
        assert result["today"] == period(2, 40, 4)
        # Further retention and a reopen cannot replay the recovery.
        HistoryStore(conn).clear()
    finally:
        conn.close()
    conn = db.connect(path)
    try:
        assert store_at(conn, NOW).summary() == result
    finally:
        conn.close()
