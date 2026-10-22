"""What the messaging connections remember on this computer: `messaging.json`.

For each messaging app it holds whether the connection is on, which bot or workspace it is (its id
and name, public facts) and who may talk to Raphael through it (the user and chat ids of the app,
as strings, and the name each showed when it asked to pair, so the owner can tell them apart). It
never holds a secret: those are in the vault. Nothing here logs a name or an id.

Version 1 had Telegram only, with whole-number ids; it is read as version 2 and written as such
the next time something changes.
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
VERSION = 2
MAX_ALLOWED = 10
MAX_NAME_CHARS = 60


@dataclass
class AllowedUser:
    user_id: str
    name: str
    chat_id: str = ""  # where a message to this user goes; the user id itself when it is empty

    @property
    def chat(self) -> str:
        return self.chat_id or self.user_id


@dataclass
class ChannelConfig:
    enabled: bool = False
    bot_id: str | None = None
    bot_username: str | None = None
    allowed: list[AllowedUser] = field(default_factory=list)

    @property
    def connected(self) -> bool:
        return self.bot_id is not None

    def is_allowed(self, user_id: str) -> bool:
        return any(user.user_id == user_id for user in self.allowed)

    def chat_allowed(self, chat_id: str) -> bool:
        return any(user.chat == chat_id for user in self.allowed)


@dataclass
class MessagingConfig:
    channels: dict[str, ChannelConfig] = field(default_factory=dict)

    def channel(self, kind: str) -> ChannelConfig:
        return self.channels.setdefault(kind, ChannelConfig())


def _id(value: object) -> str | None:
    """An id as a string: a string as it is, a whole number (version 1) as its digits."""
    if type(value) is int:
        return str(value)
    return value if isinstance(value, str) and value else None


def _parse_channel(raw: object, *, v1: bool) -> ChannelConfig:
    if not isinstance(raw, dict):
        return ChannelConfig()
    identity = raw.get("bot" if v1 else "identity")
    bot_id = _id(identity.get("id")) if isinstance(identity, dict) else None
    name = identity.get("name" if not v1 else "username") if isinstance(identity, dict) else None
    allowed: list[AllowedUser] = []
    for item in raw.get("allowed") or []:
        if not isinstance(item, dict):
            continue
        user_id = _id(item.get("user_id" if v1 else "user"))
        if user_id is None:
            continue
        label = item.get("name")
        chat_id = "" if v1 else (_id(item.get("chat")) or "")
        allowed.append(
            AllowedUser(user_id, label[:MAX_NAME_CHARS] if isinstance(label, str) else "", chat_id)
        )
    return ChannelConfig(
        enabled=raw.get("enabled") is True and bot_id is not None,
        bot_id=bot_id,
        bot_username=name if isinstance(name, str) else None,
        allowed=allowed[:MAX_ALLOWED],
    )


def _parse(raw: object) -> MessagingConfig:
    if not isinstance(raw, dict):
        return MessagingConfig()
    if raw.get("version") == 1 or "channels" not in raw and "telegram" in raw:
        return MessagingConfig({"telegram": _parse_channel(raw.get("telegram"), v1=True)})
    channels = raw.get("channels")
    if not isinstance(channels, dict):
        return MessagingConfig()
    return MessagingConfig(
        {kind: _parse_channel(item, v1=False) for kind, item in channels.items() if isinstance(kind, str)}
    )


class MessagingStore:
    """The file in the data folder; a missing or damaged one reads as nothing connected."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / FILE_NAME

    def load(self) -> MessagingConfig:
        try:
            return _parse(json.loads(self._path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return MessagingConfig()
        except (OSError, ValueError):
            logger.warning("messaging.json could not be read; nothing is connected")
            return MessagingConfig()

    def save(self, config: MessagingConfig) -> None:
        """Write the file whole or not at all (a crash must not leave half of an allowlist)."""
        document = {
            "version": VERSION,
            "channels": {
                kind: {
                    "enabled": channel.enabled,
                    "identity": None
                    if channel.bot_id is None
                    else {"id": channel.bot_id, "name": channel.bot_username},
                    "allowed": [
                        {"user": u.user_id, "name": u.name, "chat": u.chat} for u in channel.allowed
                    ],
                }
                for kind, channel in config.channels.items()
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
