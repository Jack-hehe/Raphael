"""The switchboard: what is the same whatever messaging app a message comes from.

A channel (channels/) hands over each message. Only private chats with an allowlisted user reach
the assistant. Anyone else gets one short reply with a pairing code; the owner approves that code
in the app (never from a message), which adds the sender to the allowlist. The switchboard answers
the commands, keeps one question at a time per chat, turns a voice message into text and sends the
answer back to the chat it came from.

What goes in the log: counts, kinds and a short hash of a chat id. Never a message, a transcript, a
name or a secret.
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable

from .channels.base import Attachment, Channel, ChannelError, Incoming
from .pairing import Pairing
from .words import heard, words

logger = logging.getLogger(__name__)

MAX_INCOMING_CHARS = 4000
# A voice message larger than this is declined without being downloaded. It is the most a bot may
# download from Telegram (getFile), so no message the bot could fetch is turned away for its size;
# the bound only keeps what a message holds in memory in check. A voice message has no limit of
# length: one of any length is heard whole.
MAX_VOICE_BYTES = 20 * 1024 * 1024

# ask(session_id, text, language, source) -> the text to send back. stop(session_id) ends what the
# chat has under way and says whether there was something. transcribe(audio, language) -> text.
Ask = Callable[[str, str, str, str], Awaitable[str]]
Stop = Callable[[str], Awaitable[bool]]
Transcribe = Callable[[bytes, str], Awaitable[str]]


class VoiceTooLarge(Exception):
    """A voice message that would take more memory to hear than a message may: a file larger than
    `MAX_VOICE_BYTES`, or audio of more than two channels."""


def session_id(kind: str, chat: str) -> str:
    """The id of the conversation with a chat, stable across runs: the app's history lists these
    turns beside the ones that were spoken."""
    return f"{kind}:{chat}"


def split_session(session: str) -> tuple[str, str] | None:
    """(kind, chat) of a session id made by `session_id`, or None for any other."""
    kind, colon, chat = session.partition(":")
    return (kind, chat) if colon and kind and chat else None


def chat_hash(chat: str) -> str:
    """What the log says about a chat."""
    return hashlib.sha256(chat.encode()).hexdigest()[:8]


class _Say(Exception):
    """Stop what is under way and send this text instead."""


class Switchboard:
    """Routes the private messages of one channel.

    `is_allowed(user)` and `chat_allowed(chat)` are read at each message, so removing a user takes
    effect at once. `language()` is the language Raphael answers in. Without `transcribe`, a voice
    message is declined.
    """

    def __init__(
        self,
        channel: Channel,
        *,
        ask: Ask,
        stop: Stop,
        is_allowed: Callable[[str], bool],
        chat_allowed: Callable[[str], bool],
        language: Callable[[], str],
        transcribe: Transcribe | None = None,
        pairing: Pairing | None = None,
    ) -> None:
        self._channel = channel
        self._ask = ask
        self._stop = stop
        self._is_allowed = is_allowed
        self._chat_allowed = chat_allowed
        self._language = language
        self._transcribe = transcribe
        self.pairing = pairing if pairing is not None else Pairing()
        self._tasks: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def error(self) -> str | None:
        return self._channel.error

    async def run(self) -> None:
        """Receive until cancelled, or until the channel gives up."""
        try:
            await self._channel.run(self._deliver)
        finally:
            await self.aclose()

    def _deliver(self, message: Incoming) -> None:
        task = asyncio.ensure_future(self._handle(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- one message ---

    async def _handle(self, message: Incoming) -> None:
        kind = self._channel.kind
        try:
            await self._route(message)
        except asyncio.CancelledError:
            raise
        except ChannelError as error:
            logger.warning("%s: could not answer (%s)", kind, error.kind)
        except Exception as exc:  # one bad message must not end the others
            logger.warning("%s: a message failed (%s)", kind, type(exc).__name__)

    async def _route(self, message: Incoming) -> None:
        if not message.private:
            return  # groups, channels and other bots are not part of this
        language = self._language()
        if not self._is_allowed(message.user):
            await self._meet_stranger(message, language)
            return
        text = message.text
        if text is None:
            voice = next((a for a in message.attachments if a.kind == "voice"), None)
            if voice is not None and self._transcribe is not None:
                await self._converse(message.chat, language, voice=voice)
            elif message.attachments:
                logger.info(
                    "%s: an attachment was declined, chat=%s",
                    self._channel.kind,
                    chat_hash(message.chat),
                )
                await self._reply(message.chat, words(language, "voice"))
            return
        if text.startswith("/"):
            await self._command(message.chat, text, language)
            return
        await self._converse(message.chat, language, text=text[:MAX_INCOMING_CHARS])

    async def _meet_stranger(self, message: Incoming, language: str) -> None:
        code = self.pairing.issue(message.user, message.chat, message.name)
        logger.info(
            "%s: a stranger %s, chat=%s",
            self._channel.kind,
            "was given a code" if code else "was ignored",
            chat_hash(message.chat),
        )
        if code is not None:
            await self._reply(message.chat, words(language, "pair", code=code))

    async def _command(self, chat: str, text: str, language: str) -> None:
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command == "/stop":
            stopped = await self._stop(session_id(self._channel.kind, chat))
            await self._reply(chat, words(language, "stopped" if stopped else "nothing"))
        else:  # /start, /help and anything else the apps offer
            await self._reply(chat, words(language, "intro"))

    async def _converse(
        self,
        chat: str,
        language: str,
        *,
        text: str | None = None,
        voice: Attachment | None = None,
    ) -> None:
        if voice is None and not (text or "").strip():
            return
        # One question at a time for a chat: a second would cancel the first in the assistant.
        lock = self._locks.setdefault(chat, asyncio.Lock())
        async with lock:
            prefix = ""
            try:
                async with self._channel.busy(chat):
                    if voice is not None:
                        text = await self._hear(voice, language)
                        prefix = heard(language, text) + "\n\n"
                    answer = await self._ask(
                        session_id(self._channel.kind, chat), text or "", language, self._channel.kind
                    )
            except asyncio.CancelledError:
                raise
            except _Say as say:
                prefix, answer = "", str(say)
            except Exception as exc:
                logger.warning("%s: the assistant failed (%s)", self._channel.kind, type(exc).__name__)
                prefix, answer = "", words(language, "failed")
            await self._reply(chat, prefix + answer)

    async def _hear(self, voice: Attachment, language: str) -> str:
        """The words of a voice message, however long it is. Declined before it is downloaded when
        it is too large; the audio is held in memory only, for as long as this takes."""
        assert self._transcribe is not None
        too_large = words(language, "voice_large", mb=str(MAX_VOICE_BYTES // (1024 * 1024)))
        if (voice.size or 0) > MAX_VOICE_BYTES:
            raise _Say(too_large)
        data = await self._channel.fetch(voice, MAX_VOICE_BYTES)
        try:
            transcript = await self._transcribe(data, language)
        except VoiceTooLarge:
            raise _Say(too_large) from None
        finally:
            del data
        if not transcript.strip():
            raise _Say(words(language, "voice_empty"))
        return transcript.strip()

    async def _reply(self, chat: str, text: str) -> None:
        await self._channel.send(chat, text)

    # --- what the app and the worker ask for ---

    async def tell(self, chat: str, text: str) -> None:
        """Send `text` to a chat that is allowed (a background report). A chat that is not
        allowed, or a failure, sends nothing."""
        if not self._chat_allowed(chat):
            return
        try:
            await self._reply(chat, text)
        except ChannelError as error:
            logger.warning("%s: could not send a report (%s)", self._channel.kind, error.kind)

    async def welcome(self, chat: str) -> None:
        """Tell a user whose code was just approved that they are in."""
        try:
            await self._reply(chat, words(self._language(), "paired"))
        except ChannelError as error:
            logger.warning("%s: could not confirm a pairing (%s)", self._channel.kind, error.kind)
