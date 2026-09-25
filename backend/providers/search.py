"""OpenAI web search adapter will be implemented in Phase 2."""


class UnconfiguredSearch:
    async def search(self, query: str) -> list[dict]:
        raise RuntimeError("Search provider is not configured in Phase 1")
