"""Capturing sound: the microphone, or a WAV file standing in for it.

A recorder hands 1-D float32 frames at 16 kHz mono, the format the speech models take, to the
function it is given. `MicRecorder` records from a microphone. `WavRecorder` plays a file back the
same way; the app uses it in place of the microphone when RAPHAEL_FAKE_AUDIO names a file, which is
how the end-to-end runs speak without anyone speaking.

The frames reach `on_frames` on the thread that captures them: PortAudio's callback thread for the
microphone, a thread of its own for a file. `on_frames` must be quick and must not call `stop()`
(PortAudio waits for the callback to return before it stops a stream, so a callback that stops its
own stream waits for itself).

Which microphone: the device list is that of PortAudio's default host API, MME on Windows. MME
resamples through Windows, so a microphone opens at 16 kHz whatever rate it works at; WASAPI shared
mode would not, and lists every microphone once per host API besides.

PortAudio keeps the list of devices, and which of them is the default, as they were when it started.
So a recorder follows a change of the Windows default microphone, or a headset plugged in since,
only by having PortAudio read the devices again (`_read_devices_again`), which takes about 35 ms and
is possible only while none of our streams is open.
"""

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import sounddevice as sd
import soxr

from raphael_core.device.stt import read_wav

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BLOCK_SECONDS = 0.02
BLOCK_SAMPLES = round(SAMPLE_RATE * BLOCK_SECONDS)

OnFrames = Callable[[np.ndarray], None]

# The entry Windows adds ahead of the real devices: "whichever device is the default". The list of
# microphones leaves it out, since the default microphone is already in it under its own name.
_SOUND_MAPPER = "Microsoft Sound Mapper"

# The sound level that rms_level maps to 0 and to 1 (dB relative to full scale).
_QUIET_DB = -56.0
_LOUD_DB = -16.0


class NoMicrophoneError(Exception):
    """There is no microphone to record from, or it cannot be opened, or it went away."""


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    is_default: bool


class Recorder(Protocol):
    """Something that delivers frames to a function until it is stopped.

    `error` is None until the recorder fails on its own, after it has started (the microphone is
    unplugged, say, or `on_frames` raised); `start()` clears it. `start()` raises NoMicrophoneError
    when the recording cannot begin. `stop()` is safe to call at any time and any number of times.
    """

    error: Exception | None

    def start(self) -> None: ...

    def stop(self) -> None: ...


def rms(frames: np.ndarray) -> float:
    """The root-mean-square amplitude of `frames` (1.0 is full scale); 0 for no samples."""
    if frames.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frames, dtype=np.float64))))


def rms_level(frames: np.ndarray) -> float:
    """How loud `frames` are, from 0 (quiet) to 1 (loud), for the ring that shows the level.

    The root-mean-square level in dB (relative to full scale), -56 dB or less being 0 and -16 dB or
    more being 1, in a straight line between.
    """
    if frames.size == 0:
        return 0.0
    level = (20 * math.log10(rms(frames) + 1e-8) - _QUIET_DB) / (_LOUD_DB - _QUIET_DB)
    return min(max(level, 0.0), 1.0)


def resample_block(
    stream: soxr.ResampleStream, block: np.ndarray, last: bool = False
) -> np.ndarray:
    """The next piece of a recording in the stream's new rate, as 1-D float32.

    Give `last=True` with the final (possibly empty) block to get the samples the resampler still
    holds back; about 20 ms of them.
    """
    out = stream.resample_chunk(np.ascontiguousarray(block, dtype=np.float32), last=last)
    return np.asarray(out, dtype=np.float32).reshape(-1)


def _input_infos() -> tuple[list[dict], int]:
    """sounddevice's descriptions of the default host API's inputs, and the index of its default."""
    try:
        api = sd.query_hostapis(sd.default.hostapi)
        infos = [sd.query_devices(index) for index in api["devices"]]
        default = api["default_input_device"]
    except sd.PortAudioError:
        logger.warning("PortAudio could not list the audio devices", exc_info=True)
        return [], -1
    return [info for info in infos if info["max_input_channels"] > 0], default


def list_input_devices() -> list[InputDevice]:
    """The microphones of the default host API, each once; MicRecorder takes their names.

    The devices are read again first, so a headset plugged in since shows up; not while a stream
    is open (see `_read_devices_again`), when the list is the one PortAudio has.
    """
    _read_devices_again()
    infos, default = _input_infos()
    return [
        InputDevice(index=info["index"], name=info["name"], is_default=info["index"] == default)
        for info in infos
        if not info["name"].startswith(_SOUND_MAPPER)
    ]


