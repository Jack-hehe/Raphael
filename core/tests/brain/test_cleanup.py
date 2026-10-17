"""Tests for the dictation cleanup service.

No test touches the network: the OpenRouter client is a FakeClient that answers as told, and the
stores are real ones on an in-memory database.
"""

import asyncio
import inspect
import logging
import sqlite3
from datetime import UTC, datetime
from importlib import resources

import pytest

from raphael_core.brain import cleanup
from raphael_core.brain.cleanup import (
    CLEANUP_SCHEMA,
    CleanupResult,
    CleanupService,
    build_system_prompt,
    timeout_for,
)
from raphael_core.brain.dictionary import DictionaryStore
from raphael_core.brain.openrouter import (
    CLEANUP_FALLBACKS,
    Completion,
    OpenRouterClient,
    OpenRouterError,
)
from raphael_core.brain.settings import DEFAULTS, SettingsStore
from raphael_core.brain.spend import SpendTracker

NOW_MS = int(datetime(2026, 10, 17, 12, tzinfo=UTC).timestamp() * 1000)


def completion(data, cost=0.0002, model="google/gemini-3.1-flash-lite"):
    """A Completion carrying `data`, as the client returns it."""
    return Completion(
        data=data,
        model=model,
        cost_usd=cost,
        prompt_tokens=120,
        completion_tokens=30,
        latency_ms=250,
    )


class FakeClient:
    """Stands in for OpenRouterClient: waits `delay` seconds, then fails with `error` or answers
    with `reply`. Every call's keyword arguments are kept in `calls`."""

    def __init__(self):
        self.reply = None
        self.error = None
        self.delay = 0.0
        self.calls = []

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.reply


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def settings(conn):
    return SettingsStore(conn)


@pytest.fixture
def dictionary(conn):
    return DictionaryStore(conn)


@pytest.fixture
def spend(conn):
    return SpendTracker(conn, now_ms=lambda: NOW_MS, tz=UTC)


@pytest.fixture
def make_svc(fake_client, settings, dictionary, spend):
    """`make_svc(has_key=True, **overrides)` is a service on the fakes and stores above.

    `has_key` is whether there is a key, or the function that says. `overrides` are keyword
    arguments of CleanupService (timeout_fn, on_result).
    """

    def make(*, has_key=True, **overrides):
        return CleanupService(
            fake_client,
            settings,
            dictionary,
            spend,
            has_key=has_key if callable(has_key) else (lambda: has_key),
            **overrides,
        )

    return make


@pytest.fixture
def svc(make_svc):
    return make_svc()


def spend_rows(conn):
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT purpose, model, cost_usd, prompt_tokens, completion_tokens FROM spend"
        )
    ]


def dictionary_lines(prompt):
    """The lines under "User's dictionary:", which is where the prompt ends."""
    return prompt.split("User's dictionary:\n", 1)[1].splitlines()


def cleanup_log(caplog):
    """What the service has logged, as text. Fails if anything was logged with a traceback."""
    records = [r for r in caplog.records if r.name == cleanup.logger.name]
    assert all(r.exc_info is None for r in records), "a traceback was logged"
    return "\n".join(r.getMessage() for r in records)


# --- the tests of the plan ----------------------------------------------------------------------


def test_timeout_scales_with_length():
    assert timeout_for(0) == 3.0 and timeout_for(60) == 6.0 and timeout_for(600) == 15.0


async def test_cleans_and_records_spend(svc, fake_client, spend):
    fake_client.reply = completion({"text": "後天下午三點開會。"}, cost=0.0003)
    r = await svc.clean("明天 不對 後天下午三點開會", audio_seconds=3)
    assert (r.text, r.cleaned, r.fallback_reason) == ("後天下午三點開會。", True, None)
    assert spend.month_total() == pytest.approx(0.0003)


