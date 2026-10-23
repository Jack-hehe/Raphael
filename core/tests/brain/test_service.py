"""Tests for the brain's RPC service.

No test touches the network: the OpenRouter client is a FakeClient that answers as told (the last
section uses the real client on an httpx.MockTransport), the key store is a MemoryKeyStore (or a
broken one), and the stores are real ones on an in-memory database. Every call goes through a
Router, the way the device side makes it.
"""

import asyncio
import json
import logging
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest

from raphael_core.brain import service
from raphael_core.brain.dictionary import DictionaryStore
from raphael_core.brain.history import HistoryStore
from raphael_core.brain.openrouter import Completion, KeyInfo, OpenRouterClient, OpenRouterError
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.settings import DEFAULTS, SettingsStore
from raphael_core.protocol import INTERNAL_ERROR, INVALID_PARAMS, METHOD_NOT_FOUND, RpcError
from raphael_core.router import Router

from .conftest import item

NOW_MS = int(datetime(2026, 10, 17, 12, tzinfo=UTC).timestamp() * 1000)
DAY_MS = 86_400_000
KEY = "sk-or-v1-test"
SECRET = "sk-or-v1-TOPSECRET"

METHODS = [
    "brain.finalize_dictation",
    "brain.normalize_transcript",
    "brain.cancel_dictation",
    "stt.transcribe",
    "history.list",
    "history.latest",
    "history.delete",
    "history.clear",
    "dictionary.list",
    "dictionary.add",
    "dictionary.import",
    "dictionary.delete",
    "dictionary.learn",
    "settings.get",
    "settings.set",
    "languages.list",
    "secrets.status",
    "secrets.set_openrouter_key",
    "secrets.clear_openrouter_key",
    "status.get",
    "status.check_openrouter",
    "models.catalog",
    "stats.summary",
    "usage.analytics",
]


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
    """Stands in for OpenRouterClient.

    `complete_json` raises `next_error` if there is one, else returns `next_completion`.
    `key_info` waits `key_info_delay` seconds, then raises `key_info_error` if there is one, else
    returns `key_info_result`. The calls are kept: the keyword arguments of each `complete_json`
    in `calls` and the `key` argument of each `key_info` in `key_info_calls`.
    """

    def __init__(self):
        self.next_completion = None
        self.next_error = None
        self.key_info_result = KeyInfo(
            label="test key",
            limit=20.0,
            limit_remaining=17.5,
            limit_reset="monthly",
            usage_daily=0.1,
            usage_monthly=2.5,
        )
        self.key_info_error = None
        self.key_info_delay = 0.0
        self.key_info_hook = None  # called with the key as soon as a check starts
        self.calls = []
        self.key_info_calls = []

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.next_error is not None:
            raise self.next_error
        return self.next_completion

    async def key_info(self, key=None, timeout_s=5.0):
        self.key_info_calls.append(key)
        if self.key_info_hook is not None:
            self.key_info_hook(key)
        if self.key_info_delay:
            await asyncio.sleep(self.key_info_delay)
        if self.key_info_error is not None:
            raise self.key_info_error
        return self.key_info_result


class BrokenKeyStore:
    """A key store whose vault fails, with an error message that holds the key."""

    def get(self):
        raise RuntimeError(f"vault locked; the key is {SECRET}")

    def set(self, key):
        raise RuntimeError(f"vault locked; the key is {SECRET}")

    def clear(self):
        raise RuntimeError(f"vault locked; the key is {SECRET}")


class Clock:
    """The brain's clock, which a test can move."""

    def __init__(self, now_ms=NOW_MS):
        self.now_ms = now_ms

    def __call__(self):
        return self.now_ms


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def keys():
    return MemoryKeyStore()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def make_brain(conn, fake_client, keys, clock):
    """`make_brain(**overrides)` is a Brain on the fakes and the database above."""
    brains = []

    def make(**overrides):
        brain = Brain(conn, fake_client, keys, now_ms=clock, **overrides)
        brains.append(brain)
        return brain

    yield make
    for brain in brains:
        await brain.aclose()


def routed(brain):
    router = Router()
    brain.register(router)
    return router


@pytest.fixture
def router(make_brain):
    return routed(make_brain())


async def finalize(router, raw_text="你好", **overrides):
    """brain.finalize_dictation with plausible values for what the test does not care about."""
    params = {
        "raw_text": raw_text,
        "audio_seconds": 1.0,
        "stt_ms": 10,
        "target_app": None,
        **overrides,
    }
    return await router.call("brain.finalize_dictation", params)


def seed_history(conn, count, start_ms=1_000):
    """`count` history rows, one millisecond apart: "第0句" (oldest) and so on."""
    store = HistoryStore(conn)
    for i in range(count):
        store.add(item(created_at_ms=start_ms + i, text=f"第{i}句", raw_text=f"原{i}"))


def dismiss(conn, word):
    """Dismiss `word` the one way the user can: by deleting it after it was learned."""
    store = DictionaryStore(conn)
    store.delete(store.add(word, source="learned").id)


def assert_no_traceback_logged(caplog):
    assert all(record.exc_info is None for record in caplog.records), "a traceback was logged"


# --- the basics: a dictation, the key, the status and a setting -----------------------------------


async def test_finalize_cleans_normalizes_and_saves(router, fake_client, keys):
    keys.set("sk-or-v1-test")
    fake_client.next_completion = completion({"text": "明天下午三點開會。"}, cost=0.0002)
    r = await router.call(
        "brain.finalize_dictation",
        {
            "raw_text": "嗯 明天下午三点开会",
            "audio_seconds": 2.0,
            "stt_ms": 300,
            "target_app": "Code.exe",
        },
    )
    assert r["cleaned"] is True and r["text"] == "明天下午三點開會。"
    assert r["raw_text"] == "嗯 明天下午三點開會"
    items = (await router.call("history.list", {}))["items"]
    assert len(items) == 1 and items[0]["target_app"] == "Code.exe"


async def test_finalize_empty_writes_nothing(router):
    r = await router.call(
        "brain.finalize_dictation",
        {"raw_text": "  ", "audio_seconds": 0.2, "stt_ms": 10, "target_app": None},
    )
    assert r["id"] is None and r["fallback_reason"] == "empty"
    assert (await router.call("history.list", {}))["items"] == []


async def test_finalize_without_key_pastes_raw(router):
    r = await router.call(
        "brain.finalize_dictation",
        {"raw_text": "你好", "audio_seconds": 1.0, "stt_ms": 10, "target_app": None},
    )
    assert r["cleaned"] is False and r["fallback_reason"] == "no_key" and r["text"] == "你好"


async def test_invalid_key_is_not_stored(router, fake_client, keys):
    fake_client.key_info_error = OpenRouterError(401, "auth", "bad key")
    r = await router.call("secrets.set_openrouter_key", {"key": "sk-or-v1-bad"})
    assert r == {"valid": False, "reason": "auth"} and keys.get() is None


async def test_status_reports_error_after_network_failure(router, fake_client, keys):
    keys.set("sk-or-v1-test")
    fake_client.next_error = OpenRouterError(0, "network", "offline")
    await router.call(
        "brain.finalize_dictation",
        {"raw_text": "測試", "audio_seconds": 1.0, "stt_ms": 10, "target_app": None},
    )
    status = await router.call("status.get", {})
    assert status["openrouter"]["state"] == "error" and status["openrouter"]["error"] == "network"


async def test_settings_set_rejects_bad_value(router):
    with pytest.raises(RpcError) as e:
        await router.call("settings.set", {"key": "history_retention_days", "value": "x"})
    assert e.value.code == INVALID_PARAMS


# --- the contract -------------------------------------------------------------------------------


def test_registers_every_method_of_the_contract(router):
    assert router.methods() == sorted(METHODS)


# --- brain.finalize_dictation -------------------------------------------------------------------


async def test_finalize_result_and_history_row(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "明天下午三點開會。"}, cost=0.0002)
    r = await finalize(
        router, "嗯 明天下午三点开会", audio_seconds=2.0, stt_ms=300, target_app="Code.exe"
    )
    assert set(r) == {"id", "text", "raw_text", "cleaned", "fallback_reason", "llm_ms"}
    assert isinstance(r["id"], int) and isinstance(r["llm_ms"], int)
    assert r["fallback_reason"] is None
    assert (await router.call("history.latest", {}))["item"] == {
        "id": r["id"],
        "created_at_ms": NOW_MS,
        "kind": "dictation",
        "raw_text": "嗯 明天下午三點開會",
        "text": "明天下午三點開會。",
        "cleaned": True,
        "fallback_reason": None,
        "target_app": "Code.exe",
        "audio_seconds": 2.0,
        "stt_ms": 300,
        "llm_ms": r["llm_ms"],
        "model": "google/gemini-3.1-flash-lite",
        "cost_usd": 0.0002,
        "stt": None,
    }


async def test_finalize_keeps_which_speech_to_text_heard_it(router):
    await finalize(router, "你好", stt="openai/gpt-transcribe+qwen3-asr")
    row = (await router.call("history.latest", {}))["item"]
    assert row["stt"] == "openai/gpt-transcribe+qwen3-asr"


