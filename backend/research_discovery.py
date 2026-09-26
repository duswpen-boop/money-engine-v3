"""Five bounded query strategies with topic filtering and source diagnostics."""

import asyncio
import logging
import re
from urllib.parse import urlsplit

import httpx

from .relevance import relevance_score
from .research_sources import clean_url, document_type, fetch_document, source_rank
from .topic import official_domains, query_ladder

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


def _rejected(url, title, reason, score):
    rank, kind, correction = source_rank(url, title)
    return {"url": url, "title": title, "source_type": kind, "source_rank": rank,
            "is_correction": correction, "document_type": document_type(url),
            "published_at": None, "extract_status": "REJECTED", "excerpt": "",
            "source_role": "REJECTED", "source_quality": "OTHER", "relevance_score": score,
            "rejection_reason": reason}


async def discover(parsed: dict, input_urls: list[str], search, *, fetch=fetch_document,
                   retry: dict | None = None) -> tuple[list[dict], dict]:
    identity = parsed["topic_identity"]
    documents, seen, discovered, rejected = [], set(), {}, []
    bridge, diagnostics = {}, []
    recovery = bool(retry)
    if input_urls:
        discovered.update({url: "입력 URL" for url in input_urls})
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, read=25), headers={"User-Agent": "Mozilla/5.0 MoneyEngine/3.0"}) as client:
        for index in range(5):
            entry = query_ladder(identity, discovered, bridge,
                                 (retry or {}).get("missing"), (retry or {}).get("previous_queries"))[index]
            query, level = entry["query"], entry["level"]
            row = {"level": level, "query": query, "result_count": 0, "adopted": 0,
                   "rejected": 0, "rejection_reasons": [], "official_domain_search": index == 3,
                   "official_domains": official_domains(identity, discovered) if index == 3 else [],
                   "attachment_search": index == 4, "recovery": recovery or index >= 2}
            diagnostics.append(row)
            try:
                response = await search.search(query)
                results = response.get("sources", [])[:MAX_RESULTS_PER_QUERY]
                if index == 0:
                    results = [{"url": url, "title": "입력 URL"} for url in input_urls[:3]] + results
                row["result_count"] = len(results)
                for result in results:
                    try:
                        url = clean_url(result["url"])
                    except (KeyError, ValueError):
                        continue
                    if url in seen:
                        continue
                    title = str(result.get("title") or "")[:250]
                    score, reason = relevance_score(title, url, "", parsed)
                    direct_input = url in input_urls
                    if reason and not direct_input:
                        row["rejected"] += 1
                        row["rejection_reasons"].append({"title": title, "reason": reason})
                        rejected.append(_rejected(url, title, reason, score))
                        seen.add(url)
                        continue
                    discovered[url] = title
                    quality = source_quality({"url": url, "source_rank": source_rank(url, title)[0]})
                    if quality == "NEWS" and (not bridge or score > bridge.get("score", -1)):
                        bridge = {"title": title, "score": score, "url": url}
                    if len([doc for doc in documents if doc.get("extract_status") != "REJECTED"]) >= MAX_DEEP_DOCUMENTS:
                        continue
                    try:
                        doc, links = await asyncio.wait_for(fetch(client, url, title), timeout=35)
                        doc_score, doc_reason = relevance_score(doc["title"], doc["url"], doc["excerpt"], parsed)
                        published_year = re.match(r"\s*(20\d{2})", str(doc.get("published_at") or ""))
                        if published_year and identity.get("year") and published_year.group(1) < str(identity["year"]):
                            doc_reason = "HISTORICAL_YEAR"
                        if doc_reason:
                            row["rejected"] += 1
                            row["rejection_reasons"].append({"title": title, "reason": doc_reason})
                            rejected.append(_rejected(url, title, doc_reason, doc_score))
                            seen.add(url)
                            continue
                        doc.update(source_quality=source_quality(doc), relevance_score=doc_score,
                                   rejection_reason=None)
                        doc["source_role"] = source_role(doc)
                        if doc["source_role"] == "DISCOVERY" and doc_score > bridge.get("score", -1):
                            bridge = {"title": doc["title"], "score": doc_score, "url": doc["url"]}
                        documents.append(doc)
                        seen.add(doc["url"])
                        row["adopted"] += 1
                        if doc["source_role"] == "EVIDENCE":
                            attachments = sorted(links, key=lambda pair: relevance_score(pair[1], pair[0], doc["excerpt"][:400], parsed)[0], reverse=True)
                            count = sum(d.get("document_type") != "HTML" for d in documents)
                            for attached_url, attached_title in attachments[:max(0, MAX_ATTACHMENTS - count)]:
                                try:
                                    attached_url = clean_url(attached_url)
                                    if attached_url in seen:
                                        continue
                                    link_score, link_reason = relevance_score(attached_title + " " + title, attached_url, "", parsed)
                                    if link_reason:
                                        row["rejected"] += 1
                                        row["rejection_reasons"].append({"title": attached_title, "reason": link_reason})
                                        continue
                                    attached, _ = await asyncio.wait_for(fetch(client, attached_url, attached_title), timeout=35)
                                    if attached["document_type"] == "HTML":
                                        continue
                                    attached.update(source_quality=source_quality(attached), source_role="EVIDENCE",
                                                    relevance_score=link_score, rejection_reason=None)
                                    documents.append(attached)
                                    seen.add(attached["url"])
                                    row["adopted"] += 1
                                except Exception:
                                    logging.info("Attachment unavailable: %s", attached_url)
                    except Exception as exc:
                        logging.info("Source unavailable: %s (%s)", url, type(exc).__name__)
                        seen.add(url)
                        rank, kind, correction = source_rank(url, title)
                        failed = {"url": url, "title": title, "source_rank": rank, "source_type": kind,
                                  "is_correction": correction, "document_type": document_type(url),
                                  "published_at": None, "extract_status": "FAILED", "excerpt": "",
                                  "source_quality": quality, "relevance_score": score, "rejection_reason": None}
                        failed["source_role"] = source_role(failed)
                        documents.append(failed)
                        row["rejection_reasons"].append({"title": title, "reason": "FETCH_FAILED"})
            except Exception as exc:
                row["error"] = type(exc).__name__
                logging.exception("Search pass %s failed", level)
    sort_documents(documents, identity)
    return documents + rejected[:20], {"queries": diagnostics, "discovery_bridge": bridge,
                                      "recovery_used": recovery or any(item["recovery"] for item in diagnostics),
                                      "official_domain_attempted": any(item["official_domain_search"] for item in diagnostics),
                                      "attachment_attempted": any(item["attachment_search"] for item in diagnostics),
                                      "rejected_total": len(rejected)}
