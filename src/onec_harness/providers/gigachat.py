from __future__ import annotations

import hashlib
import ssl
import sys
import time
from uuid import uuid4

import httpx

from onec_harness.providers.base import LLMResponse, Message, ProviderError
from onec_harness.providers.russian_trusted_ca import (
    RUSSIAN_TRUSTED_ROOT_CA_PEM,
    RUSSIAN_TRUSTED_ROOT_CA_SHA256,
)
from onec_harness.settings import Settings


class GigaChatProvider:
    """Small direct REST client for GigaChat with automatic access-token refresh."""

    def __init__(self, settings: Settings) -> None:
        if not settings.gigachat_credentials:
            raise ProviderError("GIGACHAT_CREDENTIALS is required for the GigaChat provider")
        self.settings = settings
        self._access_token: str | None = None
        self._expires_at = 0.0

    def _system_ssl_context(self) -> ssl.SSLContext:
        if sys.platform == "win32":
            try:
                import truststore
            except ImportError:
                return ssl.create_default_context()
            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return ssl.create_default_context()

    @staticmethod
    def _verified_official_root() -> str:
        try:
            der = ssl.PEM_cert_to_DER_cert(RUSSIAN_TRUSTED_ROOT_CA_PEM)
        except ValueError as exc:
            raise ProviderError("Bundled GigaChat root CA is malformed") from exc
        digest = hashlib.sha256(der).hexdigest()
        if digest != RUSSIAN_TRUSTED_ROOT_CA_SHA256:
            raise ProviderError("Bundled GigaChat root CA fingerprint mismatch")
        return RUSSIAN_TRUSTED_ROOT_CA_PEM

    def _verify(self) -> bool | ssl.SSLContext:
        if not self.settings.gigachat_verify_ssl:
            return False

        context = self._system_ssl_context()
        try:
            # GigaChat officially requires the Russian Trusted Root CA. Load the
            # pinned root at application level so users do not need to install it
            # machine-wide just to use Harness.
            context.load_verify_locations(cadata=self._verified_official_root())
            if self.settings.gigachat_ca_bundle:
                context.load_verify_locations(cafile=str(self.settings.gigachat_ca_bundle))
        except (OSError, ssl.SSLError) as exc:
            raise ProviderError(f"Could not load GigaChat CA certificates: {exc}") from exc
        return context

    def _tls_error(self, operation: str, exc: httpx.HTTPError) -> ProviderError:
        message = str(exc)
        if "CERTIFICATE_VERIFY_FAILED" in message or "certificate verify failed" in message.casefold():
            if sys.platform == "win32" and not self.settings.gigachat_ca_bundle:
                return ProviderError(
                    f"{operation}: TLS verification failed after loading both the Windows trust store and the pinned "
                    "Russian Trusted Root CA required by GigaChat. This usually means HTTPS interception by an antivirus, "
                    "proxy or corporate network. Add that interceptor's root PEM/CRT in Advanced settings → GigaChat CA "
                    "certificate. SSL verification is intentionally not disabled automatically. "
                    f"Original error: {exc}"
                )
            return ProviderError(
                f"{operation}: TLS certificate verification failed. Check the configured GigaChat CA certificate. "
                f"Original error: {exc}"
            )
        return ProviderError(f"{operation}: {exc}")

    async def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 30:
            return self._access_token

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "RqUID": str(uuid4()),
            "Authorization": f"Basic {self.settings.gigachat_credentials}",
            "User-Agent": "onec-harness/0.1.0",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.llm_timeout_seconds,
                verify=self._verify(),
            ) as client:
                response = await client.post(
                    self.settings.gigachat_auth_url,
                    headers=headers,
                    data={"scope": self.settings.gigachat_scope},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise self._tls_error("GigaChat OAuth failed", exc) from exc

        token = payload.get("access_token")
        if not token:
            raise ProviderError("GigaChat OAuth response does not contain access_token")

        expires_at = float(payload.get("expires_at") or (time.time() + 25 * 60))
        # Some API versions serialize epoch milliseconds, others epoch seconds.
        if expires_at > 10_000_000_000:
            expires_at /= 1000
        self._access_token = token
        self._expires_at = expires_at
        return token

    async def complete(self, messages: list[Message]) -> LLMResponse:
        token = await self._get_access_token()
        url = f"{self.settings.gigachat_base_url.rstrip('/')}/chat/completions"
        body = {
            "model": self.settings.llm_model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "max_tokens": self.settings.llm_max_tokens,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "onec-harness/0.1.0",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.llm_timeout_seconds,
                verify=self._verify(),
            ) as client:
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise self._tls_error("GigaChat completion failed", exc) from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Unexpected GigaChat completion response") from exc
        return LLMResponse(content=content, raw=payload)
