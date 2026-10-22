"""What the messaging connections remember on this computer: `messaging.json`.

It holds, for Telegram, whether the connection is on, which bot it is (its id and @name, public
facts) and who may talk to Raphael through it (Telegram user ids and the name each showed when it
asked to pair, so the owner can tell them apart). It never holds the bot token: that is in the
vault. Nothing here logs a name or an id.
"""

import contextlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "messaging.json"
MAX_ALLOWED = 10
MAX_NAME_CHARS = 60


@dataclass
class AllowedUser:
    user_id: int
    name: str


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_id: int | None = None
    bot_username: str | None = None
    allowed: list[AllowedUser] = field(default_factory=list)

    @property
    def connected(self) -> bool:
        return self.bot_id is not None

    def is_allowed(self, user_id: int) -> bool:
        return any(user.user_id == user_id for user in self.allowed)


def _int(value: object) -> int | None:
    return value if type(value) is int else None


def _parse(raw: object) -> TelegramConfig:
    if not isinstance(raw, dict):
        return TelegramConfig()
    telegram = raw.get("telegram")
    if not isinstance(telegram, dict):
        return TelegramConfig()
    bot = telegram.get("bot")
    bot_id = _int(bot.get("id")) if isinstance(bot, dict) else None
    username = bot.get("username") if isinstance(bot, dict) else None
    allowed: list[AllowedUser] = []
    for item in telegram.get("allowed") or []:
        if isinstance(item, dict) and (user_id := _int(item.get("user_id"))) is not None:
            name = item.get("name")
            allowed.append(
                AllowedUser(user_id, name[:MAX_NAME_CHARS] if isinstance(name, str) else "")
            )
    return TelegramConfig(
        enabled=telegram.get("enabled") is True and bot_id is not None,
        bot_id=bot_id,
        bot_username=username if isinstance(username, str) else None,
        allowed=allowed[:MAX_ALLOWED],
    )


class MessagingStore:
    """The file in the data folder; a missing or damaged one reads as nothing connected."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / FILE_NAME

    def load(self) -> TelegramConfig:
        try:
            return _parse(json.loads(self._path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return TelegramConfig()
        except (OSError, ValueError):
            logger.warning("messaging.json could not be read; nothing is connected")
            return TelegramConfig()

    def save(self, config: TelegramConfig) -> None:
        """Write the file whole or not at all (a crash must not leave half of an allowlist)."""
        document = {
            "version": 1,
            "telegram": {
                "enabled": config.enabled,
                "bot": None
                if config.bot_id is None
                else {"id": config.bot_id, "username": config.bot_username},
                "allowed": [{"user_id": u.user_id, "name": u.name} for u in config.allowed],
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(document, out, ensure_ascii=False)
            os.replace(temporary, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
