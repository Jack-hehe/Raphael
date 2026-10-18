"""Speech-to-text on the CPU with sherpa-onnx: three engines behind one interface.

`Qwen3AsrEngine` is the one dictation uses. `SenseVoiceEngine` is smaller and quicker, and
`WhisperOnnxEngine` runs Breeze-ASR-25; those two are there to compare engines on the user's own
recordings. `create_engine` builds the engine a name asks for from the models folder.

Building an engine loads its model, which takes a while, so it is done once; `transcribe` then turns
one segment of speech (20 seconds at most) into text.

sherpa-onnx ends the whole process, with no exception to catch, when it is given a path that does
not exist, and when `create_stream` is given hotwords for Qwen3-ASR or SenseVoice. So every engine
checks its files before it builds the recognizer, and the hints reach the stream through
`set_option`, never through `create_stream`.
"""

import logging
import re
import threading
import time
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
import sherpa_onnx

from raphael_core.device.models import ModelManager

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

# The language Qwen3-ASR is told, by the code of raphael_core.languages (Qwen3-ASR knows all of
# them, Cantonese included).
LANGUAGE_NAMES = {
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

# Qwen3-ASR fits its prompt, the audio (about 13 tokens a second) and the text it writes into
# max_total_len tokens, and cuts off the audio that does not fit. The default of 512 is too little
# for a 20 second segment with hints.
_QWEN3_MAX_TOTAL_LEN = 1024
_QWEN3_MAX_NEW_TOKENS = 384
# Hints take room in the prompt that the audio then lacks, and time: on the development laptop
# (4 threads) 20 words added about 0.2 s to a segment of 5 s and 50 words about 0.8 s. So at most 20
# words, and at most 200 characters in all, or one very long dictionary entry would take the prompt.
_MAX_HINT_WORDS = 20
_MAX_HINT_CHARS = 200
# Qwen3-ASR writes "language Chinese<asr_text>" ahead of the text. sherpa-onnx takes that away, but
# not when it is all the model wrote (for a short burst of noise, say): the marker is then the text.
_QWEN3_MARKUP = re.compile(r"(?:language\s+\w+)?<asr_text>|<\|[^|<>]*\|>")


class SttEngine(Protocol):
    """Turns speech into text. `name` is the name of the engine's model in the registry."""

    name: str

    def transcribe(
        self, samples: np.ndarray, *, hints: Sequence[str] = (), language: str = "auto"
    ) -> str:
        """The text of `samples`: 1-D float32 in [-1, 1] at 16 kHz, 20 seconds at most.

        `hints` are words (the user's dictionary) the model should lean towards writing as given,
        and `language` is "auto" or a key of LANGUAGE_NAMES. An engine that cannot use them
        ignores them. Silence, and no samples at all, give "".
        """
        ...


def _require_files(name: str, directory: Path, files: Sequence[str]) -> None:
    missing = [file for file in files if not (directory / file).exists()]
    if missing:
        raise FileNotFoundError(
            f"The speech model {name} is not downloaded: {directory} lacks {', '.join(missing)}"
        )


def _as_samples(samples: np.ndarray) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim != 1:
        raise ValueError(f"Expected 1-D samples, not {samples.ndim}-D")
    return samples


class _SherpaEngine:
    """What the engines share: a sherpa-onnx recognizer that decodes one segment at a time.

    A subclass names itself, lists the `_files` of its model (relative to the model directory) and
    builds the recognizer in `_build`.
    """

    name: str
    _files: tuple[str, ...]

    def __init__(self, model_dir: Path, num_threads: int = 4) -> None:
        model_dir = Path(model_dir)
        _require_files(self.name, model_dir, self._files)
        started = time.perf_counter()
        self._recognizer = self._build(model_dir, num_threads)
        # sherpa-onnx decodes outside the GIL, so two threads would really be inside the recognizer
        # together, and nothing promises that it allows that. The dictation worker is one thread,
        # but a caller such as the evaluation may use several.
        self._lock = threading.Lock()
        logger.info(
            "Loaded %s on %d threads in %.1f s",
            self.name,
            num_threads,
            time.perf_counter() - started,
        )

    def _build(self, model_dir: Path, num_threads: int) -> sherpa_onnx.OfflineRecognizer:
        raise NotImplementedError

    def _stream_options(self, hints: Sequence[str], language: str) -> dict[str, str]:
        """What to tell the stream before decoding it. Most engines have no use for hints or a
        language."""
        return {}

    def _text(self, recognized: str) -> str:
        """The text the recognizer wrote, as the engine gives it."""
        return recognized.strip()

    def transcribe(
        self, samples: np.ndarray, *, hints: Sequence[str] = (), language: str = "auto"
    ) -> str:
        samples = _as_samples(samples)
        options = self._stream_options(hints, language)
        if not samples.any():  # no samples, or all of them zero: there is nothing to hear
            return ""
        with self._lock:
            stream = self._recognizer.create_stream()
            for key, value in options.items():
                stream.set_option(key, value)
            stream.accept_waveform(SAMPLE_RATE, samples)
            self._recognizer.decode_stream(stream)
            return self._text(stream.result.text)


def _hotwords(hints: Sequence[str]) -> str:
    """`hints` as the comma-separated list that sherpa-onnx takes for Qwen3-ASR.

    A comma inside a hint would end a word there for sherpa-onnx too, so the hint is split at it.
    Empty words are dropped. The words are taken in order until there are _MAX_HINT_WORDS of them.
    A word that would take the list past _MAX_HINT_CHARS characters (commas included) is skipped,
    and the later words are still tried, so one very long entry does not block the rest. The
    dictionary lists the newest first, so a full list loses the oldest words.
    """
    words = [word.strip() for hint in hints for word in hint.split(",")]
    kept: list[str] = []
    length = -1  # the length of ",".join(kept); the first word has no comma before it
    for word in filter(None, words):
        if len(kept) == _MAX_HINT_WORDS:
            break
        if length + 1 + len(word) > _MAX_HINT_CHARS:
            continue
        kept.append(word)
        length += 1 + len(word)
    return ",".join(kept)


class Qwen3AsrEngine(_SherpaEngine):
    """Qwen3-ASR 0.6B, int8. Uses the hints and the language."""

    name = "qwen3-asr"
    _files = (
        "conv_frontend.onnx",
        "encoder.int8.onnx",
        "decoder.int8.onnx",
        "tokenizer/vocab.json",
        "tokenizer/merges.txt",
        "tokenizer/tokenizer_config.json",
    )

    def _build(self, model_dir: Path, num_threads: int) -> sherpa_onnx.OfflineRecognizer:
        return sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            conv_frontend=str(model_dir / "conv_frontend.onnx"),
            encoder=str(model_dir / "encoder.int8.onnx"),
            decoder=str(model_dir / "decoder.int8.onnx"),
            tokenizer=str(model_dir / "tokenizer"),
            num_threads=num_threads,
            provider="cpu",
            max_total_len=_QWEN3_MAX_TOTAL_LEN,
            max_new_tokens=_QWEN3_MAX_NEW_TOKENS,
        )

    def _stream_options(self, hints: Sequence[str], language: str) -> dict[str, str]:
        options = {}
        hotwords = _hotwords(hints)
        if hotwords:
            options["hotwords"] = hotwords
        if language != "auto":
            if language not in LANGUAGE_NAMES:
                known = ", ".join(["auto", *LANGUAGE_NAMES])
                raise ValueError(f"Unknown language {language!r}: use one of {known}")
            options["language"] = LANGUAGE_NAMES[language]
        return options

    def _text(self, recognized: str) -> str:
        return _QWEN3_MARKUP.sub("", recognized).strip()


