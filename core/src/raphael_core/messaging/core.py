"""The switchboard: what is the same whatever messaging app a message comes from.

A channel (channels/) hands over each message. Only private chats with an allowlisted user reach
the assistant. Anyone else gets one short reply with a pairing code; the owner approves that code
in the app (never from a message), which adds the sender to the allowlist. The switchboard answers
the commands, keeps one question at a time per chat, turns a voice message into text and sends the
answer back to the chat it came from.

An image or a PDF sent to the bot is a request too. It is fetched into memory (declined unasked when
the app says it is larger than `services.attachments` allows), checked by
`services.attachments.checked`, and handed to the assistant with the caption as the request's text;
without a caption the text is empty and the assistant takes it as "look at this". An album arrives
as one message per photo; the switchboard waits ALBUM_WAIT_SECONDS after the latest of them and asks
once, with at most MAX_ATTACHMENTS files. The bytes live for that one request and are never written
anywhere.

What goes in the log: counts, kinds and a short hash of a chat id. Never a message, a caption, a
transcript, a name, a file's name or id, or a secret.
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from ..services.attachments import (
    MAX_ATTACHMENTS,
    MAX_IMAGE_BYTES,
    MAX_PDF_BYTES,
    AttachmentError,
    checked,
)
from ..services.attachments import Attachment as File
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
# The kinds of attachment that are handed to the assistant as files.
FILE_KINDS = ("image", "pdf")
# How long after the latest message of an album the album is taken as whole. The apps send the
# messages of one album within a fraction of a second.
ALBUM_WAIT_SECONDS = 1.5
# The most messages of one album that are kept track of (Telegram's albums have up to ten).
MAX_ALBUM_ITEMS = 10
_MB = 1024 * 1024


class Ask(Protocol):
    """How a switchboard reaches the assistant: the text to send back for a request of a chat.
    `attachments` are the files the request came with, already checked; the switchboard names
    them only when there are some."""

    def __call__(
        self,
        session_id: str,
        text: str,
        language: str,
        source: str,
        /,
        *,
        attachments: Sequence[File] = (),
    ) -> Awaitable[str]: ...


# stop(session_id) ends what the chat has under way and says whether there was something.
# transcribe(audio, language) -> text.
Stop = Callable[[str], Awaitable[bool]]
Transcribe = Callable[[bytes, str], Awaitable[str]]


@dataclass
class _Album:
    """The messages of one album received so far: their attachments, the first caption among
    them (the apps put an album's caption on one of its photos), and the timer that ends it."""

    language: str
    attachments: list[Attachment] = field(default_factory=list)
    caption: str | None = None
    timer: "asyncio.Task[None] | None" = None


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
    message is declined. `album_wait` is how long an album is waited for after its latest
    message (shorter in tests).
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
        album_wait: float = ALBUM_WAIT_SECONDS,
    ) -> None:
        self._channel = channel
        self._ask = ask
        self._stop = stop
        self._is_allowed = is_allowed
        self._chat_allowed = chat_allowed
        self._language = language
        self._transcribe = transcribe
        self._album_wait = album_wait
        self.pairing = pairing if pairing is not None else Pairing()
        self._tasks: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        # (chat, album id) -> the album being received.
        self._albums: dict[tuple[str, str], _Album] = {}

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
        self._spawn(lambda: self._route(message))

    def _spawn(self, work: Callable[[], Awaitable[None]]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(self._handle(work))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def aclose(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._albums.clear()

    # --- one message ---

    async def _handle(self, work: Callable[[], Awaitable[None]]) -> None:
        """Run what one message (or one album) needs, so that a failure ends only that."""
        kind = self._channel.kind
        try:
            await work()
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
            if message.group is not None and message.attachments:
                self._gather(message, language)
                return
            voice = next((a for a in message.attachments if a.kind == "voice"), None)
            if voice is not None and self._transcribe is not None:
                await self._converse(message.chat, language, voice=voice)
            else:
                await self._take_files(
                    message.chat, language, message.attachments, message.caption
                )
            return
        if text.startswith("/"):
            await self._command(message.chat, text, language)
            return
        await self._converse(message.chat, language, text=text[:MAX_INCOMING_CHARS])

    async def _take_files(
        self, chat: str, language: str, attachments: list[Attachment], caption: str | None
    ) -> None:
        """A message (or an album) without text: its images and PDFs are asked about, with the
        caption as the request; one with none of them gets the reply that says what is read."""
        files = [a for a in attachments if a.kind in FILE_KINDS]
        if files:
            await self._converse(
                chat, language, text=(caption or "")[:MAX_INCOMING_CHARS], files=files
            )
        elif attachments:
            logger.info(
                "%s: an attachment was declined, chat=%s", self._channel.kind, chat_hash(chat)
            )
            await self._reply(chat, words(language, "unsupported"))

    def _gather(self, message: Incoming, language: str) -> None:
        """Keep a message of an album, and wait anew for the rest of it."""
        assert message.group is not None
        key = (message.chat, message.group)
        album = self._albums.get(key)
        if album is None:
            album = self._albums[key] = _Album(language)
        room = MAX_ALBUM_ITEMS - len(album.attachments)
        album.attachments.extend(message.attachments[: max(room, 0)])
        if album.caption is None and message.caption:
            album.caption = message.caption
        if album.timer is not None:
            album.timer.cancel()
        album.timer = self._spawn(lambda: self._end_album(key))

    async def _end_album(self, key: tuple[str, str]) -> None:
        """Once no message of the album came for a while, ask about the whole of it."""
        await asyncio.sleep(self._album_wait)
        album = self._albums.pop(key, None)
        if album is not None:
            await self._take_files(key[0], album.language, album.attachments, album.caption)

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
        files: Sequence[Attachment] = (),
    ) -> None:
        if voice is None and not files and not (text or "").strip():
            return
        # One question at a time for a chat: a second would cancel the first in the assistant.
        lock = self._locks.setdefault(chat, asyncio.Lock())
        async with lock:
            prefix = ""
            try:
                async with self._channel.busy(chat):
                    session, source = session_id(self._channel.kind, chat), self._channel.kind
                    if voice is not None:
                        text = await self._hear(voice, language)
                        prefix = heard(language, text) + "\n\n"
                    if files:
                        if len(files) > MAX_ATTACHMENTS:
                            count = str(MAX_ATTACHMENTS)
                            prefix = words(language, "files_first", count=count) + "\n\n"
                        taken = await self._fetch_files(files[:MAX_ATTACHMENTS], language)
                        logger.info(
                            "%s: a request with %d file(s), chat=%s",
                            source,
                            len(taken),
                            chat_hash(chat),
                        )
                        try:
                            answer = await self._ask(
                                session, text or "", language, source, attachments=taken
                            )
                        finally:
                            del taken
                    else:
                        answer = await self._ask(session, text or "", language, source)
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

    async def _fetch_files(self, files: Sequence[Attachment], language: str) -> list[File]:
        """The images and PDFs of a request, fetched into memory and checked. None is downloaded
        when the app says that any of them is larger than its kind may be; a file that is too
        large or not what its type says ends the request with the reply that says so."""
        limits = [MAX_PDF_BYTES if f.kind == "pdf" else MAX_IMAGE_BYTES for f in files]

        def too_large(limit: int) -> _Say:
            return _Say(words(language, "file_large", mb=str(limit // _MB)))

        for item, limit in zip(files, limits, strict=True):
            if (item.size or 0) > limit:
                raise too_large(limit)
        taken: list[File] = []
        for item, limit in zip(files, limits, strict=True):
            try:
                data = await self._channel.fetch(item, limit)
            except ChannelError as error:
                # What a fetch refuses is mostly a file larger than the limit (or than the app lets
                # a bot download); a failure of the network is the one that is not.
                if error.kind == "bad_request":
                    raise too_large(limit) from None
                raise
            try:
                taken.append(checked(item.name or "file", item.mime or "", data, max_bytes=limit))
            except AttachmentError as error:
                if error.kind == "size":
                    raise too_large(limit) from None
                raise _Say(words(language, "unsupported")) from None
            finally:
                del data
        return taken

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