async def test_no_key_and_budget_skip_the_llm(make_svc, fake_client, spend):
    assert (
        await make_svc(has_key=False).clean("你好", audio_seconds=1)
    ).fallback_reason == "no_key"
    spend.record(purpose="cleanup", model="m", cost_usd=10.0, prompt_tokens=1, completion_tokens=1)
    assert (await make_svc().clean("你好", audio_seconds=1)).fallback_reason == "budget"
    assert fake_client.calls == []


async def test_timeout_falls_back_to_raw(make_svc, fake_client):
    fake_client.delay = 1.0
    r = await make_svc(timeout_fn=lambda s: 0.05).clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好", False, "timeout")


async def test_api_error_falls_back(svc, fake_client):
    fake_client.error = OpenRouterError(402, "credits", "no credits")
    assert (await svc.clean("你好", audio_seconds=1)).fallback_reason == "error:credits"


async def test_answer_instead_of_cleanup_is_rejected(svc, fake_client):
    fake_client.reply = completion(
        {"text": "當然可以！以下是規劃步驟：\n1. 先確定目標\n2. 列出需求\n3. 排時程\n4. 分配工作"},
        cost=0.0001,
    )
    r = await svc.clean("可以幫我規劃一下嗎", audio_seconds=2)
    assert r.text == "可以幫我規劃一下嗎" and r.fallback_reason == "suspicious"


async def test_prompt_carries_dictionary_and_transcript(svc, fake_client, dictionary):
    dictionary.add("拉斐爾", ["拉菲爾"])
    fake_client.reply = completion({"text": "x"}, cost=0)
    await svc.clean("拉菲爾你好", audio_seconds=1)
    call = fake_client.calls[0]
    assert "- 拉斐爾（常被聽成：拉菲爾）" in call["system"]
    assert call["user"] == "<transcript>\n拉菲爾你好\n</transcript>"
    assert call["schema"] == CLEANUP_SCHEMA


# --- when the model is not asked ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["", " ", "\n\t  ", chr(0x3000)], ids=["empty", "space", "whitespace", "ideographic"]
)
async def test_blank_text_comes_back_as_it_is(make_svc, fake_client, raw):
    # Even without a key: blank text is the first thing checked.
    r = await make_svc(has_key=False).clean(raw, audio_seconds=1)
    assert r == CleanupResult(
        text=raw, cleaned=False, fallback_reason="empty", model=None, cost_usd=0.0, llm_ms=0
    )
    assert fake_client.calls == []


async def test_no_key_is_reported_before_the_budget(make_svc, spend):
    spend.record(purpose="cleanup", model="m", cost_usd=10.0, prompt_tokens=1, completion_tokens=1)
    r = await make_svc(has_key=False).clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好", False, "no_key")


async def test_the_budget_is_the_monthly_limit_and_being_at_it_is_being_over(
    make_svc, fake_client, settings, spend
):
    settings.set("monthly_limit_usd", 1.0)
    fake_client.reply = completion({"text": "你好。"}, cost=0)
    spend.record(purpose="cleanup", model="m", cost_usd=0.5, prompt_tokens=1, completion_tokens=1)
    assert (await make_svc().clean("你好", audio_seconds=1)).fallback_reason is None
    spend.record(purpose="cleanup", model="m", cost_usd=0.5, prompt_tokens=1, completion_tokens=1)
    r = await make_svc().clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好", False, "budget")
    assert len(fake_client.calls) == 1


async def test_the_fallbacks_without_a_call_cost_nothing_and_took_no_time(make_svc, spend):
    spend.record(purpose="cleanup", model="m", cost_usd=10.0, prompt_tokens=1, completion_tokens=1)
    r = await make_svc().clean("你好", audio_seconds=1)
    assert (r.model, r.cost_usd, r.llm_ms) == (None, 0.0, 0)


# --- the request --------------------------------------------------------------------------------


