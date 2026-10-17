"""Tests for the OpenRouter key stores.

The keyring is an in-memory fake that every test in this module installs (autouse), so nothing here
can reach the real Windows Credential Manager.
"""

import keyring
import keyring.backend
import keyring.errors
import pytest

from raphael_core.brain.secrets import CredentialKeyStore, MemoryKeyStore


class MemoryKeyring(keyring.backend.KeyringBackend):
    """A keyring that keeps its passwords in a dict.

    Like the Windows backend, it raises PasswordDeleteError when there is nothing to delete.
    """

    # Zero keeps it out of keyring's own search for a backend: tests install it with set_keyring.
    priority = 0

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def delete_password(self, service, username):
        try:
            del self.passwords[(service, username)]
        except KeyError:
            raise keyring.errors.PasswordDeleteError(service) from None


@pytest.fixture(autouse=True)
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture(params=["memory", "credential"])
def store(request):
    """Each KeyStore implementation in turn."""
    return MemoryKeyStore() if request.param == "memory" else CredentialKeyStore()


def test_credential_store_round_trip(memory_keyring):
    s = CredentialKeyStore()
    assert s.get() is None
    s.set("sk-or-v1-abc")
    assert s.get() == "sk-or-v1-abc"
    s.clear()
    s.clear()
    assert s.get() is None


def test_memory_store_round_trip():
    s = MemoryKeyStore()
    assert s.get() is None
    s.set("sk-or-v1-abc")
    assert s.get() == "sk-or-v1-abc"
    s.clear()
    s.clear()
    assert s.get() is None


def test_setting_a_key_again_replaces_it(store):
    store.set("sk-or-v1-old")
    store.set("sk-or-v1-new")
    assert store.get() == "sk-or-v1-new"
    store.clear()
    assert store.get() is None


def test_the_key_is_filed_under_service_raphael(memory_keyring):
    CredentialKeyStore().set("sk-or-v1-abc")
    assert memory_keyring.passwords == {("Raphael", "openrouter"): "sk-or-v1-abc"}


def test_the_service_and_username_can_be_chosen(memory_keyring):
    other = CredentialKeyStore(service="Elsewhere", username="someone")
    other.set("sk-or-v1-other")
    assert other.get() == "sk-or-v1-other"
    assert CredentialKeyStore().get() is None
    assert memory_keyring.passwords == {("Elsewhere", "someone"): "sk-or-v1-other"}


def test_clear_reports_failures_other_than_nothing_to_delete(memory_keyring, monkeypatch):
    # Swallowing every error would tell the user the key is gone when it is not.
    def locked(service, username):
        raise keyring.errors.KeyringLocked("the vault is locked")

    monkeypatch.setattr(memory_keyring, "delete_password", locked)
    with pytest.raises(keyring.errors.KeyringLocked):
        CredentialKeyStore().clear()
