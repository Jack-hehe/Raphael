"""Raphael's voice: the line that is spoken, the one-at-a-time rule, the wait for the first sound and
its bounds, streaming playback and the stops, the failures that leave the answer silent, and `tts.*`.
A fake Fish through httpx.MockTransport that streams chunks, and a fake player: no network, no
sound device."""

import asyncio
import logging
import queue
import threading
import time

import httpx
import numpy as np
import pytest

from raphael_core.brain.settings import SettingsStore
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router
from raphael_core.services import playback
from raphael_core.services.hub import ServiceHub
from raphael_core.services.keys import memory_keys
from raphael_core.services.speaker import (
    MAX_SPOKEN_CHARS,
    PREVIEW_LINE,
    Speaker,
    line,
    speakable,
)
from raphael_core.services.tts import PCM_RATE

from .test_tts import Chunks

KEY = "fish-the-owners-key-0123456789abcdef"
VOICE = "0123456789abcdef0123456789abcdef"
FIRST = b"\x01\x00" * 64
SECOND = b"\x02\x00" * 64


async def until(predicate, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting for a condition"
        await asyncio.sleep(0.005)


class FakePlayer:
    """Takes the chunks of a stream from its queue like the real player, notes each, calls
    `started` at the first, and ends when the stream does or `stopped` is set."""

    def __init__(self):
        self.received = []
        self.rates = []
        self.interrupted = []
        self.fail = None

    def play(self, source, rate, stopped, started):
        if self.fail:
            raise self.fail
        self.rates.append(rate)
        began = False
        while True:
            if stopped.is_set():
                self.interrupted.append(True)
                return
            try:
                chunk = source.get(timeout=0.005)
            except queue.Empty:
                continue
            if stopped.is_set():  # stopped while it waited: the stream's end after it does not count
                self.interrupted.append(True)
                return
            if chunk is None:
                self.interrupted.append(False)
                return
            self.received.append(chunk)
            if not began:
                began = True
                started()


class Rig:
    def __init__(self, conn, answer=None):
        self.settings = SettingsStore(conn)
        self.keys = memory_keys()
        self.requests: list[httpx.Request] = []
        self.body = Chunks(FIRST, SECOND)
        self.answer = answer or (lambda request: httpx.Response(200, stream=self.body))
        self.hub = ServiceHub(
            conn, object(), self.keys, transport=httpx.MockTransport(self.handle)
        )
        self.player = FakePlayer()
        self.speaker = Speaker(self.hub, self.player)
        self.rpc = Router()
        self.speaker.register(self.rpc)

    def handle(self, request):
        self.requests.append(request)
        return self.answer(request)

    def set_up(self, key=KEY, voice=VOICE):
        self.settings.set("tts_service", "fish")
        self.settings.set("tts_fish_voice", voice)
        if key:
            self.keys.set("fish", key)

    def texts(self):
        import json

        return [json.loads(r.content)["text"] for r in self.requests]


@pytest.fixture
async def rig(conn):
    rig = Rig(conn)
    yield rig
    await rig.speaker.aclose()
    await rig.hub.aclose()


def a_rig_with(conn, speaker_kwargs=None, body=None):
    rig = Rig(conn)
    if body is not None:
        rig.body = body
    rig.speaker = Speaker(rig.hub, rig.player, **(speaker_kwargs or {}))
    rig.rpc = Router()
    rig.speaker.register(rig.rpc)
    return rig


# --- what is spoken --------------------------------------------------------------------------------


def test_the_prefix_is_spoken_with_a_stop_and_the_brackets_never_are():
    assert line("告", "マスター、ご指示をどうぞ。") == "告。マスター、ご指示をどうぞ。"
    assert line("Answer", "It is 24 degrees.") == "Answer. It is 24 degrees."
    assert "《" not in line("解", "はい。")


def test_addresses_and_markdown_are_left_out_and_whitespace_is_collapsed():
    said = speakable("**看**這個：https://example.com/a?b=1 還有\n\n- `code`\n- 第二點")
    assert said == "看 這個： 還有 code 第二點"


def test_a_long_answer_is_cut_at_a_sentence_end():
    sentence = "這是一個很長的句子，說了很多的內容。"
    said = speakable(sentence * 60)
    assert len(said) <= MAX_SPOKEN_CHARS and said.endswith("。")
    assert speakable("x" * 1000) == "x" * MAX_SPOKEN_CHARS


def test_nothing_to_say_is_nothing():
    assert line("解", "   ") == "" and line("解", "https://example.com") == ""


# --- saying ----------------------------------------------------------------------------------------


async def test_a_line_is_asked_of_fish_and_played_as_it_streams_in(rig):
    rig.set_up()
    await rig.speaker.say("告", "マスター、ご指示をどうぞ。")
    assert rig.texts() == ["告。マスター、ご指示をどうぞ。"]
    await until(lambda: rig.player.received == [FIRST, SECOND])
    assert rig.player.rates == [PCM_RATE]


async def test_say_returns_when_the_first_sound_begins_not_before(conn):
    release = asyncio.Event()
    rig = a_rig_with(conn, body=Chunks(release, FIRST))
    rig.set_up()
    saying = asyncio.ensure_future(rig.speaker.say("解", "はい。"))
    await asyncio.sleep(0.1)
    assert not saying.done() and rig.player.received == []  # nothing yet: the voice is not ready
    release.set()
    await asyncio.wait_for(saying, 2)
    assert rig.player.received == [FIRST]
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_the_first_chunk_is_played_before_the_rest_of_the_body_is_made(conn):
    rest = asyncio.Event()
    rig = a_rig_with(conn, body=Chunks(FIRST, rest, SECOND))
    rig.set_up()
    await asyncio.wait_for(rig.speaker.say("解", "はい。"), 2)
    assert rig.player.received == [FIRST]  # playing, with the body still open
    rest.set()
    await until(lambda: rig.player.received == [FIRST, SECOND])
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_the_wait_is_bounded_and_the_sound_follows_when_it_comes(conn):
    release = asyncio.Event()
    rig = a_rig_with(conn, {"first_audio_wait_s": 0.1}, Chunks(release, FIRST))
    rig.set_up()
    began = time.monotonic()
    await rig.speaker.say("解", "はい。")
    assert 0.08 < time.monotonic() - began < 1.0  # returned without the sound
    assert rig.player.received == []
    release.set()
    await until(lambda: rig.player.received == [FIRST])  # played when it arrived
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_a_sound_that_comes_too_late_is_dropped_and_logged_as_a_kind(conn, caplog):
    release = asyncio.Event()
    body = Chunks(release, FIRST)
    rig = a_rig_with(conn, {"first_audio_wait_s": 0.05, "late_drop_s": 0.15}, body)
    rig.set_up()
    with caplog.at_level(logging.DEBUG):
        await rig.speaker.say("解", "祕密です。")
        await until(lambda: "could not speak" in caplog.text)
    release.set()
    await asyncio.sleep(0.05)
    assert rig.player.received == [] and body.closed
    assert "late" in caplog.text and "祕密" not in caplog.text
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_no_voice_service_chosen_asks_nothing_and_does_not_wait(rig):
    rig.keys.set("fish", KEY)
    began = time.monotonic()
    await rig.speaker.say("告", "マスター。")
    assert time.monotonic() - began < 0.5
    assert rig.requests == [] and rig.player.received == []


def test_a_voice_that_can_be_heard_has_a_service_a_key_and_a_voice_id(rig):
    assert rig.speaker.speaks() is False
    rig.settings.set("tts_service", "fish")
    assert rig.speaker.speaks() is False  # no voice id, no key
    rig.settings.set("tts_fish_voice", VOICE)
    assert rig.speaker.speaks() is False  # no key
    rig.keys.set("fish", KEY)
    assert rig.speaker.speaks() is True
    rig.settings.set("tts_service", "none")
    assert rig.speaker.speaks() is False


@pytest.mark.parametrize("missing", ["key", "voice"])
async def test_a_missing_key_or_voice_leaves_the_answer_silent_and_logs_a_kind_only(
    rig, caplog, missing
):
    rig.set_up(key=None if missing == "key" else KEY, voice="" if missing == "voice" else VOICE)
    began = time.monotonic()
    with caplog.at_level(logging.DEBUG):
        await rig.speaker.say("解", "祕密の答え。")
        await rig.speaker.aclose()
    assert time.monotonic() - began < 0.5  # a voice that cannot speak is not waited for
    assert rig.requests == [] and rig.player.received == []
    assert "missing_secret" in caplog.text or "not_configured" in caplog.text
    assert "祕密" not in caplog.text and KEY not in caplog.text


@pytest.mark.parametrize("status", [401, 402, 429, 503])
async def test_a_fish_error_is_logged_as_a_kind_and_nothing_is_retried(rig, caplog, status):
    rig.answer = lambda request: httpx.Response(status, json={"message": "祕密の答え"})
    rig.set_up()
    began = time.monotonic()
    with caplog.at_level(logging.DEBUG):
        await rig.speaker.say("解", "祕密の答え。")
        await until(lambda: "could not speak" in caplog.text)
        await asyncio.sleep(0.05)
    assert time.monotonic() - began < 0.5
    assert len(rig.requests) == 1 and rig.player.received == []
    for needle in ("祕密", KEY, VOICE):
        assert needle not in caplog.text


async def test_a_network_failure_is_a_silent_answer(rig, caplog):
    def down(request):
        raise httpx.ConnectError("no route", request=request)

    rig.answer = down
    rig.set_up()
    with caplog.at_level(logging.DEBUG):
        await rig.speaker.say("解", "はい。")
        await until(lambda: "network" in caplog.text)
    assert rig.player.received == []


async def test_a_stream_that_breaks_midway_plays_what_came_and_logs_a_kind(conn, caplog):
    request = httpx.Request("POST", "https://api.fish.audio/v1/tts")
    rig = a_rig_with(conn, body=Chunks(FIRST, httpx.ReadError("reset", request=request)))
    rig.set_up()
    with caplog.at_level(logging.DEBUG):
        await rig.speaker.say("解", "はい。")
        await until(lambda: "network" in caplog.text)
    await until(lambda: rig.player.interrupted == [False])  # it ended by itself, not stopped
    assert rig.player.received == [FIRST]
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_a_device_that_cannot_play_is_logged_by_the_type_of_its_error(rig, caplog):
    rig.player.fail = OSError("no output device for 祕密")
    rig.set_up()
    began = time.monotonic()
    with caplog.at_level(logging.DEBUG):
        await rig.speaker.say("解", "はい。")
        await until(lambda: "playback" in caplog.text)
    assert time.monotonic() - began < 1.0  # the wait ended at the failure, not at its bound
    assert "OSError" in caplog.text and "祕密" not in caplog.text


async def test_a_failure_in_a_row_is_logged_once(rig, caplog):
    rig.answer = lambda request: httpx.Response(401)
    rig.set_up()
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            await rig.speaker.say("解", "はい。")
            await until(lambda: len(rig.requests) >= 1)
            await rig.speaker.aclose()
            rig.requests.clear()
    assert caplog.text.count("could not speak") == 1


async def test_the_text_of_a_line_is_never_logged(rig, caplog):
    rig.set_up()
    with caplog.at_level(logging.DEBUG):
        await rig.speaker.say("解", "ひみつの答えです。")
        await until(lambda: rig.player.interrupted == [False])
    assert "ひみつ" not in caplog.text and KEY not in caplog.text and VOICE not in caplog.text


# --- one at a time, and stopping -------------------------------------------------------------------


async def test_a_new_line_replaces_the_one_being_played(conn):
    rest = asyncio.Event()
    body = Chunks(FIRST, rest)  # the first line never finishes by itself
    rig = a_rig_with(conn, body=body)
    rig.set_up()
    await rig.speaker.say("解", "第一句。")
    assert rig.player.received == [FIRST]
    rig.body = Chunks(SECOND)
    await rig.speaker.say("解", "第二句。")
    await until(lambda: sorted(rig.player.interrupted) == [False, True])  # the first was stopped
    assert body.closed and rig.player.received[-1] == SECOND
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_a_new_line_drops_a_request_that_is_still_on_its_way(conn):
    release = asyncio.Event()
    rig = a_rig_with(conn, speaker_kwargs={"first_audio_wait_s": 0.05}, body=Chunks(release, FIRST))
    rig.set_up()
    await rig.speaker.say("解", "第一句。")
    rig.speaker.stop()
    release.set()
    await asyncio.sleep(0.1)
    assert rig.player.received == []
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_stop_ends_the_sound_at_once_and_closes_the_stream(conn):
    body = Chunks(FIRST, asyncio.Event())  # a body that is never done
    rig = a_rig_with(conn, body=body)
    rig.set_up()
    await rig.speaker.say("解", "はい。")
    assert rig.player.received == [FIRST]
    rig.speaker.stop()
    await until(lambda: rig.player.interrupted == [True])
    await until(lambda: body.closed)  # the request in flight is closed
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_a_stop_during_the_wait_for_the_first_sound_ends_the_wait(conn):
    rig = a_rig_with(conn, body=Chunks(asyncio.Event(), FIRST))
    rig.set_up()
    saying = asyncio.ensure_future(rig.speaker.say("解", "はい。"))
    await asyncio.sleep(0.05)
    assert not saying.done()
    rig.speaker.stop()
    await asyncio.wait_for(saying, 1)
    assert rig.player.received == []
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_the_stop_method_stops_and_a_stop_with_nothing_playing_is_harmless(conn):
    body = Chunks(FIRST, asyncio.Event())
    rig = a_rig_with(conn, body=body)
    rig.set_up()
    assert await rig.rpc.call("tts.stop", {}) == {"ok": True}
    await rig.speaker.say("解", "はい。")
    assert await rig.rpc.call("tts.stop", {}) == {"ok": True}
    await until(lambda: rig.player.interrupted == [True])
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_saying_with_the_service_set_to_none_still_stops_what_was_playing(conn):
    rig = a_rig_with(conn, body=Chunks(FIRST, asyncio.Event()))
    rig.set_up()
    await rig.speaker.say("解", "はい。")
    rig.settings.set("tts_service", "none")
    await rig.speaker.say("解", "出ません。")
    await until(lambda: rig.player.interrupted == [True])
    assert len(rig.requests) == 1
    await rig.speaker.aclose()
    await rig.hub.aclose()


# --- tts.preview -----------------------------------------------------------------------------------


async def test_a_preview_always_speaks_the_japanese_greeting_and_says_so(rig):
    rig.set_up()
    assert await rig.rpc.call("tts.preview", {"text": "Notice. Master, please give your instructions."}) == {
        "ok": True
    }
    assert rig.texts() == [PREVIEW_LINE] == ["告。マスター、ご指示をどうぞ。"]
    assert rig.player.received  # playing by the time it answers


async def test_a_preview_tells_why_it_could_not(rig):
    assert await rig.rpc.call("tts.preview", {"text": "x"}) == {"ok": False, "kind": "not_configured"}
    rig.settings.set("tts_service", "fish")
    rig.settings.set("tts_fish_voice", VOICE)
    assert await rig.rpc.call("tts.preview", {"text": "x"}) == {"ok": False, "kind": "missing_secret"}
    rig.keys.set("fish", KEY)
    rig.answer = lambda request: httpx.Response(402)
    assert await rig.rpc.call("tts.preview", {"text": "x"}) == {"ok": False, "kind": "credits"}


async def test_a_preview_tells_a_device_that_cannot_play(rig):
    rig.set_up()
    rig.player.fail = OSError("none")
    assert await rig.rpc.call("tts.preview", {"text": "x"}) == {"ok": False, "kind": "playback"}


@pytest.mark.parametrize("text", [None, 5, "", "  ", "x" * 201])
async def test_a_preview_takes_a_short_text_only(rig, text):
    with pytest.raises(RpcError) as caught:
        await rig.rpc.call("tts.preview", {"text": text})
    assert caught.value.code == INVALID_PARAMS


async def test_a_preview_is_stopped_by_a_stop(conn):
    rig = a_rig_with(conn, body=Chunks(FIRST, asyncio.Event()))
    rig.set_up()
    assert await rig.rpc.call("tts.preview", {"text": "好"}) == {"ok": True}
    await rig.rpc.call("tts.stop", {})
    await until(lambda: rig.player.interrupted == [True])
    await rig.speaker.aclose()
    await rig.hub.aclose()


# --- the player ------------------------------------------------------------------------------------


class FakeStream:
    def __init__(self, log, **kwargs):
        self.log = log
        self.kwargs = kwargs

    def start(self):
        self.log.append("start")

    def close(self):
        self.log.append("close")

    def write(self, block):
        self.log.append(("write", block.shape, block.dtype))

    def stop(self):
        self.log.append("stop")

    def abort(self):
        self.log.append("abort")


def a_player(log, on_write=None):
    player = playback.SoundDevicePlayer.__new__(playback.SoundDevicePlayer)
    player._lock = threading.Lock()
    streams = []

    class Sd:
        @staticmethod
        def OutputStream(**kwargs):
            stream = FakeStream(log, **kwargs)
            streams.append(stream)
            if on_write:
                original = stream.write

                def write(block):
                    original(block)
                    on_write()

                stream.write = write
            return stream

    player._sd = Sd
    player.streams = streams
    return player


def a_stream_of(*chunks):
    source = queue.Queue()
    for chunk in chunks:
        source.put(chunk)
    source.put(None)
    return source


def a_tone(seconds=0.3, rate=44_100):
    t = np.arange(int(rate * seconds)) / rate
    return (0.3 * 32767 * np.sin(2 * np.pi * 440 * t)).astype("<i2").tobytes()


def test_the_player_writes_the_stream_in_blocks_then_lets_the_device_drain():
    log, began = [], []
    tone = a_tone()
    pieces = [tone[i : i + 5_000] for i in range(0, len(tone), 5_000)]
    player = a_player(log)
    player.play(a_stream_of(*pieces), 44_100, threading.Event(), lambda: began.append(log[:]))
    writes = [entry for entry in log if isinstance(entry, tuple)]
    assert len(writes) >= 5 and log[-2:] == ["stop", "close"] and "abort" not in log
    assert all(shape[1] == 1 and dtype == np.int16 for _, shape, dtype in writes)
    assert sum(shape[0] for _, shape, _ in writes) == len(tone) // 2  # every sample, once
    assert player.streams[0].kwargs == {"samplerate": 44_100, "channels": 1, "dtype": "int16"}
    assert began == [["start"]]  # started: once, after the device is open, before the first write


def test_a_sample_split_between_two_chunks_is_kept_whole():
    log = []
    tone = a_tone(0.2)
    player = a_player(log)
    player.play(a_stream_of(tone[:1], tone[1:7], tone[7:]), 44_100, threading.Event(), lambda: None)
    writes = [entry for entry in log if isinstance(entry, tuple)]
    assert sum(shape[0] for _, shape, _ in writes) == len(tone) // 2


def test_the_player_aborts_within_a_block_of_a_stop():
    log = []
    stopped = threading.Event()
    tone = a_tone()
    a_player(log, on_write=stopped.set).play(a_stream_of(tone), 44_100, stopped, lambda: None)
    assert len([e for e in log if isinstance(e, tuple)]) == 1
    assert log[-2:] == ["abort", "close"] and "stop" not in log


def test_a_player_stopped_before_it_began_opens_nothing_and_does_not_start():
    log, began = [], []
    stopped = threading.Event()
    stopped.set()
    a_player(log).play(a_stream_of(a_tone()), 44_100, stopped, lambda: began.append(1))
    assert log == [] and began == []


def test_a_stream_that_ends_with_nothing_in_it_opens_nothing():
    log, began = [], []
    a_player(log).play(a_stream_of(), 44_100, threading.Event(), lambda: began.append(1))
    assert log == [] and began == []


def test_a_player_waits_for_chunks_that_are_late_and_stops_when_told():
    log = []
    stopped = threading.Event()
    source = queue.Queue()
    player = a_player(log)
    worker = threading.Thread(target=player.play, args=(source, 44_100, stopped, lambda: None))
    worker.start()
    time.sleep(0.15)  # a slow network: nothing yet, and nothing opened
    assert log == []
    source.put(a_tone(0.1))
    time.sleep(0.15)
    stopped.set()
    worker.join(2)
    assert not worker.is_alive() and "abort" in log
