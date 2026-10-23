"""Raphael's voice: the line that is spoken, the one-at-a-time rule, the stops, the failures that
leave the answer silent, and `tts.*`. A fake Fish through httpx.MockTransport and a fake player: no
network, no sound device."""

import asyncio
import io
import json
import logging
import threading

import httpx
import numpy as np
import pytest

from raphael_core.brain.settings import SettingsStore
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router
from raphael_core.services import playback
from raphael_core.services.hub import ServiceHub
from raphael_core.services.keys import memory_keys
from raphael_core.services.speaker import MAX_SPOKEN_CHARS, Speaker, line, speakable

KEY = "fish-the-owners-key-0123456789abcdef"
VOICE = "0123456789abcdef0123456789abcdef"


async def until(predicate, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting for a condition"
        await asyncio.sleep(0.005)


class FakePlayer:
    """Notes what it is asked to play and blocks like a real player until it is stopped or told to
    finish."""

    def __init__(self):
        self.played = []
        self.started = threading.Event()
        self.finish = threading.Event()
        self.interrupted = []
        self.hold = False
        self.fail = None

    def play(self, data, stopped):
        if self.fail:
            raise self.fail
        self.played.append(data)
        self.started.set()
        if self.hold:
            while not (self.finish.is_set() or stopped.is_set()):
                stopped.wait(0.005)
            self.interrupted.append(stopped.is_set())


class Rig:
    def __init__(self, conn, answer=None):
        self.settings = SettingsStore(conn)
        self.keys = memory_keys()
        self.requests: list[httpx.Request] = []
        self.answer = answer or (lambda request: httpx.Response(200, content=b"mp3:" + request.content))
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
        self.settings.set("tts_enabled", True)
        self.settings.set("tts_fish_voice", voice)
        if key:
            self.keys.set("fish", key)

    def texts(self):
        return [json.loads(r.content)["text"] for r in self.requests]


@pytest.fixture
async def rig(conn):
    rig = Rig(conn)
    yield rig
    await rig.speaker.aclose()
    await rig.hub.aclose()


# --- what is spoken --------------------------------------------------------------------------------


def test_the_prefix_is_spoken_with_a_stop_and_the_brackets_never_are():
    assert line("告", "主人，請下達指示。") == "告。主人，請下達指示。"
    assert line("Answer", "It is 24 degrees.") == "Answer. It is 24 degrees."
    assert "《" not in line("答", "好。")


def test_addresses_and_markdown_are_left_out_and_whitespace_is_collapsed():
    said = speakable("**看**這個：https://example.com/a?b=1 還有\n\n- `code`\n- 第二點")
    assert said == "看 這個： 還有 code 第二點"


def test_a_long_answer_is_cut_at_a_sentence_end():
    sentence = "這是一個很長的句子，說了很多的內容。"
    said = speakable(sentence * 60)
    assert len(said) <= MAX_SPOKEN_CHARS and said.endswith("。")
    assert speakable("x" * 1000) == "x" * MAX_SPOKEN_CHARS


def test_nothing_to_say_is_nothing():
    assert line("答", "   ") == "" and line("答", "https://example.com") == ""


# --- saying ----------------------------------------------------------------------------------------


async def test_a_line_is_asked_of_fish_and_played(rig):
    rig.set_up()
    rig.speaker.say("告", "主人，請下達指示。")
    await until(lambda: rig.player.played)
    assert rig.texts() == ["告。主人，請下達指示。"]
    assert rig.player.played[0].startswith(b"mp3:")


async def test_no_voice_service_chosen_asks_nothing(rig):
    rig.keys.set("fish", KEY)
    rig.speaker.say("告", "主人。")
    await asyncio.sleep(0.05)
    assert rig.requests == [] and rig.player.played == []


@pytest.mark.parametrize("missing", ["key", "voice"])
async def test_a_missing_key_or_voice_leaves_the_answer_silent_and_logs_a_kind_only(
    rig, caplog, missing
):
    rig.set_up(key=None if missing == "key" else KEY, voice="" if missing == "voice" else VOICE)
    with caplog.at_level(logging.DEBUG):
        rig.speaker.say("答", "祕密的答案。")
        await until(lambda: "could not speak" in caplog.text)
        await rig.speaker.aclose()
    assert rig.requests == [] and rig.player.played == []
    assert "missing_secret" in caplog.text or "not_configured" in caplog.text
    assert "祕密" not in caplog.text and KEY not in caplog.text


@pytest.mark.parametrize("status", [401, 402, 429, 503])
async def test_a_fish_error_is_logged_as_a_kind_and_nothing_is_retried(rig, caplog, status):
    rig.answer = lambda request: httpx.Response(status, json={"message": "祕密的答案"})
    rig.set_up()
    with caplog.at_level(logging.DEBUG):
        rig.speaker.say("答", "祕密的答案。")
        await until(lambda: "could not speak" in caplog.text)
        await asyncio.sleep(0.05)
    assert len(rig.requests) == 1 and rig.player.played == []
    for needle in ("祕密", KEY, VOICE):
        assert needle not in caplog.text


async def test_a_network_failure_is_a_silent_answer(rig, caplog):
    def down(request):
        raise httpx.ConnectError("no route", request=request)

    rig.answer = down
    rig.set_up()
    with caplog.at_level(logging.DEBUG):
        rig.speaker.say("答", "好。")
        await until(lambda: "network" in caplog.text)
    assert rig.player.played == []


async def test_a_device_that_cannot_play_is_logged_by_the_type_of_its_error(rig, caplog):
    rig.player.fail = OSError("no output device for 祕密")
    rig.set_up()
    with caplog.at_level(logging.DEBUG):
        rig.speaker.say("答", "好。")
        await until(lambda: "playback" in caplog.text)
    assert "OSError" in caplog.text and "祕密" not in caplog.text


async def test_a_failure_in_a_row_is_logged_once(rig, caplog):
    rig.answer = lambda request: httpx.Response(401)
    rig.set_up()
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            rig.speaker.say("答", "好。")
            await until(lambda: len(rig.requests) >= 1)
            await rig.speaker.aclose()
            rig.requests.clear()
    assert caplog.text.count("could not speak") == 1


# --- one at a time, and stopping -------------------------------------------------------------------


async def test_a_new_line_replaces_the_one_being_played(rig):
    rig.player.hold = True
    rig.set_up()
    rig.speaker.say("答", "第一句。")
    await until(lambda: rig.player.played)
    rig.speaker.say("答", "第二句。")
    await until(lambda: len(rig.player.played) == 2)
    rig.player.finish.set()
    await until(lambda: len(rig.player.interrupted) == 2)
    assert rig.player.interrupted[0] is True  # the first was stopped, not finished


async def test_a_new_line_drops_a_request_that_is_still_on_its_way(conn):
    release = asyncio.Event()

    class SlowRig(Rig):
        async def slow(self, request):
            await release.wait()
            return httpx.Response(200, content=b"mp3")

    rig = SlowRig(conn)
    rig.hub = ServiceHub(conn, object(), rig.keys, transport=httpx.MockTransport(rig.slow))
    rig.speaker = Speaker(rig.hub, rig.player)
    rig.set_up()
    rig.speaker.say("答", "第一句。")
    await asyncio.sleep(0.05)
    rig.speaker.stop()
    release.set()
    await asyncio.sleep(0.1)
    assert rig.player.played == []
    await rig.speaker.aclose()
    await rig.hub.aclose()


async def test_stop_ends_the_sound_at_once(rig):
    rig.player.hold = True
    rig.set_up()
    rig.speaker.say("答", "好。")
    await until(lambda: rig.player.played)
    rig.speaker.stop()
    await until(lambda: rig.player.interrupted == [True])


async def test_the_stop_method_stops_and_a_stop_with_nothing_playing_is_harmless(rig):
    rig.player.hold = True
    rig.set_up()
    assert await rig.rpc.call("tts.stop", {}) == {"ok": True}
    rig.speaker.say("答", "好。")
    await until(lambda: rig.player.played)
    assert await rig.rpc.call("tts.stop", {}) == {"ok": True}
    await until(lambda: rig.player.interrupted == [True])


async def test_saying_with_the_voice_turned_off_still_stops_what_was_playing(rig):
    rig.player.hold = True
    rig.set_up()
    rig.speaker.say("答", "好。")
    await until(lambda: rig.player.played)
    rig.settings.set("tts_enabled", False)
    rig.speaker.say("答", "不出聲。")
    await until(lambda: rig.player.interrupted == [True])
    assert len(rig.requests) == 1


# --- tts.preview -----------------------------------------------------------------------------------


async def test_a_preview_speaks_the_text_as_given_and_says_so(rig):
    rig.set_up()
    assert await rig.rpc.call("tts.preview", {"text": "《告。主人，請下達指示。》"}) == {"ok": True}
    assert rig.texts() == ["告。主人，請下達指示。"]  # the brackets only show a line, they are not said
    await until(lambda: rig.player.played)


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


async def test_a_preview_is_stopped_by_a_stop(rig):
    rig.player.hold = True
    rig.set_up()
    assert await rig.rpc.call("tts.preview", {"text": "好"}) == {"ok": True}
    await rig.rpc.call("tts.stop", {})
    await until(lambda: rig.player.interrupted == [True])


# --- the player ------------------------------------------------------------------------------------


def a_tone_as_mp3(seconds=0.3, rate=44_100):
    import soundfile

    t = np.arange(int(rate * seconds)) / rate
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    buffer = io.BytesIO()
    soundfile.write(buffer, tone, rate, format="MP3")
    return buffer.getvalue()


def test_the_decoder_reads_mp3_the_format_requested_from_fish():
    samples, rate = playback.decode(a_tone_as_mp3())
    assert rate == 44_100 and samples.ndim == 2 and samples.dtype == np.float32
    assert abs(samples.shape[0] / rate - 0.3) < 0.1 and float(np.abs(samples).max()) > 0.1


class FakeStream:
    def __init__(self, log, **kwargs):
        self.log = log
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.log.append("close")

    def write(self, block):
        self.log.append(("write", block.shape[0]))

    def stop(self):
        self.log.append("stop")

    def abort(self):
        self.log.append("abort")


def a_player(log, on_write=None):
    player = playback.SoundDevicePlayer.__new__(playback.SoundDevicePlayer)
    player._lock = threading.Lock()

    class Sd:
        @staticmethod
        def OutputStream(**kwargs):
            stream = FakeStream(log, **kwargs)
            if on_write:
                original = stream.write

                def write(block):
                    original(block)
                    on_write()

                stream.write = write
            return stream

    player._sd = Sd
    return player


def test_the_player_writes_the_whole_file_in_blocks_then_lets_the_stream_drain():
    log = []
    a_player(log).play(a_tone_as_mp3(), threading.Event())
    writes = [entry for entry in log if isinstance(entry, tuple)]
    assert len(writes) >= 5 and log[-2:] == ["stop", "close"] and "abort" not in log


def test_the_player_aborts_within_a_block_of_a_stop():
    log = []
    stopped = threading.Event()
    a_player(log, on_write=stopped.set).play(a_tone_as_mp3(), stopped)
    assert [e for e in log if isinstance(e, tuple)] == [("write", log[0][1])]
    assert log[-2:] == ["abort", "close"] and "stop" not in log


def test_a_player_stopped_before_it_began_opens_nothing():
    log = []
    stopped = threading.Event()
    stopped.set()
    a_player(log).play(a_tone_as_mp3(), stopped)
    assert log == []
