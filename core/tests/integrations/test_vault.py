"""Tests for the vault of the plugins' secrets.

The keyring is the in-memory fake from tests/conftest.py, which every test gets, so nothing here
can reach the real Windows Credential Manager.
"""

import pytest

from raphael_core.integrations.vault import (
    MAX_SECRET_CHARS,
    CredentialVault,
    MemoryVault,
    SecretTooLong,
    secret_name,
)


@pytest.fixture(params=["memory", "credential"])
def vault(request):
    return MemoryVault() if request.param == "memory" else CredentialVault()


def test_a_secret_round_trips_and_is_removed(vault):
    assert vault.get("a:env:TOKEN") is None
    vault.set("a:env:TOKEN", "s3cret")
    assert vault.get("a:env:TOKEN") == "s3cret"
    vault.delete("a:env:TOKEN")
    vault.delete("a:env:TOKEN")  # nothing to delete: not an error
    assert vault.get("a:env:TOKEN") is None


def test_setting_a_secret_again_replaces_it(vault):
    vault.set("a:env:TOKEN", "old")
    vault.set("a:env:TOKEN", "new")
    assert vault.get("a:env:TOKEN") == "new"


def test_each_secret_is_a_credential_of_its_own(memory_keyring):
    vault = CredentialVault()
    vault.set(secret_name("a", "env", "TOKEN"), "one")
    vault.set(secret_name("a", "header", "Authorization"), "two")
    vault.set(secret_name("b", "env", "TOKEN"), "three")
    # Three targets and nothing moved aside under "<user>@<service>".
    assert sorted(memory_keyring.credentials) == [
        "Raphael plugin a:env:TOKEN",
        "Raphael plugin a:header:Authorization",
        "Raphael plugin b:env:TOKEN",
    ]
    assert vault.get(secret_name("a", "env", "TOKEN")) == "one"


def test_replacing_a_secret_leaves_exactly_one_credential(memory_keyring):
    # Overwriting alone would leave the old value in the vault under a second name.
    vault = CredentialVault()
    vault.set("a:env:TOKEN", "old")
    vault.set("a:env:TOKEN", "new")
    assert memory_keyring.credentials == {"Raphael plugin a:env:TOKEN": ("secret", "new")}
    vault.delete("a:env:TOKEN")
    assert memory_keyring.credentials == {}


def test_the_openrouter_key_is_not_touched(memory_keyring):
    from raphael_core.brain.secrets import CredentialKeyStore

    CredentialKeyStore().set("sk-or-v1-abc")
    vault = CredentialVault()
    vault.set("a:env:TOKEN", "one")
    vault.delete("a:env:TOKEN")
    assert memory_keyring.credentials == {"Raphael": ("openrouter", "sk-or-v1-abc")}


def test_a_secret_the_vault_cannot_hold_is_refused_before_anything_is_written(
    vault, memory_keyring
):
    vault.set("a:env:TOKEN", "kept")
    with pytest.raises(SecretTooLong):
        vault.set("a:env:TOKEN", "x" * (MAX_SECRET_CHARS + 1))
    assert vault.get("a:env:TOKEN") == "kept"
    vault.set("a:env:TOKEN", "x" * MAX_SECRET_CHARS)
