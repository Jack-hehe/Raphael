"""The suite stays off the real Windows Credential Manager.

tests/conftest.py gives every test an in-memory keyring. Most of these tests ask for no keyring
fixture on purpose: they fail if that stops being automatic. The real Windows backend is made to
raise first, so even a failure here cannot reach the vault of whoever runs the suite.
"""

import keyring
import keyring.backends.fail
import keyring.errors
import pytest
from keyring.backends.Windows import WinVaultKeyring

from raphael_core.brain.secrets import CredentialKeyStore


@pytest.fixture
def real_vault_forbidden(monkeypatch):
    def forbidden(self, *args, **kwargs):
        raise AssertionError("the real Windows Credential Manager was used")

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(WinVaultKeyring, name, forbidden)


def test_a_credential_store_with_defaults_works_without_the_real_vault(real_vault_forbidden):
    store = CredentialKeyStore()
    store.set("sk-or-v1-abc")
    assert store.get() == "sk-or-v1-abc"
    store.clear()
    assert store.get() is None


@pytest.mark.parametrize("attempt", [1, 2])
def test_each_test_gets_a_new_empty_keyring(real_vault_forbidden, attempt):
    # The second attempt would find the first one's key if the keyring outlived a test.
    store = CredentialKeyStore()
    assert store.get() is None
    store.set(f"sk-or-v1-attempt-{attempt}")


def test_outside_a_test_the_keyring_refuses_every_use(memory_keyring):
    # If the per-test keyring ever stopped being installed, a test would meet this, not the vault.
    assert isinstance(memory_keyring.restored_afterwards, keyring.backends.fail.Keyring)
    with pytest.raises(keyring.errors.NoKeyringError):
        memory_keyring.restored_afterwards.get_password("Raphael", "openrouter")


def test_the_in_memory_keyring_keeps_what_an_overwrite_replaces(memory_keyring):
    # What the Windows backend does, and so what CredentialKeyStore.set has to work around.
    keyring.set_password("Service", "user", "one")
    keyring.set_password("Service", "user", "two")
    assert keyring.get_password("Service", "user") == "two"
    assert memory_keyring.credentials == {
        "Service": ("user", "two"),
        "user@Service": ("user", "one"),
    }
    keyring.delete_password("Service", "user")
    assert memory_keyring.credentials == {}
    with pytest.raises(keyring.errors.PasswordDeleteError):
        keyring.delete_password("Service", "user")
