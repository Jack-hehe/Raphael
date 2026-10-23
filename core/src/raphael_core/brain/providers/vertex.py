"""Vertex service-account OAuth, using an installed RSA implementation only."""

import asyncio
import base64
import json
import time

import httpx

from ..openrouter import OpenRouterError
from .clients import CompatibleProvider, model_info

TOKEN_URL = "https://oauth2.googleapis.com/token"


class VertexProvider(CompatibleProvider):
    def __init__(self, *args, clock=time.time, **kwargs):
        super().__init__(*args, **kwargs)
        self._clock = clock
        self._token = None
        self._expires = 0
        self._token_lock = asyncio.Lock()

    async def _access_token(self, timeout_s):
        async with self._token_lock:
            if self._token and self._clock() < self._expires - 60:
                return self._token
            try:
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import padding, rsa
                account = json.loads(self._key_provider() or "{}")
                key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
                if not isinstance(key, rsa.RSAPrivateKey) or not isinstance(account["client_email"], str):
                    raise ValueError
                issued = int(self._clock())
                def encode(value):
                    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=")
                header = encode({"alg": "RS256", "typ": "JWT"})
                claims = encode({"iss": account["client_email"], "scope": "https://www.googleapis.com/auth/cloud-platform",
                                 "aud": TOKEN_URL, "iat": issued, "exp": issued + 3600})
                unsigned = header + b"." + claims
                signature = key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
                assertion = unsigned + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")
            except ImportError:
                raise OpenRouterError(0, "unsupported", "Vertex requires the installed cryptography package") from None
            except Exception:
                raise OpenRouterError(0, "auth", "Invalid Vertex service account") from None
            try:
                # Never trust a token_uri from pasted JSON: a private assertion goes only to Google.
                response = await self._client().post(TOKEN_URL, data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion.decode()},
                    timeout=timeout_s, follow_redirects=False)
            except httpx.TimeoutException:
                raise OpenRouterError(0, "timeout", "Vertex authentication timed out") from None
            except httpx.TransportError:
                raise OpenRouterError(0, "offline", "Vertex authentication is offline") from None
            if not response.is_success:
                kind = "rate_limit" if response.status_code == 429 else "server" if response.status_code >= 500 else "auth"
                raise OpenRouterError(response.status_code, kind, "Vertex authentication failed")
            try:
                payload = response.json()
                token, lifetime = payload["access_token"], payload["expires_in"]
                if (not isinstance(token, str) or not token or any(not 33 <= ord(c) <= 126 for c in token)
                        or type(lifetime) not in (int, float) or not 60 < lifetime <= 3600):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise OpenRouterError(0, "auth", "Invalid Vertex token response") from None
            self._token, self._expires = token, self._clock() + lifetime
            return token

    def _headers(self):
        return {"Authorization": f"Bearer {self._token}"}

    async def _send(self, *args, **kwargs):
        timeout_s = args[2] if len(args) > 2 else kwargs["timeout_s"]
        await self._access_token(timeout_s)
        try:
            return await super()._send(*args, **kwargs)
        except OpenRouterError as error:
            if error.kind == "auth":
                self._token, self._expires = None, 0
            raise

    async def models(self, timeout_s=5):
        await self._access_token(timeout_s)
        # Publisher models do not have a portable OpenAI /models API. This is a
        # conservative static Gemini list, not a claim of regional entitlement.
        return [{**model_info({"id": model, "context_length": 1_048_576}, kind="google"),
                 "discovery": "static"} for model in ("google/gemini-2.5-flash", "google/gemini-2.5-pro")]
