"""Telegram as a channel: a bot that Raphael polls.

The owner makes a bot with BotFather and gives Raphael its token. Raphael polls the Bot API for
messages (`getUpdates` with a long timeout), so no public address is needed.

What goes in the log: counts and kinds. Never a message, a name, a token, or the address of a
request (it contains the token): this module silences the log lines of httpx that would show one.
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from .base import Attachment, ChannelError, ChannelType, Deliver, Identity, Incoming

logger = logging.getLogger(__name__)

KIND = "telegram"
API_ROOT = "https://api.telegram.org"
# How long Telegram holds a poll open when nothing arrives, and how long the HTTP call may take
# beyond that.
POLL_SECONDS = 30
REQUEST_SLACK_SECONDS = 15
MAX_MESSAGE_UNITS = 4096
TYPING_REPEAT_SECONDS = 4.0
MAX_BACKOFF_SECONDS = 60.0
_TOKEN_SHAPE = re.compile(r"\d{3,}:[A-Za-z0-9_-]{20,}")
# Voice messages are Ogg/Opus; an audio file counts as one only when it is.
_VOICE_MIMES = ("audio/ogg", "audio/opus", "application/ogg")


class _KeepTokensOutOfTheLog(logging.Filter):
    """httpx logs the address of each request at INFO, and the address of a Bot API call carries
    the token in its path."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "api.telegram.org" not in record.getMessage()


logging.getLogger("httpx").addFilter(_KeepTokensOutOfTheLog())


def looks_like_token(value: str) -> bool:
    return _TOKEN_SHAPE.fullmatch(value) is not None


def _units(text: str) -> int:
    """Telegram counts the length of a message in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def split_message(text: str, limit: int = MAX_MESSAGE_UNITS) -> list[str]:
    """`text` in pieces Telegram accepts, cut at a line break or a space where one is near."""
    pieces: list[str] = []
    rest = text.strip("\n")
    while rest:
        if _units(rest) <= limit:
            pieces.append(rest)
            break
        cut = min(len(rest), limit)
        while _units(rest[:cut]) > limit:
            cut -= 1
        window = rest[:cut]
        for mark in ("\n\n", "\n", " "):
            at = window.rfind(mark)
            if at >= cut // 2:
                cut = at + len(mark)
                break
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    return [piece for piece in pieces if piece.strip()]


# --- the Bot API -----------------------------------------------------------------------------


class TelegramApi:
    """The few calls of the Bot API that Raphael makes."""

    def __init__(self, token: str, http: httpx.AsyncClient) -> None:
        self._token = token
        self._http = http

    async def _call(self, method: str, payload: dict | None = None, *, wait: float = 0) -> Any:
        url = f"{API_ROOT}/bot{self._token}/{method}"
        try:
            response = await self._http.post(
                url, json=payload or {}, timeout=httpx.Timeout(10.0 + wait, connect=10.0)
            )
        except httpx.HTTPError:
            # The exception text holds the address, and the address holds the token.
            raise ChannelError("network") from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("ok") is True:
            return body.get("result")
        code = response.status_code
        parameters = body.get("parameters") if isinstance(body, dict) else None
        retry = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if code in (401, 404):  # 404: a token that is not one
            raise ChannelError("unauthorized")
        if code == 409:
            raise ChannelError("conflict")
        if code == 429:
            raise ChannelError("rate_limited", float(retry) if isinstance(retry, int) else 5.0)
        if code >= 500:
            raise ChannelError("network")
        raise ChannelError("bad_request")

    async def get_me(self) -> dict:
        me = await self._call("getMe")
        if not isinstance(me, dict) or type(me.get("id")) is not int:
            raise ChannelError("bad_request")
        return me

    async def get_updates(self, offset: int | None, timeout: int = POLL_SECONDS) -> list[dict]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload, wait=timeout + REQUEST_SLACK_SECONDS)
        return [u for u in result if isinstance(u, dict)] if isinstance(result, list) else []

    async def send_message(self, chat_id: int, text: str) -> None:
        await self._call("sendMessage", {"chat_id": chat_id, "text": text})

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def download(self, file_id: str, limit: int) -> bytes:
        """A file the bot may fetch (up to 20 MB), held in memory. Nothing is written to disk."""
        info = await self._call("getFile", {"file_id": file_id})
        path = info.get("file_path") if isinstance(info, dict) else None
        size = info.get("file_size") if isinstance(info, dict) else None
        if not isinstance(path, str) or (type(size) is int and size > limit):
            raise ChannelError("bad_request")
        url = f"{API_ROOT}/file/bot{self._token}/{path}"
        data = bytearray()
        try:
            async with self._http.stream("GET", url, timeout=httpx.Timeout(60.0, connect=10.0)) as response:
                if response.status_code != 200:
                    raise ChannelError("network" if response.status_code >= 500 else "bad_request")
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if len(data) > limit:
                        raise ChannelError("bad_request")
        except httpx.HTTPError:
            raise ChannelError("network") from None
        return bytes(data)


# --- the channel -----------------------------------------------------------------------------


def _incoming(update: dict) -> Incoming | None:
    """A message of the Bot API as the switchboard sees it, or None for an update that is no
    message. Bots are left out here; the switchboard drops what is not a private chat."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat, sender = message.get("chat"), message.get("from")
    if not isinstance(chat, dict) or not isinstance(sender, dict) or sender.get("is_bot") is True:
        return None
    user_id, chat_id = sender.get("id"), chat.get("id")
    if type(user_id) is not int or type(chat_id) is not int:
        return None
    name = " ".join(
        part for part in (sender.get("first_name"), sender.get("last_name")) if isinstance(part, str)
    ) or (sender.get("username") if isinstance(sender.get("username"), str) else "")
    text = message.get("text")
    attachments: list[Attachment] = []
    for field_name in ("voice", "audio"):
        item = message.get(field_name)
        if not isinstance(item, dict) or not isinstance(item.get("file_id"), str):
            continue
        mime = item.get("mime_type") if isinstance(item.get("mime_type"), str) else None
        is_voice = field_name == "voice" or (mime is not None and mime.lower() in _VOICE_MIMES)
        size, duration = item.get("file_size"), item.get("duration")
        attachments.append(
            Attachment(
                "voice" if is_voice else "other",
                item["file_id"],
                mime,
                size if type(size) is int else None,
                float(duration) if type(duration) in (int, float) else None,
            )
        )
    return Incoming(
        chat=str(chat_id),
        user=str(user_id),
        name=name,
        # A private chat has the same id as its user.
        private=chat.get("type") == "private" and user_id == chat_id,
        text=text if isinstance(text, str) else None,
        attachments=attachments,
    )


