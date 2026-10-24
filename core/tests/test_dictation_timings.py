from raphael_core.tools.dictation_timings import parse, report


def test_legacy_headers_do_not_claim_cleanup_is_finished_and_counts_survive_paste():
    messages = [
        ("00", "dictation: recording"),
        ("01", '/audio/transcriptions "HTTP/1.1 429 Too Many Requests"'),
        ("02", "A stretch of speech could not be transcribed; it is left out"),
        ("10", "dictation: stopping"),
        ("10", 'event {"type":"progress","stage":"transcribing","done":15,"total":16}'),
        ("13", 'event {"type":"progress","stage":"cleaning","done":16,"total":16}'),
        ("13", '/chat/completions "HTTP/1.1 200 OK"'),
        ("16", '[app_lib::paste::windows] paste: CtrlV'),
        ("16", 'event {"type":"progress","stage":"done","done":0,"total":0}'),
    ]
    sessions = parse([f"[2026-10-24][16:39:{second}][INFO] {line}" for second, line in messages])
    session, = sessions
    assert session.delay("transcribed") == 3
    assert session.delay("cleaned") == 6
    assert session.delay("pasted") == 6
    assert (session.done_at_stop, session.segments, session.rate_limits, session.dropped) == (15, 16, 1, 1)
    assert "stop->pasted: n=1 p50=6.000s p90=6.000s" in report(sessions)


def test_missing_terminal_event_is_unknown_instead_of_a_zero_latency_sample():
    sessions = parse(["[2026-10-24][16:39:10] dictation: stopping"])
    assert "stop->pasted: n=0 p50=?s p90=?s" in report(sessions)