@pytest.mark.parametrize("raw_text", ["", "  ", "\n \t\n"])
async def test_finalize_blank_text_is_empty_and_never_asks_the_model(
    router, fake_client, keys, raw_text
):
    keys.set(KEY)
    assert await finalize(router, raw_text) == {
        "id": None,
        "text": "",
        "raw_text": "",
        "cleaned": False,
        "fallback_reason": "empty",
        "llm_ms": 0,
    }
    assert fake_client.calls == []
    assert (await router.call("history.list", {}))["items"] == []


async def test_finalize_without_key_saves_the_raw_text(router, fake_client):
    r = await finalize(router, "你好", target_app="notepad.exe")
    assert fake_client.calls == []
    row = (await router.call("history.latest", {}))["item"]
    assert (row["id"], row["text"], row["raw_text"]) == (r["id"], "你好", "你好")
    assert (row["cleaned"], row["fallback_reason"], row["model"], row["cost_usd"]) == (
        False,
        "no_key",
        None,
        0.0,
    )


async def test_finalize_gives_the_model_the_text_as_heard_with_the_replacements(
    router, fake_client, keys
):
    # The model reads the speech-to-text result without OpenCC's conversion, so that it is not
    # fed conversion errors; the dictionary's replacements are applied all the same. The raw text
    # that is saved and pasted on a fallback is converted as before.
    keys.set(KEY)
    await router.call("dictionary.add", {"word": "拉斐爾", "misheard": ["拉菲爾"]})
    fake_client.next_completion = completion({"text": "拉斐爾你好，這是測試。"})
    r = await finalize(router, "拉菲爾你好，这是测试")
    assert r["raw_text"] == "拉斐爾你好，這是測試"
    assert fake_client.calls[0]["user"] == "<transcript>\n拉斐爾你好，这是测试\n</transcript>"


async def test_finalize_does_not_feed_the_model_opencc_errors(router, fake_client, keys):
    # s2twp turns 就是只要 into 就是隻要: that is what the raw text says, not what the model reads,
    # and the model's Traditional Chinese is not run through s2twp again.
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "只要按 Alt 就可以觸發。"})
    r = await finalize(router, "就是只要按O S就可以触发")
    assert r["raw_text"] == "就是隻要按O S就可以觸發"
    assert fake_client.calls[0]["user"] == "<transcript>\n就是只要按O S就可以触发\n</transcript>"
    assert r["text"] == "只要按 Alt 就可以觸發。"
    assert (await router.call("history.latest", {}))["item"]["text"] == "只要按 Alt 就可以觸發。"


async def test_finalize_passes_the_target_app_to_the_prompt(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "好。"})
    await finalize(router, "好", target_app="LINE.exe")
    call = fake_client.calls[0]
    assert "Target app: LINE.exe (chat)" in call["system"]
    assert "LINE" not in call["user"]


async def test_finalize_without_a_target_app_says_unknown(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "好。"})
    await finalize(router, "好", target_app=None)
    assert "Target app: unknown" in fake_client.calls[0]["system"]


async def test_a_fallback_after_the_call_pastes_the_converted_raw_text(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": ""})
    r = await finalize(router, "这是测试")
    assert (r["text"], r["fallback_reason"]) == ("這是測試", "suspicious")


@pytest.mark.parametrize(
    "spoken",
    [
        "只要這個系統發佈，周末就不會干擾你。",
        "我們只要有一個干擾，就會停下來。",
    ],
)
async def test_a_fallback_pastes_and_saves_traditional_speech_as_it_is(router, spoken):
    # No key: the text that is pasted is the speech-to-text result. It is Traditional Chinese
    # already, and OpenCC over the whole sentence would write 隻要, 幹擾 and 釋出 into it.
    r = await finalize(router, spoken)
    assert (r["cleaned"], r["fallback_reason"]) == (False, "no_key")
    assert r["text"] == r["raw_text"] == spoken
    row = (await router.call("history.latest", {}))["item"]
    assert (row["text"], row["raw_text"]) == (spoken, spoken)


async def test_a_fallback_drops_the_obvious_stutters_but_the_history_keeps_the_raw_text(router):
    r = await finalize(router, "之後會有 Hermes，Hermes agents 可以做任何，任何事情")
    assert r["fallback_reason"] == "no_key"
    assert r["text"] == "之後會有 Hermes agents 可以做任何事情"
    assert r["raw_text"] == "之後會有 Hermes，Hermes agents 可以做任何，任何事情"
    row = (await router.call("history.latest", {}))["item"]
    assert row["text"] == r["text"] and row["raw_text"] == r["raw_text"]


async def test_every_kind_of_fallback_drops_the_stutters(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "台北。"})  # an answer: suspicious
    r = await finalize(router, "台灣的首都，首都是哪裡")
    assert (r["fallback_reason"], r["text"]) == ("suspicious", "台灣的首都是哪裡")
    fake_client.next_error = OpenRouterError(0, "network", "offline")
    r = await finalize(router, "please update the, the config file")
    assert (r["fallback_reason"], r["text"]) == ("error:network", "please update the config file")


async def test_a_cleaned_text_is_the_models_to_write_and_its_repeats_stay(
    router, fake_client, keys
):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "Hermes, Hermes agent 好用。"})
    r = await finalize(router, "Hermes Hermes agent 好用")
    assert (r["cleaned"], r["text"]) == (True, "Hermes, Hermes agent 好用。")


async def test_a_fallback_converts_only_the_simplified_words_of_mixed_speech(router):
    r = await finalize(router, "就是只要按這個开關，资料我後面再发給你。")
    assert r["text"] == r["raw_text"] == "就是只要按這個開關，資料我後面再發給你。"


async def test_finalize_failure_falls_back_to_the_raw_text(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_error = OpenRouterError(0, "network", "offline")
    r = await finalize(router, "你好")
    assert (r["text"], r["cleaned"], r["fallback_reason"]) == ("你好", False, "error:network")
    assert (await router.call("history.latest", {}))["item"]["fallback_reason"] == "error:network"


async def test_finalize_accepts_any_number_for_the_times_and_no_target_app(router):
    await router.call(
        "brain.finalize_dictation", {"raw_text": "你好", "audio_seconds": 2, "stt_ms": 312.6}
    )
    row = (await router.call("history.latest", {}))["item"]
    assert (row["audio_seconds"], row["stt_ms"], row["target_app"]) == (2.0, 313, None)


# After the model has answered, the text is normalized once more. The model is told to write
# Traditional Chinese, so a Japanese text is trusted to be right; it would be corrupted otherwise:
# OpenCC turns the kanji-only line 学校 into 學校.


async def test_cleaned_chinese_is_converted_to_taiwan_traditional(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "明天下午三点开会。"})
    r = await finalize(router, "明天下午三點開會")
    assert r["cleaned"] is True and r["text"] == "明天下午三點開會。"
    assert (await router.call("history.latest", {}))["item"]["text"] == "明天下午三點開會。"


async def test_a_cleaned_list_keeps_its_line_breaks_in_the_text_and_the_history(
    router, fake_client, keys
):
    # The conversion of leftover Simplified Chinese and the replacements work line by line;
    # the lead-in, the blank line and the items reach the paste and the history as written.
    keys.set(KEY)
    await router.call("dictionary.add", {"word": "拉斐爾", "misheard": ["拉菲爾"]})
    fake_client.next_completion = completion(
        {"text": "有兩件事：\n\n1. 我们开会。\n2. 拉菲爾說好。"}
    )
    r = await finalize(router, "有兩件事 第一個是我们开会 第二個是拉菲爾說好")
    expected = "有兩件事：\n\n1. 我們開會。\n2. 拉斐爾說好。"
    assert r["cleaned"] is True and r["text"] == expected
    assert (await router.call("history.latest", {}))["item"]["text"] == expected


async def test_cleaned_text_with_hiragana_is_not_converted(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "今日は学校です。\n学校"})
    r = await finalize(router, "今日は学校です 学校")
    assert r["cleaned"] is True and r["text"] == "今日は学校です。\n学校"


async def test_replacements_apply_to_the_cleaned_text(router, fake_client, keys):
    keys.set(KEY)
    await router.call("dictionary.add", {"word": "拉斐爾", "misheard": ["拉菲爾"]})
    fake_client.next_completion = completion({"text": "拉菲爾說好。"})
    assert (await finalize(router, "拉斐爾說好"))["text"] == "拉斐爾說好。"


async def test_replacements_apply_to_cleaned_text_that_has_hiragana(router, fake_client, keys):
    keys.set(KEY)
    await router.call("dictionary.add", {"word": "ラファエル", "misheard": ["ラファエロ"]})
    fake_client.next_completion = completion({"text": "ラファエロさんに会います。"})
    r = await finalize(router, "ラファエロさんに会います")
    assert r["raw_text"] == "ラファエルさんに会います"
    assert r["text"] == "ラファエルさんに会います。"


