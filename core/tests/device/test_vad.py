"""Tests for the voice activity segmenter.

Most tests put a fake sherpa-onnx detector in place of the real one, so they need no model. The
tests marked `models` run the real Silero VAD from the models folder: set RAPHAEL_TEST_MODELS=1 to
include them.
"""

from types import SimpleNamespace

import numpy as np
import pytest
import sherpa_onnx

from raphael_core.device.models import ModelManager
from raphael_core.device.stt import read_wav
from raphael_core.device.vad import Segmenter
from raphael_core.paths import models_dir

WINDOW = 512


class FakeDetector:
    """A VoiceActivityDetector that records what it is fed and finds the speech it is told to.

    `plan` maps the number of windows accepted so far to the segments (lists of samples) that
    become available at that point; `flush_plan` is what flush() makes available.
    """

    instances: list["FakeDetector"] = []
    plan: dict[int, list[list[float]]] = {}
    flush_plan: list[list[float]] = []

    def __init__(self, config, buffer_size_in_seconds):
        self.config = config
        self.buffer_size_in_seconds = buffer_size_in_seconds
        self.windows: list[np.ndarray] = []
        self.flushed = False
        self.resets = 0
        self.queue: list[SimpleNamespace] = []
        FakeDetector.instances.append(self)

    def accept_waveform(self, samples):
        self.windows.append(np.array(samples, dtype=np.float32))
        for segment in self.plan.get(len(self.windows), []):
            self.queue.append(SimpleNamespace(samples=list(segment), start=0))

    def flush(self):
        self.flushed = True
        for segment in self.flush_plan:
            self.queue.append(SimpleNamespace(samples=list(segment), start=0))

    def reset(self):
        self.resets += 1

    def empty(self):
        return not self.queue

    @property
    def front(self):
        return self.queue[0]

    def pop(self):
        self.queue.pop(0)


@pytest.fixture
def fake_detector(monkeypatch):
    FakeDetector.instances = []
    FakeDetector.plan = {}
    FakeDetector.flush_plan = []
    monkeypatch.setattr(sherpa_onnx, "VoiceActivityDetector", FakeDetector)
    return FakeDetector


@pytest.fixture
def model_file(tmp_path):
    path = tmp_path / "silero_vad_v5.onnx"
    path.write_bytes(b"not really a model")
    return path


def test_missing_model_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Segmenter(tmp_path / "nope.onnx")


def test_a_directory_is_not_a_model(tmp_path):
    with pytest.raises(FileNotFoundError):
        Segmenter(tmp_path)


def test_the_detector_is_built_from_the_settings(fake_detector, model_file):
    Segmenter(model_file, min_silence_s=0.7, max_segment_s=15.0, threshold=0.4)
    (detector,) = fake_detector.instances
    config = detector.config
    assert config.silero_vad.model == str(model_file)
    assert config.silero_vad.threshold == pytest.approx(0.4)
    assert config.silero_vad.min_silence_duration == pytest.approx(0.7)
    assert config.silero_vad.min_speech_duration == pytest.approx(0.25)
    assert config.silero_vad.window_size == 512
    assert config.silero_vad.max_speech_duration == pytest.approx(15.0)
    assert config.sample_rate == 16000
    assert detector.buffer_size_in_seconds == 120


def test_defaults(fake_detector, model_file):
    Segmenter(model_file)
    config = fake_detector.instances[0].config
    assert config.silero_vad.threshold == pytest.approx(0.5)
    assert config.silero_vad.min_silence_duration == pytest.approx(0.5)
    assert config.silero_vad.max_speech_duration == pytest.approx(20.0)


def test_audio_goes_in_as_windows_of_512_with_the_rest_kept_for_next_time(
    fake_detector, model_file
):
    seg = Segmenter(model_file)
    detector = fake_detector.instances[0]
    audio = np.arange(1300, dtype=np.float32) / 2000
    assert seg.accept(audio[:700]) == []
    assert len(detector.windows) == 1
    assert seg.accept(audio[700:]) == []
    assert len(detector.windows) == 2  # 1300 samples: two windows and 276 left over
    assert all(w.size == WINDOW and w.dtype == np.float32 for w in detector.windows)
    assert np.array_equal(np.concatenate(detector.windows), audio[: 2 * WINDOW])


def test_flush_pads_the_last_window_with_zeros_and_flushes(fake_detector, model_file):
    seg = Segmenter(model_file)
    detector = fake_detector.instances[0]
    audio = np.full(600, 0.3, np.float32)
    seg.accept(audio)
    assert len(detector.windows) == 1
    assert seg.flush() == []
    assert len(detector.windows) == 2
    last = detector.windows[1]
    assert last.size == WINDOW
    assert np.all(last[: 600 - WINDOW] == np.float32(0.3))
    assert np.all(last[600 - WINDOW :] == 0.0)
    assert detector.flushed
    assert detector.resets == 1  # the detector starts afresh for whatever comes next


