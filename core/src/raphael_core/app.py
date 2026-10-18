"""The core's entry point: the device side and the brain wired to one router.

`build_app` makes an `App`, a Router with every method of the plan on it: the brain's (Task 8), the
dictation service's (Task 12) and the few that belong to neither (`core.*`, `models.*`, `audio.*`).
`__main__` serves it on stdio.

Start-up. The app's first calls come within seconds of the process starting (`core.hello`, the
saved microphone, the settings page's questions), so the models, which take tens of seconds to
load, are never waited for: the first sherpa-onnx object of a process pays about 15 s of ONNX
Runtime start-up and the engine a few more. A worker thread imports the speech libraries (the
"kit": numpy, sherpa-onnx, PortAudio), builds the dictation service and then loads the two models.
A dictation started before they are loaded still records: the audio queues up, and
`dictation.stop` waits for the models (spec section 5, Task 12). `dictation.*` and the methods
that need the kit wait for them instead of failing.

The libraries themselves are imported before the core reads its first request (`__main__` calls
`wait_for_libraries`). On Windows, numpy's DLLs hang while they load if another thread is blocked
in a read on the stdin pipe: the loader asks Windows about the stdin handle, and Windows holds
that handle for the pending read. The reader of stdio_server is such a thread from its first
moment to the end of the process, so a numpy imported after it started would load only when the
app closed stdin. The import takes a fraction of a second when the files are cached. Anything
else heavy that a later phase imports at run time (PyTorch for TTS, say) is exposed to the same
trap.

Speech-to-text. The `stt_engine` setting, read at each `dictation.start`, picks the engine for that
dictation: "local" is the engine on this computer; "cloud" sends each stretch of speech to the
brain's `stt.transcribe` (OpenRouter) and falls back to the local engine, which still loads at
start-up so the fallback costs no wait. Cloud stretches are cut at 30 s, not 10: the model hears
more context, and it is fast enough that the last stretch is no long tail.

Threads. The event loop runs the brain and the router; the SQLite connection is used on it only.
Everything that blocks (downloading a model, opening the microphone, loading a model) runs on a
worker thread: `asyncio.to_thread` for a call, a thread of its own for the loading.

stdout is the JSON-RPC channel (see stdio_server); everything this module says goes to the log.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import keyring

from . import __version__, paths
from .brain import textnorm
from .brain.db import connect
from .brain.openrouter import Completion, KeyInfo, OpenRouterClient, OpenRouterError
from .brain.secrets import CredentialKeyStore, KeyStore, MemoryKeyStore
from .brain.service import Brain
from .device.models import REGISTRY, ModelManager
from .protocol import (
    INVALID_PARAMS,
    NO_MICROPHONE,
    NOT_CONFIGURED,
    PROTOCOL_VERSION,
    RpcError,
)
from .router import Router

logger = logging.getLogger(__name__)

# The speech models the app uses, in the order the settings page lists them. The registry has more
# (sensevoice and breeze-asr-25 are for the evaluation tools); the app downloads these two.
VAD_MODEL = "silero-vad"
ENGINE_MODEL = "qwen3-asr"
APP_MODELS = (VAD_MODEL, ENGINE_MODEL)

# Speech without a pause is cut into pieces of at most this long and transcribed while the key is
# still held, so there is no long tail to wait for after the release (the engine takes 20 s).
DICTATION_SEGMENT_SECONDS = 10.0
# The same for cloud speech-to-text: longer, so the model gets context (OpenRouter's upstream limit
# is 60 s a request).
CLOUD_SEGMENT_SECONDS = 30.0
# More threads do not make Qwen3-ASR faster (Task 10).
STT_THREADS = 4
# audio.level notifications a second, for the settings page's meter.
LEVEL_HZ = 30.0
# models.progress notifications are sent at most this often (the last one always is).
PROGRESS_INTERVAL_S = 0.1
# How long closing the app waits for the start-up threads. A thread inside ONNX Runtime cannot be
# stopped, so after this the process leaves without it.
SHUTDOWN_PATIENCE_S = 3.0

# The key the fake LLM's in-memory key store holds: any key makes the cleanup run.
_FAKE_KEY = "fake-llm-key"
_TRANSCRIPT_OPEN, _TRANSCRIPT_CLOSE = "<transcript>\n", "\n</transcript>"


# --- the fake LLM -----------------------------------------------------------------------------


class EchoClient:
    """Stands in for the OpenRouter client when RAPHAEL_FAKE_LLM=1: the "cleaned" text is the
    transcript as it was said, at no cost, with no network. The end-to-end runs use it."""

    def __init__(self) -> None:
        self.warmed_up = False
        self.closed = False

    async def complete_json(
        self,
        *,
        model: str,
        fallbacks: list[str],
        system: str,
        user: str,
        schema: dict,
        schema_name: str,
        timeout_s: float,
        max_tokens: int,
    ) -> Completion:
        text = user
        if text.startswith(_TRANSCRIPT_OPEN) and text.endswith(_TRANSCRIPT_CLOSE):
            text = text[len(_TRANSCRIPT_OPEN) : len(text) - len(_TRANSCRIPT_CLOSE)]
        return Completion(
            data={"text": text},
            model="fake/echo",
            cost_usd=0.0,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        )

    async def transcribe(self, **kwargs: Any) -> Any:
        # No cloud speech-to-text without the network: the local engine takes over, as it would
        # for a real failure.
        raise OpenRouterError(0, "bad_request", "The fake LLM does not transcribe")

    async def key_info(self, key: str | None = None, timeout_s: float = 5.0) -> KeyInfo:
        return KeyInfo(
            label="fake",
            limit=None,
            limit_remaining=None,
            limit_reset=None,
            usage_daily=0.0,
            usage_monthly=0.0,
        )

    def warm_up(self) -> None:
        self.warmed_up = True

    async def aclose(self) -> None:
        self.closed = True


# --- the speech models, loaded once and shared ------------------------------------------------


class _SharedSegmenter:
    """The one voice activity detector, handed to every dictation, as `SegmenterLike`.

    It is the same object each time (the dictation service waits for a cancelled session to let go
    of a segmenter by its identity), and it waits for the detector instead of failing when the
    first dictation comes before the detector is built: the audio just queues up until then.
    """

    def __init__(self, speech: SpeechModels) -> None:
        self._speech = speech
        # The longest stretch for the coming dictation, or None for what the detector was built
        # with. Set before the dictation's worker starts; the detector takes it at the start of
        # its next recording.
        self.max_segment_s: float | None = None

    def for_dictation(self, max_segment_s: float) -> _SharedSegmenter:
        self.max_segment_s = max_segment_s
        return self

    def accept(self, frames: Any) -> list:
        segmenter = self._speech.segmenter()
        if self.max_segment_s is not None:
            segmenter.set_max_segment_s(self.max_segment_s)
        return segmenter.accept(frames)

    def flush(self) -> list:
        segmenter = self._speech.built_segmenter()
        return [] if segmenter is None else segmenter.flush()

    @property
    def speech_seconds(self) -> float:
        segmenter = self._speech.built_segmenter()
        return 0.0 if segmenter is None else segmenter.speech_seconds


class SpeechModels:
    """The voice activity detector and the speech engine, loaded once and kept.

    `make_segmenter(path)` builds the detector from its model file and `make_engine()` the engine;
    both are slow and are called on a worker thread. `load_pending` loads what is downloaded and
    not yet loaded; it is called at start-up and again when a download finishes. `engine()` and
    the shared segmenter wait for their own model while a load is under way (not for the other
    one), and try again if there was no load or it failed, so the first dictation after a download
    needs nothing else.
    """

    def __init__(
        self,
        manager: ModelManager,
        *,
        make_segmenter: Callable[[Path], Any],
        make_engine: Callable[[], Any],
    ) -> None:
        self._manager = manager
        self._make_segmenter = make_segmenter
        self._make_engine = make_engine
        self._cond = threading.Condition()
        self._loading = False
        self._closed = False
        self._loaded: dict[str, Any] = {VAD_MODEL: None, ENGINE_MODEL: None}
        self._failures: dict[str, Exception] = {}
        self.dictation_segmenter = _SharedSegmenter(self)

    def load_pending(self) -> None:
        """Load the models that are downloaded and not loaded. Failures are logged; they come out
        of `engine()` or the segmenter when someone needs the model."""
        with self._cond:
            while self._loading:
                self._cond.wait()
            if self._closed:
                return
            self._loading = True
        try:
            # The detector first: it pays ONNX Runtime's start-up, and any dictation needs it.
            self._load(VAD_MODEL, self._build_segmenter)
            self._load(ENGINE_MODEL, self._make_engine)
        finally:
            with self._cond:
                self._loading = False
                self._cond.notify_all()

    def _build_segmenter(self) -> Any:
        path = self._manager.dir(VAD_MODEL) / REGISTRY[VAD_MODEL].files[0]
        return self._make_segmenter(path)

    def _load(self, name: str, build: Callable[[], Any]) -> None:
        if self._loaded[name] is not None or not self._manager.is_ready(name):
            return
        began = time.perf_counter()
        try:
            model = build()
        except Exception as exc:
            logger.exception("The speech model %s could not be loaded", name)
            self._failures[name] = exc
            return
        with self._cond:
            if self._closed:
                return
            self._loaded[name] = model
            self._failures.pop(name, None)
            self._cond.notify_all()  # whoever waits for this model need not wait for the other
        logger.info("Speech model %s loaded in %.1f s", name, time.perf_counter() - began)

    def built_segmenter(self) -> Any:
        """The detector, or None if it is not loaded (this never waits)."""
        return self._loaded[VAD_MODEL]

    def segmenter(self) -> Any:
        """The detector, waiting for it if it is being loaded. FileNotFoundError if its model is
        not downloaded; the error that stopped it, if loading failed."""
        return self._get(VAD_MODEL)

    def engine(self) -> Any:
        """The speech engine, waiting for it if it is being loaded (`DictationService`'s
        `engine_provider`). FileNotFoundError if its model is not downloaded."""
        return self._get(ENGINE_MODEL)

    def _get(self, name: str) -> Any:
        with self._cond:
            while self._loaded[name] is None and self._loading:
                self._cond.wait()
        if self._loaded[name] is None:
            self.load_pending()  # none under way: load what has been downloaded since
        model = self._loaded[name]
        if model is not None:
            return model
        failure = self._failures.get(name)
        if failure is not None:
            raise failure
        raise FileNotFoundError(f"The speech model {name} is not downloaded")

    def require_models(self) -> None:
        """RpcError(NOT_CONFIGURED) if a model is neither loaded nor on disk: better to say so at
        the key press than after the user has spoken."""
        missing = [
            name
            for name in APP_MODELS
            if self._loaded[name] is None and not self._manager.is_ready(name)
        ]
        if missing:
            raise RpcError(
                NOT_CONFIGURED, f"The speech models are not downloaded: {', '.join(missing)}"
            )

    def close(self) -> None:
        """Let go of the models, for good: nothing is loaded after this."""
        with self._cond:
            self._closed = True
            self._loaded = dict.fromkeys(self._loaded)
            self._cond.notify_all()


# --- the level meter --------------------------------------------------------------------------


class _Run:
    """One opening of the meter's microphone. Late frames from a closed one are dropped."""

    def __init__(self) -> None:
        self.recorder: Any = None
        self.active = True
        self.lock = threading.Lock()
        self.last_sent = -float("inf")


