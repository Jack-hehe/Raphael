"""Tests for the device side's cloud speech-to-text engine.

The brain is a fake `call` (what the app wires to `router.call` from the worker thread), the local
engine a fake that says what it heard. No network, no key, no model.
"""

import base64
import concurrent.futures
import io
import logging
import wave

import numpy as np
import pytest

from raphael_core.device import cloud_stt
from raphael_core.device.cloud_stt import CloudSttEngine, encode_wav
from raphael_core.protocol import (
    BUDGET_EXCEEDED,
    INTERNAL_ERROR,
    NO_LOCAL_MODEL,
    NOT_CONFIGURED,
    RpcError,
)

RATE = 16_000


def speech(seconds: float = 1.0) -> np.ndarray:
    t = np.arange(round(seconds * RATE)) / RATE
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


class Brain:
    """A fake `call(method, params, timeout)`: answers with `reply`, or raises `error`."""

    def __init__(self, reply=None, error=None):
        self.reply = (
            reply
            if reply is not None
            else {"text": "雲端聽到的", "model": "openai/gpt-transcribe", "cost_usd": 0.0001}
        )
        self.error = error
        self.calls = []

    def __call__(self, method, params, timeout):
        self.calls.append((method, params, timeout))
        if self.error is not None:
            raise self.error
        return self.reply


class Local:
    name = "qwen3-asr"

    def __init__(self):
        self.calls = []

    def transcribe(self, samples, *, hints=(), language="auto"):
        self.calls.append(
            {"seconds": samples.size / RATE, "hints": list(hints), "language": language}
        )
        return "本機聽到的"


def engine(brain, local=None, warned=None, slept=None):
    local = local or Local()
    slept = [] if slept is None else slept
    return CloudSttEngine(
        brain,
        fallback=lambda: local,
        warned=set() if warned is None else warned,
        sleep=slept.append,
    ), local


class Refusals(Brain):
    """A brain that raises each of `errors` in turn, then answers."""

    def __init__(self, *errors):
        super().__init__()
        self.errors = list(errors)

    def __call__(self, method, params, timeout):
        self.calls.append((method, params, timeout))
        if self.errors:
            raise self.errors.pop(0)
        return self.reply


def no_local_model():
    raise RpcError(NO_LOCAL_MODEL, "There is no local speech model")


# --- the WAV ----------------------------------------------------------------------------------


def test_the_wav_is_16_bit_mono_16_khz_and_round_trips():
    samples = speech(0.5)
    data = encode_wav(samples)
    with wave.open(io.BytesIO(data), "rb") as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, RATE)
        frames = reader.readframes(reader.getnframes())
    back = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    assert back.size == samples.size
    assert np.max(np.abs(back - samples)) < 1 / 16384


def test_samples_beyond_full_scale_are_clipped_not_wrapped():
    data = encode_wav(np.array([2.0, -2.0, 1.0, -1.0], dtype=np.float32))
    with wave.open(io.BytesIO(data), "rb") as reader:
        values = np.frombuffer(reader.readframes(4), dtype="<i2")
    assert list(values) == [32767, -32768, 32767, -32768]


# --- the request ------------------------------------------------------------------------------


def test_a_segment_goes_to_stt_transcribe_as_base64_wav_with_hints_and_language():
    brain = Brain()
    cloud, local = engine(brain)
    samples = speech(2.0)
    text = cloud.transcribe(samples, hints=["拉斐爾"], language="zh")
    assert text == "雲端聽到的"
    assert local.calls == []
    ((method, params, timeout),) = brain.calls
    assert method == "stt.transcribe"
    assert base64.b64decode(params["audio"]) == encode_wav(samples)
    assert params["hints"] == ["拉斐爾"] and params["language"] == "zh"
    assert timeout == pytest.approx(cloud_stt.wait_for(2.0))
    assert cloud.last_used == "openai/gpt-transcribe"


def test_the_wait_covers_the_brains_own_timeout():
    assert cloud_stt.wait_for(2.0) > 7.0  # the brain waits min(30, 5 + s)
    assert cloud_stt.wait_for(100.0) > 30.0


def test_silence_asks_nobody():
    brain = Brain()
    cloud, local = engine(brain)
    assert cloud.transcribe(np.zeros(RATE, dtype=np.float32)) == ""
    assert cloud.transcribe(np.zeros(0, dtype=np.float32)) == ""
    assert brain.calls == [] and local.calls == []


# --- falling back to the local engine ---------------------------------------------------------


FAILURES = [
    (RpcError(NOT_CONFIGURED, "No key", {"kind": "no_key"}), "no_key"),
    (RpcError(BUDGET_EXCEEDED, "Limit", {"kind": "budget"}), "budget"),
    (RpcError(INTERNAL_ERROR, "offline", {"kind": "network"}), "network"),
    (RpcError(INTERNAL_ERROR, "boom"), "error"),
    (concurrent.futures.TimeoutError(), "timeout"),
    (RuntimeError("the loop is closed"), "error"),
]


