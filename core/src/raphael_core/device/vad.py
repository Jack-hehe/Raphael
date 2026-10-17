"""Cutting a stream of audio into stretches of speech with Silero VAD on sherpa-onnx.

Dictation hears the microphone for as long as the key is down, and the speech-to-text engines take
no more than 20 seconds at a time (Task 10). A `Segmenter` is fed the audio as it arrives, in
pieces of any size, and gives back the stretches that hold speech, each ready to transcribe.

sherpa-onnx ends the whole process, with no exception to catch, when it is given a model path that
does not exist, so the file is checked before the detector is built.
"""

import logging
from pathlib import Path

import numpy as np
import sherpa_onnx

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

# Silero VAD v5 takes windows of exactly 512 samples (32 ms) and nothing else.
_WINDOW = 512
# Speech shorter than this is a click or a cough, not a word.
_MIN_SPEECH_S = 0.25
# How much audio the detector holds on to while it waits for speech to end.
_BUFFER_S = 120


class Segmenter:
    """Finds the speech in audio that arrives bit by bit.

    `accept` takes the next frames (1-D float32 at 16 kHz, any number of them) and returns the
    stretches of speech that have ended since; `flush` ends the one that is still going, at the
    end of the recording. A stretch ends when `min_silence_s` of quiet follows it. None is longer
    than `max_segment_s`. After `flush` the segmenter starts afresh and can be used again, one
    recording after another.

    `speech_seconds` is the total length of the speech found in the current recording. It can still
    be read after `flush`; the first `accept` of the next recording sets it back to zero.

    Not safe to call from several threads at once.
    """

    def __init__(
        self,
        model_path: Path,
        *,
        min_silence_s: float = 0.5,
        max_segment_s: float = 20.0,
        threshold: float = 0.5,
    ) -> None:
        model_path = Path(model_path)
        if not model_path.is_file():
            raise FileNotFoundError(f"The voice activity model is not downloaded: {model_path}")
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model_path)
        config.silero_vad.threshold = threshold
        config.silero_vad.min_silence_duration = min_silence_s
        config.silero_vad.min_speech_duration = _MIN_SPEECH_S
        config.silero_vad.window_size = _WINDOW
        # Only makes the detector stricter as a stretch gets long, so it is not a limit: see _split.
        config.silero_vad.max_speech_duration = max_segment_s
        config.sample_rate = SAMPLE_RATE
        self._vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=_BUFFER_S)
        self._max_samples = max(1, int(max_segment_s * SAMPLE_RATE))
        self._rest = np.zeros(0, dtype=np.float32)  # the end of the audio, short of a window
        self._flushed = False  # a recording has ended, and no new one has begun
        self.speech_seconds = 0.0

    def accept(self, frames: np.ndarray) -> list[np.ndarray]:
        """Take the next frames; the segments of speech that are now complete, oldest first."""
        frames = np.asarray(frames, dtype=np.float32)
        if frames.ndim != 1:
            raise ValueError(f"Expected 1-D frames, not {frames.ndim}-D")
        if self._flushed:
            self._flushed = False
            self.speech_seconds = 0.0
        audio = np.concatenate([self._rest, frames]) if self._rest.size else frames
        whole = audio.size - audio.size % _WINDOW
        segments: list[np.ndarray] = []
        for start in range(0, whole, _WINDOW):
            self._vad.accept_waveform(audio[start : start + _WINDOW])
            segments += self._drain()
        self._rest = audio[whole:].copy()  # a copy: `frames` is the caller's to reuse
        return segments

    def flush(self) -> list[np.ndarray]:
        """The end of the recording: the speech that was still going on, if any."""
        if self._rest.size:
            window = np.zeros(_WINDOW, dtype=np.float32)
            window[: self._rest.size] = self._rest
            self._vad.accept_waveform(window)
            self._rest = np.zeros(0, dtype=np.float32)
        self._vad.flush()
        segments = self._drain()
        self._vad.reset()  # flush leaves the model's memory of the sound it just heard
        self._flushed = True
        return segments

    def _drain(self) -> list[np.ndarray]:
        segments: list[np.ndarray] = []
        while not self._vad.empty():
            samples = np.asarray(self._vad.front.samples, dtype=np.float32)
            self._vad.pop()
            if samples.size == 0:
                continue
            self.speech_seconds += samples.size / SAMPLE_RATE
            segments += self._split(samples)
        return segments

    def _split(self, samples: np.ndarray) -> list[np.ndarray]:
        """`samples` in as few equal pieces as keep each within the limit.

        Equal, so a stretch of 21 seconds is two of 10.5 and not one of 20 and one of 1. They are
        cut where the count falls, not at a pause, which is why this is the last resort: the
        detector's own limit usually ends a stretch at a quiet moment before it comes to this.
        """
        count = -(-samples.size // self._max_samples)  # ceiling
        if count <= 1:
            return [samples]
        return list(np.array_split(samples, count))
