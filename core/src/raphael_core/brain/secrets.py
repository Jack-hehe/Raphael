"""Where the OpenRouter key is kept: Windows Credential Manager, through keyring.

The key lives nowhere else: not in a config file, not in the database, not in a log. Never log or
print a key from this module or from anything that handles one.
"""

import contextlib
from typing import Protocol

import keyring
from keyring.errors import PasswordDeleteError


class KeyStore(Protocol):
    def get(self) -> str | None:
        """The stored key, or None when there is none."""
        ...

    def set(self, key: str) -> None:
        """Store the key, replacing any earlier one."""
        ...

    def clear(self) -> None:
        """Remove the key. Does nothing when there is none."""
        ...


class CredentialKeyStore:
    """The key as a generic credential in Windows Credential Manager (the user's vault).

    `service` and `username` name the credential. keyring reports "nothing to delete" as
    PasswordDeleteError, which `clear` swallows; any other failure reaches the caller, because
    pretending a key is gone when it is not would be worse than failing.

    The Windows backend of keyring 25.7.0 keeps the value that `set` replaces in a second
    credential, named "<username>@<service>", until `clear` removes both.
    """

    def __init__(self, service: str = "Raphael", username: str = "openrouter") -> None:
        self._service = service
        self._username = username

    def get(self) -> str | None:
        return keyring.get_password(self._service, self._username)

    def set(self, key: str) -> None:
        keyring.set_password(self._service, self._username, key)

    def clear(self) -> None:
        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(self._service, self._username)


class MemoryKeyStore:
    """A key kept in this object only: for tests and for running without a vault."""

    def __init__(self) -> None:
        self._key: str | None = None

    def get(self) -> str | None:
        return self._key

    def set(self, key: str) -> None:
        self._key = key

    def clear(self) -> None:
        self._key = None
