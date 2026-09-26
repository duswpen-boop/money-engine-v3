"""Conservative gates for publishable text and the final package."""

import re

from .research_sources import clean_url


def verified_values(research: dict) -> dict:
    return {key: fact["value"] for key, fact in (research or {}).get("facts", {}).items()
            if fact.get("status") == "VERIFIED" and fact.get("value")}


def safe_title(research: dict, primary: str = "") -> str:
    facts = verified_values(research)
    subject = facts.get("program_name") or primary or "공식자료 확인"
    # A generated keyword may contain unverified dates or money: only the verified program is trusted.
    if not facts.get("program_name"):
        subject = "지원 내용 확인 필요"
    region = facts.get("region") or ""
    return f"{region} {subject} 신청 자격과 방법".strip()[:90]


def forbidden_claims(research: dict) -> list[str]:
    claims = []
    for key in ("announcement_date", "application_start", "application_end", "amount_or_limit",
                "rate_or_interest", "support_period", "eligibility"):
        fact = (research or {}).get("facts", {}).get(key, {})
        value = fact.get("input_value")
        if fact.get("status") != "VERIFIED" and value and len(str(value)) >= 3:
            claims.append(str(value))
    return claims


def compact(value: str) -> str:
    return re.sub(r"[\s,.\-/]", "", value).lower()


def sanitize_text(body: str, research: dict) -> tuple[str, list[str]]:
    issues = []
    body = re.sub(r"(?im)^\s*(?:meta\s*)?description\s*:\s*.*$", "", body)
    body = body.replace("\\_", "_").replace("\\*", "*")
    summary = (research or {}).get("summary") or {}
    allowed = {summary.get("official_url"),
               *(attachment.get("url") for attachment in summary.get("official_attachments", [])),
               *(fact.get("source_url") for fact in (research or {}).get("facts", {}).values())}
    allowed = {clean_url(url) for url in allowed if url}
    def link_fix(match):
        try:
            url = clean_url(match.group(2))
            if url in allowed:
                return f"[{match.group(1)}]({url})"
        except ValueError:
            pass
        issues.append("근거 없는 URL을 본문에서 제거함")
        return match.group(1)
    body = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", link_fix, body)
    def url_fix(match):
        try:
            url = clean_url(match.group())
            if url not in allowed:
                issues.append("근거 없는 URL을 본문에서 제거함")
                return ""
            return url
        except ValueError:
            issues.append("유효하지 않은 URL을 본문에서 제거함")
            return ""
    body = re.sub(r"https?://[^\s)<>]+", url_fix, body)
    lines = []
    for line in body.splitlines():
        if any(compact(claim) in compact(line) for claim in forbidden_claims(research)):
            issues.append("미검증 입력 사실이 본문에 포함되어 제거됨")
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip(), list(dict.fromkeys(issues))


def quality_issues(content: dict, *, require_images=False) -> list[str]:
    research = content.get("research") or {}
    facts = research.get("facts") or {}
    issues = []
    if research.get("conflict") or any(f.get("status") == "CONFLICT" for f in facts.values()):
        issues.append("해결되지 않은 공식자료 또는 입력 주장 충돌")
    if not (research.get("summary") or {}).get("official_url"):
        issues.append("직접 읽은 공식 출처 없음")
    for field, label in (("eligibility", "대상"), ("application_method", "신청방법")):
        if facts.get(field, {}).get("status") != "VERIFIED":
            issues.append(f"{label} 공식 확인 필요")
    body, removed = sanitize_text(content.get("body") or "", research)
    issues.extend(removed)
    if not body or "## " not in body:
        issues.append("본문 구조 확인 필요")
    if re.search(r"(?im)^\s*(?:meta\s*)?description\s*:", body):
        issues.append("본문에 Meta Description 노출")
    title = content.get("title") or ""
    if any(compact(claim) in compact(title) for claim in forbidden_claims(research)):
        issues.append("제목에 검증되지 않은 날짜·금액·조건")
    if not content.get("meta_description"):
        issues.append("Meta Description 누락")
    if require_images:
        images = content.get("images") or []
        if not images or any(image["status"] != "COMPLETED" or not image.get("file_path") for image in images[:3]):
            issues.append("이미지 생성 미완료")
        if not 8 <= len(content.get("tags") or []) <= 12:
            issues.append("태그 8~12개 확인 필요")
    return list(dict.fromkeys(issues))
