"""Tests for the speech-to-text engines.

Most tests put a fake recognizer in place of sherpa-onnx's, so they need no model and load nothing.
The tests marked `models` run the real models from the models folder: set RAPHAEL_TEST_MODELS=1 to
include them.
"""

import re
import struct
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import sherpa_onnx

from raphael_core.device.models import REGISTRY, ModelManager
from raphael_core.device.stt import (
    LANGUAGE_NAMES,
    DolphinEngine,
    FireRedAsrEngine,
    ParaformerEngine,
    Qwen3AsrEngine,
    SenseVoiceEngine,
    WhisperOnnxEngine,
    WhisperSmallEngine,
    WhisperTurboEngine,
    create_engine,
    read_wav,
)
from raphael_core.paths import models_dir

ENGINES = {
    "qwen3-asr": Qwen3AsrEngine,
    "sensevoice": SenseVoiceEngine,
    "breeze-asr-25": WhisperOnnxEngine,
    "whisper-small": WhisperSmallEngine,
    "whisper-turbo": WhisperTurboEngine,
    "paraformer-zh": ParaformerEngine,
    "dolphin-small": DolphinEngine,
    "fire-red-asr": FireRedAsrEngine,
}
# Every file of every model, to leave out one at a time.
MODEL_FILES = [(name, file) for name in ENGINES for file in REGISTRY[name].files]


class FakeStream:
    def __init__(self) -> None:
        self.options: dict[str, str] = {}
        self.sample_rate = None
        self.waveform = None
        self.result = SimpleNamespace(text="")

    def set_option(self, key: str, value: str) -> None:
        self.options[key] = value

    def accept_waveform(self, sample_rate, waveform) -> None:
        self.sample_rate = sample_rate
        self.waveform = waveform


class FakeRecognizer:
    """What sherpa_onnx.OfflineRecognizer builds: answers with `text`, and keeps each stream as it
    was when it was decoded in `decoded`."""

    def __init__(self, text: str = "hello") -> None:
        self.text = text
        self.created = 0
        self.decoded: list[SimpleNamespace] = []

    def create_stream(self, hotwords=None) -> FakeStream:
        # The real one ends the whole process for Qwen3-ASR and SenseVoice when it is given these.
        assert hotwords is None, "create_stream(hotwords=...) ends the process in sherpa-onnx"
        self.created += 1
        return FakeStream()

    def decode_stream(self, stream: FakeStream) -> None:
        assert stream.waveform is not None, "the audio must be in before decoding"
        self.decoded.append(
            SimpleNamespace(
                options=dict(stream.options),
                sample_rate=stream.sample_rate,
                waveform=np.asarray(stream.waveform, dtype=np.float32),
            )
        )
        stream.result.text = self.text


class SlowRecognizer(FakeRecognizer):
    """Takes a moment to decode, and notes how many decodings ran at the same time."""

    def __init__(self) -> None:
        super().__init__()
        self._guard = threading.Lock()
        self._running = 0
        self.most_running = 0

    def decode_stream(self, stream: FakeStream) -> None:
        with self._guard:
            self._running += 1
            self.most_running = max(self.most_running, self._running)
        time.sleep(0.05)
        with self._guard:
            self._running -= 1
        super().decode_stream(stream)


class FakeOfflineRecognizer:
    """In place of the class sherpa_onnx.OfflineRecognizer: loads nothing, and notes each `from_*`
    call as (method, keyword arguments) in `calls`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.recognizer = FakeRecognizer()

    def from_qwen3_asr(self, **arguments):
        return self._built("from_qwen3_asr", arguments)

    def from_sense_voice(self, **arguments):
        return self._built("from_sense_voice", arguments)

    def from_whisper(self, **arguments):
        return self._built("from_whisper", arguments)

    def from_paraformer(self, **arguments):
        return self._built("from_paraformer", arguments)

    def from_dolphin_ctc(self, **arguments):
        return self._built("from_dolphin_ctc", arguments)

    def from_fire_red_asr(self, **arguments):
        return self._built("from_fire_red_asr", arguments)

    def _built(self, method: str, arguments: dict) -> FakeRecognizer:
        self.calls.append((method, arguments))
        return self.recognizer


@pytest.fixture
def sherpa(monkeypatch):
    """sherpa_onnx.OfflineRecognizer replaced by a fake that records how it is used."""
    fake = FakeOfflineRecognizer()
    monkeypatch.setattr(sherpa_onnx, "OfflineRecognizer", fake)
    return fake


def install(root: Path, name: str, *, without: str | None = None) -> Path:
    """The files of model `name` under `root`, empty, all but `without`. Gives the directory."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for file in REGISTRY[name].files:
        if file != without:
            (directory / file).parent.mkdir(parents=True, exist_ok=True)
            (directory / file).touch()
    return directory


