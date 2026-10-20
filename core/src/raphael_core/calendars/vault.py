"""Where the calendar addresses are kept: Windows Credential Manager, through keyring.

An address is a secret, so it is kept the way the OpenRouter key is (brain/secrets.py): nowhere
else. Not in a config file, not in the database (which holds only the label of each calendar), not
in a log, and not in anything the page is sent. Never log or print an address from this module or
from anything that handles one.

Each calendar is its own credential, named "Raphael calendar <id>". The Windows backend of keyring
files a credential under its service name alone and, when a second user name is written to a
service, moves the first one to "<username>@<service>"; one service for each calendar keeps clear
of that. The value is kept in UTF-16, and a credential holds at most 2560 bytes: fetch.URL_MAX_CHARS
is set to fit.
"""

import contextlib
from typing import Protocol

import keyring
from keyring.errors import PasswordDeleteError


class AddressVault(Protocol):
    def get(self, calendar_id: int) -> str | None:
        """The address of the calendar, or None when there is none."""
        ...

    def set(self, calendar_id: int, address: str) -> None:
        """Store the address, replacing any earlier one."""
        ...

    def clear(self, calendar_id: int) -> None:
        """Remove the address. Does nothing when there is none."""
        ...


class CredentialAddressVault:
    """The addresses as generic credentials in Windows Credential Manager (the user's vault).

    keyring reports "nothing to delete" as PasswordDeleteError, which `clear` swallows; any other
    failure reaches the caller, because pretending an address is gone when it is not would be worse
    than failing. As in CredentialKeyStore, `set` removes what is stored before it writes, so that
    no earlier value stays behind in the vault.
    """

    _USERNAME = "address"

    def __init__(self, service: str = "Raphael calendar") -> None:
        self._service = service

    def _name(self, calendar_id: int) -> str:
        return f"{self._service} {calendar_id}"

    def get(self, calendar_id: int) -> str | None:
        return keyring.get_password(self._name(calendar_id), self._USERNAME)

    def set(self, calendar_id: int, address: str) -> None:
        self.clear(calendar_id)
        keyring.set_password(self._name(calendar_id), self._USERNAME, address)

    def clear(self, calendar_id: int) -> None:
        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(self._name(calendar_id), self._USERNAME)


class MemoryAddressVault:
    """Addresses kept in this object only: for tests and for running without a vault."""

    def __init__(self) -> None:
        self._addresses: dict[int, str] = {}

    def get(self, calendar_id: int) -> str | None:
        return self._addresses.get(calendar_id)

    def set(self, calendar_id: int, address: str) -> None:
        self._addresses[calendar_id] = address

    def clear(self, calendar_id: int) -> None:
        self._addresses.pop(calendar_id, None)
