"""Large provider credentials stay entirely in Windows Credential Manager.

Service-account JSON exceeds a single Windows generic credential's blob limit.
Small keys retain the existing credential name; larger values use vault chunks
and a small manifest, with no files or database copies of the secret.
"""

import json
import re
import uuid

from ..secrets import CredentialKeyStore

PREFIX = "raphael-vault-v1:"


class ProviderCredentialStore:
    def __init__(self, username):
        self.username = username
        self.root = CredentialKeyStore(username=username)

    def _parts(self, value):
        if not value or not value.startswith(PREFIX):
            return []
        try:
            manifest = json.loads(value[len(PREFIX):])
            token, count = manifest["token"], manifest["count"]
            if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token) or type(count) is not int or not 1 <= count <= 128:
                raise ValueError
            return [CredentialKeyStore(username=f"{self.username}:{token}:{index}") for index in range(count)]
        except (ValueError, KeyError, TypeError):
            raise ValueError("Invalid provider credential manifest") from None

    def get(self):
        value = self.root.get()
        parts = self._parts(value)
        if not parts:
            return value
        chunks = [part.get() for part in parts]
        if any(chunk is None for chunk in chunks):
            raise ValueError("Incomplete provider credential")
        return "".join(chunks)

    def set(self, value):
        old = self.root.get()
        old_parts = self._parts(old)
        new_parts = []
        try:
            if len(value.encode("utf-16-le")) <= 2000 and not value.startswith(PREFIX):
                self.root.set(value)
            else:
                chunks = [value[i:i + 500] for i in range(0, len(value), 500)]
                if not 1 <= len(chunks) <= 128:
                    raise ValueError("Provider credential exceeds vault capacity")
                manifest = PREFIX + json.dumps({"token": uuid.uuid4().hex, "count": len(chunks)})
                new_parts = self._parts(manifest)
                for part, chunk in zip(new_parts, chunks, strict=True):
                    part.set(chunk)
                self.root.set(manifest)
        except Exception:
            for part in new_parts:
                part.clear()
            if old:
                self.root.set(old)
            else:
                self.root.clear()
            raise
        for part in old_parts:
            part.clear()

    def clear(self):
        for part in self._parts(self.root.get()):
            part.clear()
        self.root.clear()
