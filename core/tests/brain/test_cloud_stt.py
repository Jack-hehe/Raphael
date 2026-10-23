"""Tests for `stt.transcribe`, the brain's cloud speech-to-text, called through a Router.

The OpenRouter client is a fake (the last test uses the real client on an httpx.MockTransport),
the key store is a MemoryKeyStore and the stores are real ones on an in-memory database. No test
touches the network or a real key.
"""

import asyncio
import base64
import io
import json
import logging
import wave
from datetime import UTC, datetime

import httpx
import pytest

from raphael_core.brain import cloud_stt
from raphael_core.brain.openrouter import OpenRouterClient, OpenRouterError, Transcription
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.settings import SettingsStore
from raphael_core.protocol import (
    BUDGET_EXCEEDED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    NOT_CONFIGURED,
    RpcError,
)
from raphael_core.router import Router

NOW_MS = int(datetime(2026, 10, 18, 12, tzinfo=UTC).timestamp() * 1000)
KEY = "sk-or-v1-test"


def wav_b64(seconds: float = 2.0, rate: int = 16_000) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * round(seconds * rate))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class FakeClient:
    """Stands in for OpenRouterClient's transcribe; `delay` makes it slow."""

    def __init__(self):
        self.calls = []
        self.error = None
        self.delay = 0.0
        self.result = self.heard("你好世界")
        self.later = []  # what the calls after the first hear: each a result or an error

    @staticmethod
    def heard(text):
        return Transcription(
            text=text,
            model="elevenlabs/scribe-v2",
            cost_usd=0.00015,
            seconds=2.0,
            input_tokens=0,
            output_tokens=0,
            latency_ms=300,
        )

    async def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if len(self.calls) > 1 and self.later:
            heard = self.later.pop(0)
            if isinstance(heard, Exception):
                raise heard
            return heard
        return self.result


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def keys():
    store = MemoryKeyStore()
    store.set(KEY)
    return store


@pytest.fixture
def router(conn, fake_client, keys):
    router = Router()
    Brain(conn, fake_client, keys, now_ms=lambda: NOW_MS).register(router)
    return router


async def transcribe(router, **overrides):
    params = {"audio": wav_b64(), "hints": ["拉斐爾"], "language": "auto", **overrides}
    return await router.call("stt.transcribe", params)


def spend_rows(conn):
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT purpose, model, cost_usd, prompt_tokens, completion_tokens FROM spend"
        )
    ]


# --- what is asked and what comes back --------------------------------------------------------


async def test_the_audio_the_hints_and_the_model_of_the_settings_go_to_the_client(
    router, fake_client
):
    audio = wav_b64(3.0)
    result = await transcribe(router, audio=audio, hints=["拉斐爾", "Tauri"])
    assert result == {"text": "你好世界", "model": "elevenlabs/scribe-v2", "cost_usd": 0.00015}
    (call,) = fake_client.calls
    assert call["model"] == "elevenlabs/scribe-v2"
    assert call["audio_b64"] == audio
    assert call["hints"] == ["拉斐爾", "Tauri"]
    assert call["language"] is None  # automatic


@pytest.mark.parametrize(
    "language", ["zh", "ja", "en", "ko", "fr", "de", "es", "it", "pt", "vi", "th", "id"]
)
async def test_a_chosen_language_goes_as_its_iso_code(router, fake_client, language):
    await transcribe(router, language=language)
    assert fake_client.calls[0]["language"] == language
    assert fake_client.calls[0]["languages"] == []


async def test_cantonese_goes_to_the_cloud_as_chinese(router, fake_client):
    # OpenRouter documents ISO 639-1 codes only, and Cantonese has none.
    await transcribe(router, language="yue")
    assert fake_client.calls[0]["language"] == "zh"


async def test_automatic_hands_the_codes_of_the_chosen_languages_along(router, fake_client):
    await router.call("settings.set", {"key": "languages", "value": ["yue-HK", "en-GB", "ja-JP"]})
    await transcribe(router, language="auto")
    call = fake_client.calls[0]
    assert call["language"] is None
    assert call["languages"] == ["zh", "en", "ja"]


async def test_a_single_language_hands_no_list_along(router, fake_client):
    await router.call("settings.set", {"key": "languages", "value": ["en-GB", "ja-JP"]})
    await transcribe(router, language="en")
    assert (fake_client.calls[0]["language"], fake_client.calls[0]["languages"]) == ("en", [])


async def test_the_model_follows_the_setting(router, fake_client):
    await router.call("settings.set", {"key": "stt_cloud_model", "value": "elevenlabs/scribe-v2"})
    await transcribe(router)
    assert fake_client.calls[0]["model"] == "elevenlabs/scribe-v2"


