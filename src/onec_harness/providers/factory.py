from __future__ import annotations

from onec_harness.providers.anthropic import AnthropicProvider
from onec_harness.providers.base import LLMProvider, ProviderError
from onec_harness.providers.gigachat import GigaChatProvider
from onec_harness.providers.openai_compatible import OpenAICompatibleProvider
from onec_harness.settings import Settings


def create_provider(settings: Settings) -> LLMProvider:
    name = settings.provider_name

    if name == "gigachat":
        return GigaChatProvider(settings)

    if name in {"openai", "deepseek", "openai_compatible", "compatible"}:
        if not settings.llm_api_key:
            raise ProviderError("LLM_API_KEY is required for this provider")
        if name == "openai":
            base_url = settings.openai_base_url
        elif name == "deepseek":
            base_url = settings.deepseek_base_url
        else:
            base_url = settings.openai_compatible_base_url or ""
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
        )

    if name == "anthropic":
        return AnthropicProvider(
            base_url=settings.anthropic_base_url,
            api_key=settings.anthropic_api_key or "",
            model=settings.llm_model,
            anthropic_version=settings.anthropic_version,
            timeout=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
        )

    raise ProviderError(
        f"Unknown LLM_PROVIDER={settings.llm_provider!r}. "
        "Use gigachat, openai, deepseek, openai_compatible or anthropic."
    )
