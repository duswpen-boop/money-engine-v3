"""Replaceable external service interfaces and provider selection."""

from .base import ImageProvider, LLMProvider, SearchProvider

__all__ = ["ImageProvider", "LLMProvider", "SearchProvider"]


def research_providers(api_key: str) -> tuple[LLMProvider, SearchProvider]:
    from .llm import OpenAILLM
    from .search import OpenAIWebSearch
    return OpenAILLM(api_key), OpenAIWebSearch(api_key)
