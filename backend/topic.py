"""Input anchors are search hints, never verified facts."""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

GENERIC = {"지원", "사업", "공고", "신청", "방법", "안내", "추가", "확대", "정책", "신규", "속보", "오늘", "최신", "2026년",
           "공개", "발표", "접수", "신청기간", "접수기간", "지원대상", "추가지원", "변경"}
WEAK_SUBJECTS = {"자동차", "보조금", "카드", "소상공인", "지원금", "사업", "혜택"}


def topic_identity(parsed: dict, input_source: str) -> dict:
    headline = next((line.strip(" #*[]-·") for line in input_source.splitlines()
                     if len(line.strip()) >= 8 and not line.strip().startswith("http")), "")[:180]
    program = str(parsed.get("program_name") or "").strip()[:120]
    region = str(parsed.get("region") or "").strip()[:60]
    organization = str(parsed.get("organization") or "").strip()[:90]
    benefit = str(parsed.get("amount_or_limit") or parsed.get("key_changes") or "").strip()[:100]
    terms = list(dict.fromkeys(word for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", program + " " + headline)
                               if word not in GENERIC and not re.fullmatch(r"\d+", word)))[:12]
    return {"region": region or None, "organization": organization or None,
            "program_name": program or None, "announcement_date": parsed.get("announcement_date"),
            "application_start": parsed.get("application_start"), "application_end": parsed.get("application_end"),
            "benefit_type": parsed.get("key_changes"), "benefit_amount": parsed.get("amount_or_limit"),
            "target": parsed.get("eligibility"), "distinctive_terms": terms,
            "input_headline": headline}


def anchor_query(identity: dict) -> str:
    fields = [identity.get(key) for key in ("region", "organization", "program_name")]
    core = [str(value).strip() for value in fields if value]
    core.extend(identity.get("distinctive_terms", [])[:5])
    return " ".join(dict.fromkeys(core))[:220]


def recovery_queries(identity: dict, candidates: dict[str, str]) -> list[str]:
    region, org, program = (identity.get(k) or "" for k in ("region", "organization", "program_name"))
    benefit = identity.get("benefit_type") or identity.get("benefit_amount") or ""
    terms = " ".join(identity.get("distinctive_terms", [])[:3])
    year_match = re.search(r"20\d{2}", str(identity))
    year = year_match.group() if year_match else str(datetime.now(timezone.utc).year)
    domains = []
    for url in candidates:
        host = urlsplit(url).hostname or ""
        if host.endswith((".go.kr", ".or.kr", ".gov.kr")) and host not in domains:
            domains.append(host)
    queries = [f'"{region}" "{program}" 공식 공고' if region and program else "",
               f'"{org}" {benefit} {program} {year} 공고' if org else "",
               f'site:{domains[0]} {program or terms} 정정 공고 첨부' if domains else "",
               f'{region} {org} {program or terms} 보도자료 공식 사업명',
               f'{region} {program or terms} 공고 첨부 PDF HWP HWPX']
    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))[:5]


def identity_matches(identity: dict, verified: dict) -> bool:
    program = str(identity.get("program_name") or "")
    official = str(verified.get("program_name") or "")
    if not program or not official:
        return True
    tokens = [word for word in re.findall(r"[가-힣A-Za-z]{2,}", program) if word not in GENERIC]
    if not (any(word in official for word in tokens) or program in official or official in program):
        return False
    if any(word not in WEAK_SUBJECTS and len(word) >= 3 for word in tokens):
        return True
    distinctive = [word for word in identity.get("distinctive_terms", [])
                   if len(word) >= 3 and word not in program and word != identity.get("region")
                   and word != identity.get("organization")]
    if distinctive:
        official_context = " ".join(str(verified.get(key) or "") for key in
                                    ("program_name", "key_changes", "eligibility", "amount_or_limit"))
        return any(word in official_context for word in distinctive)
    return True
