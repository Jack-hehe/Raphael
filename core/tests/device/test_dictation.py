"""Tests for the dictation service.

Everything the service touches is a fake: the recorder (a thread that feeds scripted chunks, as
the microphone's callback would), the segmenter (one segment per chunk), the engine (scripted
texts) and the brain (handlers on the router). No test opens a microphone, loads a model or reaches
the network.
"""

import asyncio
import logging
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from raphael_core.device import audio, dictation
from raphael_core.device.audio import NoMicrophoneError
from raphael_core.device.dictation import (
    MAX_RECORDING_SECONDS,
    MIC_STALL_SECONDS,
    SILENT_PEAK,
    DictationService,
    join_segments,
)
from raphael_core.protocol import (
    BUSY,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    NO_MICROPHONE,
    NOT_CONFIGURED,
    NOT_FOUND,
    RpcError,
)
from raphael_core.router import Router

RATE = 16_000
LOGGER = "raphael_core.device.dictation"


def chunk(seconds: float, level: float = 0.1) -> np.ndarray:
    return np.full(round(seconds * RATE), level, dtype=np.float32)


async def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for a condition")
        await asyncio.sleep(0.005)


class FakeClock:
    """Time that only moves when a test moves it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRecorder:
    """A recorder: on start() a thread feeds the scripted chunks through the callback."""

    def __init__(self) -> None:
        self.on_frames = None
        self.error: Exception | None = None
        self.chunks: list[np.ndarray] = []
        self.fail_on_start = False
        self.error_after: int | None = None  # sets .error and stops feeding after this many chunks
        self.tail: np.ndarray | None = None  # delivered by stop(), on the thread that calls it
        self.start_gate: threading.Event | None = None  # start() waits for it
        self.start_entered = threading.Event()
        self.stop_gate: threading.Event | None = None  # the next stop() waits for it (only once)
        self.next_stop_gate: threading.Event | None = None  # ... and the one after that
        self.stop_threads: list[int] = []
        self.stop_entered = threading.Event()
        self.clock: FakeClock | None = None  # advanced by each chunk's length before it is fed
        self.start_count = 0
        self.stop_count = 0
        self.fed_count = 0
        self.start_thread: int | None = None
        self.feed_thread: int | None = None
        self.tail_thread: int | None = None
        self.running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.start_thread = threading.get_ident()
        self.start_entered.set()
        if self.start_gate is not None:
            self.start_gate.wait(10)
        if self.fail_on_start:
            raise NoMicrophoneError("There is no microphone")
        self.start_count += 1
        self.error = None
        self.fed_count = 0
        self.running = True
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._feed, daemon=True)
        self._thread.start()

    def _feed(self) -> None:
        self.feed_thread = threading.get_ident()
        for index, piece in enumerate(list(self.chunks)):
            if self._stop_event.is_set():
                return
            if self.clock is not None:
                self.clock.advance(piece.size / RATE)
            self.on_frames(piece)
            self.fed_count += 1
            if self.error_after is not None and index + 1 >= self.error_after:
                self.error = NoMicrophoneError("The microphone was unplugged")
                return

    def stop(self) -> None:
        self.stop_count += 1
        self.stop_threads.append(threading.get_ident())
        gate, self.stop_gate, self.next_stop_gate = self.stop_gate, self.next_stop_gate, None
        if gate is not None:
            self.stop_entered.set()
            gate.wait(10)
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(10)
        if self.running:
            self.running = False
            if self.tail is not None:
                self.tail_thread = threading.get_ident()
                self.on_frames(self.tail)

    async def fed(self, count: int) -> None:
        await wait_until(lambda: self.fed_count >= count)


class FakeSegmenter:
    """One segment per chunk. `speech_seconds` is the speech of the current dictation only: it
    starts again at the first accept() after a flush(), like the real Segmenter's."""

    def __init__(self) -> None:
        self.emit_nothing = False
        self.speech_seconds = 0.0
        self.accepted_samples = 0
        self.accept_calls = 0
        self.flush_calls = 0
        self.flush_segments: list[np.ndarray] = []
        self.fail_on_accept = False
        self.in_use = 0
        self.overlaps = 0
        self._flushed = False

    def accept(self, frames: np.ndarray) -> list[np.ndarray]:
        self.in_use += 1
        self.overlaps += self.in_use > 1
        try:
            if self.fail_on_accept:
                raise RuntimeError("the detector failed")
            if self._flushed:
                self.speech_seconds = 0.0
                self._flushed = False
            self.accept_calls += 1
            self.accepted_samples += frames.size
            if self.emit_nothing:
                return []
            self.speech_seconds += frames.size / RATE
            return [frames]
        finally:
            self.in_use -= 1

    def flush(self) -> list[np.ndarray]:
        self.flush_calls += 1
        self._flushed = True
        segments, self.flush_segments = self.flush_segments, []
        self.speech_seconds += sum(segment.size for segment in segments) / RATE
        return segments


class FakeEngine:
    name = "fake"

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.calls: list[dict] = []
        self.gate: threading.Event | None = None  # transcribe() waits for it
        self.entered = threading.Event()
        self.fail_on: set[int] = set()

    def transcribe(self, samples, *, hints=(), language="auto"):
        index = len(self.calls)
        self.calls.append({"samples": samples.size, "hints": list(hints), "language": language})
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        if index in self.fail_on:
            raise RuntimeError("the decoder failed")
        return self.texts[index] if index < len(self.texts) else f"text{index}"


class FakeBrain:
    """The handlers the service reaches through the router."""

    def __init__(self, router: Router) -> None:
        self.router = router
        self.finalize_params: list[dict] = []
        self.finalize_calls = 0
        self.normalize_params: list[dict] = []
        self.normalize_error: RpcError | None = None
        self.fail_dictionary = False
        self.entries = [{"id": 1, "word": "拉斐爾", "misheard": [], "source": "manual"}]
        self.on_finalize = None
        self.finalize_gate: asyncio.Event | None = None
        self.finalize_entered = asyncio.Event()
        self.cancelled: list[str] = []  # the sessions the service told the brain to forget
        self.cancel_error: RpcError | None = None
        router.register("brain.finalize_dictation", self._finalize)
        router.register("brain.normalize_transcript", self._normalize)
        router.register("brain.cancel_dictation", self._cancel_dictation)
        router.register("dictionary.list", self._dictionary_list)

    async def _finalize(self, params: dict) -> dict:
        self.finalize_calls += 1
        self.finalize_params.append(params)
        if self.on_finalize is not None:
            self.on_finalize()
        if self.finalize_gate is not None:
            self.finalize_entered.set()
            await self.finalize_gate.wait()
        return {
            "id": 7,
            "text": params["raw_text"],
            "raw_text": params["raw_text"],
            "cleaned": True,
            "fallback_reason": None,
            "llm_ms": 5,
        }

    async def _normalize(self, params: dict) -> dict:
        self.normalize_params.append(params)
        if self.normalize_error is not None:
            raise self.normalize_error
        return {"text": params["raw_text"] + "（正規化）"}

    async def _cancel_dictation(self, params: dict) -> dict:
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancelled.append(params["session_id"])
        return {}

    async def _dictionary_list(self, params: dict) -> dict:
        if self.fail_dictionary:
            raise RpcError(INTERNAL_ERROR, "the dictionary is broken")
        return {"entries": self.entries}


class Rig:
    """A DictationService wired to the fakes, which the tests script through its attributes."""

    def __init__(self, router: Router, **service_kwargs) -> None:
        self.router = router
        self.recorder = FakeRecorder()
        self.segmenter = FakeSegmenter()
        self.engine = FakeEngine()
        self.recorders_made = 0
        self.segmenter_gate: threading.Event | None = None
        self.segmenter_entered = threading.Event()
        self.segmenter_thread: int | None = None
        self.provider_gate: threading.Event | None = None
        self.provider_thread: int | None = None
        self.provider_calls = 0
        self.provider_error: Exception | None = None
        self.clock = service_kwargs.get("clock")
        self.recorder.clock = self.clock if isinstance(self.clock, FakeClock) else None
        self.service = DictationService(
            router,
            recorder_factory=self._make_recorder,
            segmenter_factory=self._make_segmenter,
            engine_provider=self._provide_engine,
            **service_kwargs,
        )
        self.service.register(router)

    def _make_recorder(self, on_frames):
        self.recorders_made += 1
        self.recorder.on_frames = on_frames
        return self.recorder

    def _make_segmenter(self):
        self.segmenter_thread = threading.get_ident()
        self.segmenter_entered.set()
        if self.segmenter_gate is not None:
            self.segmenter_gate.wait(10)
        return self.segmenter

    def _provide_engine(self):
        self.provider_thread = threading.get_ident()
        self.provider_calls += 1
        if self.provider_gate is not None:
            self.provider_gate.wait(10)
        if self.provider_error is not None:
            raise self.provider_error
        return self.engine

    def release_everything(self) -> None:
        for gate in (self.recorder.start_gate, self.segmenter_gate, self.provider_gate):
            if gate is not None:
                gate.set()
        if self.engine.gate is not None:
            self.engine.gate.set()


@pytest.fixture
def router() -> Router:
    return Router()


@pytest.fixture
def brain(router) -> FakeBrain:
    return FakeBrain(router)


@pytest.fixture
def notifications(router) -> list:
    seen: list = []
    router.set_notifier(lambda method, params: seen.append((method, params)))
    return seen


@pytest.fixture
async def make_rig(router, brain):
    rigs: list[Rig] = []

    def make(**kwargs) -> Rig:
        rig = Rig(router, **kwargs)
        rigs.append(rig)
        return rig

    yield make
    for rig in rigs:
        rig.release_everything()
        await rig.service.aclose()


@pytest.fixture
def rig(make_rig) -> Rig:
    return make_rig()


async def start(router, session_id="s1"):
    return await router.call("dictation.start", {"session_id": session_id})


async def stop(router, session_id="s1", target_app=None):
    return await router.call("dictation.stop", {"session_id": session_id, "target_app": target_app})


def our_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("raphael-dictation")]


# --- join_segments -------------------------------------------------------------------------------


def test_join_segments():
    assert join_segments(["明天", "下午開會"]) == "明天下午開會"
    assert join_segments(["hello", "world"]) == "hello world"
    assert join_segments(["用", "OpenRouter"]) == "用 OpenRouter"


