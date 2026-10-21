"""Tests for the entry point's wiring.

The speech side is a fake kit: a microphone that delivers scripted chunks, a segmenter that makes
one segment per chunk, an engine that always hears the same words, and the real DictationService.
So the whole wiring (router, brain, models, dictation, the level meter) runs here without a
microphone, a speech model, sherpa-onnx at work or the network. The real thing, in a child process
with the real models, is tests/test_e2e.py.
"""

import asyncio
import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

import raphael_core
from raphael_core import __main__ as entry
from raphael_core import app as app_module
from raphael_core.app import (
    APP_MODELS,
    DICTATION_SEGMENT_SECONDS,
    STT_THREADS,
    App,
    EchoClient,
    LevelMonitor,
    SpeechModels,
    build_app,
)
from raphael_core.brain import catalog, pricing, textnorm
from raphael_core.brain.cleanup import build_user_message
from raphael_core.brain.openrouter import OpenRouterError
from raphael_core.device import audio, dictation, models
from raphael_core.device.models import REGISTRY, ModelFile, ModelManager, ModelSpec
from raphael_core.protocol import (
    BUSY,
    INVALID_PARAMS,
    NO_MICROPHONE,
    NOT_CONFIGURED,
    NOT_FOUND,
    PROTOCOL_VERSION,
    RpcError,
)
from raphael_core.router import Router

RATE = 16_000
WORDS = "你好世界"


def loud(seconds: float = 0.5) -> np.ndarray:
    return np.full(round(seconds * RATE), 0.2, dtype=np.float32)


async def wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for a condition")
        await asyncio.sleep(0.005)


def make_models(root: Path, names=APP_MODELS) -> None:
    """The files that make each model 'ready' (existence is all the registry looks at)."""
    for name in names:
        for relative in REGISTRY[name].files:
            path = root / name / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")


# --- a fake speech side -----------------------------------------------------------------------


class FakeMic:
    """A microphone: start() delivers the kit's scripted chunks at once, on the calling thread."""

    def __init__(self, kit, on_frames, device=None):
        self.kit = kit
        self.on_frames = on_frames
        self.device = device
        self.error = None
        self.started = 0
        self.stopped = 0
        self.fail_to_start = False

    @property
    def open(self) -> bool:
        return self.started > self.stopped

    def start(self):
        if self.fail_to_start:
            raise audio.NoMicrophoneError("no microphone to open")
        self.started += 1
        for chunk in self.kit.script:
            self.on_frames(chunk)

    def stop(self):
        if self.open:
            self.stopped += 1


class FakeWav:
    def __init__(self, on_frames, path, realtime=True):
        self.on_frames = on_frames
        self.path = path
        self.realtime = realtime
        self.error = None
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class FakeSegmenter:
    """One segment per chunk. `speech_seconds` starts again at the first chunk after a flush."""

    def __init__(self, kit, path, **kwargs):
        self.path = path
        self.kwargs = kwargs
        self.speech_seconds = 0.0
        self.flushes = 0
        self._flushed = False
        self.calls_from: set[int] = set()
        self.max_segment_s: list[float] = []  # each limit it was told, as set_max_segment_s keeps
        kit.segmenters.append(self)

    def set_max_segment_s(self, seconds):
        if not self.max_segment_s or self.max_segment_s[-1] != seconds:
            self.max_segment_s.append(seconds)

    def accept(self, frames):
        self.calls_from.add(threading.get_ident())
        if self._flushed:
            self._flushed = False
            self.speech_seconds = 0.0
        self.speech_seconds += frames.size / RATE
        return [frames]

    def flush(self):
        self.flushes += 1
        self._flushed = True
        return []


class FakeEngine:
    name = "qwen3-asr"

    def __init__(self, kit, **kwargs):
        self.kit = kit
        self.kwargs = kwargs

    def transcribe(self, samples, *, hints=(), language="auto"):
        self.kit.transcriptions.append({"hints": list(hints), "language": language})
        return WORDS


def make_kit(*, script=None, devices=None, engine_gate=None, segmenter_gate=None):
    """The things the app imports lazily, with fakes for the speech side."""
    kit = SimpleNamespace(
        script=[loud(), loud()] if script is None else script,
        mics=[],
        wavs=[],
        segmenters=[],
        engines=[],
        transcriptions=[],
        devices=[] if devices is None else devices,
        automatic_device=None,
    )

    def make_mic(on_frames, device=None):
        mic = FakeMic(kit, on_frames, device)
        kit.mics.append(mic)
        return mic

    def make_wav(on_frames, path, realtime=True):
        wav = FakeWav(on_frames, path, realtime)
        kit.wavs.append(wav)
        return wav

    def make_segmenter(path, **kwargs):
        if segmenter_gate is not None:
            assert segmenter_gate.wait(30)
        return FakeSegmenter(kit, path, **kwargs)

    def create_engine(name, manager, num_threads=4):
        if engine_gate is not None:
            assert engine_gate.wait(30)
        engine = FakeEngine(kit, name=name, num_threads=num_threads)
        kit.engines.append(engine)
        return engine

    kit.DictationService = dictation.DictationService
    kit.MicRecorder = make_mic
    kit.WavRecorder = make_wav
    kit.NoMicrophoneError = audio.NoMicrophoneError
    kit.rms_level = audio.rms_level
    kit.list_input_devices = lambda: kit.devices
    kit.automatic_device_name = lambda: kit.automatic_device
    kit.current_input_devices = lambda: kit.devices
    kit.Segmenter = make_segmenter
    kit.create_engine = create_engine
    return kit


@pytest.fixture
async def make_app(tmp_path):
    """`make_app(kit, ...)` is a started App on a data directory of its own."""
    apps: list[App] = []

    def make(
        kit=None,
        *,
        ready=APP_MODELS,
        fake_llm=True,
        fake_audio=None,
        manager=None,
        agent_actions=None,
    ):
        data = tmp_path / "data"
        make_models(data / "models", ready)
        app = App(
            data_dir=data,
            fake_audio=fake_audio,
            fake_llm=fake_llm,
            manager=manager,
            load_kit=lambda: make_kit() if kit is None else kit,
            agent_actions=agent_actions,
        )
        apps.append(app)
        app.start()
        return app

    yield make
    for app in apps:
        await app.aclose()


@pytest.fixture
def notes():
    """Every notification an App sends, as (method, params)."""

    class Notes(list):
        def methods(self, method):
            return [params for name, params in self if name == method]

    return Notes()


def listen(app, notes) -> None:
    app.set_notifier(lambda method, params: notes.append((method, params)))


async def dictate(app, session="s1"):
    await app.call("dictation.start", {"session_id": session})
    return await app.call("dictation.stop", {"session_id": session, "target_app": "notepad.exe"})


# --- the fake LLM -----------------------------------------------------------------------------


async def test_the_fake_llm_gives_the_transcript_back_at_no_cost():
    client = EchoClient()
    text = "嗯 我想 呃 想買 OpenRouter 的額度"
    done = await client.complete_json(
        model="m",
        fallbacks=[],
        system="s",
        user=build_user_message(text),  # how the cleanup service words its question
        schema={},
        schema_name="x",
        timeout_s=1.0,
        max_tokens=64,
    )
    assert done.data == {"text": text}
    assert done.cost_usd == 0.0 and done.prompt_tokens == 0 and done.completion_tokens == 0
    assert (await client.key_info()).label  # a key check passes
    client.warm_up()
    await client.aclose()


async def test_the_fake_llm_has_no_model_list_as_if_there_were_no_network(make_app, caplog):
    with pytest.raises(OpenRouterError) as failed:
        await EchoClient().models(timeout_s=1.0)
    assert failed.value.kind == "network"
    # So the catalog is Raphael's own models, and nothing went wrong on the way there.
    with caplog.at_level(logging.WARNING):
        listed = await make_app().call("models.catalog")
    assert listed["source"] == "builtin" and listed["llm"] and listed["stt"]
    assert not [record for record in caplog.records if record.name.startswith("raphael_core")]


async def test_the_fake_llm_keeps_a_transcript_with_line_breaks_whole():
    text = "第一段\n\n第二段"
    done = await EchoClient().complete_json(
        model="m",
        fallbacks=[],
        system="s",
        user=build_user_message(text),
        schema={},
        schema_name="x",
        timeout_s=1.0,
        max_tokens=64,
    )
    assert done.data == {"text": text}


# --- the models, loaded once and shared -------------------------------------------------------


class Loads:
    """Stand-ins for the segmenter and the engine of SpeechModels, counting their loads."""

    def __init__(self, engine_gate=None, segmenter_gate=None):
        self.engine_gate = engine_gate
        self.segmenter_gate = segmenter_gate
        self.segmenters: list = []
        self.engines: list = []
        self.engine_errors: list[Exception] = []

    def make_segmenter(self, path):
        if self.segmenter_gate is not None:
            assert self.segmenter_gate.wait(30)
        segmenter = SimpleNamespace(path=path, speech_seconds=0.0, fed=[], flushed=0)
        segmenter.accept = lambda frames: segmenter.fed.append(frames) or []
        segmenter.flush = lambda: setattr(segmenter, "flushed", segmenter.flushed + 1) or []
        self.segmenters.append(segmenter)
        return segmenter

    def make_engine(self, name):
        if self.engine_gate is not None:
            assert self.engine_gate.wait(30)
        if self.engine_errors:
            raise self.engine_errors.pop(0)
        engine = SimpleNamespace(name=name)
        self.engines.append(engine)
        return engine


@pytest.fixture
def speech_in(tmp_path):
    def make(ready=APP_MODELS, **gates):
        root = tmp_path / "models"
        make_models(root, ready)
        loads = Loads(**gates)
        manager = ModelManager(root)
        speech = SpeechModels(
            manager, make_segmenter=loads.make_segmenter, make_engine=loads.make_engine
        )
        return speech, loads, manager

    return make


def test_loading_builds_the_segmenter_and_the_engine_once(speech_in):
    speech, loads, manager = speech_in()
    speech.load_pending()
    speech.load_pending()
    assert len(loads.segmenters) == 1 and len(loads.engines) == 1
    assert loads.segmenters[0].path == manager.dir("silero-vad") / "silero_vad_v5.onnx"
    assert speech.engine() is loads.engines[0]
    assert speech.dictation_segmenter is speech.dictation_segmenter


def test_loading_builds_only_what_is_downloaded(speech_in):
    speech, loads, _ = speech_in(ready=("silero-vad",))
    speech.load_pending()
    assert len(loads.segmenters) == 1 and loads.engines == []


def test_a_model_that_is_not_downloaded_is_a_file_not_found(speech_in):
    speech, loads, _ = speech_in(ready=())
    speech.load_pending()
    assert loads.segmenters == [] and loads.engines == []
    with pytest.raises(FileNotFoundError, match="qwen3-asr"):
        speech.engine()
    with pytest.raises(FileNotFoundError, match="silero-vad"):
        speech.dictation_segmenter.accept(loud())


def test_require_models_says_not_configured_for_what_is_missing(speech_in):
    speech, _, manager = speech_in(ready=("silero-vad",))
    with pytest.raises(RpcError) as caught:
        speech.require_models()
    assert caught.value.code == NOT_CONFIGURED and "qwen3-asr" in caught.value.message
    make_models(manager.dir("qwen3-asr").parent, ("qwen3-asr",))
    speech.require_models()  # on disk is enough: it is loaded on the way


def test_the_engine_call_waits_for_a_load_that_is_under_way(speech_in):
    gate = threading.Event()
    speech, loads, _ = speech_in(engine_gate=gate)
    loader = threading.Thread(target=speech.load_pending)
    loader.start()
    deadline = time.monotonic() + 10
    while not loads.segmenters and time.monotonic() < deadline:
        time.sleep(0.005)
    got: list = []
    waiter = threading.Thread(target=lambda: got.append(speech.engine()))
    waiter.start()
    waiter.join(0.3)
    assert waiter.is_alive() and not got  # still loading: it waits, it does not load a second one
    gate.set()
    waiter.join(10)
    loader.join(10)
    assert got == loads.engines and len(loads.engines) == 1