@pytest.fixture
def qwen3(tmp_path, sherpa):
    return Qwen3AsrEngine(install(tmp_path, "qwen3-asr"))


@pytest.fixture
def speech():
    """A second of noise: anything but silence."""
    return np.random.default_rng(0).uniform(-0.5, 0.5, 16_000).astype(np.float32)


# --- choosing and loading an engine


def test_language_names():
    assert LANGUAGE_NAMES == {
        "zh": "Chinese",
        "yue": "Cantonese",
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
        "it": "Italian",
        "pt": "Portuguese",
        "vi": "Vietnamese",
        "th": "Thai",
        "id": "Indonesian",
    }


def test_qwen3_asr_knows_every_language_that_can_be_chosen():
    from raphael_core.languages import STT_CODES

    assert set(STT_CODES) == set(LANGUAGE_NAMES)


def test_unknown_engine(tmp_path):
    with pytest.raises(ValueError):
        create_engine("nope", ModelManager(tmp_path))


def test_missing_files_raise_instead_of_killing_the_process(tmp_path):
    with pytest.raises(FileNotFoundError):
        Qwen3AsrEngine(tmp_path)


@pytest.mark.parametrize("name", ENGINES)
def test_a_model_that_is_not_downloaded_is_refused_before_sherpa_is_called(name, tmp_path, sherpa):
    with pytest.raises(FileNotFoundError):
        ENGINES[name](tmp_path)
    with pytest.raises(FileNotFoundError):
        create_engine(name, ModelManager(tmp_path))
    assert sherpa.calls == []


@pytest.mark.parametrize(("name", "missing"), MODEL_FILES)
def test_every_file_of_the_model_is_needed(name, missing, tmp_path, sherpa):
    directory = install(tmp_path, name, without=missing)
    with pytest.raises(FileNotFoundError, match=re.escape(missing)):
        ENGINES[name](directory)
    assert sherpa.calls == []


@pytest.mark.parametrize("name", ENGINES)
def test_create_engine_gives_the_engine_of_that_name(name, tmp_path, sherpa):
    install(tmp_path, name)
    engine = create_engine(name, ModelManager(tmp_path), num_threads=2)
    assert type(engine) is ENGINES[name]
    assert engine.name == name
    [(_, arguments)] = sherpa.calls
    assert arguments["num_threads"] == 2


def test_threads_default_to_four(tmp_path, sherpa):
    Qwen3AsrEngine(install(tmp_path, "qwen3-asr"))
    assert sherpa.calls[0][1]["num_threads"] == 4


def test_qwen3_recognizer_settings(tmp_path, sherpa):
    directory = install(tmp_path, "qwen3-asr")
    Qwen3AsrEngine(directory)
    assert sherpa.calls == [
        (
            "from_qwen3_asr",
            {
                "conv_frontend": str(directory / "conv_frontend.onnx"),
                "encoder": str(directory / "encoder.int8.onnx"),
                "decoder": str(directory / "decoder.int8.onnx"),
                "tokenizer": str(directory / "tokenizer"),
                "num_threads": 4,
                "provider": "cpu",
                "max_total_len": 1024,
                "max_new_tokens": 384,
            },
        )
    ]


def test_sensevoice_recognizer_settings(tmp_path, sherpa):
    directory = install(tmp_path, "sensevoice")
    SenseVoiceEngine(directory)
    assert sherpa.calls == [
        (
            "from_sense_voice",
            {
                "model": str(directory / "model.int8.onnx"),
                "tokens": str(directory / "tokens.txt"),
                "num_threads": 4,
                "provider": "cpu",
                "language": "",
                "use_itn": True,
            },
        )
    ]


