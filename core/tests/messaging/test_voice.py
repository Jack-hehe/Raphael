"""Voice messages: decoding Ogg/Opus in memory a block at a time, the size limit, and the way a
transcript is answered. The Bot API is the fake of test_telegram; the speech engine and the voice
detector are fakes."""

import io

import httpx
import numpy as np
import pytest
import soundfile

from raphael_core.messaging import voice
from raphael_core.messaging.core import MAX_VOICE_BYTES, VoiceTooLarge
from raphael_core.messaging.words import HEARD_CHARS, heard

from .test_telegram import OWNER, FakeTelegram, make_gateway, run_until


def opus(seconds=1.0, *, rate=48000, channels=1, frequency=300.0) -> bytes:
    """An Ogg/Opus clip of a tone, made in memory."""
    t = np.arange(int(seconds * rate)) / rate
    tone = (0.3 * np.sin(2 * np.pi * frequency * t)).astype("float32")
    audio = np.stack([tone] * channels, axis=1) if channels > 1 else tone
    buffer = io.BytesIO()
    soundfile.write(buffer, audio, rate, format="OGG", subtype="OPUS")
    return buffer.getvalue()


# --- decoding ---


def test_opus_decodes_in_memory_to_mono_16_khz():
    samples = voice.decode(opus(1.0))
    assert samples.dtype == np.float32 and samples.ndim == 1
    assert abs(samples.size - 16000) <= 400
    assert 0.1 < float(np.abs(samples).max()) < 0.6  # the tone came through


def test_stereo_is_averaged_to_mono():
    samples = voice.decode(opus(0.5, channels=2))
    assert samples.ndim == 1 and abs(samples.size - 8000) <= 400


def test_a_long_message_is_decoded_a_block_at_a_time_and_whole():
    # Five minutes, far more than the two minutes that used to be the limit: every block is a few
    # seconds of 16 kHz mono, and together they are the whole message.
    blocks = list(voice.decoded_blocks(opus(300.0, rate=8000)))
    assert len(blocks) > 1
    assert max(block.size for block in blocks) <= (voice.BLOCK_SECONDS + 1) * voice.SAMPLE_RATE
    assert abs(sum(block.size for block in blocks) - 300 * voice.SAMPLE_RATE) <= 400
    assert all(block.dtype == np.float32 and block.ndim == 1 for block in blocks)


def test_bytes_that_are_no_audio_raise():
    with pytest.raises(Exception):
        voice.decode(b"not audio at all")


class Segmenter:
    """Finds the stretches it is given once, in the first block it is fed."""

    def __init__(self, found):
        self.found, self.heard = found, []

    def accept(self, samples):
        self.heard.append(samples.size)
        found, self.found = self.found, []
        return found

    def flush(self):
        return []


class Engine:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []

    def transcribe(self, samples, hints=()):
        self.calls.append((samples.size, list(hints)))
        return self.texts.pop(0)


def test_each_stretch_of_speech_goes_through_the_engine_with_the_hints_and_is_joined():
    stretch = np.zeros(1600, dtype="float32")
    engine = Engine("你好", "world")
    text = voice.transcribe_voice(opus(1.0), engine, Segmenter([stretch, stretch]), ["Raphael"])
    assert text == "你好 world"
    assert engine.calls == [(1600, ["Raphael"])] * 2


def test_a_long_message_is_fed_to_the_segmenter_a_block_at_a_time():
    segmenter = Segmenter([])
    voice.transcribe_voice(opus(30.0, rate=8000), Engine(), segmenter)
    assert len(segmenter.heard) >= 3 and max(segmenter.heard) <= (voice.BLOCK_SECONDS + 1) * voice.SAMPLE_RATE


def test_no_speech_is_no_text():
    assert voice.transcribe_voice(opus(0.2), Engine(), Segmenter([]), []) == ""


# --- answering a voice message ---


class FileTelegram(FakeTelegram):
    """The fake Bot API that also serves files: `getFile` and the download address."""

    def __init__(self):
        super().__init__()
        self.files: dict[str, bytes] = {}
        self.downloads: list[str] = []
        self.get_files: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/file/bot"):
            token, _, name = path[len("/file/bot") :].partition("/")
            self.downloads.append(name)
            if token != self.valid_token or name not in self.files:
                return httpx.Response(404)
            return httpx.Response(200, content=self.files[name])
        if path.endswith("/getFile"):
            import json

            name = json.loads(request.content)["file_id"]
            self.get_files.append(name)
            size = len(self.files.get(name, b""))
            return httpx.Response(
                200, json={"ok": True, "result": {"file_id": name, "file_path": name, "file_size": size}}
            )
        return await super().__call__(request)


@pytest.fixture
def files():
    return FileTelegram()


@pytest.fixture
def files_http(files):
    return httpx.AsyncClient(transport=httpx.MockTransport(files))


def transcriber(text="what is the weather", calls=None):
    async def transcribe(audio, language):
        if calls is not None:
            calls.append((len(audio), language))
        if isinstance(text, Exception):
            raise text
        return text

    return transcribe


async def test_a_voice_message_is_heard_asked_and_answered_in_text(files, files_http):
    files.files["v1"] = b"ogg bytes"
    files.message(OWNER, None, voice={"file_id": "v1", "duration": 3, "file_size": 9, "mime_type": "audio/ogg"})
    calls = []
    gateway = make_gateway(files, files_http, transcribe=transcriber(calls=calls))
    await run_until(gateway, lambda: files.sent)
    assert calls == [(9, "en")]
    assert gateway.asked == [(f"telegram:{OWNER}", "what is the weather", "en", "telegram")]
    assert files.sent == [(OWNER, heard("en", "what is the weather") + "\n\n《答》好")]