async def test_the_request_matches_what_the_client_takes(make_svc, fake_client):
    fake_client.reply = completion({"text": "你好。"})
    await make_svc(timeout_fn=lambda seconds: seconds / 2).clean("你好", audio_seconds=8)
    (call,) = fake_client.calls
    # The keyword arguments must bind to the real client's method, or the real call would fail.
    inspect.signature(OpenRouterClient.complete_json).bind(object(), **call)
    assert call["model"] == DEFAULTS["cleanup_model"]
    assert call["fallbacks"] == CLEANUP_FALLBACKS
    assert call["schema_name"] == "cleaned_dictation"
    assert call["timeout_s"] == 4.0  # the timeout function's answer for the audio's length
    assert call["max_tokens"] == 256


async def test_the_model_comes_from_the_settings(svc, fake_client, settings):
    fake_client.reply = completion({"text": "你好。"})
    settings.set("cleanup_model", "openai/gpt-6-luna")
    await svc.clean("你好", audio_seconds=1)
    assert fake_client.calls[0]["model"] == "openai/gpt-6-luna"


async def test_the_timeout_is_timeout_for_unless_told_otherwise(svc, fake_client):
    fake_client.reply = completion({"text": "你好。"})
    await svc.clean("你好", audio_seconds=60)
    assert fake_client.calls[0]["timeout_s"] == timeout_for(60) == 6.0


@pytest.mark.parametrize(("length", "max_tokens"), [(2, 256), (96, 256), (97, 258), (200, 464)])
async def test_max_tokens_follows_the_length_of_the_text(svc, fake_client, length, max_tokens):
    fake_client.reply = completion({"text": "好"})
    await svc.clean("字" * length, audio_seconds=1)
    assert fake_client.calls[0]["max_tokens"] == max_tokens


# --- the dictionary in the prompt ---------------------------------------------------------------


async def test_the_whole_dictionary_reaches_the_prompt(svc, fake_client, dictionary):
    dictionary.add("OpenRouter")
    dictionary.add("拉斐爾", ["拉菲爾", "拉非爾"])
    fake_client.reply = completion({"text": "你好。"})
    await svc.clean("你好", audio_seconds=1)
    # The newest word first, as the dictionary lists them.
    assert dictionary_lines(fake_client.calls[0]["system"]) == [
        "- 拉斐爾（常被聽成：拉菲爾、拉非爾）",
        "- OpenRouter",
    ]


async def test_an_empty_dictionary_reaches_the_prompt_as_empty(svc, fake_client):
    fake_client.reply = completion({"text": "你好。"})
    await svc.clean("你好", audio_seconds=1)
    assert dictionary_lines(fake_client.calls[0]["system"]) == ["（空）"]


async def test_a_word_added_between_two_dictations_is_in_the_second_prompt(
    svc, fake_client, dictionary
):
    fake_client.reply = completion({"text": "你好。"})
    await svc.clean("你好", audio_seconds=1)
    dictionary.add("拉斐爾", ["拉菲爾"])
    await svc.clean("你好", audio_seconds=1)
    first, second = (dictionary_lines(call["system"]) for call in fake_client.calls)
    assert first == ["（空）"]
    assert second == ["- 拉斐爾（常被聽成：拉菲爾）"]


# --- the reply ----------------------------------------------------------------------------------


async def test_the_result_of_a_cleanup(svc, fake_client, conn):
    fake_client.reply = completion(
        {"text": "你好。"}, cost=0.0007, model="anthropic/claude-haiku-5.5"
    )
    fake_client.delay = 0.1
    r = await svc.clean("你好", audio_seconds=1)
    assert r.model == "anthropic/claude-haiku-5.5"  # the fallback model that answered
    assert r.cost_usd == 0.0007
    assert 50 <= r.llm_ms < 1000  # the milliseconds the model took
    assert spend_rows(conn) == [("cleanup", "anthropic/claude-haiku-5.5", 0.0007, 120, 30)]