def test_whisper_recognizer_settings(tmp_path, sherpa):
    directory = install(tmp_path, "breeze-asr-25")
    WhisperOnnxEngine(directory)
    assert sherpa.calls == [
        (
            "from_whisper",
            {
                "encoder": str(directory / "breeze-asr-25-half-encoder.int8.onnx"),
                "decoder": str(directory / "breeze-asr-25-half-decoder.int8.onnx"),
                "tokens": str(directory / "breeze-asr-25-half-tokens.txt"),
                "num_threads": 4,
                "provider": "cpu",
                "language": "zh",
            },
        )
    ]


@pytest.mark.parametrize(
    ("name", "prefix", "language"),
    [("whisper-small", "small", ""), ("whisper-turbo", "turbo", "")],
)
def test_the_other_whisper_models_find_the_language_themselves(name, prefix, language, tmp_path, sherpa):
    directory = install(tmp_path, name)
    create_engine(name, ModelManager(tmp_path))
    assert sherpa.calls == [
        (
            "from_whisper",
            {
                "encoder": str(directory / f"{prefix}-encoder.int8.onnx"),
                "decoder": str(directory / f"{prefix}-decoder.int8.onnx"),
                "tokens": str(directory / f"{prefix}-tokens.txt"),
                "num_threads": 4,
                "provider": "cpu",
                "language": language,
            },
        )
    ]


def test_paraformer_recognizer_settings(tmp_path, sherpa):
    directory = install(tmp_path, "paraformer-zh")
    ParaformerEngine(directory)
    assert sherpa.calls == [
        (
            "from_paraformer",
            {
                "paraformer": str(directory / "model.int8.onnx"),
                "tokens": str(directory / "tokens.txt"),
                "num_threads": 4,
                "provider": "cpu",
            },
        )
    ]


def test_dolphin_recognizer_settings(tmp_path, sherpa):
    directory = install(tmp_path, "dolphin-small")
    DolphinEngine(directory)
    assert sherpa.calls == [
        (
            "from_dolphin_ctc",
            {
                "model": str(directory / "model.int8.onnx"),
                "tokens": str(directory / "tokens.txt"),
                "num_threads": 4,
                "provider": "cpu",
            },
        )
    ]


def test_fire_red_recognizer_settings(tmp_path, sherpa):
    directory = install(tmp_path, "fire-red-asr")
    FireRedAsrEngine(directory)
    assert sherpa.calls == [
        (
            "from_fire_red_asr",
            {
                "encoder": str(directory / "encoder.int8.onnx"),
                "decoder": str(directory / "decoder.int8.onnx"),
                "tokens": str(directory / "tokens.txt"),
                "num_threads": 4,
                "provider": "cpu",
            },
        )
    ]


def test_the_engines_that_have_no_use_for_hints_ignore_them(tmp_path, sherpa, speech):
    for name in ("paraformer-zh", "dolphin-small", "fire-red-asr", "whisper-small"):
        install(tmp_path, name)
        engine = create_engine(name, ModelManager(tmp_path))
        engine.transcribe(speech, hints=["拉斐爾"], language="ja")
    assert all(decoded.options == {} for decoded in sherpa.recognizer.decoded)
    assert len(sherpa.recognizer.decoded) == 4


# --- transcribing


@pytest.mark.parametrize("name", ENGINES)
def test_transcribe_gives_the_text_of_the_audio(name, tmp_path, sherpa, speech):
    sherpa.recognizer.text = "  hello world \n"
    install(tmp_path, name)
    engine = create_engine(name, ModelManager(tmp_path))
    assert engine.transcribe(speech) == "hello world"
    [decoded] = sherpa.recognizer.decoded
    assert decoded.sample_rate == 16_000
    assert np.array_equal(decoded.waveform, speech)