async def test_fallback_text_is_not_normalized_a_second_time(router):
    # The text pasted when there is no cleanup is the normalized raw text as it is. A rule whose
    # word holds its own misheard form would add its word again on a second pass.
    await router.call("dictionary.add", {"word": "Claude Code", "misheard": ["Claude"]})
    r = await finalize(router, "用Claude寫程式")
    assert r["cleaned"] is False
    assert r["text"] == r["raw_text"] == "用Claude Code寫程式"


async def test_finalize_still_answers_when_the_history_cannot_be_written(
    router, fake_client, keys, conn, caplog
):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "你好。"})
    conn.execute("DROP TABLE history")
    caplog.set_level(logging.DEBUG)
    r = await finalize(router, "你好")
    assert (r["id"], r["text"], r["cleaned"]) == (None, "你好。", True)
    assert_no_traceback_logged(caplog)
    assert "你好" not in caplog.text


# --- a dictation that is cancelled while its text is cleaned ------------------------------------
# The request to the model is made, so it is paid for; the text is not kept, or it would be the
# latest dictation, which "paste last" pastes, and it would be counted in the statistics.


def gated(fake_client):
    """Make the fake model wait until the test lets it answer; returns (entered, open_gate)."""
    entered, gate = asyncio.Event(), asyncio.Event()
    answer = fake_client.complete_json

    async def wait_then_answer(**kwargs):
        entered.set()
        await gate.wait()
        return await answer(**kwargs)

    fake_client.complete_json = wait_then_answer
    return entered, gate.set


async def test_a_dictation_cancelled_while_it_is_cleaned_is_paid_for_and_not_kept(
    router, fake_client, keys
):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "早安，你好嗎。"}, cost=0.0004)
    await finalize(router, "早安你好嗎", session_id="s0")  # the dictation "paste last" should find
    fake_client.next_completion = completion({"text": "你好嗎我很好。"}, cost=0.0004)
    entered, open_gate = gated(fake_client)
    pending = asyncio.create_task(finalize(router, "你好嗎我很好", session_id="s1"))
    await entered.wait()
    assert await router.call("brain.cancel_dictation", {"session_id": "s1"}) == {}
    open_gate()
    r = await pending
    assert (r["id"], r["text"], r["cleaned"]) == (None, "你好嗎我很好。", True)
    latest = (await router.call("history.latest", {}))["item"]
    assert (latest["raw_text"], latest["text"]) == ("早安你好嗎", "早安，你好嗎。")
    assert len((await router.call("history.list", {}))["items"]) == 1
    status = await router.call("status.get", {})
    assert status["spend"]["month_usd"] == pytest.approx(0.0008), "both requests were made"
    assert status["last"]["text"] == latest["text"]
    assert (await router.call("stats.summary", {}))["all"]["dictations"] == 1


async def test_the_other_dictations_are_kept_while_one_is_cancelled(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "你好嗎我很好。"})
    entered, open_gate = gated(fake_client)
    first = asyncio.create_task(finalize(router, "你好嗎我很好", session_id="s1"))
    await entered.wait()
    second = asyncio.create_task(finalize(router, "你好嗎我很好", session_id="s2"))
    await asyncio.sleep(0)
    await router.call("brain.cancel_dictation", {"session_id": "s1"})
    open_gate()
    r1, r2 = await first, await second
    assert r1["id"] is None and isinstance(r2["id"], int)
    assert len((await router.call("history.list", {}))["items"]) == 1


@pytest.mark.parametrize("other", ["s2", "S1", " s1"])
async def test_cancelling_another_session_keeps_the_text(router, fake_client, keys, other):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "你好嗎我很好。"})
    entered, open_gate = gated(fake_client)
    pending = asyncio.create_task(finalize(router, "你好嗎我很好", session_id="s1"))
    await entered.wait()
    await router.call("brain.cancel_dictation", {"session_id": other})
    open_gate()
    assert isinstance((await pending)["id"], int)


async def test_a_cancel_that_comes_too_late_or_never_matched_keeps_nothing_back(
    router, fake_client, keys
):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "你好嗎我很好。"})
    done = await finalize(router, "你好嗎我很好", session_id="s1")
    assert isinstance(done["id"], int)
    assert await router.call("brain.cancel_dictation", {"session_id": "s1"}) == {}
    assert await router.call("brain.cancel_dictation", {"session_id": "never-seen"}) == {}
    # The late cancel is forgotten: the same id, used again, is kept.
    again = await finalize(router, "你好嗎我很好", session_id="s1")
    assert isinstance(again["id"], int)
    assert len((await router.call("history.list", {}))["items"]) == 2


async def test_a_dictation_without_a_session_is_kept_as_before(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "你好嗎我很好。"})
    assert isinstance((await finalize(router, "你好嗎我很好"))["id"], int)


async def test_a_cancelled_dictation_logs_how_many_not_what(router, fake_client, keys, caplog):
    caplog.set_level(logging.DEBUG)
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "你好嗎我很好。"})
    entered, open_gate = gated(fake_client)
    pending = asyncio.create_task(finalize(router, "你好嗎我很好", session_id="sess-9f3a"))
    await entered.wait()
    await router.call("brain.cancel_dictation", {"session_id": "sess-9f3a"})
    open_gate()
    await pending
    assert "你好嗎我很好" not in caplog.text and "sess-9f3a" not in caplog.text
    assert any("cancelled" in record.getMessage() for record in caplog.records)


# --- parameters ---------------------------------------------------------------------------------

FIN = "brain.finalize_dictation"
GOOD = {"raw_text": "你好", "audio_seconds": 1.0, "stt_ms": 10, "target_app": None}


def without(params, name):
    return {key: value for key, value in params.items() if key != name}


# Each of these is wrong in exactly one way. The brain must answer INVALID_PARAMS, and must not let
# an AttributeError or an OverflowError reach the router, which would call that INTERNAL_ERROR.
# Some are caught by the brain's own checks; others are what a store refuses (a blank word, an
# unknown setting, a bool for a number: ValueError, KeyError and TypeError).
BAD_PARAMS = [
    (FIN, {}, "finalize: nothing"),
    (FIN, {**GOOD, "raw_text": 5}, "finalize: raw_text number"),
    (FIN, {**GOOD, "raw_text": None}, "finalize: raw_text null"),
    (FIN, without(GOOD, "raw_text"), "finalize: no raw_text"),
    (FIN, {**GOOD, "audio_seconds": "1"}, "finalize: audio_seconds str"),
    (FIN, {**GOOD, "audio_seconds": True}, "finalize: audio_seconds bool"),
    (FIN, {**GOOD, "audio_seconds": -1.0}, "finalize: audio_seconds < 0"),
    (FIN, without(GOOD, "audio_seconds"), "finalize: no audio_seconds"),
    (FIN, {**GOOD, "stt_ms": "10"}, "finalize: stt_ms str"),
    (FIN, {**GOOD, "stt_ms": -1}, "finalize: stt_ms < 0"),
    (FIN, {**GOOD, "stt_ms": 10**30}, "finalize: stt_ms beyond SQLite"),
    (FIN, without(GOOD, "stt_ms"), "finalize: no stt_ms"),
    (FIN, {**GOOD, "target_app": 5}, "finalize: target_app number"),
    (FIN, {**GOOD, "stt": 5}, "finalize: stt number"),
    (FIN, {**GOOD, "session_id": 5}, "finalize: session_id number"),
    ("brain.normalize_transcript", {}, "normalize_transcript: nothing"),
    ("brain.normalize_transcript", {"raw_text": 5}, "normalize_transcript: raw_text number"),
    ("brain.normalize_transcript", {"raw_text": None}, "normalize_transcript: raw_text null"),
    ("brain.cancel_dictation", {}, "cancel_dictation: no session_id"),
    ("brain.cancel_dictation", {"session_id": None}, "cancel_dictation: session_id null"),
    ("brain.cancel_dictation", {"session_id": 5}, "cancel_dictation: session_id number"),
    ("history.list", {"query": 5}, "history.list: query number"),
    ("history.list", {"limit": "5"}, "history.list: limit str"),
    ("history.list", {"limit": 2.5}, "history.list: limit float"),
    ("history.list", {"limit": True}, "history.list: limit bool"),
    ("history.list", {"before_ms": "x"}, "history.list: before_ms str"),
    ("history.list", {"after_ms": "x"}, "history.list: after_ms str"),
    ("history.list", {"after_ms": 1.5}, "history.list: after_ms float"),
    ("history.delete", {}, "history.delete: no id"),
    ("history.delete", {"id": "1"}, "history.delete: id str"),
    ("history.delete", {"id": True}, "history.delete: id bool"),
    ("history.delete", {"id": 10**30}, "history.delete: id beyond SQLite"),
    ("dictionary.add", {}, "dictionary.add: no word"),
    ("dictionary.add", {"word": 5}, "dictionary.add: word number"),
    ("dictionary.add", {"word": None}, "dictionary.add: word null"),
    ("dictionary.add", {"word": "  "}, "dictionary.add: blank word"),
    ("dictionary.add", {"word": "x", "misheard": "y"}, "dictionary.add: misheard str"),
    ("dictionary.add", {"word": "x", "misheard": [1]}, "dictionary.add: misheard [number]"),
    ("dictionary.add", {"word": "x", "misheard": {"a": "b"}}, "dictionary.add: misheard object"),
    ("dictionary.delete", {}, "dictionary.delete: no id"),
    ("dictionary.delete", {"id": "1"}, "dictionary.delete: id str"),
    ("dictionary.learn", {}, "dictionary.learn: no corrections"),
    ("dictionary.learn", {"corrections": "x"}, "dictionary.learn: corrections str"),
    ("dictionary.learn", {"corrections": [{"word": "x"}]}, "dictionary.learn: no misheard"),
    ("dictionary.learn", {"corrections": [{"word": 1, "misheard": "y"}]}, "learn: word number"),
    ("dictionary.learn", {"corrections": ["x"]}, "dictionary.learn: item str"),

    ("settings.set", {}, "settings.set: nothing"),
    ("settings.set", {"key": "languages"}, "settings.set: no value"),
    ("settings.set", {"value": "ja"}, "settings.set: no key"),
    ("settings.set", {"key": 5, "value": 1}, "settings.set: key number"),
    ("settings.set", {"key": "no_such_setting", "value": 1}, "settings.set: unknown key"),
    ("settings.set", {"key": "monthly_limit_usd", "value": True}, "settings.set: bool for float"),
    ("settings.set", {"key": "monthly_limit_usd", "value": None}, "settings.set: null"),
    ("secrets.set_openrouter_key", {}, "set_openrouter_key: no key"),
    ("secrets.set_openrouter_key", {"key": 5}, "set_openrouter_key: key number"),
]


