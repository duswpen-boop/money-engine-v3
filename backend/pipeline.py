"""Resumable pipeline. Each completed stage is persisted before the next starts."""

import asyncio
import logging
from pathlib import Path

from .content_engine import (cluster, duplicate_check, intent, keyword_map, search_demand,
                             serp_opportunity, tags, value_add, write_content)
from .article_plan import make_article_plan
from .gates import article_issues, research_gate
from .image_planner import duplicate_image, image_scene_plan
from .credentials import get_openai_key
from .paths import images_dir
from .providers import research_providers
from .providers.image import OpenAIImage
from .quality import quality_issues, safe_title, sanitize_text
from .research import run_research
from .store import (add_usage, claim_research, get_content, save_output, set_run_status,
                    set_step, update_content, update_image)

def image_specs(content: dict) -> list[dict]:
    return image_scene_plan(content)["images"]


async def generate_image(content_id: str, slot: int, provider=None) -> bool:
    if slot not in range(1, 4):
        raise ValueError("지원하지 않는 이미지 슬롯입니다.")
    content = get_content(content_id)
    if not content:
        raise ValueError("작업을 찾을 수 없습니다.")
    spec = image_specs(content)[slot - 1]
    path = images_dir() / content_id / spec["filename"]
    candidate = path.with_name(path.stem + ".candidate.webp")
    update_image(content_id, slot, "RUNNING", prompt=spec["prompt"], scene=spec["scene"], error=None)
    try:
        if provider is None:
            key = get_openai_key()
            if not key:
                raise RuntimeError("SETTINGS에서 OpenAI API 키를 저장하세요.")
            provider = OpenAIImage(key)
        for attempt in range(2):
            prompt = spec["prompt"] + (" Previous image did not pass relevance or visual QA. Use a clearly different candid composition." if attempt else "")
            await provider.generate(prompt, str(candidate))
            if not candidate.is_file() or candidate.stat().st_size < 100:
                raise RuntimeError("생성된 이미지 파일을 확인할 수 없습니다.")
            others = [row["file_path"] for row in get_content(content_id)["images"]
                      if row["slot"] != slot and row["status"] == "COMPLETED" and row.get("file_path")]
            if duplicate_image(str(candidate), others):
                assessment = {"passed": False, "reason": "다른 이미지와 장면이 중복됨"}
            elif not hasattr(provider, "assess"):
                assessment = {"passed": False, "reason": "이미지 시각 QA 공급자가 없음"}
            else:
                assessment = await provider.assess(str(candidate), spec)
            save_output(content_id, f"IMAGE_QA_{slot}", {"attempt": attempt + 1, **assessment,
                                                            "related_section": spec["related_section"]})
            if assessment.get("passed"):
                candidate.replace(path)
                update_image(content_id, slot, "COMPLETED", filename=spec["filename"], file_path=str(path),
                             insert_after=spec["insert_after"], error=None)
                return True
        raise RuntimeError("이미지 QA 실패: " + str(assessment.get("reason", "주제 관련성 부족")))
    except Exception as exc:
        logging.exception("Image %d failed for %s", slot, content_id)
        update_image(content_id, slot, "FAILED", error=str(exc)[:300])
        return False
    finally:
        candidate.unlink(missing_ok=True)


async def finish_package(content_id: str):
    content = get_content(content_id)
    body, removed = sanitize_text(content.get("body") or "", content.get("research") or {})
    if body != content.get("body"):
        update_content(content_id, body=body)
    refreshed = get_content(content_id)
    issues = quality_issues(refreshed, require_images=True) + article_issues(refreshed) + removed
    if any(not refreshed["outputs"].get(f"IMAGE_QA_{slot}", {}).get("passed") for slot in range(1, 4)):
        issues.append("이미지 시각 QA 미통과")
    if research_gate(refreshed)["status"] == "CONTENT_BLOCKED":
        issues.append("Research 핵심 사실 부족 또는 충돌")
    issues = list(dict.fromkeys(issues))
    decision = "REVIEW_REQUIRED" if issues else "PUBLISH_READY"
    update_content(content_id, publish_decision=decision, grade="B" if issues else "A")
    save_output(content_id, "FINAL_SANITIZE", {"issues": issues, "changed": body != content.get("body")})
    set_step(content_id, "FINAL_SANITIZE", "COMPLETED", "; ".join(issues)[:400] or None)
    save_output(content_id, "FINAL_PACKAGE", {"decision": decision, "issues": issues,
                                                 "post_publish": "발행 후 공식 정정공고와 신청 마감일을 재확인하세요."})
    set_step(content_id, "FINAL_PACKAGE", "COMPLETED")


