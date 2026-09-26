"""Topic-aware source scoring; matching a region alone is never sufficient."""

import re

GENERIC = {"지원", "사업", "공고", "신청", "확대", "추가", "대상", "관련", "정보", "보도자료", "신규", "안내"}
EV_TERMS = ("전기차", "전기자동차", "전기승용", "전기화물", "수소차", "수소전기차", "수소전기자동차", "친환경차", "무공해차", "ev.or.kr")
EV_UNRELATED = ("소상공인", "경영안정자금", "긴급복지", "기업조례", "전통시장", "상공회의소")


def _normalized(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def relevance_score(title: str, url: str, excerpt: str, parsed: dict) -> tuple[int, str | None]:
    identity = parsed.get("topic_identity") or {}
    program = identity.get("program_name") or parsed.get("program_name") or ""
    region = identity.get("region") or parsed.get("region") or ""
    text = _normalized(f"{title} {url} {excerpt[:1800]}")
    if not text:
        return 0, "EMPTY_RESULT"
    is_ev = any(term in _normalized(program + " " + identity.get("input_headline", "")) for term in EV_TERMS[:-1])
    if is_ev and not any(_normalized(term) in text for term in EV_TERMS):
        return 0, "SUBJECT_MISMATCH"
    if is_ev and any(_normalized(term) in text for term in EV_UNRELATED) and not any(_normalized(term) in text for term in EV_TERMS):
        return 0, "SUBJECT_MISMATCH"
    words = [word for word in re.findall(r"[가-힣A-Za-z]{2,}", program)
             if word not in GENERIC and word not in (region, identity.get("organization"))]
    subject_hits = [word for word in words if _normalized(word) in text]
    if words and not subject_hits and not (is_ev and any(_normalized(term) in text for term in EV_TERMS)):
        return 0, "SUBJECT_MISMATCH"
    score = min(6, len(subject_hits) * 3)
    if is_ev and any(_normalized(term) in text for term in EV_TERMS):
        score += 5
    region_variants = [region, region.replace("광역시", "시").replace("특별시", "시"), region.replace("군", "")]
    if region and any(_normalized(value) in text for value in region_variants if len(value) >= 2):
        score += 3
    if identity.get("organization") and _normalized(identity["organization"]) in text:
        score += 2
    if any(token in text for token in ("보조금", "추가지원", "추가보급", "구매지원", "카드수수료")):
        score += 1
    numbers = identity.get("number_anchors") or []
    source_numbers = {part.replace(",", "") for part in re.findall(r"\d[\d,]*", text)}
    if any(re.sub(r"\D", "", number) in source_numbers for number in numbers if len(re.sub(r"\D", "", number)) >= 3):
        score += 3
    year = identity.get("year")
    seen_years = set(re.findall(r"20\d{2}", f"{title} {url} {excerpt[:350]}"))
    if year and str(year) in seen_years:
        score += 2
    elif year and seen_years and max(seen_years) < str(year):
        return 0, "HISTORICAL_YEAR"
    if score < (5 if is_ev else 3):
        return score, "LOW_RELEVANCE"
    return score, None


def relevant_source(title: str, url: str, excerpt: str, parsed: dict) -> bool:
    return relevance_score(title, url, excerpt, parsed)[1] is None