@pytest.mark.parametrize(
    ("method", "params"),
    [pytest.param(method, params, id=name) for method, params, name in BAD_PARAMS],
)
async def test_bad_params_are_invalid_params(router, method, params):
    with pytest.raises(RpcError) as e:
        await router.call(method, params)
    assert e.value.code == INVALID_PARAMS


async def test_a_bad_dictation_writes_nothing_and_asks_nothing(router, fake_client, keys):
    keys.set(KEY)
    with pytest.raises(RpcError):
        await finalize(router, 5)
    assert fake_client.calls == []
    assert (await router.call("history.list", {}))["items"] == []


async def test_optional_params_may_be_null(router, conn):
    seed_history(conn, 2)
    listed = await router.call("history.list", {"query": None, "limit": None, "before_ms": None})
    assert len(listed["items"]) == 2
    added = await router.call("dictionary.add", {"word": "Tailscale", "misheard": None})
    assert added["entry"]["misheard"] == []


# --- history ------------------------------------------------------------------------------------


async def test_history_list_defaults_to_50_and_clamps_to_200(router, conn):
    seed_history(conn, 205)
    assert len((await router.call("history.list", {}))["items"]) == 50
    assert len((await router.call("history.list", {"limit": 7}))["items"]) == 7
    assert len((await router.call("history.list", {"limit": 200}))["items"]) == 200
    assert len((await router.call("history.list", {"limit": 1000}))["items"]) == 200


@pytest.mark.parametrize("limit", [0, -1, -100])
async def test_history_list_never_means_everything(router, conn, limit):
    # SQLite reads LIMIT -1 as "no limit".
    seed_history(conn, 5)
    assert len((await router.call("history.list", {"limit": limit}))["items"]) == 1


async def test_history_list_is_newest_first_and_filters_and_pages(router, conn):
    seed_history(conn, 5)

    async def texts(**params):
        return [entry["text"] for entry in (await router.call("history.list", params))["items"]]

    assert await texts() == [f"第{i}句" for i in (4, 3, 2, 1, 0)]
    assert await texts(query="第3") == ["第3句"]
    assert await texts(query="原1") == ["第1句"]  # the query looks at the raw text too
    assert await texts(before_ms=1_003, limit=2) == ["第2句", "第1句"]
    assert await texts(after_ms=1_003) == ["第4句", "第3句"]
    assert await texts(after_ms=1_001, before_ms=1_004) == ["第3句", "第2句", "第1句"]


async def test_history_latest_and_delete(router, conn):
    assert await router.call("history.latest", {}) == {"item": None}
    seed_history(conn, 3)
    latest = (await router.call("history.latest", {}))["item"]
    assert latest["text"] == "第2句"
    assert await router.call("history.delete", {"id": latest["id"]}) == {"deleted": True}
    assert await router.call("history.delete", {"id": latest["id"]}) == {"deleted": False}
    assert (await router.call("history.latest", {}))["item"]["text"] == "第1句"


async def test_history_clear_deletes_every_item_and_counts_them(router, conn):
    seed_history(conn, 3)
    assert await router.call("history.clear", {}) == {"deleted": 3}
    assert (await router.call("history.list", {}))["items"] == []
    assert await router.call("history.clear", {}) == {"deleted": 0}


# --- dictionary ---------------------------------------------------------------------------------


async def test_dictionary_add_list_delete(router):
    added = await router.call("dictionary.add", {"word": "拉斐爾", "misheard": ["拉菲爾"]})
    entry = added["entry"]
    assert entry == {
        "id": entry["id"],
        "word": "拉斐爾",
        "misheard": ["拉菲爾"],
        "source": "manual",
    }
    other = (await router.call("dictionary.add", {"word": "Tailscale"}))["entry"]
    assert other["misheard"] == [] and other["id"] != entry["id"]
    listed = await router.call("dictionary.list", {})
    assert [e["word"] for e in listed["entries"]] == ["Tailscale", "拉斐爾"]  # newest first
    assert set(listed["entries"][0]) == {"id", "word", "misheard", "source"}
    assert await router.call("dictionary.delete", {"id": entry["id"]}) == {"deleted": True}
    assert await router.call("dictionary.delete", {"id": entry["id"]}) == {"deleted": False}
    assert [e["word"] for e in (await router.call("dictionary.list", {}))["entries"]] == [
        "Tailscale"
    ]


async def test_dictionary_add_merges_the_misheard_forms_of_a_known_word(router):
    await router.call("dictionary.add", {"word": "拉斐爾", "misheard": ["拉菲爾"]})
    merged = await router.call("dictionary.add", {"word": "拉斐爾", "misheard": ["拉非爾"]})
    assert merged["entry"]["misheard"] == ["拉菲爾", "拉非爾"]
    assert len((await router.call("dictionary.list", {}))["entries"]) == 1


async def test_dictionary_learn_adds_the_corrected_words(router):
    learned = await router.call(
        "dictionary.learn",
        {
            "corrections": [
                {"word": "手機", "misheard": "首機"},
                {"word": "Typeless", "misheard": "type less"},
            ]
        },
    )
    assert learned == {"learned": ["手機", "Typeless"]}
    # The words are terms now; the forms are only hints until the pairs are seen again.
    entries = (await router.call("dictionary.list", {}))["entries"]
    assert {(e["word"], tuple(e["misheard"]), e["source"]) for e in entries} == {
        ("手機", (), "learned"),
        ("Typeless", (), "learned"),
    }


async def test_dictionary_learn_reports_only_what_changed(router, conn):
    dismiss(conn, "Typeless")
    await router.call("dictionary.add", {"word": "手機", "misheard": ["首機"]})
    learned = await router.call(
        "dictionary.learn",
        {
            "corrections": [
                {"word": "手機", "misheard": "首機"},  # known already
                {"word": "Typeless", "misheard": "type less"},  # dismissed
                {"word": "手機", "misheard": "手雞"},  # a new form of a known word
            ]
        },
    )
    assert learned == {"learned": ["手機"]}


async def test_dictionary_learn_takes_at_most_three(router):
    corrections = [{"word": f"Word{i}", "misheard": f"ward{i}"} for i in range(5)]
    learned = await router.call("dictionary.learn", {"corrections": corrections})
    assert learned == {"learned": ["Word0", "Word1", "Word2"]}


async def learn(router, word, misheard, *more):
    corrections = [{"word": word, "misheard": misheard}] + [
        {"word": w, "misheard": m} for w, m in more
    ]
    return await router.call("dictionary.learn", {"corrections": corrections})


async def test_dictionary_learn_makes_a_rule_only_when_a_pair_is_seen_again(router):
    first = await learn(router, "Claude", "cloud")
    assert first == {"learned": ["Claude"]}, "a term and a hint: no rule, so no extra field"
    assert (await router.call("dictionary.list", {}))["entries"][0]["misheard"] == []
    second = await learn(router, "Claude", "cloud")
    assert second == {"learned": ["Claude"], "rules": ["Claude"]}
    assert (await router.call("dictionary.list", {}))["entries"][0]["misheard"] == ["cloud"]
    assert await learn(router, "Claude", "cloud") == {"learned": []}


async def test_dictionary_learn_counts_the_same_pair_once_in_a_dictation(router):
    result = await learn(router, "Claude", "cloud", ("Claude", "cloud"), ("Claude", " cloud "))
    assert result == {"learned": ["Claude"]}
    entries = (await router.call("dictionary.list", {}))["entries"]
    assert entries[0]["misheard"] == [], "three of the same are one sighting"


