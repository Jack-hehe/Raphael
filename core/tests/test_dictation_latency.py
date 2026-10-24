from raphael_core.tools.dictation_latency import measure


async def test_short_clean_tail_skips_paid_cleanup_through_real_stop_and_brain():
    baseline = await measure(legacy=True, scale=.001)
    current = await measure(legacy=False, scale=.001)
    assert (baseline.cloud_calls, baseline.cleanup_calls) == (1, 1)
    assert (current.cloud_calls, current.cleanup_calls) == (1, 0)
    assert "transcribing" in current.stages
    assert current.stages[-1] == "cleaning"


async def test_unclean_tail_still_cleans_after_local_race():
    current = await measure(legacy=False, local=True, text="Hello world", scale=.001)
    assert current.cleanup_calls == 1
    assert current.cloud_calls == 1