class SenseVoiceEngine(_SherpaEngine):
    """SenseVoice-Small, int8. Finds the language itself and ignores the hints."""

    name = "sensevoice"
    _files = ("model.int8.onnx", "tokens.txt")

    def _build(self, model_dir: Path, num_threads: int) -> sherpa_onnx.OfflineRecognizer:
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_dir / "model.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=num_threads,
            provider="cpu",
            language="",
            use_itn=True,
        )


class WhisperOnnxEngine(_SherpaEngine):
    """Breeze-ASR-25 (MediaTek's Whisper for Taiwanese Mandarin), for evaluation only. It takes
    neither hints nor a language."""

    name = "breeze-asr-25"
    _files = (
        "breeze-asr-25-half-encoder.int8.onnx",
        "breeze-asr-25-half-decoder.int8.onnx",
        "breeze-asr-25-half-tokens.txt",
    )

    def _build(self, model_dir: Path, num_threads: int) -> sherpa_onnx.OfflineRecognizer:
        return sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(model_dir / "breeze-asr-25-half-encoder.int8.onnx"),
            decoder=str(model_dir / "breeze-asr-25-half-decoder.int8.onnx"),
            tokens=str(model_dir / "breeze-asr-25-half-tokens.txt"),
            num_threads=num_threads,
            provider="cpu",
            language="zh",
        )


_ENGINES: dict[str, type[_SherpaEngine]] = {
    engine.name: engine for engine in (Qwen3AsrEngine, SenseVoiceEngine, WhisperOnnxEngine)
}


def create_engine(name: str, models: ModelManager, num_threads: int = 4) -> SttEngine:
    """The engine called `name`, loaded from its model in `models`.

    ValueError if there is no such engine; FileNotFoundError if its model is not downloaded.
    """
    try:
        engine = _ENGINES[name]
    except KeyError:
        raise ValueError(f"Unknown speech-to-text engine: {name}") from None
    return engine(models.dir(name), num_threads)


def _resample(samples: np.ndarray, rate: int) -> np.ndarray:
    """`samples` recorded at `rate` Hz, as 16 kHz samples.

    The Fourier method: the spectrum is cut off at the new rate's Nyquist frequency (or padded out
    with zeros, going up) and transformed back. Taking every n-th sample instead would fold what is
    above 8 kHz down into the speech. The transform treats the clip as if it went on repeating, so
    an end that is loud rings a little at the other end.
    """
    count = round(samples.size * SAMPLE_RATE / rate)
    if samples.size == 0 or count == 0:
        return np.zeros(count, dtype=np.float32)
    spectrum = np.fft.rfft(samples)
    resized = np.zeros(count // 2 + 1, dtype=spectrum.dtype)
    kept = min(spectrum.size, resized.size)
    resized[:kept] = spectrum[:kept]
    resampled = np.fft.irfft(resized, count) * (count / samples.size)
    return np.clip(resampled, -1.0, 1.0).astype(np.float32)


def read_wav(path: Path) -> np.ndarray:
    """A 16-bit PCM mono WAV file as float32 samples in [-1, 1] at 16 kHz.

    A file at another sample rate is resampled; some of the test recordings that come with the
    models are at 44.1 kHz. ValueError for anything else that is not 16-bit PCM mono: stereo, other
    sample widths, float or compressed audio, a file that is not a WAV at all.
    """
    try:
        with wave.open(str(path), "rb") as reader:
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            rate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"{path} is not a WAV file that can be read: {exc}") from exc
    if channels != 1 or width != 2 or rate < 1:
        raise ValueError(
            f"{path} is not a 16-bit PCM mono WAV file: it has {width * 8}-bit samples, "
            f"{channels} channels and a rate of {rate} Hz"
        )
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    return samples if rate == SAMPLE_RATE else _resample(samples, rate)