def test_the_segmenter_call_waits_for_a_load_that_is_under_way(speech_in):
    gate = threading.Event()
    speech, loads, _ = speech_in(segmenter_gate=gate)
    loader = threading.Thread(target=speech.load_pending)
    loader.start()
    fed: list = []
    waiter = threading.Thread(target=lambda: fed.append(speech.dictation_segmenter.accept(loud())))
    waiter.start()
    waiter.join(0.3)
    assert waiter.is_alive()
    gate.set()
    waiter.join(10)
    loader.join(10)
    assert len(loads.segmenters) == 1 and len(loads.segmenters[0].fed) == 1


def test_the_segmenter_is_there_as_soon_as_it_is_built_not_when_the_engine_is_too(speech_in):
    # The first dictation's audio can be cut into speech at once; the engine loads for seconds more.
    gate = threading.Event()
    speech, loads, _ = speech_in(engine_gate=gate)
    loader = threading.Thread(target=speech.load_pending)
    loader.start()
    got: list = []
    waiter = threading.Thread(target=lambda: got.append(speech.dictation_segmenter.accept(loud())))
    waiter.start()
    waiter.join(5)
    assert not waiter.is_alive() and got == [[]]  # fed to the detector, with the engine unloaded
    assert loads.engines == [] and loader.is_alive()
    gate.set()
    loader.join(10)
    assert len(loads.engines) == 1


def test_a_model_that_arrives_later_is_loaded_when_it_is_asked_for(speech_in):
    speech, loads, manager = speech_in(ready=())
    speech.load_pending()
    make_models(manager.dir("qwen3-asr").parent, ("qwen3-asr",))  # downloaded since
    assert speech.engine() is loads.engines[0]


def test_an_engine_that_failed_to_load_is_tried_again(speech_in):
    speech, loads, _ = speech_in()
    loads.engine_errors += [RuntimeError("out of memory"), RuntimeError("still no memory")]
    speech.load_pending()  # the first try fails: logged, and start-up goes on
    with pytest.raises(RuntimeError, match="still no memory"):
        speech.engine()  # asking tries again
    assert speech.engine() is loads.engines[0]  # and again


def test_flushing_the_shared_segmenter_before_anything_was_built_builds_nothing(speech_in):
    speech, loads, _ = speech_in()
    shared = speech.dictation_segmenter
    assert shared.flush() == [] and shared.speech_seconds == 0.0  # nothing built or heard yet
    assert loads.segmenters == []  # and asking did not build one
    speech.load_pending()
    loads.segmenters[0].speech_seconds = 2.5
    assert shared.speech_seconds == 2.5
    shared.flush()
    assert loads.segmenters[0].flushed == 1


def test_close_lets_go_of_the_models(speech_in):
    speech, loads, _ = speech_in()
    speech.load_pending()
    speech.close()
    assert speech.built_segmenter() is None
    # Closed for good: nothing is loaded again behind it.
    speech.load_pending()
    assert len(loads.segmenters) == 1


# --- the local speech model the user chose ----------------------------------------------------


def test_the_engine_is_built_for_the_model_that_was_selected(speech_in):
    speech, loads, _ = speech_in(ready=(*APP_MODELS, "sensevoice"))
    assert speech.engine_name == "qwen3-asr"
    speech.select_engine("sensevoice")
    speech.load_pending()
    assert [engine.name for engine in loads.engines] == ["sensevoice"]
    assert speech.engine() is loads.engines[0]


def test_a_new_selection_keeps_the_old_engine_until_the_new_one_is_built(speech_in):
    gate = threading.Event()
    speech, loads, _ = speech_in(ready=(*APP_MODELS, "sensevoice"))
    speech.load_pending()
    old = speech.engine()
    loads.engine_gate = gate
    speech.select_engine("sensevoice")
    loader = threading.Thread(target=speech.load_pending)
    loader.start()
    deadline = time.monotonic() + 10
    while not loader.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    # A dictation that starts while the new engine loads uses the old one and does not wait.
    assert speech.engine() is old
    gate.set()
    loader.join(10)
    assert speech.engine() is loads.engines[-1] and speech.engine().name == "sensevoice"


def test_selecting_the_engine_that_is_loaded_builds_nothing(speech_in):
    speech, loads, _ = speech_in()
    speech.load_pending()
    speech.select_engine("qwen3-asr")
    speech.load_pending()
    assert len(loads.engines) == 1


def test_a_selection_that_is_not_downloaded_leaves_the_loaded_engine_alone(speech_in):
    speech, loads, _ = speech_in()
    speech.load_pending()
    speech.select_engine("sensevoice")
    speech.load_pending()
    assert len(loads.engines) == 1 and speech.engine() is loads.engines[0]


def test_require_models_asks_for_the_selected_model(speech_in):
    speech, _, manager = speech_in()
    speech.select_engine("sensevoice")
    with pytest.raises(RpcError) as caught:
        speech.require_models()
    assert caught.value.code == NOT_CONFIGURED and "sensevoice" in caught.value.message
    make_models(manager.dir("sensevoice").parent, ("sensevoice",))
    speech.require_models()


def test_the_engine_that_is_asked_for_is_the_selected_one_when_nothing_is_loaded(speech_in):
    speech, loads, _ = speech_in(ready=("silero-vad", "sensevoice"))
    speech.select_engine("sensevoice")
    assert speech.engine() is loads.engines[0] and loads.engines[0].name == "sensevoice"


@pytest.fixture
def local_models(monkeypatch):
    """Tiny stand-ins for two models of the catalogue, served by a mock transport."""
    served: list[str] = []
    specs = {
        "silero-vad": ModelSpec(
            name="silero-vad",
            files=("silero_vad_v5.onnx",),
            downloads=(ModelFile("https://models.test/vad.onnx", "silero_vad_v5.onnx", 10),),
        ),
        "qwen3-asr": REGISTRY["qwen3-asr"],
        "sensevoice": ModelSpec(
            name="sensevoice",
            files=("model.int8.onnx", "tokens.txt"),
            downloads=(
                ModelFile("https://models.test/sense.onnx", "model.int8.onnx", 1_500_000),
                ModelFile("https://models.test/tokens.txt", "tokens.txt", 100),
            ),
        ),
        "paraformer-zh": ModelSpec(
            name="paraformer-zh",
            files=("model.int8.onnx",),
            downloads=(ModelFile("https://models.test/gone", "model.int8.onnx", 5),),
        ),
    }
    sizes = {file.url: file.size for spec in specs.values() for file in spec.downloads}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        served.append(url)
        if url not in sizes or url.endswith("/gone"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"\0" * sizes[url])

    for name, spec in specs.items():
        monkeypatch.setitem(models.REGISTRY, name, spec)
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    yield SimpleNamespace(served=served, http=http)
    http.close()


def local_app(make_app, local_models, tmp_path, kit=None, ready=APP_MODELS):
    return make_app(
        kit or make_kit(), ready=ready, manager=ModelManager(tmp_path / "data" / "models", local_models.http)
    )


async def test_the_local_engine_is_the_model_the_setting_names_from_the_first_load(tmp_path):
    kit = make_kit()
    data = tmp_path / "data"
    make_models(data / "models", (*APP_MODELS, "sensevoice"))
    app = App(data_dir=data, fake_llm=True, load_kit=lambda: kit, agent_actions=None)
    try:
        await app.call("settings.set", {"key": "stt_local_model", "value": "sensevoice"})
        app.start()
        await wait_until(lambda: kit.segmenters and kit.engines)
        assert [engine.kwargs["name"] for engine in kit.engines] == ["sensevoice"]
    finally:
        await app.aclose()


async def test_models_status_follows_the_chosen_local_model(make_app, tmp_path):
    app = make_app(ready=("silero-vad", "sensevoice"))
    await app.call("settings.set", {"key": "stt_local_model", "value": "sensevoice"})
    await app.call("models.local_use", {"name": "sensevoice"})
    assert await app.call("models.status") == {
        "models": [
            {"name": "silero-vad", "ready": True},
            {"name": "sensevoice", "ready": True},
        ]
    }


async def test_models_local_lists_the_catalogue_with_what_is_on_disk(make_app):
    app = make_app(ready=(*APP_MODELS, "sensevoice"))
    result = await app.call("models.local")
    names = [m["name"] for m in result["models"]]
    assert names == [entry.name for entry in models.LOCAL_STT_MODELS]
    assert "silero-vad" not in names
    sense = next(m for m in result["models"] if m["name"] == "sensevoice")
    assert sense == {
        "name": "sensevoice",
        "title": "SenseVoice",
        "languages": ["zh", "en", "ja", "ko", "yue"],
        "note": "fast",
        "size": 163_002_883,
        "ready": True,
    }
    assert {m["name"] for m in result["models"] if m["ready"]} == {"qwen3-asr", "sensevoice"}
    assert result["active"] == "qwen3-asr" and result["downloading"] is None


async def test_choosing_a_local_model_downloads_it_then_makes_it_the_local_engine(
    make_app, local_models, tmp_path, notes
):
    kit = make_kit()
    app = local_app(make_app, local_models, tmp_path, kit)
    listen(app, notes)
    await wait_until(lambda: kit.engines)
    result = await app.call("models.local_use", {"name": "sensevoice"})
    assert result["ok"] is True
    assert result["settings"]["stt_local_model"] == "sensevoice"
    assert result["settings"]["stt_engine"] == "local"
    assert (tmp_path / "data" / "models" / "sensevoice" / "tokens.txt").is_file()
    await asyncio.sleep(0.05)
    progress = [p for p in notes.methods("models.progress") if p["name"] == "sensevoice"]
    assert progress and progress[-1] == {"name": "sensevoice", "done": 100, "total": 100}
    assert any(p["total"] == 1_500_000 and p["done"] == 1_500_000 for p in progress)
    await wait_until(lambda: kit.engines[-1].kwargs["name"] == "sensevoice")
    assert (await app.call("models.local"))["active"] == "sensevoice"


async def test_the_dictation_after_the_switch_uses_the_new_engine(make_app, local_models, tmp_path):
    kit = make_kit()
    app = local_app(make_app, local_models, tmp_path, kit)
    await wait_until(lambda: kit.engines)
    await app.call("models.local_use", {"name": "sensevoice"})
    await wait_until(lambda: kit.engines[-1].kwargs["name"] == "sensevoice")
    assert (await dictate(app))["text"] == WORDS + WORDS


async def test_a_model_that_is_on_disk_is_chosen_without_any_request(
    make_app, local_models, tmp_path
):
    app = local_app(make_app, local_models, tmp_path, ready=(*APP_MODELS, "sensevoice"))
    result = await app.call("models.local_use", {"name": "sensevoice"})
    assert result["settings"]["stt_local_model"] == "sensevoice"
    assert local_models.served == []


async def test_a_download_that_fails_leaves_the_engine_in_use_and_says_why(
    make_app, local_models, tmp_path
):
    kit = make_kit()
    app = local_app(make_app, local_models, tmp_path, kit)
    await wait_until(lambda: kit.engines)
    with pytest.raises(RpcError) as caught:
        await app.call("models.local_use", {"name": "paraformer-zh"})
    assert "404" in caught.value.message
    settings = (await app.call("settings.get"))["settings"]
    assert settings["stt_local_model"] == "qwen3-asr" and settings["stt_engine"] == "cloud"
    assert not (tmp_path / "data" / "models" / "paraformer-zh").exists()
    assert len(kit.engines) == 1
    assert (await app.call("models.local"))["downloading"] is None  # and another try is allowed


