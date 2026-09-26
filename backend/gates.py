"""Fail closed before paid writing or image generation."""

import re

from .quality import verified_values
from .topic import identity_matches

MIN_COVERAGE = 0.70
BLOCK_MESSAGE = "공식자료 확보 부족 — 본문/이미지 생성을 중단했습니다."
EMPTY_PHRASES = ("확인된 구체적 정보 없음", "공식기관에서 확인", "확인되지 않았습니다",
                 "알려진 바가 없습니다", "구체적인 정보가 없습니다")


def fact_in_section(value: str, section: str) -> bool:
    compact = lambda item: re.sub(r"\s+", "", item)
    if compact(value) in compact(section):
        return True
    numbers = re.findall(r"\d[\d,]*(?:\.[\d]+)?", value)
    if numbers and any(number in section for number in numbers):
        words = [word for word in re.findall(r"[가-힣]{3,}", value) if word in section]
        return bool(words)
    words = [word for word in re.findall(r"[가-힣A-Za-z]{3,}", value) if word in section]
    return len(set(words)) >= 2


def research_gate(content: dict) -> dict:
    research = content.get("research") or {}
    facts = research.get("facts") or {}
    values = verified_values(research)
    identity = (research.get("parsed") or {}).get("topic_identity") or {}
    required = {"jurisdiction": ("region",), "organization": ("organization",),
                "program_name": ("program_name",), "target": ("eligibility",),
                "benefit": ("amount_or_limit", "key_changes"), "action": ("application_method",)}
    seasonal = any(identity.get(key) for key in ("announcement_date", "application_start", "application_end"))
    if seasonal:
        required["date"] = ("announcement_date", "application_start", "application_end")
    covered = {name: next((field for field in fields if values.get(field)), None)
               for name, fields in required.items()}
    coverage = sum(bool(value) for value in covered.values()) / len(required)
    critical_missing = [name for name in ("program_name", "target", "action", "date")
                        if name in required and not covered[name]]
    conflict = research.get("conflict") or any(fact.get("status") == "CONFLICT" for fact in facts.values())
    drift = not identity_matches(identity, values)
    official = bool((research.get("summary") or {}).get("official_url"))
    blocked = coverage < MIN_COVERAGE or bool(critical_missing) or conflict or drift or not official
    reasons = (["핵심 사실 검증률 70% 미만"] if coverage < MIN_COVERAGE else [])
    if critical_missing:
        reasons.append("필수 행동 정보 미검증: " + ", ".join(critical_missing))
    if conflict:
        reasons.append("중대한 출처 충돌")
    if drift:
        reasons.append("입력 소재와 공식 사업명이 다름")
    if not official:
        reasons.append("읽을 수 있는 공식 원출처 없음")
    return {"status": "CONTENT_BLOCKED" if blocked else "PASSED", "coverage": round(coverage, 3),
            "covered": covered, "required": list(required), "reasons": reasons,
            "message": BLOCK_MESSAGE if blocked else "Research 근거 충족"}


def article_issues(content: dict) -> list[str]:
    body = content.get("body") or ""
    research = content.get("research") or {}
    plan = (content.get("outputs") or {}).get("ARTICLE_PLAN") or {}
    headings = list(re.finditer(r"(?m)^##\s+(.+)$", body))
    issues = []
    if not headings:
        return ["QUALITY_FAIL: 근거 있는 H2가 없음"]
    if sum(body.count(phrase) for phrase in EMPTY_PHRASES) >= 2:
        issues.append("QUALITY_FAIL: UNKNOWN 일반론 반복")
    if not plan.get("sections"):
        issues.append("QUALITY_FAIL: ARTICLE PLAN 없음")
    facts = verified_values(research)
    verified_numbers = {str(int(number.replace(",", ""))) for value in facts.values()
                        for number in re.findall(r"\d[\d,]*", str(value))}
    prose = re.sub(r"https?://\S+", "", body)
    claimed_numbers = {str(int(number.replace(",", ""))) for number in re.findall(
        r"(\d[\d,]*(?:\.\d+)?)\s*(?:원|만원|억원|%|년|월|일|명|대|호|개월)", prose)
        if "." not in number}
    if claimed_numbers - verified_numbers:
        issues.append("QUALITY_FAIL: 공식 근거 없는 숫자·날짜·금액")
    unsupported = 0
    for index, heading in enumerate(headings):
        title = heading.group(1).strip()
        section = next((entry for entry in plan.get("sections", []) if entry["heading"] == title), None)
        text = body[heading.end():headings[index + 1].start() if index + 1 < len(headings) else len(body)]
        if not section or not any(fact_in_section(str(facts.get(key) or ""), text)
                                  for key in (section.get("fact_keys") if section else []) if facts.get(key)):
            unsupported += 1
    if unsupported:
        issues.append(f"QUALITY_FAIL: 구체적인 VERIFIED fact가 없는 H2 {unsupported}개")
    return issues