async def test_the_timeout_grows_with_the_audio_up_to_30_seconds(router, fake_client):
    assert cloud_stt.timeout_for(2.0) == 7.0
    assert cloud_stt.timeout_for(40.0) == 30.0
    await transcribe(router, audio=wav_b64(4.0))
    assert fake_client.calls[0]["timeout_s"] == pytest.approx(9.0)


async def test_what_it_cost_is_recorded_in_the_spend(router, conn):
    await transcribe(router)
    assert spend_rows(conn) == [("stt", "elevenlabs/scribe-v2", 0.00015, 0, 0)]
    status = await router.call("status.get", {})
    assert status["spend"]["month_usd"] == pytest.approx(0.00015)


# --- a transcript in a script the user does not write ------------------------------------------


async def test_an_automatic_transcript_in_a_foreign_script_is_heard_again_in_the_first_language(
    router, fake_client, conn
):
    fake_client.result = fake_client.heard("ใครใช้ GitHub")
    fake_client.later = [fake_client.heard("開啟 GitHub")]
    result = await transcribe(router, language="auto")
    assert result["text"] == "開啟 GitHub"
    first, second = fake_client.calls
    assert first["language"] is None
    assert (second["language"], second["languages"]) == ("zh", [])  # the first of the languages
    assert second["audio_b64"] == first["audio_b64"] and second["hints"] == first["hints"]
    assert len(spend_rows(conn)) == 2  # both calls were paid for


@pytest.mark.parametrize(
    ("languages", "heard", "first_code"),
    [
        (["ja-JP", "en-US"], "Привет, как дела", "ja"),
        (["en-US"], "مرحبا بالعالم", "en"),
        (["zh-HK", "en-GB"], "नमस्ते दुनिया", "zh"),
    ],
)
async def test_each_script_outside_the_languages_is_heard_again(
    router, fake_client, languages, heard, first_code
):
    await router.call("settings.set", {"key": "languages", "value": languages})
    fake_client.result = fake_client.heard(heard)
    await transcribe(router)
    assert [call["language"] for call in fake_client.calls] == [None, first_code]


@pytest.mark.parametrize(
    ("languages", "heard"),
    [
        (["zh-TW", "en-US", "ja-JP"], "開啟 GitHub 然後 open the page"),
        (["zh-TW", "en-US", "ja-JP"], "これは日本語です"),
        (["zh-TW"], "ok"),
        (["zh-TW"], "12:30"),
        (["zh-TW", "th-TH"], "สวัสดีครับ"),
        (["ko-KR"], "안녕하세요"),
        (["ja-JP"], "コーヒーをください"),
    ],
)
async def test_a_transcript_in_the_scripts_of_the_languages_is_taken_as_it_is(
    router, fake_client, languages, heard
):
    await router.call("settings.set", {"key": "languages", "value": languages})
    fake_client.result = fake_client.heard(heard)
    result = await transcribe(router)
    assert result["text"] == heard and len(fake_client.calls) == 1


async def test_a_language_the_user_chose_is_left_alone(router, fake_client):
    fake_client.result = fake_client.heard("ใครใช้ GitHub")
    result = await transcribe(router, language="zh")
    assert result["text"] == "ใครใช้ GitHub" and len(fake_client.calls) == 1


async def test_the_second_hearing_is_the_last(router, fake_client):
    fake_client.result = fake_client.heard("ใครใช้ GitHub")
    fake_client.later = [fake_client.heard("ใครใช้ GitHub อีกครั้ง"), fake_client.heard("x")]
    result = await transcribe(router)
    assert result["text"] == "ใครใช้ GitHub อีกครั้ง" and len(fake_client.calls) == 2


async def test_when_the_second_hearing_fails_the_first_transcript_stands(
    router, fake_client, caplog
):
    fake_client.result = fake_client.heard("ใครใช้ GitHub")
    fake_client.later = [OpenRouterError(0, "network", "no")]
    with caplog.at_level(logging.DEBUG):
        result = await transcribe(router)
    assert result["text"] == "ใครใช้ GitHub"
    assert "ใคร" not in caplog.text and "GitHub" not in caplog.text


# --- refusals ---------------------------------------------------------------------------------


async def test_no_key_is_not_configured_and_asks_nobody(router, fake_client, keys, conn):
    keys.clear()
    with pytest.raises(RpcError) as caught:
        await transcribe(router)
    assert caught.value.code == NOT_CONFIGURED and caught.value.data == {"kind": "no_key"}
    assert fake_client.calls == [] and spend_rows(conn) == []


async def test_the_monthly_limit_reached_is_budget_exceeded(router, fake_client, conn):
    await router.call("settings.set", {"key": "monthly_limit_usd", "value": 0})
    with pytest.raises(RpcError) as caught:
        await transcribe(router)
    assert caught.value.code == BUDGET_EXCEEDED and caught.value.data == {"kind": "budget"}
    assert fake_client.calls == []