async def test_dictionary_learn_withdraws_a_rule_the_user_changes_back(router):
    await learn(router, "Claude", "cloud")
    await learn(router, "Claude", "cloud")
    reverted = await learn(router, "cloud", "Claude")
    assert reverted == {"learned": [], "reverted": ["Claude"]}
    entries = (await router.call("dictionary.list", {}))["entries"]
    assert [(e["word"], e["misheard"], e["source"]) for e in entries] == [("Claude", [], "learned")]
    assert await learn(router, "Claude", "cloud") == {"learned": []}, "not learned again"


async def test_a_pair_that_is_no_rule_does_not_rewrite_what_is_dictated(
    router, fake_client, keys
):
    # Learning the correction 轉錄 for 轉入 must not turn a right 轉入 into 轉錄, however often
    # it is seen, and "cloud" is Claude's only when the same correction comes twice.
    for _ in range(3):
        assert await learn(router, "轉錄", "轉入") == {"learned": ["轉錄"]}
    await learn(router, "Claude", "cloud")
    r = await finalize(router, "我想把錢轉入帳戶 upload it to the cloud")
    assert r["text"] == "我想把錢轉入帳戶 upload it to the cloud"
    await learn(router, "Claude", "cloud")
    r = await finalize(router, "我想把錢轉入帳戶 upload it to the cloud")
    assert r["text"] == "我想把錢轉入帳戶 upload it to the Claude"


async def test_the_hints_of_the_dictionary_reach_the_cleanup_prompt(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "好。"})
    await router.call("dictionary.add", {"word": "拉斐爾", "misheard": ["拉菲爾"]})
    await learn(router, "Claude", "cloud")
    await learn(router, "手機", "首機")
    await finalize(router, "好")
    system = fake_client.calls[0]["system"]
    assert "- 拉斐爾（常被聽成：拉菲爾）" in system
    assert "- Claude（常被聽成：cloud）" in system, "a hint is told to the model like a rule"
    assert "- 手機（常被聽成：首機）" in system
    # The words the user chose come before the ones learned.
    assert system.index("- 拉斐爾") < system.index("- 手機") < system.index("- Claude")


async def test_dictionary_learn_learns_even_if_an_older_version_saved_learning_off(router, conn):
    # Learning is always on now. A database from a version with the switch may hold it off: that
    # must neither be offered again nor stop the learning.
    conn.execute("INSERT INTO settings (key, value) VALUES ('learn_corrections', 'false')")
    assert "learn_corrections" not in (await router.call("settings.get", {}))["settings"]
    learned = await router.call(
        "dictionary.learn", {"corrections": [{"word": "手機", "misheard": "首機"}]}
    )
    assert learned == {"learned": ["手機"]}
    assert [e["word"] for e in (await router.call("dictionary.list", {}))["entries"]] == ["手機"]


async def test_dictionary_learn_does_not_log_the_words(router, caplog):
    caplog.set_level(logging.DEBUG)
    await router.call("dictionary.learn", {"corrections": [{"word": "手機", "misheard": "首機"}]})
    assert "手機" not in caplog.text and "首機" not in caplog.text


# --- what puts a word in the dictionary ----------------------------------------------------------
#
# A word joins the dictionary when the user adds or imports it, or when they corrected it in a
# pasted dictation (`dictionary.learn`). Never because it is said often: a word that speech-to-text
# writes right every time needs no entry.


async def test_a_word_said_ten_times_right_is_never_added(router, conn):
    for _ in range(10):
        result = await finalize(router, raw_text="今天用 Tailscale 連回家")
        assert "Tailscale" in result["text"]
    assert len(HistoryStore(conn).list(limit=50)) == 10
    assert (await router.call("dictionary.list", {}))["entries"] == []
    assert DictionaryStore(conn).hint_forms() == []


async def test_there_is_no_method_that_adds_words_by_how_often_they_are_said(router):
    with pytest.raises(RpcError) as raised:
        await router.call("dictionary.auto_add", {})
    assert raised.value.code == METHOD_NOT_FOUND


async def test_a_corrected_word_is_added_once_with_its_misheard_form(router, conn):
    assert await learn(router, "Tailscale", "tail scale") == {"learned": ["Tailscale"]}
    entries = (await router.call("dictionary.list", {}))["entries"]
    assert [(e["word"], e["source"]) for e in entries] == [("Tailscale", "learned")]
    # The misheard form is kept with the word: a hint for the cleanup model from the first
    # sighting, a replacement rule from the second.
    assert DictionaryStore(conn).hint_forms() == [("tail scale", "Tailscale")]
    assert await learn(router, "Tailscale", "tail scale") == {
        "learned": ["Tailscale"],
        "rules": ["Tailscale"],
    }
    entries = (await router.call("dictionary.list", {}))["entries"]
    assert [(e["word"], e["misheard"]) for e in entries] == [("Tailscale", ["tail scale"])]


async def test_retyping_the_same_word_is_no_correction(router, conn):
    assert await learn(router, "Tailscale", "Tailscale") == {"learned": []}
    assert await learn(router, "手機", " 手機 ") == {"learned": []}
    assert await learn(router, "Tailscale", "tailscale") == {"learned": []}, "letter case only"
    assert (await router.call("dictionary.list", {}))["entries"] == []
    assert DictionaryStore(conn).hint_forms() == []


# --- settings -----------------------------------------------------------------------------------


async def test_settings_get_and_set_round_trip(router):
    assert await router.call("settings.get", {}) == {"settings": DEFAULTS}
    after = await router.call("settings.set", {"key": "languages", "value": ["ja-JP", "en-GB"]})
    expected = {**DEFAULTS, "languages": ["ja-JP", "en-GB"]}
    assert after == {"settings": expected}
    assert (await router.call("settings.get", {}))["settings"] == expected
    # A whole number is a valid float setting.
    limit = await router.call("settings.set", {"key": "monthly_limit_usd", "value": 5})
    assert limit["settings"]["monthly_limit_usd"] == 5.0


async def test_a_rejected_setting_changes_nothing(router):
    with pytest.raises(RpcError):
        await router.call("settings.set", {"key": "monthly_limit_usd", "value": "lots"})
    assert (await router.call("settings.get", {}))["settings"] == DEFAULTS


@pytest.mark.parametrize("value", [[], ["zh-TW", "zh-CN"], ["en-US", "en-GB"], "zh-TW", ["auto"]])
async def test_an_invalid_selection_of_languages_is_invalid_params(router, value):
    with pytest.raises(RpcError) as raised:
        await router.call("settings.set", {"key": "languages", "value": value})
    assert raised.value.code == INVALID_PARAMS
    assert (await router.call("settings.get", {}))["settings"]["languages"] == ["zh-TW"]


async def test_the_assistants_settings_are_in_settings_get_and_can_be_set(router):
    settings = (await router.call("settings.get", {}))["settings"]
    assert (settings["agent_model"], settings["agent_city"]) == ("openai/gpt-6-luna", "台北")
    assert "agent_router" not in settings  # Jev always routes: there is no choice to show
    # Before OpenRouter's list of models has been read, the model is one of Raphael's own
    # (test_model_choice.py has the rest).
    for key, value in [("agent_model", "anthropic/claude-haiku-5.5"), ("agent_city", "東京")]:
        settings = (await router.call("settings.set", {"key": key, "value": value}))["settings"]
        assert settings[key] == value


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("agent_model", " "),
        ("agent_model", "two words"),
        ("agent_city", ""),
        ("agent_city", None),
    ],
)
async def test_a_bad_value_for_the_assistants_settings_is_invalid_params(router, key, value):
    with pytest.raises(RpcError) as raised:
        await router.call("settings.set", {"key": key, "value": value})
    assert raised.value.code == INVALID_PARAMS
    assert (await router.call("settings.get", {}))["settings"][key] == DEFAULTS[key]


async def test_languages_list_gives_the_groups_and_their_variants(router):
    result = await router.call("languages.list", {})
    groups = result["groups"]
    assert [group["id"] for group in groups][:3] == ["zh", "en", "ja"]
    assert groups[0]["variants"][0] == {
        "id": "zh-TW",
        "name": "繁體中文（台灣）",
        "note": "台灣用語，例如 軟體、視窗",
    }
    assert json.loads(json.dumps(result)) == result


# --- the Chinese of the chosen variant ------------------------------------------------------------


async def test_finalize_writes_mainland_simplified_for_zh_cn(router, fake_client, keys):
    keys.set(KEY)
    await router.call("settings.set", {"key": "languages", "value": ["zh-CN", "en-US"]})
    # The model leaves a Traditional word in its reply: it is made Simplified.
    fake_client.next_completion = completion({"text": "这个软件很好用，我們明天開會。"})
    r = await finalize(router, "這個軟件很好用 我们明天开会")
    assert r["raw_text"] == "这个软件很好用 我们明天开会"
    assert r["cleaned"] is True and r["text"] == "这个软件很好用，我们明天开会。"
    assert "Simplified characters with Mainland usage" in fake_client.calls[0]["system"]
    assert (await router.call("history.latest", {}))["item"]["text"] == r["text"]


async def test_a_fallback_for_zh_cn_pastes_simplified(router, fake_client, keys):
    keys.set(KEY)
    await router.call("settings.set", {"key": "languages", "value": ["zh-CN"]})
    fake_client.next_error = OpenRouterError(0, "network", "offline")
    r = await finalize(router, "這個軟件")
    assert (r["text"], r["cleaned"]) == ("这个软件", False)