def test_flush_with_nothing_left_over_adds_no_window(fake_detector, model_file):
    seg = Segmenter(model_file)
    detector = fake_detector.instances[0]
    seg.accept(np.zeros(2 * WINDOW, np.float32))
    seg.flush()
    assert len(detector.windows) == 2
    assert detector.flushed


def test_segments_come_back_as_float32_arrays(fake_detector, model_file):
    fake_detector.plan = {2: [[0.1] * 4000]}
    seg = Segmenter(model_file)
    parts = seg.accept(np.zeros(2 * WINDOW, np.float32))
    assert len(parts) == 1
    assert isinstance(parts[0], np.ndarray)
    assert parts[0].dtype == np.float32 and parts[0].ndim == 1
    assert parts[0].size == 4000


def test_segments_found_by_flush_are_returned_by_flush(fake_detector, model_file):
    fake_detector.flush_plan = [[0.2] * 3000]
    seg = Segmenter(model_file)
    assert seg.accept(np.zeros(WINDOW, np.float32)) == []
    parts = seg.flush()
    assert [p.size for p in parts] == [3000]


def test_every_segment_waiting_is_taken(fake_detector, model_file):
    fake_detector.plan = {1: [[0.1] * 100, [0.2] * 200]}
    seg = Segmenter(model_file)
    parts = seg.accept(np.zeros(WINDOW, np.float32))
    assert [p.size for p in parts] == [100, 200]


def test_a_segment_over_the_limit_is_split_in_equal_pieces(fake_detector, model_file):
    samples = list(np.linspace(-1, 1, 50 * 16000, dtype=np.float32))
    fake_detector.plan = {1: [samples]}
    seg = Segmenter(model_file)  # 20 s
    parts = seg.accept(np.zeros(WINDOW, np.float32))
    assert [p.size for p in parts] == [266667, 266667, 266666]
    assert all(p.size <= 20 * 16000 for p in parts)
    assert np.array_equal(np.concatenate(parts), np.asarray(samples, dtype=np.float32))


def test_a_segment_of_exactly_the_limit_is_not_split(fake_detector, model_file):
    fake_detector.plan = {1: [[0.5] * (20 * 16000)]}
    parts = Segmenter(model_file).accept(np.zeros(WINDOW, np.float32))
    assert [p.size for p in parts] == [20 * 16000]


def test_the_limit_is_the_one_given(fake_detector, model_file):
    fake_detector.plan = {1: [[0.5] * (7 * 16000)]}
    seg = Segmenter(model_file, max_segment_s=3.0)
    parts = seg.accept(np.zeros(WINDOW, np.float32))
    assert len(parts) == 3
    assert all(p.size <= 3 * 16000 for p in parts)


def test_speech_seconds_adds_up_the_segments_found(fake_detector, model_file):
    fake_detector.plan = {1: [[0.1] * 16000], 2: [[0.1] * 8000]}
    fake_detector.flush_plan = [[0.1] * 4000]
    seg = Segmenter(model_file)
    assert seg.speech_seconds == 0.0
    seg.accept(np.zeros(WINDOW, np.float32))
    assert seg.speech_seconds == pytest.approx(1.0)
    seg.accept(np.zeros(WINDOW, np.float32))
    assert seg.speech_seconds == pytest.approx(1.5)
    seg.flush()
    assert seg.speech_seconds == pytest.approx(1.75)  # flush does not reset it


def test_speech_seconds_counts_only_the_current_recording(fake_detector, model_file):
    """One Segmenter serves many dictations: each reports its own speech, not the sum."""
    fake_detector.plan = {1: [[0.1] * 16000], 3: [[0.1] * 8000]}
    fake_detector.flush_plan = [[0.1] * 4000]
    seg = Segmenter(model_file)
    seg.accept(np.zeros(WINDOW, np.float32))
    seg.flush()
    assert seg.speech_seconds == pytest.approx(1.25)  # still readable after flush
    assert seg.speech_seconds == pytest.approx(1.25)  # and reading it changes nothing
    seg.accept(np.zeros(2 * WINDOW, np.float32))  # a new dictation: windows 2 and 3
    assert seg.speech_seconds == pytest.approx(0.5)
    seg.flush()
    assert seg.speech_seconds == pytest.approx(0.75)


def test_a_new_recording_with_no_speech_reports_none(fake_detector, model_file):
    fake_detector.plan = {1: [[0.1] * 16000]}
    seg = Segmenter(model_file)
    seg.accept(np.zeros(WINDOW, np.float32))
    seg.flush()
    assert seg.speech_seconds == pytest.approx(1.0)
    seg.accept(np.zeros(WINDOW, np.float32))
    seg.flush()
    assert seg.speech_seconds == 0.0