class LevelMonitor:
    """Sends `audio.level {session_id: null, level}` from the microphone, for the settings page's
    meter. It owns a microphone of its own, which it gives up while a dictation has the real one
    (`suspend`) and takes back afterwards (`resume`).

    `make_recorder(on_frames)` makes a recorder; `level_of(frames)` is the level from 0 to 1;
    `no_microphone` is the exception a recorder raises when it cannot open. At most `hz` levels a
    second go out. The methods are for the event loop only; the recorder's callback is not.
    """

    def __init__(
        self,
        router: Router,
        *,
        make_recorder: Callable[[Callable[[Any], None]], Any],
        level_of: Callable[[Any], float],
        no_microphone: type[Exception],
        hz: float = LEVEL_HZ,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._router = router
        self._make_recorder = make_recorder
        self._level_of = level_of
        self._no_microphone = no_microphone
        self._interval = 1.0 / hz
        self._clock = clock
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wanted = False  # the page wants levels
        self._suspended = False  # a dictation has the microphone
        self._run: _Run | None = None

    async def set_on(self, on: bool) -> None:
        """Start or stop the levels. RpcError(NO_MICROPHONE) if the microphone will not open."""
        async with self._lock:
            self._wanted = on
            if on:
                await self._open(strict=True)
            else:
                await self._close()

    async def suspend(self) -> None:
        async with self._lock:
            self._suspended = True
            await self._close()

    async def resume(self, may_resume: Callable[[], bool] | None = None) -> None:
        """Take the microphone back after a dictation. `may_resume` is asked once the meter is
        this call's, so a dictation that started while it waited keeps the meter suspended."""
        async with self._lock:
            if may_resume is not None and not may_resume():
                return
            self._suspended = False
            if self._wanted:
                await self._open(strict=False)

    async def restart(self) -> None:
        """Open the microphone again, after the choice of microphone changed."""
        async with self._lock:
            await self._close()
            if self._wanted:
                await self._open(strict=False)

    async def aclose(self) -> None:
        async with self._lock:
            self._wanted = False
            await self._close()

    async def _open(self, *, strict: bool) -> None:
        if self._run is not None or self._suspended:
            return
        self._loop = asyncio.get_running_loop()
        run = _Run()
        run.recorder = self._make_recorder(lambda frames: self._on_frames(run, frames))
        try:
            await asyncio.to_thread(run.recorder.start)
        except self._no_microphone as exc:
            run.active = False
            if strict:
                self._wanted = False
                raise RpcError(NO_MICROPHONE, str(exc) or "There is no microphone") from exc
            logger.warning("The level meter could not open the microphone: %s", exc)
            return
        self._run = run

    async def _close(self) -> None:
        run, self._run = self._run, None
        if run is None:
            return
        run.active = False
        await asyncio.to_thread(run.recorder.stop)

    def _on_frames(self, run: _Run, frames: Any) -> None:
        """The recorder's callback, on the audio thread (and once on the thread that stops it)."""
        if not run.active:
            return
        now = self._clock()
        with run.lock:
            if now - run.last_sent < self._interval:
                return
            run.last_sent = now
        level = self._level_of(frames)
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._send, run, level)
            except RuntimeError:  # the loop is closed: the process is going down
                pass

    def _send(self, run: _Run, level: float) -> None:
        if run.active:
            self._router.notify("audio.level", {"session_id": None, "level": level})


