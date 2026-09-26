"""Bounded, topic-locked discovery with fetch and extraction diagnostics."""

import asyncio
import logging
import re
from urllib.parse import urlsplit

import httpx

from .relevance import relevance_score
from .research_sources import clean_url, document_type, fetch_document, source_rank
from .topic import candidate_program, official_domains, query_ladder, relaxed_query

MAX_QUERY_PASSES = 5
MAX_RESULTS_PER_QUERY = 8
MAX_DEEP_DOCUMENTS = 12
MAX_ATTACHMENTS = 6


def source_quality(doc: dict) -> str:
    host = urlsplit(doc["url"]).hostname or ""
    if host.endswith((".go.kr", ".gov.kr")) or host == "ev.or.kr":
        return "OFFICIAL"
    if host.endswith(".or.kr"):
        return "PUBLIC_AGENCY"
    if doc.get("source_rank", 5) == 5:
        return "NEWS"
    return "OTHER"


def source_role(doc: dict) -> str:
    if doc.get("rejection_reason"):
        return "REJECTED"
    quality = source_quality(doc)
    return "EVIDENCE" if quality in ("OFFICIAL", "PUBLIC_AGENCY") else "DISCOVERY" if quality == "NEWS" else "SUPPORTING"


def sort_documents(documents: list[dict], identity: dict) -> None:
    year = str(identity.get("year") or "")
    def key(doc):
        date = re.sub(r"\D", "", str(doc.get("published_at") or "")[:10])
        current = bool(year and year in (doc.get("published_at") or "")[:10])
        return (doc.get("source_role") == "REJECTED", doc.get("source_rank", 9),
                not current, -int(date or "0"), -doc.get("relevance_score", 0))
    documents.sort(key=key)


def _rejected(url, title, reason, score, result):
    rank, kind, correction = source_rank(url, title)
    return {"url": url, "title": title, "source_type": kind, "source_rank": rank,
            "is_correction": correction, "document_type": document_type(url),
            "published_at": result.get("published_date"), "extract_status": "REJECTED", "excerpt": "",
            "source_role": "REJECTED", "source_quality": "OTHER", "relevance_score": score,
            "rejection_reason": reason, "search_status": "SEARCH_FOUND", "fetch_status": "NOT_ATTEMPTED",
            "parse_status": "NOT_ATTEMPTED", "source_domain": urlsplit(url).hostname or "",
            "source_snippet": (result.get("snippet") or "")[:450]}


def _accepted(doc: dict, reason: str) -> dict:
    return {"title": doc.get("title") or "", "url": doc["url"], "domain": doc.get("source_domain"),
            "role": doc.get("source_role"), "relevance_score": doc.get("relevance_score", 0),
            "adoption_reason": reason, "fetch_status": doc.get("fetch_status"), "parse_status": doc.get("parse_status"),
            "fact_extraction_executed": False, "extracted_facts_count": 0, "extraction_failure_reason": None}


def _bridge(title: str, identity: dict, current: dict, score: int) -> dict:
    if not title or score <= current.get("score", -1):
        return current
    candidates = identity.get("number_anchors") or []
    compact_title = re.sub(r"\D", "", title)
    number = next((value for value in candidates if re.sub(r"\D", "", value) in compact_title), None)
    return {"candidate_program_name": candidate_program(title, identity),
            "organization": identity.get("organization"), "region": identity.get("region"),
            "subject": identity.get("subjects") or identity.get("distinctive_terms", [])[:2],
            "distinctive_number": number, "announcement_date": identity.get("announcement_date"),
            "official_keyword": "공고", "score": score, "url": current.get("url")}


