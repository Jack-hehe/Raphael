"""Raphael's voice: his answers spoken aloud, one at a time, and stopped the moment the owner moves on.

`Speaker.say(prefix, text)` is called by the assistant (`agent.service`) with the line to speak, for
the owner's own requests only (never a dictation, a message from a phone, a report of the
background worker, or a question waiting for a yes). The line is the one the voice speaks, which is
Japanese whatever the language on screen is (`agent.persona.spoken_line` makes it): the owner's
voice model speaks Japanese only. The prefix (解, 告, 否, 了) is part of his character and is
spoken ("告。マスター，…"); the brackets of the overlay are not. The text is made speakable first
(`speakable`): addresses and the marks of markdown are left out, whitespace is collapsed and a long
answer is cut at a sentence end, since an answer read aloud for a minute is not an answer.

What happens, in order, all on the event loop except the playing:

1. `say` stops whatever was being said (one utterance at a time: a new one replaces the old). With
   no voice service chosen it returns at once.
2. A task asks the service for the sound (`ServiceHub.speech`, a stream of PCM) and gives each chunk
   to the player (`playback`) as it arrives, on a worker thread, which writes it to the device a
   block of about 50 ms at a time and looks at the utterance's `stopped` event before each block.
   The sound begins with the first chunk: nothing waits for the rest.
3. `say` waits for that beginning: it returns when the player is about to write the first block
   (the voice is ready and playing), so that the assistant can show the line at that very moment.
   The wait is bounded by `FIRST_AUDIO_WAIT_S`; after that `say` returns anyway and the sound
   begins whenever the first chunk comes, but a first chunk that comes more than `LATE_DROP_S`
   after the request is dropped (a line shown long ago and spoken now is worse than a silent one).
   A voice that fails (no key, no voice id, the network, an error code of the service, a device that
   cannot play) ends the wait at once.

`stop` (the owner starts a new recording, presses Esc, asks a new question, `tts.stop`) sets the
event of the utterance, cancels its task, which closes the request still on its way, and ends the
wait: the sound ends within a block. A failure is logged as a kind, once for each kind in a row, and
the answer, already on screen, stays silent (or ends where the stream broke): nothing is retried,
which would only delay. The text, the voice id and the audio are never logged.

`speech.state` reports `playing` at the first sound and exactly one `done` after the player
exits, including its last buffer. An attempt with no sound (failure, stop, late drop) still
reports `done`. Answers carry their `turn_id`; previews omit it. Notifications contain no words.

RPC methods:

- `tts.stop` -> `{ok: true}`: the speech ends at once.
- `tts.preview {text}` -> `{ok: true}` once the sound is playing, or `{ok: false, kind}` (the kinds
  of `common.ServiceError`, or "playback"): a short line spoken in the voice that is set up now,
  for the settings page to try it. It is always the Japanese greeting `PREVIEW_LINE`, whatever
  language the page is in; `text` (at most PREVIEW_MAX_CHARS characters) is what the page shows
  and is checked but not spoken. It replaces whatever was being said, like an answer.
"""

import asyncio
import logging
import queue
import re
import threading
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..protocol import invalid_params
from ..router import Notifier, Router
from .common import ServiceError
from .hub import ServiceHub
from .playback import Player, SoundDevicePlayer
from .tts import PCM_RATE

logger = logging.getLogger(__name__)

# The most that is spoken of an answer: about half a minute of speech.
MAX_SPOKEN_CHARS = 400
# The most that `tts.preview` takes.
PREVIEW_MAX_CHARS = 200
# What `tts.preview` speaks: the greeting, in the voice's own language.
PREVIEW_LINE = "告。マスター、ご指示をどうぞ。"
# How long an answer waits for the first sound before it is returned without it.
FIRST_AUDIO_WAIT_S = 2.5
# A first chunk of sound later than this after the request is dropped, unplayed.
LATE_DROP_S = 8.0

_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# The marks of markdown that are read as symbols if left in: emphasis, code, headings, quotes.
_MARKDOWN = re.compile(r"[*_`#>~|]+")
_BULLET = re.compile(r"^\s*(?:[-+•]|\d+[.)])\s+", re.MULTILINE)
_SENTENCE_END = "。！？!?.\n"


class Speaking(Protocol):
    """What the assistant needs of a voice: whether there is one to wait for, say a line (returns
    when the sound begins, or has been given up for now), stop."""

    def speaks(self) -> bool: ...

    async def say(self, prefix: str, text: str, *, turn_id: int | None = None) -> None: ...

    def stop(self) -> None: ...


