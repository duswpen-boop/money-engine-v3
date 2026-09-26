"""Input anchors are search hints, never verified facts."""

import re
from dataclasses import dataclass, field
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
    region_match = re.search(r"[가-힣]{2,10}(?:특별자치도|특별자치시|광역시|특별시|시|군)", region)
    if region_match:
        region = region_match.group()
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
            "period": (re.search(r"[1-4]\s*분기", input_source[:700]) or [None])[0],
            "time_sensitive": bool(re.search(r"오늘|어제|추가|신규|접수 시작|마감|20\d{2}\s*년\s*\d{1,2}\s*월", input_source[:700])),
            "subjects": subjects, "action": "추가 지원" if "추가" in input_source[:500] else "지원"}


@dataclass
class QuerySpec:
    region: str = ""
    organization: str = ""
    year: str = ""
    period: str = ""
    program: str = ""
    subject: str = ""
    action: str = ""
    numbers: list[str] = field(default_factory=list)
    document_type: str = ""
    domain: str = ""


def _clean_program(program: str, region: str, year: str, period: str) -> str:
    value = str(program or "")
    for token in (region, region.replace("광역시", "시"), year + "년", year, period):
        if token:
            value = value.replace(token, " ")
    value = re.sub(r"\s+", " ", value).strip()
    # Repeated municipality names and years from the parser are search hints, not a title.
    return value


def render_query(spec: QuerySpec) -> str:
    region = spec.region.strip()
    year = re.search(r"20\d{2}", str(spec.year or ""))
    year = year.group() if year else ""
    period = re.sub(r"\s+", "", spec.period or "")
    program = _clean_program(spec.program, region, year, period)
    subject = _clean_program(spec.subject, region, year, period)
    org = _clean_program(spec.organization, region, year, period)
    pieces = [region]
    if org and org != region and org not in program:
        pieces.append(org)
    if subject and subject not in program:
        pieces.append(subject)
    if program:
        pieces.append(program)
    if spec.action and spec.action not in " ".join(pieces) and not (spec.action == "지원" and "지원" in program):
        pieces.append(spec.action)
    if year:
        pieces.append(year + "년")
    if period:
        pieces.append(period)
    if spec.numbers:
        pieces.append(spec.numbers[0])  # One amount representation per query.
    if spec.document_type:
        pieces.append(spec.document_type)
    query = " ".join(dict.fromkeys(piece.strip() for piece in pieces if piece.strip()))
    if spec.domain:
        query = f"site:{spec.domain} {query}"
    return re.sub(r"\s+", " ", query).strip()[:220]


def anchor_query(identity: dict) -> str:
    base = render_query(QuerySpec(region=identity.get("region") or "", organization=identity.get("organization") or "",
                                  program=identity.get("program_name") or ""))
    extras = [term for term in identity.get("distinctive_terms", []) if term not in base and term not in GENERIC]
    return " ".join([base] + extras[:4])[:220]


