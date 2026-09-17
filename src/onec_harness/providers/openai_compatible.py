from __future__ import annotations

import httpx

from onec_harness.providers.base import LLMResponse, Message, ProviderError


class OpenAICompatibleProvider:
    """Provider for OpenAI-style /chat/completions endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        max_tokens: int = 4096,
    ) -> None:
        if not base_url:
            raise ProviderError("An API base URL is required")
        if not api_key:
            raise ProviderError("An API key is required")
        if not model:
            raise ProviderError("LLM_MODEL is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    async def complete(self, messages: list[Message]) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "onec-harness/0.1.0",
        }
        body = {
            "model": self.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "max_tokens": self.max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI-compatible completion failed: {exc}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Unexpected OpenAI-compatible completion response") from exc
        return LLMResponse(content=content or "", raw=payload)