@pytest.mark.parametrize("params", [{}, {"name": 3}, {"name": "silero-vad"}, {"name": "nope"}])
async def test_models_local_use_takes_only_a_model_of_the_catalogue(make_app, params):
    with pytest.raises(RpcError) as caught:
        await make_app().call("models.local_use", params)
    assert caught.value.code == INVALID_PARAMS


class SlowManager(ModelManager):
    """A manager whose download of a model goes on until it is told to end, a chunk at a time."""

    def __init__(self, root):
        super().__init__(root)
        self.started = threading.Event()
        self.finish = threading.Event()

    def ensure(self, name, progress=None):
        if self.is_ready(name):
            return self.dir(name)
        for done in range(1, 1000):
            if progress is not None:
                progress(done, 1000)  # raises when the download is cancelled
            self.started.set()
            if self.finish.wait(0.01):
                break
        make_models(self.dir(name).parent, (name,))
        return self.dir(name)


async def test_a_download_can_be_cancelled_and_leaves_the_engine_in_use(make_app, tmp_path, notes):
    manager = SlowManager(tmp_path / "data" / "models")
    kit = make_kit()
    app = make_app(kit, manager=manager)
    listen(app, notes)
    await wait_until(lambda: kit.engines)
    task = asyncio.create_task(app.call("models.local_use", {"name": "sensevoice"}))
    await wait_until(manager.started.is_set)
    assert (await app.call("models.local"))["downloading"] == "sensevoice"
    assert await app.call("models.local_cancel") == {"cancelled": True}
    result = await asyncio.wait_for(task, 10)
    assert result == {"ok": False, "cancelled": True}
    assert not manager.is_ready("sensevoice")
    settings = (await app.call("settings.get"))["settings"]
    assert settings["stt_local_model"] == "qwen3-asr"
    assert (await app.call("models.local"))["downloading"] is None


async def test_cancelling_with_nothing_downloading_is_no_error(make_app):
    assert await make_app().call("models.local_cancel") == {"cancelled": False}


async def test_only_one_local_model_is_downloaded_at_a_time(make_app, tmp_path):
    manager = SlowManager(tmp_path / "data" / "models")
    app = make_app(make_kit(), manager=manager)
    task = asyncio.create_task(app.call("models.local_use", {"name": "sensevoice"}))
    await wait_until(manager.started.is_set)
    with pytest.raises(RpcError) as caught:
        await app.call("models.local_use", {"name": "paraformer-zh"})
    assert caught.value.code == BUSY
    manager.finish.set()
    assert (await asyncio.wait_for(task, 10))["ok"] is True


async def test_dictation_does_not_wait_for_a_download(make_app, tmp_path):
    manager = SlowManager(tmp_path / "data" / "models")
    kit = make_kit()
    app = make_app(kit, manager=manager)
    await wait_until(lambda: kit.engines)
    task = asyncio.create_task(app.call("models.local_use", {"name": "sensevoice"}))
    await wait_until(manager.started.is_set)
    result = await asyncio.wait_for(dictate(app), 5)  # the download is still under way
    assert result["text"] == WORDS + WORDS and not task.done()
    manager.finish.set()
    await asyncio.wait_for(task, 10)


async def test_dictation_does_not_wait_for_the_catalogue(make_app, tmp_path):
    manager = SlowManager(tmp_path / "data" / "models")
    app = make_app(make_kit(), manager=manager)
    assert [m["name"] for m in (await app.call("models.local"))["models"]]
    assert (await asyncio.wait_for(dictate(app), 5))["text"] == WORDS + WORDS


async def test_a_model_chosen_in_the_settings_directly_is_picked_up_by_the_next_dictation(
    make_app,
):
    kit = make_kit()
    app = make_app(kit, ready=(*APP_MODELS, "sensevoice"))
    await wait_until(lambda: kit.engines)
    await app.call("settings.set", {"key": "stt_local_model", "value": "sensevoice"})
    await dictate(app)
    await wait_until(lambda: kit.engines[-1].kwargs["name"] == "sensevoice")


async def test_a_model_chosen_in_the_settings_that_is_not_downloaded_changes_nothing(make_app):
    kit = make_kit()
    app = make_app(kit)
    await wait_until(lambda: kit.engines)
    await app.call("settings.set", {"key": "stt_local_model", "value": "sensevoice"})
    assert (await dictate(app))["text"] == WORDS + WORDS
    assert len(kit.engines) == 1


async def test_a_model_that_is_not_in_use_can_be_deleted(make_app, tmp_path):
    app = make_app(ready=(*APP_MODELS, "sensevoice"))
    directory = tmp_path / "data" / "models" / "sensevoice"
    assert directory.is_dir()
    assert await app.call("models.local_delete", {"name": "sensevoice"}) == {"deleted": True}
    assert not directory.exists()
    sense = next(m for m in (await app.call("models.local"))["models"] if m["name"] == "sensevoice")
    assert sense["ready"] is False
    assert (tmp_path / "data" / "models" / "silero-vad").is_dir()


async def test_deleting_a_model_that_is_not_there_is_no_error(make_app):
    app = make_app()
    assert await app.call("models.local_delete", {"name": "sensevoice"}) == {"deleted": False}


async def test_the_model_in_use_is_not_deleted(make_app, tmp_path):
    app = make_app()
    with pytest.raises(RpcError) as caught:
        await app.call("models.local_delete", {"name": "qwen3-asr"})
    assert caught.value.code == BUSY and "in use" in caught.value.message
    assert (tmp_path / "data" / "models" / "qwen3-asr").is_dir()


async def test_the_voice_detector_is_never_deleted(make_app, tmp_path):
    app = make_app()
    with pytest.raises(RpcError) as caught:
        await app.call("models.local_delete", {"name": "silero-vad"})
    assert caught.value.code == INVALID_PARAMS
    assert (tmp_path / "data" / "models" / "silero-vad").is_dir()


@pytest.mark.parametrize("params", [{}, {"name": 1}, {"name": "nope"}])
async def test_models_local_delete_takes_only_a_model_of_the_catalogue(make_app, params):
    with pytest.raises(RpcError) as caught:
        await make_app().call("models.local_delete", params)
    assert caught.value.code == INVALID_PARAMS


async def test_a_model_being_downloaded_is_not_deleted(make_app, tmp_path):
    manager = SlowManager(tmp_path / "data" / "models")
    app = make_app(make_kit(), manager=manager)
    task = asyncio.create_task(app.call("models.local_use", {"name": "sensevoice"}))
    await wait_until(manager.started.is_set)
    with pytest.raises(RpcError) as caught:
        await app.call("models.local_delete", {"name": "sensevoice"})
    assert caught.value.code == BUSY
    manager.finish.set()
    await asyncio.wait_for(task, 10)


async def test_a_model_the_disk_will_not_let_go_of_is_an_error_that_says_so(
    make_app, tmp_path, monkeypatch
):
    app = make_app(ready=(*APP_MODELS, "sensevoice"))

    def refuse(path, *args, **kwargs):
        raise PermissionError("in use by another program")

    monkeypatch.setattr(app_module.shutil, "rmtree", refuse)
    with pytest.raises(RpcError) as caught:
        await app.call("models.local_delete", {"name": "sensevoice"})
    assert "sensevoice" in caught.value.message and "in use by another" in caught.value.message


# --- the level meter --------------------------------------------------------------------------


class MeterMic:
    def __init__(self, on_frames):
        self.on_frames = on_frames
        self.started = 0
        self.stopped = 0
        self.fail_to_start = False
        self.error = None

    @property
    def open(self):
        return self.started > self.stopped

    def start(self):
        if self.fail_to_start:
            raise audio.NoMicrophoneError("unplugged")
        self.started += 1

    def stop(self):
        if self.open:
            self.stopped += 1


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def meter(notes):
    router = Router()
    router.set_notifier(lambda method, params: notes.append((method, params)))
    clock = FakeClock()
    mics: list[MeterMic] = []
    state = SimpleNamespace(unplugged=False)

    def make_recorder(on_frames):
        mic = MeterMic(on_frames)
        mic.fail_to_start = state.unplugged
        mics.append(mic)
        return mic

    monitor = LevelMonitor(
        router,
        make_recorder=make_recorder,
        level_of=lambda frames: float(frames.max()),
        no_microphone=audio.NoMicrophoneError,
        hz=10.0,
        clock=clock,
    )
    return SimpleNamespace(monitor=monitor, mics=mics, clock=clock, notes=notes, state=state)


async def levels(meter, count):
    await wait_until(lambda: len(meter.notes.methods("audio.level")) >= count)
    return meter.notes.methods("audio.level")


async def test_the_meter_sends_the_level_with_no_session(meter):
    await meter.monitor.set_on(True)
    assert len(meter.mics) == 1 and meter.mics[0].open
    meter.mics[0].on_frames(np.full(320, 0.25, dtype=np.float32))
    assert await levels(meter, 1) == [{"session_id": None, "level": 0.25}]


async def test_the_meter_sends_at_most_hz_levels_a_second(meter):
    await meter.monitor.set_on(True)
    on_frames = meter.mics[0].on_frames
    for _ in range(5):
        on_frames(np.full(320, 0.1, dtype=np.float32))
    meter.clock.now += 0.05  # half of a tenth of a second
    on_frames(np.full(320, 0.1, dtype=np.float32))
    meter.clock.now += 0.06
    on_frames(np.full(320, 0.3, dtype=np.float32))
    got = await levels(meter, 2)
    await asyncio.sleep(0.05)
    assert [n["level"] for n in meter.notes.methods("audio.level")] == pytest.approx([0.1, 0.3])
    assert len(got) >= 2


async def test_switching_the_meter_off_closes_the_microphone_and_ends_the_levels(meter):
    await meter.monitor.set_on(True)
    mic = meter.mics[0]
    await meter.monitor.set_on(False)
    assert not mic.open
    mic.on_frames(np.full(320, 0.9, dtype=np.float32))  # a late block
    await asyncio.sleep(0.05)
    assert meter.notes.methods("audio.level") == []
    await meter.monitor.set_on(False)  # again: nothing to do


async def test_switching_the_meter_on_twice_opens_one_microphone(meter):
    await meter.monitor.set_on(True)
    await meter.monitor.set_on(True)
    assert len(meter.mics) == 1


async def test_no_microphone_is_reported_and_the_meter_can_be_tried_again(meter):
    meter.state.unplugged = True
    with pytest.raises(RpcError) as caught:
        await meter.monitor.set_on(True)
    assert caught.value.code == NO_MICROPHONE and not meter.mics[0].open
    meter.state.unplugged = False
    await meter.monitor.set_on(True)
    assert meter.mics[-1].open


async def test_a_meter_that_failed_to_open_is_not_wanted_when_a_dictation_ends(meter):
    meter.state.unplugged = True
    with pytest.raises(RpcError):
        await meter.monitor.set_on(True)
    meter.state.unplugged = False
    await meter.monitor.suspend()
    await meter.monitor.resume()
    assert not meter.mics[-1].open  # the page was told it failed; it asks again if it wants to


async def test_the_meter_gives_the_microphone_up_to_a_dictation_and_takes_it_back(meter):
    await meter.monitor.set_on(True)
    first = meter.mics[0]
    await meter.monitor.suspend()
    assert not first.open
    first.on_frames(np.full(320, 0.9, dtype=np.float32))
    await asyncio.sleep(0.05)
    assert meter.notes.methods("audio.level") == []
    await meter.monitor.resume()
    assert len(meter.mics) == 2 and meter.mics[1].open


async def test_a_meter_that_was_off_stays_off_after_a_dictation(meter):
    await meter.monitor.suspend()
    await meter.monitor.resume()
    assert meter.mics == []


async def test_switching_the_meter_on_during_a_dictation_waits_for_its_end(meter):
    await meter.monitor.suspend()
    await meter.monitor.set_on(True)
    assert meter.mics == []  # the dictation has the microphone
    await meter.monitor.resume()
    assert len(meter.mics) == 1 and meter.mics[0].open


