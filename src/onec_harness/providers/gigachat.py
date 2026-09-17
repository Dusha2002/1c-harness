from __future__ import annotations

import time
from uuid import uuid4

import httpx

from onec_harness.providers.base import LLMResponse, Message, ProviderError
from onec_harness.settings import Settings


class GigaChatProvider:
    """Small direct REST client for GigaChat with automatic access-token refresh."""

    def __init__(self, settings: Settings) -> None:
        if not settings.gigachat_credentials:
            raise ProviderError("GIGACHAT_CREDENTIALS is required for the GigaChat provider")
        self.settings = settings
        self._access_token: str | None = None
        self._expires_at = 0.0

    def _verify(self) -> bool | str:
        if self.settings.gigachat_ca_bundle:
            return str(self.settings.gigachat_ca_bundle)
        return self.settings.gigachat_verify_ssl

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
            raise ProviderError(f"GigaChat OAuth failed: {exc}") from exc

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
            raise ProviderError(f"GigaChat completion failed: {exc}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Unexpected GigaChat completion response") from exc
        return LLMResponse(content=content, raw=payload)
