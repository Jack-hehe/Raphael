"""A voice message as text: decode the audio, find the speech, hand it to a speech engine.

Voice messages come as Ogg/Opus, which libsndfile (through soundfile) reads from memory; nothing is
written to disk. The speech is found by a segmenter of its own: the one that dictation uses is not
safe to share with it. The engine is whichever one dictation would use, so a cloud engine falls back
to the local one by itself.

Runs on a worker thread (`asyncio.to_thread`); the audio and the text are never logged.
"""

import io
from collections.abc import Sequence
from typing import Any

import numpy as np

from ..device.dictation import join_segments
from .core import MAX_VOICE_SECONDS, VoiceTooLong

SAMPLE_RATE = 16_000
# The length the sender's app says may be a little off.
_SLACK_S = 5
# A voice message is mono or stereo; more channels are refused. The decoded audio may take this much.
MAX_CHANNELS = 2
MAX_DECODED_BYTES = 128 * 1024 * 1024


def decode(data: bytes) -> np.ndarray:
    """The audio as mono float32 at 16 kHz. VoiceTooLong when it is longer than a message may be.
    Anything libsndfile cannot read raises (soundfile.LibsndfileError)."""
    import soundfile
    import soxr

    with soundfile.SoundFile(io.BytesIO(data)) as clip:
        rate = clip.samplerate
        # What reading allocates is frames x channels x 4 bytes: bound it before reading.
        if (
            clip.channels > MAX_CHANNELS
            or clip.frames > rate * (MAX_VOICE_SECONDS + _SLACK_S)
            or clip.frames * clip.channels * 4 > MAX_DECODED_BYTES
        ):
            raise VoiceTooLong
        audio = clip.read(dtype="float32", always_2d=True)
    mono = np.ascontiguousarray(audio.mean(axis=1), dtype=np.float32)
    if rate != SAMPLE_RATE:
        mono = soxr.resample(mono, rate, SAMPLE_RATE).astype(np.float32, copy=False)
    return mono


def transcribe_clip(
    samples: np.ndarray, engine: Any, segmenter: Any, hints: Sequence[str] = ()
) -> str:
    """The words in `samples` (16 kHz mono): the speech the segmenter finds, each stretch through
    the engine, joined. Empty when there is no speech."""
    segments = [*segmenter.accept(samples), *segmenter.flush()]
    texts = [str(engine.transcribe(segment, hints=list(hints))).strip() for segment in segments]
    return join_segments(texts)


# A 0.3 s tone of 440 Hz in Ogg/Opus (48 kHz, mono), made by ffmpeg with libopus: what the packaged
# core decodes to show that its copy of libsndfile carries the Opus codec (packaging/smoke.py).
_SELF_CHECK_CLIP = (
    "T2dnUwACAAAAAAAAAAAPzr09AAAAAPiyHtwBE09wdXNIZWFkAQE4AYC7AAAAAABPZ2dTAAAAAAAAAAAAAA/OvT0BAAAAf9xzlwE9T3B1c1RhZ3MM"
    "AAAATGF2ZjYxLjcuMTAwAQAAAB0AAABlbmNvZGVyPUxhdmM2MS4xOS4xMDEgbGlib3B1c09nZ1MABHg5AAAAAAAAD869PQIAAADROHzREBgaExMS"
    "ExYSERMVEhITFQ8IguI0RUVYmrXIAta13jPFojARNHYlQIAIo0Dn/y/8jzMkk0Un6n0z85S6Rwr/yDGY1AidSG8MidvU3uDhQ0RC1F1E2LIInJAr"
    "405aeiaTDNcBCbiAZ3bLCJyQK+NOWngTuMy2NK0+bU0wCJyQK+NLm0xeYbfB5lvvmfY8DAickCtYcM5qqBUdod52Snea7poPM4AInJAr405aeuSk"
    "KkITwq5aETAInJAr405aeyahpwKW+bGYPAickCvjTlp6+wg2DgmhB9/MXWwInJAr405aewSWZYVyqdZO7cU0l4AInJAr405aeBF+xhPZnno6JbAIn"
    "JAr405aevqu/suHmgS4gyIInJAr405aeByZn4DZJ4fn8JOhCJyQK+NOWnr6/Nvs4to3lfvMGapACAYmzYJhnP5DbwYxHKTQ"
)


def self_check() -> int:
    """Decode the clip above and say whether it came out as a tone of the right length: the exit
    status of `raphael-core --check-voice-decoder`."""
    import base64

    samples = decode(base64.b64decode(_SELF_CHECK_CLIP))
    ok = abs(samples.size - int(0.3 * SAMPLE_RATE)) <= 200 and float(np.abs(samples).max()) > 0.05
    print("voice decoder ok" if ok else "voice decoder failed")
    return 0 if ok else 1
