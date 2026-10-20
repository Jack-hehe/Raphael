"""Fixtures for the whole test suite.

Every test runs with an in-memory keyring in place of Windows Credential Manager, so no test, in
any directory, can reach the real vault of whoever runs the suite. The guard fails closed: outside
a test's own in-memory keyring, any use of the keyring raises.

Tests marked `models` need the speech models downloaded and take a while to load them, so they are
skipped unless the environment variable RAPHAEL_TEST_MODELS is 1.

`conn` is a database in memory with the brain's schema, for the tests of what keeps its data there:
the brain, the assistant and the plugins.
"""

import os
import sqlite3
from collections.abc import Iterator

import keyring
import keyring.backend
import keyring.backends.fail
import keyring.errors
import pytest

from raphael_core.brain import db


class MemoryKeyring(keyring.backend.KeyringBackend):
    """A keyring that keeps its credentials in a dict, shaped like the Windows backend's.

    Windows Credential Manager files a credential under a target name, the service, and keeps the
    username inside it. Setting a password where a credential exists first re-saves that one under
    "<username>@<service>" (keyring 25.7.0, keyring/backends/Windows.py), so an overwrite leaves
    the old value in the vault. This fake does the same, which is what lets a test see whether
    CredentialKeyStore.set leaves a stale copy behind. Like the real one, it raises
    PasswordDeleteError when there is nothing to delete.
    """

    # Zero keeps it out of keyring's own search for a backend: tests install it with set_keyring.
    priority = 0

    def __init__(self) -> None:
        super().__init__()
        self.credentials: dict[str, tuple[str, str]] = {}  # target -> (username, password)
        # The keyring that comes back when the test is over; set by the fixture below.
        self.restored_afterwards: keyring.backend.KeyringBackend | None = None

    def get_password(self, service, username):
        found = self.credentials.get(service)
        if found is None or found[0] != username:
            found = self.credentials.get(f"{username}@{service}")
        return None if found is None else found[1]

    def set_password(self, service, username, password):
        existing = self.credentials.get(service)
        if existing is not None:
            self.credentials[f"{existing[0]}@{service}"] = existing
        self.credentials[service] = (username, password)

    def delete_password(self, service, username):
        deleted = False
        for target in (service, f"{username}@{service}"):
            found = self.credentials.get(target)
            if found is not None and found[0] == username:
                del self.credentials[target]
                deleted = True
        if not deleted:
            raise keyring.errors.PasswordDeleteError(service)


def pytest_configure(config):
    """Before any test: a keyring that raises on every use, instead of the real vault."""
    keyring.set_keyring(keyring.backends.fail.Keyring())


def pytest_collection_modifyitems(config, items):
    """Skip the tests marked `models` unless RAPHAEL_TEST_MODELS=1."""
    if os.environ.get("RAPHAEL_TEST_MODELS") == "1":
        return
    skip = pytest.mark.skip(reason="needs the speech models: set RAPHAEL_TEST_MODELS=1")
    for item in items:
        if item.get_closest_marker("models"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def memory_keyring():
    """A new, empty in-memory keyring for each test; the previous backend comes back afterwards."""
    backend = MemoryKeyring()
    backend.restored_afterwards = keyring.get_keyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(backend.restored_afterwards)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()