async def run_pipeline(content_id: str, *, llm=None, search=None, image=None):
    content = get_content(content_id)
    if content is None or content["run"]["status"] == "RUNNING":
        return
    current = "PARSE"
    try:
        if llm is None or search is None:
            key = get_openai_key()
            if not key:
                if next(s for s in content["steps"] if s["step"] == "VERIFY")["status"] != "COMPLETED":
                    await run_research(content_id)
                else:
                    set_step(content_id, "SEARCH_DEMAND", "FAILED", "SETTINGS에서 OpenAI API 키를 저장하세요.")
                return
            default_llm, default_search = research_providers(key)
            llm = llm or default_llm
            search = search or default_search
        if next(s for s in content["steps"] if s["step"] == "VERIFY")["status"] != "COMPLETED":
            await run_research(content_id, llm=llm, search=search, finish_run=False)
            content = get_content(content_id)
            if next(s for s in content["steps"] if s["step"] == "VERIFY")["status"] != "COMPLETED":
                return
        elif not claim_research(content_id):
            return
        content = get_content(content_id)
        gate = research_gate(content)
        save_output(content_id, "RESEARCH_GATE", gate)
        if gate["status"] == "CONTENT_BLOCKED":
            update_content(content_id, publish_decision="REVIEW_REQUIRED", grade="BLOCKED",
                           body=None, meta_description=None, tags=[])
            set_run_status(content_id, "COMPLETED")
            return
        stages = ["SEARCH_DEMAND", "SERP", "SEARCH_INTENT", "DUPLICATE_CHECK", "KEYWORD_MAP",
                  "VALUE_ADD", "WRITE", "QUALITY_GATE", "TAG", "IMAGE", "FINAL_SANITIZE"]
        for current in stages:
            content = get_content(content_id)
            if next(s for s in content["steps"] if s["step"] == current)["status"] == "COMPLETED":
                continue
            outputs = content["outputs"]
            set_step(content_id, current, "RUNNING")
            if current == "SEARCH_DEMAND":
                result = await search_demand(content, search, llm)
            elif current == "SERP":
                result = await serp_opportunity(content, outputs["SEARCH_DEMAND"], llm)
            elif current == "SEARCH_INTENT":
                result = await intent(content, outputs, llm)
            elif current == "DUPLICATE_CHECK":
                result = duplicate_check(content, outputs["SEARCH_INTENT"])
            elif current == "KEYWORD_MAP":
                result = await keyword_map(content, outputs, llm)
                update_content(content_id, primary_keyword=result["primary_keyword"],
                               secondary_keywords=result["secondary_keywords"],
                               watch_keywords=result["watch_queries"])
            elif current == "VALUE_ADD":
                result = await value_add(content, outputs, llm)
            elif current == "WRITE":
                plan = make_article_plan(content, outputs)
                save_output(content_id, "ARTICLE_PLAN", plan)
                if plan["status"] != "READY":
                    update_content(content_id, publish_decision="REVIEW_REQUIRED", grade="BLOCKED", body=None,
                                   meta_description=None, tags=[])
                    save_output(content_id, "QUALITY_GATE", {"decision": "QUALITY_FAIL",
                                                            "issues": ["본문에 필요한 근거 있는 H2가 부족합니다."]})
                    set_step(content_id, "WRITE", "COMPLETED", "ARTICLE PLAN의 근거 부족")
                    set_run_status(content_id, "COMPLETED")
                    return
                outputs["ARTICLE_PLAN"] = plan
                result = await write_content(content, outputs, llm)
                update_content(content_id, title=result["title"], body=result["body"],
                               meta_description=result["meta_description"])
            elif current == "QUALITY_GATE":
                value_issues = article_issues(content)
                result = {"issues": quality_issues(content) + value_issues,
                          "decision": "QUALITY_FAIL" if value_issues else "REVIEW_REQUIRED"}
                if value_issues:
                    update_content(content_id, publish_decision="REVIEW_REQUIRED", grade="QUALITY_FAIL",
                                   body=None, meta_description=None, tags=[])
                    save_output(content_id, current, result)
                    set_step(content_id, current, "COMPLETED", "; ".join(value_issues)[:400])
                    set_run_status(content_id, "COMPLETED")
                    return
                result["decision"] = "REVIEW_REQUIRED" if result["issues"] else "PUBLISH_READY"
            elif current == "TAG":
                result = await tags(content, outputs, llm)
                update_content(content_id, tags=result["tags"])
            elif current == "IMAGE":
                save_output(content_id, "IMAGE_SCENE_PLAN", image_scene_plan(content))
                if image is None:
                    key = get_openai_key()
                    if not key:
                        raise RuntimeError("SETTINGS에서 OpenAI API 키를 저장하세요.")
                    image = OpenAIImage(key)
                results = []
                for slot in range(1, 4):
                    existing = content["images"][slot - 1]
                    results.append(True if existing["status"] == "COMPLETED" else
                                   await generate_image(content_id, slot, image))
                result = {"completed_slots": [i for i, ok in enumerate(results, 1) if ok],
                          "failed_slots": [i for i, ok in enumerate(results, 1) if not ok]}
            else:
                cleaned, warnings = sanitize_text(content.get("body") or "", content.get("research") or {})
                if cleaned != content.get("body"):
                    update_content(content_id, body=cleaned)
                result = {"changed": cleaned != content.get("body"), "issues": warnings}
            save_output(content_id, current, result)
            set_step(content_id, current, "COMPLETED", "일부 이미지 실패 · 개별 다시 생성 가능" if current == "IMAGE" and result["failed_slots"] else None)
        current = "FINAL_PACKAGE"
        content = get_content(content_id)
        if next(s for s in content["steps"] if s["step"] == current)["status"] != "COMPLETED":
            cluster_data = cluster(content, content["outputs"])
            update_content(content_id, cluster=cluster_data, next_content=cluster_data["next_content"])
            await finish_package(content_id)
    except Exception as exc:
        logging.exception("Pipeline failed at %s for %s", current, content_id)
        set_step(content_id, current, "FAILED", str(exc)[:400] if isinstance(exc, RuntimeError)
                 else f"{current} 단계 오류: {type(exc).__name__}. 로그를 확인하세요.")
    finally:
        for provider in (llm, search, image):
            if provider is not None and hasattr(provider, "usage"):
                add_usage(content_id, provider.usage)


async def regenerate_image(content_id: str, slot: int, provider=None):
    await generate_image(content_id, slot, provider)
    await finish_package(content_id)
