"""OpenAI LLM adapter will be implemented in Phase 2/3."""

from .base import LLMProvider


class UnconfiguredLLM:
    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        raise RuntimeError("LLM provider is not configured in Phase 1")
