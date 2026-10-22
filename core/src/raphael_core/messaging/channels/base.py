"""What a messaging app must offer for the switchboard to talk through it.

A channel only moves messages: it receives them as `Incoming`, sends text, shows that Raphael is
busy and fetches an attachment. It decides nothing; who may talk, what a command does and how a
question is answered is the switchboard's (core.py).
"""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Protocol

import httpx


class ChannelError(Exception):
    """A call a messaging app refused or that did not get there. `kind` is one of: unauthorized
    (the credentials are wrong or revoked), conflict (another client uses the bot), rate_limited,
    network, bad_request. The message never holds a secret or anything sent."""

    def __init__(self, kind: str, retry_after: float = 0.0) -> None:
        super().__init__(kind)
        self.kind = kind
        self.retry_after = retry_after


@dataclass
class Attachment:
    """Something besides text in a message. `ref` is what the channel needs to fetch it. `size` is
    in bytes and `duration` in seconds, as the sender's app reports them (None when it does not)."""

    kind: str  # "voice" (a recorded message) or "other"
    ref: str
    mime: str | None = None
    size: int | None = None
    duration: float | None = None


@dataclass
class Incoming:
    """A message as the switchboard sees it, whatever app it came from. Ids are strings: an app's
    ids may be larger than a JavaScript number holds, or not numbers at all."""

    chat: str  # where the reply goes
    user: str  # who wrote
    name: str = ""  # for the owner to tell people apart; never logged
    private: bool = True  # only one-to-one chats reach the assistant
    text: str | None = None
    attachments: list[Attachment] = field(default_factory=list)


Deliver = Callable[[Incoming], None]


class Channel(Protocol):
    kind: str
    error: str | None  # why the last attempt to receive failed; None while it works

    async def run(self, deliver: Deliver) -> None:
        """Receive messages, giving each to `deliver` (which returns at once), until cancelled or
        until the credentials are refused."""
        ...

    async def send(self, chat: str, text: str) -> None:
        """Send `text`, in as many messages as the app needs."""
        ...

    def busy(self, chat: str) -> AbstractAsyncContextManager[None]:
        """Show that Raphael is working on an answer, for as long as the context is open. Best
        effort: a failure is not an error."""
        ...

    async def fetch(self, attachment: Attachment, limit: int) -> bytes:
        """The bytes of an attachment, in memory. ChannelError("bad_request") when it is larger
        than `limit` bytes."""
        ...


@dataclass(frozen=True)
class Identity:
    """Who the credentials belong to: the bot or workspace. A different one starts with nobody
    allowed."""

    id: str
    name: str | None


@dataclass(frozen=True)
class ChannelType:
    """One kind of messaging app, as the service sets it up. `secrets` are the names of what the
    vault keeps for it (`messaging:<kind>:<name>`)."""

    kind: str
    secrets: tuple[str, ...]
    # The secrets out of the params of `connect`; None when they cannot be right, ValueError when a
    # param is not of the type it must be.
    read_secrets: Callable[[dict], dict[str, str] | None]
    check: Callable[[dict[str, str], httpx.AsyncClient], Awaitable[Identity]]
    make: Callable[[dict[str, str], httpx.AsyncClient], Channel]