# --- the app ----------------------------------------------------------------------------------


def _import_kit() -> SimpleNamespace:
    """What needs numpy, sherpa-onnx and PortAudio, imported in one go (seconds). The tests pass
    the app another loader, with fakes."""
    from .device import audio, dictation, stt, vad

    return SimpleNamespace(
        DictationService=dictation.DictationService,
        MicRecorder=audio.MicRecorder,
        WavRecorder=audio.WavRecorder,
        NoMicrophoneError=audio.NoMicrophoneError,
        rms_level=audio.rms_level,
        list_input_devices=audio.list_input_devices,
        Segmenter=vad.Segmenter,
        create_engine=stt.create_engine,
    )


def _invalid(message: str) -> RpcError:
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


class App(Router):
    """The Router of the whole core. `start()` begins loading the speech side; `aclose()` closes
    what the app opened (the microphone, the HTTP client, the database).

    `data_dir` holds the database, and the models unless `manager` says otherwise (or
    RAPHAEL_MODELS_DIR does). With `fake_audio`, a WAV file plays in real time in place of the
    microphone; with `fake_llm`, an in-process client returns the transcript unchanged and there is
    an in-memory key. `load_kit` is for tests.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        fake_audio: Path | None = None,
        fake_llm: bool = False,
        manager: ModelManager | None = None,
        load_kit: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__()
        self._fake_audio = fake_audio
        self._load_kit = _import_kit if load_kit is None else load_kit
        data_dir.mkdir(parents=True, exist_ok=True)
        self.manager = ModelManager(paths.models_dir(data_dir)) if manager is None else manager

        # The database is opened here, on the thread that runs the loop, and used there only.
        self._conn = connect(paths.db_path(data_dir))
        try:
            self.keys: KeyStore
            self.client: Any
            if fake_llm:
                self.keys = MemoryKeyStore()
                self.keys.set(_FAKE_KEY)
                self.client = EchoClient()
            else:
                self.keys = CredentialKeyStore()
                self.client = OpenRouterClient(self.keys.get)
            self.brain = Brain(self._conn, self.client, self.keys)  # prunes the history
        except BaseException:
            self._conn.close()
            raise
        self.brain.register(self)

        self._speech = SpeechModels(
            self.manager, make_segmenter=self._make_segmenter, make_engine=self._make_engine
        )
        self._inner = Router()  # the dictation service's own methods, behind the proxies below
        self._kit: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._service: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._monitor: LevelMonitor | None = None
        self._starts = 0  # dictation.start requests under way (the meter makes way for them)
        self._meter_tasks: set[asyncio.Task[None]] = set()
        self._device_name: str | None = None  # the microphone, set by the app from device.json
        self._stt_engine = "local"  # the stt_engine setting, as read at the latest dictation.start
        self._stt_warned: set[str] = set()  # kinds of cloud failure already logged as warnings
        self._loop: asyncio.AbstractEventLoop | None = None
        self._threads: list[threading.Thread] = []
        self._closing = threading.Event()
        self._closed = False
        self._shutdown_requested = asyncio.Event()

        methods = {
            "core.hello": self._hello,
            "core.shutdown": self._shutdown,
            "models.status": self._models_status,
            "models.ensure": self._models_ensure,
            "audio.devices": self._audio_devices,
            "audio.set_device": self._audio_set_device,
            "audio.monitor": self._audio_monitor,
            "dictation.start": functools.partial(self._dictation, "dictation.start"),
            "dictation.stop": functools.partial(self._dictation, "dictation.stop"),
            "dictation.cancel": functools.partial(self._dictation, "dictation.cancel"),
        }
        for method, handler in methods.items():
            self.register(method, handler)

    # --- start-up and shutdown ------------------------------------------------------------------

    def start(self) -> None:
        """Begin loading the speech side and warming up what the first dictation would build."""
        if self._threads:
            return
        for target, name in ((self._prewarm, "prewarm"), (self._startup, "startup")):
            thread = threading.Thread(target=target, name=f"raphael-{name}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def wait_for_libraries(self, timeout: float | None = None) -> None:
        """Wait until the speech libraries are imported (or failed to be), up to `timeout` s.

        Call it before the first read of stdin: see the module docstring. Giving up is logged as
        an error: the core serves on, but numpy may now hang until stdin closes.
        """
        done, _ = concurrent.futures.wait([self._kit], timeout)
        if not done:
            logger.error(
                "The speech libraries were not imported within %g s; serving without them for now "
                "(on Windows their import may now hang until stdin closes)",
                timeout,
            )

    @property
    def startup_running(self) -> bool:
        """Whether a start-up thread is still at work (it may be inside ONNX Runtime)."""
        return any(thread.is_alive() for thread in self._threads)

    def _prewarm(self) -> None:
        """Build the things the first dictation would build: the OpenCC converter (about 100 ms)
        and the HTTP client of OpenRouter (over a second)."""
        began = time.perf_counter()
        try:
            textnorm.to_taiwan_traditional("软件")  # simplified: goes through OpenCC
        except Exception:
            logger.exception("Could not warm up OpenCC")
        try:
            self.client.warm_up()
        except Exception:
            logger.exception("Could not warm up the OpenRouter client")
        if isinstance(self.keys, CredentialKeyStore):
            try:
                keyring.get_keyring()  # finds the Windows backend (0.7 s); reads no credential
            except Exception:
                logger.exception("Could not find the keyring backend")
        logger.info(
            "Warmed up OpenCC, the OpenRouter client and the keyring in %.1f s",
            time.perf_counter() - began,
        )

    def _startup(self) -> None:
        began = time.perf_counter()
        try:
            self._start_speech_side(began)
        except BaseException as exc:  # nobody may wait forever for a start-up that died
            for future in (self._kit, self._service):
                if not future.done():
                    future.set_exception(exc)
            if not isinstance(exc, Exception):
                raise
            logger.exception("The speech side could not be started")

    def _start_speech_side(self, began: float) -> None:
        kit = self._load_kit()
        self._kit.set_result(kit)
        logger.info("Speech libraries imported in %.1f s", time.perf_counter() - began)
        if self._closing.is_set():
            self._service.set_exception(RuntimeError("The core is closing"))
            return
        service = kit.DictationService(
            self,
            recorder_factory=self._new_recorder,
            segmenter_factory=self._dictation_segmenter,
            engine_provider=self._dictation_engine,
            on_idle=self._on_dictation_idle,
        )
        service.register(self._inner)
        self._service.set_result(service)
        logger.info("Dictation is ready %.1f s after start-up began", time.perf_counter() - began)
        if self._closing.is_set():
            return
        self._speech.load_pending()
        logger.info(
            "Speech models settled %.1f s after start-up began", time.perf_counter() - began
        )

    def _load_models_in_background(self) -> None:
        thread = threading.Thread(
            target=self._speech.load_pending, name="raphael-load-models", daemon=True
        )
        self._threads.append(thread)
        thread.start()

    async def aclose(self, patience: float = SHUTDOWN_PATIENCE_S) -> None:
        """Cancel a dictation in progress, close the microphone, the HTTP client and the database.
        Safe to call more than once. Waits up to `patience` seconds for the start-up threads.

        Every step is tried whatever happened to the ones before it: a step that fails is logged,
        and a cancellation is passed on once the rest are done.
        """
        if self._closed:
            return
        self._closed = True
        self._closing.set()

        async def close_dictation() -> None:
            if self._service.done() and self._service.exception() is None:
                await self._service.result().aclose()

        async def close_meter() -> None:
            await self._meter_settled()  # a meter being given back, first
            if self._monitor is not None:
                await self._monitor.aclose()

        async def join_threads() -> None:
            await asyncio.to_thread(self._join_threads, patience)

        async def close_speech() -> None:
            self._speech.close()

        async def close_database() -> None:
            self._conn.close()

        steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("cancel the dictation", close_dictation),
            ("close the level meter", close_meter),
            ("close the HTTP client", self.client.aclose),
            ("wait for the start-up threads", join_threads),
            ("let go of the speech models", close_speech),
            ("close the database", close_database),
        ]
        interrupted: BaseException | None = None
        for what, step in steps:
            try:
                await step()
            except Exception:
                logger.exception("Closing the core: could not %s", what)
            except BaseException as exc:  # cancelled, say: the rest still has to be closed
                interrupted = interrupted or exc
        if interrupted is not None:
            raise interrupted

    def _join_threads(self, patience: float) -> None:
        deadline = time.monotonic() + patience
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        if self.startup_running:
            logger.warning("Start-up is still loading; not waiting for it")

    # --- the speech side ------------------------------------------------------------------------

    def _make_segmenter(self, path: Path) -> Any:
        return self._kit.result().Segmenter(path, max_segment_s=DICTATION_SEGMENT_SECONDS)

    def _make_engine(self) -> Any:
        return self._kit.result().create_engine(ENGINE_MODEL, self.manager, num_threads=STT_THREADS)

    def _dictation_segmenter(self) -> Any:
        """The shared detector, cutting at the length that suits this dictation's engine."""
        cloud = self._stt_engine == "cloud"
        seconds = CLOUD_SEGMENT_SECONDS if cloud else DICTATION_SEGMENT_SECONDS
        return self._speech.dictation_segmenter.for_dictation(seconds)

    def _dictation_engine(self) -> Any:
        """The engine for this dictation (the service's `engine_provider`, on its worker): the
        local one, or the cloud with the local one behind it."""
        if self._stt_engine != "cloud":
            return self._speech.engine()
        from .device.cloud_stt import CloudSttEngine  # numpy: the kit has loaded it already

        return CloudSttEngine(
            self._call_from_thread, fallback=self._speech.engine, warned=self._stt_warned
        )

    def _call_from_thread(self, method: str, params: dict, timeout: float) -> Any:
        """`self.call(method, params)` on the event loop, from a worker thread; waits `timeout`
        seconds at most (concurrent.futures.TimeoutError), and then gives the call up."""
        loop = self._loop
        if loop is None:
            raise RuntimeError("No event loop to call the brain on")
        future = asyncio.run_coroutine_threadsafe(self.call(method, params), loop)
        try:
            return future.result(timeout)
        except BaseException:
            future.cancel()
            raise

    async def _read_stt_engine(self) -> None:
        """Note which speech-to-text the coming dictation uses. Local if the setting cannot be
        read: nothing leaves the computer that the user has not asked to send."""
        self._loop = asyncio.get_running_loop()
        try:
            settings = (await self.call("settings.get", {}))["settings"]
            engine = settings.get("stt_engine")
        except Exception as exc:
            logger.warning(
                "Could not read the speech-to-text setting; using the local one: %s", exc
            )
            engine = "local"
        self._stt_engine = engine if engine in ("cloud", "local") else "local"

    def _new_recorder(self, on_frames: Callable[[Any], None]) -> Any:
        kit = self._kit.result()
        if self._fake_audio is not None:
            return kit.WavRecorder(on_frames, self._fake_audio, realtime=True)
        return kit.MicRecorder(on_frames, self._device_name)

    @staticmethod
    async def _wait_for(future: concurrent.futures.Future) -> Any:
        """The result of a future that a thread sets. Cancelling the request leaves it be."""
        if future.done():
            return future.result()
        return await asyncio.shield(asyncio.wrap_future(future))

    # --- core -----------------------------------------------------------------------------------

    async def _hello(self, params: dict) -> dict:
        return {"protocol": PROTOCOL_VERSION, "version": __version__}

    async def _shutdown(self, params: dict) -> dict:
        """Close what the app opened, then end the process: the server stops once this is
        answered (`wait_for_shutdown`), as it would when the app closes its stdin."""
        await self.aclose()
        self._shutdown_requested.set()
        return {}

    async def wait_for_shutdown(self) -> None:
        """Return once `core.shutdown` has closed the app: run_stdio's `stop`."""
        await self._shutdown_requested.wait()

    # --- models ---------------------------------------------------------------------------------

    async def _models_status(self, params: dict) -> dict:
        return {"models": [{"name": n, "ready": self.manager.is_ready(n)} for n in APP_MODELS]}

    async def _models_ensure(self, params: dict) -> dict:
        names = params.get("names")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise _invalid("names must be a list of model names")
        unknown = [n for n in names if n not in APP_MODELS]
        if unknown:
            raise _invalid(f"not a model the app downloads: {', '.join(unknown)}")
        loop = asyncio.get_running_loop()

        def progress_for(name: str) -> Callable[[int, int], None]:
            sent_at = -float("inf")

            def progress(done: int, total: int) -> None:  # on the download's thread
                nonlocal sent_at
                now = time.monotonic()
                last = total > 0 and done >= total
                if not (last or now - sent_at >= PROGRESS_INTERVAL_S):
                    return
                sent_at = now
                try:
                    loop.call_soon_threadsafe(
                        self.notify, "models.progress", {"name": name, "done": done, "total": total}
                    )
                except RuntimeError:  # the loop is closed
                    pass

            return progress

        def download() -> None:
            for name in names:
                self.manager.ensure(name, progress_for(name))

        await asyncio.to_thread(download)
        self._load_models_in_background()  # the first dictation should not wait for them
        return {"ok": True}

    # --- the microphone -------------------------------------------------------------------------

    async def _audio_devices(self, params: dict) -> dict:
        kit = await self._wait_for(self._kit)
        # Listing reads the devices again, so that a headset plugged in since shows up, and that is
        # not done while a stream is open. The settings meter's stream is the one that usually is:
        # it makes way for the reading and comes back after, as it does for a dictation.
        await self._meter("make way for reading the devices", lambda m: m.suspend())
        try:
            devices = await asyncio.to_thread(kit.list_input_devices)
        finally:
            await self._give_meter_back()
        return {
            "devices": [
                {"index": d.index, "name": d.name, "is_default": d.is_default} for d in devices
            ],
            "selected": self._device_name,
        }

    async def _audio_set_device(self, params: dict) -> dict:
        name = params.get("name")
        if name is not None and not isinstance(name, str):
            raise _invalid("name must be a string or null")
        name = name if name is not None and name.strip() else None  # blank: the default
        changed = name != self._device_name
        self._device_name = name
        if changed and self._monitor is not None:
            await self._monitor.restart()  # a running meter follows the choice
        return {}

    async def _audio_monitor(self, params: dict) -> dict:
        on = params.get("on")
        if type(on) is not bool:
            raise _invalid("on must be true or false")
        if not on and self._monitor is None:
            return {}
        monitor = await self._get_monitor()
        await monitor.set_on(on)
        return {}

    async def _get_monitor(self) -> LevelMonitor:
        service = await self._wait_for(self._service)
        if self._monitor is None:
            kit = self._kit.result()
            self._monitor = LevelMonitor(
                self,
                make_recorder=self._new_recorder,
                level_of=kit.rms_level,
                no_microphone=kit.NoMicrophoneError,
            )
            if service.active:  # a dictation has the microphone: the meter waits for its end
                await self._monitor.suspend()
        return self._monitor

    # --- dictation ------------------------------------------------------------------------------

    async def _dictation(self, method: str, params: dict) -> Any:
        """`dictation.*`, from the service once it exists. It makes way for the meter's
        microphone, and gives it back when the dictation is over.

        The meter comes back through the service's `on_idle`, whichever way a session ends (the
        watchdog's cancel of a lost session has no request behind it). A request waits for that
        before it answers. A start that did not start gives it back itself: while a start is under
        way the meter stays suspended, so a stop that ends meanwhile cannot reopen it beside the
        new dictation.
        """
        service = await self._wait_for(self._service)
        if method != "dictation.start":
            try:
                return await self._inner.call(method, params)
            finally:
                await self._meter_settled()
        self._speech.require_models()
        self._starts += 1
        try:
            await self._read_stt_engine()
            await self._meter("make way for the dictation", lambda m: m.suspend())
            return await self._inner.call(method, params)
        finally:
            self._starts -= 1
            await self._meter_settled()
            if not service.active:  # never started (or already over)
                await self._give_meter_back()

    def _dictation_idle(self) -> bool:
        """No dictation has, or is about to take, the microphone."""
        if self._starts:
            return False
        service = self._service
        return not (service.done() and service.exception() is None and service.result().active)

    def _on_dictation_idle(self) -> None:
        """The service's `on_idle`, on the loop: give the meter its microphone back."""
        if self._monitor is None:
            return
        task = asyncio.get_running_loop().create_task(self._give_meter_back())
        self._meter_tasks.add(task)
        task.add_done_callback(self._meter_tasks.discard)

    async def _give_meter_back(self) -> None:
        await self._meter("take the microphone back", lambda m: m.resume(self._dictation_idle))

    async def _meter_settled(self) -> None:
        """Wait for the meter to be given back, if it is being (without cancelling that)."""
        if self._meter_tasks:
            await asyncio.wait(list(self._meter_tasks))

    async def _meter(self, what: str, step: Callable[[LevelMonitor], Awaitable[None]]) -> None:
        """Do `step` to the level meter, if there is one. What goes wrong with the meter is logged
        and goes no further: it must not spoil a dictation."""
        if self._monitor is None:
            return
        try:
            await step(self._monitor)
        except Exception:
            logger.exception("The level meter could not %s", what)


def build_app(
    *, data_dir: Path | None = None, fake_audio: Path | None = None, fake_llm: bool = False
) -> App:
    """The core's App (its Router), with the speech side already loading.

    `data_dir` is where the database lives (default: Raphael's folder in the user's application
    data, or RAPHAEL_DATA_DIR). `fake_audio` is a WAV file that the recorder plays in real time in
    place of the microphone. `fake_llm` swaps OpenRouter for a client that returns the transcript
    unchanged at no cost, and the Credential Manager for an in-memory key.
    """
    app = App(
        data_dir=paths.data_dir() if data_dir is None else Path(data_dir),
        fake_audio=None if fake_audio is None else Path(fake_audio),
        fake_llm=fake_llm,
    )
    app.start()
    return app
