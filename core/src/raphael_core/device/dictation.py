"""The dictation service: the microphone and the speech-to-text engine behind three RPC methods.

`dictation.start` opens the microphone, `dictation.stop` turns what was said into text and has the
brain clean it, `dictation.cancel` throws everything away. While the key is held the audio is cut
into stretches of speech as it comes in, and each stretch is transcribed at once, so a long
dictation is mostly done by the time the key is released and only the last stretch is left to wait
for (spec section 5).

Threads. The recorder calls `_on_frames` on the audio thread (and once more, from the thread that
calls `stop()`, with the last few milliseconds): it only counts the frames, puts them on a
`queue.SimpleQueue` and, now and then, reports the level. One worker thread per session takes the
frames off the queue, feeds the segmenter and transcribes every stretch that ends. Everything else
runs on the event loop: the calls to the brain (the stores are used on the loop thread only), the
state of the session, the microphone watchdog. Whatever can block for long (opening the
microphone, building a segmenter, getting the engine, which may still be loading) runs on a worker
thread, never on the loop. Thread to loop goes through `loop.call_soon_threadsafe`.

The service is handed what it uses and builds none of it, so the app can load the engine and the
voice activity model once, at start-up, which takes tens of seconds (ONNX Runtime). The segmenter
factory is called once per dictation, off the loop. It should return a segmenter that is ready
(one built at start-up can be used again: `flush()` leaves it fresh), or make one cheaply.

Importing this module loads neither sherpa-onnx nor PortAudio; making a service does.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from raphael_core.protocol import (
    BUSY,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    NO_MICROPHONE,
    NOT_CONFIGURED,
    NOT_FOUND,
    RpcError,
)
from raphael_core.router import Router

if TYPE_CHECKING:
    from raphael_core.device.audio import Recorder
    from raphael_core.device.stt import SttEngine

logger = logging.getLogger(__name__)

# The format of the frames a recorder delivers (audio.SAMPLE_RATE).
SAMPLE_RATE = 16_000
# A safety net against a key that is held for hours: after this long the audio is no longer kept.
MAX_RECORDING_SECONDS = 300
# Less speech than this is a cough or a tap, not a dictation.
MIN_SPEECH_SECONDS = 0.3
# Windows MME may never tell us that a microphone was unplugged: the frames just stop. A recording
# that gets no frames for this long is treated like one whose recorder reported an error.
MIC_STALL_SECONDS = 2.0
# A session still recording this long after its start has been forgotten: the controller should
# have stopped or cancelled it long since (the audio stops being kept at MAX_RECORDING_SECONDS). It
# is cancelled and the microphone closed, so the service does not stay busy, and the microphone
# open, for good.
LOST_SESSION_SECONDS = MAX_RECORDING_SECONDS + 60

_LEVEL_METHOD = "audio.level"

# What goes on a session's queue besides frames.
_FLUSH = object()  # the recording is over: finish and transcribe what is left
_CANCEL = object()  # throw it all away
_CAPPED = object()  # the recording passed MAX_RECORDING_SECONDS: say so (not on the audio thread)


class SegmenterLike(Protocol):
    """What the service needs of `vad.Segmenter`.

    `speech_seconds` is the length of the speech found in the current dictation: it starts again
    at the first `accept` after a `flush`.
    """

    speech_seconds: float

    def accept(self, frames: np.ndarray) -> list[np.ndarray]: ...

    def flush(self) -> list[np.ndarray]: ...


def _is_cjk(ch: str) -> bool:
    """Han, kana, and the punctuation that goes with them (CJK and full-width forms)."""
    code = ord(ch)
    return (
        0x3000 <= code <= 0x30FF  # CJK symbols and punctuation, hiragana, katakana
        or 0x31F0 <= code <= 0x31FF  # katakana phonetic extensions
        or 0x3400 <= code <= 0x4DBF  # Han, extension A
        or 0x4E00 <= code <= 0x9FFF  # Han
        or 0xF900 <= code <= 0xFAFF  # Han, compatibility ideographs
        or 0xFF00 <= code <= 0xFFEF  # full-width and half-width forms
        or 0x20000 <= code <= 0x2FA1F  # Han, extension B onwards
    )


def join_segments(texts: Sequence[str]) -> str:
    """The texts of the segments as one: no separator where both sides of the join are CJK (Chinese
    and Japanese are not written with spaces), one space everywhere else."""
    joined = ""
    for text in texts:
        piece = text.strip()
        if not piece:
            continue
        if joined and not (_is_cjk(joined[-1]) and _is_cjk(piece[0])):
            joined += " "
        joined += piece
    return joined


class _Session:
    """One dictation, from `dictation.start` to the end of `dictation.stop` or `.cancel`."""

    def __init__(
        self, session_id: str, loop: asyncio.AbstractEventLoop, now: float, max_samples: int
    ) -> None:
        self.id = session_id
        self.loop = loop
        # "starting" (being set up), "recording", "stopping" (the last words are transcribed), or
        # "ended". Only the loop reads and writes it.
        self.state = "starting"
        self.cancelled = False
        self.ready = asyncio.Event()  # set when the start has finished, well or not
        self.done: asyncio.Future[None] = loop.create_future()  # set when the worker has exited

        self.recorder: Recorder | None = None
        self.recorder_open = False
        self.segmenter: SegmenterLike | None = None
        self.worker: threading.Thread | None = None
        self.worker_told = False
        self.watch: asyncio.Task[None] | None = None
        self.watch_busy = False  # the watchdog is closing the recorder or cancelling the session
        self.started_at = now  # when the recording began, on the service's clock
        self.stalled = False
        self.close_lock = asyncio.Lock()
        self.hints: list[str] = []
        self.language = "auto"

        # The audio thread's, under `lock`. `queue` is thread-safe by itself.
        self.queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self.lock = threading.Lock()
        self.closed = False  # the recorder is closed: late frames are dropped
        self.levels_on = True
        self.received = 0
        self.max_samples = max_samples
        self.capped = False
        self.last_frame_at = now
        self.last_level_at = -float("inf")

        # The worker's; the loop reads them once `done` is set.
        self.engine: SttEngine | None = None
        self.texts: list[str] = []
        self.fed_any = False
        self.failure: Exception | None = None  # the first thing that went wrong
        self.fatal = False  # ... and it was one that ends the session's work

    def fail(self, error: Exception, *, fatal: bool) -> None:
        if self.failure is None:
            self.failure = error
        self.fatal = self.fatal or fatal


def _session_id(params: dict) -> str:
    value = params.get("session_id")
    if not isinstance(value, str) or not value:
        raise RpcError(INVALID_PARAMS, "session_id must be a non-empty string")
    return value


def _target_app(params: dict) -> str | None:
    value = params.get("target_app")
    if value is not None and not isinstance(value, str):
        raise RpcError(INVALID_PARAMS, "target_app must be a string or null")
    return value


def _failure_as_error(error: Exception) -> RpcError:
    if isinstance(error, RpcError):
        return error
    if isinstance(error, FileNotFoundError):
        return RpcError(NOT_CONFIGURED, f"A speech model is not downloaded: {error}")
    return RpcError(INTERNAL_ERROR, f"Speech-to-text failed: {error or type(error).__name__}")


class DictationService:
    """Dictation behind `dictation.start`, `dictation.stop` and `dictation.cancel`.

    `router` is how the service reaches the brain (`dictionary.list`, `settings.get`,
    `brain.finalize_dictation`) and where it sends `audio.level` notifications. `recorder_factory`
    makes a recorder that delivers its frames to the function it is given. `segmenter_factory` and
    `engine_provider` are called off the loop; `engine_provider` may block until the engine has
    loaded, and is first called when the first stretch of speech has been heard, so a recording
    does not wait for it. `level_hz` is the most `audio.level` notifications a second.

    `stall_seconds` and `clock` (the time, in seconds) belong to the microphone watchdog and the
    level throttle; tests set them.
    """

    def __init__(
        self,
        router: Router,
        *,
        recorder_factory: Callable[[Callable[[np.ndarray], None]], Recorder],
        segmenter_factory: Callable[[], SegmenterLike],
        engine_provider: Callable[[], SttEngine],
        level_hz: float = 30.0,
        stall_seconds: float = MIC_STALL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not level_hz > 0:
            raise ValueError(f"level_hz must be more than 0, not {level_hz}")
        if not stall_seconds > 0:
            raise ValueError(f"stall_seconds must be more than 0, not {stall_seconds}")
        # Only here, not at import: audio.py brings in sherpa-onnx and PortAudio.
        from raphael_core.device import audio

        self._rms_level = audio.rms_level
        self._no_microphone = audio.NoMicrophoneError
        self._router = router
        self._recorder_factory = recorder_factory
        self._segmenter_factory = segmenter_factory
        self._engine_provider = engine_provider
        self._level_interval = 1.0 / level_hz
        self._stall_seconds = stall_seconds
        self._watch_interval = min(0.25, stall_seconds / 4)
        self._clock = clock
        self._session: _Session | None = None
        # Cancelled sessions whose worker has not exited yet (it may be inside the engine).
        self._retiring: list[_Session] = []

    @property
    def active(self) -> bool:
        """True from the start of `dictation.start` until the speech is transcribed or cancelled."""
        return self._session is not None

    @property
    def stall_seconds(self) -> float:
        return self._stall_seconds

    def register(self, router: Router) -> None:
        router.register("dictation.start", self._start)
        router.register("dictation.stop", self._stop)
        router.register("dictation.cancel", self._cancel)

    async def aclose(self) -> None:
        """Cancel the dictation in progress, if any, and close the microphone."""
        session = self._session
        if session is not None:
            await self._cancel_session(session)

    # --- dictation.start ------------------------------------------------------------------------

    async def _start(self, params: dict) -> dict:
        session_id = _session_id(params)
        if self._session is not None:
            raise RpcError(BUSY, "A dictation is already in progress")
        session = _Session(
            session_id,
            asyncio.get_running_loop(),
            self._clock(),
            int(MAX_RECORDING_SECONDS * SAMPLE_RATE),
        )
        self._session = session
        try:
            await self._set_up(session)
        except asyncio.CancelledError:
            self._abandon(session)
            raise
        except BaseException:
            await self._tear_down(session)
            raise
        finally:
            session.ready.set()
        return {}

    async def _set_up(self, session: _Session) -> None:
        session.hints, session.language = await self._context()
        self._check_not_cancelled(session)
        segmenter = await asyncio.to_thread(self._segmenter_factory)
        session.segmenter = segmenter
        await self._wait_for_retired(segmenter)
        self._check_not_cancelled(session)
        recorder = self._recorder_factory(lambda frames: self._on_frames(session, frames))
        session.recorder = recorder
        opening = asyncio.ensure_future(asyncio.to_thread(recorder.start))
        try:
            await asyncio.shield(opening)
        except self._no_microphone as exc:
            raise RpcError(NO_MICROPHONE, str(exc) or "There is no microphone") from exc
        except asyncio.CancelledError:
            # The request was cancelled while the microphone was opening. The thread goes on, so
            # the microphone is closed again as soon as it is open.
            opening.add_done_callback(lambda done: self._close_late(recorder, done))
            raise
        session.recorder_open = True
        session.started_at = session.last_frame_at = self._clock()
        self._check_not_cancelled(session)
        session.worker = threading.Thread(
            target=self._run, args=(session,), name=f"raphael-dictation-{session.id}", daemon=True
        )
        session.worker.start()
        session.watch = asyncio.create_task(self._watchdog(session))
        session.state = "recording"

    def _close_late(self, recorder: Recorder, opening: asyncio.Future[None]) -> None:
        if opening.cancelled() or opening.exception() is not None:
            return  # it did not open (and the exception is now looked at)
        asyncio.get_running_loop().run_in_executor(None, self._stop_recorder, recorder)

    @staticmethod
    def _check_not_cancelled(session: _Session) -> None:
        if session.cancelled:
            raise RpcError(NOT_FOUND, "The dictation was cancelled before it started")

    async def _context(self) -> tuple[list[str], str]:
        """The user's dictionary words (newest first, as the brain lists them) and the language."""
        hints: list[str] = []
        language = "auto"
        try:
            result = await self._router.call("dictionary.list", {})
            for entry in result["entries"]:
                word = entry.get("word")
                if isinstance(word, str) and word.strip():
                    hints.append(word)
        except Exception as exc:
            logger.warning("Could not read the dictionary; dictating without hints: %s", exc)
        try:
            result = await self._router.call("settings.get", {})
            value = result["settings"].get("language")
            if isinstance(value, str) and value:
                language = value
        except Exception as exc:
            logger.warning("Could not read the language setting; using automatic: %s", exc)
        return hints, language

    async def _wait_for_retired(self, segmenter: SegmenterLike) -> None:
        """A segmenter is used by one thread at a time: wait for a cancelled session that still has
        this one (a cancel does not wait for the engine to finish what it is decoding)."""
        self._retiring = [s for s in self._retiring if not s.done.done()]
        waiting = [s.done for s in self._retiring if s.segmenter is segmenter]
        if waiting:
            await asyncio.wait(waiting)

    # --- the audio thread -----------------------------------------------------------------------

    def _on_frames(self, session: _Session, frames: np.ndarray) -> None:
        """The recorder's callback. Runs on the audio thread, and once on the thread that calls
        `stop()`; so it keeps its state under a lock and does no more than it must."""
        frames = np.asarray(frames, dtype=np.float32)
        now = self._clock()
        with session.lock:
            session.last_frame_at = now
            if session.closed or frames.size == 0:
                return
            room = session.max_samples - session.received
            hit_cap = (room <= 0 or frames.size > room) and not session.capped
            if hit_cap:
                session.capped = True
            if room > 0:
                frames = frames[:room]
                session.received += frames.size
            keep = room > 0
            send_level = (
                keep and session.levels_on and now - session.last_level_at >= self._level_interval
            )
            if send_level:
                session.last_level_at = now
        if keep:
            session.queue.put(frames)
        if hit_cap:
            session.queue.put(_CAPPED)
        if send_level:
            # Here and not in the worker: that one can be inside the engine for seconds, and the
            # ring that shows the level should keep moving while the user is still talking.
            self._call_on_loop(
                session,
                self._router.notify,
                _LEVEL_METHOD,
                {"session_id": session.id, "level": self._rms_level(frames)},
            )

    @staticmethod
    def _call_on_loop(session: _Session, fn: Callable[..., Any], *args: Any) -> None:
        try:
            session.loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # the loop is closed: the process is going down
            pass

    # --- the worker thread ----------------------------------------------------------------------

    def _run(self, session: _Session) -> None:
        try:
            self._work(session)
        except BaseException as exc:
            logger.exception("The dictation worker failed")
            session.fail(exc if isinstance(exc, Exception) else RuntimeError(str(exc)), fatal=True)
        finally:
            self._call_on_loop(session, self._set_done, session)

    @staticmethod
    def _set_done(session: _Session) -> None:
        if not session.done.done():
            session.done.set_result(None)

    def _work(self, session: _Session) -> None:
        while True:
            item = session.queue.get()
            if item is _FLUSH or item is _CANCEL:
                break
            if item is _CAPPED:
                logger.warning(
                    "The recording reached the limit of %g s; the rest of it is not kept",
                    session.max_samples / SAMPLE_RATE,
                )
                continue
            if session.cancelled or session.fatal:
                continue  # drained, so the queue does not grow
            self._feed(session, item)
        self._finish(session, transcribe=item is _FLUSH)

    def _feed(self, session: _Session, frames: np.ndarray) -> None:
        assert session.segmenter is not None
        session.fed_any = True
        try:
            segments = session.segmenter.accept(frames)
        except Exception as exc:
            logger.exception("The voice activity detector failed")
            session.fail(exc, fatal=True)
            return
        for segment in segments:
            self._transcribe(session, segment)

    def _finish(self, session: _Session, *, transcribe: bool) -> None:
        """The end of the recording: the speech that was still going on, and a segmenter that is
        fresh for the next dictation either way."""
        assert session.segmenter is not None
        try:
            rest = session.segmenter.flush()
        except Exception as exc:
            logger.exception("The voice activity detector failed")
            session.fail(exc, fatal=True)
            return
        if transcribe:
            for segment in rest:
                self._transcribe(session, segment)

    def _transcribe(self, session: _Session, segment: np.ndarray) -> None:
        if session.cancelled or session.fatal:
            return
        if session.engine is None:
            try:
                session.engine = self._engine_provider()
            except Exception as exc:
                logger.exception("The speech-to-text engine is not available")
                session.fail(exc, fatal=True)
                return
        try:
            text = session.engine.transcribe(
                segment, hints=session.hints, language=session.language
            )
        except Exception as exc:
            logger.exception("A stretch of speech could not be transcribed; it is left out")
            session.fail(exc, fatal=False)
            return
        text = str(text).strip()
        if text and not session.cancelled:
            session.texts.append(text)

    # --- the microphone watchdog ----------------------------------------------------------------

    async def _watchdog(self, session: _Session) -> None:
        """Two things to look out for while a session is recording.

        A recorder that has gone quiet is closed. The session goes on, as for a recorder error: the
        stop uses the speech that was captured, or says there is no microphone.

        A session that nobody has stopped or cancelled for LOST_SESSION_SECONDS is cancelled, the
        way `dictation.cancel` would, and the microphone closed.
        """
        while session.state == "recording":
            await asyncio.sleep(self._watch_interval)
            if session.state != "recording":
                return
            now = self._clock()
            if now - session.started_at >= LOST_SESSION_SECONDS:
                logger.warning(
                    "The dictation session %r is lost: it has been recording for %g s with no "
                    "stop or cancel; cancelling it and closing the microphone",
                    session.id,
                    now - session.started_at,
                )
                session.watch_busy = True
                try:
                    await self._cancel_session(session)
                finally:
                    session.watch_busy = False
                return
            if not session.stalled and now - session.last_frame_at >= self._stall_seconds:
                logger.warning(
                    "No audio from the microphone for %g s: taking it as gone and closing it",
                    self._stall_seconds,
                )
                session.stalled = True
                session.watch_busy = True
                try:
                    await self._close_recorder(session)
                finally:
                    session.watch_busy = False

    async def _stop_watchdog(self, session: _Session) -> None:
        """Make the watchdog end: wake it if it is asleep, wait for it if it is busy closing the
        recorder or cancelling. Called from the watchdog itself (through its cancel): nothing to do.
        """
        watch = session.watch
        if watch is None or watch.done() or watch is asyncio.current_task():
            return
        if not session.watch_busy:
            watch.cancel()
        await asyncio.wait([watch])

    # --- dictation.stop -------------------------------------------------------------------------

    async def _stop(self, params: dict) -> dict:
        session_id = _session_id(params)
        target_app = _target_app(params)
        session = self._session
        if session is None or session.id != session_id or session.state == "stopping":
            raise RpcError(NOT_FOUND, f"There is no dictation session {session_id!r} to stop")
        if session.state == "starting":
            await session.ready.wait()
            if self._session is not session or session.state != "recording":
                raise RpcError(NOT_FOUND, f"The dictation session {session_id!r} did not start")
        requested = time.perf_counter()
        session.state = "stopping"
        try:
            await self._stop_watchdog(session)
            with session.lock:
                session.levels_on = False
            await self._close_recorder(session)  # the last frames arrive before it returns
            with session.lock:
                session.closed = True
            self._tell_worker(session, _FLUSH)
            await asyncio.wait([session.done])
        except BaseException:
            self._abandon(session)
            raise
        stt_ms = round((time.perf_counter() - requested) * 1000)
        self._end(session)  # idle again before the brain is asked: a new dictation may start
        if session.cancelled:
            raise RpcError(NOT_FOUND, f"The dictation session {session_id!r} was cancelled")

        assert session.recorder is not None and session.segmenter is not None
        mic_failed = session.recorder.error is not None or session.stalled
        text = join_segments(session.texts)
        if session.failure is not None and not text:
            raise _failure_as_error(session.failure)
        # The segmenter's own count, but not if it never heard this recording: a segmenter that is
        # used again still reports the last dictation until its first frames come.
        speech = float(session.segmenter.speech_seconds) if session.fed_any else 0.0
        if speech < MIN_SPEECH_SECONDS or not text:
            if mic_failed:
                reason = session.recorder.error or "no sound for %g s" % self._stall_seconds
                raise RpcError(NO_MICROPHONE, f"The microphone stopped working: {reason}")
            return {
                "id": None,
                "text": "",
                "raw_text": "",
                "cleaned": False,
                "fallback_reason": "empty",
                "llm_ms": 0,
                "audio_seconds": speech,
                "stt_ms": stt_ms,
            }
        if mic_failed:
            logger.warning("The microphone stopped during the recording; using what was captured")
        result = await self._router.call(
            "brain.finalize_dictation",
            {
                "raw_text": text,
                "audio_seconds": speech,
                "stt_ms": stt_ms,
                "target_app": target_app,
            },
        )
        return {**result, "audio_seconds": speech, "stt_ms": stt_ms}

    # --- dictation.cancel -----------------------------------------------------------------------

    async def _cancel(self, params: dict) -> dict:
        """A recovery call: it cancels whatever session is active, whatever id it was given."""
        session = self._session
        if session is not None:
            await self._cancel_session(session)
        return {}

    async def _cancel_session(self, session: _Session) -> None:
        session.cancelled = True
        if session.state == "starting":
            # The start sees the flag at its next step, closes what it opened and ends the session.
            await session.ready.wait()
            return
        await self._stop_watchdog(session)
        with session.lock:
            session.levels_on = False
        await self._close_recorder(session)
        with session.lock:
            session.closed = True
        self._tell_worker(session, _CANCEL)
        if session.worker is not None:
            self._retiring.append(session)
        self._end(session)

    # --- shared parts ---------------------------------------------------------------------------

    async def _close_recorder(self, session: _Session) -> None:
        """Stop the recorder once, off the loop. Whoever comes second waits for the first."""
        async with session.close_lock:
            if session.recorder is None or not session.recorder_open:
                return
            await asyncio.to_thread(self._stop_recorder, session.recorder)
            session.recorder_open = False

    @staticmethod
    def _stop_recorder(recorder: Recorder) -> None:
        try:
            recorder.stop()
        except Exception:
            logger.exception("The microphone did not stop cleanly")

    def _tell_worker(self, session: _Session, signal: object) -> None:
        """Send the worker its last item. Only the first counts."""
        if session.worker is not None and not session.worker_told:
            session.worker_told = True
            session.queue.put(signal)

    def _end(self, session: _Session) -> None:
        session.state = "ended"
        if self._session is session:
            self._session = None

    async def _tear_down(self, session: _Session) -> None:
        """A start that did not succeed: nothing stays open and the service is idle."""
        session.cancelled = True
        await self._close_recorder(session)
        with session.lock:
            session.closed = True
        self._tell_worker(session, _CANCEL)
        self._end(session)

    def _abandon(self, session: _Session) -> None:
        """Like cancelling, but without waiting for anything: for a request that was itself
        cancelled, which may not await."""
        session.cancelled = True
        with session.lock:
            session.closed = True
            session.levels_on = False
        if session.watch is not None and not session.watch_busy:
            session.watch.cancel()
        if session.recorder is not None and session.recorder_open:
            session.recorder_open = False
            # Fire and forget, like `_close_late`: the request that is being abandoned must not
            # hold up the loop for as long as the microphone takes to close.
            session.loop.run_in_executor(None, self._stop_recorder, session.recorder)
        self._tell_worker(session, _CANCEL)
        if session.worker is not None and session not in self._retiring:
            self._retiring.append(session)
        self._end(session)
