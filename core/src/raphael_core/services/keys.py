"""The owner's own keys for the services that take one: Brave, Tavily, Exa, Firecrawl and Jina.

Each key is a credential of its own in Windows Credential Manager, through the same store as the
OpenRouter key (`brain.secrets.CredentialKeyStore`), with its own target name ("Raphael service
brave", ...): one target for each secret, so that Windows never moves one aside to make room for
another, and a key is removed before a new one is written, never overwritten in place (see
`CredentialKeyStore.set`). Tavily is both a search and a reader, and has one key for both.

A key lives nowhere else: not in a file, not in the database, not in a log, and never in an answer
to the window, which is told only whether one is stored. Never log or print a key from this module
or from anything that handles one.
"""

import logging
from collections.abc import Callable

from ..brain.secrets import CredentialKeyStore, KeyStore, MemoryKeyStore

logger = logging.getLogger(__name__)

# The services that take a key of the owner's. Jina works without one, with lower limits.
KEYED_SERVICES = ("brave", "tavily", "exa", "firecrawl", "jina")
# The longest key taken: far more than any service issues, and well within a credential.
MAX_KEY_CHARS = 512
# The target name of each key's credential is this, a space and the service.
CREDENTIAL_PREFIX = "Raphael service"


class KeyRefused(ValueError):
    """A key that cannot be stored. `kind` says why: "service" (no such keyed service), "blank",
    "long" or "characters" (a control character, a space inside: no service issues such a key,
    and one would break the header it is sent in). Never what the key was."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def checked_key(key: str) -> str:
    """`key` without the spaces around it, or KeyRefused if it is not a key that can be stored."""
    text = key.strip()
    if not text:
        raise KeyRefused("blank")
    if len(text) > MAX_KEY_CHARS:
        raise KeyRefused("long")
    if any(char.isspace() or not char.isprintable() for char in text):
        raise KeyRefused("characters")
    return text


class ServiceKeys:
    """The keys of the keyed services. `store_for(service)` is the store of one service's key,
    made once and kept."""

    def __init__(self, store_for: Callable[[str], KeyStore]) -> None:
        self._store_for = store_for
        self._stores: dict[str, KeyStore] = {}

    def _store(self, service: str) -> KeyStore:
        if service not in KEYED_SERVICES:
            raise KeyRefused("service")
        if service not in self._stores:
            self._stores[service] = self._store_for(service)
        return self._stores[service]

    def get(self, service: str) -> str | None:
        """The stored key of `service`, or None. A store that fails reads as no key (logged by the
        type of the error only: its message could hold the key), and the call that needed it then
        fails as one with no key."""
        store = self._store(service)
        try:
            key = store.get()
        except Exception as exc:
            logger.warning("The key store of a service failed: %s", type(exc).__name__)
            return None
        return key if key and key.strip() else None

    def has(self, service: str) -> bool:
        return self.get(service) is not None

    def set(self, service: str, key: str) -> None:
        """Store the key of `service` (`checked_key` first), in place of the one it had."""
        store = self._store(service)
        store.set(checked_key(key))

    def clear(self, service: str) -> None:
        self._store(service).clear()


def credential_keys() -> ServiceKeys:
    """The keys in Windows Credential Manager, one credential for each service."""
    return ServiceKeys(
        lambda service: CredentialKeyStore(service=f"{CREDENTIAL_PREFIX} {service}", username="key")
    )


def memory_keys() -> ServiceKeys:
    """The keys in memory only: for tests and for running without a vault."""
    return ServiceKeys(lambda service: MemoryKeyStore())
