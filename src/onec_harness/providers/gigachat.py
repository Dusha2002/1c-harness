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
        self._resolved_scope: str | None = None

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

    @staticmethod
    def _normalized_credentials(raw: str) -> str:
        value = raw.strip().strip('"').strip("'")
        lowered = value.casefold()
        if lowered.startswith("basic "):
            value = value[6:]
        elif lowered.startswith("bearer "):
            value = value[7:]
        # Authorization keys are Base64-like strings and should not contain
        # whitespace; this also fixes keys copied with an accidental newline.
        return "".join(value.split())

    @staticmethod
    def _oauth_error_payload(response: httpx.Response) -> tuple[int | None, str]:
        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            return None, text[:500] or response.reason_phrase
        if isinstance(payload, dict):
            code = payload.get("code")
            try:
                numeric_code = int(code) if code is not None else None
            except (TypeError, ValueError):
                numeric_code = None
            message = str(payload.get("message") or payload.get("error") or payload)
            return numeric_code, message[:500]
        return None, str(payload)[:500]

    def _scope_candidates(self) -> list[str]:
        allowed = ["GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"]
        configured = self.settings.gigachat_scope.strip().upper()
        if configured in allowed:
            return [configured, *[scope for scope in allowed if scope != configured]]
        return allowed

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

        credentials = self._normalized_credentials(self.settings.gigachat_credentials or "")
        if not credentials:
            raise ProviderError("GigaChat Authorization Key is empty")

        scopes = [self._resolved_scope] if self._resolved_scope else self._scope_candidates()
        last_error: tuple[int | None, str, int, str] | None = None

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.llm_timeout_seconds,
                verify=self._verify(),
            ) as client:
                for scope in scopes:
                    headers = {
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "RqUID": str(uuid4()),
                        "Authorization": f"Basic {credentials}",
                        "User-Agent": "onec-harness/0.5.0",
                    }
                    response = await client.post(
                        self.settings.gigachat_auth_url,
                        headers=headers,
                        data={"scope": scope},
                    )
                    if response.is_success:
                        payload = response.json()
                        token = payload.get("access_token")
                        if not token:
                            raise ProviderError("GigaChat OAuth response does not contain access_token")
                        expires_at = float(payload.get("expires_at") or (time.time() + 25 * 60))
                        if expires_at > 10_000_000_000:
                            expires_at /= 1000
                        self._access_token = token
                        self._expires_at = expires_at
                        self._resolved_scope = scope
                        return token

                    code, message = self._oauth_error_payload(response)
                    last_error = (code, message, response.status_code, scope)

                    # Official GigaChat errors 7/6 indicate a mismatch between
                    # Authorization Key and scope. Try the other documented API
                    # scopes automatically so personal/business users do not
                    # need to know this detail during setup.
                    if code in {6, 7}:
                        continue
                    break
        except httpx.HTTPError as exc:
            raise self._tls_error("GigaChat OAuth failed", exc) from exc

        if last_error is None:
            raise ProviderError("GigaChat OAuth failed without a response")

        code, message, status, scope = last_error
        detail = f"code={code}, message={message}" if code is not None else message
        if code == 4:
            detail += ". Проверьте Authorization Key: Harness принимает как сам ключ, так и строку с префиксом Basic."
        elif code in {6, 7}:
            detail += ". Ключ не подошёл ни к PERS, ни к B2B, ни к CORP; возможно, он устарел или перевыпущен."
        elif code in {1, 5}:
            detail += ". Проверьте тип API/scope в личном кабинете GigaChat."
        raise ProviderError(
            f"GigaChat OAuth failed: HTTP {status}; {detail}; last scope={scope}"
        )

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