@pytest.mark.parametrize(("error", "kind"), FAILURES)
def test_any_failure_falls_back_to_the_local_engine(error, kind, caplog):
    cloud, local = engine(Brain(error=error))
    text = cloud.transcribe(speech(1.0), hints=["拉斐爾"], language="ja")
    assert text == "本機聽到的"
    assert local.calls == [{"seconds": 1.0, "hints": ["拉斐爾"], "language": "ja"}]
    assert cloud.last_used == "qwen3-asr"
    assert any(kind in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_a_reply_without_text_falls_back():
    cloud, local = engine(Brain(reply={"model": "x"}))
    assert cloud.transcribe(speech()) == "本機聽到的"
    assert len(local.calls) == 1


def test_after_a_failure_the_rest_of_the_dictation_stays_local():
    brain = Brain(error=RpcError(INTERNAL_ERROR, "slow", {"kind": "timeout"}))
    cloud, local = engine(brain)
    cloud.transcribe(speech())
    cloud.transcribe(speech())
    assert len(brain.calls) == 1 and len(local.calls) == 2


def test_each_kind_of_failure_is_warned_about_once(caplog):
    warned: set[str] = set()
    for _ in range(3):  # three dictations, each with its own engine
        cloud, _ = engine(
            Brain(error=RpcError(NOT_CONFIGURED, "No key", {"kind": "no_key"})), warned=warned
        )
        cloud.transcribe(speech())
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and warned == {"no_key"}


def test_a_long_segment_is_cut_for_the_local_engine():
    cloud, local = engine(Brain(error=RpcError(NOT_CONFIGURED, "No key", {"kind": "no_key"})))
    text = cloud.transcribe(speech(30.0))
    assert [round(c["seconds"], 3) for c in local.calls] == [15.0, 15.0]
    assert text == "本機聽到的本機聽到的"  # Chinese is joined without a space


# --- refusals that pass, and no local engine --------------------------------------------------

RATE_LIMIT = RpcError(INTERNAL_ERROR, "busy", {"kind": "rate_limit"})


@pytest.mark.parametrize("kind", ["rate_limit", "server"])
def test_a_refusal_that_passes_is_tried_again_after_a_wait(kind):
    brain = Refusals(RpcError(INTERNAL_ERROR, "busy", {"kind": kind}))
    slept: list[float] = []
    cloud, local = engine(brain, slept=slept)
    assert cloud.transcribe(speech()) == "雲端聽到的"
    assert len(brain.calls) == 2 and slept == [1.0] and local.calls == []
    assert cloud.transcribe(speech()) == "雲端聽到的"  # the dictation stays in the cloud
    assert len(brain.calls) == 3


def test_a_refusal_that_lasts_falls_back_after_three_tries():
    brain = Refusals(RATE_LIMIT, RATE_LIMIT, RATE_LIMIT)
    slept: list[float] = []
    cloud, local = engine(brain, slept=slept)
    assert cloud.transcribe(speech()) == "本機聽到的"
    assert len(brain.calls) == 3 and slept == [1.0, 2.0] and len(local.calls) == 1


def test_other_failures_are_not_tried_again():
    brain = Refusals(RpcError(NOT_CONFIGURED, "No key", {"kind": "no_key"}))
    slept: list[float] = []
    cloud, _ = engine(brain, slept=slept)
    cloud.transcribe(speech())
    assert len(brain.calls) == 1 and slept == []


def test_without_a_local_engine_the_next_segment_tries_the_cloud_again(caplog):
    brain = Refusals(*[RATE_LIMIT] * 3)
    cloud = CloudSttEngine(brain, fallback=no_local_model, warned=set(), sleep=lambda _: None)
    with pytest.raises(RpcError) as raised:
        cloud.transcribe(speech())
    assert raised.value.code == NO_LOCAL_MODEL
    assert cloud.transcribe(speech()) == "雲端聽到的"
    assert len(brain.calls) == 4
    assert any("no local engine" in r.getMessage() for r in caplog.records)


def test_nothing_of_the_audio_or_the_text_is_logged(caplog):
    caplog.set_level(logging.DEBUG)
    samples = speech()
    cloud, _ = engine(Brain(error=RpcError(INTERNAL_ERROR, "boom", {"kind": "server"})))
    cloud.transcribe(samples, hints=["秘密詞"])
    ok, _ = engine(Brain())
    ok.transcribe(samples)
    audio = base64.b64encode(encode_wav(samples)).decode()
    assert audio[:200] not in caplog.text
    assert "秘密詞" not in caplog.text and "雲端聽到的" not in caplog.text
