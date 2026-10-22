"""A voice message as text: decode the audio, find the speech, hand it to a speech engine.

Voice messages come as Ogg/Opus, which libsndfile (through soundfile) reads from memory; nothing is
written to disk. A message has no limit of length, so it is never decoded whole: a few seconds at a
time are decoded, made mono and 16 kHz, and given to the segmenter, and each stretch of speech it
finds is transcribed as soon as it ends. What is held is the downloaded file (`MAX_VOICE_BYTES` at
most), one block, the stretch under way and the words so far. The speech is found by a segmenter of
its own: the one that dictation uses is not safe to share with it. The engine is whichever one
dictation would use.

Runs on a worker thread (`asyncio.to_thread`); the audio and the text are never logged.
"""

import io
from collections.abc import Iterator, Sequence
from typing import Any

import numpy as np

from ..textjoin import join_segments
from .core import VoiceTooLarge

SAMPLE_RATE = 16_000
# A voice message is mono or stereo; more channels are refused before anything is read.
MAX_CHANNELS = 2
# How much is decoded at a time: at 48 kHz in stereo, under 4 MB.
BLOCK_SECONDS = 10


def decoded_blocks(data: bytes) -> Iterator[np.ndarray]:
    """The audio as mono float32 at 16 kHz, `BLOCK_SECONDS` at a time. VoiceTooLarge when it has
    more than two channels. Anything libsndfile cannot read raises (soundfile.LibsndfileError)."""
    import soundfile
    import soxr

    with soundfile.SoundFile(io.BytesIO(data)) as clip:
        if clip.channels > MAX_CHANNELS:
            raise VoiceTooLarge
        rate = clip.samplerate
        resampler = (
            None if rate == SAMPLE_RATE else soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32")
        )
        for block in clip.blocks(blocksize=rate * BLOCK_SECONDS, dtype="float32", always_2d=True):
            mono = np.ascontiguousarray(block.mean(axis=1), dtype=np.float32)
            if resampler is not None:
                mono = resampler.resample_chunk(mono)
            if mono.size:
                yield mono
        if resampler is not None:
            rest = resampler.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
            if rest.size:
                yield rest


def decode(data: bytes) -> np.ndarray:
    """The whole audio as mono float32 at 16 kHz, for a clip known to be short (the self-check)."""
    blocks = list(decoded_blocks(data))
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def transcribe_voice(data: bytes, engine: Any, segmenter: Any, hints: Sequence[str] = ()) -> str:
    """The words of a voice message (`data`, as downloaded): the speech the segmenter finds, each
    stretch through the engine as soon as it ends, joined. Empty when there is no speech."""
    texts: list[str] = []

    def hear(segments: Sequence[np.ndarray]) -> None:
        for segment in segments:
            texts.append(str(engine.transcribe(segment, hints=list(hints))).strip())

    for block in decoded_blocks(data):
        hear(segmenter.accept(block))
    hear(segmenter.flush())
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