async def test_the_whitespace_around_the_cleaned_text_is_dropped(svc, fake_client):
    # A line break at the end of a paste could press Enter in the window it goes to.
    fake_client.reply = completion({"text": "\n 你好。 \n"})
    r = await svc.clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned) == ("你好。", True)


@pytest.mark.parametrize(
    "data",
    [
        {"text": ""},
        {"text": " \n"},
        {},
        {"other": "你好"},
        {"text": None},
        {"text": 5},
        {"text": ["你好"]},
    ],
    ids=["empty", "blank", "no-keys", "wrong-key", "null", "number", "list"],
)
async def test_a_reply_without_text_is_suspicious_but_was_paid_for(svc, fake_client, conn, data):
    fake_client.reply = completion(data, cost=0.0004)
    r = await svc.clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好", False, "suspicious")
    assert (r.model, r.cost_usd) == ("google/gemini-3.1-flash-lite", 0.0004)
    assert spend_rows(conn) == [("cleanup", "google/gemini-3.1-flash-lite", 0.0004, 120, 30)]


@pytest.mark.parametrize(("length", "longest_ok"), [(9, 33), (10, 35), (100, 170)])
async def test_a_reply_may_be_one_and_a_half_times_the_text_plus_twenty_characters(
    svc, fake_client, conn, length, longest_ok
):
    # The limit is 1.5 * length + 20 characters: 33.5, 35 and 170 for these three lengths.
    raw = "字" * length
    fake_client.reply = completion({"text": "好" * longest_ok})
    r = await svc.clean(raw, audio_seconds=1)
    assert (r.cleaned, r.fallback_reason) == (True, None)
    fake_client.reply = completion({"text": "好" * (longest_ok + 1)})
    r = await svc.clean(raw, audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == (raw, False, "suspicious")
    assert len(spend_rows(conn)) == 2  # the rejected reply cost money too


async def test_a_long_cleanup_that_grows_by_a_third_is_not_taken_for_an_answer(svc, fake_client):
    # Punctuation, line breaks and the numbers of a list make a cleanup longer than what was said.
    raw = (
        "週末的待辦事項 打掃客廳和廚房 把換季的衣服收進衣櫃 去郵局寄包裹給姊姊 預約下個月的健康檢查"
        " 整理這個月的發票 幫陽台的植物澆水施肥 修好書房的檯燈 洗車並且加滿油 買下週要用的食材"
        " 回覆朋友的生日邀請"
    )
    cleaned = (
        "週末的待辦事項：\n1. 打掃客廳和廚房\n2. 把換季的衣服收進衣櫃\n3. 去郵局寄包裹給姊姊\n"
        "4. 預約下個月的健康檢查\n5. 整理這個月的發票\n6. 幫陽台的植物澆水施肥\n7. 修好書房的檯燈\n"
        "8. 洗車並且加滿油\n9. 買下週要用的食材\n10. 回覆朋友的生日邀請"
    )
    assert len(raw) >= 100 and 1.25 <= len(cleaned) / len(raw) <= 1.35  # 102 -> 134 characters
    fake_client.reply = completion({"text": cleaned})
    r = await svc.clean(raw, audio_seconds=25)
    assert (r.text, r.cleaned, r.fallback_reason) == (cleaned, True, None)


# --- the call fails -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "auth",
        "credits",
        "rate_limit",
        "bad_request",
        "server",
        "timeout",
        "network",
        "bad_response",
    ],
)
async def test_every_kind_of_api_error_names_the_fallback(svc, fake_client, conn, kind):
    # Also the client's own timeout: it is "error:timeout", while "timeout" is the deadline of the
    # service itself (test_timeout_falls_back_to_raw).
    fake_client.error = OpenRouterError(0, kind, "x")
    r = await svc.clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好", False, f"error:{kind}")
    assert (r.model, r.cost_usd) == (None, 0.0)
    assert spend_rows(conn) == []