async def test_finalize_writes_hong_kong_traditional_for_zh_hk(router, fake_client, keys):
    keys.set(KEY)
    await router.call("settings.set", {"key": "languages", "value": ["zh-HK"]})
    fake_client.next_completion = completion({"text": "就是只要打开這個软件。"})
    r = await finalize(router, "就是只要打开这个软件")
    assert r["raw_text"] == "就是隻要打開這個軟件"  # s2hk alone, as for Taiwan
    assert r["text"] == "就是只要打開這個軟件。"  # 只要 stays: only the Simplified words change
    assert "Hong Kong usage" in fake_client.calls[0]["system"]


async def test_without_chinese_nothing_is_converted(router, fake_client, keys):
    keys.set(KEY)
    await router.call("settings.set", {"key": "languages", "value": ["en-GB"]})
    fake_client.next_completion = completion({"text": "这个 colour."})
    r = await finalize(router, "这个 colour")
    assert (r["raw_text"], r["text"]) == ("这个 colour", "这个 colour.")


# --- brain.normalize_transcript -----------------------------------------------------------------
#
# What the voice assistant is asked: the transcript with the dictionary's replacements and the
# Chinese of the chosen variant, and nothing else.


async def normalize(router, raw_text):
    return await router.call("brain.normalize_transcript", {"raw_text": raw_text})


async def test_normalize_applies_the_replacements_and_the_chinese_script(router):
    await router.call("dictionary.add", {"word": "拉斐爾", "misheard": ["拉菲爾"]})
    assert await normalize(router, "拉菲爾 打开软件") == {"text": "拉斐爾 開啟軟體"}  # Taiwan usage


async def test_normalize_leaves_traditional_chinese_alone(router):
    # s2twp over a Traditional sentence would turn 只要 into 隻要.
    assert await normalize(router, "只要你打開這個軟體") == {"text": "只要你打開這個軟體"}


@pytest.mark.parametrize(
    ("languages", "expected"),
    [
        (["zh-CN"], "这个软件"),
        (["zh-HK"], "這個軟件"),
        (["en-GB"], "這個软件"),  # no Chinese chosen: nothing is converted
        (["ja-JP"], "這個软件"),
    ],
)
async def test_normalize_follows_the_chinese_variant_of_the_languages_setting(
    router, languages, expected
):
    await router.call("settings.set", {"key": "languages", "value": languages})
    assert await normalize(router, "這個软件") == {"text": expected}


async def test_normalize_of_nothing_is_nothing(router):
    assert await normalize(router, "") == {"text": ""}
    assert await normalize(router, "  ") == {"text": "  "}  # the words are not trimmed here


async def test_normalize_asks_no_model_writes_no_history_and_costs_nothing(
    router, fake_client, keys, conn
):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "不該被問到"})
    await normalize(router, "你好")
    assert fake_client.calls == []
    assert (await router.call("history.list", {}))["items"] == []
    assert conn.execute("SELECT count(*) FROM spend").fetchone()[0] == 0


async def test_normalize_works_without_a_key_and_over_the_monthly_limit(router, conn, keys):
    assert keys.get() is None
    assert await normalize(router, "你好") == {"text": "你好"}
    keys.set(KEY)
    await router.call("settings.set", {"key": "monthly_limit_usd", "value": 1})
    conn.execute(
        "INSERT INTO spend (created_at_ms, purpose, model, cost_usd, prompt_tokens,"
        " completion_tokens) VALUES (?, 'cleanup', 'm', 5.0, 1, 1)",
        (NOW_MS,),
    )
    assert await normalize(router, "你好") == {"text": "你好"}


# --- secrets ------------------------------------------------------------------------------------


async def test_secrets_status_follows_the_key_store(router, keys):
    assert await router.call("secrets.status", {}) == {"has_key": False}
    keys.set(KEY)
    assert await router.call("secrets.status", {}) == {"has_key": True}


async def test_a_valid_key_is_checked_with_itself_and_then_stored(router, fake_client, keys):
    stored_during_check = []
    fake_client.key_info_hook = lambda key: stored_during_check.append(keys.get())
    r = await router.call("secrets.set_openrouter_key", {"key": "sk-or-v1-good"})
    assert r == {
        "valid": True,
        "label": "test key",
        "limit": 20.0,
        "limit_remaining": 17.5,
        "limit_reset": "monthly",
    }
    assert fake_client.key_info_calls == ["sk-or-v1-good"]
    assert stored_during_check == [None]  # not stored before it was found valid
    assert keys.get() == "sk-or-v1-good"
    assert await router.call("secrets.status", {}) == {"has_key": True}


async def test_a_key_is_trimmed_before_it_is_checked_and_stored(router, fake_client, keys):
    await router.call("secrets.set_openrouter_key", {"key": "  sk-or-v1-good\n"})
    assert fake_client.key_info_calls == ["sk-or-v1-good"]
    assert keys.get() == "sk-or-v1-good"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
async def test_a_blank_key_is_an_auth_failure_without_a_call(router, fake_client, keys, blank):
    r = await router.call("secrets.set_openrouter_key", {"key": blank})
    assert r == {"valid": False, "reason": "auth"}
    assert fake_client.key_info_calls == [] and keys.get() is None


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (0, "network"), (0, "timeout"), (429, "rate_limit"), (503, "server")],
)
async def test_a_key_that_cannot_be_checked_is_not_stored(router, fake_client, keys, status, kind):
    keys.set("sk-or-v1-old")
    fake_client.key_info_error = OpenRouterError(status, kind, "no")
    r = await router.call("secrets.set_openrouter_key", {"key": "sk-or-v1-new"})
    assert r == {"valid": False, "reason": kind}
    assert keys.get() == "sk-or-v1-old"  # the key in use stays
    # Nothing was learned about the key in use, so the status does not change.
    assert (await router.call("status.get", {}))["openrouter"]["state"] == "unknown"


async def test_a_key_check_that_hangs_gives_up(make_brain, fake_client, keys):
    router = routed(make_brain(key_check_timeout_s=0.05))
    fake_client.key_info_delay = 5.0
    r = await router.call("secrets.set_openrouter_key", {"key": "sk-or-v1-slow"})
    assert r == {"valid": False, "reason": "timeout"} and keys.get() is None


async def test_clearing_the_key_forgets_the_status(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_error = OpenRouterError(0, "network", "offline")
    await finalize(router)
    assert (await router.call("status.get", {}))["openrouter"]["state"] == "error"
    assert await router.call("secrets.clear_openrouter_key", {}) == {}
    assert keys.get() is None
    assert await router.call("secrets.status", {}) == {"has_key": False}
    assert (await router.call("status.get", {}))["openrouter"]["state"] == "no_key"
    keys.set(KEY)  # a key that arrives later starts afresh
    fresh = (await router.call("status.get", {}))["openrouter"]
    assert (fresh["state"], fresh["error"], fresh["key"]) == ("unknown", None, None)


# --- the key store fails ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("secrets.status", {}),
        ("secrets.set_openrouter_key", {"key": "sk-or-v1-good"}),
        ("secrets.clear_openrouter_key", {}),
    ],
)
async def test_a_failing_key_store_is_an_internal_error_without_its_message(
    conn, fake_client, method, params, caplog
):
    caplog.set_level(logging.DEBUG)
    router = routed(Brain(conn, fake_client, BrokenKeyStore(), now_ms=Clock()))
    with pytest.raises(RpcError) as e:
        await router.call(method, params)
    assert e.value.code == INTERNAL_ERROR and SECRET not in e.value.message
    assert SECRET not in caplog.text
    assert_no_traceback_logged(caplog)


@pytest.mark.parametrize(
    ("method", "params"),
    [("secrets.set_openrouter_key", {"key": SECRET}), ("status.check_openrouter", {})],
)
async def test_a_client_that_breaks_is_an_internal_error_without_its_message(
    router, fake_client, keys, method, params, caplog
):
    # OpenRouterClient fails with OpenRouterError and nothing else; if it ever does, the message
    # of the bug could hold the key, and neither the log nor the reply may carry it.
    keys.set("sk-or-v1-old")
    fake_client.key_info_error = RuntimeError(f"a bug; the key is {SECRET}")
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RpcError) as e:
        await router.call(method, params)
    assert e.value.code == INTERNAL_ERROR and SECRET not in e.value.message
    assert SECRET not in caplog.text
    assert_no_traceback_logged(caplog)
    assert keys.get() == "sk-or-v1-old"


async def test_dictation_survives_a_failing_key_store(conn, fake_client, caplog):
    caplog.set_level(logging.DEBUG)
    router = routed(Brain(conn, fake_client, BrokenKeyStore(), now_ms=Clock()))
    r = await finalize(router, "你好")
    assert (r["text"], r["cleaned"], r["fallback_reason"]) == ("你好", False, "error:unexpected")
    assert fake_client.calls == []
    assert SECRET not in caplog.text
    assert_no_traceback_logged(caplog)


