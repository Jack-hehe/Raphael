"""Use the OS vault locally and an ephemeral Actions secret on hosted CI only."""

import os

from ..brain.secrets import CredentialKeyStore


def key_provider():
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return os.environ.get("OPENROUTER_API_KEY")
    return CredentialKeyStore().get()
