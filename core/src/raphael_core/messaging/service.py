"""The messaging connections as the app uses them: `messaging.telegram.*`.

The bot token is in the vault and nowhere else; it never comes back to the window. Only the main
window may call these methods (the Rust allowlist); the gateway has no way to reach them, so a
message from Telegram cannot approve a pairing or change a setting.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..integrations.vault import SecretVault
from ..protocol import INTERNAL_ERROR, INVALID_PARAMS, RpcError
from ..router import Router
from .config import MAX_ALLOWED, AllowedUser, MessagingStore, TelegramConfig
from .telegram import (
    Ask,
    Stop,
    TelegramApi,
    TelegramError,
    TelegramGateway,
    SESSION_PREFIX,
    chat_hash,
    looks_like_token,
)

logger = logging.getLogger(__name__)

TOKEN_SECRET = "messaging:telegram:bot_token"


def _invalid(message: str) -> RpcError:
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


class MessagingService:
    """`ask` and `stop` are what the gateway reaches the assistant by; `language` is the language
    Raphael answers in. `http` is a client for tests (a fake server); otherwise one is made."""

    def __init__(
        self,
        store: MessagingStore,
        vault: SecretVault,
        *,
        ask: Ask,
        stop: Stop,
        language: Callable[[], str],
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._store = store
        self._vault = vault
        self._ask = ask
        self._stop = stop
        self._language = language
        self._http = http
        self._owns_http = http is None
        self._config: TelegramConfig = store.load()
        self._gateway: TelegramGateway | None = None
        self._task: asyncio.Task[None] | None = None
        self._ended = False
        # One lock for the changes of the connection, so a switch and a disconnect cannot cross.
        self._lock = asyncio.Lock()

    def register(self, router: Router) -> None:
        for method, handler in {
            "messaging.telegram.status": self._status,
            "messaging.telegram.connect": self._connect,
            "messaging.telegram.set_enabled": self._set_enabled,
            "messaging.telegram.approve": self._approve,
            "messaging.telegram.reject": self._reject,
            "messaging.telegram.remove_user": self._remove_user,
            "messaging.telegram.disconnect": self._disconnect,
        }.items():
            router.register(method, handler)

    # --- lifecycle ---

    def start(self) -> None:
        """Begin polling if the connection is on. Needs the running loop."""
        if self._config.enabled and self._token() is not None:
            self._launch()

    async def aclose(self) -> None:
        self._ended = True
        await self._stop_polling()
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    def _token(self) -> str | None:
        try:
            return self._vault.get(TOKEN_SECRET)
        except Exception as exc:
            logger.warning("The vault could not be read for Telegram: %s", type(exc).__name__)
            return None

    def _launch(self) -> None:
        if self._ended or (self._task is not None and not self._task.done()):
            return
        token = self._token()
        if token is None:
            return
        gateway = TelegramGateway(
            TelegramApi(token, self._client()),
            ask=self._ask,
            stop=self._stop,
            is_allowed=lambda user_id: self._config.is_allowed(user_id),
            language=self._language,
        )
        self._gateway = gateway
        self._task = asyncio.ensure_future(gateway.run())

    async def _stop_polling(self) -> None:
        task, self._task = self._task, None
        gateway, self._gateway = self._gateway, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if gateway is not None:
            await gateway.aclose()

    # --- what the worker says when a task ends ---

    async def report(self, session: str, text: str) -> None:
        """A background task of a Telegram chat has ended: tell that chat."""
        gateway = self._gateway
        if gateway is None or not session.startswith(SESSION_PREFIX):
            return
        number = session[len(SESSION_PREFIX) :]
        if not number.lstrip("-").isdigit():
            return
        await gateway.tell(int(number), text)

    # --- the methods ---

    def _view(self) -> dict[str, Any]:
        gateway = self._gateway
        running = self._task is not None and not self._task.done()
        pending = (
            []
            if gateway is None
            else [
                {"code": p.code, "name": p.name, "expires_in_s": max(0, int(left))}
                for p, left in gateway.pairing.pending()
            ]
        )
        config = self._config
        return {
            "connected": config.connected,
            "enabled": config.enabled,
            "running": running,
            "error": None if gateway is None else gateway.error,
            "bot": {"username": config.bot_username} if config.connected else None,
            "allowed": [{"user_id": u.user_id, "name": u.name} for u in config.allowed],
            "pending": pending,
        }

    async def _status(self, params: dict) -> dict:
        return self._view()

    async def _connect(self, params: dict) -> dict:
        """Check a token with `getMe`, keep it in the vault and turn the connection on. The token
        is never returned. A token that does not work comes back as `ok: false` with the reason."""
        token = params.get("token")
        if not isinstance(token, str):
            raise _invalid("token must be a string")
        token = token.strip()
        if not looks_like_token(token):
            return {"ok": False, "reason": "invalid_token"}
        try:
            me = await TelegramApi(token, self._client()).get_me()
        except TelegramError as error:
            logger.info("Telegram token check failed: %s", error.kind)
            return {"ok": False, "reason": "network" if error.kind == "network" else "invalid_token"}
        username = me.get("username")
        async with self._lock:
            await self._stop_polling()
            previous = self._config
            self._vault.set(TOKEN_SECRET, token)
            same_bot = previous.bot_id == me["id"]
            self._config = TelegramConfig(
                enabled=True,
                bot_id=me["id"],
                bot_username=username if isinstance(username, str) else None,
                # A different bot has other people talking to it: the allowlist starts empty.
                allowed=previous.allowed if same_bot else [],
            )
            self._store.save(self._config)
            self._launch()
        return {"ok": True, "status": self._view()}

    async def _set_enabled(self, params: dict) -> dict:
        enabled = params.get("enabled")
        if type(enabled) is not bool:
            raise _invalid("enabled must be true or false")
        async with self._lock:
            if not self._config.connected:
                raise _invalid("Telegram is not connected")
            self._config.enabled = enabled
            self._store.save(self._config)
            if enabled:
                self._launch()
            else:
                await self._stop_polling()
        return self._view()

    async def _approve(self, params: dict) -> dict:
        code = params.get("code")
        if not isinstance(code, str):
            raise _invalid("code must be a string")
        gateway = self._gateway
        pairing = None if gateway is None else gateway.pairing.take(code)
        if gateway is None or pairing is None:
            return {"approved": False, "status": self._view()}
        if not self._config.is_allowed(pairing.user_id):
            if len(self._config.allowed) >= MAX_ALLOWED:
                raise _invalid("too many people are allowed")
            self._config.allowed.append(AllowedUser(pairing.user_id, pairing.name))
            self._store.save(self._config)
        logger.info("Telegram: a pairing was approved, chat=%s", chat_hash(pairing.chat_id))
        task = asyncio.ensure_future(gateway.welcome(pairing.chat_id))
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return {"approved": True, "status": self._view()}

    async def _reject(self, params: dict) -> dict:
        code = params.get("code")
        if not isinstance(code, str):
            raise _invalid("code must be a string")
        if self._gateway is not None:
            self._gateway.pairing.take(code)
        return self._view()

    async def _remove_user(self, params: dict) -> dict:
        user_id = params.get("user_id")
        if type(user_id) is not int:
            raise _invalid("user_id must be a whole number")
        self._config.allowed = [u for u in self._config.allowed if u.user_id != user_id]
        self._store.save(self._config)
        return self._view()

    async def _disconnect(self, params: dict) -> dict:
        """Forget the bot: the token is deleted from the vault and nobody stays allowed."""
        async with self._lock:
            await self._stop_polling()
            with contextlib.suppress(Exception):
                self._vault.delete(TOKEN_SECRET)
            if self._token() is not None:
                raise RpcError(INTERNAL_ERROR, "The token could not be removed from the vault")
            self._config = TelegramConfig()
            self._store.save(self._config)
        return self._view()
