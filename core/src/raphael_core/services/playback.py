"""Playing Raphael's voice: a decoded audio file through the default output device.

`SoundDevicePlayer.play` decodes the bytes of an audio file with soundfile (libsndfile 1.1 and
later read MP3; the one the core ships is 1.2.2, and `decode` is tested on a real MP3) and writes
them to a PortAudio output stream a block at a time. It blocks until the sound has been played or
the caller's `stopped` event is set: the event is looked at before each block (about 50 ms of
sound), and when it is set the stream is aborted, so the buffered sound is thrown away and
silence follows within a block (the last block's own buffer, about a tenth of a second, is waited
for once the whole file has been written). It is meant for a worker thread, never the event loop.

One utterance plays at a time: a lock makes a second `play` wait until the first has let go of the
device (which it does within a block of its `stopped` being set). PortAudio is imported when the
player is made, by then already loaded by the microphone (`device.audio`), and numpy when
something plays: the core answers its first calls before the speech libraries are in, and nothing
imported with `app` may load them (see there). The audio itself is never written to disk or
logged.
"""

import io
import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np

# Frames written to the stream at a time: the longest the sound goes on after `stopped` is set.
BLOCK_SECONDS = 0.05


class Player(Protocol):
    """Something that plays an audio file. `play` blocks until it is over, or `stopped` is set."""

    def play(self, data: bytes, stopped: threading.Event) -> None: ...


def decode(data: bytes) -> "tuple[np.ndarray, int]":
    """The audio file `data` as float32 samples of shape (frames, channels), and the sample rate.
    Anything libsndfile cannot read raises (soundfile.LibsndfileError)."""
    import soundfile

    samples, rate = soundfile.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return samples, int(rate)


class SoundDevicePlayer:
    """The default output device, through PortAudio (sounddevice)."""

    def __init__(self) -> None:
        import sounddevice

        self._sd = sounddevice
        self._lock = threading.Lock()

    def play(self, data: bytes, stopped: threading.Event) -> None:
        import numpy as np

        samples, rate = decode(data)
        if samples.shape[0] == 0 or stopped.is_set():
            return
        block = max(1, int(rate * BLOCK_SECONDS))
        with self._lock:
            if stopped.is_set():  # replaced while it waited for the device
                return
            with self._sd.OutputStream(
                samplerate=rate, channels=samples.shape[1], dtype="float32"
            ) as stream:
                for start in range(0, samples.shape[0], block):
                    if stopped.is_set():
                        stream.abort()
                        return
                    stream.write(np.ascontiguousarray(samples[start : start + block]))
                # Closing a running stream throws its buffers away, so it is stopped first, which
                # waits until the last block has been played.
                stream.stop()
