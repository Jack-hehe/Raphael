"""Where the secrets of the plugins are kept: Windows Credential Manager, through keyring.

A plugin's environment values and request headers are secrets (a token is the usual one). Like the
OpenRouter key (`brain.secrets`) they live nowhere else: not in the config file, not in the
database, not in a log, and not in anything that goes back to the window. Never log or print a
value from this module or from anything that handles one.

Each secret is a credential of its own, with its own target name, so that Windows never has to
move one aside to make room for another (see `CredentialKeyStore`: the Windows backend of keyring
files a credential under the service name, and a second one under the same name pushes the first to
"<username>@<service>"). The Credential Manager takes at most 1280 characters of text in a
credential, so a longer value is refused up front instead of failing half way.
"""

import contextlib
from typing import Protocol

import keyring
from keyring.errors import PasswordDeleteError

SERVICE_PREFIX = "Raphael plugin"
# The Credential Manager's blob is 2560 bytes and keyring writes UTF-16: 1280 characters, with room
# to spare for what keyring adds.
MAX_SECRET_CHARS = 1000


class SecretTooLong(ValueError):
    """A secret that the Credential Manager could not hold."""


class SecretVault(Protocol):
    def get(self, name: str) -> str | None:
        """The secret called `name`, or None when there is none."""
        ...

    def set(self, name: str, value: str) -> None:
        """Store a secret, replacing an earlier one with the same name."""
        ...

    def delete(self, name: str) -> None:
        """Remove a secret. Does nothing when there is none."""
        ...


def secret_name(server_id: str, kind: str, key: str) -> str:
    """The name of one secret of one server: `kind` is "env" or "header"."""
    return f"{server_id}:{kind}:{key}"


# A value longer than one credential (an OAuth token can be) is cut into pieces of this many
# characters, each its own credential: the count is kept under the name itself, the pieces under
# "<name>#1", "<name>#2" and so on.
CHUNK_CHARS = 800


def _checked(value: str) -> str:
    if len(value) > MAX_SECRET_CHARS:
        raise SecretTooLong(f"A secret holds at most {MAX_SECRET_CHARS} characters")
    return value


def store_long(vault: SecretVault, name: str, value: str) -> None:
    """Keep `value`, of any length, under `name`, in as many credentials as it takes. What an earlier
    value left over (more pieces than this one has) is removed."""
    before = _piece_count(vault, name)
    pieces = [value[i : i + CHUNK_CHARS] for i in range(0, len(value), CHUNK_CHARS)] or [""]
    for number, piece in enumerate(pieces, start=1):
        vault.set(f"{name}#{number}", piece)
    vault.set(name, str(len(pieces)))
    for number in range(len(pieces) + 1, before + 1):
        vault.delete(f"{name}#{number}")


def load_long(vault: SecretVault, name: str) -> str | None:
    """The value kept under `name`, or None when there is none or a piece of it is missing."""
    count = _piece_count(vault, name)
    if count == 0:
        return None
    pieces = [vault.get(f"{name}#{number}") for number in range(1, count + 1)]
    return None if any(piece is None for piece in pieces) else "".join(p for p in pieces if p is not None)


def delete_long(vault: SecretVault, name: str) -> None:
    """Remove a value kept with `store_long`, every piece of it."""
    for number in range(1, _piece_count(vault, name) + 1):
        vault.delete(f"{name}#{number}")
    vault.delete(name)


def _piece_count(vault: SecretVault, name: str) -> int:
    count = vault.get(name)
    return int(count) if count is not None and count.isdigit() and int(count) <= 1000 else 0


class CredentialVault:
    """The secrets as generic credentials in Windows Credential Manager (the user's vault).

    The credential's target is "Raphael plugin <name>" and its user name is "secret". `set` removes
    the stored secret before it writes the new one, for the reason `CredentialKeyStore.set` gives:
    an overwrite would leave the old value in the vault under a second name. Any failure other than
    "nothing to delete" reaches the caller, since pretending a secret is gone when it is not would
    be worse than failing.
    """

    _USERNAME = "secret"

    def _service(self, name: str) -> str:
        return f"{SERVICE_PREFIX} {name}"

    def get(self, name: str) -> str | None:
        return keyring.get_password(self._service(name), self._USERNAME)

    def set(self, name: str, value: str) -> None:
        _checked(value)
        self.delete(name)
        keyring.set_password(self._service(name), self._USERNAME, value)

    def delete(self, name: str) -> None:
        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(self._service(name), self._USERNAME)


class MemoryVault:
    """Secrets kept in this object only: for tests and for running without a vault."""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._secrets.get(name)

    def set(self, name: str, value: str) -> None:
        self._secrets[name] = _checked(value)

    def delete(self, name: str) -> None:
        self._secrets.pop(name, None)

    def names(self) -> list[str]:
        """What is stored (for a test to see that nothing is left behind)."""
        return sorted(self._secrets)
