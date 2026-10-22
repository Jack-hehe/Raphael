"""Talking to Raphael through a Telegram bot.

The owner makes a bot with BotFather and gives Raphael its token. Raphael polls the Bot API for
messages (`getUpdates` with a long timeout), so no public address is needed. Only private chats
with an allowlisted user reach the assistant. Anyone else gets one short reply with a pairing code;
the owner approves that code in the app (never from Telegram), which adds the sender to the
allowlist.

What goes in the log: counts, kinds and a short hash of a chat id. Never a message, a name, a
token, or the address of a request (it contains the token): this module silences the log lines of
httpx that would show one.
"""

import asyncio
import contextlib
import hashlib
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
# How long Telegram holds a poll open when nothing arrives, and how long the HTTP call may take
# beyond that.
POLL_SECONDS = 30
REQUEST_SLACK_SECONDS = 15
MAX_MESSAGE_UNITS = 4096
CODE_LENGTH = 6
CODE_TTL_SECONDS = 600.0
MAX_PENDING = 10
# Letters and digits that cannot be mistaken for each other when read off a phone.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TYPING_REPEAT_SECONDS = 4.0
MAX_BACKOFF_SECONDS = 60.0
SESSION_PREFIX = "telegram:"
MAX_INCOMING_CHARS = 4000
_TOKEN_SHAPE = re.compile(r"\d{3,}:[A-Za-z0-9_-]{20,}")
_NAME_CHARS = 60


class _KeepTokensOutOfTheLog(logging.Filter):
    """httpx logs the address of each request at INFO, and the address of a Bot API call carries
    the token in its path."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "api.telegram.org" not in record.getMessage()


logging.getLogger("httpx").addFilter(_KeepTokensOutOfTheLog())


def looks_like_token(value: str) -> bool:
    return _TOKEN_SHAPE.fullmatch(value) is not None


def session_id(chat_id: int) -> str:
    """The id of the conversation with a chat, stable across runs: the app's history lists these
    turns beside the ones that were spoken."""
    return f"{SESSION_PREFIX}{chat_id}"


def chat_hash(chat_id: int) -> str:
    """What the log says about a chat."""
    return hashlib.sha256(str(chat_id).encode()).hexdigest()[:8]


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


def compose(prefix: str, text: str, detail: str | None) -> str:
    """The answer as one message: the line of the overlay and, when there is more, the longer text."""
    line = f"{prefix}{text}".strip()
    if detail and detail.strip() and detail.strip() != text.strip():
        return f"{line}\n\n{detail.strip()}" if line else detail.strip()
    return line


# --- the Bot API -----------------------------------------------------------------------------


class TelegramError(Exception):
    """A call the Bot API refused or that did not get there. `kind` is one of: unauthorized (the
    token is wrong or revoked), conflict (another poller uses the bot), rate_limited, network,
    bad_request. The message never holds the token or anything sent."""

    def __init__(self, kind: str, retry_after: float = 0.0) -> None:
        super().__init__(kind)
        self.kind = kind
        self.retry_after = retry_after


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
            raise TelegramError("network") from None
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
            raise TelegramError("unauthorized")
        if code == 409:
            raise TelegramError("conflict")
        if code == 429:
            raise TelegramError("rate_limited", float(retry) if isinstance(retry, int) else 5.0)
        if code >= 500:
            raise TelegramError("network")
        raise TelegramError("bad_request")

    async def get_me(self) -> dict:
        me = await self._call("getMe")
        if not isinstance(me, dict) or type(me.get("id")) is not int:
            raise TelegramError("bad_request")
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


# --- pairing ---------------------------------------------------------------------------------


@dataclass
class PendingPairing:
    code: str
    user_id: int
    chat_id: int
    name: str
    expires_at: float


class Pairing:
    """The codes that strangers were given and the owner has not yet approved. They live in memory
    only: a restart forgets them, and the stranger asks again."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._by_code: dict[str, PendingPairing] = {}

    def _sweep(self) -> None:
        now = self._clock()
        for code in [c for c, p in self._by_code.items() if p.expires_at <= now]:
            del self._by_code[code]

    def issue(self, user_id: int, chat_id: int, name: str) -> str | None:
        """A new code for a stranger, or None when one was already given to that user (a code is
        told once, not at every message) or too many are waiting."""
        self._sweep()
        if any(p.user_id == user_id for p in self._by_code.values()):
            return None
        if len(self._by_code) >= MAX_PENDING:
            return None
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._by_code:
                break
        self._by_code[code] = PendingPairing(
            code, user_id, chat_id, name[:_NAME_CHARS], self._clock() + CODE_TTL_SECONDS
        )
        return code

    def pending(self) -> list[tuple[PendingPairing, float]]:
        """Each waiting code with the seconds it has left, oldest first."""
        self._sweep()
        now = self._clock()
        return [(p, p.expires_at - now) for p in self._by_code.values()]

    def take(self, code: str) -> PendingPairing | None:
        """Remove and return the pairing a code stands for."""
        self._sweep()
        return self._by_code.pop(code.strip().upper(), None)

    def clear(self) -> None:
        self._by_code.clear()