async def test_a_voice_message_that_is_too_large_is_never_downloaded(files, files_http):
    files.message(OWNER, None, voice={"file_id": "big", "duration": 5, "file_size": MAX_VOICE_BYTES + 1})
    gateway = make_gateway(files, files_http, transcribe=transcriber())
    await run_until(gateway, lambda: files.sent)
    assert files.get_files == [] and files.downloads == [] and gateway.asked == []
    assert files.sent[0][1] == "That voice message is too large (the limit is 20 MB)."


async def test_a_long_voice_message_is_heard_whole(files, files_http):
    # Telegram says how long it is; however long, it is downloaded and heard, and all of what was
    # heard is asked.
    files.files["v1"] = b"ogg bytes"
    files.message(OWNER, None, voice={"file_id": "v1", "duration": 3 * 3600, "file_size": 9})
    said = "word " * 2000
    gateway = make_gateway(files, files_http, transcribe=transcriber(said))
    await run_until(gateway, lambda: files.sent)
    assert gateway.asked == [(f"telegram:{OWNER}", said.strip(), "en", "telegram")]


async def test_a_file_the_bot_api_reports_as_too_large_is_not_downloaded(files, files_http):
    files.files["v1"] = b"x" * (MAX_VOICE_BYTES + 1)
    files.message(OWNER, None, voice={"file_id": "v1", "duration": 3})
    gateway = make_gateway(files, files_http, transcribe=transcriber())
    await run_until(gateway, lambda: files.sent)
    assert files.get_files == ["v1"] and files.downloads == [] and gateway.asked == []


async def test_silence_gets_a_fixed_reply_and_asks_nothing(files, files_http):
    files.files["v1"] = b"ogg"
    files.message(OWNER, None, voice={"file_id": "v1", "duration": 1})
    gateway = make_gateway(files, files_http, transcribe=transcriber("  "))
    await run_until(gateway, lambda: files.sent)
    assert files.sent[0][1] == "I didn't hear anything." and gateway.asked == []


async def test_a_clip_that_decodes_too_large_is_declined_like_a_large_one(files, files_http):
    files.files["v1"] = b"ogg"
    files.message(OWNER, None, voice={"file_id": "v1", "duration": 1})
    gateway = make_gateway(files, files_http, transcribe=transcriber(VoiceTooLarge()))
    await run_until(gateway, lambda: files.sent)
    assert "too large" in files.sent[0][1] and gateway.asked == []


async def test_a_failure_to_hear_gets_a_plain_reply_without_details(files, files_http):
    files.files["v1"] = b"ogg"
    files.message(OWNER, None, voice={"file_id": "v1", "duration": 1})
    gateway = make_gateway(files, files_http, transcribe=transcriber(RuntimeError("secret detail")))
    await run_until(gateway, lambda: files.sent)
    assert "secret detail" not in files.sent[0][1] and "did not work" in files.sent[0][1]


async def test_an_audio_file_that_is_not_ogg_is_declined(files, files_http):
    files.message(OWNER, None, audio={"file_id": "a1", "duration": 1, "mime_type": "audio/mpeg"})
    gateway = make_gateway(files, files_http, transcribe=transcriber())
    await run_until(gateway, lambda: files.sent)
    assert files.downloads == [] and gateway.asked == [] and "text and voice" in files.sent[0][1]


async def test_an_ogg_audio_file_is_heard_like_a_voice_message(files, files_http):
    files.files["a1"] = b"ogg"
    files.message(OWNER, None, audio={"file_id": "a1", "duration": 1, "mime_type": "audio/ogg"})
    gateway = make_gateway(files, files_http, transcribe=transcriber())
    await run_until(gateway, lambda: files.sent)
    assert len(gateway.asked) == 1


async def test_a_voice_message_from_a_stranger_gets_a_code_not_a_download(files, files_http):
    files.files["v1"] = b"ogg"
    files.message(555, None, voice={"file_id": "v1", "duration": 1})
    gateway = make_gateway(files, files_http, transcribe=transcriber())
    await run_until(gateway, lambda: files.sent)
    assert files.downloads == [] and gateway.asked == []


async def test_neither_the_audio_nor_the_words_reach_the_log(files, files_http, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    files.files["v1"] = b"ogg"
    files.message(OWNER, None, voice={"file_id": "v1", "duration": 1})
    gateway = make_gateway(files, files_http, transcribe=transcriber("my secret plan"))
    await run_until(gateway, lambda: files.sent)
    assert "my secret plan" not in caplog.text


def test_the_heard_line_is_cut_and_localised():
    long = "字" * 200
    line = heard("zh-TW", long)
    assert line.startswith("聽到：「") and line.endswith("…」") and len(line) <= HEARD_CHARS + 6
    assert heard("en", "hi").startswith("Heard: ") and heard("ja", "hi").startswith("聞き取り")
    assert "\n" not in heard("en", "two\nlines")


def test_a_clip_with_many_channels_is_refused_before_it_is_read():
    audio = np.zeros((8000, 6), dtype="float32")
    buffer = io.BytesIO()
    soundfile.write(buffer, audio, 8000, format="OGG", subtype="VORBIS")
    with pytest.raises(VoiceTooLarge):
        voice.decode(buffer.getvalue())