async def test_a_microphone_that_cannot_be_taken_back_is_logged_not_raised(meter, caplog):
    await meter.monitor.set_on(True)
    await meter.monitor.suspend()
    meter.state.unplugged = True
    await meter.monitor.resume()  # the dictation is over; the meter's microphone is gone
    assert not meter.mics[-1].open and "meter" in caplog.text
    meter.state.unplugged = False
    await meter.monitor.restart()  # the user picks another one in the settings
    assert meter.mics[-1].open


async def test_restart_moves_the_meter_to_the_new_microphone(meter):
    await meter.monitor.set_on(True)
    await meter.monitor.restart()
    assert len(meter.mics) == 2 and not meter.mics[0].open and meter.mics[1].open


async def test_restart_leaves_a_meter_that_is_off_alone(meter):
    await meter.monitor.restart()
    assert meter.mics == []


async def test_resume_leaves_the_meter_off_while_a_dictation_still_has_the_microphone(meter):
    await meter.monitor.set_on(True)
    await meter.monitor.suspend()
    await meter.monitor.resume(lambda: False)  # a new dictation took the microphone meanwhile
    assert len(meter.mics) == 1 and not meter.mics[0].open
    await meter.monitor.resume(lambda: True)
    assert len(meter.mics) == 2 and meter.mics[1].open


async def test_resume_asks_whether_it_may_once_it_holds_the_meter(meter):
    await meter.monitor.set_on(True)
    await meter.monitor.suspend()
    state = SimpleNamespace(free=True)
    await meter.monitor._lock.acquire()  # someone else is busy with the meter
    resuming = asyncio.create_task(meter.monitor.resume(lambda: state.free))
    await asyncio.sleep(0.05)
    state.free = False  # a dictation starts while the resume waits its turn
    meter.monitor._lock.release()
    await resuming
    assert not any(mic.open for mic in meter.mics)


async def test_closing_the_meter_closes_the_microphone(meter):
    await meter.monitor.set_on(True)
    await meter.monitor.aclose()
    assert not meter.mics[0].open


# --- the app: what it answers while the speech side loads -------------------------------------


EXPECTED_METHODS = {
    "core.hello",
    "core.shutdown",
    "models.status",
    "models.ensure",
    "audio.devices",
    "audio.set_device",
    "audio.monitor",
    "dictation.start",
    "dictation.stop",
    "dictation.cancel",
    "brain.finalize_dictation",
    "history.list",
    "history.latest",
    "history.delete",
    "dictionary.list",
    "dictionary.add",
    "dictionary.import",
    "dictionary.delete",
    "dictionary.auto_add",
    "dictionary.dismiss",
    "settings.get",
    "settings.set",
    "secrets.status",
    "secrets.set_openrouter_key",
    "secrets.clear_openrouter_key",
    "status.get",
    "status.check_openrouter",
    "integrations.list",
    "integrations.catalog",
    "integrations.connect",
    "integrations.connect_cancel",
    "integrations.add",
    "integrations.set_enabled",
    "integrations.disconnect",
    "integrations.refresh",
    "integrations.tools",
    "integrations.set_tool_enabled",
    "integrations.set_tools_enabled",
    "integrations.audit",
}


async def test_every_method_is_there_from_the_start_even_while_the_kit_loads(tmp_path):
    release = threading.Event()

    def slow_kit():
        assert release.wait(30)
        return make_kit()

    app = App(data_dir=tmp_path, fake_llm=True, load_kit=slow_kit)
    app.start()
    try:
        assert EXPECTED_METHODS <= set(app.methods())
        # The app asks for all of these as soon as the core is up, long before the speech side is.
        assert await app.call("core.hello") == {
            "protocol": PROTOCOL_VERSION,
            "version": raphael_core.__version__,
        }
        assert (await app.call("settings.get"))["settings"]["monthly_limit_usd"] == 10.0
        assert await app.call("audio.set_device", {"name": "USB Mic"}) == {}
        assert [m["ready"] for m in (await app.call("models.status"))["models"]] == [False, False]
        assert (await app.call("secrets.status")) == {"has_key": True}
        pending = asyncio.ensure_future(app.call("dictation.cancel"))
        await asyncio.sleep(0.2)
        assert not pending.done()  # the dictation side is not there yet; it waits, it does not fail
        release.set()
        assert await asyncio.wait_for(pending, 10) == {}
    finally:
        release.set()
        await app.aclose()


async def test_a_request_that_gives_up_waiting_for_the_speech_side_does_not_spoil_it(tmp_path):
    release = threading.Event()
    app = App(
        data_dir=tmp_path,
        fake_llm=True,
        load_kit=lambda: release.wait(30) and make_kit(),
    )
    app.start()
    try:
        waiting = asyncio.ensure_future(app.call("dictation.cancel"))
        await asyncio.sleep(0.1)
        waiting.cancel()  # the page closed, say
        with pytest.raises(asyncio.CancelledError):
            await waiting
        release.set()
        assert await asyncio.wait_for(app.call("dictation.cancel"), 10) == {}
        assert (await app.call("audio.devices"))["devices"] == []
    finally:
        release.set()
        await app.aclose()


async def test_a_speech_side_that_cannot_load_is_an_error_but_the_core_goes_on(tmp_path, caplog):
    def broken_kit():
        raise ImportError("PortAudio library not found")

    app = App(data_dir=tmp_path, fake_llm=True, load_kit=broken_kit)
    app.start()
    try:
        with pytest.raises(RpcError) as caught:
            await app.call("dictation.start", {"session_id": "s"})
        assert "PortAudio" in caught.value.message
        with pytest.raises(RpcError):
            await app.call("audio.devices")
        assert (await app.call("core.hello"))["protocol"] == PROTOCOL_VERSION
        assert "PortAudio" in caplog.text
    finally:
        await app.aclose()


async def test_wait_for_libraries_returns_when_the_kit_is_imported(make_app, caplog):
    kit = make_kit()
    app = make_app(kit)
    started = time.monotonic()
    await asyncio.to_thread(app.wait_for_libraries, 10)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert time.monotonic() - started < 5
    assert app._kit.done() and app._kit.result() is kit


async def test_wait_for_libraries_gives_up_after_its_timeout(tmp_path, caplog):
    release = threading.Event()
    app = App(data_dir=tmp_path, fake_llm=True, load_kit=lambda: release.wait(30) and make_kit())
    app.start()
    try:
        started = time.monotonic()
        with caplog.at_level(logging.ERROR, logger=app_module.logger.name):
            await asyncio.to_thread(app.wait_for_libraries, 0.2)
        assert 0.15 < time.monotonic() - started < 5
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1 and "0.2 s" in errors[0].getMessage()  # said, not silent
    finally:
        release.set()
        await app.aclose()


async def test_wait_for_libraries_returns_when_the_import_failed(tmp_path):
    def broken():
        raise ImportError("no PortAudio")

    app = App(data_dir=tmp_path, fake_llm=True, load_kit=broken)
    app.start()
    try:
        await asyncio.to_thread(app.wait_for_libraries, 10)  # does not raise; the use reports it
    finally:
        await app.aclose()


async def test_the_version_is_the_packages(make_app):
    hello = await make_app().call("core.hello")
    assert hello == {"protocol": 1, "version": raphael_core.__version__}


async def test_the_plugins_are_there_and_keep_their_secrets_in_memory_with_the_fake_llm(
    make_app, memory_keyring, tmp_path
):
    app = make_app()
    assert await app.call("integrations.list") == {"servers": []}
    added = await app.call(
        "integrations.add",
        {
            "name": "Notes",
            "transport": "stdio",
            "command": "unused",
            "env": [{"name": "TOKEN", "value": "s3cret-value"}],
        },
    )
    assert added["server"]["env"] == ["TOKEN"]
    # The run with the fake LLM never touches the user's vault, nor writes a secret to disk.
    assert memory_keyring.credentials == {}
    assert "s3cret-value" not in (tmp_path / "data" / "integrations.json").read_text(encoding="utf-8")
    assert await app.call("integrations.audit") == {"entries": []}


async def test_the_plugins_keep_their_secrets_in_the_credential_manager_otherwise(
    make_app, memory_keyring
):
    app = make_app(fake_llm=False)
    added = await app.call(
        "integrations.add",
        {
            "name": "Notes",
            "transport": "stdio",
            "command": "unused",
            "env": [{"name": "TOKEN", "value": "s3cret-value"}],
        },
    )
    server_id = added["server"]["id"]
    assert memory_keyring.credentials == {
        f"Raphael plugin {server_id}:env:TOKEN": ("secret", "s3cret-value")
    }
    await app.call("integrations.disconnect", {"id": server_id})
    assert memory_keyring.credentials == {}


async def test_the_assistant_reads_the_brains_catalog_to_tell_whether_a_model_takes_tools(
    make_app,
):
    app = make_app()
    luna = "openai/gpt-6-luna"
    entry = catalog.CatalogEntry(
        id=luna,
        kind="llm",
        name="GPT-6 Luna",
        provider="OpenAI",
        price=catalog.ModelPrice(input_per_mtok=0.1, output_per_mtok=0.5),
        variable_price=False,
        context_length=None,
        created_at_ms=None,
        capabilities=frozenset({"structured_output", "reasoning", "reasoning_off", "max_tokens"}),
    )
    saved = {
        "reading": pricing.CACHE_READING,
        "fetched_at_ms": 1,
        "models": [catalog.entry_to_json(entry)],
    }
    with app._conn:
        app._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            (pricing.CACHE_KEY, json.dumps(saved)),
        )
    # The list OpenRouter gave last says the model takes no tools: the next request leaves the
    # plugins out. The assistant and the brain read the same catalog.
    assert [e.id for e in app.brain.known_models()] == [luna]
    assert app.agent._tool_support.supports(luna) is False


async def test_before_any_list_was_read_the_assistant_takes_raphaels_own_models_to_take_tools(
    make_app,
):
    app = make_app()
    assert app.brain.known_models() is None
    assert app.agent._tool_support.supports("openai/gpt-6-luna") is True  # the builtin table
    assert app.agent._tool_support.supports("some/other-model") is None


# --- start-up loads the heavy things, not the first key press ---------------------------------


async def test_the_models_load_at_start_without_any_dictation(make_app):
    kit = make_kit()
    app = make_app(kit)
    await wait_until(lambda: kit.segmenters and kit.engines)
    segmenter, engine = kit.segmenters[0], kit.engines[0]
    assert DICTATION_SEGMENT_SECONDS == 10.0 and STT_THREADS == 4
    assert segmenter.kwargs == {"max_segment_s": 10.0}  # speech without pauses is cut
    assert segmenter.path == app.manager.dir("silero-vad") / "silero_vad_v5.onnx"
    assert engine.kwargs == {"name": "qwen3-asr", "num_threads": 4}


async def test_nothing_is_loaded_for_a_model_that_is_not_downloaded(make_app):
    kit = make_kit()
    app = make_app(kit, ready=())
    await asyncio.sleep(0.3)
    await app.call("core.hello")
    assert kit.segmenters == [] and kit.engines == []


async def test_the_opencc_converter_is_warmed_up_at_start(make_app):
    textnorm._converter.cache_clear()
    make_app(make_kit())
    await wait_until(lambda: textnorm._converter.cache_info().currsize == 1)


async def test_the_openrouter_client_is_warmed_up_at_start(make_app):
    app = make_app(make_kit())
    await wait_until(lambda: app.client.warmed_up)


# --- models.status and models.ensure ----------------------------------------------------------


async def test_models_status_lists_the_two_models_the_app_uses(make_app):
    app = make_app(ready=())
    assert await app.call("models.status") == {
        "models": [
            {"name": "silero-vad", "ready": False},
            {"name": "qwen3-asr", "ready": False},
        ]
    }


