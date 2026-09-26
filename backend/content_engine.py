"""Search opportunity and writing stages consume verified research, never raw claims."""

import json
import re
from difflib import SequenceMatcher

from .db import connection
from .quality import safe_title, sanitize_text, verified_values
from .research_sources import clean_url


def schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
DEMAND_SCHEMA = schema({"demand_level": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"]},
                        "signals": STRINGS, "reason": STRING})
SERP_SCHEMA = schema({"competition": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "UNKNOWN"]},
                      "opportunity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                      "result_mix": STRINGS, "content_gap": STRINGS})
INTENT_SCHEMA = schema({"axes": {"type": "array", "items": {"type": "string", "enum":
                                 ["WHO", "MONEY", "WHEN", "WHERE", "HOW", "DOCUMENT", "RISK", "AFTER"]}},
                        "questions": STRINGS, "h2_outline": STRINGS})
KEYWORD_SCHEMA = schema({"primary_keyword": STRING, "secondary_keywords": STRINGS,
                         "long_tail_keywords": STRINGS, "watch_queries": STRINGS, "image_slug": STRING})
VALUE_SCHEMA = schema({"ideas": {"type": "array", "items": schema({"kind": STRING, "fact_keys": STRINGS,
                                                                         "description": STRING})}})
WRITE_SCHEMA = schema({"title_candidate": STRING, "meta_description": STRING, "body": STRING})
TAG_SCHEMA = schema({"tags": STRINGS})


def _facts(content: dict) -> dict:
    return {key: {"value": fact["value"], "source_url": fact.get("source_url"),
                  "evidence": fact.get("evidence")}
            for key, fact in (content.get("research") or {}).get("facts", {}).items()
            if fact.get("status") == "VERIFIED" and fact.get("value")}


def _compact(content: dict, outputs: dict, *stages: str) -> str:
    return json.dumps({"verified_facts": _facts(content),
                       "topic_identity": ((content.get("research") or {}).get("parsed") or {}).get("topic_identity"),
                       "unknown_fields": [key for key, fact in (content.get("research") or {}).get("facts", {}).items()
                                          if fact.get("status") != "VERIFIED"],
                       "official_url": ((content.get("research") or {}).get("summary") or {}).get("official_url"),
                       **{name: outputs.get(name, {}) for name in stages}}, ensure_ascii=False)


async def search_demand(content: dict, search, llm) -> dict:
    facts = verified_values(content.get("research") or {})
    phrase = " ".join(str(facts.get(key) or "") for key in ("region", "program_name")).strip()
    if not phrase:
        phrase = ((content.get("research") or {}).get("parsed") or {}).get("search_query") or "지원 공고"
    response = await search.search(phrase[:180] + " 신청 대상 지원금 방법 최신")
    sources = []
    for item in response.get("sources", [])[:10]:
        try:
            sources.append({"title": item.get("title", ""), "url": clean_url(item["url"])})
        except (KeyError, ValueError):
            continue
    assessment = await llm.generate_structured(
        "실제 검색량 데이터는 없습니다. 정확한 월 검색량 숫자를 만들지 마세요. 아래 검색 출처와 주제만으로 수요를"
        " HIGH/MEDIUM/LOW/UNKNOWN으로 보수적으로 판단하고 신호를 설명하세요. 출처가 부족하면 UNKNOWN.\n"
        + json.dumps({"query": phrase, "sources": sources, "search_notes": response.get("text", "")[:1800]}, ensure_ascii=False),
        DEMAND_SCHEMA)
    return {"demand_level": assessment["demand_level"], "signals": assessment["signals"][:8],
            "reason": assessment["reason"], "observed_sources": sources,
            "search_notes": response.get("text", "")[:1800]}