def test_a_rejected_call_does_not_end_the_reading_of_speech_seconds(fake_detector, model_file):
    fake_detector.plan = {1: [[0.1] * 16000]}
    seg = Segmenter(model_file)
    seg.accept(np.zeros(WINDOW, np.float32))
    seg.flush()
    with pytest.raises(ValueError):
        seg.accept(np.zeros((2, 2), np.float32))
    assert seg.speech_seconds == pytest.approx(1.0)


def test_speech_seconds_counts_a_split_segment_once(fake_detector, model_file):
    fake_detector.plan = {1: [[0.1] * (30 * 16000)]}
    seg = Segmenter(model_file)
    seg.accept(np.zeros(WINDOW, np.float32))
    assert seg.speech_seconds == pytest.approx(30.0)


def test_accept_takes_float32_1d_only(fake_detector, model_file):
    seg = Segmenter(model_file)
    with pytest.raises(ValueError):
        seg.accept(np.zeros((2, 512), np.float32))
    assert seg.accept(np.zeros(0, np.float32)) == []
    assert seg.accept(np.zeros(600, np.float64)) == []  # converted, not rejected
    assert fake_detector.instances[0].windows[0].dtype == np.float32


def test_accept_after_flush_starts_clean(fake_detector, model_file):
    seg = Segmenter(model_file)
    detector = fake_detector.instances[0]
    seg.accept(np.zeros(600, np.float32))
    seg.flush()
    before = len(detector.windows)
    seg.accept(np.zeros(100, np.float32))
    assert len(detector.windows) == before  # the 100 samples wait for a full window of their own
    seg.flush()
    assert len(detector.windows) == before + 1


# --- with the real model ---------------------------------------------------------------------


@pytest.mark.models
def test_finds_speech_and_ignores_silence():
    models = ModelManager(models_dir())
    seg = Segmenter(models.dir("silero-vad") / "silero_vad_v5.onnx")
    speech = read_wav(models.dir("sensevoice") / "test_wavs" / "zh.wav")
    silence = np.zeros(16000, np.float32)
    parts = seg.accept(np.concatenate([silence, speech, silence])) + seg.flush()
    assert parts and 0.5 * speech.size / 16000 <= seg.speech_seconds <= 1.1 * speech.size / 16000
    quiet = Segmenter(models.dir("silero-vad") / "silero_vad_v5.onnx")
    assert quiet.accept(np.zeros(48000, np.float32)) + quiet.flush() == []


@pytest.mark.models
def test_segments_never_exceed_20_seconds():
    models = ModelManager(models_dir())
    seg = Segmenter(models.dir("silero-vad") / "silero_vad_v5.onnx")
    speech = read_wav(models.dir("qwen3-asr") / "test_wavs" / "raokouling.wav")
    long_audio = np.tile(speech, int(np.ceil(50 * 16000 / speech.size)))
    parts = seg.accept(long_audio) + seg.flush()
    assert all(p.size <= 20 * 16000 for p in parts)


@pytest.mark.models
def test_speech_arriving_in_small_pieces_gives_the_same_segments():
    models = ModelManager(models_dir())
    path = models.dir("silero-vad") / "silero_vad_v5.onnx"
    speech = read_wav(models.dir("sensevoice") / "test_wavs" / "zh.wav")
    silence = np.zeros(16000, np.float32)
    audio = np.concatenate([silence, speech, silence])
    whole = Segmenter(path)
    expected = whole.accept(audio) + whole.flush()
    pieces = Segmenter(path)
    got = []
    for start in range(0, audio.size, 320):  # 20 ms, as the microphone gives it
        got += pieces.accept(audio[start : start + 320])
    got += pieces.flush()
    assert [p.size for p in got] == [p.size for p in expected]
    assert pieces.speech_seconds == pytest.approx(whole.speech_seconds)


@pytest.mark.models
def test_a_segmenter_can_be_used_again_after_flush():
    models = ModelManager(models_dir())
    seg = Segmenter(models.dir("silero-vad") / "silero_vad_v5.onnx")
    speech = read_wav(models.dir("sensevoice") / "test_wavs" / "zh.wav")
    audio = np.concatenate([np.zeros(16000, np.float32), speech, np.zeros(16000, np.float32)])
    first = seg.accept(audio) + seg.flush()
    first_seconds = seg.speech_seconds
    second = seg.accept(audio) + seg.flush()
    assert first and second
    assert [p.size for p in second] == [p.size for p in first]
    assert seg.speech_seconds == pytest.approx(first_seconds)  # not the sum of the two