async def test_models_status_follows_what_is_on_disk(make_app, tmp_path):
    app = make_app(ready=("silero-vad",))
    status = (await app.call("models.status"))["models"]
    assert status == [{"name": "silero-vad", "ready": True}, {"name": "qwen3-asr", "ready": False}]
    make_models(tmp_path / "data" / "models", ("qwen3-asr",))
    status = (await app.call("models.status"))["models"]
    assert [m["ready"] for m in status] == [True, True]


@pytest.fixture
def downloads(monkeypatch):
    """Tiny stand-ins for the two registry models, served by a mock transport."""
    served: list[str] = []
    specs = {
        "silero-vad": ModelSpec(
            name="silero-vad",
            files=("silero_vad_v5.onnx",),
            downloads=(ModelFile("https://models.test/vad.onnx", "silero_vad_v5.onnx", 200_000),),
        ),
        "qwen3-asr": ModelSpec(
            name="qwen3-asr",
            files=("encoder.onnx", "tokenizer/vocab.json"),
            downloads=(
                ModelFile("https://models.test/encoder.onnx", "encoder.onnx", 1_500_000),
                ModelFile("https://models.test/vocab.json", "tokenizer/vocab.json", 100),
            ),
        ),
    }
    sizes = {file.url: file.size for spec in specs.values() for file in spec.downloads}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        served.append(url)
        if url not in sizes:
            return httpx.Response(404)
        return httpx.Response(200, content=b"\0" * sizes[url])

    for name, spec in specs.items():
        monkeypatch.setitem(models.REGISTRY, name, spec)
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    yield SimpleNamespace(served=served, http=http, specs=specs, sizes=sizes)
    http.close()


async def test_models_ensure_downloads_and_reports_progress(make_app, downloads, tmp_path, notes):
    kit = make_kit()
    root = tmp_path / "data" / "models"
    app = make_app(kit, ready=(), manager=ModelManager(root, downloads.http))
    listen(app, notes)
    result = await app.call("models.ensure", {"names": ["silero-vad", "qwen3-asr"]})
    assert result == {"ok": True}
    assert (root / "silero-vad" / "silero_vad_v5.onnx").is_file()
    assert (root / "qwen3-asr" / "tokenizer" / "vocab.json").is_file()
    await asyncio.sleep(0.05)
    progress = notes.methods("models.progress")
    first = [p for p in progress if p["name"] == "silero-vad"]
    assert first and first[-1] == {"name": "silero-vad", "done": 200_000, "total": 200_000}
    assert all(set(p) == {"name", "done", "total"} for p in progress)
    # In order, and all of it before the answer: the page ends on a full bar.
    order = [p["name"] for p in progress]
    assert order == sorted(order, key=APP_MODELS.index)
    assert [m["ready"] for m in (await app.call("models.status"))["models"]] == [True, True]


async def test_models_ensure_throttles_the_progress_it_sends(make_app, downloads, tmp_path, notes):
    app = make_app(make_kit(), ready=(), manager=ModelManager(tmp_path / "m", downloads.http))
    listen(app, notes)
    await app.call("models.ensure", {"names": ["qwen3-asr"]})
    await asyncio.sleep(0.05)
    # 1.5 MB in 512 KiB chunks is 3 chunks; a burst this quick is not sent chunk by chunk.
    sent = [p for p in notes.methods("models.progress") if p["total"] == 1_500_000]
    assert 1 <= len(sent) < 3 and sent[-1]["done"] == 1_500_000


async def test_the_speech_models_load_once_they_are_downloaded(make_app, downloads, tmp_path):
    kit = make_kit()
    app = make_app(kit, ready=(), manager=ModelManager(tmp_path / "m", downloads.http))
    await asyncio.sleep(0.2)
    assert kit.segmenters == [] and kit.engines == []
    await app.call("models.ensure", {"names": ["silero-vad", "qwen3-asr"]})
    await wait_until(lambda: kit.segmenters and kit.engines)  # with no dictation asked for
    result = await dictate(app)
    assert result["text"] == WORDS + WORDS and len(kit.segmenters) == 1


async def test_models_ensure_of_nothing_is_fine(make_app):
    assert await make_app().call("models.ensure", {"names": []}) == {"ok": True}


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"names": "qwen3-asr"},
        {"names": [1]},
        {"names": ["no-such-model"]},
        {"names": ["breeze-asr-25"]},  # evaluation only: the app does not download it
        {"names": ["sensevoice"]},
    ],
)
async def test_models_ensure_checks_its_params(make_app, params):
    with pytest.raises(RpcError) as caught:
        await make_app().call("models.ensure", params)
    assert caught.value.code == INVALID_PARAMS


async def test_a_download_that_fails_is_an_error_and_loads_nothing(
    make_app, downloads, tmp_path, monkeypatch
):
    kit = make_kit()
    spec = downloads.specs["silero-vad"]
    monkeypatch.setitem(
        models.REGISTRY,
        "silero-vad",
        ModelSpec(
            "silero-vad", spec.files, downloads=(ModelFile("https://models.test/gone", "a", 5),)
        ),
    )
    app = make_app(kit, ready=(), manager=ModelManager(tmp_path / "m", downloads.http))
    with pytest.raises(RpcError) as caught:
        await app.call("models.ensure", {"names": ["silero-vad"]})
    assert "404" in caught.value.message
    await asyncio.sleep(0.1)
    assert kit.segmenters == []


# --- the microphone ---------------------------------------------------------------------------


async def test_audio_devices_lists_the_microphones_and_the_selected_one(make_app):
    kit = make_kit(
        devices=[
            SimpleNamespace(index=1, name="Headset", is_default=True),
            SimpleNamespace(index=2, name="Array", is_default=False),
        ]
    )
    app = make_app(kit)
    assert await app.call("audio.devices") == {
        "devices": [
            {"index": 1, "name": "Headset", "is_default": True},
            {"index": 2, "name": "Array", "is_default": False},
        ],
        "selected": None,
        "automatic_device": None,
    }
    await app.call("audio.set_device", {"name": "Array"})
    assert (await app.call("audio.devices"))["selected"] == "Array"


async def test_audio_devices_can_read_automatic_resolution_without_restarting_meter(make_app):
    kit = make_kit()
    kit.automatic_device = "Array Mic"
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    recorder = kit.mics[-1]

    def must_not_refresh():
        raise AssertionError("A status-only query must not refresh PortAudio")

    kit.list_input_devices = must_not_refresh
    listing = await app.call("audio.devices", {"refresh": False})
    assert listing["automatic_device"] == "Array Mic"
    assert kit.mics[-1] is recorder
    assert recorder.stopped == 0


async def test_audio_devices_reads_with_the_meter_closed_and_gives_the_meter_back(make_app):
    """Listing the microphones reads the devices again, which is not done while a stream is open:
    the settings meter's stream is closed for it and opened again after."""
    kit = make_kit()
    open_while_listing = []

    def list_devices():
        open_while_listing.append([mic.open for mic in kit.mics])
        return []

    kit.list_input_devices = list_devices
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    await app.call("audio.devices")
    assert open_while_listing == [[False]]  # the meter's first microphone was closed meanwhile
    assert len(kit.mics) == 2 and kit.mics[1].open  # and the meter is back