REGIONAL_DOMAINS = {"순천": "suncheon.go.kr", "춘천": "chuncheon.go.kr", "보은": "boeun.go.kr",
                    "대구": "daegu.go.kr", "부산": "busan.go.kr", "서울": "seoul.go.kr",
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


def _subject(identity: dict) -> str:
    if identity.get("subjects"):
        return " ".join(identity["subjects"])
    program = str(identity.get("program_name") or "")
    for word in ("소상공인", "중소기업", "청년", "전세임대", "국민임대"):
        if word in program or word in str(identity.get("input_headline") or ""):
            return word
    return " ".join(identity.get("distinctive_terms", [])[:2])


def _variant(identity: dict) -> str:
    program = str(identity.get("program_name") or "")
    if "금융지원" in program or "육성자금" in program:
        return _subject(identity) + " 이차보전"
    if identity.get("subjects"):
        return "전기자동차 수소전기차 추가 보급" if len(identity["subjects"]) > 1 else "전기자동차 구매보조금"
    return program


def query_ladder(identity: dict, discovered: dict[str, str] | None = None,
                 bridge: dict | None = None, missing: list[str] | None = None,
                 previous: list[str] | None = None) -> list[dict]:
    discovered, bridge = discovered or {}, bridge or {}
    region, year = identity.get("region") or "", identity.get("year") or ""
    period, program = identity.get("period") or "", identity.get("program_name") or ""
    subject, number = _subject(identity), (identity.get("number_anchors") or [])[:1]
    if missing and "benefit" in missing:
        budget = next((value for value in identity.get("number_anchors", []) if value.endswith("억원")), None)
        if budget:
            number = [budget]
    domain = official_domains(identity, discovered)
    candidate = bridge.get("candidate_program_name") or program
    specs = [
        QuerySpec(region=region, year=year, period=period, program=program),
        QuerySpec(region=region, year=year, period=period, program=_variant(identity), numbers=number),
        QuerySpec(region=region, year=year, subject=subject, numbers=number, document_type="보도자료"),
        QuerySpec(region=region, year=year, period=period, program=candidate,
                  numbers=[(bridge.get("distinctive_number") or number[0]).replace(",", "")]
                  if bridge.get("distinctive_number") or number else [],
                  domain=domain[0] if domain else ""),
        QuerySpec(region=region, year=year, subject=subject, numbers=number, document_type="공고 첨부 PDF HWP"),
    ]
    levels = ["LEVEL 1 EXACT", "LEVEL 2 ENTITY VARIANT", "LEVEL 3 NEWS DISCOVERY",
              "LEVEL 4 OFFICIAL TARGET", "LEVEL 5 DOCUMENT"]
    field_terms = {"target": "신청 대상", "action": "신청 방법", "date": "접수일", "benefit": "지원액",
                   "program_name": "사업명", "organization": "시행기관", "period": "지원기간", "rate": "이차보전"}
    prior = set(previous or [])
    result = []
    for index, spec in enumerate(specs):
        if missing:
            spec.document_type = " ".join([spec.document_type] + [field_terms.get(name, name) for name in missing[:2]]).strip()
            levels[index] = f"RECOVERY {index + 1}"
        query = render_query(spec) if index != 3 or domain else ""
        if query in prior:
            spec.document_type = " ".join([spec.document_type, "상세 신청자격 변경공고"]).strip()
            query = render_query(spec)
        result.append({"level": levels[index], "query": query, "domain": domain[0] if index == 3 and domain else None})
    return result


def relaxed_query(identity: dict, level: int, *, missing: list[str] | None = None) -> str:
    """After zero hits, shed period and amount while retaining region and policy subject."""
    program = _clean_program(identity.get("program_name") or "", identity.get("region") or "",
                             identity.get("year") or "", identity.get("period") or "")
    subject = _subject(identity)
    if level >= 3 and ("금융지원" in program or "육성자금" in program):
        program = subject + " 이차보전"
    return render_query(QuerySpec(region=identity.get("region") or "", program=program,
                                  year=identity.get("year") or "", document_type="보도자료" if level >= 3 else ""))


def candidate_program(title: str, identity: dict) -> str:
    """Extract policy words only; never carry source branding or SEO suffix to official search."""
    text = re.split(r"\s*[|｜]\s*|\s+[-–—]\s+", str(title or ""))[0]
    text = re.sub(r"^\[[^]]{1,25}\]\s*", "", text)
    text = re.sub(r"(?:\s+|^)(?:CKP|연합뉴스|뉴스|한국경영지원센터|공식 원문).*$", "", text)
    region = identity.get("region") or ""
    year = str(identity.get("year") or "")
    period = identity.get("period") or ""
    text = _clean_program(text, region, year, period)
    tokens = re.findall(r"[가-힣A-Za-z]{2,}", text)
    subject = _subject(identity)
    allowed = [word for word in tokens if word in (identity.get("program_name") or "") or word in subject or
               word in {"이차보전", "융자", "보조금", "추가지원", "추가", "지원", "시행", "육성자금", "금융지원"}]
    return " ".join(dict.fromkeys(allowed))[:80] if allowed else identity.get("program_name") or ""


def recovery_queries(identity: dict, candidates: dict[str, str]) -> list[str]:
    return [entry["query"] for entry in query_ladder(identity, candidates, missing=["target"])]


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
