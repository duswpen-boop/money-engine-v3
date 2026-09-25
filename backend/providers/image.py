"""Image adapter will be implemented in Phase 4."""


class UnconfiguredImage:
    async def generate(self, prompt: str, output_path: str) -> str:
        raise RuntimeError("Image provider is not configured in Phase 1")