def test_join_segments_cjk_boundaries():
    kana_a, kana_b = chr(0x3042), chr(0x30AB)  # hiragana a, katakana ka
    assert join_segments([f"今日は{kana_a}", f"{kana_b}タカナ"]) == f"今日は{kana_a}{kana_b}タカナ"
    # full-width punctuation counts as CJK on either side
    assert join_segments(["你好" + chr(0xFF0C), "世界"]) == "你好" + chr(0xFF0C) + "世界"
    assert join_segments(["你好", chr(0xFF08) + "世界"]) == "你好" + chr(0xFF08) + "世界"
    assert join_segments(["好" + chr(0x3002), "接著"]) == "好" + chr(0x3002) + "接著"
    # CJK on one side only: a space
    assert join_segments(["你好", "world"]) == "你好 world"
    assert join_segments(["hello", "世界"]) == "hello 世界"
    # ASCII punctuation is not CJK
    assert join_segments(["好,", "世界"]) == "好, 世界"
    assert join_segments(["三點", "3"]) == "三點 3"


def test_join_segments_ignores_blank_pieces_and_trims():
    assert join_segments([]) == ""
    assert join_segments(["", "  "]) == ""
    assert join_segments(["  明天 ", "", " 下午\n"]) == "明天下午"
    assert join_segments(["a", " ", "b"]) == "a b"
    assert join_segments(["only"]) == "only"


# --- the main path -------------------------------------------------------------------------------


async def test_stop_joins_segments_and_calls_the_brain(rig, router, brain):
    rig.engine.texts = ["明天", "下午", "開會"]
    rig.recorder.chunks = [chunk(1.0)] * 3
    assert await start(router) == {}
    await rig.recorder.fed(3)
    r = await stop(router, target_app="notepad.exe")
    assert brain.finalize_params[-1]["raw_text"] == "明天下午開會"
    assert brain.finalize_params[-1]["target_app"] == "notepad.exe"
    assert r["text"] == "明天下午開會" and r["raw_text"] == "明天下午開會"
    assert isinstance(r["stt_ms"], int) and r["stt_ms"] >= 0
    assert r["audio_seconds"] == pytest.approx(3.0)
    assert r["id"] == 7 and r["cleaned"] is True and r["llm_ms"] == 5
    assert brain.finalize_params[-1]["audio_seconds"] == pytest.approx(3.0)
    assert brain.finalize_params[-1]["stt_ms"] == r["stt_ms"]
    assert rig.engine.calls[0]["hints"] == ["拉斐爾"]
    assert rig.engine.calls[0]["language"] == "auto"
    assert not rig.service.active


async def test_the_dictionary_words_go_to_the_engine_newest_first(rig, router, brain):
    brain.entries = [
        {"id": 3, "word": "新詞", "misheard": [], "source": "manual"},
        {"id": 2, "word": "OpenRouter", "misheard": [], "source": "manual"},
        {"id": 1, "word": "舊詞", "misheard": [], "source": "manual"},
    ]
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    await stop(router)
    assert rig.engine.calls[0]["hints"] == ["新詞", "OpenRouter", "舊詞"]


async def test_the_words_the_user_chose_come_before_the_learned_ones_in_the_hints(
    rig, router, brain
):
    # An engine takes the first of the hints (20 locally, 50 in the cloud), so the learned words,
    # up to three a dictation, must not push the user's own out of the list.
    brain.entries = [
        {"id": 5, "word": "新學的", "misheard": [], "source": "learned"},
        {"id": 4, "word": "新詞", "misheard": [], "source": "manual"},
        {"id": 3, "word": "舊學的", "misheard": [], "source": "learned"},
        {"id": 2, "word": "OpenRouter", "misheard": [], "source": "suggested"},
        {"id": 1, "word": "舊詞", "misheard": [], "source": "manual"},
    ]
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    await stop(router)
    assert rig.engine.calls[0]["hints"] == ["新詞", "OpenRouter", "舊詞", "新學的", "舊學的"]


async def test_many_learned_words_do_not_push_the_users_names_out_of_the_local_hints(
    rig, router, brain
):
    from raphael_core.device.stt import _hotwords

    names = ["拉斐爾", "OpenRouter", "Tailscale"]
    learned = [{"id": 100 + n, "word": f"學到{n}", "misheard": [], "source": "learned"}
               for n in range(40, 0, -1)]
    chosen = [{"id": n, "word": name, "misheard": [], "source": "manual"}
              for n, name in enumerate(names, 1)]
    brain.entries = [*learned, *chosen]  # the learned are newer: newest first, they come first
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    await stop(router)
    hints = rig.engine.calls[0]["hints"]
    assert hints[:3] == names and len(hints) == 43
    assert set(names) <= set(_hotwords(hints).split(","))


async def test_the_engine_detects_the_language_and_the_setting_is_not_read(rig, router, brain):
    # The `languages` setting only chooses how the text is written, which is the brain's work:
    # recognition is never held to one language, so a dictation asks the brain for its words only.
    asked = []
    call = router.call

    async def recording(method, params=None):
        asked.append(method)
        return await call(method, params)

    router.call = recording
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    await stop(router)
    assert rig.engine.calls[0]["language"] == "auto"
    assert asked == [
        "dictation.start", "dictionary.list", "dictation.stop", "brain.finalize_dictation"
    ]  # fmt: skip


async def test_a_broken_dictionary_does_not_stop_dictation(rig, router, brain, caplog):
    brain.fail_dictionary = True
    rig.recorder.chunks = [chunk(1.0)]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await start(router)
        await rig.recorder.fed(1)
        r = await stop(router)
    assert r["text"] == "text0"
    assert rig.engine.calls[0]["hints"] == []
    assert any("dictionary" in record.getMessage() for record in caplog.records)


async def test_a_missing_brain_method_falls_back_to_no_hints(make_rig, router, brain):
    # a router with no dictionary.list
    bare = Router()
    bare.register("brain.finalize_dictation", brain._finalize)
    rig = Rig(bare)
    rig.recorder.chunks = [chunk(1.0)]
    await bare.call("dictation.start", {"session_id": "s1"})
    await rig.recorder.fed(1)
    r = await bare.call("dictation.stop", {"session_id": "s1", "target_app": None})
    assert r["text"] == "text0"
    assert rig.engine.calls[0]["hints"] == [] and rig.engine.calls[0]["language"] == "auto"
    await rig.service.aclose()


async def test_long_dictation_is_transcribed_while_recording(rig, router):
    rig.recorder.chunks = [chunk(20.0)] * 15  # five minutes
    await start(router)
    await wait_until(lambda: len(rig.engine.calls) >= 14, timeout=5)
    before = len(rig.engine.calls)
    await stop(router, target_app=None)
    assert len(rig.engine.calls) - before <= 1


async def test_audio_is_cut_into_segments_only_by_the_segmenter(rig, router):
    # the service hands every frame to the segmenter and nothing else
    rig.recorder.chunks = [chunk(0.5)] * 4
    await start(router)
    await rig.recorder.fed(4)
    await stop(router)
    assert rig.segmenter.accepted_samples == 4 * RATE // 2
    assert rig.segmenter.flush_calls == 1


async def test_what_the_segmenter_holds_back_is_transcribed_at_stop(rig, router, brain):
    rig.segmenter.emit_nothing = True
    rig.segmenter.flush_segments = [chunk(2.0)]
    rig.engine.texts = ["最後一句"]
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["text"] == "最後一句" and r["audio_seconds"] == pytest.approx(2.0)
    assert len(rig.engine.calls) == 1


# --- silence -------------------------------------------------------------------------------------


async def test_silent_recording_skips_the_brain(rig, router, brain):  # Review Focus 2
    rig.segmenter.emit_nothing = True
    await start(router)
    r = await stop(router)
    assert r["text"] == "" and r["fallback_reason"] == "empty" and brain.finalize_calls == 0
    assert r["id"] is None and r["raw_text"] == "" and r["cleaned"] is False
    assert r["llm_ms"] == 0 and r["audio_seconds"] == 0 and "stt_ms" in r
    assert rig.engine.calls == []


async def test_less_than_a_third_of_a_second_of_speech_skips_the_brain(rig, router, brain):
    rig.recorder.chunks = [chunk(0.29)]
    rig.engine.texts = ["嗯"]
    await start(router)
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["text"] == "" and r["fallback_reason"] == "empty" and brain.finalize_calls == 0
    assert r["audio_seconds"] == pytest.approx(0.29)


async def test_exactly_a_third_of_a_second_is_enough(rig, router, brain):
    rig.recorder.chunks = [chunk(0.3)]
    await start(router)
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["text"] == "text0" and brain.finalize_calls == 1


async def test_text_that_comes_out_empty_skips_the_brain(rig, router, brain):
    rig.engine.texts = ["", "  "]
    rig.recorder.chunks = [chunk(1.0)] * 2
    await start(router)
    await rig.recorder.fed(2)
    r = await stop(router)
    assert r["text"] == "" and r["fallback_reason"] == "empty" and brain.finalize_calls == 0
    assert r["audio_seconds"] == pytest.approx(2.0)


# --- a microphone that picks up no sound ---------------------------------------------------------

# What a muted or sleeping wireless headset delivered: RMS about 1.5e-5, peak 3e-5, for 20 s.
DEAD_MIC = 1.5e-5


def test_the_silence_threshold_is_about_minus_50_dbfs():
    assert SILENT_PEAK == 0.003


async def test_a_dead_microphone_is_reported_silent(rig, router, brain):
    rig.segmenter.emit_nothing = True  # the detector hears no speech in it, as it should not
    rig.recorder.chunks = [chunk(1.0, DEAD_MIC)] * 3
    await start(router)
    await rig.recorder.fed(3)
    r = await stop(router)
    assert r["text"] == "" and r["fallback_reason"] == "empty" and brain.finalize_calls == 0
    assert r["silent"] is True


async def test_near_silence_that_the_engine_turns_into_nothing_is_reported_silent(
    rig, router, brain
):
    # a detector that lets the hiss through to the engine, which finds no words in it
    rig.engine.texts = ["", ""]
    rig.recorder.chunks = [chunk(1.0, DEAD_MIC)] * 2
    await start(router)
    await rig.recorder.fed(2)
    r = await stop(router)
    assert r["text"] == "" and brain.finalize_calls == 0
    assert r["silent"] is True