async def serp_opportunity(content: dict, demand: dict, llm) -> dict:
    sources = demand.get("observed_sources", [])
    if not sources:
        return {"competition": "UNKNOWN", "opportunity": "LOW", "result_mix": [],
                "content_gap": ["검색자료가 부족해 SERP를 판단할 수 없음"]}
    result = await llm.generate_structured(
        "이 목록은 웹 검색에서 관찰한 출처이며 Google 순위나 정확한 경쟁 수치는 아닙니다. "
        "공식기관/언론/블로그 분포와 검색자의 미해결 질문을 분석하세요. 출처가 부족하면 competition UNKNOWN. "
        "검색결과 개수만으로 경쟁도를 판단하지 마세요.\n"
        + _compact(content, {"SEARCH_DEMAND": demand}, "SEARCH_DEMAND"), SERP_SCHEMA)
    return {"competition": result["competition"], "opportunity": result["opportunity"],
            "result_mix": result["result_mix"][:8], "content_gap": result["content_gap"][:8]}


async def intent(content: dict, outputs: dict, llm) -> dict:
    result = await llm.generate_structured(
        "정책 종류에 맞춰 서로 다른 검색의도와 H2 순서를 만드세요. 필요 없는 축은 제외합니다. "
        "공식 확인되지 않은 항목은 질문으로만 남기고 사실처럼 쓰지 마세요.\n"
        + _compact(content, outputs, "SERP"), INTENT_SCHEMA)
    axes = list(dict.fromkeys(x for x in result["axes"] if x in
                             {"WHO", "MONEY", "WHEN", "WHERE", "HOW", "DOCUMENT", "RISK", "AFTER"}))
    return {"axes": axes, "questions": result["questions"][:12], "h2_outline": result["h2_outline"][:9]}


def duplicate_check(content: dict, intent_data: dict) -> dict:
    with connection() as db:
        existing = [dict(row) for row in db.execute(
            "SELECT c.id,c.title,c.region,c.program_name,c.primary_keyword,c.published_url,"
            "o.result_json AS intent_json FROM contents c LEFT JOIN pipeline_outputs o "
            "ON o.content_id=c.id AND o.step='SEARCH_INTENT' WHERE c.id<>? "
            "ORDER BY c.updated_at DESC LIMIT 200",
            (content["id"],))]
    best, score = None, 0.0
    for row in existing:
        same_program = bool(content.get("program_name") and row["program_name"] and
                            content["program_name"] == row["program_name"])
        same_region = bool(content.get("region") and content["region"] == row["region"])
        title_score = SequenceMatcher(None, content["title"], row["title"]).ratio()
        current_axes = set(intent_data.get("axes", []))
        old_axes = set(json.loads(row["intent_json"]).get("axes", [])) if row["intent_json"] else set()
        intent_overlap = len(current_axes & old_axes) / len(current_axes | old_axes) if current_axes | old_axes else 0
        candidate = (0.48 if same_program and same_region else 0.22 if same_program else 0) + 0.3 * title_score + 0.22 * intent_overlap
        if candidate > score:
            best, score = row, candidate
    if best is None or score < 0.35:
        return {"decision": "NEW", "existing_id": None, "existing_url": None, "similarity": round(score, 2)}
    decision = "UPDATE_EXISTING" if score >= 0.75 else "POSSIBLE_CANNIBALIZATION" if score >= 0.55 else "EXPAND_EXISTING"
    return {"decision": decision, "existing_id": best["id"],
            "existing_url": best["published_url"] or None, "similarity": round(score, 2)}


async def keyword_map(content: dict, outputs: dict, llm) -> dict:
    result = await llm.generate_structured(
        "검증된 사업명과 검색의도에서 파생한 정확한 한국어 검색어를 만드세요. 과도한 반복, 확인되지 않은 금액·날짜 금지. "
        "image_slug는 영문 소문자와 하이픈으로 2~7단어.\n"
        + _compact(content, outputs, "SEARCH_INTENT", "SERP"), KEYWORD_SCHEMA)
    clean = lambda xs, n: list(dict.fromkeys(str(x).strip()[:80] for x in xs if str(x).strip()))[:n]
    program = verified_values(content.get("research") or {}).get("program_name") or ""
    region = verified_values(content.get("research") or {}).get("region") or ""
    primary = str(result["primary_keyword"])[:90]
    if program and program.replace(" ", "") not in primary.replace(" ", ""):
        primary = f"{region} {program}".strip()[:90]
    return {"primary_keyword": primary,
            "secondary_keywords": clean(result["secondary_keywords"], 7),
            "long_tail_keywords": clean(result["long_tail_keywords"], 12),
            "watch_queries": clean(result["watch_queries"], 12),
            "image_slug": result["image_slug"]}


