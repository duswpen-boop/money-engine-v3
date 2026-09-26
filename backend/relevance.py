"""Keep a search hit only when its subject matches the submitted program."""

import re

GENERIC = {"지원", "사업", "공고", "신청", "확대", "추가", "가맹", "대상", "관련", "정보", "보도자료", "소상공인"}


def relevant_source(title: str, url: str, excerpt: str, parsed: dict) -> bool:
    subject = parsed.get("program_name") or parsed.get("search_query") or ""
    words = [w for w in re.findall(r"[가-힣A-Za-z]{2,}", subject) if w not in GENERIC]
    words = [w for w in words if w not in {parsed.get("region"), parsed.get("organization")}]
    heading = re.sub(r"\s+", "", (title or "") + " " + (url or "")).lower()
    intro = re.sub(r"\s+", "", (excerpt or "")[:1800]).lower()
    matches_heading = [w for w in words if w.lower() in heading]
    matches_intro = [w for w in words if w.lower() in intro]
    if matches_heading:
        return True
    if len(matches_intro) >= 2:
        return True
    # When no useful program phrase could be parsed, leave the result for fact validation.
    return not words