def _has_device(name: str) -> bool:
    """Whether PortAudio's list has a microphone called `name`."""
    return any(info["name"] == name for info in _input_infos()[0])


def _pick_device(name: str | None) -> tuple[dict, bool]:
    """The description of the microphone called `name`, or of the system default for None, and
    whether it is the one called `name`. A name that is not there gives the default."""
    infos, default = _input_infos()
    if name is not None:
        for info in infos:
            if info["name"] == name:
                return info, True
        logger.warning("The microphone %r is not there; using the system default", name)
    for info in infos:
        if info["index"] == default:
            return info, False
    raise NoMicrophoneError("There is no microphone: Windows has no default recording device")


# PortAudio reads the list of devices once, when sounddevice is imported. A microphone plugged in
# since, or a new default microphone, shows only after it is read again, which closes every open
# stream. So a recorder reads it again before it opens, and the list of microphones does, but only
# when none of ours is recording.
_active = 0
_active_lock = threading.Lock()
# Held while a stream is being opened and while the devices are read again, so that neither pulls
# the ground from under the other: a stream that is still opening is not yet counted in `_active`.
# Taken before `_active_lock`, never the other way round.
_devices_lock = threading.Lock()


def _read_devices_again() -> bool:
    """Make PortAudio list the devices anew. False if a stream is open (so it was left alone)."""
    with _devices_lock, _active_lock:
        if _active:
            return False
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            logger.warning("PortAudio could not be restarted", exc_info=True)
            if getattr(sd, "_initialized", 1) != 0:
                return False  # it was never stopped, so nothing was lost
            # Stopped, and not started again: no recorder could ever open a stream. Try once more.
            try:
                sd._initialize()
            except Exception:
                logger.error("PortAudio is stopped and could not be started", exc_info=True)
                return False
    return True


class _Capture:
    """One run of a MicRecorder, from start() to stop(): its stream and its resampler."""

    def __init__(self, resampler: soxr.ResampleStream | None) -> None:
        self.resampler = resampler
        self.stream: sd.InputStream | None = None
        self.stopping = False