@pytest.mark.parametrize(
    ("written", "text"),
    [
        # What Qwen3-ASR wrote for a burst of noise in a probe: only the marker that comes before
        # the text, which sherpa-onnx removes when something else comes before it, but not alone.
        ("<asr_text>", ""),
        ("language None<asr_text>", ""),
        ("language Chinese<asr_text>你好", "你好"),
        ("<|im_end|>hello", "hello"),
        ("the language barrier", "the language barrier"),
    ],
)
def test_qwen3_markup_is_not_text(written, text, qwen3, sherpa, speech):
    sherpa.recognizer.text = written
    assert qwen3.transcribe(speech) == text


def test_audio_in_another_float_type_is_converted(qwen3, sherpa, speech):
    qwen3.transcribe(speech.astype(np.float64))
    assert np.array_equal(sherpa.recognizer.decoded[0].waveform, speech)


def test_audio_must_be_one_dimensional(qwen3, sherpa, speech):
    with pytest.raises(ValueError, match="1-D"):
        qwen3.transcribe(speech.reshape(-1, 1))
    assert sherpa.recognizer.created == 0


@pytest.mark.parametrize("name", ENGINES)
@pytest.mark.parametrize("silence", [np.zeros(0), np.zeros(16_000)], ids=["empty", "zeros"])
def test_nothing_to_hear_is_not_decoded(name, silence, tmp_path, sherpa):
    install(tmp_path, name)
    engine = create_engine(name, ModelManager(tmp_path))
    assert engine.transcribe(silence.astype(np.float32), hints=["拉斐爾"]) == ""
    assert sherpa.recognizer.created == 0


def test_each_call_gets_its_own_stream(qwen3, sherpa, speech):
    qwen3.transcribe(speech, hints=["Codex"], language="ja")
    qwen3.transcribe(speech)
    first, second = sherpa.recognizer.decoded
    assert first.options == {"hotwords": "Codex", "language": "Japanese"}
    assert second.options == {}


# --- hints and language


def test_hints_go_to_the_stream_as_hotwords(qwen3, sherpa, speech):
    # The fake refuses create_stream(hotwords=...), as the real recognizer would end the process.
    qwen3.transcribe(speech, hints=["拉斐爾", "OpenRouter", "Codex"])
    assert sherpa.recognizer.decoded[0].options == {"hotwords": "拉斐爾,OpenRouter,Codex"}


@pytest.mark.parametrize(
    ("hints", "hotwords"),
    [
        # A comma would end a word in the list, so it ends the hint's word there.
        (["拉斐爾", "OpenRouter,Codex"], "拉斐爾,OpenRouter,Codex"),
        ([" Codex ", "Open Router"], "Codex,Open Router"),
        (["Codex", "", "  ", ",", " , "], "Codex"),
    ],
)
def test_hints_are_cleaned(hints, hotwords, qwen3, sherpa, speech):
    qwen3.transcribe(speech, hints=hints)
    assert sherpa.recognizer.decoded[0].options == {"hotwords": hotwords}


@pytest.mark.parametrize("hints", [[], ["", " ", ","]])
def test_without_words_there_is_no_hotwords_option(hints, qwen3, sherpa, speech):
    qwen3.transcribe(speech, hints=hints)
    assert sherpa.recognizer.decoded[0].options == {}


def test_only_the_first_twenty_hint_words_are_kept(qwen3, sherpa, speech):
    words = [f"word{i}" for i in range(60)]
    qwen3.transcribe(speech, hints=words)
    assert sherpa.recognizer.decoded[0].options["hotwords"] == ",".join(words[:20])


def ten_characters(count: int) -> list[str]:
    return [f"hintword{i:02d}" for i in range(count)]


def test_hint_text_does_not_pass_200_characters(qwen3, sherpa, speech):
    words = ten_characters(20)  # 19 of them with their commas would be 208 characters
    qwen3.transcribe(speech, hints=words)
    hotwords = sherpa.recognizer.decoded[0].options["hotwords"]
    assert hotwords == ",".join(words[:18])  # 197 characters
    assert len(hotwords) <= 200


