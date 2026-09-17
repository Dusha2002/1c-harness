from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True, frozen=True)
class Message:
    role: str
    content: str


@dataclass(slots=True)
class LLMResponse:
    content: str
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    async def complete(self, messages: list[Message]) -> LLMResponse:
        """Generate one assistant response for a normalized message list."""
        ...


class ProviderError(RuntimeError):
    """Raised when an LLM provider cannot complete a request."""