class TelegramChannel:
    kind = KIND

    def __init__(
        self,
        api: TelegramApi,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api = api
        self._sleep = sleep
        self._offset: int | None = None
        self.error: str | None = None  # why the last poll failed; None while it works

    async def run(self, deliver: Deliver) -> None:
        """Poll until cancelled, or until Telegram says the token is no good."""
        delay = 1.0
        logger.info("Telegram polling started")
        while True:
            try:
                updates = await self._api.get_updates(self._offset)
            except ChannelError as error:
                self.error = error.kind
                if error.kind == "unauthorized":
                    logger.warning("Telegram refused the token; polling stopped")
                    return
                wait = max(delay, error.retry_after)
                logger.warning("Telegram poll failed: kind=%s retry_in=%.0fs", error.kind, wait)
                await self._sleep(wait)
                delay = min(delay * 2, MAX_BACKOFF_SECONDS)
                continue
            self.error = None
            delay = 1.0
            if updates:
                logger.info("Telegram: %d update(s)", len(updates))
            for update in updates:
                update_id = update.get("update_id")
                if type(update_id) is int:
                    self._offset = update_id + 1
                message = _incoming(update)
                if message is not None:
                    deliver(message)

    async def send(self, chat: str, text: str) -> None:
        pieces = split_message(text)
        for piece in pieces:
            await self._api.send_message(int(chat), piece)
        logger.info("Telegram: sent %d message(s)", len(pieces))

    @contextlib.asynccontextmanager
    async def busy(self, chat: str) -> AsyncIterator[None]:
        typing = asyncio.ensure_future(self._typing(int(chat)))
        try:
            yield
        finally:
            typing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing

    async def _typing(self, chat_id: int) -> None:
        while True:
            try:
                await self._api.send_chat_action(chat_id)
            except ChannelError:
                pass  # the indicator is a courtesy
            await asyncio.sleep(TYPING_REPEAT_SECONDS)

    async def fetch(self, attachment: Attachment, limit: int) -> bytes:
        return await self._api.download(attachment.ref, limit)


# --- how the service sets Telegram up ----------------------------------------------------------


def _read_secrets(params: dict) -> dict[str, str] | None:
    token = params.get("token")
    if not isinstance(token, str):
        raise ValueError("token must be a string")
    if not looks_like_token(token.strip()):
        return None
    return {"bot_token": token.strip()}


async def _check(secrets: dict[str, str], http: httpx.AsyncClient) -> Identity:
    me = await TelegramApi(secrets["bot_token"], http).get_me()
    username = me.get("username")
    return Identity(str(me["id"]), username if isinstance(username, str) else None)


def _make(secrets: dict[str, str], http: httpx.AsyncClient) -> TelegramChannel:
    return TelegramChannel(TelegramApi(secrets["bot_token"], http))


TELEGRAM = ChannelType(
    kind=KIND, secrets=("bot_token",), read_secrets=_read_secrets, check=_check, make=_make
)