async def test_sound_that_holds_no_speech_is_not_reported_silent(rig, router, brain):
    rig.segmenter.emit_nothing = True  # the detector finds no speech in a room's noise
    rig.recorder.chunks = [chunk(1.0, 0.05)] * 2
    await start(router)
    await rig.recorder.fed(2)
    r = await stop(router)
    assert r["text"] == "" and r["fallback_reason"] == "empty" and brain.finalize_calls == 0
    assert r["silent"] is False


async def test_sound_whose_text_comes_out_empty_is_not_reported_silent(rig, router, brain):
    rig.engine.texts = ["", "  "]
    rig.recorder.chunks = [chunk(1.0)] * 2
    await start(router)
    await rig.recorder.fed(2)
    r = await stop(router)
    assert r["text"] == "" and brain.finalize_calls == 0
    assert r["silent"] is False


async def test_a_tap_on_the_desk_is_not_reported_silent(rig, router, brain):
    rig.recorder.chunks = [chunk(0.29)]  # under MIN_SPEECH_SECONDS, but there is sound
    rig.engine.texts = ["嗯"]
    await start(router)
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["text"] == "" and r["fallback_reason"] == "empty"
    assert r["silent"] is False


async def test_a_dictation_with_text_is_not_silent(rig, router, brain):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["text"] == "text0" and r["silent"] is False


async def test_text_from_a_quiet_voice_is_not_silent(rig, router, brain):
    # not empty, so never silent, whatever the level (the engine heard words)
    rig.recorder.chunks = [chunk(1.0, DEAD_MIC)]
    await start(router)
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["text"] == "text0" and r["silent"] is False


async def test_the_session_peak_decides_not_the_average(rig, router, brain):
    # two seconds of nothing around one 20 ms block of real sound
    rig.segmenter.emit_nothing = True
    rig.recorder.chunks = [chunk(1.0, DEAD_MIC), chunk(0.02, 0.05), chunk(1.0, DEAD_MIC)]
    await start(router)
    await rig.recorder.fed(3)
    r = await stop(router)
    assert r["text"] == "" and r["silent"] is False


async def test_the_silence_threshold_is_where_it_says(rig, router, brain):
    rig.segmenter.emit_nothing = True
    for level, silent in [(SILENT_PEAK * 0.9, True), (SILENT_PEAK * 1.1, False)]:
        rig.recorder.chunks = [chunk(0.5, level)]
        await start(router)
        await rig.recorder.fed(1)
        r = await stop(router)
        assert r["silent"] is silent, level