async def test_a_timeout_counts_the_time_waited(make_svc, fake_client, conn):
    fake_client.delay = 1.0
    r = await make_svc(timeout_fn=lambda s: 0.05).clean("你好", audio_seconds=1)
    assert 0 < r.llm_ms < 900  # it waited for the timeout, not for the 1 s of the model
    assert spend_rows(conn) == []


@pytest.mark.parametrize(
    "error", [RuntimeError("boom"), KeyError("x"), ValueError("v"), OSError("disk"), TypeError("t")]
)
async def test_any_other_error_of_the_client_falls_back_too(svc, fake_client, error):
    fake_client.error = error
    r = await svc.clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好", False, "error:unexpected")


async def test_an_unexpected_error_of_the_client_counts_the_time_waited(svc, fake_client):
    fake_client.delay = 0.1
    fake_client.error = RuntimeError("boom")
    r = await svc.clean("你好", audio_seconds=1)
    assert r.fallback_reason == "error:unexpected"
    assert 50 <= r.llm_ms < 1000


async def test_cancelling_the_cleanup_is_not_turned_into_a_fallback(svc, fake_client):
    fake_client.delay = 10.0
    task = asyncio.create_task(svc.clean("你好", audio_seconds=1))
    await asyncio.sleep(0.01)
    assert len(fake_client.calls) == 1  # the call is in flight
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_cost_is_recorded_even_when_the_text_is_not_usable(svc, fake_client, spend):
    # The money is gone either way, so the limit has to know.
    fake_client.reply = completion({}, cost=0.25)
    await svc.clean("你好", audio_seconds=1)
    assert spend.month_total() == pytest.approx(0.25)


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -0.5], ids=["nan", "inf", "negative"])
async def test_a_cost_the_tracker_refuses_does_not_lose_the_cleaned_text(
    svc, fake_client, spend, caplog, cost
):
    fake_client.reply = completion({"text": "你好。"}, cost=cost)
    with caplog.at_level(logging.WARNING, logger=cleanup.logger.name):
        r = await svc.clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好。", True, None)
    assert r.cost_usd == 0.0  # a number the history can store
    assert spend.month_total() == 0.0
    assert "ValueError" in cleanup_log(caplog)


