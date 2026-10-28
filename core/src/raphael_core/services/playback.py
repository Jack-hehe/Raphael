"""Playing Raphael's voice: raw PCM, as it arrives, through the default output device.

`SoundDevicePlayer.play` takes the sound from a queue that another thread fills with the chunks of
the stream (`tts.stream`: 16-bit little-endian mono PCM, no container, so there is nothing to decode
and a chunk is playable the moment it is in) and writes it to a PortAudio output stream a block at
a time (about 50 ms). It blocks until the stream has ended and been played, or the caller's
`stopped` event is set: the event is looked at before each block and while waiting for the next
chunk, and when it is set the stream is aborted, so the buffered sound is thrown away and silence
follows within a block (when the stream ends by itself, the last block's own buffer, about a tenth
of a second, is waited for). It is meant for a worker thread, never the event loop.

The output stream is opened when the first block is ready, not before, and `started` is called then,
just before the first block is written: that is the moment the sound begins, which the speaker
reports to the answer that waits for it (`speaker.Speaker.say`). A chunk may end in the middle of a
16-bit sample; the odd byte waits for the next one. A network slower than the sound leaves a gap
where the device has nothing to play, never an error.

One utterance plays at a time: a lock makes a second `play` wait until the first has let go of the
device (which it does within a block of its `stopped` being set). PortAudio is imported when the
player is made, by then already loaded by the microphone (`device.audio`), and numpy when
something plays: the core answers its first calls before the speech libraries are in, and nothing
imported with `app` may load them (see there). The audio itself is never written to disk or
logged.
"""

import queue
import threading
from collections.abc import Callable
from typing import Protocol

# Frames written to the stream at a time: the longest the sound goes on after `stopped` is set.
BLOCK_SECONDS = 0.05
# How long the player waits for the next chunk before it looks at `stopped` again.
POLL_SECONDS = 0.05
# The width of one sample (16-bit PCM, mono).
_SAMPLE_BYTES = 2


class Player(Protocol):
    """Something that plays a stream of PCM. `play` blocks until the stream (the chunks of bytes
    that `source` gives, ended by a None) has been played, or `stopped` is set. `started` is called,
    from the playing thread, once, when the first sound is about to be heard; it is not called for a
    stream that is stopped or ends before there is anything to play."""

    def play(
        self,
        source: "queue.Queue[bytes | None]",
        rate: int,
        stopped: threading.Event,
        started: Callable[[], None],
    ) -> None: ...


class SoundDevicePlayer:
    """The default output device, through PortAudio (sounddevice)."""

    def __init__(self) -> None:
        import sounddevice

        self._sd = sounddevice
        self._lock = threading.Lock()

    def play(
        self,
        source: "queue.Queue[bytes | None]",
        rate: int,
        stopped: threading.Event,
        started: Callable[[], None],
    ) -> None:
        import numpy as np

        block_bytes = max(1, int(rate * BLOCK_SECONDS)) * _SAMPLE_BYTES
        with self._lock:
            if stopped.is_set():  # replaced while it waited for the device
                return
            pending = bytearray()
            stream = None
            ended = False
            try:
                while not ended:
                    if stopped.is_set():
                        return
                    try:
                        chunk = source.get(timeout=POLL_SECONDS)
                    except queue.Empty:
                        continue
                    if chunk is None:
                        ended = True
                    else:
                        pending += chunk
                    while len(pending) >= block_bytes or (ended and len(pending) >= _SAMPLE_BYTES):
                        if stopped.is_set():
                            return
                        if stream is None:
                            stream = self._sd.OutputStream(
                                samplerate=rate, channels=1, dtype="int16"
                            )
                            stream.start()
                            started()
                        size = min(block_bytes, len(pending) - len(pending) % _SAMPLE_BYTES)
                        samples = np.frombuffer(bytes(pending[:size]), dtype="<i2")
                        del pending[:size]
                        stream.write(samples.reshape(-1, 1))
                if stopped.is_set():
                    return  # stopped as the stream ended: silence now, not after the buffer
                if stream is not None:
                    # Closing a running stream throws its buffers away, so it is stopped first,
                    # which waits until the last block has been played.
                    stream.stop()
            finally:
                if stream is not None:
                    if stopped.is_set():
                        stream.abort()
                    stream.close()