async def test_the_peak_is_kept_per_dictation(rig, router, brain):
    rig.segmenter.emit_nothing = True
    rig.recorder.chunks = [chunk(0.5, 0.1)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    assert (await stop(router, "s1"))["silent"] is False
    rig.recorder.chunks = [chunk(0.5, DEAD_MIC)]
    await start(router, "s2")
    await rig.recorder.fed(1)
    assert (await stop(router, "s2"))["silent"] is True


async def test_a_recording_that_got_no_frames_at_all_is_not_called_silent(rig, router, brain):
    # a stop right after the start, before the first block: nothing to judge, so no blame
    rig.segmenter.emit_nothing = True
    await start(router)
    r = await stop(router)
    assert r["text"] == "" and r["fallback_reason"] == "empty"
    assert r["silent"] is False


async def test_the_tail_the_recorder_delivers_at_stop_counts_for_the_peak(rig, router, brain):
    rig.segmenter.emit_nothing = True
    rig.recorder.chunks = [chunk(0.5, DEAD_MIC)]
    rig.recorder.tail = chunk(0.02, 0.05)
    await start(router)
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["silent"] is False


# --- starting and stopping -----------------------------------------------------------------------


async def test_busy_and_unknown_session(rig, router):
    await start(router, "s1")
    with pytest.raises(RpcError) as e:
        await start(router, "s2")
    assert e.value.code == BUSY
    with pytest.raises(RpcError) as e:
        await stop(router, "nope")
    assert e.value.code == NOT_FOUND


async def test_busy_leaves_the_active_session_untouched(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    with pytest.raises(RpcError) as e:
        await start(router, "s2")
    assert e.value.code == BUSY
    assert rig.recorder.stop_count == 0 and rig.service.active
    assert rig.recorders_made == 1
    await rig.recorder.fed(1)
    assert (await stop(router, "s1"))["text"] == "text0"


async def test_start_while_another_is_still_setting_up_is_busy(rig, router):
    rig.recorder.start_gate = threading.Event()
    first = asyncio.create_task(start(router, "s1"))
    await wait_until(rig.recorder.start_entered.is_set)
    with pytest.raises(RpcError) as e:
        await start(router, "s2")
    assert e.value.code == BUSY
    rig.recorder.start_gate.set()
    assert await first == {}
    assert rig.service.active


async def test_a_second_stop_while_the_first_is_working_finds_nothing(rig, router):
    rig.engine.gate = threading.Event()
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    assert rig.engine.entered.wait(5)
    first = asyncio.create_task(stop(router))
    await asyncio.sleep(0.05)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == NOT_FOUND
    assert not first.done()
    rig.engine.gate.set()
    assert (await first)["text"] == "text0"


async def test_a_second_stop_finds_nothing(rig, router):
    await start(router)
    await stop(router)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == NOT_FOUND


async def test_stop_for_an_unknown_session_opens_nothing(rig, router):
    with pytest.raises(RpcError) as e:
        await stop(router, "nope")
    assert e.value.code == NOT_FOUND
    assert rig.recorders_made == 0 and rig.recorder.start_count == 0 and not rig.service.active


async def test_stop_with_the_wrong_id_leaves_the_active_session_alone(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    with pytest.raises(RpcError) as e:
        await stop(router, "s2")
    assert e.value.code == NOT_FOUND
    assert rig.service.active and rig.recorder.stop_count == 0
    await rig.recorder.fed(1)
    assert (await stop(router, "s1"))["text"] == "text0"


async def test_stop_that_arrives_during_setup_waits_for_it(rig, router):
    rig.recorder.start_gate = threading.Event()
    rig.recorder.chunks = [chunk(1.0)]
    starting = asyncio.create_task(start(router, "s1"))
    await wait_until(rig.recorder.start_entered.is_set)
    stopping = asyncio.create_task(stop(router, "s1"))
    await asyncio.sleep(0.05)
    assert not stopping.done()
    rig.recorder.start_gate.set()
    assert await starting == {}
    r = await stopping
    assert r["text"] in ("", "text0")  # the chunk may or may not have been fed before the stop
    assert not rig.service.active and rig.recorder.stop_count >= 1


async def test_stop_after_a_failed_start_finds_nothing_and_leaves_no_microphone_open(rig, router):
    rig.recorder.fail_on_start = True
    with pytest.raises(RpcError):
        await start(router)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == NOT_FOUND
    assert not rig.recorder.running


async def test_no_microphone_leaves_service_idle(rig, router):  # Review Focus 5
    rig.recorder.fail_on_start = True
    with pytest.raises(RpcError) as e:
        await start(router)
    assert e.value.code == NO_MICROPHONE and not rig.service.active
    rig.recorder.fail_on_start = False
    assert await start(router, "s2") == {}


async def test_microphone_lost_mid_recording_keeps_captured_speech(rig, router):
    rig.engine.texts = ["第一句"]
    rig.recorder.chunks = [chunk(1.0)] * 3
    rig.recorder.error_after = 1  # sets .error and stops feeding after one chunk
    await start(router)
    await wait_until(lambda: rig.recorder.error is not None)
    r = await stop(router)
    assert r["text"] == "第一句"


async def test_microphone_lost_with_no_speech_is_no_microphone(rig, router, brain):
    rig.recorder.chunks = [chunk(0.1)]  # too little speech to count
    rig.recorder.error_after = 1
    await start(router)
    await wait_until(lambda: rig.recorder.error is not None)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == NO_MICROPHONE
    assert brain.finalize_calls == 0 and not rig.service.active and rig.recorder.stop_count >= 1


async def test_microphone_lost_before_any_audio_is_no_microphone(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    rig.recorder.error_after = 1
    rig.segmenter.emit_nothing = True  # the one chunk held no speech
    await start(router)
    await wait_until(lambda: rig.recorder.error is not None)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == NO_MICROPHONE


async def test_invalid_params(rig, router):
    for params in ({}, {"session_id": ""}, {"session_id": 5}, {"session_id": None}):
        with pytest.raises(RpcError) as e:
            await router.call("dictation.start", params)
        assert e.value.code == INVALID_PARAMS
        with pytest.raises(RpcError) as e:
            await router.call("dictation.stop", {**params, "target_app": None})
        assert e.value.code == INVALID_PARAMS
    await start(router)
    with pytest.raises(RpcError) as e:
        await router.call("dictation.stop", {"session_id": "s1", "target_app": 5})
    assert e.value.code == INVALID_PARAMS
    assert rig.service.active  # a bad request did not end the session
    assert (await router.call("dictation.stop", {"session_id": "s1"}))["fallback_reason"] == "empty"


async def test_the_service_is_idle_while_the_brain_cleans_the_text(rig, router, brain):
    seen: list[bool] = []
    brain.on_finalize = lambda: seen.append(rig.service.active)
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    await stop(router)
    assert seen == [False]
    assert await start(router, "s2") == {}


async def test_one_worker_thread_per_session_and_it_ends(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    assert len(our_threads()) == 1
    await stop(router)
    await wait_until(lambda: not our_threads())
    await start(router, "s2")
    await router.call("dictation.cancel", {"session_id": "s2"})
    await wait_until(lambda: not our_threads())


# --- levels --------------------------------------------------------------------------------------


async def test_levels_are_throttled(rig, router, notifications):
    rig.recorder.chunks = [chunk(0.01)] * 200  # 2 s of audio delivered as fast as possible
    await start(router)
    await rig.recorder.fed(200)
    await stop(router)
    assert len([n for n in notifications if n[0] == "audio.level"]) <= 2 * 30 + 2


async def test_levels_follow_the_wall_clock(make_rig, router, notifications):
    clock = FakeClock()
    rig = make_rig(clock=clock, stall_seconds=1e6)
    rig.recorder.chunks = [chunk(0.01)] * 200  # the clock moves 10 ms per chunk: 2 s in all
    await start(router)
    await rig.recorder.fed(200)
    await stop(router)
    levels = [n for n in notifications if n[0] == "audio.level"]
    assert len(levels) == 50  # a first one, then one every 40 ms (the first 10 ms step past 33 ms)


async def test_a_level_message_has_the_session_and_a_level_from_zero_to_one(
    rig, router, notifications
):
    rig.recorder.chunks = [chunk(0.5, level=0.1)]
    await start(router, "abc")
    await rig.recorder.fed(1)
    await stop(router, "abc")
    levels = [params for method, params in notifications if method == "audio.level"]
    assert levels, "no level was sent"
    assert levels[0]["session_id"] == "abc"
    assert 0.0 <= levels[0]["level"] <= 1.0
    assert levels[0]["level"] == pytest.approx(audio.rms_level(chunk(0.5, 0.1)))


async def test_levels_do_not_follow_a_stop(make_rig, router, notifications):
    clock = FakeClock()
    rig = make_rig(clock=clock, stall_seconds=1e6)
    rig.recorder.chunks = [chunk(0.5)]
    rig.recorder.tail = chunk(0.5)
    await start(router)
    await rig.recorder.fed(1)
    clock.advance(1.0)  # long enough for the next frame to be due a level
    await stop(router)
    await asyncio.sleep(0.05)
    assert len([n for n in notifications if n[0] == "audio.level"]) == 1  # the tail has none


async def test_levels_keep_coming_while_the_engine_is_busy(make_rig, router, notifications):
    clock = FakeClock()
    rig = make_rig(clock=clock, stall_seconds=1e6)
    rig.engine.gate = threading.Event()  # the first segment's transcription never finishes
    rig.recorder.chunks = [chunk(1.0)] + [chunk(0.05)] * 40
    await start(router)
    await rig.recorder.fed(41)
    assert await asyncio.to_thread(rig.engine.entered.wait, 5)  # the worker is inside the engine
    # about one per 50 ms of the 2 s since the first segment; the loop may not have caught up yet
    await wait_until(lambda: len([n for n in notifications if n[0] == "audio.level"]) > 20)
    rig.engine.gate.set()
    await stop(router)


def test_the_level_rate_must_be_positive():
    for hz in (0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            DictationService(
                Router(),
                recorder_factory=lambda cb: None,
                segmenter_factory=lambda: None,
                engine_provider=lambda: None,
                level_hz=hz,
            )


# --- progress after the stop ---------------------------------------------------------------------


def progress(notifications) -> list[tuple[str, int, int]]:
    """The `dictation.progress` notifications as (stage, done, total), in order."""
    return [
        (params["stage"], params["done"], params["total"])
        for method, params in notifications
        if method == "dictation.progress"
    ]


async def test_nothing_is_reported_as_progress_while_recording(rig, router, notifications):
    rig.recorder.chunks = [chunk(1.0)] * 3
    await start(router)
    await rig.recorder.fed(3)
    await wait_until(lambda: len(rig.engine.calls) == 3)
    await asyncio.sleep(0.02)
    assert progress(notifications) == []
    await stop(router)


async def test_progress_counts_the_segments_left_after_the_stop_then_the_cleanup(
    rig, router, brain, notifications
):
    # Three segments were transcribed while recording; the segmenter holds back a fourth.
    rig.recorder.chunks = [chunk(1.0)] * 3
    rig.segmenter.flush_segments = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(3)
    await wait_until(lambda: len(rig.engine.calls) == 3)
    seen_at_finalize: list = []
    brain.on_finalize = lambda: seen_at_finalize.extend(progress(notifications))
    await stop(router)
    # At the stop the last stretch is not cut yet: one more segment is counted for it.
    assert progress(notifications) == [
        ("transcribing", 3, 4),
        ("transcribing", 4, 4),
        ("cleaning", 4, 4),
    ]
    # "cleaning" is sent before the brain is asked, not after.
    assert seen_at_finalize[-1] == ("cleaning", 4, 4)


async def test_progress_jumps_to_cleaning_when_everything_was_transcribed_while_recording(
    rig, router, notifications
):
    rig.recorder.chunks = [chunk(1.0)] * 2
    await start(router)
    await rig.recorder.fed(2)
    await wait_until(lambda: len(rig.engine.calls) == 2)
    await stop(router)
    assert progress(notifications) == [
        ("transcribing", 2, 3),  # the guess for a last stretch, until the segmenter is flushed
        ("transcribing", 2, 2),
        ("cleaning", 2, 2),
    ]


async def test_progress_follows_segments_that_are_transcribed_after_the_stop(
    rig, router, notifications
):
    rig.engine.gate = threading.Event()
    rig.recorder.chunks = [chunk(1.0)] * 3
    await start(router)
    await rig.recorder.fed(3)
    assert await asyncio.to_thread(rig.engine.entered.wait, 5)  # inside the first segment
    stopping = asyncio.ensure_future(stop(router))
    await wait_until(lambda: progress(notifications) != [])
    assert progress(notifications) == [("transcribing", 0, 2)]
    rig.engine.gate.set()
    await stopping
    steps = progress(notifications)
    assert steps == [
        ("transcribing", 0, 2),
        ("transcribing", 1, 2),
        ("transcribing", 2, 3),
        ("transcribing", 3, 4),
        ("transcribing", 3, 3),
        ("cleaning", 3, 3),
    ]
    fractions = [done / total for _, done, total in steps]
    assert fractions == sorted(fractions)


async def test_a_progress_message_has_the_session_the_counts_and_the_audio_length(
    rig, router, notifications
):
    rig.recorder.chunks = [chunk(1.5)]
    await start(router, "abc")
    await rig.recorder.fed(1)
    await wait_until(lambda: len(rig.engine.calls) == 1)
    await stop(router, "abc")
    messages = [params for method, params in notifications if method == "dictation.progress"]
    keys = {"session_id", "stage", "done", "total", "audio_seconds"}
    assert all(set(m) == keys for m in messages)
    assert all(m["session_id"] == "abc" for m in messages)
    assert all(isinstance(m["done"], int) and isinstance(m["total"], int) for m in messages)
    assert messages[0]["audio_seconds"] == pytest.approx(1.5)  # what was recorded
    assert messages[-1]["stage"] == "cleaning"
    assert messages[-1]["audio_seconds"] == pytest.approx(1.5)  # the speech the cleanup gets


async def test_a_dictation_with_nothing_to_clean_reports_no_cleaning(
    rig, router, brain, notifications
):
    rig.segmenter.emit_nothing = True
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["text"] == "" and brain.finalize_calls == 0
    assert progress(notifications) == [("transcribing", 0, 1), ("transcribing", 0, 0)]


async def test_a_failed_stop_reports_no_cleaning(rig, router, notifications):
    rig.provider_error = FileNotFoundError("model.onnx")
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    with pytest.raises(RpcError):
        await stop(router)
    assert all(stage == "transcribing" for stage, _, _ in progress(notifications))


async def test_a_cancelled_dictation_reports_no_progress(rig, router, brain, notifications):
    rig.engine.gate = threading.Event()
    rig.recorder.chunks = [chunk(1.0)] * 3
    await start(router)
    await rig.recorder.fed(3)
    assert await asyncio.to_thread(rig.engine.entered.wait, 5)
    await router.call("dictation.cancel", {"session_id": "s1"})
    rig.engine.gate.set()
    await wait_until(lambda: not our_threads())
    await asyncio.sleep(0.02)
    assert progress(notifications) == []


# --- cancel --------------------------------------------------------------------------------------


async def test_cancel_discards_and_allows_restart(rig, router, brain):
    await start(router, "s1")
    assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
    assert brain.finalize_calls == 0
    assert rig.recorder.stop_count == 1 and not rig.service.active
    await start(router, "s2")


async def test_cancel_throws_away_pending_transcriptions(rig, router, brain):
    rig.engine.gate = threading.Event()
    rig.engine.texts = ["舊的"]
    rig.recorder.chunks = [chunk(1.0)] * 3
    await start(router, "s1")
    await rig.recorder.fed(3)
    assert rig.engine.entered.wait(5)  # one segment is being transcribed, two are waiting
    await router.call("dictation.cancel", {"session_id": "s1"})
    rig.engine.gate.set()
    await wait_until(lambda: not our_threads())
    assert len(rig.engine.calls) == 1  # the other two were never transcribed
    assert brain.finalize_calls == 0
    # the next dictation does not inherit any of it
    rig.engine.texts = ["舊的", "新的"]
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s2")
    await rig.recorder.fed(1)
    r = await stop(router, "s2")
    assert r["text"] == "新的" and r["audio_seconds"] == pytest.approx(1.0)


async def test_cancel_with_another_id_leaves_the_active_session_alone(rig, router):
    await start(router, "s1")
    assert await router.call("dictation.cancel", {"session_id": "other"}) == {}
    assert await router.call("dictation.cancel", {"session_id": "other"}) == {}
    assert rig.service.active and rig.recorder.stop_count == 0


async def test_a_late_cancel_during_cleanup_preserves_the_new_session(rig, router, brain):
    rig.engine.texts = ["first", "second"]
    rig.recorder.chunks = [chunk(1.0)]
    gate = brain.finalize_gate = asyncio.Event()
    await start(router, "s1")
    await rig.recorder.fed(1)
    first_stop = asyncio.create_task(stop(router, "s1"))
    try:
        await asyncio.wait_for(brain.finalize_entered.wait(), 5)
        brain.finalize_gate = None
        await start(router, "s2")
        await rig.recorder.fed(1)
        assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
        assert rig.recorder.running
        assert (await stop(router, "s2"))["text"] == "second"
        assert not first_stop.done()
    finally:
        gate.set()
    # The cancel was for s1, whose text was being cleaned: that one is dropped, s2 is not.
    with pytest.raises(RpcError) as dropped:
        await first_stop
    assert dropped.value.code == NOT_FOUND
    assert brain.cancelled == ["s1"]


# --- dictation.cancel while the brain cleans the text ------------------------------------------
# The service is idle by then, but the request to the model is made and paid for. The brain is told,
# so that it does not keep the text (it would be the latest dictation, the one "paste last" pastes).


async def test_the_brain_is_told_which_session_a_text_belongs_to(rig, router, brain):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    await stop(router, "s1")
    assert brain.finalize_params[-1]["session_id"] == "s1"


async def test_a_cancel_while_the_text_is_cleaned_tells_the_brain_and_drops_the_result(
    rig, router, brain
):
    gate = brain.finalize_gate = asyncio.Event()
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    stopping = asyncio.create_task(stop(router, "s1"))
    try:
        await asyncio.wait_for(brain.finalize_entered.wait(), 5)
        assert not rig.service.active, "idle: a new dictation may start while the text is cleaned"
        assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
        assert brain.cancelled == ["s1"]
    finally:
        gate.set()
    with pytest.raises(RpcError) as dropped:
        await stopping
    assert dropped.value.code == NOT_FOUND and "cancelled" in str(dropped.value)


async def test_a_cancel_that_comes_twice_tells_the_brain_twice_without_harm(rig, router, brain):
    gate = brain.finalize_gate = asyncio.Event()
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    stopping = asyncio.create_task(stop(router, "s1"))
    try:
        await asyncio.wait_for(brain.finalize_entered.wait(), 5)
        assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
        assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
    finally:
        gate.set()
    with pytest.raises(RpcError):
        await stopping


async def test_a_cancel_after_the_text_came_back_does_not_trouble_the_brain(rig, router, brain):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    assert (await stop(router, "s1"))["text"] == "text0"
    assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
    assert brain.cancelled == []


async def test_a_recovery_cancel_leaves_the_text_that_is_being_cleaned_alone(rig, router, brain):
    gate = brain.finalize_gate = asyncio.Event()
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    stopping = asyncio.create_task(stop(router, "s1"))
    try:
        await asyncio.wait_for(brain.finalize_entered.wait(), 5)
        assert await router.call("dictation.cancel", {}) == {}
    finally:
        gate.set()
    assert (await stopping)["text"] == "text0"
    assert brain.cancelled == []


async def test_a_brain_that_cannot_be_told_still_has_the_result_dropped(rig, router, brain, caplog):
    brain.cancel_error = RpcError(INTERNAL_ERROR, "no")
    gate = brain.finalize_gate = asyncio.Event()
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    stopping = asyncio.create_task(stop(router, "s1"))
    try:
        await asyncio.wait_for(brain.finalize_entered.wait(), 5)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
    finally:
        gate.set()
    with pytest.raises(RpcError) as dropped:
        await stopping
    assert dropped.value.code == NOT_FOUND
    assert any("brain" in record.getMessage() for record in caplog.records)
    assert "text0" not in caplog.text


async def test_the_session_that_is_being_cleaned_can_be_cancelled_once_a_new_one_has_begun(
    rig, router, brain
):
    rig.engine.texts = ["first", "second"]
    rig.recorder.chunks = [chunk(1.0)]
    gate = brain.finalize_gate = asyncio.Event()
    await start(router, "s1")
    await rig.recorder.fed(1)
    first_stop = asyncio.create_task(stop(router, "s1"))
    try:
        await asyncio.wait_for(brain.finalize_entered.wait(), 5)
        brain.finalize_gate = None
        await start(router, "s2")
        await rig.recorder.fed(1)
        # s2 is the active one: a cancel naming s1 reaches s1 only, one naming s2 reaches s2 only.
        assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
        assert rig.service.active and brain.cancelled == ["s1"]
        assert await router.call("dictation.cancel", {"session_id": "s2"}) == {}
        assert not rig.service.active and brain.cancelled == ["s1"]
    finally:
        gate.set()
    with pytest.raises(RpcError):
        await first_stop


async def test_cancel_without_an_id_cancels_the_active_session(rig, router):
    await start(router, "s1")
    assert await router.call("dictation.cancel", {}) == {}
    assert not rig.service.active and rig.recorder.stop_count == 1
    await start(router, "s2")
    assert await router.call("dictation.cancel", {"session_id": None}) == {}
    assert not rig.service.active


async def test_cancel_with_nothing_active_is_fine(rig, router):
    assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
    assert await router.call("dictation.cancel", {}) == {}
    assert rig.recorders_made == 0


@pytest.mark.parametrize("session_id", ["", 5, False, [], {}])
async def test_cancel_rejects_invalid_ids_without_ending_the_session(rig, router, session_id):
    params = {"session_id": session_id}
    with pytest.raises(RpcError) as error:
        await router.call("dictation.cancel", params)
    assert error.value.code == INVALID_PARAMS
    assert rig.recorders_made == 0
    await start(router)
    with pytest.raises(RpcError) as error:
        await router.call("dictation.cancel", params)
    assert error.value.code == INVALID_PARAMS
    assert rig.service.active and rig.recorder.stop_count == 0


async def test_cancel_while_start_is_setting_up_the_microphone(rig, router):
    rig.recorder.start_gate = threading.Event()
    starting = asyncio.create_task(start(router, "s1"))
    await wait_until(rig.recorder.start_entered.is_set)
    cancelling = asyncio.create_task(router.call("dictation.cancel", {"session_id": "s1"}))
    await asyncio.sleep(0.05)
    assert not cancelling.done()  # it answers once the microphone is closed
    rig.recorder.start_gate.set()
    assert await cancelling == {}
    with pytest.raises(RpcError) as e:
        await starting
    assert e.value.code == NOT_FOUND
    assert rig.recorder.stop_count >= 1 and not rig.recorder.running
    assert not rig.service.active and not our_threads()
    rig.recorder.start_gate = None
    assert await start(router, "s2") == {}  # and the service works again


async def test_cancel_while_start_is_building_the_segmenter(rig, router):
    rig.segmenter_gate = threading.Event()
    starting = asyncio.create_task(start(router, "s1"))
    await wait_until(rig.segmenter_entered.is_set)
    cancelling = asyncio.create_task(router.call("dictation.cancel", {"session_id": "s1"}))
    await asyncio.sleep(0.05)
    rig.segmenter_gate.set()
    assert await cancelling == {}
    with pytest.raises(RpcError) as e:
        await starting
    assert e.value.code == NOT_FOUND
    assert rig.recorder.start_count == 0  # the microphone was never opened
    assert not rig.service.active


async def test_cancel_while_stop_is_waiting_for_the_engine(rig, router, brain):
    rig.engine.gate = threading.Event()
    rig.segmenter.flush_segments = [chunk(1.0)]  # what the stop would still have transcribed
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    assert rig.engine.entered.wait(5)
    stopping = asyncio.create_task(stop(router, "s1"))
    await asyncio.sleep(0.05)
    assert not stopping.done()
    assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}  # does not wait
    assert not rig.service.active
    rig.engine.gate.set()
    with pytest.raises(RpcError) as e:
        await stopping
    assert e.value.code == NOT_FOUND
    assert brain.finalize_calls == 0
    assert len(rig.engine.calls) == 1  # the rest was thrown away


async def test_a_new_session_waits_for_a_cancelled_one_that_shares_its_segmenter(rig, router):
    rig.engine.gate = threading.Event()
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    assert rig.engine.entered.wait(5)  # the first session's worker is inside the engine
    await router.call("dictation.cancel", {"session_id": "s1"})
    restarting = asyncio.create_task(start(router, "s2"))  # the factory hands back the same one
    await asyncio.sleep(0.1)
    assert not restarting.done()  # two threads must not use one segmenter at once
    rig.engine.gate.set()
    assert await restarting == {}
    await rig.recorder.fed(1)
    await stop(router, "s2")
    assert rig.segmenter.overlaps == 0


async def test_aclose_cancels_an_active_session(rig, router):
    await start(router)
    await rig.service.aclose()
    assert not rig.service.active and rig.recorder.stop_count == 1
    await rig.service.aclose()  # and again, with nothing active


# --- the recorder's thread -----------------------------------------------------------------------


async def test_the_last_frames_from_recorder_stop_are_kept(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    rig.recorder.tail = chunk(1.0)
    rig.engine.texts = ["前面", "尾巴"]
    await start(router)
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["text"] == "前面尾巴" and r["audio_seconds"] == pytest.approx(2.0)
    assert (
        rig.recorder.tail_thread != rig.recorder.feed_thread
    )  # a second thread, as in MicRecorder
    assert rig.segmenter.accepted_samples == 2 * RATE


async def test_frames_from_two_threads_are_all_counted(rig, router):
    await start(router)
    on_frames = rig.recorder.on_frames
    piece = chunk(0.01)

    def hammer():
        for _ in range(200):
            on_frames(piece)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    await stop(router)
    assert rig.segmenter.accepted_samples == 4 * 200 * piece.size


async def test_frames_after_stop_are_dropped(rig, router):
    await start(router)
    session = rig.service._session
    on_frames = rig.recorder.on_frames
    await stop(router)
    await wait_until(lambda: not our_threads())
    on_frames(chunk(1.0))  # a recorder that delivers late
    assert session.queue.qsize() == 0 and rig.segmenter.accepted_samples == 0


async def test_a_start_that_is_itself_cancelled_closes_the_microphone(rig, router):
    rig.recorder.start_gate = threading.Event()
    starting = asyncio.create_task(start(router))
    await wait_until(rig.recorder.start_entered.is_set)
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert not rig.service.active
    rig.recorder.start_gate.set()  # the microphone opens after all ...
    await wait_until(lambda: rig.recorder.stop_count >= 1)  # ... and is closed again
    assert not rig.recorder.running and not our_threads()


async def test_a_stop_that_is_cancelled_while_closing_the_microphone_closes_it_itself(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    gate = rig.recorder.stop_gate = threading.Event()  # the first close gets stuck
    stopping = asyncio.create_task(stop(router))
    await wait_until(rig.recorder.stop_entered.is_set)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert not rig.service.active
    await wait_until(lambda: rig.recorder.stop_count == 2)  # it closed it by itself ...
    await wait_until(lambda: not rig.recorder.running)  # ... on a thread
    gate.set()
    await wait_until(lambda: not our_threads())


async def test_a_cancelled_stop_does_not_wait_for_the_microphone_to_close(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    first = rig.recorder.stop_gate = threading.Event()  # the close in progress gets stuck
    second = rig.recorder.next_stop_gate = threading.Event()  # and so would the one that follows
    loop_thread = threading.get_ident()
    stopping = asyncio.create_task(stop(router))
    await wait_until(rig.recorder.stop_entered.is_set)
    began = time.monotonic()
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert time.monotonic() - began < 2  # the loop was not held up by the stuck close
    assert not rig.service.active
    await wait_until(lambda: rig.recorder.stop_count == 2)
    assert loop_thread not in rig.recorder.stop_threads  # no close ran on the loop thread
    first.set()
    second.set()
    await wait_until(lambda: not our_threads())


async def test_a_stop_that_is_itself_cancelled_ends_the_session(rig, router, brain):
    rig.engine.gate = threading.Event()
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    assert rig.engine.entered.wait(5)
    stopping = asyncio.create_task(stop(router))
    await asyncio.sleep(0.05)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert not rig.service.active and not rig.recorder.running
    rig.engine.gate.set()
    await wait_until(lambda: not our_threads())
    assert brain.finalize_calls == 0
    assert await start(router, "s2") == {}


# --- threads -------------------------------------------------------------------------------------


async def test_blocking_calls_run_off_the_loop_thread(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    loop_thread = threading.get_ident()
    await start(router)
    await rig.recorder.fed(1)
    await stop(router)
    assert rig.segmenter_thread not in (None, loop_thread)
    assert rig.recorder.start_thread not in (None, loop_thread)
    assert rig.provider_thread not in (None, loop_thread)


async def test_a_slow_engine_load_does_not_hold_up_the_recording(rig, router):
    rig.provider_gate = threading.Event()  # the engine is still loading
    rig.recorder.chunks = [chunk(1.0)] * 2
    await asyncio.wait_for(start(router), 2)  # start does not wait for it
    await rig.recorder.fed(2)
    assert rig.engine.calls == []
    stopping = asyncio.create_task(stop(router))
    await asyncio.sleep(0.05)
    assert not stopping.done()  # the text needs the engine
    rig.provider_gate.set()
    r = await stopping
    assert r["text"] == "text0 text1"
    assert r["audio_seconds"] == pytest.approx(2.0)
    assert rig.provider_calls == 1


async def test_the_engine_is_asked_for_once_per_session(rig, router):
    rig.recorder.chunks = [chunk(1.0)] * 3
    await start(router)
    await rig.recorder.fed(3)
    await stop(router)
    assert rig.provider_calls == 1


def test_importing_the_service_does_not_load_sherpa_onnx_or_portaudio():
    code = (
        "import sys; import raphael_core.device.dictation; "
        "bad = [m for m in ('sherpa_onnx', 'sounddevice') if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --- engine trouble ------------------------------------------------------------------------------


async def test_a_missing_model_is_not_configured(rig, router, brain):
    rig.provider_error = FileNotFoundError("qwen3-asr is not downloaded")
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == NOT_CONFIGURED
    assert brain.finalize_calls == 0 and not rig.service.active and rig.recorder.stop_count >= 1
    rig.provider_error = None
    assert await start(router, "s2") == {}  # the service is usable again


async def test_an_engine_that_cannot_be_made_is_an_internal_error(rig, router):
    rig.provider_error = RuntimeError("out of memory")
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == INTERNAL_ERROR and "out of memory" in e.value.message
    assert not rig.service.active


async def test_a_segment_that_fails_to_decode_is_skipped(rig, router, caplog):
    rig.engine.fail_on = {1}
    rig.engine.texts = ["甲", "乙", "丙"]
    rig.recorder.chunks = [chunk(1.0)] * 3
    await start(router)
    await rig.recorder.fed(3)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        r = await stop(router)
    assert r["text"] == "甲丙"
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


async def test_when_every_segment_fails_the_error_comes_out(rig, router):
    rig.engine.fail_on = {0, 1}
    rig.recorder.chunks = [chunk(1.0)] * 2
    await start(router)
    await rig.recorder.fed(2)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == INTERNAL_ERROR
    assert not rig.service.active


async def test_a_segmenter_that_fails_ends_the_recording_with_an_error(rig, router):
    rig.segmenter.fail_on_accept = True
    rig.recorder.chunks = [chunk(1.0)] * 2
    await start(router)
    await rig.recorder.fed(2)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == INTERNAL_ERROR
    assert rig.segmenter.flush_calls == 1  # and it was reset for the next time


# --- the segmenter is the user's, session after session ------------------------------------------


async def test_audio_seconds_is_the_speech_of_this_dictation_only(rig, router):
    rig.recorder.chunks = [chunk(2.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    first = await stop(router, "s1")
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s2")
    await rig.recorder.fed(1)
    second = await stop(router, "s2")
    assert first["audio_seconds"] == pytest.approx(2.0)
    assert second["audio_seconds"] == pytest.approx(1.0)  # no baseline to take away


async def test_a_recording_without_audio_does_not_inherit_the_last_speech(rig, router, brain):
    rig.recorder.chunks = [chunk(2.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    await stop(router, "s1")
    # nothing is accepted in the second session, so the segmenter still says 2 s
    rig.recorder.chunks = []
    await start(router, "s2")
    r = await stop(router, "s2")
    assert r["fallback_reason"] == "empty" and r["audio_seconds"] == 0
    assert brain.finalize_calls == 1


async def test_a_cancelled_session_leaves_the_segmenter_flushed(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router, "s1")
    await rig.recorder.fed(1)
    await router.call("dictation.cancel", {"session_id": "s1"})
    await wait_until(lambda: not our_threads())
    assert rig.segmenter.flush_calls == 1


# --- the 300 s cap -------------------------------------------------------------------------------


def test_the_cap_is_five_minutes():
    assert MAX_RECORDING_SECONDS == 300
    assert dictation.SAMPLE_RATE == audio.SAMPLE_RATE


async def test_a_recording_is_capped_at_five_minutes(rig, router, caplog):
    rig.recorder.chunks = [chunk(100.5, 0.0)] * 3  # 301.5 s
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await start(router)
        await rig.recorder.fed(3)
        r = await stop(router)
    assert rig.segmenter.accepted_samples == 300 * RATE
    assert [call["samples"] for call in rig.engine.calls] == [
        round(100.5 * RATE),
        round(100.5 * RATE),
        300 * RATE - 2 * round(100.5 * RATE),
    ]
    assert r["audio_seconds"] == pytest.approx(300.0)
    assert r["text"]  # what was kept is still transcribed
    limit = [record for record in caplog.records if "limit" in record.getMessage()]
    assert len(limit) == 1 and limit[0].levelno == logging.WARNING


async def test_a_recording_that_ends_exactly_at_the_cap_loses_nothing(rig, router, caplog):
    rig.recorder.chunks = [chunk(100.0, 0.0)] * 3
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await start(router)
        await rig.recorder.fed(3)
        r = await stop(router)
    assert rig.segmenter.accepted_samples == 300 * RATE
    assert r["audio_seconds"] == pytest.approx(300.0)
    assert not [record for record in caplog.records if "limit" in record.getMessage()]


async def test_after_the_cap_the_recording_goes_on_but_nothing_more_is_kept(
    rig, router, monkeypatch, caplog
):
    monkeypatch.setattr(dictation, "MAX_RECORDING_SECONDS", 5)
    rig.engine.texts = ["一", "二", "三", "四", "五", "六", "七", "八"]
    rig.recorder.chunks = [chunk(1.0)] * 8
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await start(router)
        await rig.recorder.fed(8)
        assert rig.service.active  # still recording: the stop decides when it ends
        r = await stop(router)
    assert r["text"] == "一二三四五" and r["audio_seconds"] == pytest.approx(5.0)
    assert len(rig.engine.calls) == 5
    assert sum("limit" in record.getMessage() for record in caplog.records) == 1


async def test_a_stop_after_the_cap_is_not_slowed_by_the_queue(rig, router, monkeypatch):
    monkeypatch.setattr(dictation, "MAX_RECORDING_SECONDS", 2)
    rig.recorder.chunks = [chunk(1.0)] * 50
    await start(router)
    await rig.recorder.fed(50)
    r = await stop(router)
    assert r["audio_seconds"] == pytest.approx(2.0)


# --- the microphone watchdog ---------------------------------------------------------------------


def test_the_default_stall_is_two_seconds():
    assert MIC_STALL_SECONDS == 2.0
    service = DictationService(
        Router(),
        recorder_factory=lambda cb: None,
        segmenter_factory=lambda: None,
        engine_provider=lambda: None,
    )
    assert service.stall_seconds == MIC_STALL_SECONDS


async def test_frames_that_stop_arriving_end_the_recording(make_rig, router, caplog):
    clock = FakeClock()
    rig = make_rig(clock=clock)
    rig.engine.texts = ["第一句"]
    rig.recorder.chunks = [chunk(1.0)]  # then the microphone goes quiet without a word
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await start(router)
        await rig.recorder.fed(1)
        clock.advance(MIC_STALL_SECONDS - 0.1)
        await asyncio.sleep(0.4)  # a few checks
        assert rig.recorder.stop_count == 0  # not yet
        clock.advance(0.2)
        await wait_until(lambda: rig.recorder.stop_count >= 1)
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert rig.service.active  # the session lives on until the stop, as after a recorder error
    r = await stop(router)
    assert r["text"] == "第一句"
    assert not rig.service.active


async def test_a_stalled_microphone_with_no_speech_is_no_microphone(make_rig, router, brain):
    clock = FakeClock()
    rig = make_rig(clock=clock)
    await start(router)  # not a single frame arrives
    clock.advance(MIC_STALL_SECONDS + 0.1)
    await wait_until(lambda: rig.recorder.stop_count >= 1)
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == NO_MICROPHONE
    assert brain.finalize_calls == 0 and not rig.service.active


async def test_the_stall_is_measured_from_the_last_frame(make_rig, router):
    clock = FakeClock()
    rig = make_rig(clock=clock)
    await start(router)
    on_frames = rig.recorder.on_frames
    for _ in range(4):
        clock.advance(MIC_STALL_SECONDS - 0.2)
        on_frames(chunk(0.01))
        await asyncio.sleep(0.3)  # the watchdog looks
    assert rig.recorder.stop_count == 0 and rig.service.active


async def test_a_stop_waits_for_the_watchdog_to_finish_closing_the_microphone(make_rig, router):
    clock = FakeClock()
    rig = make_rig(clock=clock)
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    gate = rig.recorder.stop_gate = threading.Event()  # the watchdog's close gets stuck
    clock.advance(MIC_STALL_SECONDS + 0.5)
    await wait_until(rig.recorder.stop_entered.is_set)
    stopping = asyncio.create_task(stop(router))
    await asyncio.sleep(0.1)
    assert not stopping.done()  # it does not close the microphone a second time under the first
    assert rig.recorder.stop_count == 1
    gate.set()
    assert (await stopping)["text"] == "text0"
    assert rig.recorder.stop_count == 1


async def test_a_short_stall_time_works_with_the_real_clock(make_rig, router):
    rig = make_rig(stall_seconds=0.2)
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await wait_until(lambda: rig.recorder.stop_count >= 1, timeout=3)
    r = await stop(router)
    assert r["text"] == "text0"


async def test_the_watchdog_ends_with_the_session(make_rig, router):
    clock = FakeClock()
    rig = make_rig(clock=clock)
    rig.recorder.chunks = [chunk(1.0)]
    tasks = len(asyncio.all_tasks())
    await start(router)
    await rig.recorder.fed(1)
    await stop(router)
    assert len(asyncio.all_tasks()) == tasks
    assert rig.recorder.stop_count == 1
    clock.advance(10 * MIC_STALL_SECONDS)
    await asyncio.sleep(0.4)
    assert rig.recorder.stop_count == 1  # nothing looked at the clock again


async def test_the_watchdog_ends_when_the_session_is_cancelled(make_rig, router):
    clock = FakeClock()
    rig = make_rig(clock=clock)
    tasks = len(asyncio.all_tasks())
    await start(router)
    assert len(asyncio.all_tasks()) == tasks + 1  # the watchdog
    await router.call("dictation.cancel", {"session_id": "s1"})
    assert len(asyncio.all_tasks()) == tasks
    await start(router, "s2")  # a new session, with a fresh timer
    clock.advance(MIC_STALL_SECONDS - 0.5)
    await asyncio.sleep(0.4)
    assert rig.service.active and rig.recorder.stop_count == 1  # only the cancel's


async def test_the_tail_of_a_watchdog_close_is_kept_once(make_rig, router):
    clock = FakeClock()
    rig = make_rig(clock=clock)
    rig.recorder.chunks = [chunk(1.0)]
    rig.recorder.tail = chunk(1.0)
    rig.engine.texts = ["甲", "乙"]
    await start(router)
    await rig.recorder.fed(1)
    clock.advance(MIC_STALL_SECONDS + 1)
    await wait_until(lambda: rig.recorder.stop_count >= 1)
    r = await stop(router)
    assert r["text"] == "甲乙"
    assert rig.segmenter.accepted_samples == 2 * RATE


# --- a session nobody stops ------------------------------------------------------------------


def test_a_lost_session_is_given_a_minute_over_the_recording_cap():
    assert dictation.LOST_SESSION_SECONDS == MAX_RECORDING_SECONDS + 60 == 360


async def test_a_session_nobody_stops_is_cancelled(make_rig, router, brain, caplog):
    clock = FakeClock()
    rig = make_rig(clock=clock, stall_seconds=1e6)  # the stall watchdog stays out of this
    rig.recorder.chunks = [chunk(1.0)]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await start(router, "s1")
        await rig.recorder.fed(1)
        clock.advance(dictation.LOST_SESSION_SECONDS - 5)
        await asyncio.sleep(0.4)  # a few checks
        assert rig.service.active and rig.recorder.stop_count == 0  # not yet
        clock.advance(10)
        await wait_until(lambda: not rig.service.active)
    assert rig.recorder.stop_count == 1 and not rig.recorder.running  # the microphone is closed
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "lost" in r.getMessage()]
    await wait_until(lambda: not our_threads())
    assert brain.finalize_calls == 0
    with pytest.raises(RpcError) as e:  # as after a cancel
        await stop(router, "s1")
    assert e.value.code == NOT_FOUND
    assert await start(router, "s2") == {}  # and a new dictation can begin


async def test_a_session_that_is_stopped_in_time_is_left_alone(make_rig, router, brain):
    clock = FakeClock()
    rig = make_rig(clock=clock, stall_seconds=1e6)
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    clock.advance(dictation.LOST_SESSION_SECONDS - 5)
    r = await stop(router)
    assert r["text"] == "text0" and brain.finalize_calls == 1
    clock.advance(100)
    await asyncio.sleep(0.4)
    assert rig.recorder.stop_count == 1  # only the stop's own


async def test_the_limit_is_counted_from_the_start_of_each_session(make_rig, router):
    clock = FakeClock()
    rig = make_rig(clock=clock, stall_seconds=1e6)
    await start(router, "s1")
    clock.advance(dictation.LOST_SESSION_SECONDS - 5)
    await router.call("dictation.cancel", {"session_id": "s1"})
    await start(router, "s2")  # a new timer
    clock.advance(dictation.LOST_SESSION_SECONDS - 5)
    await asyncio.sleep(0.4)
    assert rig.service.active


async def test_a_session_whose_microphone_stalled_is_still_cancelled_at_the_limit(
    make_rig, router, brain, caplog
):
    clock = FakeClock()
    rig = make_rig(clock=clock)  # the default 2 s stall watchdog
    rig.recorder.chunks = [chunk(1.0)]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await start(router)
        await rig.recorder.fed(1)
        clock.advance(MIC_STALL_SECONDS + 1)
        await wait_until(lambda: rig.recorder.stop_count >= 1)  # the stall closed the microphone
        await asyncio.sleep(0.8)  # the watchdog goes on looking, but says it once
    assert len([r for r in caplog.records if "No audio" in r.getMessage()]) == 1
    assert rig.service.active
    clock.advance(dictation.LOST_SESSION_SECONDS)
    await wait_until(lambda: not rig.service.active)
    assert rig.recorder.stop_count == 1 and brain.finalize_calls == 0


async def test_a_stop_right_after_the_stall_does_not_wait_for_the_next_check(make_rig, router):
    clock = FakeClock()
    rig = make_rig(clock=clock)
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    clock.advance(MIC_STALL_SECONDS + 1)
    await wait_until(lambda: rig.recorder.stop_count >= 1)
    tasks = len(asyncio.all_tasks())
    r = await stop(router)
    assert r["text"] == "text0"
    assert len(asyncio.all_tasks()) == tasks - 1  # the watchdog went with the stop


async def test_a_stop_during_the_lost_session_cancel_gets_not_found(make_rig, router, brain):
    clock = FakeClock()
    rig = make_rig(clock=clock, stall_seconds=1e6)
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    gate = rig.recorder.stop_gate = threading.Event()  # closing the microphone takes a while
    clock.advance(dictation.LOST_SESSION_SECONDS + 1)
    await wait_until(rig.recorder.stop_entered.is_set)
    with pytest.raises(RpcError) as e:  # the session is already being cancelled
        await stop(router)
    assert e.value.code == NOT_FOUND
    assert rig.service.active  # the microphone is still being closed
    gate.set()
    await wait_until(lambda: not rig.service.active)
    assert brain.finalize_calls == 0
    assert rig.recorder.stop_count == 1


async def test_cancel_while_the_watchdog_is_closing_a_stalled_microphone(make_rig, router, brain):
    clock = FakeClock()
    rig = make_rig(clock=clock)  # the default 2 s stall watchdog
    rig.recorder.chunks = [chunk(1.0)]
    tasks = len(asyncio.all_tasks())
    await start(router)
    await rig.recorder.fed(1)
    gate = rig.recorder.stop_gate = threading.Event()  # the watchdog's close gets stuck
    clock.advance(MIC_STALL_SECONDS + 1)
    await wait_until(rig.recorder.stop_entered.is_set)
    cancelling = asyncio.create_task(router.call("dictation.cancel", {"session_id": "s1"}))
    await asyncio.sleep(0.05)
    assert not cancelling.done()  # it answers once the microphone is closed
    gate.set()
    done, _ = await asyncio.wait([cancelling], timeout=3)
    assert done, "dictation.cancel is still waiting after the stalled microphone was closed"
    assert await cancelling == {}
    assert not rig.service.active and rig.recorder.stop_count == 1  # closed once
    assert len(asyncio.all_tasks()) == tasks  # the watchdog ended with the session
    assert brain.finalize_calls == 0
    clock.advance(dictation.LOST_SESSION_SECONDS)  # nothing is left to look at the clock
    await asyncio.sleep(0.4)
    assert rig.recorder.stop_count == 1
    assert await start(router, "s2") == {}


async def test_cancel_while_the_watchdog_is_cancelling_a_lost_session(make_rig, router):
    clock = FakeClock()
    rig = make_rig(clock=clock, stall_seconds=1e6)
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    gate = rig.recorder.stop_gate = threading.Event()
    clock.advance(dictation.LOST_SESSION_SECONDS + 1)
    await wait_until(rig.recorder.stop_entered.is_set)  # the watchdog is cancelling the session
    cancelling = asyncio.create_task(router.call("dictation.cancel", {"session_id": "s1"}))
    await asyncio.sleep(0.05)
    gate.set()
    done, _ = await asyncio.wait([cancelling], timeout=3)
    assert done
    assert not rig.service.active and rig.recorder.stop_count == 1


async def test_two_cancels_at_once_retire_the_session_once(rig, router):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    session = rig.service._session
    gate = rig.recorder.stop_gate = threading.Event()
    first = asyncio.create_task(router.call("dictation.cancel", {"session_id": "s1"}))
    second = asyncio.create_task(router.call("dictation.cancel", {"session_id": "s1"}))
    await wait_until(rig.recorder.stop_entered.is_set)
    await asyncio.sleep(0.05)
    gate.set()
    assert await first == {} and await second == {}
    assert rig.service._retiring.count(session) == 1
    assert rig.recorder.stop_count == 1


# --- the real recorder, segmenter and engine -----------------------------------------------------


@pytest.mark.models
async def test_dictation_with_the_real_wav_recorder_segmenter_and_engine(router, brain):
    from raphael_core.device.audio import WavRecorder
    from raphael_core.device.models import ModelManager
    from raphael_core.device.stt import SenseVoiceEngine, read_wav
    from raphael_core.device.vad import Segmenter
    from raphael_core.paths import models_dir

    models = ModelManager(models_dir())
    wav = models.dir("sensevoice") / "test_wavs" / "zh.wav"
    seconds = read_wav(wav).size / RATE
    vad_model = models.dir("silero-vad") / "silero_vad_v5.onnx"
    engine = await asyncio.to_thread(SenseVoiceEngine, models.dir("sensevoice"))
    segmenters: list[Segmenter] = []

    def make_segmenter() -> Segmenter:
        segmenters.append(Segmenter(vad_model))
        return segmenters[-1]

    service = DictationService(
        router,
        recorder_factory=lambda on_frames: WavRecorder(on_frames, wav, realtime=True),
        segmenter_factory=make_segmenter,
        engine_provider=lambda: engine,
    )
    service.register(router)
    try:
        await start(router, "real")
        await asyncio.sleep(seconds + 0.3)  # the file plays at the pace of speech
        r = await stop(router, "real", target_app="pytest")
    finally:
        await service.aclose()
    assert r["text"] and brain.finalize_params[-1]["raw_text"] == r["text"]
    assert 0.5 * seconds <= r["audio_seconds"] <= 1.1 * seconds
    assert segmenters[0].speech_seconds == pytest.approx(r["audio_seconds"])


# --- on_idle: the service says when it is free again (final review, item 5) ---------------------


@pytest.fixture
def idle_rig(make_rig):
    """A rig whose on_idle records whether the service was idle when it was called."""
    seen: list[bool] = []
    holder: dict = {}

    def on_idle() -> None:
        seen.append(not holder["rig"].service.active)

    holder["rig"] = rig = make_rig(on_idle=on_idle)
    rig.idle_calls = seen
    return rig


async def test_on_idle_follows_a_stop(idle_rig, router, brain):
    idle_rig.recorder.chunks = [chunk(1.0)]
    seen_at_finalize: list[int] = []
    brain.on_finalize = lambda: seen_at_finalize.append(len(idle_rig.idle_calls))
    await start(router)
    await idle_rig.recorder.fed(1)
    assert idle_rig.idle_calls == []
    await stop(router)
    assert idle_rig.idle_calls == [True]
    assert seen_at_finalize == [1]  # idle before the brain is asked, as the service is


async def test_on_idle_follows_a_cancel(idle_rig, router):
    await start(router)
    await router.call("dictation.cancel", {"session_id": "s1"})
    assert idle_rig.idle_calls == [True]


async def test_on_idle_follows_a_start_that_failed(idle_rig, router):
    idle_rig.recorder.fail_on_start = True
    with pytest.raises(RpcError):
        await start(router)
    assert idle_rig.idle_calls == [True]


async def test_on_idle_follows_a_stop_that_was_itself_cancelled(idle_rig, router):
    idle_rig.engine.gate = threading.Event()
    idle_rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await idle_rig.recorder.fed(1)
    assert idle_rig.engine.entered.wait(5)
    stopping = asyncio.create_task(stop(router))
    await asyncio.sleep(0.05)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert idle_rig.idle_calls == [True]
    idle_rig.engine.gate.set()
    await wait_until(lambda: not our_threads())


async def test_on_idle_follows_a_session_nobody_stopped(make_rig, router):
    clock = FakeClock()
    calls: list[int] = []
    rig = make_rig(clock=clock, stall_seconds=1e6, on_idle=lambda: calls.append(1))
    await start(router)
    clock.advance(dictation.LOST_SESSION_SECONDS + 1)
    await wait_until(lambda: not rig.service.active)
    assert calls == [1]


async def test_a_busy_start_does_not_say_idle(idle_rig, router):
    await start(router, "s1")
    with pytest.raises(RpcError) as e:
        await start(router, "s2")
    assert e.value.code == BUSY and idle_rig.idle_calls == []


async def test_an_on_idle_that_fails_is_logged_and_the_service_goes_on(make_rig, router, caplog):
    def on_idle() -> None:
        raise RuntimeError("the meter is broken")

    rig = make_rig(on_idle=on_idle)
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        r = await stop(router)
    assert r["text"] == "text0"
    assert "the meter is broken" in caplog.text
    assert await start(router, "s2") == {}


# --- a dictation.cancel that is itself cancelled (final review, item 8) -------------------------


async def test_a_cancel_that_is_itself_cancelled_still_ends_the_session(rig, router, brain):
    rig.recorder.chunks = [chunk(1.0)]
    await start(router)
    await rig.recorder.fed(1)
    gate = rig.recorder.stop_gate = threading.Event()  # the cancel's close gets stuck
    cancelling = asyncio.create_task(router.call("dictation.cancel", {"session_id": "s1"}))
    await wait_until(rig.recorder.stop_entered.is_set)
    cancelling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelling
    assert not rig.service.active  # not left half-ended, busy for good
    await wait_until(lambda: rig.recorder.stop_count == 2)  # the microphone is closed anyway
    await wait_until(lambda: not rig.recorder.running)
    gate.set()
    await wait_until(lambda: not our_threads())
    assert brain.finalize_calls == 0
    with pytest.raises(RpcError) as e:
        await stop(router, "s1")
    assert e.value.code == NOT_FOUND


# --- which speech-to-text heard it ---------------------------------------------------------------


async def test_the_brain_is_told_which_speech_to_text_heard_the_dictation(rig, router, brain):
    rig.recorder.chunks = [chunk(1.0)] * 2
    await start(router)
    await rig.recorder.fed(2)
    await stop(router)
    assert brain.finalize_params[-1]["stt"] == "fake"  # the engine's name


async def test_an_engine_that_changes_model_per_segment_is_reported_in_order(rig, router, brain):
    used = iter(["openai/gpt-transcribe", "qwen3-asr", "qwen3-asr"])
    transcribe = rig.engine.transcribe

    def cloud_then_local(samples, **kwargs):
        rig.engine.last_used = next(used)
        return transcribe(samples, **kwargs)

    rig.engine.transcribe = cloud_then_local
    rig.recorder.chunks = [chunk(1.0)] * 3
    await start(router)
    await rig.recorder.fed(3)
    await stop(router)
    assert brain.finalize_params[-1]["stt"] == "openai/gpt-transcribe+qwen3-asr"


# --- the voice assistant's speech ----------------------------------------------------------------
#
# A session started with purpose "agent" records and transcribes like any other, but what comes
# back is the transcript to ask the assistant, not text to paste: the brain only normalizes it (the
# dictionary's replacements and the Chinese script), without cleanup, history or cost.


async def start_agent(router, session_id="s1"):
    return await router.call("dictation.start", {"session_id": session_id, "purpose": "agent"})


async def test_an_agent_session_answers_with_the_normalized_transcript_and_nothing_to_paste(
    rig, router, brain
):
    rig.engine.texts = ["打開", "YouTube"]
    rig.recorder.chunks = [chunk(1.0)] * 2
    assert await start_agent(router) == {}
    await rig.recorder.fed(2)
    r = await stop(router, target_app="notepad.exe")
    assert r == {
        "purpose": "agent",
        "text": "打開 YouTube（正規化）",
        "stt": "fake",
        "audio_seconds": pytest.approx(2.0),
        "silent": False,
    }
    assert brain.normalize_params == [{"raw_text": "打開 YouTube"}]
    assert brain.finalize_calls == 0  # no cleanup, no history row, no paste
    assert not rig.service.active


async def test_an_agent_session_is_not_cleaned_so_its_progress_has_no_cleaning_stage(
    rig, router, notifications
):
    rig.recorder.chunks = [chunk(1.0)] * 2
    await start_agent(router)
    await rig.recorder.fed(2)
    await stop(router)
    steps = progress(notifications)
    assert steps and all(stage == "transcribing" for stage, _, _ in steps)


async def test_an_agent_session_says_which_speech_to_text_heard_it(rig, router):
    used = iter(["openai/gpt-transcribe", "qwen3-asr"])
    transcribe = rig.engine.transcribe

    def cloud_then_local(samples, **kwargs):
        rig.engine.last_used = next(used)
        return transcribe(samples, **kwargs)

    rig.engine.transcribe = cloud_then_local
    rig.recorder.chunks = [chunk(1.0)] * 2
    await start_agent(router)
    await rig.recorder.fed(2)
    assert (await stop(router))["stt"] == "openai/gpt-transcribe+qwen3-asr"


async def test_an_agent_session_that_heard_nothing_asks_nobody(rig, router, brain):
    rig.segmenter.emit_nothing = True
    await start_agent(router)
    r = await stop(router)
    assert r == {
        "purpose": "agent",
        "text": "",
        "stt": "",
        "audio_seconds": 0,
        "silent": False,
    }
    assert brain.normalize_params == [] and brain.finalize_calls == 0


async def test_an_agent_session_on_a_dead_microphone_is_reported_silent(rig, router, brain):
    rig.segmenter.emit_nothing = True
    rig.recorder.chunks = [chunk(1.0, DEAD_MIC)] * 3
    await start_agent(router)
    await rig.recorder.fed(3)
    r = await stop(router)
    assert r["purpose"] == "agent" and r["text"] == "" and r["silent"] is True


async def test_an_agent_session_whose_normalizing_fails_keeps_the_words_as_heard(
    rig, router, brain, caplog
):
    brain.normalize_error = RpcError(INTERNAL_ERROR, "the brain is broken: 秘密")
    rig.engine.texts = ["打開 YouTube"]
    rig.recorder.chunks = [chunk(1.0)]
    await start_agent(router)
    await rig.recorder.fed(1)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        r = await stop(router)
    assert r["text"] == "打開 YouTube" and r["purpose"] == "agent"
    assert "秘密" not in caplog.text and "打開 YouTube" not in caplog.text  # type of error only


async def test_an_agent_session_that_is_cancelled_while_recording_is_gone(rig, router, brain):
    rig.recorder.chunks = [chunk(1.0)]
    await start_agent(router)
    await rig.recorder.fed(1)
    assert await router.call("dictation.cancel", {"session_id": "s1"}) == {}
    assert not rig.service.active
    with pytest.raises(RpcError) as e:
        await stop(router)
    assert e.value.code == NOT_FOUND
    assert brain.normalize_params == [] and brain.finalize_calls == 0


async def test_an_agent_session_without_a_microphone_is_no_microphone(rig, router):
    rig.recorder.fail_on_start = True
    with pytest.raises(RpcError) as e:
        await start_agent(router)
    assert e.value.code == NO_MICROPHONE
    assert not rig.service.active


@pytest.mark.parametrize("params", [{}, {"purpose": None}, {"purpose": "dictation"}])
async def test_a_session_without_a_purpose_or_for_dictation_is_cleaned_and_saved(
    rig, router, brain, params
):
    rig.recorder.chunks = [chunk(1.0)]
    await router.call("dictation.start", {"session_id": "s1", **params})
    await rig.recorder.fed(1)
    r = await stop(router)
    assert r["id"] == 7 and r["text"] == "text0" and "purpose" not in r
    assert brain.finalize_calls == 1 and brain.normalize_params == []


@pytest.mark.parametrize("purpose", ["", "AGENT", "assistant", 1, True, ["agent"], {}])
async def test_any_other_purpose_is_invalid_params_and_starts_nothing(rig, router, purpose):
    with pytest.raises(RpcError) as e:
        await router.call("dictation.start", {"session_id": "s1", "purpose": purpose})
    assert e.value.code == INVALID_PARAMS
    assert not rig.service.active and rig.recorders_made == 0
    await start(router)  # the service is free for the next one
    assert rig.service.active
