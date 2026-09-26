"""OpenAI Responses web search adapter; normalize URL-only action sources and citations."""

import os
import re
from urllib.parse import unquote, urlsplit

from openai import AsyncOpenAI

from ..research_sources import clean_url


def _path_title(url: str) -> str:
    slug = unquote(urlsplit(url).path.rstrip('/').split('/')[-1])
    slug = re.sub(r"[_-]+", " ", slug).strip()
    if not slug or re.fullmatch(r"(?:\d+|[0-9a-f]{12,}|(?:view|notice|article|read|detail)(?:\.do|\.html)?)", slug, re.I):
        return ""
    return slug[:120] if len(re.findall(r"[가-힣A-Za-z]", slug)) >= 4 else ""


def normalize_search_response(output: list[dict], text: str) -> dict:
    unique: dict[str, dict] = {}
    has_web_call = False
    for item in output:
        if item.get("type") == "web_search_call":
            has_web_call = True
            raw_sources = (item.get("action") or {}).get("sources") or []
            sources = [(source, "") for source in raw_sources]
        elif item.get("type") == "message":
            sources = []
            for part in item.get("content") or []:
                body = part.get("text") or text or ""
                for annotation in part.get("annotations") or []:
                    if annotation.get("type") != "url_citation":
                        continue
                    start = annotation.get("start_index", 0)
                    snippet = body[max(0, start - 190):min(len(body), annotation.get("end_index", start) + 90)] if isinstance(start, int) else ""
                    sources.append((annotation, snippet))
        else:
            continue
        for source, snippet in sources:
            url = source.get("url")
            if not url or urlsplit(url).scheme not in ("http", "https"):
                continue
            try:
                url = clean_url(url)
            except ValueError:
                continue
            item = unique.setdefault(url, {"url": url, "title": "", "snippet": "", "domain": urlsplit(url).hostname or "",
                                           "published_date": None, "title_origin": "MISSING"})
            title = (source.get("title") or "").strip()
            if title:
                item["title"], item["title_origin"] = title[:250], "CITATION"
            if snippet and len(snippet) > len(item["snippet"]):
                item["snippet"] = snippet[:450]
            date = source.get("published_date") or source.get("date")
            if date:
                item["published_date"] = str(date)[:30]
    for item in unique.values():
        if not item["title"]:
            item["title"] = _path_title(item["url"])
            if item["title"]:
                item["title_origin"] = "URL_PATH"
    return {"text": text, "sources": list(unique.values()), "web_search_executed": has_web_call,
            "provider": "OPENAI_WEB_SEARCH"}


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
            input=("아래 검색어의 지역·정책 주제·연도·숫자를 유지해 해당 소재를 검색하세요. "
                   "관련 없는 같은 지역의 다른 지원사업은 배제하세요. 원발행기관 공고, 정정공고, "
                   "보도자료와 첨부자료 URL을 찾으세요. 뉴스는 공식 원문 탐색 힌트로 활용하세요. "
                   "결과가 없다면 다른 정책으로 주제를 바꾸지 마세요.\n검색어: " + query),
        )
        self.usage["api_requests"] += 1
        if response.usage:
            self.usage["input_tokens"] += response.usage.input_tokens or 0
            self.usage["output_tokens"] += response.usage.output_tokens or 0
        normalized = normalize_search_response([item.model_dump(exclude_none=True) for item in response.output],
                                               response.output_text or "")
        if not normalized["web_search_executed"]:
            raise RuntimeError("웹 검색이 실행되지 않았습니다.")
        return normalized


class UnconfiguredSearch:
    async def search(self, query: str) -> dict:
        raise RuntimeError("Search provider is not configured")
