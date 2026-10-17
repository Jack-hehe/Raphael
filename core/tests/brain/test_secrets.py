"""Tests for the OpenRouter key stores.

The keyring is the in-memory fake from tests/conftest.py, which every test gets, so nothing here
can reach the real Windows Credential Manager.
"""

import keyring.errors
import pytest

from raphael_core.brain.secrets import CredentialKeyStore, MemoryKeyStore


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
    assert memory_keyring.credentials == {"Raphael": ("openrouter", "sk-or-v1-abc")}


def test_the_service_and_username_can_be_chosen(memory_keyring):
    other = CredentialKeyStore(service="Elsewhere", username="someone")
    other.set("sk-or-v1-other")
    assert other.get() == "sk-or-v1-other"
    assert CredentialKeyStore().get() is None
    assert memory_keyring.credentials == {"Elsewhere": ("someone", "sk-or-v1-other")}


def test_replacing_the_key_leaves_exactly_one_credential(memory_keyring):
    # Overwriting alone would leave the old key in the vault as "openrouter@Raphael".
    s = CredentialKeyStore()
    s.set("sk-or-v1-old")
    s.set("sk-or-v1-new")
    assert s.get() == "sk-or-v1-new"
    assert memory_keyring.credentials == {"Raphael": ("openrouter", "sk-or-v1-new")}


def test_a_key_that_cannot_be_removed_is_not_replaced(memory_keyring, monkeypatch):
    s = CredentialKeyStore()
    s.set("sk-or-v1-old")

    def locked(service, username):
        raise keyring.errors.KeyringLocked("the vault is locked")

    monkeypatch.setattr(memory_keyring, "delete_password", locked)
    with pytest.raises(keyring.errors.KeyringLocked):
        s.set("sk-or-v1-new")
    assert s.get() == "sk-or-v1-old"


def test_clear_reports_failures_other_than_nothing_to_delete(memory_keyring, monkeypatch):
    # Swallowing every error would tell the user the key is gone when it is not.
    def locked(service, username):
        raise keyring.errors.KeyringLocked("the vault is locked")

    monkeypatch.setattr(memory_keyring, "delete_password", locked)
    with pytest.raises(keyring.errors.KeyringLocked):
        CredentialKeyStore().clear()