# --- the words -------------------------------------------------------------------------------

_WORDS: dict[str, dict[str, str]] = {
    "zh-TW": {
        "intro": "我是拉斐爾。直接傳訊息給我，我會像在電腦前一樣回答你。傳 /stop 可以中斷正在跑的背景任務。",
        "pair": "這個機器人是私人的。請主人在電腦的拉斐爾裡核准配對碼 {code}（10 分鐘內有效）。",
        "paired": "已連接。現在可以傳訊息給我了。",
        "voice": "目前只能處理文字訊息。",
        "stopped": "已中斷。",
        "nothing": "目前沒有在跑的任務。",
        "confirm": "（這需要你的確認，請到電腦上回答。）",
        "failed": "這次沒有成功，請再試一次。",
    },
    "en": {
        "intro": "I'm Raphael. Send me a message and I'll answer as I would at your computer. Send /stop to interrupt a background task.",
        "pair": "This bot is private. Ask the owner to approve the code {code} in Raphael on the computer (valid for 10 minutes).",
        "paired": "Connected. You can message me now.",
        "voice": "I can only read text messages for now.",
        "stopped": "Stopped.",
        "nothing": "Nothing is running.",
        "confirm": "(This needs your confirmation. Please answer at the computer.)",
        "failed": "That did not work. Please try again.",
    },
    "ja": {
        "intro": "ラファエルです。メッセージを送ってください。パソコンの前と同じように答えます。/stop でバックグラウンドの作業を止められます。",
        "pair": "このボットは非公開です。パソコンのラファエルでコード {code} を承認してください（10 分間有効）。",
        "paired": "接続しました。メッセージを送ってください。",
        "voice": "今はテキストメッセージだけ読めます。",
        "stopped": "止めました。",
        "nothing": "実行中の作業はありません。",
        "confirm": "（確認が必要です。パソコンで答えてください。）",
        "failed": "うまくいきませんでした。もう一度お試しください。",
    },
}


def words(language: str, key: str, **values: str) -> str:
    table = _WORDS.get(language, _WORDS["en"])
    return table[key].format(**values)


# --- the gateway -----------------------------------------------------------------------------

Ask = Callable[[str, str, str], Awaitable[str]]
Stop = Callable[[str], Awaitable[bool]]


