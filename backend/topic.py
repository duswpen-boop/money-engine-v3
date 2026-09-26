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
    if not region:
        found_region = re.search(r"(?:서울|부산|대구|인천|광주|대전|울산)(?:특별시|광역시|시)|[가-힣]{2,7}(?:특별자치도|특별자치시|시|군)", input_source[:600])
        region = found_region.group() if found_region else ""
    organization = str(parsed.get("organization") or "").strip()[:90]
    benefit = str(parsed.get("amount_or_limit") or parsed.get("key_changes") or "").strip()[:100]
    terms = list(dict.fromkeys(word for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", program + " " + headline)
                               if word not in GENERIC and not re.fullmatch(r"\d+", word)))[:12]
    year_match = re.search(r"20\d{2}", input_source[:1500])
    year = year_match.group() if year_match else str(datetime.now(timezone.utc).year)
    numbered = [(match.group().replace(" ", ""), input_source[max(0, match.start()-8):match.start()])
                for match in re.finditer(r"(?<!\d)\d[\d,]{1,6}\s*(?:대|억원|만원|호|명)", input_source[:2500])]
    numbered.sort(key=lambda entry: 0 if re.search(r"총\s*$|전체\s*$", entry[1]) else
                  1 if entry[0].endswith("억원") else 2)
    numbers = list(dict.fromkeys(value for value, _ in numbered))[:6]
    ev = " ".join((program, headline, input_source[:400]))
    subjects = [name for name, terms_group in (("전기차", ("전기차", "전기자동차", "전기승용", "전기화물")),
                                           ("수소차", ("수소차", "수소전기차", "수소전기자동차")))
                if any(term in ev for term in terms_group)]
    if subjects and not any(subject in program for subject in subjects):
        program = " ".join(subjects) + (" 보조금" if "보조금" in input_source[:500] else "") + (" 추가 지원" if "추가" in input_source[:500] else " 지원")
        terms = list(dict.fromkeys(re.findall(r"[가-힣A-Za-z0-9]{2,}", program + " " + headline)))[:12]
    return {"region": region or None, "organization": organization or None,
            "program_name": program or None, "announcement_date": parsed.get("announcement_date"),
            "application_start": parsed.get("application_start"), "application_end": parsed.get("application_end"),
            "benefit_type": parsed.get("key_changes"), "benefit_amount": parsed.get("amount_or_limit"),
            "target": parsed.get("eligibility"), "distinctive_terms": terms,
            "input_headline": headline, "year": year, "number_anchors": numbers,
            "time_sensitive": bool(re.search(r"오늘|어제|추가|신규|접수 시작|마감|20\d{2}\s*년\s*\d{1,2}\s*월", input_source[:700])),
            "subjects": subjects, "action": "추가 지원" if "추가" in input_source[:500] else "지원"}


def anchor_query(identity: dict) -> str:
    fields = [identity.get(key) for key in ("region", "organization", "program_name")]
    core = [str(value).strip() for value in fields if value]
    core.extend(identity.get("subjects", []))
    core.extend(identity.get("distinctive_terms", [])[:5])
    return " ".join(dict.fromkeys(core))[:220]


REGIONAL_DOMAINS = {"대구": "daegu.go.kr", "부산": "busan.go.kr", "서울": "seoul.go.kr",
                    "인천": "incheon.go.kr", "대전": "daejeon.go.kr", "광주": "gwangju.go.kr",
                    "울산": "ulsan.go.kr", "경기": "gg.go.kr", "충북": "chungbuk.go.kr",
                    "충남": "chungnam.go.kr", "전북": "jeonbuk.go.kr", "전남": "jeonnam.go.kr",
                    "경북": "gb.go.kr", "경남": "gyeongnam.go.kr", "제주": "jeju.go.kr"}


def official_domains(identity: dict, discovered: dict[str, str]) -> list[str]:
    region = identity.get("region") or ""
    domains = [domain for prefix, domain in REGIONAL_DOMAINS.items() if prefix in region]
    for url in discovered:
        host = urlsplit(url).hostname or ""
        if host.endswith((".go.kr", ".gov.kr", ".or.kr")) and host not in domains:
            domains.append(host)
    if identity.get("subjects") and "ev.or.kr" not in domains:
        domains.append("ev.or.kr")
    return domains[:4]


def query_ladder(identity: dict, discovered: dict[str, str] | None = None,
                 bridge: dict | None = None, missing: list[str] | None = None,
                 previous: list[str] | None = None) -> list[dict]:
    discovered, bridge = discovered or {}, bridge or {}
    region = identity.get("region") or ""
    short_region = region.replace("광역시", "시").replace("특별시", "시")
    year = identity.get("year") or str(datetime.now(timezone.utc).year)
    subjects = identity.get("subjects") or identity.get("distinctive_terms", [])[:2]
    subject = " ".join(subjects)
    program = identity.get("program_name") or identity.get("input_headline") or subject
    action = identity.get("action") or "지원"
    number = " ".join((identity.get("number_anchors") or [])[:2])
    exact = " ".join(dict.fromkeys(value for value in (region, year, subject, program, action) if value))
    variant = f"{short_region} {'전기자동차 수소전기차' if identity.get('subjects') else program} {action} {year} {number}".strip()
    news = f"{region} {subject or program} {number} {year} 보도자료 뉴스".strip()
    bridge_term = bridge.get("title") or program
    domains = official_domains(identity, discovered)
    official = f"site:{domains[0]} {short_region} {subject or program} {bridge_term} {year}" if domains else f"{region} {subject or program} {bridge_term} 공식 원문 {year}"
    docs = f"{region} {subject or program} {number} {year} 공고 고시 보도자료 변경공고 첨부 PDF HWP HWPX"
    levels = [("LEVEL 1 EXACT", exact), ("LEVEL 2 ENTITY VARIANT", variant),
              ("LEVEL 3 NEWS DISCOVERY", news), ("LEVEL 4 OFFICIAL TARGET", official),
              ("LEVEL 5 DOCUMENT", docs)]
    if missing:
        field_terms = {"target": "신청 대상 거주 요건", "action": "신청 방법 제작 수입사",
                       "date": "접수 시작 발표일", "benefit": "지원액 추가예산 물량",
                       "program_name": "정확한 사업명", "organization": "담당부서 시행기관"}
        missing_phrase = " ".join(field_terms.get(item, item) for item in missing[:3])
        numbers = " ".join((identity.get("number_anchors") or [])[:2])
        levels = [(f"RECOVERY {index}", query + " " + missing_phrase + " " + numbers)
                  for index, (_, query) in enumerate(levels, 1)]
    previous_set = set(previous or [])
    result = []
    for level, query in levels:
        normalized = " ".join(query.split())[:300]
        if normalized in previous_set:
            normalized = (normalized + " " + " ".join((identity.get("number_anchors") or [])[:2]) +
                          " 정정공고 상세 신청자격").strip()[:300]
        result.append({"level": level, "query": normalized})
    return result


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