async def test_status_reports_a_failing_key_store_as_an_auth_error(conn, fake_client):
    router = routed(Brain(conn, fake_client, BrokenKeyStore(), now_ms=Clock()))
    status = await router.call("status.get", {})
    assert (status["openrouter"]["state"], status["openrouter"]["error"]) == ("error", "auth")
    assert set(status) == {"openrouter", "spend", "last"}


# --- status -------------------------------------------------------------------------------------


async def test_status_without_a_key_or_history(router):
    assert await router.call("status.get", {}) == {
        "openrouter": {
            "state": "no_key",
            "latency_ms": None,
            "checked_at_ms": None,
            "error": None,
            "key": None,
            "usage": None,
        },
        "spend": {
            "month_usd": 0.0,
            "today_usd": 0.0,
            "limit_usd": 10.0,
            "forecast_usd": 0.0,
            "daily": [0.0] * 30,
        },
        "last": None,
    }


async def test_status_is_unknown_until_openrouter_has_been_in_touch(router, fake_client, keys):
    keys.set(KEY)
    openrouter = (await router.call("status.get", {}))["openrouter"]
    assert openrouter == {
        "state": "unknown",
        "latency_ms": None,
        "checked_at_ms": None,
        "error": None,
        "key": None,
        "usage": None,
    }
    # The first read returns immediately, before the background key check runs.
    assert fake_client.calls == [] and fake_client.key_info_calls == []


async def test_status_after_a_cleanup(router, fake_client, keys):
    keys.set(KEY)
    fake_client.next_completion = completion({"text": "你好。"}, cost=0.0003)
    r = await finalize(router, "你好", audio_seconds=1.5, stt_ms=120, target_app="Code.exe")
    status = await router.call("status.get", {})
    assert status["openrouter"] == {
        "state": "ok",
        "latency_ms": r["llm_ms"],
        "checked_at_ms": NOW_MS,
        "error": None,
        "key": None,
        "usage": None,
    }
    spend = status["spend"]
    assert spend["month_usd"] == pytest.approx(0.0003)
    assert spend["today_usd"] == pytest.approx(0.0003)
    assert spend["limit_usd"] == 10.0
    assert len(spend["daily"]) == 30 and spend["daily"][-1] == pytest.approx(0.0003)
    assert spend["forecast_usd"] >= spend["month_usd"]
    assert status["last"] == {
        "text": "你好。",
        "created_at_ms": NOW_MS,
        "target_app": "Code.exe",
        "stt_ms": 120,
        "llm_ms": r["llm_ms"],
    }


@pytest.mark.parametrize(
    ("status", "kind"), [(0, "network"), (0, "timeout"), (401, "auth"), (402, "credits")]
)
async def test_status_holds_the_kind_of_the_last_failure(router, fake_client, keys, status, kind):
    keys.set(KEY)
    fake_client.next_error = OpenRouterError(status, kind, "no")
    await finalize(router)
    openrouter = (await router.call("status.get", {}))["openrouter"]
    assert (openrouter["state"], openrouter["error"], openrouter["latency_ms"]) == (
        "error",
        kind,
        None,
    )
    assert openrouter["checked_at_ms"] == NOW_MS


async def test_status_recovers_when_a_later_call_succeeds(router, fake_client, clock, keys):
    keys.set(KEY)
    fake_client.next_error = OpenRouterError(0, "network", "offline")
    await finalize(router)
    clock.now_ms += 60_000
    fake_client.next_error = None
    fake_client.next_completion = completion({"text": "你好。"})
    await finalize(router)
    openrouter = (await router.call("status.get", {}))["openrouter"]
    assert (openrouter["state"], openrouter["error"]) == ("ok", None)
    assert openrouter["checked_at_ms"] == NOW_MS + 60_000


async def test_status_ignores_dictations_that_never_asked_the_model(
    router, fake_client, keys, conn
):
    keys.clear()
    r = await finalize(router)
    assert r["fallback_reason"] == "no_key" and fake_client.calls == []
    assert (await router.call("status.get", {}))["openrouter"]["state"] == "no_key"


async def test_a_valid_key_makes_the_status_ok(router):
    await router.call("secrets.set_openrouter_key", {"key": "sk-or-v1-good"})
    openrouter = (await router.call("status.get", {}))["openrouter"]
    assert (openrouter["state"], openrouter["error"], openrouter["checked_at_ms"]) == (
        "ok",
        None,
        NOW_MS,
    )
    assert isinstance(openrouter["latency_ms"], int)
    assert openrouter["key"]["label"] == "test key"


async def test_check_without_a_key_makes_no_call(router, fake_client):
    r = await router.call("status.check_openrouter", {})
    assert (r["state"], r["key"], r["checked_at_ms"]) == ("no_key", None, None)
    assert fake_client.key_info_calls == []


async def test_check_asks_openrouter_about_the_stored_key(router, fake_client, keys):
    keys.set(KEY)
    r = await router.call("status.check_openrouter", {})
    assert fake_client.key_info_calls == [None]  # no key passed: the client reads the stored one
    assert (r["state"], r["error"], r["checked_at_ms"]) == ("ok", None, NOW_MS)
    assert isinstance(r["latency_ms"], int)
    assert r["key"] == {
        "label": "test key",
        "limit": 20.0,
        "limit_remaining": 17.5,
        "limit_reset": "monthly",
        "usage_daily": 0.1,
        "usage_monthly": 2.5,
        "usage_weekly": None,
        "usage_total": None,
    }
    assert (await router.call("status.get", {}))["openrouter"] == r


async def test_a_failed_check_is_reported_and_keeps_the_last_key_info(router, fake_client, keys):
    keys.set(KEY)
    first = await router.call("status.check_openrouter", {})
    fake_client.key_info_error = OpenRouterError(0, "network", "offline")
    r = await router.call("status.check_openrouter", {})
    assert (r["state"], r["error"], r["latency_ms"]) == ("error", "network", None)
    assert r["key"] == first["key"]
    assert (await router.call("status.get", {}))["openrouter"] == r


async def test_a_check_that_hangs_gives_up(make_brain, fake_client, keys):
    router = routed(make_brain(key_check_timeout_s=0.05))
    keys.set(KEY)
    fake_client.key_info_delay = 5.0
    r = await router.call("status.check_openrouter", {})
    assert (r["state"], r["error"]) == ("error", "timeout")


# --- construction -------------------------------------------------------------------------------


def test_construction_prunes_history_older_than_the_retention(make_brain, conn):
    store = HistoryStore(conn)
    old = store.add(item(created_at_ms=NOW_MS - 30 * DAY_MS - 1))
    edge = store.add(item(created_at_ms=NOW_MS - 30 * DAY_MS))
    fresh = store.add(item(created_at_ms=NOW_MS - DAY_MS))
    make_brain()
    assert store.get(old) is None
    assert store.get(edge) is not None and store.get(fresh) is not None


def test_construction_follows_the_retention_setting(make_brain, conn):
    SettingsStore(conn).set("history_retention_days", 7)
    store = HistoryStore(conn)
    old = store.add(item(created_at_ms=NOW_MS - 8 * DAY_MS))
    kept = store.add(item(created_at_ms=NOW_MS - 6 * DAY_MS))
    make_brain()
    assert store.get(old) is None and store.get(kept) is not None


@pytest.mark.parametrize("days", [-1, 10**30])
def test_construction_survives_a_retention_that_makes_no_sense(make_brain, conn, days):
    SettingsStore(conn).set("history_retention_days", days)
    store = HistoryStore(conn)
    ancient = store.add(item(created_at_ms=1))
    make_brain()
    assert store.get(ancient) is not None


def test_construction_goes_on_when_pruning_fails(make_brain, conn, caplog):
    conn.execute("DROP TABLE history")
    caplog.set_level(logging.DEBUG)
    make_brain()
    assert_no_traceback_logged(caplog)
    mine = [r.getMessage() for r in caplog.records if r.name == service.logger.name]
    assert any("prune" in message for message in mine), "the failure is logged"


def test_construction_makes_no_network_call(make_brain, fake_client, keys):
    keys.set(KEY)
    make_brain()
    assert fake_client.calls == [] and fake_client.key_info_calls == []


# --- with the real client -----------------------------------------------------------------------
# The fakes above could drift from OpenRouterClient. These tests wire the brain the way the entry
# point does (OpenRouterClient(keys.get)) and put a fake OpenRouter behind it, on httpx.


class FakeOpenRouter:
    """The HTTP side of OpenRouter, as a handler for httpx.MockTransport.

    Only `valid_key` is accepted; any other key gets a 401. A chat completion answers with
    `reply_text`, or fails with `chat_status` if that is not 200.
    """

    def __init__(self, valid_key=KEY):
        self.valid_key = valid_key
        self.reply_text = "你好。"
        self.chat_status = 200
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Bearer {self.valid_key}":
            return httpx.Response(401, json={"error": {"message": "No auth credentials found"}})
        if request.url.path.endswith("/key"):
            data = {
                "label": "Raphael",
                "limit": 20,
                "limit_remaining": 17.5,
                "limit_reset": "monthly",
                "usage_daily": 0.1,
                "usage_monthly": 2.5,
            }
            return httpx.Response(200, json={"data": data})
        if self.chat_status != 200:
            return httpx.Response(self.chat_status, json={"error": {"message": "unavailable"}})
        content = json.dumps({"text": self.reply_text}, ensure_ascii=False)
        choice = {"message": {"content": content}, "finish_reason": "stop"}
        usage = {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.0002}
        return httpx.Response(200, json={"choices": [choice], "usage": usage})