def speakable(text: str) -> str:
    """`text` as it is read aloud: addresses and markdown marks taken out, whitespace collapsed,
    and cut after the last sentence end within MAX_SPOKEN_CHARS (or at the limit if there is
    none). Empty if nothing is left to say."""
    plain = _URL.sub(" ", text)
    plain = _BULLET.sub("", plain)
    plain = _MARKDOWN.sub(" ", plain)
    plain = " ".join(plain.split())
    if len(plain) <= MAX_SPOKEN_CHARS:
        return plain
    window = plain[:MAX_SPOKEN_CHARS]
    end = max(window.rfind(mark) for mark in _SENTENCE_END)
    return (window[: end + 1] if end > MAX_SPOKEN_CHARS // 3 else window).strip()


def line(prefix: str, text: str) -> str:
    """The line as it is spoken: the prefix, a stop, the text made speakable. "" when the text has
    nothing to say. The prefix of the English persona is a word ("Notice"), the others one
    character (告), and each is followed by the stop of its script."""
    body = speakable(text)
    if not body:
        return ""
    stop = ". " if prefix.isascii() else "。"
    return f"{prefix.strip()}{stop}{body}" if prefix.strip() else body


@dataclass
class _Utterance:
    """One thing being said: `stopped` ends the sound, `ready` is set when the sound begins (or
    when there will be none: a failure, a stop), `failure` is the kind of the failure, if any."""

    stopped: threading.Event = field(default_factory=threading.Event)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    failure: str | None = None
    task: "asyncio.Task[None] | None" = None
    turn_id: int | None = None
    finished: bool = False
    playing: bool = False
    voice: str | None = None


class Speaker:
    """The voice of Raphael: `hub` makes the sound, `player` plays it (the sound device by
    default; tests give a fake). `first_audio_wait_s` and `late_drop_s` are the two bounds of the
    module, for tests."""

    def __init__(
        self,
        hub: ServiceHub,
        player: Player | None = None,
        *,
        first_audio_wait_s: float = FIRST_AUDIO_WAIT_S,
        late_drop_s: float = LATE_DROP_S,
    ) -> None:
        self._hub = hub
        self._player = player
        self._first_audio_wait_s = first_audio_wait_s
        self._late_drop_s = late_drop_s
        self._current: _Utterance | None = None
        self._notify: Notifier | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        # The kind of the latest failure, so that a service that is down is logged once in a row.
        self._last_failure: str | None = None

    def register(self, router: Router) -> None:
        self._notify = router.notify
        router.register("tts.stop", self._stop_rpc)
        router.register("tts.preview", self._preview)

    def _player_now(self) -> Player:
        """The player, made on first use: PortAudio is not needed until something is said."""
        if self._player is None:
            self._player = SoundDevicePlayer()
        return self._player

    # --- saying and stopping --------------------------------------------------------------------

    def speaks(self) -> bool:
        """Whether Raphael can be heard now: a voice service is chosen and has its key and voice.
        The assistant asks before it shows a line, to know whether to wait for the voice and to
        have the Japanese line made."""
        return self._hub.tts_ready()

    async def say(self, prefix: str, text: str, *, turn_id: int | None = None) -> None:
        """Speak a line of Raphael's (see the module), replacing what was being said. Returns when
        the sound begins, when it cannot (the voice failed), or after FIRST_AUDIO_WAIT_S, whichever
        is first; at once when the owner has Raphael's voice off or there is nothing to say."""
        self.stop()
        if not self._hub.enabled("voice"):
            self._ended(_Utterance(turn_id=turn_id))
            return
        spoken = line(prefix, text)
        if not spoken:
            self._ended(_Utterance(turn_id=turn_id))
            return
        utterance = self._start(spoken, turn_id)
        try:
            await asyncio.wait_for(utterance.ready.wait(), self._first_audio_wait_s)
        except TimeoutError:
            pass  # the answer goes out without the sound, which follows when it comes

    async def say_member(self, text: str, *, voice: str = "", turn_id: int | None = None) -> None:
        """Speak a member's reply through the configured service with an optional voice override."""
        self.stop()
        if not self._hub.enabled("voice"):
            return
        spoken = line("", text)
        if not spoken:
            return
        utterance = self._start(spoken, turn_id, voice=voice or None)
        try:
            await asyncio.wait_for(utterance.ready.wait(), self._first_audio_wait_s)
        except TimeoutError:
            pass

    def stop(self) -> None:
        """End the speech at once: the sound, a request still on its way, and the wait for it."""
        utterance, self._current = self._current, None
        if utterance is None:
            return
        utterance.stopped.set()
        utterance.ready.set()
        task = utterance.task
        if task is not None and not task.done():
            task.cancel()

    def _start(self, spoken: str, turn_id: int | None = None, *, voice: str | None = None) -> _Utterance:
        utterance = _Utterance(turn_id=turn_id, voice=voice)
        self._current = utterance
        task = asyncio.get_running_loop().create_task(self._speak(spoken, utterance))
        utterance.task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        # Also runs if a stop cancels the task before its coroutine ever starts.
        task.add_done_callback(lambda _: self._ended(utterance))
        return utterance

    def _state(self, utterance: _Utterance, state: str) -> None:
        if self._notify is not None:
            params: dict[str, Any] = {"state": state}
            if utterance.turn_id is not None:
                params["turn_id"] = utterance.turn_id
            self._notify("speech.state", params)

    def _began(self, utterance: _Utterance) -> None:
        if not utterance.finished and not utterance.playing:
            utterance.playing = True
            self._state(utterance, "playing")
        utterance.ready.set()

    def _ended(self, utterance: _Utterance) -> None:
        if not utterance.finished:
            utterance.finished = True
            self._state(utterance, "done")
        utterance.ready.set()
        if self._current is utterance:
            self._current = None

    async def _speak(self, spoken: str, utterance: _Utterance) -> None:
        """Ask for the sound and hand it to the player as it comes. Never raises for a failure of
        the world: it is logged as a kind and noted on the utterance, and the wait is ended."""
        loop = asyncio.get_running_loop()
        source: queue.Queue[bytes | None] = queue.Queue()
        playing: asyncio.Future[None] | None = None

        def started() -> None:  # on the playing thread: the first block is about to be heard
            try:
                loop.call_soon_threadsafe(self._began, utterance)
            except RuntimeError:  # the loop is gone
                pass

        try:
            sound = self._hub.speech(spoken) if utterance.voice is None else self._hub.speech(spoken, voice=utterance.voice)
            async with aclosing(sound) as chunks:
                try:
                    first = await asyncio.wait_for(anext(chunks), self._late_drop_s)
                except TimeoutError:
                    self._fail(utterance, "late")
                    return
                except StopAsyncIteration:
                    self._fail(utterance, "bad_response")
                    return
                if utterance.stopped.is_set():  # replaced or stopped while the sound was on its way
                    return
                source.put(first)
                playing = asyncio.ensure_future(
                    asyncio.to_thread(
                        self._player_now().play, source, PCM_RATE, utterance.stopped, started
                    )
                )
                playing.add_done_callback(lambda done: self._played(utterance, done))
                try:
                    async for chunk in chunks:
                        if utterance.stopped.is_set() or playing.done():
                            break
                        source.put(chunk)
                except ServiceError as error:  # the stream broke: what came is played
                    self._failed(error.kind)
                source.put(None)
            try:
                await asyncio.shield(playing)
            except Exception:  # noted by `_played`
                pass
        except ServiceError as error:
            self._fail(utterance, error.kind)
        except Exception as exc:  # a bug in a service must not reach the answer
            self._fail(utterance, type(exc).__name__)
        finally:
            source.put(None)  # lets a player that is waiting for a chunk end
            # Cancelling an asyncio thread future does not stop its device. Wait for the
            # player's actual exit before reporting silence (including its final buffer).
            if playing is not None:
                try:
                    await asyncio.shield(playing)
                except Exception:
                    pass
            self._ended(utterance)

    def _played(self, utterance: _Utterance, done: "asyncio.Future[None]") -> None:
        """The player has ended: the wait is over whatever came of it, and a device that could not
        play is a failure of the kind "playback" (logged by the type of its error)."""
        utterance.ready.set()
        if done.cancelled():
            return
        error = done.exception()
        if error is None:
            self._last_failure = None
            return
        utterance.failure = "playback"
        self._failed("playback", type(error).__name__)
        # The producer may be waiting for another network chunk forever after the
        # device has failed. Close it too, so the terminal notification is not lost.
        utterance.stopped.set()
        if utterance.task is not None and not utterance.task.done():
            utterance.task.cancel()

    def _fail(self, utterance: _Utterance, kind: str) -> None:
        utterance.failure = kind
        self._failed(kind)

    def _failed(self, kind: str, detail: str | None = None) -> None:
        if kind != self._last_failure:
            suffix = "" if detail is None else f" ({detail})"
            logger.warning("Raphael could not speak: %s%s", kind, suffix)
        self._last_failure = kind

    async def aclose(self) -> None:
        """End the speech and wait for its tasks."""
        self.stop()
        tasks = list(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- tts.* ----------------------------------------------------------------------------------

    async def _stop_rpc(self, params: dict) -> dict:
        self.stop()
        return {"ok": True}

    async def _preview(self, params: dict) -> dict:
        text = params.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > PREVIEW_MAX_CHARS:
            raise invalid_params(f"text must be 1 to {PREVIEW_MAX_CHARS} characters")
        self.stop()
        utterance = self._start(PREVIEW_LINE)
        # The sound is "playing" once its first block is about to be written; a service or a
        # device that fails ends the wait at once, and that is told.
        try:
            await asyncio.wait_for(utterance.ready.wait(), self._late_drop_s + 2.0)
        except TimeoutError:
            return {"ok": False, "kind": "timeout"}
        if utterance.failure is not None:
            return {"ok": False, "kind": utterance.failure}
        return {"ok": True}
