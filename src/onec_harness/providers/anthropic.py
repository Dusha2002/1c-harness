from __future__ import annotations

import httpx

from onec_harness.providers.base import LLMResponse, Message, ProviderError


class AnthropicProvider:
    """Minimal adapter for Anthropic Messages API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        anthropic_version: str,
        timeout: float = 120.0,
        max_tokens: int = 4096,
    ) -> None:
        if not api_key:
            raise ProviderError("ANTHROPIC_API_KEY is required")
        if not model:
            raise ProviderError("LLM_MODEL is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.anthropic_version = anthropic_version
        self.timeout = timeout
        self.max_tokens = max_tokens

    async def complete(self, messages: list[Message]) -> LLMResponse:
        system_parts = [message.content for message in messages if message.role == "system"]
        api_messages = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"user", "assistant"}
        ]
        body: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": api_messages,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "Content-Type": "application/json",
            "User-Agent": "onec-harness/0.1.0",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic completion failed: {exc}") from exc

        blocks = payload.get("content", [])
        text = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text:
            raise ProviderError("Unexpected Anthropic Messages response")
        return LLMResponse(content=text, raw=payload)
