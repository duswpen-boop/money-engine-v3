"""Targeted edits leave other generated fields and images intact."""

import re

from .content_engine import tags
from .credentials import get_openai_key
from .pipeline import finish_package
from .providers.llm import OpenAILLM
from .quality import compact, forbidden_claims, safe_title, sanitize_text, verified_values
from .store import get_content, save_output, update_content

TEXT_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}},
               "required": ["text"], "additionalProperties": False}


async def edit_part(content_id: str, part: str, heading: str | None = None, llm=None):
    content = get_content(content_id)
    if content is None or not content.get("body"):
        raise ValueError("완성된 본문을 찾을 수 없습니다.")
    if part not in {"title", "meta", "lead", "h2", "tags"}:
        raise ValueError("수정할 부분이 올바르지 않습니다.")
    if llm is None:
        key = get_openai_key()
        if not key:
            raise RuntimeError("SETTINGS에서 OpenAI API 키를 저장하세요.")
        llm = OpenAILLM(key)
    research = content.get("research") or {}
    facts = verified_values(research)
    instruction = ("확인된 사실만 사용하세요. 자료에 없는 숫자·날짜·대상·URL은 만들지 마세요. "
                   "결과에는 수정 대상 텍스트만 반환하고 다른 문단은 쓰지 마세요. "
                   f"확인된 사실: {facts}. 현재 제목: {content['title']}. ")
    if part == "tags":
        result = await tags(content, content.get("outputs") or {}, llm)
        update_content(content_id, tags=result["tags"])
    else:
        body = content["body"]
        if part == "h2":
            matches = list(re.finditer(r"(?m)^##\s+(.+)$", body))
            target = next((i for i, match in enumerate(matches) if match.group(1).strip() == heading), None)
            if target is None:
                raise ValueError("해당 H2를 찾을 수 없습니다.")
            start = matches[target].end()
            end = matches[target + 1].start() if target + 1 < len(matches) else len(body)
            original = body[start:end].strip()
            instruction += f"제목 '## {heading}' 아래의 본문만 다시 작성하세요. 현재 내용: {original}"
        elif part == "lead":
            match = re.search(r"(?m)^##\s+", body)
            first = re.search(r"(?m)^#\s+.+$", body)
            if not first or not match:
                raise ValueError("본문 구조가 수정에 적합하지 않습니다.")
            start, end = first.end(), match.start()
            instruction += f"H1 아래의 도입부만 다시 작성하세요. 현재 내용: {body[start:end].strip()}"
        else:
            instruction += f"현재 {part}: {content['title'] if part == 'title' else content['meta_description']}"
        response = await llm.generate_structured(instruction, TEXT_SCHEMA)
        text = response["text"].strip()
        if not text or any(compact(claim) in compact(text) for claim in forbidden_claims(research)):
            raise ValueError("수정 결과에 미검증 주장이 포함됐습니다. 기존 결과를 유지합니다.")
        if part == "title":
            verified_digits = set(re.findall(r"\d+", " ".join(map(str, facts.values()))))
            if any(digit not in verified_digits for digit in re.findall(r"\d+", text)):
                text = safe_title(research)
            update_content(content_id, title=text[:90])
        elif part == "meta":
            text = re.sub(r"(?i)^(?:meta\s*)?description\s*:\s*", "", text).strip()
            update_content(content_id, meta_description=text[:180])
        else:
            text, warnings = sanitize_text(text, research)
            if not text or warnings:
                raise ValueError("수정 결과를 안전하게 확인할 수 없습니다. 기존 결과를 유지합니다.")
            update_content(content_id, body=(body[:start].rstrip() + "\n\n" + text + "\n\n" + body[end:].lstrip()).strip())
    save_output(content_id, "EDIT_" + part.upper(), {"last_edited": part, "heading": heading})
    await finish_package(content_id)
    return get_content(content_id)
