"""Every planned section has an answer, verified fact and source."""

from .quality import verified_values

RULES = (
    (("대상", "자격", "누가", "제외"), ("eligibility", "region"), "WHO"),
    (("한도", "금액", "금리", "이자", "지원", "물량"), ("amount_or_limit", "rate_or_interest", "key_changes"), "MONEY"),
    (("기간", "언제", "마감", "접수일", "일정"), ("application_start", "application_end", "announcement_date"), "WHEN"),
    (("방법", "신청", "접수", "절차", "어떻게"), ("application_method", "organization"), "HOW"),
    (("서류", "준비"), ("application_method", "key_changes"), "DOCUMENT"),
    (("기관", "어디", "지역"), ("organization", "region", "application_method"), "WHERE"),
)


def make_article_plan(content: dict, outputs: dict) -> dict:
    facts = (content.get("research") or {}).get("facts") or {}
    verified = verified_values(content.get("research") or {})
    ideas = outputs.get("VALUE_ADD", {}).get("ideas", [])
    sections = []
    for heading in outputs.get("SEARCH_INTENT", {}).get("h2_outline", [])[:9]:
        if not isinstance(heading, str) or not heading.strip():
            continue
        rule = next((entry for entry in RULES if any(word in heading for word in entry[0])), None)
        if not rule:
            continue
        keys = [key for key in rule[1] if verified.get(key) and facts[key].get("source_url")]
        if not keys:
            continue
        additional = [idea["description"] for idea in ideas if set(idea.get("fact_keys", [])) <= set(keys)][:2]
        sections.append({"heading": heading.strip()[:80], "question": heading.strip(), "intent": rule[2],
                         "fact_keys": keys, "facts": {key: verified[key] for key in keys},
                         "sources": list(dict.fromkeys(facts[key]["source_url"] for key in keys)),
                         "value_add": additional})
    return {"sections": sections, "status": "READY" if len(sections) >= 2 else "INSUFFICIENT_FACTS"}
