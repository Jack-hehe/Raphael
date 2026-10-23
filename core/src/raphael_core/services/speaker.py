"""Raphael's voice: his answers spoken aloud, one at a time, and stopped the moment the owner moves on.

`Speaker.say(prefix, text)` is called by the assistant (`agent.service`) with the line the
overlay shows, for the owner's own requests only (never a dictation, a message from a phone, a
report of the background worker, or a question waiting for a yes). The prefix (答, 告, 否, 了) is
part of his character and is spoken ("告。主人，…"); the brackets of the overlay are not. The text is
made speakable first (`speakable`): addresses and the marks of markdown are left out, whitespace is
collapsed and a long answer is cut at a sentence end, since an answer read aloud for a minute is
not an answer.

What happens, in order, all on the event loop except the playing:

1. `say` stops whatever was being said (one utterance at a time: a new one replaces the old) and
   returns at once. With no voice service chosen it does nothing more.
2. A task asks the service for the audio (`ServiceHub.speech`) as soon as it starts, in parallel
   with the answer being shown: nothing waits for it, and nothing it does can delay the answer.
3. The audio is played on a worker thread by the player (`playback`), which looks at the
   utterance's `stopped` event before each block of about 50 ms.

`stop` (the owner starts a new recording, presses Esc, asks a new question, `tts.stop`) sets the
event of the utterance and cancels its task: the sound ends within a block, and a request still on
its way is dropped. A failure (no key, no voice id, the network, an error code of the service, a
device that cannot play) is logged as a kind, once for each kind in a row, and the answer, already
on screen, stays silent: nothing is retried. The text, the voice id and the audio are never logged.

RPC methods:

- `tts.stop` -> `{ok: true}`: the speech ends at once.
- `tts.preview {text}` -> `{ok: true}` once the sound is playing, or `{ok: false, kind}` (the kinds
  of `common.ServiceError`, or "playback"): a short line spoken in the voice that is set up now,
  for the settings page to try it. The text is at most PREVIEW_MAX_CHARS characters and is spoken
  as given, without the brackets 《》 that show a line of his. It replaces whatever was being said, like an answer.
"""

import asyncio
import logging
import re
import threading
from typing import Any, Protocol

from ..protocol import invalid_params
from ..router import Router
from .common import ServiceError
from .hub import ServiceHub
from .playback import Player, SoundDevicePlayer
from .tts import Audio

logger = logging.getLogger(__name__)

# The most that is spoken of an answer: about half a minute of speech.
MAX_SPOKEN_CHARS = 400
# The most that `tts.preview` takes.
PREVIEW_MAX_CHARS = 200
# How long `tts.preview` waits after the audio is in hand to see whether the device can play it.
PLAYBACK_GRACE_S = 0.4

_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# The marks of markdown that are read as symbols if left in: emphasis, code, headings, quotes.
_MARKDOWN = re.compile(r"[*_`#>~|]+")
_BULLET = re.compile(r"^\s*(?:[-+•]|\d+[.)])\s+", re.MULTILINE)
_SENTENCE_END = "。！？!?.\n"


class Speaking(Protocol):
    """What the assistant needs of a voice: say a line, stop."""

    def say(self, prefix: str, text: str) -> None: ...

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


class Speaker:
    """The voice of Raphael: `hub` makes the audio, `player` plays it (the sound device by
    default; tests give a fake)."""

    def __init__(self, hub: ServiceHub, player: Player | None = None) -> None:
        self._hub = hub
        self._player = player
        self._stopped: threading.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        # The kind of the latest failure, so that a service that is down is logged once in a row.
        self._last_failure: str | None = None

    def register(self, router: Router) -> None:
        router.register("tts.stop", self._stop_rpc)
        router.register("tts.preview", self._preview)

    def _player_now(self) -> Player:
        """The player, made on first use: PortAudio is not needed until something is said."""
        if self._player is None:
            self._player = SoundDevicePlayer()
        return self._player

    # --- saying and stopping --------------------------------------------------------------------

    def say(self, prefix: str, text: str) -> None:
        """Speak a line of Raphael's (see the module), replacing what was being said. Returns at
        once; does nothing when no voice service is chosen or there is nothing to say."""
        self.stop()
        if self._hub.tts_service() == "none":
            return
        spoken = line(prefix, text)
        if not spoken:
            return
        self._start(spoken)

    def stop(self) -> None:
        """End the speech at once: the sound, and a request still on its way."""
        stopped, self._stopped = self._stopped, None
        task, self._task = self._task, None
        if stopped is not None:
            stopped.set()
        if task is not None and not task.done():
            task.cancel()

    def _start(self, spoken: str) -> asyncio.Task[None] | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        stopped = threading.Event()
        self._stopped = stopped
        task = loop.create_task(self._speak(spoken, stopped))
        self._task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _speak(self, spoken: str, stopped: threading.Event) -> None:
        try:
            audio = await self._hub.speech(spoken)
        except ServiceError as error:
            self._failed(error.kind)
            return
        except Exception as exc:  # a bug in a service must not reach the answer
            self._failed(type(exc).__name__)
            return
        if stopped.is_set():  # replaced or stopped while the audio was on its way
            return
        await self._play(audio, stopped)

    async def _play(self, audio: Audio, stopped: threading.Event) -> str | None:
        """Play the audio on a worker thread; the kind of the failure, or None."""
        try:
            await asyncio.to_thread(self._player_now().play, audio.data, stopped)
        except Exception as exc:  # no device, a file that cannot be decoded
            self._failed("playback", type(exc).__name__)
            return "playback"
        self._last_failure = None
        return None

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
        stopped = threading.Event()
        self._stopped = stopped
        try:
            audio = await self._hub.speech(text.replace("《", "").replace("》", "").strip())
        except ServiceError as error:
            self._failed(error.kind)
            return {"ok": False, "kind": error.kind}
        except Exception as exc:
            self._failed(type(exc).__name__)
            return {"ok": False, "kind": "error"}
        if stopped.is_set():  # something else was said, or stopped, meanwhile
            return {"ok": True}
        task = asyncio.ensure_future(self._play(audio, stopped))
        self._task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        # The sound is "playing" once the audio is in hand; a device that cannot play it fails
        # within moments, and that is told, while a sound that plays on is not waited for.
        await asyncio.wait({task}, timeout=PLAYBACK_GRACE_S)
        if task.done() and not task.cancelled() and task.result() is not None:
            return {"ok": False, "kind": "playback"}
        return {"ok": True}