async def discover(parsed: dict, input_urls: list[str], search, *, fetch=fetch_document,
                   retry: dict | None = None) -> tuple[list[dict], dict]:
    identity = parsed["topic_identity"]
    documents, seen, discovered, rejected = [], set(), {}, []
    bridge, rows, accepted = {}, [], []
    recovery = bool(retry)
    prior_results = None
    if input_urls:
        discovered.update({url: "입력 URL" for url in input_urls})
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, read=25), headers={"User-Agent": "Mozilla/5.0 MoneyEngine/3.0"}) as client:
        for index in range(MAX_QUERY_PASSES):
            entry = query_ladder(identity, discovered, bridge, (retry or {}).get("missing"),
                                 (retry or {}).get("previous_queries"))[index]
            query, level = entry["query"], entry["level"]
            if prior_results == 0 and index in (1, 2):
                query = relaxed_query(identity, index + 1, missing=(retry or {}).get("missing"))
                if query in (retry or {}).get("previous_queries", []):
                    query += " 공고"
            domains = official_domains(identity, discovered) if index == 3 else []
            row = {"level": level, "query": query, "final_rendered_query": query,
                   "provider": type(search).__name__ if type(search).__name__ != "OpenAIWebSearch" else "OPENAI_WEB_SEARCH",
                   "result_count": 0, "adopted": 0, "rejected": 0, "rejection_reasons": [], "accepted_sources": [],
                   "official_domain_search": index == 3 and bool(domains) and bool(query),
                   "official_domains": domains, "attachment_search": index == 4,
                   "recovery": recovery or index >= 2}
            rows.append(row)
            if not query:
                row["error"] = "OFFICIAL_DOMAIN_NOT_RESOLVED"
                continue
            try:
                response = await search.search(query)
                row["provider"] = response.get("provider") or row["provider"]
                results = (response.get("sources") or [])[:MAX_RESULTS_PER_QUERY]
                if index == 0:
                    results = [{"url": url, "title": "입력 URL"} for url in input_urls[:3]] + results
                row["result_count"] = len(results)
                prior_results = len(results)
                for result in results:
                    try:
                        url = clean_url(result["url"])
                    except (KeyError, ValueError):
                        continue
                    if url in seen:
                        continue
                    title, snippet = str(result.get("title") or "")[:250], str(result.get("snippet") or "")[:450]
                    score, reason = relevance_score(title, url, snippet, parsed)
                    direct_input = url in input_urls
                    if reason and reason != "NEEDS_FETCH" and not direct_input:
                        row["rejected"] += 1
                        row["rejection_reasons"].append({"title": title, "reason": reason})
                        rejected.append(_rejected(url, title, reason, score, result))
                        seen.add(url)
                        continue
                    discovered[url] = title
                    quality = source_quality({"url": url, "source_rank": source_rank(url, title)[0]})
                    if quality == "NEWS" and reason is None:
                        bridge = _bridge(title, identity, bridge, score)
                    if sum(d.get("fetch_status") == "FETCH_SUCCESS" for d in documents) >= MAX_DEEP_DOCUMENTS:
                        continue
                    try:
                        doc, links = await asyncio.wait_for(fetch(client, url, "" if reason == "NEEDS_FETCH" or
                                                              result.get("title_origin") == "URL_PATH" else title), timeout=35)
                        doc_title = doc.get("title") or title
                        doc_score, doc_reason = relevance_score(doc_title, doc["url"], doc.get("excerpt") or "", parsed)
                        published_year = re.match(r"\s*(20\d{2})", str(doc.get("published_at") or ""))
                        if published_year and identity.get("year") and published_year.group(1) < str(identity["year"]):
                            doc_reason = "HISTORICAL_YEAR"
                        if doc.get("extract_status") != "OK" and doc_reason == "NEEDS_FETCH":
                            doc_reason = None  # Retain the found page as PARSE_FAILED, not a subject rejection.
                        if doc_reason:
                            row["rejected"] += 1
                            row["rejection_reasons"].append({"title": doc_title, "reason": doc_reason})
                            rejected.append(_rejected(url, doc_title, doc_reason, doc_score, result))
                            seen.add(url)
                            continue
                        doc.update(source_quality=source_quality(doc), relevance_score=doc_score,
                                   rejection_reason=None, search_status="SEARCH_FOUND", fetch_status="FETCH_SUCCESS",
                                   parse_status="PARSE_SUCCESS" if doc.get("extract_status") == "OK" else "PARSE_FAILED",
                                   source_domain=urlsplit(doc["url"]).hostname or "", source_snippet=snippet)
                        doc["source_role"] = source_role(doc)
                        documents.append(doc)
                        seen.add(doc["url"])
                        row["adopted"] += 1
                        info = _accepted(doc, "제목·요약·본문의 주제 및 지역 일치" if reason is None else "제목 부족: 페이지 본문 대조 후 채택")
                        accepted.append(info)
                        row["accepted_sources"].append(info)
                        if doc["source_role"] == "DISCOVERY":
                            bridge = _bridge(doc_title, identity, bridge, doc_score)
                        if doc["source_role"] == "EVIDENCE":
                            links = sorted(links, key=lambda pair: relevance_score(pair[1], pair[0], doc["excerpt"][:400], parsed)[0], reverse=True)
                            count = sum(d.get("document_type") != "HTML" for d in documents)
                            for attached_url, attached_title in links[:max(0, MAX_ATTACHMENTS - count)]:
                                try:
                                    attached_url = clean_url(attached_url)
                                    if attached_url in seen:
                                        continue
                                    link_score, link_reason = relevance_score(attached_title + " " + doc_title, attached_url, "", parsed)
                                    if link_reason and link_reason != "NEEDS_FETCH":
                                        continue
                                    attached, _ = await asyncio.wait_for(fetch(client, attached_url, attached_title), timeout=35)
                                    if attached["document_type"] == "HTML":
                                        continue
                                    score_after, reason_after = relevance_score(attached_title + " " + doc_title,
                                                                                 attached["url"], attached.get("excerpt") or "", parsed)
                                    if reason_after:
                                        continue
                                    attached.update(source_quality=source_quality(attached), source_role="EVIDENCE",
                                                    relevance_score=score_after, rejection_reason=None,
                                                    search_status="SEARCH_FOUND", fetch_status="FETCH_SUCCESS",
                                                    parse_status="PARSE_SUCCESS" if attached["extract_status"] == "OK" else "PARSE_FAILED",
                                                    source_domain=urlsplit(attached["url"]).hostname or "", source_snippet="")
                                    documents.append(attached)
                                    seen.add(attached["url"])
                                    row["adopted"] += 1
                                    info = _accepted(attached, "공식 페이지의 관련 첨부자료")
                                    accepted.append(info)
                                    row["accepted_sources"].append(info)
                                except Exception as exc:
                                    logging.info("Attachment unavailable: %s (%s)", attached_url, type(exc).__name__)
                    except Exception as exc:
                        logging.info("Source unavailable: %s (%s)", url, type(exc).__name__)
                        seen.add(url)
                        rank, kind, correction = source_rank(url, title)
                        failed = {"url": url, "title": title, "source_rank": rank, "source_type": kind,
                                  "is_correction": correction, "document_type": document_type(url),
                                  "published_at": result.get("published_date"), "extract_status": "FAILED", "excerpt": "",
                                  "source_quality": quality, "relevance_score": score, "rejection_reason": None,
                                  "search_status": "SEARCH_FOUND", "fetch_status": "FETCH_FAILED", "parse_status": "NOT_ATTEMPTED",
                                  "source_domain": urlsplit(url).hostname or "", "source_snippet": snippet}
                        failed["source_role"] = source_role(failed)
                        documents.append(failed)
                        info = _accepted(failed, "관련 결과 발견, 원문 읽기 실패")
                        info["extraction_failure_reason"] = "FETCH_FAILED"
                        accepted.append(info)
                        row["accepted_sources"].append(info)
                        row["rejection_reasons"].append({"title": title, "reason": "FETCH_FAILED"})
            except Exception as exc:
                row["error"] = type(exc).__name__
                logging.exception("Search pass %s failed", level)
    sort_documents(documents, identity)
    failure = None
    if not any(d.get("source_role") == "EVIDENCE" and d.get("parse_status") == "PARSE_SUCCESS" for d in documents):
        failure = ("OFFICIAL_SOURCE_FOUND_BUT_FETCH_FAILED" if any(d.get("source_role") == "EVIDENCE" and
                   d.get("fetch_status") == "FETCH_FAILED" for d in documents) else
                   "OFFICIAL_SOURCE_FOUND_BUT_PARSE_FAILED" if any(d.get("source_role") == "EVIDENCE" and
                   d.get("parse_status") == "PARSE_FAILED" for d in documents) else
                   "OFFICIAL_DOMAIN_NOT_RESOLVED" if not official_domains(identity, discovered) else "NO_OFFICIAL_SOURCE")
    return documents + rejected[:20], {"queries": rows, "accepted_sources": accepted,
                                       "discovery_bridge": bridge, "failure_code": failure,
                                       "official_domain_candidates": official_domains(identity, discovered),
                                       "recovery_used": recovery or any(item["recovery"] for item in rows),
                                       "official_domain_attempted": any(item["official_domain_search"] for item in rows),
                                       "attachment_attempted": any(item["attachment_search"] for item in rows),
                                       "rejected_total": len(rejected)}
