"""Tests for microphone capture.

The microphone tests put a fake `sounddevice` in place of the real one: no test opens a real audio
device, so they run anywhere and never make a sound or record one.
"""

import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import sounddevice
import soxr

from raphael_core.device import audio
from raphael_core.device.audio import (
    SAMPLE_RATE,
    InputDevice,
    MicRecorder,
    NoMicrophoneError,
    WavRecorder,
    list_input_devices,
    resample_block,
    rms_level,
)


def write_test_wav(directory: Path, seconds: float = 1.0, rate: int = SAMPLE_RATE) -> Path:
    """A 16-bit PCM mono sine wave, written with the wave module."""
    count = round(seconds * rate)
    wave_form = 0.5 * np.sin(2 * np.pi * 440 * np.arange(count) / rate)
    path = directory / "tone.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes((wave_form * 32767).astype("<i2").tobytes())
    return path


def wait_for(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


# --- rms_level and resample_block ------------------------------------------------------------


def test_rms_level_mapping():
    assert rms_level(np.zeros(1600, np.float32)) == 0.0
    assert rms_level(np.full(1600, 10 ** (-36 / 20), np.float32)) == pytest.approx(0.5, abs=0.01)
    assert rms_level(np.full(1600, 0.9, np.float32)) == 1.0


def test_rms_level_of_no_samples_is_zero():
    assert rms_level(np.zeros(0, np.float32)) == 0.0


def test_rms_level_is_a_plain_float():
    assert type(rms_level(np.full(160, 0.01, np.float32))) is float


def test_resample_48k_to_16k():
    stream = soxr.ResampleStream(48000, 16000, 1, dtype="float32")
    t = np.arange(48000) / 48000
    out = np.concatenate(
        [
            resample_block(stream, b)
            for b in np.array_split(np.sin(2 * np.pi * 440 * t).astype(np.float32), 50)
        ]
        + [resample_block(stream, np.zeros(0, np.float32), last=True)]
    )
    assert abs(out.size - 16000) <= 32


def test_resample_block_gives_one_dimensional_float32_and_keeps_the_tone():
    stream = soxr.ResampleStream(44100, 16000, 1, dtype="float32")
    t = np.arange(44100) / 44100
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    pieces = [resample_block(stream, b) for b in np.array_split(tone, 100)]
    pieces.append(resample_block(stream, np.zeros(0, np.float32), last=True))
    assert all(p.ndim == 1 and p.dtype == np.float32 for p in pieces)
    out = np.concatenate(pieces)
    assert abs(out.size - 16000) <= 32
    middle = out[4000:12000]
    assert float(np.sqrt(np.mean(middle**2))) == pytest.approx(0.5 / np.sqrt(2), abs=0.02)


# --- WavRecorder -----------------------------------------------------------------------------


def test_wav_recorder_streams_whole_file(tmp_path):
    path = write_test_wav(tmp_path, seconds=1.0)
    got = []
    rec = WavRecorder(got.append, path, realtime=False)
    rec.start()
    rec.stop()
    assert sum(len(x) for x in got) == 16000
    assert rec.error is None


def test_wav_recorder_blocks_are_20_ms_float32_copies_of_the_file(tmp_path):
    path = write_test_wav(tmp_path, seconds=0.105)  # 1680 samples: 5 blocks of 320 and one of 80
    got = []
    rec = WavRecorder(got.append, path, realtime=False)
    rec.start()
    rec.stop()
    assert [len(x) for x in got] == [320] * 5 + [80]
    assert all(x.ndim == 1 and x.dtype == np.float32 for x in got)
    from raphael_core.device.stt import read_wav

    assert np.array_equal(np.concatenate(got), read_wav(path))
    got[0][:] = 0  # a block is the receiver's own: changing it changes nothing else
    assert np.array_equal(np.concatenate(got[1:]), read_wav(path)[320:])


def test_wav_recorder_realtime_delivers_at_the_speed_of_speech(tmp_path):
    path = write_test_wav(tmp_path, seconds=0.2)
    got = []
    rec = WavRecorder(got.append, path)  # realtime is the default
    started = time.monotonic()
    rec.start()
    assert wait_for(lambda: sum(len(x) for x in got) == 3200)
    assert time.monotonic() - started >= 0.15
    rec.stop()


def test_wav_recorder_realtime_start_does_not_wait_for_the_file(tmp_path):
    path = write_test_wav(tmp_path, seconds=2.0)
    got = []
    rec = WavRecorder(got.append, path)
    started = time.monotonic()
    rec.start()
    assert time.monotonic() - started < 0.5
    rec.stop()


def test_wav_recorder_stop_ends_realtime_early(tmp_path):
    path = write_test_wav(tmp_path, seconds=5.0)
    got = []
    rec = WavRecorder(got.append, path)
    rec.start()
    assert wait_for(lambda: len(got) >= 2)
    started = time.monotonic()
    rec.stop()
    assert time.monotonic() - started < 0.5
    delivered = len(got)
    assert delivered < 250
    time.sleep(0.1)
    assert len(got) == delivered  # nothing arrives after stop() has returned
    assert not [t for t in threading.enumerate() if t.name == "raphael-wav-recorder"]


def test_wav_recorder_stop_is_idempotent_and_safe_before_start(tmp_path):
    rec = WavRecorder(lambda frames: None, write_test_wav(tmp_path), realtime=True)
    rec.stop()
    rec.start()
    rec.stop()
    rec.stop()


def test_wav_recorder_resamples_a_file_at_another_rate(tmp_path):
    path = write_test_wav(tmp_path, seconds=1.0, rate=44100)
    got = []
    rec = WavRecorder(got.append, path, realtime=False)
    rec.start()
    rec.stop()
    assert sum(len(x) for x in got) == 16000


def test_wav_recorder_cannot_read_the_file(tmp_path):
    rec = WavRecorder(lambda frames: None, tmp_path / "missing.wav", realtime=False)
    with pytest.raises(NoMicrophoneError, match="missing.wav"):
        rec.start()
    not_a_wav = tmp_path / "bad.wav"
    not_a_wav.write_bytes(b"not a wav file")
    with pytest.raises(NoMicrophoneError):
        WavRecorder(lambda frames: None, not_a_wav, realtime=False).start()


def test_wav_recorder_receiver_that_raises_sets_error_and_ends(tmp_path):
    def broken(frames):
        raise RuntimeError("receiver broke")

    rec = WavRecorder(broken, write_test_wav(tmp_path), realtime=True)
    rec.start()
    assert wait_for(lambda: rec.error is not None)
    assert isinstance(rec.error, RuntimeError)
    rec.stop()


# --- the fake sounddevice --------------------------------------------------------------------


class FakeStream:
    """What sd.InputStream is to the recorder: it can be fed blocks and made to finish."""

    def __init__(self, fake, **kwargs):
        self.fake = fake
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.finished_callback = kwargs["finished_callback"]
        self.started = 0
        self.stops = 0
        self.closes = 0
        self.active = False

    def start(self):
        if self.fake.start_error is not None:
            raise self.fake.start_error
        self.started += 1
        self.active = True

    def stop(self):
        self.stops += 1
        if self.active:
            self.active = False
            self.finished_callback()

    def close(self):
        self.closes += 1

    def feed(self, block):
        """Deliver `block` (1-D) the way PortAudio does: a 2-D view that is reused afterwards."""
        indata = np.asarray(block, dtype=np.float32).reshape(-1, 1)
        return self.callback(indata, indata.shape[0], None, sounddevice.CallbackFlags())

    def unplug(self):
        """The device goes away: the stream finishes without anyone calling stop()."""
        self.active = False
        self.finished_callback()


class FakeSd:
    """A stand-in for the `sounddevice` module with two microphones and some outputs."""

    PortAudioError = sounddevice.PortAudioError
    CallbackAbort = sounddevice.CallbackAbort

    def __init__(self, devices=None, default_input=1):
        self.devices = (
            devices
            if devices is not None
            else [
                self.device(0, "Microsoft Sound Mapper - Input", 2, 44100.0),
                self.device(1, "Headset Mic", 1, 44100.0),
                self.device(2, "Array Mic", 2, 48000.0),
                self.device(3, "Microsoft Sound Mapper - Output", 0, 44100.0),
                self.device(4, "Speakers", 0, 48000.0),
            ]
        )
        self.default_input = default_input
        self.default = SimpleNamespace(hostapi=0)
        self.rates_at_16k = {1: False, 2: False}  # which devices open at 16 kHz as they are
        self.streams: list[FakeStream] = []
        self.checked: list[tuple] = []
        self.start_error = None
        self.open_error = None
        self.refreshes = 0
        self.after_refresh = None

    @staticmethod
    def device(index, name, inputs, rate):
        return {
            "name": name,
            "index": index,
            "hostapi": 0,
            "max_input_channels": inputs,
            "max_output_channels": 0 if inputs else 2,
            "default_samplerate": rate,
        }

    def query_hostapis(self, index=None):
        assert index == 0
        return {
            "name": "MME",
            "devices": [d["index"] for d in self.devices],
            "default_input_device": self.default_input,
        }

    def query_devices(self, device=None):
        assert isinstance(device, int)
        return self.devices[device]

    def check_input_settings(self, device=None, channels=None, dtype=None, samplerate=None, **kw):
        self.checked.append((device, channels, dtype, samplerate))
        if samplerate == SAMPLE_RATE and not self.rates_at_16k.get(device, False):
            raise sounddevice.PortAudioError("Invalid sample rate")

    def InputStream(self, **kwargs):  # noqa: N802 - mirrors sounddevice's class
        if self.open_error is not None:
            raise self.open_error
        stream = FakeStream(self, **kwargs)
        self.streams.append(stream)
        return stream

    def _terminate(self):
        self.refreshes += 1

    def _initialize(self):
        if self.after_refresh is not None:
            self.after_refresh(self)


@pytest.fixture
def fake_sd(monkeypatch):
    fake = FakeSd()
    monkeypatch.setattr(audio, "sd", fake)
    # A test that leaves a recorder running must not stop the next one from looking for devices.
    monkeypatch.setattr(audio, "_active", 0)
    return fake


# --- list_input_devices ----------------------------------------------------------------------


def test_list_input_devices_lists_each_microphone_once(fake_sd):
    assert list_input_devices() == [
        InputDevice(index=1, name="Headset Mic", is_default=True),
        InputDevice(index=2, name="Array Mic", is_default=False),
    ]


def test_list_input_devices_with_no_default_or_no_microphones(fake_sd):
    fake_sd.default_input = -1
    assert [d.is_default for d in list_input_devices()] == [False, False]
    fake_sd.devices = [FakeSd.device(0, "Speakers", 0, 44100.0)]
    assert list_input_devices() == []


def test_list_input_devices_when_audio_is_unavailable(fake_sd):
    def broken(index=None):
        raise sounddevice.PortAudioError("no host API")

    fake_sd.query_hostapis = broken
    assert list_input_devices() == []


# --- MicRecorder -----------------------------------------------------------------------------


def test_mic_recorder_opens_at_16k_when_the_device_takes_it(fake_sd):
    fake_sd.rates_at_16k[1] = True
    got = []
    rec = MicRecorder(got.append)
    rec.start()
    (stream,) = fake_sd.streams
    assert fake_sd.checked[0] == (1, 1, "float32", 16000)
    assert stream.kwargs["samplerate"] == 16000
    assert stream.kwargs["channels"] == 1
    assert stream.kwargs["dtype"] == "float32"
    assert stream.kwargs["device"] == 1
    assert stream.started == 1

    block = np.linspace(-0.5, 0.5, 320, dtype=np.float32)
    stream.feed(block)
    assert len(got) == 1
    assert got[0].ndim == 1 and got[0].dtype == np.float32
    assert np.array_equal(got[0], block)
    rec.stop()
    assert len(got) == 1  # no resampler, so no tail


def test_mic_recorder_hands_over_a_copy_of_the_buffer(fake_sd):
    fake_sd.rates_at_16k[1] = True
    got = []
    rec = MicRecorder(got.append)
    rec.start()
    indata = np.full((320, 1), 0.25, np.float32)
    fake_sd.streams[0].callback(indata, 320, None, None)
    indata[:] = 0.0  # PortAudio reuses its buffer after the callback returns
    assert np.all(got[0] == 0.25)
    rec.stop()


def test_mic_recorder_takes_the_first_channel_of_a_stereo_buffer(fake_sd):
    fake_sd.rates_at_16k[1] = True
    got = []
    rec = MicRecorder(got.append)
    rec.start()
    indata = np.stack([np.full(100, 0.1, np.float32), np.full(100, 0.9, np.float32)], axis=1)
    fake_sd.streams[0].callback(indata, 100, None, None)
    assert np.all(got[0] == np.float32(0.1))
    rec.stop()


def test_mic_recorder_falls_back_to_the_default_rate_and_resamples(fake_sd):
    got = []
    rec = MicRecorder(got.append)
    rec.start()
    (stream,) = fake_sd.streams
    assert stream.kwargs["samplerate"] == 44100
    t = np.arange(44100) / 44100
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    for block in np.array_split(tone, 100):
        stream.feed(block)
    rec.stop()
    assert all(x.ndim == 1 and x.dtype == np.float32 for x in got)
    total = sum(len(x) for x in got)
    assert abs(total - 16000) <= 32  # the resampler's tail is delivered by stop()


def test_mic_recorder_falls_back_when_the_rate_check_gives_another_error(fake_sd):
    def odd(*args, **kwargs):
        raise ValueError("odd")

    fake_sd.check_input_settings = odd
    rec = MicRecorder(lambda frames: None)
    rec.start()
    assert fake_sd.streams[0].kwargs["samplerate"] == 44100
    rec.stop()


def test_mic_recorder_default_device_is_the_system_default(fake_sd):
    MicRecorder(lambda frames: None).start()
    assert fake_sd.streams[0].kwargs["device"] == 1


def test_mic_recorder_picks_a_device_by_name(fake_sd):
    rec = MicRecorder(lambda frames: None, device="Array Mic")
    rec.start()
    assert fake_sd.streams[0].kwargs["device"] == 2
    assert fake_sd.streams[0].kwargs["samplerate"] == 48000
    rec.stop()


def test_mic_recorder_unknown_device_name_uses_the_default(fake_sd, caplog):
    rec = MicRecorder(lambda frames: None, device="Unplugged Mic")
    rec.start()
    assert fake_sd.streams[0].kwargs["device"] == 1
    assert "Unplugged Mic" in caplog.text
    rec.stop()


def test_mic_recorder_no_microphone_raises_after_looking_again(fake_sd):
    fake_sd.devices = [FakeSd.device(0, "Speakers", 0, 44100.0)]
    fake_sd.default_input = -1
    with pytest.raises(NoMicrophoneError):
        MicRecorder(lambda frames: None).start()
    assert fake_sd.refreshes == 1
    assert fake_sd.streams == []


def test_mic_recorder_finds_a_microphone_plugged_in_after_the_first_look(fake_sd):
    fake_sd.devices = [FakeSd.device(0, "Speakers", 0, 44100.0)]
    fake_sd.default_input = -1

    def plug_in(fake):
        fake.devices = [FakeSd.device(0, "New Mic", 1, 44100.0)]
        fake.default_input = 0

    fake_sd.after_refresh = plug_in
    rec = MicRecorder(lambda frames: None)
    rec.start()
    assert fake_sd.streams[0].kwargs["device"] == 0
    rec.stop()


def test_mic_recorder_failure_to_open_raises_no_microphone_error(fake_sd):
    fake_sd.open_error = sounddevice.PortAudioError("Device unavailable")
    with pytest.raises(NoMicrophoneError, match="Device unavailable"):
        MicRecorder(lambda frames: None).start()


def test_mic_recorder_failure_to_start_closes_the_stream(fake_sd):
    fake_sd.start_error = sounddevice.PortAudioError("Blocked by privacy settings")
    with pytest.raises(NoMicrophoneError):
        MicRecorder(lambda frames: None).start()
    assert fake_sd.streams and all(s.closes >= 1 for s in fake_sd.streams)


def test_mic_recorder_does_not_look_again_while_another_recorder_records(fake_sd):
    first = MicRecorder(lambda frames: None)
    first.start()
    fake_sd.open_error = sounddevice.PortAudioError("busy")
    with pytest.raises(NoMicrophoneError):
        MicRecorder(lambda frames: None).start()
    assert fake_sd.refreshes == 0  # re-reading the devices would close the first one's stream
    first.stop()


def test_mic_recorder_stop_is_idempotent(fake_sd):
    rec = MicRecorder(lambda frames: None)
    rec.stop()  # before start: nothing to do
    rec.start()
    rec.stop()
    rec.stop()
    (stream,) = fake_sd.streams
    assert stream.stops == 1
    assert stream.closes == 1
    assert rec.error is None


def test_mic_recorder_stopping_is_not_an_error(fake_sd):
    rec = MicRecorder(lambda frames: None)
    rec.start()
    rec.stop()  # the stream's finished callback runs inside stop()
    assert rec.error is None


def test_mic_recorder_stream_that_finishes_alone_sets_error(fake_sd):
    rec = MicRecorder(lambda frames: None)
    rec.start()
    assert rec.error is None
    fake_sd.streams[0].unplug()
    assert isinstance(rec.error, NoMicrophoneError)
    rec.stop()  # still safe, and the error stays for the caller to read
    assert isinstance(rec.error, NoMicrophoneError)


def test_mic_recorder_can_start_again_after_stop(fake_sd):
    rec = MicRecorder(lambda frames: None)
    rec.start()
    fake_sd.streams[0].unplug()
    rec.stop()
    assert rec.error is not None
    rec.start()
    assert rec.error is None
    assert len(fake_sd.streams) == 2
    rec.stop()
    assert rec.error is None


def test_mic_recorder_start_twice_opens_one_stream(fake_sd):
    rec = MicRecorder(lambda frames: None)
    rec.start()
    rec.start()
    assert len(fake_sd.streams) == 1
    rec.stop()


def test_mic_recorder_late_finish_of_an_old_stream_does_not_poison_the_next(fake_sd):
    rec = MicRecorder(lambda frames: None)
    rec.start()
    old = fake_sd.streams[0]
    rec.stop()
    rec.start()
    old.finished_callback()  # a straggler from the stream that stop() already closed
    assert rec.error is None
    rec.stop()


def test_mic_recorder_receiver_that_raises_aborts_the_stream_and_sets_error(fake_sd):
    fake_sd.rates_at_16k[1] = True

    def broken(frames):
        raise RuntimeError("receiver broke")

    rec = MicRecorder(broken)
    rec.start()
    with pytest.raises(sounddevice.CallbackAbort):
        fake_sd.streams[0].feed(np.zeros(320, np.float32))
    assert isinstance(rec.error, RuntimeError)
    rec.stop()


def test_mic_recorder_callback_never_takes_the_recorders_lock(fake_sd):
    """stop() waits for the callback to finish, so a callback waiting on stop's lock would hang."""
    fake_sd.rates_at_16k[1] = True
    got = []
    rec = MicRecorder(got.append)
    rec.start()
    with rec._lock:  # as if start() or stop() were running in another thread
        fake_sd.streams[0].feed(np.zeros(320, np.float32))
    assert len(got) == 1
    rec.stop()
