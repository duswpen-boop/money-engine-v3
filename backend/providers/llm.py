"""Structured extraction provider; no API credential is embedded in code."""

import json
import os
from openai import AsyncOpenAI

from .base import LLMProvider


class OpenAILLM:
    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key, timeout=90, max_retries=1)
        self.model = os.getenv("MONEY_ENGINE_RESEARCH_MODEL", "gpt-4.1-mini")

    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        response = await self.client.responses.create(
            model=self.model,
            input=prompt,
            text={"format": {"type": "json_schema", "name": "research_data", "strict": True, "schema": schema}},
        )
        if not response.output_text:
            raise RuntimeError("구조화된 분석 결과를 받지 못했습니다.")
        return json.loads(response.output_text)


class UnconfiguredLLM:
    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        raise RuntimeError("LLM provider is not configured")