@pytest.mark.parametrize(
    ("after", "kept"),
    [
        (["ab"], ["ab"]),  # 200 characters in all: it fits
        (["abc"], []),  # 201: it does not
        (["abc", "ab"], ["ab"]),  # the one that does not fit is skipped, a shorter one still does
        (["abc", "ab", "c"], ["ab"]),  # and then nothing more fits
    ],
    ids=["200", "201", "201 then 200", "201 then 200 then more"],
)
def test_the_200_character_limit_is_exact(after, kept, qwen3, sherpa, speech):
    first = ten_characters(18)  # 197 characters
    qwen3.transcribe(speech, hints=[*first, *after])
    hotwords = sherpa.recognizer.decoded[0].options["hotwords"]
    assert hotwords == ",".join([*first, *kept])
    assert len(hotwords) <= 200


@pytest.mark.parametrize(
    ("hints", "hotwords"),
    [
        # An entry too long for what is left is skipped, and the later words are still sent.
        (["x" * 500, "Codex", "Tauri"], "Codex,Tauri"),
        (["Codex", "x" * 500, "Tauri"], "Codex,Tauri"),
        (["Codex", "x" * 201, "Tauri"], "Codex,Tauri"),
        (["Codex", "Tauri", "x" * 500], "Codex,Tauri"),
        (["x" * 200, "Tauri"], "x" * 200),
    ],
    ids=["first", "middle", "201 characters", "last", "200 characters"],
)
def test_one_very_long_entry_cannot_take_the_prompt(hints, hotwords, qwen3, sherpa, speech):
    qwen3.transcribe(speech, hints=hints)
    assert sherpa.recognizer.decoded[0].options == {"hotwords": hotwords}


def test_only_very_long_entries_leave_no_hints(qwen3, sherpa, speech):
    qwen3.transcribe(speech, hints=["x" * 500, "y" * 300])
    assert sherpa.recognizer.decoded[0].options == {}


def test_a_skipped_entry_does_not_count_towards_the_twenty_words(qwen3, sherpa, speech):
    words = [f"w{i}" for i in range(30)]
    qwen3.transcribe(speech, hints=["x" * 500, *words])
    assert sherpa.recognizer.decoded[0].options["hotwords"] == ",".join(words[:20])


@pytest.mark.parametrize("code", LANGUAGE_NAMES)
def test_language_goes_to_the_stream_by_name(code, qwen3, sherpa, speech):
    qwen3.transcribe(speech, language=code)
    assert sherpa.recognizer.decoded[0].options == {"language": LANGUAGE_NAMES[code]}


def test_auto_language_sets_nothing(qwen3, sherpa, speech):
    qwen3.transcribe(speech, language="auto")
    assert sherpa.recognizer.decoded[0].options == {}


def test_unknown_language_is_refused(qwen3, sherpa, speech):
    with pytest.raises(ValueError, match="xx"):
        qwen3.transcribe(speech, language="xx")
    assert sherpa.recognizer.created == 0


@pytest.mark.parametrize("name", ["sensevoice", "breeze-asr-25"])
def test_other_engines_ignore_hints_and_language(name, tmp_path, sherpa, speech):
    install(tmp_path, name)
    engine = create_engine(name, ModelManager(tmp_path))
    engine.transcribe(speech, hints=["拉斐爾"], language="zh")
    assert sherpa.recognizer.decoded[0].options == {}


# --- several threads