@pytest.fixture
async def real_router(conn, keys, clock):
    """`real_router(handler)` is a router on a brain whose client talks HTTP to `handler`."""
    opened = []

    def make(handler):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        brain = Brain(conn, OpenRouterClient(keys.get, http=http), keys, now_ms=clock)
        opened.append((brain, http))
        return routed(brain)

    yield make
    for brain, http in opened:
        await brain.aclose()
        await http.aclose()


async def test_real_client_a_key_is_checked_over_http_then_used(real_router, keys):
    openrouter = FakeOpenRouter(valid_key="sk-or-v1-good")
    router = real_router(openrouter)

    bad = await router.call("secrets.set_openrouter_key", {"key": "sk-or-v1-bad"})
    assert bad == {"valid": False, "reason": "auth"} and keys.get() is None

    good = await router.call("secrets.set_openrouter_key", {"key": "sk-or-v1-good"})
    assert good == {
        "valid": True,
        "label": "Raphael",
        "limit": 20.0,
        "limit_remaining": 17.5,
        "limit_reset": "monthly",
    }
    assert keys.get() == "sk-or-v1-good"

    # The key just stored is the one the client sends from now on.
    openrouter.reply_text = "明天下午三點開會。"
    r = await finalize(router, "明天下午三点开会")
    assert (r["cleaned"], r["text"]) == (True, "明天下午三點開會。")
    assert openrouter.requests[-1].headers["Authorization"] == "Bearer sk-or-v1-good"
    assert (await router.call("status.get", {}))["openrouter"]["state"] == "ok"
    checked = await router.call("status.check_openrouter", {})
    assert (checked["state"], checked["key"]["label"]) == ("ok", "Raphael")


async def test_real_client_a_server_error_pastes_the_raw_text(real_router, keys):
    keys.set(KEY)
    openrouter = FakeOpenRouter()
    openrouter.chat_status = 503
    router = real_router(openrouter)
    r = await finalize(router, "你好")
    assert (r["text"], r["cleaned"], r["fallback_reason"]) == ("你好", False, "error:server")
    assert (await router.call("status.get", {}))["openrouter"]["error"] == "server"


async def test_real_client_a_key_that_has_become_invalid_shows_as_an_auth_error(real_router, keys):
    keys.set("sk-or-v1-revoked")
    router = real_router(FakeOpenRouter(valid_key=KEY))
    checked = await router.call("status.check_openrouter", {})
    assert (checked["state"], checked["error"]) == ("error", "auth")


# --- logging ------------------------------------------------------------------------------------


async def test_nothing_secret_is_logged_and_no_traceback(router, fake_client, keys, caplog):
    # The failures below carry the key in their message and in their cause, as a key store's might.
    caplog.set_level(logging.DEBUG)
    error = OpenRouterError(401, "auth", f"rejected {SECRET}")
    error.__cause__ = RuntimeError(f"the key store said {SECRET}")
    fake_client.key_info_error = error
    assert await router.call("secrets.set_openrouter_key", {"key": SECRET}) == {
        "valid": False,
        "reason": "auth",
    }
    keys.set(SECRET)
    await router.call("status.check_openrouter", {})
    fake_client.next_error = error
    await finalize(router, "機密內容")
    assert SECRET not in caplog.text
    assert "機密內容" not in caplog.text
    assert_no_traceback_logged(caplog)
    mine = [r for r in caplog.records if r.name == service.logger.name]
    assert any("auth" in r.getMessage() for r in mine), "the failure's kind is logged"


# --- history retention while the core runs ------------------------------------------------------


async def test_changing_the_retention_prunes_the_history_at_once(router, conn):
    store = HistoryStore(conn)
    old = store.add(item(created_at_ms=NOW_MS - 8 * DAY_MS))
    kept = store.add(item(created_at_ms=NOW_MS - 6 * DAY_MS))
    await router.call("settings.set", {"key": "history_retention_days", "value": 7})
    assert store.get(old) is None and store.get(kept) is not None


async def test_a_retention_that_is_refused_prunes_nothing(router, conn):
    store = HistoryStore(conn)
    old = store.add(item(created_at_ms=NOW_MS - 8 * DAY_MS))
    with pytest.raises(RpcError):
        await router.call("settings.set", {"key": "history_retention_days", "value": "7"})
    assert store.get(old) is not None


async def test_a_core_that_runs_for_days_prunes_once_a_day_when_it_dictates(router, conn, clock):
    store = HistoryStore(conn)
    # Kept when the brain was made (pruned at NOW_MS): one hour inside the 30 days.
    aging = store.add(item(created_at_ms=NOW_MS - 30 * DAY_MS + 3_600_000))
    clock.now_ms += 2 * 3_600_000  # now it is past the retention
    await finalize(router)
    assert store.get(aging) is not None  # less than a day since the last prune: not again yet
    clock.now_ms = NOW_MS + DAY_MS
    await finalize(router)
    assert store.get(aging) is None
    # The dictation just saved is fresh and stays.
    assert [entry["raw_text"] for entry in (await router.call("history.list", {}))["items"]] == [
        "你好",
        "你好",
    ]


async def test_changing_the_retention_starts_the_day_again(router, conn, clock):
    store = HistoryStore(conn)
    clock.now_ms += 12 * 3_600_000
    await router.call("settings.set", {"key": "history_retention_days", "value": 30})  # prunes
    aging = store.add(item(created_at_ms=clock.now_ms - 30 * DAY_MS + 3_600_000))
    clock.now_ms = NOW_MS + DAY_MS + 3_600_000  # a day after the start, not after the change
    await finalize(router)
    assert store.get(aging) is not None
    clock.now_ms = NOW_MS + 12 * 3_600_000 + DAY_MS
    await finalize(router)
    assert store.get(aging) is None


# --- dictionary.import ---------------------------------------------------------------------------


async def imported(router, words):
    return await router.call("dictionary.import", {"words": words})


async def test_import_adds_the_words_as_manual_entries(router):
    assert await imported(router, ["Tailscale", "拉斐爾"]) == {"added": 2, "skipped": 0, "invalid": 0}
    entries = (await router.call("dictionary.list", {}))["entries"]
    assert sorted((e["word"], e["source"], e["misheard"]) for e in entries) == [
        ("Tailscale", "manual", []),
        ("拉斐爾", "manual", []),
    ]


async def test_import_skips_known_words_in_any_letter_case_and_repeats_in_the_list(router):
    await router.call("dictionary.add", {"word": "Tailscale", "misheard": ["tail scale"]})
    result = await imported(router, ["tailscale", "OpenRouter", "openrouter", " OpenRouter "])
    assert result == {"added": 1, "skipped": 3, "invalid": 0}
    entries = (await router.call("dictionary.list", {}))["entries"]
    assert sorted(e["word"] for e in entries) == ["OpenRouter", "Tailscale"]
    assert [e["misheard"] for e in entries if e["word"] == "Tailscale"] == [["tail scale"]]


async def test_import_counts_blank_words_as_invalid(router):
    assert await imported(router, ["Kokoro", "  ", ""]) == {"added": 1, "skipped": 0, "invalid": 2}


async def test_import_takes_a_word_the_user_dismissed_and_forgets_the_dismissal(router, conn):
    dismiss(conn, "OpenRouter")
    assert await imported(router, ["openrouter"]) == {"added": 1, "skipped": 0, "invalid": 0}
    assert DictionaryStore(conn).dismissed() == set()


async def test_import_is_one_transaction(router, monkeypatch):
    real = DictionaryStore._put
    calls = []

    def failing(self, word, forms, source):
        calls.append(word)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk full")
        return real(self, word, forms, source)

    monkeypatch.setattr(DictionaryStore, "_put", failing)
    with pytest.raises(RpcError) as err:
        await imported(router, ["a1", "b2", "c3"])
    assert err.value.code == INTERNAL_ERROR
    monkeypatch.undo()
    assert (await router.call("dictionary.list", {}))["entries"] == []


async def test_import_never_logs_the_words(router, caplog):
    caplog.set_level(logging.DEBUG)
    await imported(router, ["SecretProjectName"])
    assert "SecretProjectName" not in caplog.text


async def test_import_takes_at_most_5000_words(router):
    await imported(router, [f"w{n}" for n in range(service.IMPORT_MAX)])
    with pytest.raises(RpcError) as err:
        await imported(router, [f"x{n}" for n in range(service.IMPORT_MAX + 1)])
    assert err.value.code == INVALID_PARAMS


@pytest.mark.parametrize("params", [{}, {"words": "x"}, {"words": [1]}, {"words": None}])
async def test_import_refuses_bad_params(router, params):
    with pytest.raises(RpcError) as err:
        await router.call("dictionary.import", params)
    assert err.value.code == INVALID_PARAMS
