"""The codes that strangers are given until the owner approves them in the app."""

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

CODE_LENGTH = 6
CODE_TTL_SECONDS = 600.0
MAX_PENDING = 10
# Letters and digits that cannot be mistaken for each other when read off a phone.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_NAME_CHARS = 60


@dataclass
class PendingPairing:
    code: str
    user: str
    chat: str
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

    def issue(self, user: str, chat: str, name: str) -> str | None:
        """A new code for a stranger, or None when one was already given to that user (a code is
        told once, not at every message) or too many are waiting."""
        self._sweep()
        if any(p.user == user for p in self._by_code.values()):
            return None
        if len(self._by_code) >= MAX_PENDING:
            return None
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._by_code:
                break
        self._by_code[code] = PendingPairing(
            code, user, chat, name[:_NAME_CHARS], self._clock() + CODE_TTL_SECONDS
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
