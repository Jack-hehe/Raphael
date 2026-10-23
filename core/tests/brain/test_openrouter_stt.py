"""Tests for the OpenRouter client's transcription call (cloud speech-to-text).

Every request goes to an httpx.MockTransport, so no test touches the network.
"""

import base64
import json
import logging

import httpx
import pytest

from raphael_core.brain import openrouter
from raphael_core.brain.catalog import STT, parse_catalog
from raphael_core.brain.openrouter import (
    OpenRouterClient,
    OpenRouterError,
    Transcription,
    transcription_body,
)

from .test_catalog import RECORDED

KEY = "sk-or-v1-test"
# Every transcription model of the recorded list of OpenRouter's models, as the catalog reads it:
# the models the settings page offers for speech-to-text.
LISTED_STT_MODELS = [entry.id for entry in parse_catalog(RECORDED) if entry.kind == STT]
AUDIO = base64.b64encode(b"RIFF....WAVEfmt fake").decode("ascii")


@pytest.fixture
async def client_with():
    opened: list[httpx.AsyncClient] = []

    def make(handler, key=KEY):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        opened.append(http)
        return OpenRouterClient(key if callable(key) else (lambda: key), http=http)

    yield make
    for http in opened:
        await http.aclose()


def stt_reply(text="你好世界", cost=0.000375, **usage):
    return httpx.Response(
        200,
        json={
            "text": text,
            "usage": {
                "seconds": 5,
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": cost,
                **usage,
            },
        },
    )


async def transcribe(client, **overrides):
    args = {
        "model": "openai/gpt-transcribe",
        "audio_b64": AUDIO,
        "language": None,
        "hints": [],
        "timeout_s": 5.0,
    }
    return await client.transcribe(**{**args, **overrides})


# --- the request ------------------------------------------------------------------------------


async def test_a_transcription_is_posted_as_json_with_the_audio_in_base64(client_with):
    seen = []

    def handler(request):
        seen.append(request)
        return stt_reply()

    await transcribe(client_with(handler))
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer sk-or-v1-test"
    assert request.headers["x-openrouter-title"] == "Raphael"
    body = json.loads(request.content)
    assert body["model"] == "openai/gpt-transcribe"
    assert body["input_audio"] == {"data": AUDIO, "format": "wav"}
    assert body["provider"]["data_collection"] == "deny"


def test_gpt_transcribe_gets_the_hints_as_keywords_and_the_language_as_languages():
    body = transcription_body(
        "openai/gpt-transcribe", AUDIO, "wav", language="zh", hints=["拉斐爾", "OpenRouter"]
    )
    options = body["provider"]["options"]["openai"]
    assert options["keywords"] == ["拉斐爾", "OpenRouter"]
    assert options["languages"] == ["zh"]
    assert isinstance(options["prompt"], str) and options["prompt"]
    # gpt-transcribe takes `languages` in place of `language`; the two are not to be sent together.
    assert "language" not in body


def test_gpt_transcribe_with_automatic_language_sends_no_language_at_all():
    body = transcription_body("openai/gpt-transcribe", AUDIO, "wav", language=None, hints=[])
    options = body["provider"]["options"]["openai"]
    assert "languages" not in options and "keywords" not in options
    assert "language" not in body


def test_gpt_transcribe_gets_the_languages_to_detect_among():
    body = transcription_body(
        "openai/gpt-transcribe", AUDIO, "wav", language=None, languages=["zh", "en"], hints=[]
    )
    assert body["provider"]["options"]["openai"]["languages"] == ["zh", "en"]
    assert "language" not in body
    # One chosen language wins over the list.
    one = transcription_body(
        "openai/gpt-transcribe", AUDIO, "wav", language="ja", languages=["zh", "en"], hints=[]
    )
    assert one["provider"]["options"]["openai"]["languages"] == ["ja"]


@pytest.mark.parametrize("model", [m for m in LISTED_STT_MODELS if m != "openai/gpt-transcribe"])
def test_the_other_models_ignore_the_list_of_languages(model):
    body = transcription_body(model, AUDIO, "wav", language=None, languages=["zh", "en"], hints=[])
    assert "language" not in body and "languages" not in json.dumps(body)


def test_keywords_a_provider_would_refuse_are_dropped_and_the_list_is_bounded():
    hints = ["  ok  ", "", "a<b", "line\nbreak", "c>d", "car\rriage", "x" * 200]
    hints += [f"w{i}" for i in range(200)]
    body = transcription_body("openai/gpt-transcribe", AUDIO, "wav", language=None, hints=hints)
    keywords = body["provider"]["options"]["openai"]["keywords"]
    assert keywords[0] == "ok"
    assert not any(set(k) & set("<>\r\n") for k in keywords)
    assert all(0 < len(k) <= openrouter.MAX_KEYWORD_CHARS for k in keywords)
    assert len(keywords) == openrouter.MAX_KEYWORDS