@pytest.mark.parametrize("kind", ["network", "timeout", "server", "auth", "credits"])
async def test_a_failed_call_says_its_kind(router, fake_client, conn, kind):
    fake_client.error = OpenRouterError(0, kind, "no")
    with pytest.raises(RpcError) as caught:
        await transcribe(router)
    assert caught.value.code == INTERNAL_ERROR and caught.value.data == {"kind": kind}
    assert spend_rows(conn) == []


async def test_no_answer_in_time_is_a_timeout(router, fake_client, monkeypatch):
    monkeypatch.setattr(cloud_stt, "timeout_for", lambda seconds: 0.05)
    fake_client.delay = 5.0
    with pytest.raises(RpcError) as caught:
        await transcribe(router)
    assert caught.value.data == {"kind": "timeout"}


@pytest.mark.parametrize(
    "params",
    [
        {"audio": 5},
        {"audio": "not base64!"},
        {"audio": base64.b64encode(b"not a wav file").decode()},
        {"hints": "拉斐爾"},
        {"hints": [1]},
        {"language": "xx"},
        {"language": "zh-TW"},
        {"language": 1},
    ],
)
async def test_bad_params_are_invalid_params(router, fake_client, params):
    with pytest.raises(RpcError) as caught:
        await transcribe(router, **params)
    assert caught.value.code == INVALID_PARAMS
    assert fake_client.calls == []


async def test_nothing_of_the_audio_the_text_or_the_key_is_logged(router, fake_client, caplog):
    caplog.set_level(logging.DEBUG)
    audio = wav_b64()
    await transcribe(router, audio=audio, hints=["秘密詞"])
    fake_client.error = OpenRouterError(500, "server", f"boom {KEY}")
    with pytest.raises(RpcError):
        await transcribe(router, audio=audio)
    assert audio not in caplog.text and "你好世界" not in caplog.text
    assert KEY not in caplog.text and "秘密詞" not in caplog.text


# --- with the real client ---------------------------------------------------------------------


def choose_gpt_transcribe(conn) -> None:
    """Make gpt-transcribe the speech model, as a choice from OpenRouter's list would. These tests
    have no list to choose from (test_model_choice.py has the choosing), so the id is saved as the
    store keeps it."""
    SettingsStore(conn).set("stt_cloud_model", "openai/gpt-transcribe")


async def test_end_to_end_on_the_real_client_with_a_mock_transport(conn):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"text": "早安", "usage": {"seconds": 2, "cost": 0.0002}})

    keys = MemoryKeyStore()
    keys.set(KEY)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        router = Router()
        Brain(conn, OpenRouterClient(keys.get, http=http), keys, now_ms=lambda: NOW_MS).register(
            router
        )
        choose_gpt_transcribe(conn)
        result = await transcribe(router, language="zh", hints=["拉斐爾"])
    assert result == {"text": "早安", "model": "openai/gpt-transcribe", "cost_usd": 0.0002}
    body = seen[0]
    assert body["model"] == "openai/gpt-transcribe"
    assert body["provider"]["options"]["openai"]["keywords"] == ["拉斐爾"]
    assert body["provider"]["options"]["openai"]["languages"] == ["zh"]
    assert spend_rows(conn) == [("stt", "openai/gpt-transcribe", 0.0002, 0, 0)]


async def test_gpt_transcribe_gets_a_single_selected_code_when_automatic(conn):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"text": "早安", "usage": {"seconds": 2, "cost": 0.0002}})

    keys = MemoryKeyStore()
    keys.set(KEY)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        router = Router()
        Brain(conn, OpenRouterClient(keys.get, http=http), keys, now_ms=lambda: NOW_MS).register(
            router
        )
        choose_gpt_transcribe(conn)
        await router.call("settings.set", {"key": "languages", "value": ["zh-TW"]})
        await transcribe(router, language="auto")
    assert seen[0]["provider"]["options"]["openai"]["languages"] == ["zh"]
    assert "language" not in seen[0]


async def test_gpt_transcribe_gets_every_chosen_language_when_automatic(conn):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"text": "早安", "usage": {"seconds": 2, "cost": 0.0002}})

    keys = MemoryKeyStore()
    keys.set(KEY)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        router = Router()
        Brain(conn, OpenRouterClient(keys.get, http=http), keys, now_ms=lambda: NOW_MS).register(
            router
        )
        choose_gpt_transcribe(conn)
        await router.call(
            "settings.set", {"key": "languages", "value": ["zh-TW", "en-US", "ja-JP"]}
        )
        await transcribe(router, language="auto")
        await router.call(
            "settings.set", {"key": "stt_cloud_model", "value": "elevenlabs/scribe-v2"}
        )
        await transcribe(router, language="auto")
    assert seen[0]["provider"]["options"]["openai"]["languages"] == ["zh", "en", "ja"]
    assert "language" not in seen[0]
    assert "language" not in seen[1]  # the others detect it themselves