def test_decodings_run_one_at_a_time(tmp_path, sherpa, speech):
    sherpa.recognizer = SlowRecognizer()
    engine = Qwen3AsrEngine(install(tmp_path, "qwen3-asr"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        texts = list(pool.map(lambda _: engine.transcribe(speech), range(4)))
    assert texts == ["hello"] * 4
    assert sherpa.recognizer.most_running == 1


# --- read_wav


def write_wav(path: Path, frames: bytes, *, rate=16_000, width=2, channels=1) -> Path:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return path


def tone(hz: float, rate: int, seconds: float = 1.0, amplitude: float = 0.5) -> np.ndarray:
    return amplitude * np.sin(2 * np.pi * hz * np.arange(round(rate * seconds)) / rate)


def test_read_wav_gives_float32_samples_in_range(tmp_path):
    pcm = np.array([0, 16384, -16384, 32767, -32768], dtype="<i2")
    samples = read_wav(write_wav(tmp_path / "a.wav", pcm.tobytes()))
    assert samples.dtype == np.float32
    assert samples.tolist() == [0.0, 0.5, -0.5, 32767 / 32768, -1.0]


def test_read_wav_of_no_samples(tmp_path):
    assert read_wav(write_wav(tmp_path / "a.wav", b"")).shape == (0,)


@pytest.mark.parametrize("rate", [8_000, 22_050, 44_100, 48_000])
def test_read_wav_resamples_to_16k(rate, tmp_path):
    pcm = np.round(tone(440, rate) * 32768).astype("<i2")
    samples = read_wav(write_wav(tmp_path / "a.wav", pcm.tobytes(), rate=rate))
    assert samples.dtype == np.float32
    assert samples.shape == (16_000,)
    # 440 cycles in the second, so the same tone at 16 kHz, with nothing lost or added on the way.
    assert np.max(np.abs(samples - tone(440, 16_000))) < 1e-3


def test_resampling_keeps_what_is_above_8_khz_out(tmp_path):
    # Dropping samples without a filter would fold this tone down to 4 kHz.
    pcm = np.round(tone(12_000, 48_000) * 32768).astype("<i2")
    samples = read_wav(write_wav(tmp_path / "a.wav", pcm.tobytes(), rate=48_000))
    assert np.max(np.abs(samples)) < 1e-3


def test_resampling_keeps_the_length_in_seconds(tmp_path):
    pcm = np.zeros(44_100 * 3 + 17, dtype="<i2")  # a length that does not divide evenly
    samples = read_wav(write_wav(tmp_path / "a.wav", pcm.tobytes(), rate=44_100))
    assert abs(samples.size / 16_000 - pcm.size / 44_100) < 1 / 16_000


@pytest.mark.parametrize(
    "layout",
    [{"channels": 2}, {"width": 1}, {"width": 3}, {"width": 4}],
    ids=["stereo", "8-bit", "24-bit", "32-bit"],
)
def test_read_wav_refuses_other_layouts(layout, tmp_path):
    path = write_wav(tmp_path / "a.wav", bytes(24), **layout)
    with pytest.raises(ValueError, match="16-bit PCM mono"):
        read_wav(path)


def float_wav(path: Path) -> Path:
    """A WAV of 32-bit float samples (format tag 3), which is not PCM."""
    data = np.zeros(4, dtype="<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, 1, 16_000, 64_000, 4, 32)
    chunks = (
        b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    )
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    return path


@pytest.mark.parametrize("content", [b"", b"this is not a WAV file at all"], ids=["empty", "text"])
def test_read_wav_refuses_what_is_not_a_wav(content, tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="not a WAV file"):
        read_wav(path)


def test_read_wav_refuses_audio_that_is_not_pcm(tmp_path):
    with pytest.raises(ValueError, match="not a WAV file"):
        read_wav(float_wav(tmp_path / "a.wav"))


def test_read_wav_of_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_wav(tmp_path / "nope.wav")


# --- the real models (RAPHAEL_TEST_MODELS=1)


@pytest.mark.models
def test_qwen3_transcribes_with_hints():
    models = ModelManager(models_dir())
    eng = create_engine("qwen3-asr", models)
    wav = read_wav(models.dir("qwen3-asr") / "test_wavs" / "raokouling.wav")
    text = eng.transcribe(wav, hints=["拉斐爾", "OpenRouter,Codex"])  # must not crash the process
    assert len(text) > 5


@pytest.mark.models
def test_sensevoice_transcribes_japanese():
    models = ModelManager(models_dir())
    text = create_engine("sensevoice", models).transcribe(
        read_wav(models.dir("sensevoice") / "test_wavs" / "ja.wav")
    )
    assert any(0x3040 <= ord(ch) <= 0x30FF for ch in text)  # some kana


@pytest.mark.models
def test_read_wav_keeps_the_length_of_a_test_recording():
    # raokouling.wav is recorded at 44.1 kHz, so read_wav has to resample it to 16 kHz.
    wav = ModelManager(models_dir()).dir("qwen3-asr") / "test_wavs" / "raokouling.wav"
    with wave.open(str(wav), "rb") as reader:
        seconds = reader.getnframes() / reader.getframerate()
    assert abs(read_wav(wav).size / 16_000 - seconds) < 1 / 16_000