async def test_audio_devices_opens_no_meter_that_is_not_wanted(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.devices")
    assert kit.mics == []
    await app.call("audio.monitor", {"on": True})
    await app.call("audio.monitor", {"on": False})
    await app.call("audio.devices")
    assert len(kit.mics) == 1 and not kit.mics[0].open


async def test_audio_devices_during_a_dictation_leaves_the_meter_for_the_dictations_end(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    await app.call("dictation.start", {"session_id": "s"})
    assert len(kit.mics) == 2 and kit.mics[1].open and not kit.mics[0].open
    await app.call("audio.devices")
    assert len(kit.mics) == 2 and kit.mics[1].open  # the dictation's microphone is untouched
    await app.call("dictation.stop", {"session_id": "s", "target_app": None})
    assert len(kit.mics) == 3 and kit.mics[2].open  # the meter comes back with the dictation's end


async def test_audio_devices_still_answers_when_the_meter_cannot_come_back(make_app, caplog):
    kit = make_kit(devices=[SimpleNamespace(index=1, name="Headset", is_default=True)])
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    original = kit.MicRecorder

    def flaky(on_frames, device=None):
        if len(kit.mics) >= 1:
            raise OSError("PortAudio went away")
        return original(on_frames, device)

    kit.MicRecorder = flaky
    listing = await app.call("audio.devices")
    assert [d["name"] for d in listing["devices"]] == ["Headset"]
    assert "The level meter could not take the microphone back" in caplog.text


async def test_the_selected_microphone_is_the_one_a_dictation_opens(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.set_device", {"name": "Array"})
    await dictate(app, "a")
    assert kit.mics[-1].device == "Array"
    await app.call("audio.set_device", {"name": None})  # null: the system default
    await dictate(app, "b")
    assert kit.mics[-1].device is None
    await app.call("audio.set_device", {"name": "Headset"})
    await app.call("audio.set_device", {})  # left out is null too
    assert (await app.call("audio.devices"))["selected"] is None


@pytest.mark.parametrize("name", [5, ["Array"], True])
async def test_audio_set_device_wants_a_name_or_null(make_app, name):
    with pytest.raises(RpcError) as caught:
        await make_app().call("audio.set_device", {"name": name})
    assert caught.value.code == INVALID_PARAMS


async def test_a_blank_microphone_name_means_the_default(make_app):
    app = make_app()
    await app.call("audio.set_device", {"name": "   "})
    assert (await app.call("audio.devices"))["selected"] is None


async def test_audio_monitor_sends_levels_with_no_session(make_app, notes):
    kit = make_kit()
    app = make_app(kit)
    listen(app, notes)
    assert await app.call("audio.monitor", {"on": True}) == {}
    await wait_until(lambda: notes.methods("audio.level"))
    assert all(level["session_id"] is None for level in notes.methods("audio.level"))
    notes.clear()
    assert await app.call("audio.monitor", {"on": False}) == {}
    assert not kit.mics[0].open


@pytest.mark.parametrize("params", [{}, {"on": "yes"}, {"on": 1}, {"on": None}])
async def test_audio_monitor_wants_a_boolean(make_app, params):
    with pytest.raises(RpcError) as caught:
        await make_app().call("audio.monitor", params)
    assert caught.value.code == INVALID_PARAMS


async def test_audio_monitor_without_a_microphone_is_no_microphone(make_app):
    kit = make_kit()
    app = make_app(kit)
    original = kit.MicRecorder

    def broken(on_frames, device=None):
        mic = original(on_frames, device)
        mic.fail_to_start = True
        return mic

    kit.MicRecorder = broken
    with pytest.raises(RpcError) as caught:
        await app.call("audio.monitor", {"on": True})
    assert caught.value.code == NO_MICROPHONE


async def test_changing_the_microphone_moves_a_running_meter(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    await app.call("audio.set_device", {"name": "Array"})
    assert len(kit.mics) == 2 and not kit.mics[0].open
    assert kit.mics[1].device == "Array" and kit.mics[1].open


async def test_a_dictation_takes_the_microphone_from_the_meter_and_gives_it_back(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    meter = kit.mics[0]
    await app.call("dictation.start", {"session_id": "s"})
    assert not meter.open and kit.mics[1].open  # one microphone open, and it is the dictation's
    await app.call("dictation.stop", {"session_id": "s", "target_app": None})
    assert len(kit.mics) == 3 and kit.mics[2].open  # the meter is back
    await app.call("audio.monitor", {"on": False})
    assert not any(mic.open for mic in kit.mics)


async def test_a_meter_switched_on_during_a_dictation_waits_for_its_end(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("dictation.start", {"session_id": "s"})
    await app.call("audio.monitor", {"on": True})
    assert len(kit.mics) == 1 and kit.mics[0].open  # only the dictation's
    await app.call("dictation.stop", {"session_id": "s", "target_app": None})
    assert len(kit.mics) == 2 and kit.mics[1].open and not kit.mics[0].open


async def test_a_cancelled_dictation_gives_the_microphone_back_to_the_meter_too(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    await app.call("dictation.start", {"session_id": "s"})
    await app.call("dictation.cancel", {"session_id": "s"})
    assert kit.mics[-1].open and len(kit.mics) == 3


async def test_a_meter_that_cannot_make_way_or_come_back_does_not_spoil_the_dictation(
    make_app, caplog
):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    original = kit.MicRecorder

    def flaky(on_frames, device=None):
        if len(kit.mics) >= 2:  # the meter's second microphone, after the dictation's
            raise OSError("PortAudio went away")
        return original(on_frames, device)

    kit.MicRecorder = flaky

    def will_not_close():
        raise OSError("it will not close")

    kit.mics[0].stop = will_not_close  # making way fails, and then taking back
    result = await dictate(app)
    assert result["text"] == WORDS + WORDS  # the dictation went on without the meter
    assert caplog.text.count("The level meter could not") == 2


async def test_a_stop_that_ends_while_a_new_start_makes_way_does_not_reopen_the_meter(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    await app.call("dictation.start", {"session_id": "s1"})
    service = app._service.result()
    monitor = app._monitor
    await monitor._lock.acquire()  # the meter is busy (a restart, say)
    starting = asyncio.create_task(app.call("dictation.start", {"session_id": "s2"}))
    await asyncio.sleep(0.05)  # s2 waits to make way for itself ...
    stopping = asyncio.create_task(
        app.call("dictation.stop", {"session_id": "s1", "target_app": None})
    )
    await wait_until(lambda: not service.active)  # ... and s1's stop is over, waiting to resume
    await asyncio.sleep(0.05)
    monitor._lock.release()
    await stopping
    assert await starting == {}  # s1 was over by the time s2 got that far
    await asyncio.sleep(0.1)
    assert service.active
    assert sum(mic.open for mic in kit.mics) == 1  # s2's: no meter beside a dictation
    await app.call("dictation.stop", {"session_id": "s2", "target_app": None})
    assert kit.mics[-1].open and sum(mic.open for mic in kit.mics) == 1  # the meter is back


async def test_the_meter_comes_back_after_a_session_nobody_stopped(make_app, monkeypatch):
    monkeypatch.setattr(dictation, "LOST_SESSION_SECONDS", 0.3)
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    await app.call("dictation.start", {"session_id": "s"})  # and nobody stops it
    service = app._service.result()
    await wait_until(lambda: not service.active)  # the watchdog cancelled it
    await wait_until(lambda: len(kit.mics) == 3 and kit.mics[2].open)  # the meter is back
    assert sum(mic.open for mic in kit.mics) == 1


async def test_a_dictation_that_fails_to_start_gives_the_microphone_back_too(make_app):
    kit = make_kit()
    app = make_app(kit, ready=("silero-vad",))
    await app.call("audio.monitor", {"on": True})
    with pytest.raises(RpcError) as caught:
        await app.call("dictation.start", {"session_id": "s"})  # no engine downloaded
    assert caught.value.code == NOT_CONFIGURED
    assert kit.mics[-1].open and sum(mic.open for mic in kit.mics) == 1


# --- dictation through the app ----------------------------------------------------------------


async def test_a_dictation_goes_through_the_whole_app(make_app, notes):
    kit = make_kit()
    app = make_app(kit)
    listen(app, notes)
    await app.call("dictionary.add", {"word": "拉斐爾"})
    await app.call("settings.set", {"key": "language", "value": "zh"})
    result = await dictate(app)
    assert result["text"] == WORDS + WORDS  # joined without a space: CJK on both sides
    assert result["cleaned"] is True and result["fallback_reason"] is None
    assert result["audio_seconds"] == pytest.approx(1.0)
    assert kit.transcriptions == [{"hints": ["拉斐爾"], "language": "auto"}] * 2
    history = (await app.call("history.list"))["items"]
    assert len(history) == 1
    assert history[0]["text"] == result["text"] and history[0]["target_app"] == "notepad.exe"
    assert history[0]["model"] == "fake/echo" and history[0]["cost_usd"] == 0
    assert (await app.call("history.latest"))["item"]["id"] == result["id"]
    # The ring of the overlay: levels with the session's id.
    assert {level["session_id"] for level in notes.methods("audio.level")} == {"s1"}


async def test_an_agent_dictation_goes_through_the_whole_app_without_cleanup_or_history(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("dictionary.add", {"word": "哈囉", "misheard": ["你好"]})
    asked = []
    echo = app.client.complete_json

    async def spy(**kwargs):
        asked.append(kwargs)
        return await echo(**kwargs)

    app.client.complete_json = spy
    assert await app.call("dictation.start", {"session_id": "a1", "purpose": "agent"}) == {}
    result = await app.call("dictation.stop", {"session_id": "a1", "target_app": "notepad.exe"})
    assert result == {
        "purpose": "agent",
        "text": "哈囉世界哈囉世界",  # the dictionary's replacement, joined as a dictation is
        "stt": "qwen3-asr",
        "audio_seconds": pytest.approx(1.0),
        "silent": False,
    }
    assert asked == []  # no cleanup
    assert (await app.call("history.list"))["items"] == []  # no history row
    assert (await app.call("history.latest"))["item"] is None  # nothing for "paste last"
    assert (await app.call("status.get"))["spend"]["month_usd"] == 0
    # The next one is a dictation as ever.
    again = await dictate(app, "d1")
    assert again["cleaned"] is True and len(asked) == 1


async def test_one_segmenter_and_one_engine_serve_every_dictation(make_app):
    kit = make_kit()
    app = make_app(kit)
    first = await dictate(app, "a")
    second = await dictate(app, "b")
    assert first["text"] == second["text"] == WORDS + WORDS
    assert second["audio_seconds"] == pytest.approx(1.0)  # not 2.0: per dictation
    assert len(kit.segmenters) == 1 and len(kit.engines) == 1
    assert kit.segmenters[0].flushes == 2
    assert kit.segmenters[0].path == Path(app.manager.dir("silero-vad")) / "silero_vad_v5.onnx"
    assert len(kit.mics) == 2  # a recorder for each dictation


async def test_the_audio_goes_to_the_segmenter_on_a_worker_thread(make_app):
    kit = make_kit()
    app = make_app(kit)
    await dictate(app)
    assert threading.get_ident() not in kit.segmenters[0].calls_from


async def test_start_does_not_wait_for_the_engine_and_stop_does(make_app):
    gate = threading.Event()
    kit = make_kit(engine_gate=gate)
    app = make_app(kit)
    started = await asyncio.wait_for(app.call("dictation.start", {"session_id": "s"}), 5)
    assert started == {}  # recording, with the engine still loading
    stopping = asyncio.ensure_future(
        app.call("dictation.stop", {"session_id": "s", "target_app": None})
    )
    await asyncio.sleep(0.3)
    assert not stopping.done()
    gate.set()
    result = await asyncio.wait_for(stopping, 10)
    assert result["text"] == WORDS + WORDS and len(kit.engines) == 1


async def test_start_does_not_wait_for_the_first_segmenter_either(make_app):
    gate = threading.Event()
    kit = make_kit(segmenter_gate=gate)
    app = make_app(kit)
    await asyncio.wait_for(app.call("dictation.start", {"session_id": "s"}), 5)
    stopping = asyncio.ensure_future(
        app.call("dictation.stop", {"session_id": "s", "target_app": None})
    )
    await asyncio.sleep(0.3)
    assert not stopping.done()
    gate.set()
    result = await asyncio.wait_for(stopping, 10)
    assert result["text"] == WORDS + WORDS and len(kit.segmenters) == 1


async def test_dictating_without_the_models_is_not_configured_and_opens_no_microphone(make_app):
    kit = make_kit()
    app = make_app(kit, ready=())
    with pytest.raises(RpcError) as caught:
        await app.call("dictation.start", {"session_id": "s"})
    assert caught.value.code == NOT_CONFIGURED
    assert kit.mics == []
    # The service is idle again: with the models in place the next one works.
    make_models(Path(app.manager.dir("silero-vad")).parent)
    assert (await dictate(app, "t"))["text"] == WORDS + WORDS


async def test_a_dictation_with_the_wav_file_of_fake_audio_uses_it_instead_of_the_microphone(
    make_app, tmp_path
):
    kit = make_kit()
    wav = tmp_path / "speech.wav"
    app = make_app(kit, fake_audio=wav)
    await app.call("dictation.start", {"session_id": "s"})
    assert kit.mics == [] and len(kit.wavs) == 1
    assert (
        kit.wavs[0].path == wav and kit.wavs[0].realtime is True
    )  # in real time, at the pace of speech
    await app.call("dictation.cancel", {"session_id": "s"})
    assert kit.wavs[0].stopped == 1


async def test_without_a_key_the_text_comes_back_as_spoken(make_app):
    # The real client with no key stored, and no request: the cleanup falls back before asking.
    app = make_app(fake_llm=False)
    assert await app.call("secrets.status") == {"has_key": False}
    result = await dictate(app)
    assert result["text"] == WORDS + WORDS
    assert result["cleaned"] is False and result["fallback_reason"] == "no_key"


async def test_the_fake_llm_has_a_key(make_app):
    assert await make_app().call("secrets.status") == {"has_key": True}


async def test_a_dictation_cancelled_while_its_text_is_cleaned_is_not_the_latest(make_app):
    # Esc during the cleaning: the app drops the result, and "paste last" must not find the text.
    app = make_app()
    await dictate(app, "s0")
    kept = (await app.call("history.latest"))["item"]
    entered, gate = asyncio.Event(), asyncio.Event()
    echo = app.client.complete_json

    async def slow(**kwargs):
        entered.set()
        await gate.wait()
        return await echo(**kwargs)

    app.client.complete_json = slow
    await app.call("dictation.start", {"session_id": "s1"})
    stopping = asyncio.ensure_future(
        app.call("dictation.stop", {"session_id": "s1", "target_app": None})
    )
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert await app.call("dictation.cancel", {"session_id": "s1"}) == {}
    finally:
        gate.set()
    with pytest.raises(RpcError) as dropped:
        await stopping
    assert dropped.value.code == NOT_FOUND
    assert (await app.call("history.latest"))["item"] == kept
    assert len((await app.call("history.list"))["items"]) == 1
    # The next dictation is kept as ever.
    app.client.complete_json = echo
    again = await dictate(app, "s2")
    assert (await app.call("history.latest"))["item"]["id"] == again["id"]


# --- the voice assistant ----------------------------------------------------------------------

ASSISTANT_METHODS = {
    "agent.ask",
    "agent.confirm",
    "agent.cancel",
    "agent.history.list",
    "agent.history.clear",
}


class RecordingActions:
    """What the assistant does to the computer, written down instead of done."""

    def __init__(self):
        self.calls = []
        self.listed = 0

    def open_url(self, url):
        self.calls.append(("open_url", url))

    def press_key(self, name, times=1):
        self.calls.append(("press_key", name, times))

    def lock_screen(self):
        self.calls.append(("lock_screen",))

    def open_settings(self, uri):
        self.calls.append(("open_settings", uri))

    def list_start_apps(self):
        self.listed += 1
        return []

    def launch_app(self, app_id):
        self.calls.append(("launch_app", app_id))

    def copy_to_clipboard(self, text):
        self.calls.append(("copy_to_clipboard", text))


def ask_params(text, language="zh-TW", session="a1"):
    return {"session_id": session, "text": text, "ui_language": language, "target_app": None}


async def test_the_assistants_methods_are_on_the_app_from_the_start(tmp_path):
    release = threading.Event()
    app = App(data_dir=tmp_path, fake_llm=True, load_kit=lambda: release.wait(30) and make_kit())
    app.start()
    try:
        assert ASSISTANT_METHODS <= set(app.methods())
        # The assistant does not wait for the speech side: it needs no microphone.
        result = await asyncio.wait_for(app.call("agent.ask", ask_params("你好")), 5)
        assert result["status"] == "done"
    finally:
        release.set()
        await app.aclose()


async def test_the_assistant_answers_through_the_whole_app_with_the_fake_llm(make_app):
    app = make_app()
    result = await app.call("agent.ask", ask_params("你好"))
    assert result == {"status": "done", "turn_id": 1, "prefix": "答", "text": "你好"}
    en = await app.call("agent.ask", ask_params("hello", language="en", session="a2"))
    assert (en["prefix"], en["text"]) == ("Answer", "hello")
    turns = (await app.call("agent.history.list", {}))["turns"]
    assert [(t["question"], t["route"], t["status"]) for t in turns] == [
        ("hello", "chat", "done"),
        ("你好", "chat", "done"),
    ]
    assert (await app.call("status.get"))["spend"]["month_usd"] == 0  # the fake LLM is free
    assert await app.call("agent.history.clear", {}) == {"deleted": 2}


async def test_the_fake_llm_does_nothing_to_the_computer(make_app):
    from raphael_core.agent.device import NullActions

    assert isinstance(make_app().agent._actions, NullActions)


async def test_the_real_core_gets_windows_actions_unless_it_is_given_others(make_app):
    from raphael_core.agent.device import WindowsActions

    assert isinstance(make_app(fake_llm=False).agent._actions, WindowsActions)
    actions = RecordingActions()
    assert make_app(fake_llm=False, agent_actions=actions).agent._actions is actions


async def test_the_apps_of_the_start_menu_are_read_when_the_app_says_hello(make_app):
    actions = RecordingActions()
    app = make_app(fake_llm=False, agent_actions=actions)
    assert actions.listed == 0
    await app.call("core.hello")
    for _ in range(100):
        if actions.listed:
            break
        await asyncio.sleep(0.01)
    assert actions.listed == 1


async def test_without_a_key_the_assistant_still_runs_commands_and_says_why_it_cannot_do_more(
    make_app,
):
    actions = RecordingActions()
    app = make_app(fake_llm=False, agent_actions=actions)
    assert await app.call("secrets.status") == {"has_key": False}
    done = await app.call("agent.ask", ask_params("靜音"))
    assert (done["prefix"], done["text"]) == ("了", "")
    assert actions.calls == [("press_key", "volume_mute", 1)]
    refused = await app.call("agent.ask", ask_params("what is the weather", "en", "a2"))
    assert (refused["prefix"], refused["text"]) == ("Negative", "No OpenRouter key is set.")


async def test_a_dictation_for_the_assistant_and_its_question_go_together(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("dictation.start", {"session_id": "a1", "purpose": "agent"})
    heard = await app.call("dictation.stop", {"session_id": "a1", "target_app": None})
    result = await app.call("agent.ask", ask_params(heard["text"]))
    assert result == {"status": "done", "turn_id": 1, "prefix": "答", "text": WORDS + WORDS}
    assert (await app.call("history.list"))["items"] == []  # the dictation history is not touched


async def test_closing_the_app_ends_the_assistants_requests_before_closing_the_client(make_app):
    app = make_app()
    order = []
    closing = app.agent.aclose

    async def agent_aclose():
        order.append(("agent", app.client.closed))
        await closing()

    app.agent.aclose = agent_aclose
    await app.aclose()
    assert order == [("agent", False)]
    assert app.client.closed


# --- the background worker --------------------------------------------------------------------

WORKER_METHODS = {"worker.list", "worker.get", "worker.cancel"}


async def test_the_workers_methods_are_on_the_app_from_the_start(make_app):
    assert WORKER_METHODS <= set(make_app().methods())


async def test_the_assistant_and_the_worker_go_through_the_one_gate(make_app):
    app = make_app()
    assert app.agent._gate is app.gate and app.background._gate is app.gate
    assert app.gate.plugins is app.integrations


async def test_a_task_for_the_background_goes_through_the_whole_app_with_the_fake_llm(make_app):
    app = make_app()
    sent = []
    app.set_notifier(lambda method, params: sent.append((method, params)))
    handed = await app.call("agent.ask", ask_params("[delegate] what time is it", "en"))
    assert (handed["prefix"], handed["text"]) == ("Understood", "Handed to the background.")
    # The worker is a process of its own, started for the task; its report comes by itself.
    await wait_until(lambda: any(method == "worker.report" for method, _ in sent), timeout=60)
    (report,) = [params for method, params in sent if method == "worker.report"]
    assert (report["state"], report["prefix"], report["text"]) == (
        "done", "Notice", "The scripted task saw 1 results.",
    )  # fmt: skip
    # The turn that handed it over was completed with the report, at no cost.
    (turn,) = (await app.call("agent.history.list", {}))["turns"]
    assert (turn["route"], turn["prefix"], turn["text"], turn["status"]) == (
        "delegate", "Notice", "The scripted task saw 1 results.", "done",
    )  # fmt: skip
    task = (await app.call("worker.get", {"id": report["task_id"]}))["task"]
    assert [(step["kind"], step["tool"], step["tier"]) for step in task["steps"]] == [
        ("model", None, None),
        ("tool", "local_time", "T0"),
        ("model", None, None),
    ]
    audit = (await app.call("integrations.audit", {}))["entries"]
    assert [(e["server"], e["tool"], e["approver"], e["task_id"]) for e in audit] == [
        ("Raphael", "local_time", "auto", report["task_id"]),
    ]
    assert (await app.call("status.get"))["spend"]["month_usd"] == 0
    assert await app.call("agent.history.clear", {}) == {"deleted": 1}
    assert (await app.call("worker.list", {}))["tasks"] == []  # the tasks go with the history


async def test_closing_the_app_ends_the_background_tasks_and_the_worker_before_the_client(make_app):
    app = make_app()
    order = []
    closing = app.background.aclose

    async def background_aclose():
        order.append(("background", app.client.closed))
        await closing()

    app.background.aclose = background_aclose
    await app.aclose()
    assert order == [("background", False)]


# --- shutting down ----------------------------------------------------------------------------


async def test_closing_the_app_closes_the_microphone_of_a_dictation_in_progress(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("dictation.start", {"session_id": "s"})
    await app.aclose()
    assert not kit.mics[0].open


async def test_closing_the_app_closes_the_meter_and_the_client(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    await app.aclose()
    assert not kit.mics[0].open
    assert app.client.closed


async def test_closing_the_app_stops_usage_refresh_before_closing_the_client(make_app):
    app = make_app()
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def key_info(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert not app.client.closed
            cancelled.set()
            raise

    app.client.key_info = key_info
    await app.call("status.get")
    await asyncio.wait_for(started.wait(), 1)
    await app.aclose()
    assert cancelled.is_set() and app.client.closed


async def test_a_close_step_that_fails_does_not_skip_the_rest(make_app, caplog):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    await app.call("dictation.start", {"session_id": "s"})
    service = app._service.result()
    closes = SimpleNamespace(service=service.aclose)

    def broken(what):
        async def aclose():
            raise RuntimeError(f"the {what} will not close")

        return aclose

    service.aclose = broken("dictation")
    app._monitor.aclose = broken("meter")
    app.client.aclose = broken("client")
    await app.aclose()  # logs, does not raise
    assert app._speech._closed
    with pytest.raises(RpcError):
        await app.call("history.list")  # the database is closed too
    for what in ("dictation", "meter", "client"):
        assert f"the {what} will not close" in caplog.text
    await closes.service()  # let the test leave nothing running


async def test_closing_the_app_twice_is_fine(make_app):
    app = make_app()
    await app.aclose()
    await app.aclose()


async def test_closing_the_app_waits_for_the_start_up_to_finish(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.aclose()
    assert not app.startup_running


async def test_closing_the_app_does_not_wait_for_a_load_that_has_not_begun(tmp_path):
    release = threading.Event()
    app = App(data_dir=tmp_path, fake_llm=True, load_kit=lambda: release.wait(30) and make_kit())
    app.start()
    started = time.monotonic()
    await app.aclose(patience=0.2)  # the kit is still loading; no point waiting for what it builds
    assert time.monotonic() - started < 5
    release.set()


async def test_core_shutdown_closes_everything_and_answers(make_app):
    kit = make_kit()
    app = make_app(kit)
    await app.call("audio.monitor", {"on": True})
    assert await app.call("core.shutdown") == {}
    assert not kit.mics[0].open and app.client.closed


async def test_core_shutdown_tells_the_server_to_stop(make_app):
    app = make_app()
    waiting = asyncio.ensure_future(app.wait_for_shutdown())
    await asyncio.sleep(0.05)
    assert not waiting.done()  # nothing asked for it yet
    await app.call("core.shutdown")
    await asyncio.wait_for(waiting, 5)


async def test_closing_the_app_by_its_stdin_does_not_ask_the_server_to_stop(make_app):
    app = make_app()
    await app.aclose()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(app.wait_for_shutdown(), 0.1)


async def test_the_database_is_closed_with_the_app(make_app):
    app = make_app()
    await app.aclose()
    with pytest.raises(RpcError):
        await app.call("history.list")


def test_the_real_kit_takes_the_arguments_the_app_gives_it():
    # The fake kit of these tests would take anything; the real one has to take what the app passes.
    import inspect

    from raphael_core.app import _import_kit

    kit = _import_kit()
    assert "max_segment_s" in inspect.signature(kit.Segmenter).parameters
    assert "num_threads" in inspect.signature(kit.create_engine).parameters
    assert list(inspect.signature(kit.MicRecorder).parameters)[:2] == ["on_frames", "device"]
    assert list(inspect.signature(kit.WavRecorder).parameters) == ["on_frames", "path", "realtime"]
    for name in ("recorder_factory", "segmenter_factory", "engine_provider", "on_idle"):
        assert name in inspect.signature(kit.DictationService).parameters
    assert callable(kit.rms_level) and callable(kit.list_input_devices)
    assert issubclass(kit.NoMicrophoneError, Exception)


# --- build_app, with the real kit -------------------------------------------------------------


def test_build_app_is_typed_as_returning_the_app():
    # __main__ uses what only an App has (wait_for_libraries, aclose, startup_running).
    import typing

    assert typing.get_type_hints(build_app)["return"] is App


async def test_build_app_returns_a_router_that_answers(tmp_path):
    app = build_app(data_dir=tmp_path, fake_llm=True)
    try:
        assert isinstance(app, App) and isinstance(app, Router)
        assert (await app.call("core.hello"))["protocol"] == 1
        assert (await app.call("secrets.status")) == {"has_key": True}
        # Nothing is downloaded in an empty data directory.
        assert [m["ready"] for m in (await app.call("models.status"))["models"]] == [False, False]
        # The real kit loads (sherpa-onnx, numpy, PortAudio): the dictation methods answer.
        with pytest.raises(RpcError) as caught:
            await app.call("dictation.start", {"session_id": "s"})
        assert caught.value.code == NOT_CONFIGURED
    finally:
        await app.aclose()


async def test_the_models_dir_override_is_honoured_by_the_app(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    make_models(shared, ("silero-vad",))
    monkeypatch.setenv("RAPHAEL_MODELS_DIR", str(shared))
    app = App(data_dir=tmp_path / "data", fake_llm=True, load_kit=lambda: make_kit())
    try:
        status = (await app.call("models.status"))["models"]
        assert [m["ready"] for m in status] == [True, False]
    finally:
        await app.aclose()


async def test_starting_twice_starts_one_set_of_threads(tmp_path):
    kit, loads = make_kit(), []
    app = App(data_dir=tmp_path, fake_llm=True, load_kit=lambda: loads.append(1) or kit)
    make_models(tmp_path / "models")
    app.start()
    app.start()
    try:
        await wait_until(lambda: kit.engines)
        await asyncio.sleep(0.1)
        assert loads == [1] and len(kit.engines) == 1 and len(kit.segmenters) == 1
    finally:
        await app.aclose()


async def test_nothing_is_built_for_an_app_that_was_closed_while_the_kit_loaded(tmp_path):
    release, kit = threading.Event(), make_kit()
    app = App(data_dir=tmp_path, fake_llm=True, load_kit=lambda: release.wait(30) and kit)
    make_models(tmp_path / "models")
    app.start()
    await app.aclose(patience=0.1)
    release.set()
    await wait_until(lambda: not app.startup_running)
    assert kit.segmenters == [] and kit.engines == []
    with pytest.raises(RuntimeError, match="closing"):
        app._service.result()  # and the dictation methods say so instead of waiting


async def test_the_keyring_backend_is_found_ahead_of_time_only_when_it_will_be_used(
    make_app, monkeypatch
):
    found = []
    monkeypatch.setattr(app_module.keyring, "get_keyring", lambda: found.append(1))
    make_app(make_kit(), fake_llm=True)
    await asyncio.sleep(0.3)
    assert found == []  # the fake LLM has an in-memory key
    make_app(make_kit(), fake_llm=False)
    await wait_until(lambda: found)


def test_importing_the_entry_point_does_not_load_the_speech_libraries():
    # core.hello has to answer quickly: numpy, sherpa-onnx and PortAudio take seconds to import.
    code = (
        "import sys, raphael_core.app, raphael_core.__main__\n"
        "loaded = {'numpy', 'sherpa_onnx', 'sounddevice', 'soxr'} & set(sys.modules)\n"
        "assert not loaded, loaded\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr


# --- main -------------------------------------------------------------------------------------


@pytest.fixture
def entry_point(monkeypatch, tmp_path):
    """`entry.main()` with build_app and run_stdio replaced, to see what it passes them."""
    seen = SimpleNamespace(built=None, ran=None, exits=[], steps=[], waited=None)

    def wait_for_libraries(timeout=None):
        seen.steps.append("wait_for_libraries")
        seen.waited = timeout

    fake_app = SimpleNamespace(
        aclose=lambda: None,
        wait_for_shutdown=lambda: None,
        startup_running=False,
        wait_for_libraries=wait_for_libraries,
    )

    def build(**kwargs):
        seen.steps.append("build_app")
        seen.built = kwargs
        return fake_app

    def run(app, on_close=None, *, output=None, stop=None):
        seen.steps.append("run_stdio")
        seen.ran = (app, on_close)
        seen.output, seen.stop = output, stop

    protocol = object()

    def claim():  # the real one would take pytest's own stdout
        seen.steps.append("claim_stdout")
        return protocol

    monkeypatch.setattr(app_module, "build_app", build)
    monkeypatch.setattr(entry, "run_stdio", run)
    monkeypatch.setattr(entry, "claim_stdout", claim)
    seen.protocol = protocol
    monkeypatch.setattr(entry.os, "_exit", lambda code: seen.exits.append(code))
    for name in ("RAPHAEL_DATA_DIR", "RAPHAEL_FAKE_AUDIO", "RAPHAEL_FAKE_LLM"):
        monkeypatch.delenv(name, raising=False)
    seen.app = fake_app
    return seen


def test_main_builds_the_app_from_the_environment_and_serves_it(entry_point, monkeypatch, tmp_path):
    monkeypatch.setenv("RAPHAEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RAPHAEL_FAKE_AUDIO", str(tmp_path / "speech.wav"))
    monkeypatch.setenv("RAPHAEL_FAKE_LLM", "1")
    entry.main()
    assert entry_point.built == {
        "data_dir": tmp_path / "data",
        "fake_audio": tmp_path / "speech.wav",
        "fake_llm": True,
    }
    app, on_close = entry_point.ran
    assert app is entry_point.app and on_close == app.aclose
    assert entry_point.exits == []


def test_main_waits_for_numpy_before_it_reads_stdin(entry_point):
    # On Windows numpy's DLLs hang while loading if another thread is blocked reading the stdin
    # pipe, which is what run_stdio's reader is from its start (see the docstring of app.py).
    entry.main()
    assert entry_point.steps == ["claim_stdout", "build_app", "wait_for_libraries", "run_stdio"]
    assert entry_point.waited == entry.LIBRARIES_TIMEOUT_S


def test_main_writes_the_protocol_to_the_stdout_it_claimed(entry_point):
    entry.main()
    assert entry_point.output is entry_point.protocol


def test_main_stops_serving_when_core_shutdown_asks(entry_point):
    entry.main()
    assert entry_point.stop == entry_point.app.wait_for_shutdown


def test_main_claims_stdout_before_any_native_library_is_imported():
    # OpenCC comes in with the app module; ONNX Runtime and PortAudio with its start-up thread.
    code = (
        "import sys\n"
        "import raphael_core.__main__ as entry\n"
        "def claim():\n"
        "    loaded = {'opencc', 'numpy', 'sherpa_onnx', 'sounddevice'} & set(sys.modules)\n"
        "    assert not loaded, loaded\n"
        "    raise SystemExit(7)\n"
        "entry.claim_stdout = claim\n"
        "entry.main()\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 7, done.stderr


def test_main_without_a_setting_uses_the_real_things(entry_point):
    entry.main()
    assert entry_point.built == {"data_dir": None, "fake_audio": None, "fake_llm": False}


@pytest.mark.parametrize("value", ["0", "", "true", "yes"])
def test_only_a_one_turns_the_fake_llm_on(entry_point, monkeypatch, value):
    monkeypatch.setenv("RAPHAEL_FAKE_LLM", value)
    entry.main()
    assert entry_point.built["fake_llm"] is False


def test_main_leaves_at_once_when_start_up_is_still_in_native_code(entry_point):
    # A thread inside ONNX Runtime cannot be stopped; waiting for the interpreter to tear it down
    # risks a crash on the way out, and the app is closing anyway.
    entry_point.app.startup_running = True
    entry.main()
    assert entry_point.exits == [0]


# --- speech-to-text in the cloud or on this computer ------------------------------------------

CLOUD_KEY = "sk-or-v1-test"


@pytest.fixture
async def openrouter_mock():
    """`openrouter_mock(app, transcription)` sends the app's OpenRouter requests to a mock.

    `transcription(request)` answers POST /audio/transcriptions; every cleanup call fails with a
    500, so the cleanup falls back to the text as heard. `requests` keeps the bodies, by path.
    """
    opened: list[httpx.AsyncClient] = []

    def install(app, transcription):
        requests: dict[str, list] = {"transcriptions": [], "chat": []}

        def handler(request):
            body = json.loads(request.content) if request.content else None
            if request.url.path.endswith("/audio/transcriptions"):
                requests["transcriptions"].append(body)
                return transcription(request)
            requests["chat"].append(body)
            return httpx.Response(500, json={"error": {"message": "no cleanup here"}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        opened.append(http)
        app.client._http = http
        app.keys.set(CLOUD_KEY)
        return requests

    yield install
    for http in opened:
        await http.aclose()


def cloud_says(text="cloud words", cost=0.0001):
    return lambda request: httpx.Response(
        200, json={"text": text, "usage": {"seconds": 0.5, "cost": cost}}
    )


async def test_speech_to_text_is_in_the_cloud_by_default(make_app, openrouter_mock):
    kit = make_kit()
    app = make_app(kit, fake_llm=False)
    requests = openrouter_mock(app, cloud_says())
    await app.call("settings.set", {"key": "language", "value": "zh"})
    await app.call("settings.set", {"key": "stt_cloud_model", "value": "openai/gpt-transcribe"})
    result = await dictate(app)
    assert result["text"] == "cloud words cloud words"
    assert kit.transcriptions == []  # the local engine heard nothing
    assert len(requests["transcriptions"]) == 2  # one per segment
    body = requests["transcriptions"][0]
    assert body["model"] == "openai/gpt-transcribe"
    assert body["input_audio"]["format"] == "wav"
    assert body["provider"]["options"]["openai"]["languages"] == ["zh"]
    assert kit.segmenters[0].max_segment_s == [30.0]  # longer stretches for the cloud
    item = (await app.call("history.latest"))["item"]
    assert item["stt"] == "openai/gpt-transcribe"
    spend = (await app.call("status.get"))["spend"]
    assert spend["month_usd"] == pytest.approx(0.0002)  # both segments were paid for


async def test_several_languages_go_to_gpt_transcribe_as_the_ones_to_detect_among(
    make_app, openrouter_mock
):
    kit = make_kit()
    app = make_app(kit, fake_llm=False)
    requests = openrouter_mock(app, cloud_says())
    languages = ["zh-HK", "en-GB", "ja-JP"]
    await app.call("settings.set", {"key": "languages", "value": languages})
    await app.call("settings.set", {"key": "stt_cloud_model", "value": "openai/gpt-transcribe"})
    await dictate(app)
    body = requests["transcriptions"][0]
    assert body["provider"]["options"]["openai"]["languages"] == ["zh", "en", "ja"]
    assert "language" not in body


async def test_the_chosen_cloud_model_is_the_one_asked(make_app, openrouter_mock):
    app = make_app(make_kit(), fake_llm=False)
    requests = openrouter_mock(app, cloud_says())
    await app.call("settings.set", {"key": "stt_cloud_model", "value": "elevenlabs/scribe-v2"})
    await dictate(app)
    assert {body["model"] for body in requests["transcriptions"]} == {"elevenlabs/scribe-v2"}


async def test_a_cloud_that_times_out_falls_back_to_the_local_engine(make_app, openrouter_mock):
    def slow(request):
        raise httpx.ReadTimeout("no answer", request=request)

    kit = make_kit()
    app = make_app(kit, fake_llm=False)
    requests = openrouter_mock(app, slow)
    result = await dictate(app)
    assert result["text"] == WORDS + WORDS
    assert len(kit.transcriptions) == 2
    assert len(requests["transcriptions"]) == 1  # after a failure, the rest of it stays local
    assert (await app.call("history.latest"))["item"]["stt"] == "qwen3-asr"


async def test_without_a_key_the_cloud_falls_back_to_the_local_engine(make_app):
    kit = make_kit()
    app = make_app(kit, fake_llm=False)  # the real client, and no key stored
    result = await dictate(app)
    assert result["text"] == WORDS + WORDS and len(kit.transcriptions) == 2


async def test_the_fake_llm_falls_back_to_the_local_engine_too(make_app):
    kit = make_kit()
    app = make_app(kit)
    result = await dictate(app)
    assert result["text"] == WORDS + WORDS and len(kit.transcriptions) == 2
    assert (await app.call("history.latest"))["item"]["stt"] == "qwen3-asr"


async def test_local_speech_to_text_never_calls_the_cloud(make_app, openrouter_mock):
    kit = make_kit()
    app = make_app(kit, fake_llm=False)
    requests = openrouter_mock(app, cloud_says())
    await app.call("settings.set", {"key": "stt_engine", "value": "local"})
    result = await dictate(app)
    assert result["text"] == WORDS + WORDS
    assert requests["transcriptions"] == []
    assert kit.segmenters[0].max_segment_s == [10.0]
    assert (await app.call("history.latest"))["item"]["stt"] == "qwen3-asr"


async def test_a_change_of_engine_takes_effect_from_the_next_dictation(make_app, openrouter_mock):
    kit = make_kit()
    app = make_app(kit, fake_llm=False)
    requests = openrouter_mock(app, cloud_says())
    await dictate(app, "a")
    await app.call("settings.set", {"key": "stt_engine", "value": "local"})
    await dictate(app, "b")
    await app.call("settings.set", {"key": "stt_engine", "value": "cloud"})
    await dictate(app, "c")
    assert len(requests["transcriptions"]) == 4 and len(kit.transcriptions) == 2
    assert kit.segmenters[0].max_segment_s == [30.0, 10.0, 30.0]