class TelegramGateway:
    """Polls one bot and routes its private messages.

    `ask(session_id, text, language)` answers a message as the assistant would a spoken question
    and returns the text to send back; `stop(session_id)` ends the background task and says
    whether there was one. `is_allowed(user_id)` is read at each message, so removing a user takes
    effect at once. `language()` is the language Raphael answers in.
    """

    def __init__(
        self,
        api: TelegramApi,
        *,
        ask: Ask,
        stop: Stop,
        is_allowed: Callable[[int], bool],
        language: Callable[[], str],
        pairing: Pairing | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api = api
        self._ask = ask
        self._stop = stop
        self._is_allowed = is_allowed
        self._language = language
        self.pairing = pairing if pairing is not None else Pairing()
        self._sleep = sleep
        self._offset: int | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._locks: dict[int, asyncio.Lock] = {}
        self.error: str | None = None  # why the last poll failed; None while it works

    # --- polling ---

    async def run(self) -> None:
        """Poll until cancelled, or until Telegram says the token is no good."""
        delay = 1.0
        logger.info("Telegram polling started")
        try:
            while True:
                try:
                    updates = await self._api.get_updates(self._offset)
                except TelegramError as error:
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
                    self._spawn(self._handle(update))
        finally:
            await self.aclose()

    def _spawn(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- one message ---

    async def _handle(self, update: dict) -> None:
        try:
            await self._route(update)
        except asyncio.CancelledError:
            raise
        except TelegramError as error:
            logger.warning("Telegram: could not answer (%s)", error.kind)
        except Exception as exc:  # one bad message must not end the others
            logger.warning("Telegram: a message failed (%s)", type(exc).__name__)

    async def _route(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat, sender = message.get("chat"), message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        if chat.get("type") != "private" or sender.get("is_bot") is True:
            return  # groups, channels and other bots are not part of this
        user_id, chat_id = sender.get("id"), chat.get("id")
        if type(user_id) is not int or type(chat_id) is not int or user_id != chat_id:
            return
        language = self._language()
        if not self._is_allowed(user_id):
            await self._meet_stranger(user_id, chat_id, sender, language)
            return
        text = message.get("text")
        if not isinstance(text, str):
            if "voice" in message or "audio" in message:
                logger.info("Telegram: a voice message was declined, chat=%s", chat_hash(chat_id))
                await self._reply(chat_id, words(language, "voice"))
            return
        if text.startswith("/"):
            await self._command(chat_id, text, language)
            return
        await self._converse(chat_id, text[:MAX_INCOMING_CHARS], language)

    async def _meet_stranger(
        self, user_id: int, chat_id: int, sender: dict, language: str
    ) -> None:
        name = " ".join(
            part for part in (sender.get("first_name"), sender.get("last_name")) if isinstance(part, str)
        ) or (sender.get("username") if isinstance(sender.get("username"), str) else "")
        code = self.pairing.issue(user_id, chat_id, name)
        logger.info("Telegram: a stranger %s, chat=%s", "was given a code" if code else "was ignored", chat_hash(chat_id))
        if code is not None:
            await self._reply(chat_id, words(language, "pair", code=code))

    async def _command(self, chat_id: int, text: str, language: str) -> None:
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command == "/stop":
            stopped = await self._stop(session_id(chat_id))
            await self._reply(chat_id, words(language, "stopped" if stopped else "nothing"))
        else:  # /start, /help and anything else Telegram clients offer
            await self._reply(chat_id, words(language, "intro"))

    async def _converse(self, chat_id: int, text: str, language: str) -> None:
        if not text.strip():
            return
        # One question at a time for a chat: a second would cancel the first in the assistant.
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            typing = asyncio.ensure_future(self._typing(chat_id))
            try:
                answer = await self._ask(session_id(chat_id), text, language)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram: the assistant failed (%s)", type(exc).__name__)
                answer = words(language, "failed")
            finally:
                typing.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await typing
            await self._reply(chat_id, answer)

    async def _typing(self, chat_id: int) -> None:
        while True:
            try:
                await self._api.send_chat_action(chat_id)
            except TelegramError:
                pass  # the indicator is a courtesy
            await asyncio.sleep(TYPING_REPEAT_SECONDS)

    async def _reply(self, chat_id: int, text: str) -> None:
        pieces = split_message(text)
        for piece in pieces:
            await self._api.send_message(chat_id, piece)
        logger.info("Telegram: sent %d message(s), chat=%s", len(pieces), chat_hash(chat_id))

    # --- what the app and the worker ask for ---

    async def tell(self, chat_id: int, text: str) -> None:
        """Send `text` to a chat that is allowed (a background report, the pairing confirmation).
        A chat that is not allowed, or a failure, sends nothing."""
        if not self._is_allowed(chat_id):
            return
        try:
            await self._reply(chat_id, text)
        except TelegramError as error:
            logger.warning("Telegram: could not send a report (%s)", error.kind)

    async def welcome(self, chat_id: int) -> None:
        """Tell a user whose code was just approved that they are in."""
        try:
            await self._reply(chat_id, words(self._language(), "paired"))
        except TelegramError as error:
            logger.warning("Telegram: could not confirm a pairing (%s)", error.kind)