def test_whisper_on_groq_gets_the_hints_as_a_prompt_and_a_top_level_language():
    body = transcription_body(
        "openai/whisper-large-v3-turbo", AUDIO, "wav", language="ja", hints=["拉斐爾", "Tauri"]
    )
    assert body["language"] == "ja"
    prompt = body["provider"]["options"]["groq"]["prompt"]
    assert "拉斐爾" in prompt and "Tauri" in prompt


def test_the_models_given_the_dictionary_are_among_the_ones_the_list_offers():
    assert set(openrouter.STT_MODELS_WITH_HINTS) <= set(LISTED_STT_MODELS)
    assert len(LISTED_STT_MODELS) > len(openrouter.STT_MODELS_WITH_HINTS)


@pytest.mark.parametrize("model", [*LISTED_STT_MODELS, "openai/whisper-1"])
def test_the_models_said_to_get_the_dictionary_are_the_ones_that_get_it(model):
    # The settings page tells the models with the dictionary from the others by this list.
    body = transcription_body(model, AUDIO, "wav", language=None, hints=["拉斐爾"])
    hinted = "拉斐爾" in json.dumps(body, ensure_ascii=False)
    assert hinted == (model in openrouter.STT_MODELS_WITH_HINTS)


@pytest.mark.parametrize("model", [m for m in LISTED_STT_MODELS if m.split("/")[0] != "openai"])
def test_other_models_get_the_language_at_the_top_level_and_no_options(model):
    body = transcription_body(model, AUDIO, "wav", language="en", hints=["Raphael"])
    assert body["language"] == "en"
    assert body["provider"] == {"data_collection": "deny"}
    auto = transcription_body(model, AUDIO, "wav", language=None, hints=[])
    assert "language" not in auto


# --- the reply --------------------------------------------------------------------------------


async def test_the_reply_gives_the_text_the_cost_and_the_seconds(client_with):
    result = await transcribe(client_with(lambda r: stt_reply(input_tokens=12, output_tokens=7)))
    assert isinstance(result, Transcription)
    assert result.text == "你好世界"
    assert result.model == "openai/gpt-transcribe"
    assert result.cost_usd == 0.000375
    assert result.seconds == 5
    assert result.input_tokens == 12 and result.output_tokens == 7
    assert result.latency_ms >= 0


async def test_a_reply_without_usage_costs_nothing(client_with):
    result = await transcribe(client_with(lambda r: httpx.Response(200, json={"text": "hi"})))
    assert result.text == "hi" and result.cost_usd == 0.0


async def test_a_reply_without_text_is_a_bad_response(client_with):
    with pytest.raises(OpenRouterError) as caught:
        await transcribe(client_with(lambda r: httpx.Response(200, json={"usage": {}})))
    assert caught.value.kind == "bad_response"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (402, "credits"), (429, "rate_limit"), (400, "bad_request"), (503, "server")],
)
async def test_http_failures_have_their_kind(client_with, status, kind):
    reply = httpx.Response(status, json={"error": {"message": "nope"}})
    with pytest.raises(OpenRouterError) as caught:
        await transcribe(client_with(lambda r: reply))
    assert caught.value.kind == kind


async def test_a_timeout_and_no_connection_have_their_kind(client_with):
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(OpenRouterError) as caught:
        await transcribe(client_with(timeout))
    assert caught.value.kind == "timeout"
    with pytest.raises(OpenRouterError) as caught:
        await transcribe(client_with(offline))
    assert caught.value.kind == "network"


async def test_no_key_is_an_auth_error_before_any_request(client_with):
    seen = []
    with pytest.raises(OpenRouterError) as caught:
        await transcribe(client_with(lambda r: seen.append(r) or stt_reply(), key=None))
    assert caught.value.kind == "auth" and seen == []


async def test_neither_the_key_nor_the_audio_nor_the_text_is_logged(client_with, caplog):
    caplog.set_level(logging.DEBUG)
    reply = httpx.Response(500, json={"error": {"message": "boom"}})
    with pytest.raises(OpenRouterError):
        await transcribe(client_with(lambda r: reply), hints=["秘密詞"])
    await transcribe(client_with(lambda r: stt_reply("聽到的話")))
    logged = caplog.text
    assert KEY not in logged and AUDIO not in logged
    assert "秘密詞" not in logged and "聽到的話" not in logged