class MicRecorder:
    """Records from a microphone: `device` is its name (list_input_devices), None the default.

    A name that is not there falls back to the default microphone, with a warning in the log; the
    microphone someone chose last month may well be unplugged today.

    Every start with no name, "automatic", has PortAudio read the devices again first, so that it
    opens whichever microphone Windows has as its default now; a name that is not in the list gets
    one such reading, in case that microphone was plugged in since. Both are skipped while another
    stream is open. The log says, once per start, which microphone was opened.
    """

    def __init__(self, on_frames: OnFrames, device: str | None = None) -> None:
        self.error: Exception | None = None
        self._on_frames = on_frames
        self._device = device
        self._capture: _Capture | None = None
        self._lock = threading.Lock()  # start() and stop(); the audio callback never takes it

    def start(self) -> None:
        """Open the microphone and begin delivering frames. NoMicrophoneError if that fails.

        Does nothing if it is already recording.
        """
        with self._lock:
            if self._capture is not None:
                return
            self.error = None
            read = False  # whether this start has had the devices read again already
            if self._device is None or not _has_device(self._device):
                _read_devices_again()
                read = True
            try:
                capture = self._open()
            except NoMicrophoneError:
                if read or not _read_devices_again():
                    raise
                capture = self._open()
            self._capture = capture

    def stop(self) -> None:
        """Stop recording. The last frames reach `on_frames` before this returns."""
        global _active
        with self._lock:
            capture, self._capture = self._capture, None
        if capture is None:
            return
        capture.stopping = True
        assert capture.stream is not None
        stopped = True
        try:
            capture.stream.stop()
        except Exception:
            stopped = False
            logger.warning("The microphone stream did not stop cleanly", exc_info=True)
        try:
            capture.stream.close()
        except Exception:
            logger.warning("The microphone stream did not close cleanly", exc_info=True)
        with _active_lock:
            _active -= 1
        # A stream that would not stop may still be in its callback, which uses the resampler.
        if stopped and capture.resampler is not None and self.error is None:
            try:
                tail = resample_block(capture.resampler, np.zeros(0, np.float32), last=True)
                if tail.size:
                    self._on_frames(tail)
            except Exception as exc:
                logger.exception("The receiver of the microphone frames failed")
                self.error = exc

    def _open(self) -> _Capture:
        with _devices_lock:
            return self._open_stream()

    def _open_stream(self) -> _Capture:
        global _active
        stream = None
        try:
            info, chosen = _pick_device(self._device)
            index = info["index"]
            rate = SAMPLE_RATE
            try:
                sd.check_input_settings(
                    device=index, channels=1, dtype="float32", samplerate=SAMPLE_RATE
                )
            except (sd.PortAudioError, ValueError):
                rate = int(info["default_samplerate"])
            resampler = (
                None
                if rate == SAMPLE_RATE
                else soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32")
            )
            capture = _Capture(resampler)
            stream = sd.InputStream(
                samplerate=rate,
                channels=1,
                dtype="float32",
                device=index,
                blocksize=rate // 50,  # 20 ms, so the level meter moves 50 times a second
                latency="low",
                callback=lambda indata, frames, time_info, status: self._deliver(
                    capture, indata, status
                ),
                finished_callback=lambda: self._finished(capture),
            )
            capture.stream = stream
            stream.start()
        except NoMicrophoneError:
            raise
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    logger.warning("The microphone stream did not close cleanly", exc_info=True)
            raise NoMicrophoneError(f"The microphone could not be opened: {exc}") from exc
        with _active_lock:
            _active += 1
        logger.info("Microphone: %s (%s)", info["name"], "chosen" if chosen else "automatic")
        return capture

    def _deliver(self, capture: _Capture, indata: np.ndarray, status) -> None:
        """PortAudio's callback: copy, resample, pass on. It must not wait for anything."""
        if status:
            logger.debug("PortAudio reported: %s", status)  # an overflow means lost sound
        try:
            block = indata[:, 0].copy()  # PortAudio reuses its buffer once this returns
            if capture.resampler is not None:
                block = resample_block(capture.resampler, block)
            if block.size:
                self._on_frames(block)
        except Exception as exc:
            logger.exception("The receiver of the microphone frames failed")
            if self.error is None:
                self.error = exc
            raise sd.CallbackAbort from exc

    def _finished(self, capture: _Capture) -> None:
        """PortAudio's call when the stream ends: unplugged unless stop() ended it."""
        if not capture.stopping and self.error is None:
            self.error = NoMicrophoneError("The microphone stopped: was it unplugged?")


class WavRecorder:
    """Plays a 16-bit mono WAV file back as if it were being recorded, in blocks of 20 ms.

    With `realtime` the blocks arrive at the pace of speech, from a thread of their own, and
    `stop()` cuts the playback short; the recorder says nothing more once the file is done. Without
    it the whole file is delivered inside `start()` at once, for tests.
    """

    def __init__(self, on_frames: OnFrames, path: Path, realtime: bool = True) -> None:
        self.error: Exception | None = None
        self._on_frames = on_frames
        self._path = Path(path)
        self._realtime = realtime
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()  # on_frames may stop() the playback it is called from

    def start(self) -> None:
        """Begin delivering the file. NoMicrophoneError if it cannot be read. Does nothing if
        playback is already under way."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            try:
                samples = read_wav(self._path)
            except (OSError, ValueError) as exc:
                raise NoMicrophoneError(
                    f"The audio file {self._path} cannot be read: {exc}"
                ) from exc
            self.error = None
            self._stop_event = threading.Event()
            if not self._realtime:
                self._play(samples, self._stop_event)
                return
            self._thread = threading.Thread(
                target=self._play,
                args=(samples, self._stop_event),
                name="raphael-wav-recorder",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """End the playback; nothing is delivered after this returns."""
        with self._lock:
            thread, self._thread = self._thread, None
        self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def _play(self, samples: np.ndarray, stop_event: threading.Event) -> None:
        due = time.monotonic()
        try:
            for start in range(0, samples.size, BLOCK_SAMPLES):
                if stop_event.is_set():
                    return
                self._on_frames(samples[start : start + BLOCK_SAMPLES].copy())
                if self._realtime:
                    due += BLOCK_SECONDS
                    if stop_event.wait(max(0.0, due - time.monotonic())):
                        return
        except Exception as exc:
            logger.exception("The receiver of the audio file's frames failed")
            self.error = exc
