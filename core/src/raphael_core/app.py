"""The core's entry point: the device side and the brain wired to one router.

`build_app` makes an `App`, a Router with every method of the core on it: the brain's
(raphael_core.brain), the dictation service's (`dictation.*`, raphael_core.device), the voice
assistant's (`agent.*`, raphael_core.agent), the background worker's (`worker.*`,
raphael_core.background), the plugins' (`integrations.*` and `toolsets.list`,
raphael_core.integrations), the messaging apps' (`messaging.<app>.*`, raphael_core.messaging), the
services of the tools' (`services.*`, raphael_core.services), Raphael's voice (`tts.*`,
raphael_core.services.speaker), the owner's team's (`members.*`, `groups.*`, `routines.*`,
raphael_core.members) and the few that belong to none of them (`core.*`, `models.*`, `audio.*`).
`__main__` serves it on stdio.

Start-up. The app's first calls come within seconds of the process starting (`core.hello`, the
saved microphone, the settings page's questions), so the models, which take tens of seconds to
load, are never waited for: the first sherpa-onnx object of a process pays about 15 s of ONNX
Runtime start-up and the engine a few more. A worker thread imports the speech libraries (the
"kit": numpy, sherpa-onnx, PortAudio), builds the dictation service and then loads the models that
are downloaded: the voice activity detector and the local speech engine, if the user has one (with
the cloud engine there need not be any). A dictation started before they are loaded still records:
the audio queues up, and `dictation.stop` waits for the models. `dictation.*` and the methods that
need the kit wait for them instead of failing.

The libraries themselves are imported before the core reads its first request (`__main__` calls
`wait_for_libraries`). On Windows, numpy's DLLs hang while they load if another thread is blocked
in a read on the stdin pipe: the loader asks Windows about the stdin handle, and Windows holds
that handle for the pending read. The reader of stdio_server is such a thread from its first
moment to the end of the process, so a numpy imported after it started would load only when the
app closed stdin. The import takes a fraction of a second when the files are cached. Anything
else heavy imported at run time (PyTorch for TTS, say) is exposed to the same trap.

Speech-to-text. The `stt_engine` setting, read at each `dictation.start`, picks the engine for that
dictation: "local" is the engine on this computer; "cloud" sends each stretch of speech to the
brain's `stt.transcribe` (OpenRouter) and falls back to the local engine when there is one, which
then loads at start-up so the fallback costs no wait (without one, the next stretch goes to the
cloud again: device/cloud_stt.py). Cloud stretches are cut at 30 s, not 10: the model hears more
context, and it is fast enough that the last stretch is no long tail.

Threads. The event loop runs the brain and the router; the SQLite connection is used on it only.
Everything that blocks (downloading a model, opening the microphone, loading a model) runs on a
worker thread: `asyncio.to_thread` for a call, a thread of its own for the loading.

stdout is the JSON-RPC channel (see stdio_server); everything this module says goes to the log.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import functools
import gc
import json
import logging
import re
import shutil
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import keyring

from . import __version__, paths
from .agent.device import DeviceActions, NullActions, WindowsActions
from .agent.gate import ToolGate
from .agent.safety import SafetyGuard, SafetyJudge, SafetyRules
from .agent.service import AgentService
from .agent.tools import Tools
from .background.service import BackgroundService
from .background.tasks import FINAL_STATES
from .members.service import MembersService
from .computer.bridge import ShellBridge
from .computer.run import ComputerRuns
from .brain import textnorm
from .brain.db import connect
from .memory.service import MemoryService
from .brain.openrouter import (
    ChatToolCall,
    ChatTurn,
    Completion,
    Decision,
    KeyInfo,
    OpenRouterClient,
    OpenRouterError,
    TextCompletion,
    ToolPlan,
)
from .brain.secrets import CredentialKeyStore, KeyStore, MemoryKeyStore
from .brain.service import Brain
from .brain.settings import SettingsStore
from .device.models import LOCAL_STT_MODELS, REGISTRY, ModelDownloadError, ModelManager, local_model
from .integrations.service import IntegrationsService
from .integrations.vault import CredentialVault, MemoryVault, SecretVault
from .messaging.config import MessagingStore
from .messaging.service import MessagingService
from .messaging.words import compose, words
from .protocol import (
    BUSY,
    INTERNAL_ERROR,
    NOT_CONFIGURED,
    NO_LOCAL_MODEL,
    NO_MICROPHONE,
    PROTOCOL_VERSION,
    RpcError,
    invalid_params,
)
from .router import Router
from .services.attachments import Attachment
from .services.hub import ServiceHub, offline_transport
from .services.keys import ServiceKeys, credential_keys, memory_keys
from .services.speaker import Speaker

logger = logging.getLogger(__name__)


class _DownloadCancelled(Exception):
    """Raised by the progress callback of a download the user cancelled, to stop it."""


# The speech models the app starts with, in the order the settings page lists them: the detector and
# the engine Raphael has always used. The user may choose another engine of the catalogue
# (`LOCAL_STT_MODELS`) in the settings; `SpeechModels.engine_name` is the one chosen, and this one
# until then.
VAD_MODEL = "silero-vad"
ENGINE_MODEL = "qwen3-asr"
APP_MODELS = (VAD_MODEL, ENGINE_MODEL)

# Speech without a pause is cut into pieces of at most this long and transcribed while the
# recording goes on, so there is no long tail to wait for after the stop (the engine takes 20 s).
# It is the size of one request, not a limit on the dictation, which may run as long as the user
# speaks.
DICTATION_SEGMENT_SECONDS = 10.0
# The same for cloud speech-to-text: longer, so the model gets context (OpenRouter's upstream limit
# is 60 s a request).
CLOUD_SEGMENT_SECONDS = 30.0
# More threads do not make Qwen3-ASR faster.
STT_THREADS = 4
# Deleting a model folder on Windows can fail while ONNX Runtime still holds a file: it is tried
# this many times, this far apart.
DELETE_TRIES = 3
DELETE_RETRY_S = 0.2
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
# The question in what the assistant's answering model is shown; a line naming the language to
# reply in follows it.
_QUESTION = re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL)


# --- the fake LLM -----------------------------------------------------------------------------

# What a request that begins with this is for the fake LLM: a task for the background worker.
_DELEGATE_MARK = "[delegate]"
# The topics the scripted task looks up, one call each.
_SCRIPT_TOPICS = ("alpha", "beta")


def _scripted_delegate(text: str) -> bool:
    return text.lstrip().startswith(_DELEGATE_MARK)


class EchoClient:
    """Stands in for the OpenRouter client when RAPHAEL_FAKE_LLM=1: the "cleaned" text is the
    transcript as it was said, at no cost, with no network. The end-to-end runs use it.

    The voice assistant gets small talk from it: every request is "chat", and the reply is the
    question as it was asked.

    One scripted exception, for the tests of the background worker: a request that begins with
    `[delegate]` is routed to the worker, and `chat_tools` then plays a fixed task. It calls the
    tool named `look` of a connected service twice (topics "alpha" and "beta"; or the time, when the
    service is not there), a tool named `slow` once if the request says "slow", and then reports in
    one sentence with the number of results it had seen. No network, no cost."""

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
        reasoning_effort: str = "none",
    ) -> Completion:
        data: dict
        if schema_name == "raphael_route":  # the assistant's router
            data = {
                "route": "chat",
                "command": "none",
                "needs_weather": False,
                "needs_search": False,
                "needs_time": False,
                "app": "",
            }
        elif schema_name == "raphael_reply":  # the assistant's answer: the question, echoed
            question = _QUESTION.search(user)
            data = {"prefix": "", "text": question.group(1) if question else "", "detail": ""}
        elif schema_name == "raphael_app":
            data = {"app": ""}
        elif schema_name == "raphael_summary":  # the conversation folded into its summary
            data = {"summary": "(the fake LLM keeps no summary)"}
        elif schema_name == "raphael_memory":  # the reflection pass: nothing to change
            data = {"actions": []}
        else:  # the cleanup
            text = user
            if text.startswith(_TRANSCRIPT_OPEN) and text.endswith(_TRANSCRIPT_CLOSE):
                text = text[len(_TRANSCRIPT_OPEN) : len(text) - len(_TRANSCRIPT_CLOSE)]
            data = {"text": text}
        return Completion(
            data=data,
            model="fake/echo",
            cost_usd=0.0,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
        )

    async def decide(self, **kwargs: Any) -> Decision:
        questions = kwargs.get("questions")
        if isinstance(questions, dict) and questions and all(k.startswith("rule_") for k in questions):
            # The safety rules' judge: no rule matches what the fake LLM does.
            rules = {name: {"type": "noul", "noul": 0.0} for name in questions}
            return Decision(answers=rules, cost_usd=0.0, latency_ms=0)
        # Jev, sure that every request is small talk (or a task, for the scripted one).
        state = kwargs.get("state")
        spoken = state.get("utterance", "") if isinstance(state, dict) else ""
        route = "delegate" if _scripted_delegate(spoken) else "chat"
        answers = {
            "route": {"type": "choice", "choice": route, "confidence": 0.99},
            "command": {"type": "choice", "choice": "none", "confidence": 0.99},
            "needs_weather": {"type": "noul", "noul": 0.0},
            "needs_search": {"type": "noul", "noul": 0.0},
            "needs_time": {"type": "noul", "noul": 0.0},
        }
        return Decision(answers=answers, cost_usd=0.0, latency_ms=0)

    async def plan_tools(self, **kwargs: Any) -> ToolPlan:
        # No plugin tool is ever wanted.
        return ToolPlan(
            calls=[], model="fake/echo", cost_usd=0.0, prompt_tokens=0, completion_tokens=0,
            latency_ms=0,
        )  # fmt: skip

    async def chat_tools(self, *, messages: list[dict], tools: list[dict], **kwargs: Any) -> ChatTurn:
        # The scripted task of the background worker (see the class).
        offered = {tool["function"]["name"] for tool in tools}
        seen = sum(1 for message in messages if message.get("role") == "tool")
        task = " ".join(
            str(message.get("content")) for message in messages if message.get("role") == "user"
        )

        def calling(name: str, arguments: dict) -> ChatTurn:
            raw = json.dumps(arguments)
            call = ChatToolCall(id=f"call_{seen}", name=name, arguments=arguments, raw_arguments=raw)
            return ChatTurn("", [call], "fake/echo", 0.0, 0, 0, 0)

        look = next((name for name in sorted(offered) if name.endswith("__look")), None)
        slow = next((name for name in sorted(offered) if name.endswith("__slow")), None)
        if slow is not None and "slow" in task and seen == 0:
            return calling(slow, {})
        if look is not None and seen < len(_SCRIPT_TOPICS):
            return calling(look, {"topic": _SCRIPT_TOPICS[seen]})
        if look is None and "local_time" in offered and seen == 0:
            return calling("local_time", {})
        report = {"prefix": "告", "text": f"The scripted task saw {seen} results.", "detail": ""}
        return ChatTurn(json.dumps(report), [], "fake/echo", 0.0, 0, 0, 0)

    async def chat_text(self, **kwargs: Any) -> TextCompletion:
        return TextCompletion(
            text="(the fake LLM does not search the web)",
            model="fake/echo",
            cost_usd=0.0,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
            sources=[],
        )

    async def transcribe(self, **kwargs: Any) -> Any:
        # No cloud speech-to-text without the network: the local engine takes over, as it would
        # for a real failure.
        raise OpenRouterError(0, "bad_request", "The fake LLM does not transcribe")

    async def models(self, timeout_s: float = 5.0) -> list[dict]:
        # No model list without the network either: the catalog is Raphael's own models, as it is
        # when OpenRouter cannot be reached.
        raise OpenRouterError(0, "network", "The fake LLM has no model list")

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

    @property
    def silence_seconds(self) -> float:
        segmenter = self._speech.built_segmenter()
        return 0.0 if segmenter is None else segmenter.silence_seconds


class SpeechModels:
    """The voice activity detector and the speech engine, loaded once and kept.

    `make_segmenter(path)` builds the detector from its model file and `make_engine(name)` the
    engine of the model called `name`; both are slow and are called on a worker thread.
    `load_pending` loads what is downloaded and not yet loaded; it is called at start-up and again
    when a download finishes. `engine()` and the shared segmenter wait for their own model while a
    load is under way (not for the other one), and try again if there was no load or it failed, so
    the first dictation after a download needs nothing else.

    The engine is the model `engine_name`. `select_engine` names another; `load_pending` then
    builds it, and until it is built the engine that is loaded goes on serving, so that a dictation
    never waits for a switch (it waits only when there is no engine at all).
    """

    def __init__(
        self,
        manager: ModelManager,
        *,
        make_segmenter: Callable[[Path], Any],
        make_engine: Callable[[str], Any],
        engine_name: str | None = ENGINE_MODEL,
    ) -> None:
        self._manager = manager
        self._make_segmenter = make_segmenter
        self._make_engine = make_engine
        self._engine_name = engine_name
        self._engine_built_for: str | None = None  # the model the loaded engine was built from
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
            self._load_engine()
        finally:
            with self._cond:
                self._loading = False
                self._cond.notify_all()

    @property
    def engine_name(self) -> str | None:
        """The model the engine is, or is to be, built from; None when there is no local model."""
        return self._engine_name

    @property
    def loaded_engine_name(self) -> str | None:
        """The model the engine that is loaded was built from; None while there is none."""
        return self._engine_built_for

    def select_engine(self, name: str | None) -> None:
        """Make `name` (a model of the catalogue) the engine, or None for no local engine. Nothing
        is loaded yet: that is `load_pending`, which builds it if it is downloaded."""
        with self._cond:
            self._engine_name = name

    def unload_engine(self) -> None:
        """Let go of the engine that is loaded, so that its model files are free to delete. Waits
        for a load that is under way; the next `engine()` builds the selected one again."""
        with self._cond:
            while self._loading:
                self._cond.wait()
            self._loaded[ENGINE_MODEL] = None
            self._engine_built_for = None
            self._failures.pop(ENGINE_MODEL, None)
        gc.collect()

    def _build_segmenter(self) -> Any:
        path = self._manager.dir(VAD_MODEL) / REGISTRY[VAD_MODEL].files[0]
        return self._make_segmenter(path)

    def _load_engine(self) -> None:
        """Build the engine of the selected model, if it is downloaded and not the one loaded. A
        failure is logged and the engine that is loaded stays."""
        name = self._engine_name
        if name is None:  # no local model: let go of the engine that is loaded
            with self._cond:
                self._loaded[ENGINE_MODEL] = None
                self._engine_built_for = None
            return
        if self._engine_built_for == name or not self._manager.is_ready(name):
            return
        began = time.perf_counter()
        try:
            engine = self._make_engine(name)
        except Exception as exc:
            logger.exception("The speech model %s could not be loaded", name)
            self._failures[ENGINE_MODEL] = exc
            return
        with self._cond:
            if self._closed:
                return
            self._loaded[ENGINE_MODEL] = engine
            self._engine_built_for = name
            self._failures.pop(ENGINE_MODEL, None)
            self._cond.notify_all()
        logger.info("Speech model %s loaded in %.1f s", name, time.perf_counter() - began)

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
        if name == ENGINE_MODEL and self._engine_name is None:
            raise RpcError(NO_LOCAL_MODEL, "There is no local speech model")
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
        if name == ENGINE_MODEL and self._engine_name is None:
            raise RpcError(NO_LOCAL_MODEL, "There is no local speech model")
        missing = self._engine_name if name == ENGINE_MODEL else name
        raise FileNotFoundError(f"The speech model {missing} is not downloaded")

    def require_models(self, engine_needed: bool = True) -> None:
        """RpcError(NOT_CONFIGURED) if a model is neither loaded nor on disk: better to say so at
        the key press than after the user has spoken. The engine is asked for only if
        `engine_needed` (the cloud dictates without a local one) and there is one."""
        wanted = [(VAD_MODEL, VAD_MODEL)]
        if engine_needed and self._engine_name is not None:
            wanted.append((ENGINE_MODEL, self._engine_name))
        missing = [
            name
            for slot, name in wanted
            if self._loaded[slot] is None and not self._manager.is_ready(name)
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
            self._engine_built_for = None
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
        current_input_devices=audio.current_input_devices,
        automatic_device_name=audio.automatic_device_name,
        Segmenter=vad.Segmenter,
        create_engine=stt.create_engine,
    )


class App(Router):
    """The Router of the whole core. `start()` begins loading the speech side; `aclose()` closes
    what the app opened (the microphone, the HTTP client, the database).

    `data_dir` holds the database, and the models unless `manager` says otherwise (or
    RAPHAEL_MODELS_DIR does). With `fake_audio`, a WAV file plays in real time in place of the
    microphone; with `fake_llm`, an in-process client returns the transcript unchanged, the key and
    the plugins' secrets are kept in memory, and the voice assistant does nothing to this computer.
    `agent_actions` is what the assistant does to it otherwise (Windows, by default). `load_kit` is
    for tests. `memory_dir` is where Raphael's long-term memory is kept (`paths.memory_dir`, which
    goes with `data_dir`, by default).
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        models_dir: Path | None = None,
        fake_audio: Path | None = None,
        fake_llm: bool = False,
        manager: ModelManager | None = None,
        load_kit: Callable[[], Any] | None = None,
        agent_actions: DeviceActions | None = None,
        memory_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self._fake_audio = fake_audio
        self._load_kit = _import_kit if load_kit is None else load_kit
        data_dir.mkdir(parents=True, exist_ok=True)
        if manager is None:
            manager = ModelManager(paths.models_dir(data_dir) if models_dir is None else models_dir)
        self.manager = manager

        # The database is opened here, on the thread that runs the loop, and used there only.
        self._conn = connect(paths.db_path(data_dir))
        try:
            self.keys: KeyStore
            self.client: Any
            self.vault: SecretVault
            service_keys: ServiceKeys
            if fake_llm:
                self.keys = MemoryKeyStore()
                self.keys.set(_FAKE_KEY)
                self.client = EchoClient()
                self.vault = MemoryVault()
                service_keys = memory_keys()
            else:
                self.keys = CredentialKeyStore()
                self.client = OpenRouterClient(self.keys.get)
                self.vault = CredentialVault()
                service_keys = credential_keys()
            self.brain = Brain(self._conn, self.client, self.keys)  # prunes the history
        except BaseException:
            self._conn.close()
            raise
        self.brain.register(self)
        # The services the tools stand on, as the owner chose them (`services.*`). With the fake LLM
        # every one of them is offline: a run of the tests reaches no network.
        self.services = ServiceHub(
            self._conn,
            self.client,
            service_keys,
            models=self.brain.known_models,
            transport=offline_transport() if fake_llm else None,
        )
        self.services.register(self)
        # Raphael's voice (`tts.*`): his answers aloud, through the service the owner chose.
        self.speaker = Speaker(self.services)
        self.speaker.register(self)
        if agent_actions is None:
            agent_actions = NullActions() if fake_llm else WindowsActions()
        # The plugins: servers the user connected, and the hub the assistant reaches their tools by.
        self.integrations = IntegrationsService(self._conn, data_dir, self.vault, notify=self.notify)
        self.integrations.register(self)
        # The owner's safety rules (`safety.rules.*`), which the gate asks before every action that
        # changes something, with their judge (Jev).
        self.safety_rules = SafetyRules(self._conn)
        self.safety_rules.register(self)
        # The one gate every tool call goes through, the assistant's and the worker's.
        self.gate = ToolGate(
            self.integrations,
            conn=self._conn,
            safety=SafetyGuard(self.safety_rules, SafetyJudge(self.client)),
        )
        # The background worker: tasks the assistant hands over, carried out in a process of their
        # own that has no key, no database and no plugin (raphael_core.worker).
        self.background = BackgroundService(
            self._conn,
            self.client,
            self.gate,
            Tools(self.client, services=self.services),
            notify=self._worker_notice,
        )
        self.background.register(self)
        # The run on the owner's desktop: the core asks the shell, which reaches UI Automation
        # (`computer.request`, answered with `computer.reply`; `computer.interrupt` stops it).
        self.computer_bridge = ShellBridge(self.notify)
        self.computer_bridge.register(self)
        self.computer = ComputerRuns(self.computer_bridge)
        # Raphael's memory: the notes on this PC, the summary of the conversation, the reflection.
        memory_root = paths.memory_dir(data_dir) if memory_dir is None else memory_dir
        self.memory = MemoryService(self._conn, memory_root, self.client, self.keys)
        self.memory.register(self)
        self.background.use_memory(self.memory)
        # The members of the owner's team, their pictures (in the local folder the memory is in,
        # beside the shell's copy of Raphael's own) and their routines.
        self.members = MembersService(
            self._conn,
            memory_root.parent,
            background=self.background,
            memory=self.memory,
            language=lambda: self.agent.language,
            notify=self.notify,
            connections=self.integrations.connections,
        )
        self.members.register(self)
        self.memory.member_exists = self.members.member_exists
        self.agent = AgentService(
            self._conn,
            self.client,
            self.keys,
            actions=agent_actions,
            plugins=self.integrations,
            models=self.brain.known_models,
            gate=self.gate,
            background=self.background,
            notify=self._agent_notice,
            computer=self.computer,
            memory=self.memory,
            services=self.services,
            team=self.members,
            speaker=self.speaker,
        )
        self.agent.register(self)
        self.background.use_team(self.members, self.agent.team_tool)
        # The questions the background waits on: a thread one waits in is `waiting` in the member
        # column, and the overlay's notice of one names the member whose thread holds it.
        self.members.add_waiting_source(self.background.waiting_members)
        self.background.use_member_names(self.members.name_of)
        # Messaging apps (Telegram): messages reach the assistant like a spoken question.
        self.messaging = MessagingService(
            MessagingStore(data_dir),
            self.vault,
            ask=self._ask_from_messaging,
            stop=self._stop_from_messaging,
            transcribe=self._transcribe_from_messaging,
            language=lambda: self.agent.language,
        )
        self.messaging.register(self)

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
        self._local_download: str | None = None  # the model that models.local_use is fetching
        self._local_cancel = threading.Event()
        self._models_lock = asyncio.Lock()  # one change of the chosen local model at a time
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
            "models.local": self._models_local,
            "models.local_use": self._models_local_use,
            "models.local_cancel": self._models_local_cancel,
            "models.local_delete": self._models_local_delete,
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
        self._select_saved_engine()
        for target, name in ((self._prewarm, "prewarm"), (self._startup, "startup")):
            thread = threading.Thread(target=target, name=f"raphael-{name}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def _select_saved_engine(self) -> None:
        """Build the local engine the user chose (the `stt_local_model` setting) rather than the
        one Raphael has always used. On the loop's thread, which the database belongs to."""
        try:
            self._speech.select_engine(SettingsStore(self._conn).get("stt_local_model"))
        except Exception:
            logger.exception("Could not read the local speech model setting; using the default")

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
        """Build the things the first dictation would build: the OpenCC converters (about 100 ms
        each) and the HTTP client of OpenRouter (over a second)."""
        began = time.perf_counter()
        try:
            textnorm.to_taiwan_traditional("软件")  # simplified: goes through OpenCC (s2twp)
            # The cleanup is compared with the dictation in Simplified Chinese, by two conversions.
            textnorm.to_simplified("軟體")
            textnorm.to_mainland("軟體")
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
            ("stop the account usage refresh", self.brain.aclose),
            ("end the messaging connections", self.messaging.aclose),
            ("stop the routines' scheduler", self.members.aclose),
            ("end the background tasks", self.background.aclose),
            ("end the assistant's requests", self.agent.aclose),
            ("end Raphael's voice", self.speaker.aclose),
            ("end the memory's background pass", self.memory.aclose),
            ("close the services' HTTP client", self.services.aclose),
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

    def _make_engine(self, name: str) -> Any:
        return self._kit.result().create_engine(name, self.manager, num_threads=STT_THREADS)

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
            self._call_from_thread, fallback=self._fallback_engine, warned=self._stt_warned
        )

    def _fallback_engine(self) -> Any:
        """The local engine behind the cloud. One that is not downloaded, or cannot be loaded, is
        as good as none: RpcError(NO_LOCAL_MODEL)."""
        try:
            return self._speech.engine()
        except RpcError:
            raise
        except Exception as exc:
            raise RpcError(NO_LOCAL_MODEL, "There is no usable local speech model") from exc

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
            self._follow_local_model(settings.get("stt_local_model"))
        except Exception as exc:
            logger.warning(
                "Could not read the speech-to-text setting; using the local one: %s", exc
            )
            engine = "local"
        self._stt_engine = engine if engine in ("cloud", "local") else "local"

    def _follow_local_model(self, name: Any) -> None:
        """The local model set in the settings is the engine from now on, if it is downloaded. The
        engine is built in the background: this dictation goes on with the one that is loaded."""
        if name is None:  # set to none directly: the engine goes, the fallback with it
            if self._speech.engine_name is not None:
                self._speech.select_engine(None)
                self._load_models_in_background()
            return
        if not isinstance(name, str) or name == self._speech.engine_name:
            return
        if name in REGISTRY and self.manager.is_ready(name):
            self._speech.select_engine(name)
            self._load_models_in_background()

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

    async def _ask_from_messaging(
        self,
        session_id: str,
        text: str,
        language: str,
        source: str,
        /,
        *,
        attachments: Sequence[Attachment] = (),
    ) -> str:
        """A message from a messaging app, through the same entry as a spoken question. Whatever
        needs a yes is refused on the spot: a yes is given at this computer, never from a message.

        The images and PDFs the message came with (already checked by the switchboard) go as the
        request's `attachments`, in the form `agent.ask` takes from the app: `{name, mime, data}`
        with the bytes in base64. They stay in memory, for this request only."""
        params: dict[str, Any] = {
            "session_id": session_id,
            "text": text,
            "ui_language": language,
            "source": source,
        }
        if attachments:
            params["attachments"] = [
                {"name": a.name, "mime": a.mime, "data": base64.b64encode(a.data).decode("ascii")}
                for a in attachments
            ]
        reply = await self.call("agent.ask", params)
        if reply.get("status") == "confirm":
            question = reply["confirm"]
            await self.call("agent.confirm", {"id": question["id"], "approved": False})
            asked = compose(question["prefix"], question["text"], None)
            return f"{asked}\n{words(language, 'confirm')}"
        return compose(reply.get("prefix", ""), reply.get("text", ""), None)

    async def _transcribe_from_messaging(self, audio: bytes, language: str) -> str:
        """The words of a voice message, heard by the engine that dictation uses (the cloud, with
        the local one behind it if there is one, or the local one), with the user's dictionary and
        the same normalising. The audio stays in memory."""
        from .device.dictation import dictionary_hints  # numpy: after the start-up
        from .messaging import voice

        await self._read_stt_engine()
        hints = await dictionary_hints(self.call)
        cloud = self._stt_engine == "cloud"

        def hear() -> str:
            engine = self._dictation_engine()
            path = self.manager.dir(VAD_MODEL) / REGISTRY[VAD_MODEL].files[0]
            seconds = CLOUD_SEGMENT_SECONDS if cloud else DICTATION_SEGMENT_SECONDS
            segmenter = self._kit.result().Segmenter(path, max_segment_s=seconds)
            return voice.transcribe_voice(audio, engine, segmenter, hints)

        heard = await asyncio.to_thread(hear)
        if not heard.strip():
            return ""
        try:
            text = (await self.call("brain.normalize_transcript", {"raw_text": heard}))["text"]
            return text if isinstance(text, str) else heard
        except Exception as exc:
            logger.warning("Could not normalize a voice message: %s", type(exc).__name__)
            return heard

    async def _stop_from_messaging(self, session_id: str) -> bool:
        """Stop what this chat has under way: a request that still thinks and its background
        tasks, never those of the computer or of another chat."""
        thinking = self.agent.stop_session(session_id)
        return await self.background.cancel_session(session_id) or thinking

    def _agent_notice(self, method: str, params: dict) -> None:
        """The assistant's notifications go to the app; a turn written or completed is news in its
        thread, unless that thread is on screen (`MembersService.turn_arrived`)."""
        if method == "agent.turn" and type(params.get("turn_id")) is int:
            self.members.turn_arrived(params["turn_id"])
        self.notify(method, params)

    def _worker_notice(self, method: str, params: dict) -> None:
        """The background worker's notifications go to the app, and the report of a task that a
        messaging chat handed over goes back to that chat. When such a task waits for the owner's
        yes, the chat is told that the answer is given at the computer: never there. A task that
        ended completed its turn, which is news in its thread."""
        self.notify(method, params)
        if method == "background.question" and params.get("state") == "waiting":
            self._tell_chat_to_answer_here(params.get("task_id"))
            return
        if method == "worker.changed" and params.get("state") in FINAL_STATES:
            ended = self.background.task(params["task_id"]) if type(params.get("task_id")) is int else None
            if ended is not None and ended.turn_id:
                self.members.turn_arrived(ended.turn_id)
        if method != "worker.report" or type(params.get("task_id")) is not int:
            return
        task = self.background.task(params["task_id"])
        if task is None or not self.messaging.handles(task.session_id):
            return
        if task.report_text is None:
            return
        text = compose(task.report_prefix or "", task.report_text, None)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        sent = loop.create_task(self.messaging.report(task.session_id, text))
        sent.add_done_callback(lambda t: t.cancelled() or t.exception())

    def _tell_chat_to_answer_here(self, task_id: object) -> None:
        if type(task_id) is not int:
            return
        task = self.background.task(task_id)
        if task is None or not self.messaging.handles(task.session_id):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        text = words(task.language, "confirm")
        sent = loop.create_task(self.messaging.report(task.session_id, text))
        sent.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def _hello(self, params: dict) -> dict:
        self.messaging.start()  # a Telegram bot that was left on polls again
        self.agent.prewarm()  # the app is up: read the Start menu now, not at the first "open"
        self.memory.start()  # and look for turns the reflection pass has not read, soon
        self.members.start()  # the routines: one missed while the core was not running runs now
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
        engine = self._speech.engine_name
        names = (VAD_MODEL,) if engine is None else (VAD_MODEL, engine)
        return {"models": [{"name": n, "ready": self.manager.is_ready(n)} for n in names]}

    def _progress_notifier(
        self,
        loop: asyncio.AbstractEventLoop,
        name: str,
        cancelled: threading.Event | None = None,
    ) -> Callable[[int, int], None]:
        """The progress callback of a download of `name`: it sends `models.progress` at most every
        PROGRESS_INTERVAL_S (the last report always), from the download's thread. When `cancelled`
        is set it raises _DownloadCancelled, which stops the download."""
        sent_at = -float("inf")

        def progress(done: int, total: int) -> None:
            nonlocal sent_at
            if cancelled is not None and cancelled.is_set():
                raise _DownloadCancelled(name)
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

    async def _models_ensure(self, params: dict) -> dict:
        names = params.get("names")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise invalid_params("names must be a list of model names")
        engine = self._speech.engine_name
        allowed = {VAD_MODEL} if engine is None else {VAD_MODEL, engine}
        unknown = [n for n in names if n not in allowed]
        if unknown:
            raise invalid_params(f"not a model the app downloads: {', '.join(unknown)}")
        loop = asyncio.get_running_loop()

        def download() -> None:
            for name in names:
                self.manager.ensure(name, self._progress_notifier(loop, name))

        await asyncio.to_thread(download)
        self._load_models_in_background()  # the first dictation should not wait for them
        return {"ok": True}

    # --- the catalogue of local speech models ---------------------------------------------------

    @staticmethod
    def _local_name(params: dict) -> str:
        """The model of the catalogue that `params` name."""
        name = params.get("name")
        if not isinstance(name, str):
            raise invalid_params("name must be a model name")
        try:
            return local_model(name).name
        except ValueError:
            raise invalid_params(f"not a model of the catalogue: {name}") from None

    async def _models_local(self, params: dict) -> dict:
        """The catalogue: each model with its size and whether it is on disk, the one the local
        engine is, and the one being downloaded. Reading it touches no speech model."""
        settings = (await self.call("settings.get", {}))["settings"]

        def read() -> list[dict]:
            return [
                {
                    "name": entry.name,
                    "title": entry.title,
                    "languages": list(entry.languages),
                    "note": entry.note,
                    "size": REGISTRY[entry.name].download_size,
                    "ready": self.manager.is_ready(entry.name),
                }
                for entry in LOCAL_STT_MODELS
            ]

        return {
            "models": await asyncio.to_thread(read),
            "active": settings["stt_local_model"],
            "fallback": None if settings["stt_engine"] == "local" else settings["stt_local_model"],
            "downloading": self._local_download,
        }

    async def _models_local_use(self, params: dict) -> dict:
        """Make a model of the catalogue the local engine, downloading it first if it is not on
        disk. The download reports `models.progress` (named for the model); only a complete one
        changes the settings, so a download that fails or is cancelled leaves the engine in use
        as it was. Answers {"ok": True, "settings": ...}, or {"ok": False, "cancelled": True}."""
        name = self._local_name(params)
        if self._local_download is not None:
            raise RpcError(BUSY, f"Another speech model is being downloaded: {self._local_download}")
        self._local_download = name
        self._local_cancel.clear()
        progress = self._progress_notifier(asyncio.get_running_loop(), name, self._local_cancel)
        try:
            await asyncio.to_thread(self.manager.ensure, name, progress)
        except _DownloadCancelled:
            logger.info("The download of %s was cancelled", name)
            return {"ok": False, "cancelled": True}
        except ModelDownloadError as exc:
            raise RpcError(INTERNAL_ERROR, str(exc)) from exc
        finally:
            self._local_download = None
        async with self._models_lock:
            if not self.manager.is_ready(name):  # deleted while this waited for its turn
                raise RpcError(BUSY, f"{name} was removed meanwhile")
            await self.call("settings.set", {"key": "stt_local_model", "value": name})
            reply = await self.call("settings.set", {"key": "stt_engine", "value": "local"})
            self._speech.select_engine(name)
        self._load_models_in_background()  # built beside the engine in use, which serves until then
        return {"ok": True, "settings": reply["settings"]}

    async def _models_local_cancel(self, params: dict) -> dict:
        if self._local_download is None:
            return {"cancelled": False}
        self._local_cancel.set()
        return {"cancelled": True}

    async def _models_local_delete(self, params: dict) -> dict:
        async with self._models_lock:  # so that no two deletes choose each other's model
            return await self._delete_local(params)

    async def _delete_local(self, params: dict) -> dict:
        """Delete a downloaded model of the catalogue that is not in use. The detector is not one
        of them. While the cloud is the engine the local model is only the fallback, and that can
        be deleted too (not during a dictation): it is unloaded first, and another downloaded
        model takes its place, or none does. Answers whether anything was there to delete and, in
        that case, the fallback that follows."""
        name = self._local_name(params)
        if name == self._local_download:
            raise RpcError(BUSY, f"{name} is being downloaded")
        settings = (await self.call("settings.get", {}))["settings"]
        chosen = settings["stt_local_model"]
        retire = settings["stt_engine"] != "local" and name == chosen
        if not retire:
            in_use = {chosen, self._speech.engine_name, self._speech.loaded_engine_name}
            if name in in_use:
                raise RpcError(BUSY, f"{name} is in use")
        elif not self._dictation_idle():
            raise RpcError(BUSY, f"{name} is needed by the dictation")
        replacement: str | None = None
        if retire:
            replacement = next(
                (
                    entry.name
                    for entry in LOCAL_STT_MODELS
                    if entry.name != name and self.manager.is_ready(entry.name)
                ),
                None,
            )
            await self.call("settings.set", {"key": "stt_local_model", "value": replacement})
            self._speech.select_engine(replacement)
            await asyncio.to_thread(self._speech.unload_engine)

        def delete() -> bool:
            directory = self.manager.dir(name)
            if not directory.exists():
                return False
            for attempt in range(DELETE_TRIES):
                try:
                    shutil.rmtree(directory)
                    return True
                except PermissionError as exc:  # a file ONNX Runtime has not let go of yet
                    if attempt == DELETE_TRIES - 1:
                        raise RpcError(INTERNAL_ERROR, f"Could not delete {name}: {exc}") from exc
                    time.sleep(DELETE_RETRY_S)
                except OSError as exc:
                    raise RpcError(INTERNAL_ERROR, f"Could not delete {name}: {exc}") from exc
            return True

        try:
            deleted = await asyncio.to_thread(delete)
        except BaseException:
            # Some files may be gone: the old fallback comes back only if it still works.
            if retire and self.manager.is_ready(name):
                await self.call("settings.set", {"key": "stt_local_model", "value": name})
                self._speech.select_engine(name)
            raise
        finally:
            if retire:
                self._load_models_in_background()  # the fallback, if there is one
        logger.info("Deleted the speech model %s", name)
        reply: dict[str, Any] = {"deleted": deleted}
        if retire:
            reply["fallback"] = replacement
        return reply

    # --- the microphone -------------------------------------------------------------------------

    async def _audio_devices(self, params: dict) -> dict:
        refresh = params.get("refresh", True)
        if type(refresh) is not bool:
            raise invalid_params("refresh must be true or false")
        kit = await self._wait_for(self._kit)
        # Listing reads the devices again, so that a headset plugged in since shows up, and that is
        # not done while a stream is open. The settings meter's stream is the one that usually is:
        # it makes way for the reading and comes back after, as it does for a dictation.
        if refresh:
            await self._meter("make way for reading the devices", lambda m: m.suspend())
            try:
                devices = await asyncio.to_thread(kit.list_input_devices)
            finally:
                await self._give_meter_back()
        else:
            devices = await asyncio.to_thread(kit.current_input_devices)
        return {
            "devices": [
                {"index": d.index, "name": d.name, "is_default": d.is_default} for d in devices
            ],
            "selected": self._device_name,
            "automatic_device": kit.automatic_device_name(),
        }

    async def _audio_set_device(self, params: dict) -> dict:
        name = params.get("name")
        if name is not None and not isinstance(name, str):
            raise invalid_params("name must be a string or null")
        name = name if name is not None and name.strip() else None  # blank: the default
        changed = name != self._device_name
        self._device_name = name
        if changed and self._monitor is not None:
            await self._monitor.restart()  # a running meter follows the choice
        return {}

    async def _audio_monitor(self, params: dict) -> dict:
        on = params.get("on")
        if type(on) is not bool:
            raise invalid_params("on must be true or false")
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
        if method == "dictation.start":
            self.speaker.stop()  # a new recording ends what Raphael was saying
        service = await self._wait_for(self._service)
        if method != "dictation.start":
            try:
                return await self._inner.call(method, params)
            finally:
                await self._meter_settled()
        self._starts += 1
        try:
            await self._read_stt_engine()
            self._speech.require_models(engine_needed=self._stt_engine == "local")
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
    unchanged at no cost, and the Credential Manager for an in-memory key and plugin vault.
    """
    app = App(
        data_dir=paths.data_dir() if data_dir is None else Path(data_dir),
        # With no data directory given the models are in the machine-local folder, not beside the
        # database; `models_dir()` also honours RAPHAEL_DATA_DIR and RAPHAEL_MODELS_DIR.
        models_dir=paths.models_dir() if data_dir is None else None,
        fake_audio=None if fake_audio is None else Path(fake_audio),
        fake_llm=fake_llm,
    )
    app.start()
    return app