async def test_a_spend_record_that_fails_does_not_lose_the_cleaned_text(
    svc, fake_client, spend, monkeypatch, caplog
):
    def locked(**kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(spend, "record", locked)
    fake_client.reply = completion({"text": "你好。"}, cost=0.0003)
    with caplog.at_level(logging.WARNING, logger=cleanup.logger.name):
        r = await svc.clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好。", True, None)
    assert r.cost_usd == pytest.approx(0.0003)  # it did cost that
    assert "OperationalError" in cleanup_log(caplog)


def sabotage_store(request, monkeypatch, fixture, method):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(request.getfixturevalue(fixture), method, broken)


@pytest.mark.parametrize(
    ("fixture", "method"),
    [
        ("spend", "is_over"),
        ("settings", "get"),
        ("dictionary", "hint_words"),
        ("dictionary", "replacements"),
    ],
)
async def test_a_store_that_fails_falls_back_to_raw(
    request, monkeypatch, make_svc, fake_client, fixture, method
):
    sabotage_store(request, monkeypatch, fixture, method)
    heard = []
    r = await make_svc(on_result=heard.append).clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好", False, "error:unexpected")
    assert fake_client.calls == [] and heard == []


@pytest.mark.parametrize("which", ["has_key", "timeout_fn"])
async def test_a_function_given_to_the_service_that_fails_falls_back_to_raw(
    make_svc, fake_client, which
):
    def broken(*args):
        raise RuntimeError("broken")

    r = await make_svc(**{which: broken}).clean("你好", audio_seconds=1)
    assert (r.text, r.cleaned, r.fallback_reason) == ("你好", False, "error:unexpected")
    assert fake_client.calls == []


# --- on_result ----------------------------------------------------------------------------------


async def test_on_result_hears_none_when_the_model_answers(make_svc, fake_client):
    heard = []
    fake_client.reply = completion({"text": "你好。"})
    await make_svc(on_result=heard.append).clean("你好", audio_seconds=1)
    assert heard == [None]


async def test_on_result_hears_none_for_a_reply_that_is_rejected_too(make_svc, fake_client):
    heard = []
    fake_client.reply = completion({"text": ""})
    r = await make_svc(on_result=heard.append).clean("你好", audio_seconds=1)
    assert r.fallback_reason == "suspicious" and heard == [None]  # OpenRouter did answer


async def test_on_result_hears_the_error_of_the_api(make_svc, fake_client):
    heard = []
    fake_client.error = OpenRouterError(429, "rate_limit", "slow down")
    await make_svc(on_result=heard.append).clean("你好", audio_seconds=1)
    assert len(heard) == 1 and heard[0] is fake_client.error


async def test_on_result_hears_a_timeout_as_an_error(make_svc, fake_client):
    heard = []
    fake_client.delay = 1.0
    await make_svc(timeout_fn=lambda s: 0.05, on_result=heard.append).clean("你好", audio_seconds=1)
    (error,) = heard
    assert isinstance(error, OpenRouterError)
    assert (error.status, error.kind) == (0, "timeout")


async def test_on_result_hears_nothing_when_the_model_was_not_asked(make_svc, spend):
    heard = []
    await make_svc(on_result=heard.append).clean("  ", audio_seconds=1)
    await make_svc(has_key=False, on_result=heard.append).clean("你好", audio_seconds=1)
    spend.record(purpose="cleanup", model="m", cost_usd=10.0, prompt_tokens=1, completion_tokens=1)
    await make_svc(on_result=heard.append).clean("你好", audio_seconds=1)
    assert heard == []


async def test_on_result_hears_nothing_of_an_unexpected_error(make_svc, fake_client):
    heard = []
    fake_client.error = RuntimeError("boom")
    await make_svc(on_result=heard.append).clean("你好", audio_seconds=1)
    assert heard == []


async def test_an_on_result_that_fails_changes_nothing(make_svc, fake_client, caplog):
    def broken(error):
        raise RuntimeError("the status page is broken")

    svc = make_svc(on_result=broken, timeout_fn=lambda s: 0.05)
    with caplog.at_level(logging.WARNING, logger=cleanup.logger.name):
        fake_client.reply = completion({"text": "你好。"})
        r = await svc.clean("你好", audio_seconds=1)
        assert (r.text, r.cleaned, r.fallback_reason) == ("你好。", True, None)
        fake_client.reply = completion({"text": ""})
        assert (await svc.clean("你好", audio_seconds=1)).fallback_reason == "suspicious"
        fake_client.error = OpenRouterError(402, "credits", "x")
        assert (await svc.clean("你好", audio_seconds=1)).fallback_reason == "error:credits"
        fake_client.error, fake_client.delay = None, 1.0
        assert (await svc.clean("你好", audio_seconds=1)).fallback_reason == "timeout"
    assert "RuntimeError" in cleanup_log(caplog)


# --- what is logged -----------------------------------------------------------------------------


async def test_an_api_error_is_logged_by_kind_and_status_only(svc, fake_client, caplog):
    error = OpenRouterError(401, "auth", "Could not read the OpenRouter key")
    error.__cause__ = RuntimeError("the key store said sk-or-v1-LEAKED")
    fake_client.error = error
    with caplog.at_level(logging.DEBUG, logger=cleanup.logger.name):
        await svc.clean("你好", audio_seconds=1)
    log = cleanup_log(caplog)
    assert "kind=auth" in log and "status=401" in log
    assert "LEAKED" not in log and "key store" not in log and "Could not read" not in log


async def test_an_unexpected_error_is_logged_by_type_only(svc, fake_client, caplog):
    fake_client.error = RuntimeError("boom sk-or-v1-LEAKED")
    with caplog.at_level(logging.DEBUG, logger=cleanup.logger.name):
        await svc.clean("你好", audio_seconds=1)
    log = cleanup_log(caplog)
    assert "RuntimeError" in log
    assert "boom" not in log and "LEAKED" not in log


async def test_the_dictated_text_and_the_reply_are_never_logged(make_svc, fake_client, caplog):
    raw, reply = "機密內容 hunter2", "回答內容 swordfish"
    with caplog.at_level(logging.DEBUG, logger=cleanup.logger.name):
        fake_client.reply = completion({"text": reply * 20})  # too long: suspicious
        await make_svc().clean(raw, audio_seconds=1)
        fake_client.reply = completion({"text": reply}, cost=float("nan"))  # refused cost
        await make_svc().clean(raw, audio_seconds=1)
        fake_client.error = OpenRouterError(500, "server", raw)
        await make_svc().clean(raw, audio_seconds=1)
        fake_client.error = RuntimeError(raw)
        await make_svc().clean(raw, audio_seconds=1)
        fake_client.error, fake_client.delay = None, 1.0
        await make_svc(timeout_fn=lambda s: 0.05).clean(raw, audio_seconds=1)
    log = cleanup_log(caplog)
    assert log  # the failures were logged
    for secret in ("機密", "hunter2", "回答", "swordfish"):
        assert secret not in log


# --- the prompt ---------------------------------------------------------------------------------


def test_the_prompt_is_package_data_with_one_placeholder_at_its_end():
    file = resources.files("raphael_core.brain.prompts").joinpath("cleanup_system.md")
    text = file.read_text(encoding="utf-8")
    assert text.startswith("You clean up dictated text.")
    assert text.count("{dictionary}") == 1 and text.rstrip().endswith("\n{dictionary}")


def test_an_empty_dictionary_is_written_as_empty():
    prompt = build_system_prompt([], [])
    assert dictionary_lines(prompt) == ["（空）"]
    assert "{dictionary}" not in prompt


def test_each_word_has_a_line_in_the_order_given():
    prompt = build_system_prompt(
        ["OpenRouter", "拉斐爾", "ラファエル"],
        [("拉菲爾", "拉斐爾"), ("らふぁえる", "ラファエル"), ("拉非爾", "拉斐爾")],
    )
    assert dictionary_lines(prompt) == [
        "- OpenRouter",
        "- 拉斐爾（常被聽成：拉菲爾、拉非爾）",
        "- ラファエル（常被聽成：らふぁえる）",
    ]


def test_the_forms_of_a_word_keep_the_order_of_the_replacements():
    prompt = build_system_prompt(["Raphael"], [("Rafael X", "Raphael"), ("Rafael", "Raphael")])
    assert dictionary_lines(prompt) == ["- Raphael（常被聽成：Rafael X、Rafael）"]


def test_a_replacement_for_a_word_that_is_not_listed_is_ignored():
    prompt = build_system_prompt(["拉斐爾"], [("菲爾", "菲爾德")])
    assert dictionary_lines(prompt) == ["- 拉斐爾"]


@pytest.mark.parametrize("word", ["{dictionary}", "{0}", "{", "}{", "{text}"])
def test_braces_in_the_dictionary_are_text(word):
    prompt = build_system_prompt([word], [("x{y}", word)])
    assert dictionary_lines(prompt) == [f"- {word}（常被聽成：x{{y}}）"]
    # The braces of the prompt itself are as they were.
    assert prompt.count('{"text": "<the cleaned text>"}') == 1


def test_a_line_break_in_a_word_does_not_start_a_new_line():
    prompt = build_system_prompt(["Open\nRouter", "  ", ""], [("open\r\nrouter", "Open\nRouter")])
    assert dictionary_lines(prompt) == ["- Open Router（常被聽成：open router）"]


def test_a_dictionary_of_blank_words_is_empty():
    assert dictionary_lines(build_system_prompt(["", "  "], [])) == ["（空）"]
