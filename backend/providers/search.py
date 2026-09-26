"""Search provider: OpenAI Responses web search; replaceable by another adapter."""

import os
from openai import AsyncOpenAI


class OpenAIWebSearch:
    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key, timeout=90, max_retries=1)
        self.model = os.getenv("MONEY_ENGINE_RESEARCH_MODEL", "gpt-4.1-mini")
        self.usage = {"api_requests": 0, "input_tokens": 0, "output_tokens": 0}

    async def search(self, query: str) -> dict:
        response = await self.client.responses.create(
            model=self.model,
            tools=[{"type": "web_search", "search_context_size": "medium"}],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            input=("한국의 최근 정책/공고를 조사합니다. 원발행기관 공고, 정정공고, 보도자료, 시행기관, "
                   "첨부자료를 찾고 URL과 공표일을 명시하세요. 입력 주장이 사실인지 아직 단정하지 마세요.\n" + query),
        )
        self.usage["api_requests"] += 1
        if response.usage:
            self.usage["input_tokens"] += response.usage.input_tokens or 0
            self.usage["output_tokens"] += response.usage.output_tokens or 0
        sources = []
        for item in response.output:
            raw = item.model_dump(exclude_none=True)
            if raw.get("type") == "web_search_call":
                sources.extend(raw.get("action", {}).get("sources", []))
            if raw.get("type") == "message":
                for part in raw.get("content", []):
                    for annotation in part.get("annotations", []):
                        if annotation.get("type") == "url_citation":
                            sources.append(annotation)
        unique = {}
        for source in sources:
            url = source.get("url")
            if url:
                unique[url] = {"url": url, "title": source.get("title", "")}
        if not any(item.type == "web_search_call" for item in response.output):
            raise RuntimeError("웹 검색이 실행되지 않았습니다.")
        return {"text": response.output_text, "sources": list(unique.values())}


class UnconfiguredSearch:
    async def search(self, query: str) -> dict:
        raise RuntimeError("Search provider is not configured")
