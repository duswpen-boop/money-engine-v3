from typing import Protocol


class LLMProvider(Protocol):
    async def generate_structured(self, prompt: str, schema: dict) -> dict: ...


class ImageProvider(Protocol):
    async def generate(self, prompt: str, output_path: str) -> str: ...


class SearchProvider(Protocol):
    async def search(self, query: str) -> dict: ...