async def value_add(content: dict, outputs: dict, llm) -> dict:
    result = await llm.generate_structured(
        "공식 근거가 있는 행동 도움만 설계하세요. 계산은 공식 계산식이 명시된 경우에만 하세요. "
        "각 idea의 fact_keys는 verified_facts에 있는 키만 적으세요. 근거 없는 아이디어는 제외.\n"
        + _compact(content, outputs, "SEARCH_INTENT", "KEYWORD_MAP"), VALUE_SCHEMA)
    verified = set(_facts(content))
    return {"ideas": [idea for idea in result["ideas"][:6]
                      if idea.get("fact_keys") and set(idea["fact_keys"]) <= verified]}


async def write_content(content: dict, outputs: dict, llm) -> dict:
    result = await llm.generate_structured(
        "티스토리용 한국어 본문 Markdown을 작성하세요. H1, 자연스러운 lead, 핵심 정보, 공식 확인경로를 포함합니다. "
        "ARTICLE_PLAN.sections의 H2만 쓰고 각 섹션에 지정된 VERIFIED fact의 구체적인 값을 명시하세요. "
        "자료 없는 H2와 일반적인 조언은 만들지 마세요. "
        "오직 verified_facts의 사실만 확정적으로 쓰고 원문에 없는 날짜·한도·대상을 만들지 마세요. "
        "UNKNOWN 항목은 확정 문장을 만들지 마세요. 공식 URL은 주어진 것만 사용. "
        "meta_description은 본문과 별개 필드로, 본문에 Description:을 적지 마세요. "
        "'확인된 구체적 정보 없음' 같은 UNKNOWN 문장으로 분량을 채우지 마세요.\n"
        + _compact(content, outputs, "ARTICLE_PLAN", "KEYWORD_MAP", "VALUE_ADD"), WRITE_SCHEMA)
    body, removed = sanitize_text(result["body"], content.get("research") or {})
    return {"title": safe_title(content.get("research") or {}, outputs.get("KEYWORD_MAP", {}).get("primary_keyword", "")),
            "meta_description": re.sub(r"(?i)^(?:meta\s*)?description\s*:\s*", "", result["meta_description"]).strip()[:180],
            "body": body, "sanitizer_warnings": removed}


async def tags(content: dict, outputs: dict, llm) -> dict:
    result = await llm.generate_structured(
        "티스토리용 관련 태그 8~12개만 만드세요. 지역, 확인된 사업명, 검색의도와 키워드에 직접 연결해야 합니다. "
        "확인 안 된 날짜/금액과 무관한 인기 태그는 빼세요.\n"
        + _compact(content, outputs, "KEYWORD_MAP", "SEARCH_INTENT"), TAG_SCHEMA)
    cleaned = list(dict.fromkeys(re.sub(r"[#\n,]", "", str(tag)).strip()[:35]
                                 for tag in result["tags"] if str(tag).strip()))[:12]
    return {"tags": cleaned}


def cluster(content: dict, outputs: dict) -> dict:
    dupe = outputs.get("DUPLICATE_CHECK", {})
    existing = []
    if dupe.get("existing_url"):
        try:
            existing = [{"id": dupe["existing_id"], "url": clean_url(dupe["existing_url"])}]
        except ValueError:
            pass
    return {"PILLAR": None, "CURRENT": content["id"], "LIVE_CLUSTER": existing,
            "EVERGREEN_CLUSTER": [], "EXISTING": existing, "PLANNED": [],
            "next_content": None, "watch_queries": outputs.get("KEYWORD_MAP", {}).get("watch_queries", []),
            "update_date": (content.get("research") or {}).get("facts", {}).get("application_end", {}).get("value"),
            "expand_trigger": "검색자의 별도 질문이 확인되면 확장" if outputs.get("SERP", {}).get("content_gap") else None}
