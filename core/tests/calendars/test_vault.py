"""The calendar addresses in the credential vault (calendars/vault.py).

tests/conftest.py gives each test an in-memory keyring shaped like the Windows backend, so the real
Credential Manager is never touched.
"""

import keyring
import pytest
from keyring.backends.Windows import WinVaultKeyring

from raphael_core.calendars.vault import CredentialAddressVault, MemoryAddressVault

from .conftest import GOOGLE, OTHER


@pytest.fixture
def real_vault_forbidden(monkeypatch):
    def forbidden(self, *args, **kwargs):
        raise AssertionError("the real Windows Credential Manager was used")

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(WinVaultKeyring, name, forbidden)


def test_an_address_is_kept_and_read_back(real_vault_forbidden):
    vault = CredentialAddressVault()
    assert vault.get(1) is None
    vault.set(1, GOOGLE)
    assert vault.get(1) == GOOGLE


def test_each_calendar_has_a_credential_of_its_own(memory_keyring):
    vault = CredentialAddressVault()
    vault.set(1, GOOGLE)
    vault.set(2, OTHER)
    assert vault.get(1) == GOOGLE
    assert vault.get(2) == OTHER
    # a service for each calendar: the Windows backend moves a second user name of one service
    # to another credential, and that is kept clear of
    assert set(memory_keyring.credentials) == {"Raphael calendar 1", "Raphael calendar 2"}


def test_the_openrouter_key_is_not_touched(memory_keyring):
    keyring.set_password("Raphael", "openrouter", "sk-or-v1-abc")
    vault = CredentialAddressVault()
    vault.set(1, GOOGLE)
    vault.clear(1)
    assert keyring.get_password("Raphael", "openrouter") == "sk-or-v1-abc"


def test_replacing_an_address_leaves_no_copy_of_the_old_one(memory_keyring):
    vault = CredentialAddressVault()
    vault.set(1, GOOGLE)
    vault.set(1, OTHER)
    assert vault.get(1) == OTHER
    assert [password for _, password in memory_keyring.credentials.values()] == [OTHER]


def test_clearing_removes_it_and_clearing_nothing_is_fine(memory_keyring):
    vault = CredentialAddressVault()
    vault.clear(7)
    vault.set(7, GOOGLE)
    vault.clear(7)
    assert vault.get(7) is None
    assert memory_keyring.credentials == {}


def test_the_memory_vault_behaves_the_same():
    vault = MemoryAddressVault()
    assert vault.get(1) is None
    vault.set(1, GOOGLE)
    vault.set(1, OTHER)
    assert vault.get(1) == OTHER
    vault.clear(1)
    vault.clear(1)
    assert vault.get(1) is None
